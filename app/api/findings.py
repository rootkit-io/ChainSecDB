from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.findings import FindingCreate, FindingDetail
from app.services.findings import (
    DocumentNotFoundError,
    DuplicateSourceFindingError,
    EvidenceIntegrityError,
    create_finding,
    get_finding,
    list_findings,
)

router = APIRouter(tags=["findings"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/documents/{document_id}/findings", response_model=FindingDetail, status_code=201)
async def post_finding(
    document_id: UUID, payload: FindingCreate, session: Session
) -> FindingDetail:
    try:
        finding = await create_finding(session, document_id, payload)
    except DocumentNotFoundError:
        raise HTTPException(status_code=404, detail="Document not found") from None
    except DuplicateSourceFindingError:
        raise HTTPException(
            status_code=409, detail="Source finding ID already exists for document"
        ) from None
    except EvidenceIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return FindingDetail.model_validate(finding)


@router.get("/findings/{finding_id}", response_model=FindingDetail)
async def read_finding(finding_id: UUID, session: Session) -> FindingDetail:
    finding = await get_finding(session, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return FindingDetail.model_validate(finding)


@router.get("/documents/{document_id}/findings", response_model=list[FindingDetail])
async def read_document_findings(document_id: UUID, session: Session) -> list[FindingDetail]:
    try:
        findings = await list_findings(session, document_id)
    except DocumentNotFoundError:
        raise HTTPException(status_code=404, detail="Document not found") from None
    return [FindingDetail.model_validate(finding) for finding in findings]
