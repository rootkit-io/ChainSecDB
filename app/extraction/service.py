from datetime import UTC, datetime
from uuid import UUID

from psycopg.errors import UniqueViolation
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.extraction_run import ExtractionRun
from app.db.models.finding_evidence import FindingEvidence
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.extraction.schemas import ExtractionOutput, ExtractionRunCreate
from app.extraction.transitions import ExtractionStatus, require_transition
from app.services.findings import DocumentNotFoundError, EvidenceIntegrityError, resolve_evidence
from app.taxonomy.categories import VerificationStatus

FAILURE_MESSAGES = {
    "PROVIDER_ERROR": "Provider execution failed.",
    "PROVIDER_TIMEOUT": "Provider request timed out.",
    "PROVIDER_RATE_LIMIT": "Provider rate limit exceeded.",
    "PROVIDER_AUTH": "Provider authentication failed.",
    "PROVIDER_CONNECTION": "Provider connection failed.",
    "PROVIDER_REFUSAL": "Provider refused extraction.",
    "TIMEOUT": "Extraction execution timed out.",
    "INVALID_OUTPUT": "Structured extraction output is invalid.",
    "EVIDENCE_MISMATCH": "Extraction evidence does not match the source document.",
    "DUPLICATE_SOURCE_FINDING": "Source finding ID already exists for document.",
    "PERSISTENCE_ERROR": "Extraction persistence failed.",
}


class ExtractionRunNotFoundError(Exception):
    pass


class ExtractionError(Exception):
    code: str

    def __init__(self) -> None:
        super().__init__(FAILURE_MESSAGES[self.code])


class ExtractionOutputValidationError(ExtractionError):
    code = "INVALID_OUTPUT"


class ExtractionEvidenceError(ExtractionError):
    code = "EVIDENCE_MISMATCH"


class ExtractionDuplicateSourceError(ExtractionError):
    code = "DUPLICATE_SOURCE_FINDING"


class ExtractionPersistenceError(ExtractionError):
    code = "PERSISTENCE_ERROR"


async def _locked_run(session: AsyncSession, run_id: UUID) -> ExtractionRun:
    run = await session.scalar(
        select(ExtractionRun)
        .where(ExtractionRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ExtractionRunNotFoundError("Extraction run not found")
    return run


def _record_failure(run: ExtractionRun, code: str) -> None:
    run.status = ExtractionStatus.FAILED
    run.completed_at = datetime.now(UTC)
    run.failure_code = code
    run.failure_message = FAILURE_MESSAGES[code]


async def create_extraction_run(
    session: AsyncSession, document_id: UUID, payload: ExtractionRunCreate
) -> ExtractionRun:
    payload = ExtractionRunCreate.model_validate(payload)
    try:
        async with session.begin():
            document = await session.scalar(
                select(RawDocument.id)
                .where(RawDocument.id == document_id)
                .with_for_update(read=True)
            )
            if document is None:
                raise DocumentNotFoundError("Document not found")
            run = ExtractionRun(
                raw_document_id=document_id,
                status=ExtractionStatus.PENDING,
                **payload.model_dump(),
            )
            session.add(run)
            await session.flush()
    except SQLAlchemyError:
        raise ExtractionPersistenceError from None
    return run


async def start_extraction_run(session: AsyncSession, run_id: UUID) -> ExtractionRun:
    try:
        async with session.begin():
            run = await _locked_run(session, run_id)
            require_transition(run.status, ExtractionStatus.RUNNING)
            run.status = ExtractionStatus.RUNNING
            run.started_at = datetime.now(UTC)
            run.completed_at = None
            run.failure_code = None
            run.failure_message = None
            await session.flush()
    except SQLAlchemyError:
        raise ExtractionPersistenceError from None
    return run


async def fail_extraction_run(
    session: AsyncSession, run_id: UUID, failure_code: str
) -> ExtractionRun:
    if failure_code not in FAILURE_MESSAGES:
        raise ValueError("Unsupported extraction failure code")
    try:
        async with session.begin():
            run = await _locked_run(session, run_id)
            require_transition(run.status, ExtractionStatus.FAILED)
            _record_failure(run, failure_code)
            await session.flush()
    except SQLAlchemyError:
        raise ExtractionPersistenceError from None
    return run


async def complete_extraction_run(
    session: AsyncSession, run_id: UUID, structured_output: object
) -> ExtractionRun:
    error: ExtractionError | None = None
    try:
        async with session.begin():
            run = await _locked_run(session, run_id)
            require_transition(run.status, ExtractionStatus.SUCCEEDED)
            document = await session.scalar(
                select(RawDocument)
                .where(RawDocument.id == run.raw_document_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
            if document is None:
                raise DocumentNotFoundError("Document not found")
            try:
                output = ExtractionOutput.model_validate(structured_output)
                findings = []
                for item in output.findings:
                    finding = SecurityFinding(
                        raw_document_id=run.raw_document_id,
                        extraction_run_id=run.id,
                        verification_status=VerificationStatus.UNREVIEWED,
                        **item.model_dump(exclude={"evidence"}),
                    )
                    finding.evidence = []
                    for evidence in item.evidence:
                        resolve_evidence(document.raw_text, evidence)
                        finding.evidence.append(FindingEvidence(**evidence.model_dump()))
                    findings.append(finding)
            except ValidationError:
                error = ExtractionOutputValidationError()
            except EvidenceIntegrityError:
                error = ExtractionEvidenceError()

            if error is None:
                try:
                    # Keep the run lock while discarding the entire candidate batch on failure.
                    async with session.begin_nested():
                        session.add_all(findings)
                        await session.flush()
                except IntegrityError as exc:
                    if (
                        isinstance(exc.orig, UniqueViolation)
                        and exc.orig.diag.constraint_name
                        == "uq_security_findings_document_source_id"
                    ):
                        error = ExtractionDuplicateSourceError()
                    else:
                        error = ExtractionPersistenceError()
                except SQLAlchemyError:
                    error = ExtractionPersistenceError()

            if error is not None:
                _record_failure(run, error.code)
            else:
                run.status = ExtractionStatus.SUCCEEDED
                run.completed_at = datetime.now(UTC)
                run.failure_code = None
                run.failure_message = None
            await session.flush()
    except SQLAlchemyError:
        raise ExtractionPersistenceError from None
    if error is not None:
        raise error from None
    return run
