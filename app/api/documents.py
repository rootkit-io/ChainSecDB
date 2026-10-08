from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.documents import DocumentCreate, DocumentDetail, DocumentMetadata
from app.services.documents import DuplicateDocumentError, create_document, get_document

router = APIRouter(prefix="/documents", tags=["documents"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=DocumentMetadata, status_code=201)
async def post_document(payload: DocumentCreate, session: Session) -> DocumentMetadata:
    try:
        document = await create_document(session, payload)
    except DuplicateDocumentError:
        raise HTTPException(status_code=409, detail="Document content already exists") from None
    return DocumentMetadata.model_validate(document)


@router.get("/{document_id}", response_model=DocumentDetail)
async def read_document(document_id: UUID, session: Session) -> DocumentDetail:
    document = await get_document(session, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetail.model_validate(document)
