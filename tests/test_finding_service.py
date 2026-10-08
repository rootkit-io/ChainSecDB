import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.finding_evidence import FindingEvidence
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.schemas.findings import EvidenceCreate, FindingCreate
from app.services.findings import (
    DuplicateSourceFindingError,
    EvidenceIntegrityError,
    create_finding,
    resolve_evidence,
)


@pytest.mark.parametrize(
    "raw,excerpt,offsets",
    [
        ("café 🔐 report", "🔐 report", (5, 13)),
        (" \r\nexcerpt\t ", "\r\nexcerpt\t", (1, 11)),
        ("unique", "unique", (0, 6)),
    ],
)
def test_unique_excerpt_derived_exactly(raw: str, excerpt: str, offsets: tuple[int, int]) -> None:
    assert resolve_evidence(raw, EvidenceCreate(source_excerpt=excerpt)) == offsets


@pytest.mark.parametrize("raw,excerpt", [("aaa", "aa"), ("repeat repeat", "repeat")])
def test_overlapping_and_repeated_excerpt_ambiguous(raw: str, excerpt: str) -> None:
    with pytest.raises(EvidenceIntegrityError, match="requires explicit offsets"):
        resolve_evidence(raw, EvidenceCreate(source_excerpt=excerpt))


def test_no_fuzzy_or_unicode_normalized_matching() -> None:
    with pytest.raises(EvidenceIntegrityError, match="does not occur"):
        resolve_evidence("café", EvidenceCreate(source_excerpt="cafe\u0301"))


async def test_invalid_evidence_rollback_and_session_reuse(
    session: AsyncSession,
    raw_document: RawDocument,
) -> None:
    document_id = raw_document.id
    payload = FindingCreate(
        source_finding_id="H-01",
        title="Finding",
        severity="HIGH",
        canonical_category="OTHER",
        evidence=[
            EvidenceCreate(source_excerpt="Report."),
            EvidenceCreate(source_excerpt="absent"),
        ],
    )
    with pytest.raises(EvidenceIntegrityError):
        await create_finding(session, document_id, payload)
    assert not session.in_transaction()
    async with session.begin():
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0
        assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 0
    valid = payload.model_copy(update={"evidence": [EvidenceCreate(source_excerpt="Report.")]})
    finding = await create_finding(session, document_id, valid)
    assert finding.raw_document_id == document_id
    with pytest.raises(DuplicateSourceFindingError):
        await create_finding(session, document_id, valid)
    assert not session.in_transaction()
