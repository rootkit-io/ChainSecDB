from hashlib import sha256
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.raw_document import RawDocument
from app.schemas.documents import DocumentCreate


class DuplicateDocumentError(Exception):
    pass


def content_hash(raw_text: str) -> str:
    return sha256(raw_text.encode("utf-8")).hexdigest()


async def create_document(session: AsyncSession, payload: DocumentCreate) -> RawDocument:
    document = RawDocument(**payload.model_dump(), content_hash=content_hash(payload.raw_text))
    try:
        async with session.begin():
            session.add(document)
            await session.flush()
    except IntegrityError as exc:
        if (
            isinstance(exc.orig, UniqueViolation)
            and exc.orig.diag.constraint_name == "uq_raw_documents_content_hash"
        ):
            raise DuplicateDocumentError from None
        raise
    return document


async def get_document(session: AsyncSession, document_id: UUID) -> RawDocument | None:
    async with session.begin():
        return await session.get(RawDocument, document_id)
