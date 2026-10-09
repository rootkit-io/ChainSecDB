from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.extraction_run import ExtractionRun
from app.extraction.providers.base import ExtractionProvider, ExtractionProviderError
from app.extraction.schemas import ExtractionRunCreate
from app.extraction.service import (
    ExtractionPersistenceError,
    complete_extraction_run,
    create_extraction_run,
    fail_extraction_run,
    start_extraction_run,
)
from app.services.documents import get_document
from app.services.findings import DocumentNotFoundError


async def extract_document(
    session_factory: async_sessionmaker[AsyncSession],
    document_id: UUID,
    provider: ExtractionProvider,
) -> ExtractionRun:
    """Use the application's session factory (expire_on_commit=False)."""
    metadata = ExtractionRunCreate(
        provider=provider.provider_name,
        model=provider.model,
        prompt_version=provider.prompt_version,
        schema_version=provider.schema_version,
    )
    async with session_factory() as session:
        run = await create_extraction_run(session, document_id, metadata)
        run_id = run.id
        await start_extraction_run(session, run_id)
        try:
            document = await get_document(session, document_id)
        except SQLAlchemyError:
            raise ExtractionPersistenceError from None
        if document is None:
            raise DocumentNotFoundError("Document not found")
        raw_text = document.raw_text

    # Close the session and release its connection before the network request.
    try:
        output = await provider.extract(raw_text)
    except ExtractionProviderError as error:
        async with session_factory() as session:
            await fail_extraction_run(session, run_id, error.code)
        raise error from None
    async with session_factory() as session:
        return await complete_extraction_run(session, run_id, output)
