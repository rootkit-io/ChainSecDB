from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.finding_evidence import FindingEvidence
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.schemas.findings import EvidenceCreate, FindingCreate
from app.taxonomy.categories import VerificationStatus


class DocumentNotFoundError(Exception):
    pass


class DuplicateSourceFindingError(Exception):
    pass


class EvidenceIntegrityError(ValueError):
    pass


def resolve_evidence(raw_text: str, evidence: EvidenceCreate) -> tuple[int, int]:
    start, end = evidence.start_offset, evidence.end_offset
    if start is not None and end is not None:
        if end > len(raw_text) or raw_text[start:end] != evidence.source_excerpt:
            raise EvidenceIntegrityError("Evidence offsets do not match the source excerpt")
        return start, end
    start = raw_text.find(evidence.source_excerpt)
    if start == -1:
        raise EvidenceIntegrityError("Evidence excerpt does not occur in the source document")
    if raw_text.find(evidence.source_excerpt, start + 1) != -1:
        raise EvidenceIntegrityError("Repeated evidence excerpt requires explicit offsets")
    return start, start + len(evidence.source_excerpt)


async def create_finding(
    session: AsyncSession, document_id: UUID, payload: FindingCreate
) -> SecurityFinding:
    try:
        async with session.begin():
            # Keep source text stable while evidence is verified and persisted.
            document = await session.scalar(
                select(RawDocument).where(RawDocument.id == document_id).with_for_update(read=True)
            )
            if document is None:
                raise DocumentNotFoundError
            finding = SecurityFinding(
                raw_document_id=document_id,
                verification_status=VerificationStatus.UNREVIEWED,
                **payload.model_dump(exclude={"evidence"}),
            )
            finding.evidence = []
            for item in payload.evidence:
                start, end = resolve_evidence(document.raw_text, item)
                finding.evidence.append(
                    FindingEvidence(
                        **item.model_dump(exclude={"start_offset", "end_offset"}),
                        start_offset=start,
                        end_offset=end,
                    )
                )
            session.add(finding)
            await session.flush()
    except IntegrityError as exc:
        if (
            isinstance(exc.orig, UniqueViolation)
            and exc.orig.diag.constraint_name == "uq_security_findings_document_source_id"
        ):
            raise DuplicateSourceFindingError from None
        raise
    finding.evidence.sort(key=lambda item: item.id)
    return finding


async def get_finding(session: AsyncSession, finding_id: UUID) -> SecurityFinding | None:
    async with session.begin():
        result = await session.scalars(
            select(SecurityFinding)
            .where(SecurityFinding.id == finding_id)
            .options(selectinload(SecurityFinding.evidence))
        )
        return result.one_or_none()


async def list_findings(session: AsyncSession, document_id: UUID) -> list[SecurityFinding]:
    async with session.begin():
        if await session.get(RawDocument, document_id) is None:
            raise DocumentNotFoundError
        result = await session.scalars(
            select(SecurityFinding)
            .where(SecurityFinding.raw_document_id == document_id)
            .options(selectinload(SecurityFinding.evidence))
            .order_by(SecurityFinding.created_at, SecurityFinding.id)
        )
        return list(result.all())
