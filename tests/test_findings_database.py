from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.finding_evidence import FindingEvidence
from app.db.models.raw_document import RawDocument
from app.schemas.findings import EvidenceCreate, FindingCreate
from app.services.findings import create_finding

FINDING_INSERT = text("""
    INSERT INTO security_findings (
        id, raw_document_id, source_finding_id, title, severity, canonical_category,
        verification_status, line_start, line_end
    ) VALUES (
        :id, :raw_document_id, :source_finding_id, :title, :severity, :canonical_category,
        :verification_status, :line_start, :line_end
    )
""")


@pytest.mark.parametrize(
    "changes,sqlstate,constraint",
    [
        ({"title": " \t\n "}, "23514", "ck_security_findings_title"),
        ({"severity": "Major"}, "23514", "ck_security_findings_severity"),
        ({"canonical_category": "INVALID"}, "23514", "ck_security_findings_canonical_category"),
        ({"verification_status": "APPROVED"}, "23514", "ck_security_findings_verification_status"),
        ({"line_start": 0}, "23514", "ck_security_findings_line_start"),
        ({"line_end": 0}, "23514", "ck_security_findings_line_end"),
        ({"line_start": 20, "line_end": 10}, "23514", "ck_security_findings_line_range"),
        ({"raw_document_id": None}, "23502", None),
        ({"title": None}, "23502", None),
        ({"severity": None}, "23502", None),
        ({"canonical_category": None}, "23502", None),
        ({"verification_status": None}, "23502", None),
    ],
)
async def test_finding_constraints_at_postgresql_boundary(
    session: AsyncSession,
    raw_document: RawDocument,
    changes: dict[str, object],
    sqlstate: str,
    constraint: str | None,
) -> None:
    values = {
        "id": uuid4(),
        "raw_document_id": raw_document.id,
        "source_finding_id": None,
        "title": "Finding",
        "severity": "HIGH",
        "canonical_category": "OTHER",
        "verification_status": "UNREVIEWED",
        "line_start": None,
        "line_end": None,
        **changes,
    }
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(FINDING_INSERT, values)
    assert error.value.orig.sqlstate == sqlstate
    if constraint:
        assert error.value.orig.diag.constraint_name == constraint


async def test_source_id_unique_at_database_boundary(
    session: AsyncSession,
    raw_document: RawDocument,
) -> None:
    values = {
        "id": uuid4(),
        "raw_document_id": raw_document.id,
        "source_finding_id": "H-01",
        "title": "Finding",
        "severity": "HIGH",
        "canonical_category": "OTHER",
        "verification_status": "UNREVIEWED",
        "line_start": None,
        "line_end": None,
    }
    async with session.begin():
        await session.execute(FINDING_INSERT, values)
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(FINDING_INSERT, {**values, "id": uuid4()})
    assert error.value.orig.sqlstate == "23505"
    assert error.value.orig.diag.constraint_name == "uq_security_findings_document_source_id"


async def test_missing_document_foreign_key_at_database_boundary(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(
                FINDING_INSERT,
                {
                    "id": uuid4(),
                    "raw_document_id": uuid4(),
                    "source_finding_id": None,
                    "title": "Finding",
                    "severity": "HIGH",
                    "canonical_category": "OTHER",
                    "verification_status": "UNREVIEWED",
                    "line_start": None,
                    "line_end": None,
                },
            )
    assert error.value.orig.sqlstate == "23503"


@pytest.mark.parametrize(
    "changes,sqlstate,constraint",
    [
        ({"start_offset": -1}, "23514", "ck_finding_evidence_start_offset"),
        ({"end_offset": 2}, "23514", "ck_finding_evidence_offset_range"),
        ({"evidence_type": "URL"}, "23514", "ck_finding_evidence_type"),
        ({"field_name": "imaginary_field"}, "23514", "ck_finding_evidence_field_name"),
        ({"finding_id": None}, "23502", None),
        ({"finding_id": "00000000-0000-0000-0000-000000000000"}, "23503", None),
        ({"source_excerpt": None}, "23502", None),
        ({"start_offset": None}, "23502", None),
        ({"end_offset": None}, "23502", None),
    ],
)
async def test_evidence_constraints_at_postgresql_boundary(
    session: AsyncSession,
    raw_document: RawDocument,
    changes: dict[str, object],
    sqlstate: str,
    constraint: str | None,
) -> None:
    finding = await create_finding(
        session,
        raw_document.id,
        FindingCreate(
            title="Finding",
            severity="HIGH",
            canonical_category="OTHER",
        ),
    )
    values = {
        "id": uuid4(),
        "finding_id": finding.id,
        "evidence_type": "SOURCE_TEXT",
        "field_name": "root_cause",
        "source_excerpt": "Report.",
        "start_offset": 2,
        "end_offset": 9,
        **changes,
    }
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(
                text("""
                INSERT INTO finding_evidence (
                    id, finding_id, evidence_type, field_name,
                    source_excerpt, start_offset, end_offset
                ) VALUES (
                    :id, :finding_id, :evidence_type, :field_name,
                    :source_excerpt, :start_offset, :end_offset
                )
            """),
                values,
            )
    assert error.value.orig.sqlstate == sqlstate
    if constraint:
        assert error.value.orig.diag.constraint_name == constraint


async def test_source_document_deletion_restricted(
    session: AsyncSession,
    raw_document: RawDocument,
) -> None:
    document_id = raw_document.id
    await create_finding(
        session,
        document_id,
        FindingCreate(
            title="Finding",
            severity="HIGH",
            canonical_category="OTHER",
        ),
    )
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            await session.execute(
                text("DELETE FROM raw_documents WHERE id = :id"), {"id": document_id}
            )
    assert error.value.orig.sqlstate == "23503"
    assert await session.scalar(select(RawDocument.id)) == document_id


async def test_finding_deletion_cascades_evidence_preserves_document(
    session: AsyncSession,
    raw_document: RawDocument,
) -> None:
    document_id = raw_document.id
    finding = await create_finding(
        session,
        document_id,
        FindingCreate(
            title="Finding",
            severity="HIGH",
            canonical_category="OTHER",
            evidence=[EvidenceCreate(source_excerpt="Report.")],
        ),
    )
    finding_id = finding.id
    async with session.begin():
        await session.execute(
            text("DELETE FROM security_findings WHERE id = :id"), {"id": finding_id}
        )
        assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 0
        assert await session.scalar(select(RawDocument.id)) == document_id
