from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.raw_document import RawDocument
from app.extraction.schemas import ExtractionRunCreate
from app.extraction.service import (
    complete_extraction_run,
    create_extraction_run,
    start_extraction_run,
)

RUN_INSERT = text("""
    INSERT INTO extraction_runs (
        id, raw_document_id, provider, model, prompt_version, schema_version,
        status, started_at, completed_at, failure_code, failure_message
    ) VALUES (
        :id, :raw_document_id, :provider, :model, :prompt_version, :schema_version,
        :status, :started_at, :completed_at, :failure_code, :failure_message
    )
""")


@pytest.mark.parametrize(
    "changes,sqlstate",
    [
        ({"raw_document_id": None}, "23502"),
        ({"raw_document_id": uuid4()}, "23503"),
        ({"provider": None}, "23502"),
        ({"model": None}, "23502"),
        ({"prompt_version": None}, "23502"),
        ({"schema_version": None}, "23502"),
        ({"provider": " \t\n "}, "23514"),
        ({"model": ""}, "23514"),
        ({"prompt_version": ""}, "23514"),
        ({"schema_version": ""}, "23514"),
        ({"provider": "x" * 256}, "22001"),
        ({"status": None}, "23502"),
        ({"status": "UNKNOWN"}, "23514"),
        ({"started_at": datetime.now(UTC)}, "23514"),
        ({"status": "RUNNING"}, "23514"),
        ({"status": "SUCCEEDED"}, "23514"),
        ({"status": "FAILED"}, "23514"),
        ({"failure_code": "TIMEOUT"}, "23514"),
        ({"failure_message": "private"}, "23514"),
        (
            {
                "status": "SUCCEEDED",
                "started_at": datetime.now(UTC),
                "completed_at": datetime.now(UTC),
                "failure_code": "TIMEOUT",
            },
            "23514",
        ),
        (
            {
                "status": "FAILED",
                "started_at": datetime.now(UTC),
                "completed_at": datetime.now(UTC) - timedelta(days=1),
            },
            "23514",
        ),
        (
            {
                "status": "FAILED",
                "started_at": datetime.now(UTC),
                "completed_at": datetime.now(UTC),
                "failure_message": "x" * 501,
            },
            "22001",
        ),
    ],
)
async def test_run_constraints_at_postgresql_boundary(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    changes: dict,
    sqlstate: str,
) -> None:
    values = {
        "id": uuid4(),
        "raw_document_id": raw_document.id,
        **extraction_metadata.model_dump(),
        "status": "PENDING",
        "started_at": None,
        "completed_at": None,
        "failure_code": None,
        "failure_message": None,
        **changes,
    }
    with pytest.raises(DBAPIError) as error:
        async with session.begin():
            await session.execute(RUN_INSERT, values)
    assert error.value.orig.sqlstate == sqlstate


async def test_document_deletion_restricted_by_run_alone(
    session: AsyncSession, raw_document: RawDocument, extraction_metadata: ExtractionRunCreate
) -> None:
    await create_extraction_run(session, raw_document.id, extraction_metadata)
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(
                text("DELETE FROM raw_documents WHERE id=:id"), {"id": raw_document.id}
            )
    assert error.value.orig.sqlstate == "23503"


async def test_run_deletion_restricted_by_findings(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    extraction_output: dict[str, object],
) -> None:
    run = await create_extraction_run(session, raw_document.id, extraction_metadata)
    await start_extraction_run(session, run.id)
    await complete_extraction_run(session, run.id, extraction_output)
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(text("DELETE FROM extraction_runs WHERE id=:id"), {"id": run.id})
    assert error.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("cross_document", [False, True])
async def test_extraction_finding_fk_rejects_missing_or_wrong_document_run(
    session: AsyncSession,
    raw_document: RawDocument,
    extraction_metadata: ExtractionRunCreate,
    cross_document: bool,
) -> None:
    run_id = uuid4()
    if cross_document:
        other_id = uuid4()
        async with session.begin():
            session.add(
                RawDocument(
                    id=other_id,
                    source_name="manual",
                    document_type="report",
                    raw_text="Other source.",
                    content_hash="e" * 64,
                )
            )
        run = await create_extraction_run(session, other_id, extraction_metadata)
        run_id = run.id
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(
                text("""
                INSERT INTO security_findings (
                    id, raw_document_id, extraction_run_id, title, severity, canonical_category
                ) VALUES (:id, :document_id, :run_id, 'Finding', 'HIGH', 'OTHER')
            """),
                {"id": uuid4(), "document_id": raw_document.id, "run_id": run_id},
            )
    assert error.value.orig.sqlstate == "23503"
    assert error.value.orig.diag.constraint_name == "fk_security_findings_extraction_document"
