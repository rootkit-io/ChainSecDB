import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models.extraction_run import ExtractionRun
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.evaluation.dataset import REPOSITORY, EvaluationError, load_dataset, select_cases
from app.evaluation.report import build_report
from app.evaluation.runner import isolated_sessions, run_cases
from app.extraction.providers.base import ExtractionProviderError
from app.extraction.schemas import ExtractionOutput
from app.extraction.transitions import ExtractionStatus
from app.taxonomy.categories import VerificationStatus

CORPUS = REPOSITORY / "evals/datasets/v1"


class FixtureProvider:
    provider_name = "offline-integration-fixture"
    model = "deterministic-fixture"
    prompt_version = "extract-findings-v1"
    schema_version = "finding-output-v1"

    def __init__(self, dataset, failures=None, bad_evidence=False):
        self.dataset = dataset
        self.failures = failures or set()
        self.bad_evidence = bad_evidence
        self.calls = []

    async def extract(self, raw_text):
        case = next(case for case in self.dataset.cases if case.document == raw_text)
        self.calls.append(case.metadata.case_id)
        if case.metadata.case_id in self.failures:
            raise ExtractionProviderError("PROVIDER_TIMEOUT")
        data = [finding.model_dump(exclude={"gold_id", "notes"}) for finding in case.gold.findings]
        if self.bad_evidence:
            data[0]["evidence"][0]["start_offset"] = 0
        return ExtractionOutput.model_validate({"findings": data})


async def test_existing_pipeline_persists_documents_runs_and_unreviewed_findings(session_factory):
    dataset = load_dataset(CORPUS)
    cases = select_cases(dataset, "all", ["unicode", "multiple", "negative-glossary"])
    provider = FixtureProvider(dataset)
    artifact = await run_cases(dataset, cases, session_factory, provider)
    assert provider.calls == [case.metadata.case_id for case in cases]
    assert len(artifact.cases) == 3
    async with session_factory() as session, session.begin():
        documents = list((await session.scalars(select(RawDocument))).all())
        findings = list((await session.scalars(select(SecurityFinding))).all())
        runs = list((await session.scalars(select(ExtractionRun))).all())
        assert len(documents) == len(runs) == 3
        assert len(findings) == 6
        assert {document.raw_text for document in documents} == {case.document for case in cases}
        assert {document.content_hash for document in documents} == {
            case.metadata.document_sha256 for case in cases
        }
        assert all(
            finding.verification_status == VerificationStatus.UNREVIEWED for finding in findings
        )
        assert all(finding.extraction_run_id in {run.id for run in runs} for finding in findings)
        assert all(run.status == ExtractionStatus.SUCCEEDED for run in runs)
        assert all(
            run.model == provider.model and run.provider == provider.provider_name for run in runs
        )
    report = build_report(dataset, artifact)
    assert report["partial_selection"] is True
    assert report["automated_metrics"]["synthetic"]["all"]["recall"] == 1
    assert report["automated_metrics"]["synthetic"]["all"]["evidence_containment_validity"] == 1
    assert report["automated_metrics"]["real_world"]["all"]["case_count"] == 0


async def test_failed_case_not_retried_and_candidates_never_scored(session_factory):
    dataset = load_dataset(CORPUS)
    cases = select_cases(dataset, "all", ["single", "unicode"])
    provider = FixtureProvider(dataset, failures={"single"})
    artifact = await run_cases(dataset, cases, session_factory, provider)
    assert provider.calls == ["single", "unicode"]
    assert artifact.cases[0].run_status == "FAILED"
    assert artifact.cases[0].failure_code == "PROVIDER_TIMEOUT"
    assert artifact.cases[0].findings == []
    async with session_factory() as session, session.begin():
        assert await session.scalar(select(func.count()).select_from(ExtractionRun)) == 2
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 2
    assert "Provider request timed out" not in artifact.model_dump_json()
    assert "api_key" not in artifact.model_dump_json()
    result = build_report(dataset, artifact)["automated_metrics"]["synthetic"]["all"]
    assert result["reliability"]["failure_rate"] == 0.5
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["precision"] == 1


async def test_production_exact_evidence_rejects_case_atomically(session_factory):
    dataset = load_dataset(CORPUS)
    provider = FixtureProvider(dataset, bad_evidence=True)
    artifact = await run_cases(
        dataset, select_cases(dataset, "all", ["multiple"]), session_factory, provider
    )
    assert provider.calls == ["multiple"]
    assert artifact.cases[0].run_status == "FAILED"
    assert artifact.cases[0].failure_code == "EVIDENCE_MISMATCH"
    assert artifact.cases[0].findings == []
    async with session_factory() as session, session.begin():
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0
        assert await session.scalar(select(ExtractionRun.status)) == ExtractionStatus.FAILED


async def test_negative_selected_case_executes_once_and_scores_zero(session_factory):
    dataset = load_dataset(CORPUS)
    provider = FixtureProvider(dataset)
    artifact = await run_cases(
        dataset, select_cases(dataset, "holdout", ["negative-status"]), session_factory, provider
    )
    assert provider.calls == ["negative-status"]
    result = build_report(dataset, artifact)
    assert result["executed_case_ids"] == ["negative-status"]
    assert (
        result["automated_metrics"]["synthetic"]["all"]["negative_cases"]["returning_zero_findings"]
        == 1
    )


async def test_changed_prompt_or_schema_rejected_before_execution(session_factory):
    dataset = load_dataset(CORPUS)
    provider = FixtureProvider(dataset)
    provider.prompt_version = "extract-findings-v2"
    with pytest.raises(EvaluationError, match="frozen"):
        await run_cases(dataset, dataset.cases, session_factory, provider)
    assert provider.calls == []


def unscoped_url(url):
    return make_url(url).difference_update_query(["options"]).render_as_string(hide_password=False)


async def test_isolated_schema_migrates_pipeline_and_preserves_existing_data(
    database_url, session_factory, raw_document, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    import os

    original = os.environ["DATABASE_URL"]
    root_url = unscoped_url(database_url)
    engine = create_async_engine(root_url, hide_parameters=True)
    dataset = load_dataset(CORPUS)
    schema = None
    try:
        async with isolated_sessions(root_url) as sessions:
            async with sessions() as session, session.begin():
                schema = await session.scalar(text("SELECT current_schema()"))
                assert schema.startswith("chainsec_eval_")
                assert (
                    await session.scalar(text("SELECT version_num FROM alembic_version"))
                    == "0003_extraction_runs"
                )
                assert await session.scalar(select(func.count()).select_from(RawDocument)) == 0
            provider = FixtureProvider(dataset)
            artifact = await run_cases(
                dataset, select_cases(dataset, "all", ["single"]), sessions, provider
            )
            assert artifact.cases[0].run_status == "SUCCEEDED"
            assert os.environ["DATABASE_URL"] == original
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM pg_namespace WHERE nspname=:schema"),
                    {"schema": schema},
                )
                == 0
            )
        async with session_factory() as session, session.begin():
            assert await session.scalar(select(func.count()).select_from(RawDocument)) == 1
            assert await session.scalar(select(RawDocument.id)) == raw_document.id
            assert await session.scalar(select(func.count()).select_from(ExtractionRun)) == 0
    finally:
        await engine.dispose()


async def test_isolated_schema_cleanup_on_evaluation_exception(database_url):
    root_url = unscoped_url(database_url)
    engine = create_async_engine(root_url, hide_parameters=True)
    schema = None
    try:
        with pytest.raises(EvaluationError, match="intentional"):
            async with isolated_sessions(root_url) as sessions:
                async with sessions() as session:
                    schema = await session.scalar(text("SELECT current_schema()"))
                raise EvaluationError("intentional evaluation stop")
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM pg_namespace WHERE nspname=:schema"),
                    {"schema": schema},
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_migration_error_sanitized_and_schema_dropped(database_url, monkeypatch):
    import asyncio

    class FailedMigration:
        returncode = 1

        async def communicate(self):
            return b"SQL and credentials must not leak", b"postgresql://user:secret@host/db"

    async def failed(*args, **kwargs):
        return FailedMigration()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failed)
    root_url = unscoped_url(database_url)
    engine = create_async_engine(root_url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            before = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'chainsec_eval_%'"
                        )
                    )
                ).all()
            )
        with pytest.raises(EvaluationError, match="Evaluation schema migration failed") as error:
            async with isolated_sessions(root_url):
                pytest.fail("Migration failure must not yield sessions")
        assert "secret" not in str(error.value)
        async with engine.connect() as connection:
            after = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'chainsec_eval_%'"
                        )
                    )
                ).all()
            )
        assert after == before
    finally:
        await engine.dispose()


async def test_production_provider_network_guard_blocks_real_transport():
    import httpx2

    async with httpx2.AsyncClient() as client:
        with pytest.raises(AssertionError, match="real HTTP"):
            await client.get("https://api.openai.com/v1/models")


async def test_live_cli_uses_selected_cases_and_ignored_reports(
    database_url, monkeypatch, tmp_path, capsys
):
    from argparse import Namespace

    from app.evaluation.cli import live_run

    dataset = load_dataset(CORPUS)
    provider = FixtureProvider(dataset)
    monkeypatch.setenv("EVAL_DATABASE_URL", unscoped_url(database_url))
    monkeypatch.setattr("app.evaluation.cli.OpenAIExtractionProvider", lambda: provider)
    monkeypatch.setattr("app.evaluation.cli.output_path", lambda requested: tmp_path)
    await live_run(
        Namespace(live=True, dataset=CORPUS, split="dev", case_id=["single"], output=None)
    )
    assert provider.calls == ["single"]
    lines = capsys.readouterr().out.splitlines()
    preview = json.loads(lines[0])
    assert preview == {
        "case_count": 1,
        "provider": provider.provider_name,
        "model": provider.model,
        "prompt_version": "extract-findings-v1",
        "dataset_version": "chainsec-eval-v1",
    }
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["partial_selection"] is True
    assert report["executed_case_ids"] == ["single"]
    assert (tmp_path / "predictions.json").exists()


async def test_cancelled_migration_terminates_child_and_drops_schema(database_url, monkeypatch):
    import asyncio

    class InterruptedMigration:
        returncode = None
        terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        async def communicate(self):
            if not self.terminated:
                raise asyncio.CancelledError
            return b"", b""

    child = InterruptedMigration()

    async def interrupted(*args, **kwargs):
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", interrupted)
    root_url = unscoped_url(database_url)
    engine = create_async_engine(root_url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            before = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'chainsec_eval_%'"
                        )
                    )
                ).all()
            )
        with pytest.raises(asyncio.CancelledError):
            async with isolated_sessions(root_url):
                pytest.fail("Cancelled migrations cannot yield sessions")
        assert child.terminated is True
        async with engine.connect() as connection:
            after = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'chainsec_eval_%'"
                        )
                    )
                ).all()
            )
        assert after == before
    finally:
        await engine.dispose()


async def test_duplicate_document_cannot_reuse_previous_run_after_start_failure(
    session_factory, monkeypatch
):
    from dataclasses import replace

    from app.extraction.orchestrator import extract_document
    from app.extraction.service import ExtractionPersistenceError

    dataset = load_dataset(CORPUS)
    first = dataset.cases[0]
    second = replace(dataset.cases[1], document=first.document, gold=first.gold)
    second.metadata.document_sha256 = first.metadata.document_sha256
    attempts = 0

    async def start_failure(sessions, document_id, provider):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise ExtractionPersistenceError
        return await extract_document(sessions, document_id, provider)

    monkeypatch.setattr("app.evaluation.runner.extract_document", start_failure)
    provider = FixtureProvider(dataset)
    artifact = await run_cases(dataset, [first, second], session_factory, provider)
    assert attempts == 2
    assert artifact.cases[0].run_status == "SUCCEEDED"
    assert artifact.cases[1].run_status == "INCOMPLETE"
    assert artifact.cases[1].failure_code == "PERSISTENCE_ERROR"
    assert artifact.cases[1].findings == []
    assert provider.calls == ["single"]
