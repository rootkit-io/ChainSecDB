import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.models.extraction_run import ExtractionRun
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.evaluation.dataset import REPOSITORY, Dataset, EvaluationError, LoadedCase
from app.evaluation.report import git_provenance
from app.evaluation.schemas import EvaluationFinding, Prediction, Predictions, Span
from app.extraction.orchestrator import extract_document
from app.extraction.prompts import PROMPT_VERSION
from app.extraction.providers.base import ExtractionProvider, ExtractionProviderError
from app.extraction.schemas import SCHEMA_VERSION
from app.extraction.service import ExtractionError
from app.extraction.transitions import ExtractionStatus
from app.schemas.documents import DocumentCreate
from app.services.documents import DuplicateDocumentError, create_document
from app.taxonomy.categories import VerificationStatus


def evaluation_database_url() -> str:
    value = os.environ.get("EVAL_DATABASE_URL")
    try:
        url = make_url(value or "")
    except ArgumentError:
        raise EvaluationError(
            "Explicit EVAL_DATABASE_URL using postgresql+psycopg is required"
        ) from None
    if url.drivername != "postgresql+psycopg" or not url.database:
        raise EvaluationError("Explicit EVAL_DATABASE_URL using postgresql+psycopg is required")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def isolated_sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    schema = f"chainsec_eval_{uuid4().hex}"
    engine = create_async_engine(database_url, hide_parameters=True)
    created = False
    scoped_engine = None
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        scoped_url = (
            make_url(database_url)
            .update_query_dict({"options": f"-csearch_path={schema} -ctimezone=UTC"})
            .render_as_string(hide_password=False)
        )
        # A separate process avoids altering application configuration or nesting event loops.
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            cwd=REPOSITORY,
            env={**os.environ, "DATABASE_URL": scoped_url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await process.communicate()
        finally:
            if process.returncode is None:
                process.terminate()
                await process.communicate()
        if process.returncode != 0:
            raise EvaluationError("Evaluation schema migration failed")
        scoped_engine = create_async_engine(scoped_url, hide_parameters=True)
        async with scoped_engine.connect() as connection:
            if await connection.scalar(text("SELECT current_schema()")) != schema:
                raise EvaluationError("Evaluation schema isolation failed")
        yield async_sessionmaker(scoped_engine, expire_on_commit=False)
    except SQLAlchemyError:
        raise EvaluationError(
            "Evaluation database operation failed; inspect local database health"
        ) from None
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        try:
            if created:
                async with engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        except SQLAlchemyError:
            raise EvaluationError(
                "Evaluation schema cleanup failed; remove only the orphaned chainsec_eval schema"
            ) from None
        finally:
            await engine.dispose()


async def run_cases(
    dataset: Dataset,
    cases: list[LoadedCase],
    sessions: async_sessionmaker[AsyncSession],
    provider: ExtractionProvider,
) -> Predictions:
    if provider.prompt_version != PROMPT_VERSION or provider.schema_version != SCHEMA_VERSION:
        raise EvaluationError("Evaluation requires the frozen prompt and schema versions")
    commit, dirty = git_provenance()
    predictions = []
    observed_runs: set[UUID] = set()
    for case in cases:
        async with sessions() as session:
            try:
                document = await create_document(
                    session,
                    DocumentCreate(
                        source_name=case.metadata.source_name,
                        source_url=case.metadata.source_url,
                        document_type=case.metadata.document_type,
                        raw_text=case.document,
                        retrieved_at=case.metadata.retrieved_at,
                    ),
                )
                document_id = document.id
            except DuplicateDocumentError:
                async with session.begin():
                    duplicate_id = await session.scalar(
                        select(RawDocument.id).where(
                            RawDocument.content_hash == case.metadata.document_sha256
                        )
                    )
                if duplicate_id is None:
                    raise EvaluationError(
                        "Evaluation ingestion duplicate cannot be recovered"
                    ) from None
                document_id = duplicate_id
        error_code = None
        try:
            await extract_document(sessions, document_id, provider)
        except (ExtractionProviderError, ExtractionError) as error:
            error_code = error.code
        # Read persisted state, including failures; never score transient provider candidates.
        async with sessions() as session, session.begin():
            run = await session.scalar(
                select(ExtractionRun)
                .where(ExtractionRun.raw_document_id == document_id)
                .where(ExtractionRun.id.not_in(observed_runs))
                .order_by(ExtractionRun.created_at.desc(), ExtractionRun.id.desc())
                .limit(1)
            )
            if run is None:
                if error_code is None:
                    raise EvaluationError("Evaluation extraction did not persist run provenance")
                predictions.append(
                    Prediction(
                        case_id=case.metadata.case_id,
                        run_status="INCOMPLETE",
                        failure_code=error_code,
                    )
                )
                continue
            observed_runs.add(run.id)
            if run.status not in {ExtractionStatus.SUCCEEDED, ExtractionStatus.FAILED}:
                predictions.append(
                    Prediction(
                        case_id=case.metadata.case_id,
                        run_status="INCOMPLETE",
                        failure_code=error_code or "EVAL_INCOMPLETE",
                    )
                )
                continue
            stored = list(
                (
                    await session.scalars(
                        select(SecurityFinding)
                        .where(SecurityFinding.extraction_run_id == run.id)
                        .options(selectinload(SecurityFinding.evidence))
                    )
                ).all()
            )
            if any(
                finding.verification_status != VerificationStatus.UNREVIEWED for finding in stored
            ):
                raise EvaluationError("Persisted extraction finding violates initial review state")
            # Stable prediction indices do not depend on random ORM UUIDs.
            stored.sort(
                key=lambda finding: (
                    min(evidence.start_offset for evidence in finding.evidence),
                    finding.title,
                    finding.canonical_category,
                    finding.severity,
                    finding.source_finding_id or "",
                )
            )
            findings = [
                EvaluationFinding(
                    title=finding.title,
                    severity=finding.severity,
                    canonical_category=finding.canonical_category,
                    source_finding_id=finding.source_finding_id,
                    evidence=[
                        Span(
                            source_excerpt=evidence.source_excerpt,
                            start_offset=evidence.start_offset,
                            end_offset=evidence.end_offset,
                        )
                        for evidence in sorted(
                            finding.evidence,
                            key=lambda item: (
                                item.start_offset,
                                item.end_offset,
                                item.source_excerpt,
                            ),
                        )
                    ],
                )
                for finding in stored
            ]
            predictions.append(
                Prediction(
                    case_id=case.metadata.case_id,
                    run_status="SUCCEEDED"
                    if run.status == ExtractionStatus.SUCCEEDED
                    else "FAILED",
                    failure_code=run.failure_code,
                    findings=findings,
                )
            )
    return Predictions(
        dataset_version=dataset.manifest.dataset_version,
        manifest_sha256=dataset.manifest_sha256,
        mode="LIVE",
        git_commit=commit,
        working_tree_dirty=dirty,
        provider=provider.provider_name,
        model=provider.model,
        prompt_version="extract-findings-v1",
        schema_version="finding-output-v1",
        timestamp=datetime.now(UTC),
        cases=predictions,
    )
