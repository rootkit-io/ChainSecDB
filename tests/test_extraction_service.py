import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.extraction_run import ExtractionRun
from app.db.models.finding_evidence import FindingEvidence
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.extraction.schemas import ExtractionOutput, ExtractionRunCreate
from app.extraction.service import (
    FAILURE_MESSAGES,
    ExtractionDuplicateSourceError,
    ExtractionEvidenceError,
    ExtractionOutputValidationError,
    ExtractionPersistenceError,
    ExtractionRunNotFoundError,
    complete_extraction_run,
    create_extraction_run,
    fail_extraction_run,
    start_extraction_run,
)
from app.extraction.transitions import ExtractionStatus, InvalidExtractionTransitionError
from app.schemas.findings import FindingCreate
from app.services.findings import DocumentNotFoundError, create_finding
from app.taxonomy.categories import VerificationStatus


async def assert_failed(session: AsyncSession, run_id, code: str) -> None:
    assert not session.in_transaction()
    async with session.begin():
        run = await session.get(ExtractionRun, run_id, populate_existing=True)
        assert run.status == ExtractionStatus.FAILED
        assert run.failure_code == code
        assert run.failure_message == FAILURE_MESSAGES[code]
        assert run.completed_at >= run.started_at
        assert run.completed_at.tzinfo is not None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SecurityFinding)
                .where(SecurityFinding.extraction_run_id == run_id)
            )
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 0


async def test_creation_start_and_repeated_configuration(
    session: AsyncSession, raw_document: RawDocument, extraction_metadata: ExtractionRunCreate
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    assert run.status == ExtractionStatus.PENDING
    assert run.started_at is None and run.completed_at is None
    assert run.failure_code is None and run.failure_message is None
    assert run.created_at.tzinfo is not None
    for field, value in extraction_metadata.model_dump().items():
        assert getattr(run, field) == value
    second = await create_extraction_run(session, raw_document.id, extraction_metadata)
    assert second.id != run.id
    started = await start_extraction_run(session, run.id)
    assert started.status == ExtractionStatus.RUNNING
    assert started.started_at.tzinfo is not None
    assert started.completed_at is None
    with pytest.raises(InvalidExtractionTransitionError):
        await start_extraction_run(session, run.id)


async def test_successful_completion_provenance_and_api(
    session: AsyncSession,
    client: AsyncClient,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    second = deepcopy(extraction_output["findings"][0])
    second["source_finding_id"] = "H-02"
    second["evidence"][0] = {"source_excerpt": "🔐", "start_offset": 0, "end_offset": 1}
    extraction_output["findings"].append(second)
    completed = await complete_extraction_run(session, run.id, extraction_output)
    assert completed.status == ExtractionStatus.SUCCEEDED
    assert completed.completed_at >= completed.started_at
    assert completed.failure_code is None and completed.failure_message is None
    async with session.begin():
        findings = (await session.scalars(select(SecurityFinding))).all()
        assert len(findings) == 2
        for finding in findings:
            assert finding.extraction_run_id == run.id
            assert finding.raw_document_id == raw_document.id
            assert finding.verification_status == VerificationStatus.UNREVIEWED
            assert finding.source_category == " Oracle Manipulation "
            assert finding.source_severity == " Major "
            response = await client.get(f"/findings/{finding.id}")
            assert response.status_code == 200
            assert response.json()["extraction_run_id"] == str(run.id)
            assert response.json()["verification_status"] == "UNREVIEWED"
            for evidence in response.json()["evidence"]:
                assert (
                    raw_document.raw_text[evidence["start_offset"] : evidence["end_offset"]]
                    == evidence["source_excerpt"]
                )
        assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 2


@pytest.mark.parametrize("code", list(FAILURE_MESSAGES))
async def test_sanitized_explicit_failure(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    code: str,
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    await fail_extraction_run(session, run.id, code)
    await assert_failed(session, run.id, code)
    assert len(run.failure_code) <= 64 and len(run.failure_message) <= 500


@pytest.mark.parametrize(
    "state,operation",
    [
        ("PENDING", "complete"),
        ("PENDING", "fail"),
        ("SUCCEEDED", "start"),
        ("SUCCEEDED", "complete"),
        ("SUCCEEDED", "fail"),
        ("FAILED", "start"),
        ("FAILED", "complete"),
        ("FAILED", "fail"),
    ],
)
async def test_invalid_lifecycle_operations(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    state: str,
    operation: str,
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    run_id = run.id
    if state != "PENDING":
        await start_extraction_run(session, run_id)
        if state == "SUCCEEDED":
            await complete_extraction_run(session, run_id, {"findings": []})
        else:
            await fail_extraction_run(session, run_id, "TIMEOUT")
    with pytest.raises(InvalidExtractionTransitionError):
        if operation == "start":
            await start_extraction_run(session, run_id)
        elif operation == "complete":
            await complete_extraction_run(session, run_id, {"findings": []})
        else:
            await fail_extraction_run(session, run_id, "TIMEOUT")
    async with session.begin():
        saved = await session.get(ExtractionRun, run_id, populate_existing=True)
        assert saved.status.value == state


@pytest.mark.parametrize("operation", ["start", "complete", "fail"])
async def test_missing_run(session: AsyncSession, operation: str) -> None:
    with pytest.raises(ExtractionRunNotFoundError):
        if operation == "start":
            await start_extraction_run(session, uuid4())
        elif operation == "complete":
            await complete_extraction_run(session, uuid4(), {"findings": []})
        else:
            await fail_extraction_run(session, uuid4(), "TIMEOUT")


async def test_missing_document(
    session: AsyncSession, extraction_metadata: ExtractionRunCreate
) -> None:
    with pytest.raises(DocumentNotFoundError):
        await create_extraction_run(session, uuid4(), extraction_metadata)


async def test_unknown_failure_text_never_persisted(
    session: AsyncSession, raw_document: RawDocument, extraction_metadata: ExtractionRunCreate
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    with pytest.raises(ValueError, match="^Unsupported extraction failure code$"):
        await fail_extraction_run(session, run.id, "Authorization: Bearer private-secret" * 100)
    async with session.begin():
        saved = await session.get(ExtractionRun, run.id)
        assert saved.status == ExtractionStatus.RUNNING
        assert saved.failure_code is None and saved.failure_message is None


@pytest.mark.parametrize(
    "changes",
    [{"severity": "private-secret"}, {"verification_status": "VERIFIED"}, {"evidence": []}],
)
async def test_invalid_output_is_failed_without_partial_findings(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
    changes: dict,
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    bad = deepcopy(extraction_output["findings"][0])
    bad.update(changes)
    extraction_output["findings"].append(bad)
    with pytest.raises(
        ExtractionOutputValidationError, match="^Structured extraction output is invalid.$"
    ):
        await complete_extraction_run(session, run.id, extraction_output)
    await assert_failed(session, run.id, "INVALID_OUTPUT")


@pytest.mark.parametrize(
    "evidence",
    [
        {"source_excerpt": "Report.", "start_offset": 2, "end_offset": 999},
        {"source_excerpt": "Report.", "start_offset": 3, "end_offset": 10},
        {"source_excerpt": " Report.", "start_offset": 2, "end_offset": 10},
        {"source_excerpt": "report.", "start_offset": 2, "end_offset": 9},
        {"source_excerpt": "Report. ", "start_offset": 2, "end_offset": 10},
    ],
)
async def test_bad_evidence_is_failed_atomically(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
    evidence: dict,
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    bad = deepcopy(extraction_output["findings"][0])
    bad["source_finding_id"] = "H-02"
    bad["evidence"].append(evidence)
    extraction_output["findings"].append(bad)
    with pytest.raises(ExtractionEvidenceError):
        await complete_extraction_run(session, run.id, extraction_output)
    await assert_failed(session, run.id, "EVIDENCE_MISMATCH")


@pytest.mark.parametrize("existing_manual", [False, True])
async def test_duplicate_source_id_fails_whole_batch(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
    existing_manual: bool,
) -> None:
    if existing_manual:
        await create_finding(
            session,
            raw_document.id,
            FindingCreate(
                title="Manual", severity="LOW", canonical_category="OTHER", source_finding_id="H-01"
            ),
        )
    else:
        extraction_output["findings"].append(deepcopy(extraction_output["findings"][0]))
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    with pytest.raises(ExtractionDuplicateSourceError):
        await complete_extraction_run(session, run.id, extraction_output)
    await assert_failed(session, run.id, "DUPLICATE_SOURCE_FINDING")
    async with session.begin():
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == int(
            existing_manual
        )


async def test_database_failure_rolls_back_candidates_and_records_failed(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    second = deepcopy(extraction_output["findings"][0])
    second.update(source_finding_id="H-02", title="Fail storage")
    extraction_output["findings"].append(second)
    async with session.begin():
        await session.execute(
            text(
                "ALTER TABLE security_findings ADD CONSTRAINT test_storage_failure "
                "CHECK (title <> 'Fail storage')"
            )
        )
    try:
        with pytest.raises(ExtractionPersistenceError, match="^Extraction persistence failed.$"):
            await complete_extraction_run(session, run.id, extraction_output)
        await assert_failed(session, run.id, "PERSISTENCE_ERROR")
    finally:
        async with session.begin():
            await session.execute(
                text("ALTER TABLE security_findings DROP CONSTRAINT test_storage_failure")
            )


async def test_mutated_output_revalidated_by_service(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    output = ExtractionOutput.model_validate(extraction_output)
    output.findings[0].evidence.clear()
    with pytest.raises(ExtractionOutputValidationError):
        await complete_extraction_run(session, run.id, output)
    await assert_failed(session, run.id, "INVALID_OUTPUT")


async def test_commit_failure_has_no_partial_findings(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    run_id = run.id
    await start_extraction_run(session, run_id)
    second = deepcopy(extraction_output["findings"][0])
    second["source_finding_id"] = "H-02"
    extraction_output["findings"].append(second)
    async with session.begin():
        await session.execute(
            text(
                "ALTER TABLE security_findings ADD CONSTRAINT test_commit_failure "
                "UNIQUE (title) DEFERRABLE INITIALLY DEFERRED"
            )
        )
    try:
        with pytest.raises(ExtractionPersistenceError):
            await complete_extraction_run(session, run_id, extraction_output)
        async with session.begin():
            saved = await session.get(ExtractionRun, run_id, populate_existing=True)
            assert saved.status == ExtractionStatus.RUNNING
            assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0
            assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 0
    finally:
        async with session.begin():
            await session.execute(
                text("ALTER TABLE security_findings DROP CONSTRAINT test_commit_failure")
            )


@pytest.mark.parametrize("field", ["start_offset", "end_offset"])
async def test_missing_offsets_fail_run_without_inference(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
    field: str,
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    del extraction_output["findings"][0]["evidence"][0][field]
    with pytest.raises(ExtractionOutputValidationError):
        await complete_extraction_run(session, run.id, extraction_output)
    await assert_failed(session, run.id, "INVALID_OUTPUT")


@pytest.mark.parametrize("findings,evidence", [(100, 1), (1, 100)])
async def test_maximum_valid_batch_persists(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
    findings: int,
    evidence: int,
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    original = extraction_output["findings"][0]
    batch = []
    for index in range(findings):
        item = deepcopy(original)
        item["source_finding_id"] = f"H-{index}"
        item["evidence"] *= evidence
        batch.append(item)
    await complete_extraction_run(session, run.id, {"findings": batch})
    async with session.begin():
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == findings
        assert (
            await session.scalar(select(func.count()).select_from(FindingEvidence))
            == findings * evidence
        )


@pytest.mark.parametrize("competitor", ["complete", "fail"])
async def test_concurrent_terminal_transitions_refresh_stale_instances(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
    competitor: str,
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    barrier = asyncio.Barrier(2)

    async def finish(operation: str):
        async with session_factory() as active:
            stale = await active.get(ExtractionRun, run.id)
            assert stale.status == ExtractionStatus.RUNNING
            await active.commit()
            await barrier.wait()
            if operation == "complete":
                return await complete_extraction_run(active, run.id, extraction_output)
            return await fail_extraction_run(active, run.id, "TIMEOUT")

    results = await asyncio.wait_for(
        asyncio.gather(finish("complete"), finish(competitor), return_exceptions=True), timeout=10
    )
    assert sum(isinstance(result, ExtractionRun) for result in results) == 1
    assert sum(isinstance(result, InvalidExtractionTransitionError) for result in results) == 1
    async with session.begin():
        saved = await session.get(ExtractionRun, run.id, populate_existing=True)
        count = await session.scalar(select(func.count()).select_from(SecurityFinding))
        assert count == int(saved.status == ExtractionStatus.SUCCEEDED)


async def test_manual_creation_stays_manual(client: AsyncClient, raw_document: RawDocument) -> None:
    payload = {"title": "Manual", "severity": "UNKNOWN", "canonical_category": "OTHER"}
    response = await client.post(f"/documents/{raw_document.id}/findings", json=payload)
    assert response.status_code == 201
    assert response.json()["extraction_run_id"] is None
    assert response.json()["verification_status"] == "UNREVIEWED"
    assert response.json()["evidence"] == []
    rejected = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={**payload, "extraction_run_id": str(uuid4())},
    )
    assert rejected.status_code == 422
