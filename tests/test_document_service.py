from hashlib import sha256

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.raw_document import RawDocument
from app.schemas.documents import DocumentCreate
from app.services.documents import DuplicateDocumentError, content_hash, create_document


def test_hash_deterministic() -> None:
    raw_text = " \r\nreport: café 🔐\t "
    assert content_hash(raw_text) == sha256(raw_text.encode("utf-8")).hexdigest()
    assert content_hash(raw_text) == content_hash(raw_text)
    assert content_hash(raw_text) != content_hash(raw_text.strip())
    assert content_hash("café") != content_hash("cafe\u0301")


async def test_service_duplicate_rollback(session: AsyncSession) -> None:
    payload = DocumentCreate(source_name="manual", document_type="report", raw_text="raw report")
    document = await create_document(session, payload)
    document_id = document.id
    assert document.content_hash == content_hash(payload.raw_text)
    with pytest.raises(DuplicateDocumentError):
        await create_document(session, payload)
    assert not session.in_transaction()
    second = await create_document(session, payload.model_copy(update={"raw_text": "other report"}))
    assert second.id != document_id


async def test_other_integrity_error_not_duplicate(session: AsyncSession) -> None:
    payload = DocumentCreate.model_construct(
        source_name=None, document_type="report", raw_text="text"
    )
    with pytest.raises(IntegrityError):
        await create_document(session, payload)
    assert not session.in_transaction()


async def test_database_uniqueness_boundary(session: AsyncSession) -> None:
    # Bypass the service and API entirely: PostgreSQL must enforce uniqueness itself.
    values = dict(
        source_name="manual", document_type="report", raw_text="text", content_hash="a" * 64
    )
    async with session.begin():
        session.add(RawDocument(**values))
    with pytest.raises(IntegrityError) as error:
        async with session.begin():
            session.add(RawDocument(**values))
    assert isinstance(error.value.orig, UniqueViolation)
    assert error.value.orig.diag.constraint_name == "uq_raw_documents_content_hash"
    assert not session.in_transaction()
    assert await session.scalar(select(RawDocument.raw_text)) == "text"


async def test_migrated_schema(session: AsyncSession) -> None:
    connection = await session.connection()
    constraints = await connection.run_sync(
        lambda sync: inspect(sync).get_unique_constraints("raw_documents")
    )
    assert any(
        item["name"] == "uq_raw_documents_content_hash" and item["column_names"] == ["content_hash"]
        for item in constraints
    )
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns("raw_documents"))
    assert {item["name"] for item in columns} == {
        "id",
        "source_name",
        "source_url",
        "document_type",
        "raw_text",
        "content_hash",
        "retrieved_at",
        "created_at",
    }
    assert {item["name"] for item in columns if item["nullable"]} == {"source_url", "retrieved_at"}
    for item in columns:
        if item["name"] in {"retrieved_at", "created_at"}:
            assert item["type"].timezone
    assert (
        await session.scalar(text("SELECT version_num FROM alembic_version"))
        == "0001_raw_documents"
    )
