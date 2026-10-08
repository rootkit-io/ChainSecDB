import asyncio
from collections.abc import AsyncIterator
from hashlib import sha256
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.body_limit import MAX_REQUEST_BYTES
from app.core.config import Settings
from app.db.models.raw_document import RawDocument
from app.db.session import get_session
from app.main import create_app
from app.schemas.documents import MAX_RAW_TEXT_BYTES


async def test_create_and_retrieve(
    client: AsyncClient, session: AsyncSession, payload: dict[str, object]
) -> None:
    response = await client.post("/documents", json=payload)
    assert response.status_code == 201
    metadata = response.json()
    assert set(metadata) == {
        "id",
        "source_name",
        "source_url",
        "document_type",
        "content_hash",
        "retrieved_at",
        "created_at",
    }
    assert metadata["source_name"] == "manual"
    assert metadata["document_type"] == "audit_report"
    assert metadata["source_url"] == "https://example.com/security-report"
    assert metadata["retrieved_at"] is None
    assert metadata["created_at"].endswith("Z")
    assert metadata["content_hash"] == sha256(str(payload["raw_text"]).encode("utf-8")).hexdigest()
    persisted = await session.scalar(select(RawDocument))
    assert persisted is not None
    assert persisted.raw_text.encode("utf-8") == str(payload["raw_text"]).encode("utf-8")
    response = await client.get(f"/documents/{metadata['id']}")
    assert response.status_code == 200
    assert response.json() == {**metadata, "raw_text": payload["raw_text"]}


async def test_duplicate_content(
    client: AsyncClient, session: AsyncSession, payload: dict[str, object]
) -> None:
    assert (await client.post("/documents", json=payload)).status_code == 201
    for _ in range(2):
        response = await client.post(
            "/documents", json={**payload, "source_name": "another source"}
        )
        assert response.status_code == 409
        assert response.json() == {"detail": "Document content already exists"}
    assert await session.scalar(select(func.count()).select_from(RawDocument)) == 1
    assert (
        await client.post("/documents", json={**payload, "raw_text": "Different"})
    ).status_code == 201


async def test_concurrent_duplicate(
    client: AsyncClient, session: AsyncSession, payload: dict[str, object]
) -> None:
    responses = await asyncio.gather(*(client.post("/documents", json=payload) for _ in range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert await session.scalar(select(func.count()).select_from(RawDocument)) == 1


@pytest.mark.parametrize("field", ["source_name", "document_type", "raw_text"])
@pytest.mark.parametrize("value", ["", " \t\r\n "])
async def test_blank_fields(
    client: AsyncClient, payload: dict[str, object], field: str, value: str
) -> None:
    response = await client.post("/documents", json={**payload, field: value})
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["source_name", "document_type", "raw_text"])
async def test_missing_fields(client: AsyncClient, payload: dict[str, object], field: str) -> None:
    del payload[field]
    assert (await client.post("/documents", json=payload)).status_code == 422


@pytest.mark.parametrize(
    "value",
    [
        "not-a-url",
        "",
        "ftp://example.com/report",
        "javascript:alert(1)",
        "https://example.com/\x00",
    ],
)
async def test_invalid_source_url(
    client: AsyncClient, payload: dict[str, object], value: str
) -> None:
    assert (
        await client.post("/documents", json={**payload, "source_url": value})
    ).status_code == 422


@pytest.mark.parametrize("value", ["not-a-date", "2026-10-08T12:00:00", "2026-02-30T00:00:00Z"])
async def test_invalid_retrieved_at(
    client: AsyncClient, payload: dict[str, object], value: str
) -> None:
    assert (
        await client.post("/documents", json={**payload, "retrieved_at": value})
    ).status_code == 422


async def test_retrieved_at_utc(client: AsyncClient, payload: dict[str, object]) -> None:
    response = await client.post(
        "/documents", json={**payload, "retrieved_at": "2026-10-08T14:00:00+02:00"}
    )
    assert response.status_code == 201
    assert response.json()["retrieved_at"] == "2026-10-08T12:00:00Z"
    detail = await client.get(f"/documents/{response.json()['id']}")
    assert detail.json()["retrieved_at"] == "2026-10-08T12:00:00Z"


async def test_minimum_payload(client: AsyncClient) -> None:
    response = await client.post(
        "/documents", json={"source_name": "manual", "document_type": "report", "raw_text": "text"}
    )
    assert response.status_code == 201
    assert response.json()["source_url"] is None
    assert response.json()["retrieved_at"] is None


async def test_source_url_metadata_only(client: AsyncClient, payload: dict[str, object]) -> None:
    response = await client.post(
        "/documents", json={**payload, "source_url": "http://127.0.0.1:1/unreachable"}
    )
    assert response.status_code == 201
    assert response.json()["source_url"] == "http://127.0.0.1:1/unreachable"


async def test_raw_text_size_boundary(client: AsyncClient, payload: dict[str, object]) -> None:
    raw_text = "é" * (MAX_RAW_TEXT_BYTES // 2)
    response = await client.post("/documents", json={**payload, "raw_text": raw_text})
    assert response.status_code == 201
    detail = await client.get(f"/documents/{response.json()['id']}")
    assert detail.json()["raw_text"] == raw_text


async def test_oversize_request_body(client: AsyncClient) -> None:
    response = await client.post("/documents", content=b" " * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body exceeds 8 MiB"}


async def test_oversize_chunked_body(client: AsyncClient) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(9):
            yield b" " * (1024 * 1024)

    response = await client.post("/documents", content=chunks())
    assert "content-length" not in response.request.headers
    assert response.status_code == 413


async def test_request_size_boundary(client: AsyncClient) -> None:
    body = b'{"source_name":"manual","document_type":"report","raw_text":"text"}'
    body += b" " * (MAX_REQUEST_BYTES - len(body))
    response = await client.post(
        "/documents", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 201


async def test_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/documents/{uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


async def test_malformed_identifier(client: AsyncClient) -> None:
    response = await client.get("/documents/not-a-uuid")
    assert response.status_code == 422


async def test_client_hash_forbidden(
    client: AsyncClient, session: AsyncSession, payload: dict[str, object]
) -> None:
    response = await client.post("/documents", json={**payload, "content_hash": "0" * 64})
    assert response.status_code == 422
    assert await session.scalar(select(func.count()).select_from(RawDocument)) == 0


@pytest.mark.parametrize("value", ["a\x00b", "\ud800", "é" * (MAX_RAW_TEXT_BYTES // 2 + 1)])
async def test_unstorable_or_oversize_text(
    client: AsyncClient, payload: dict[str, object], value: str
) -> None:
    # ASCII escaping permits testing an invalid surrogate without encoding it in the HTTP client.
    import json

    response = await client.post(
        "/documents",
        content=json.dumps({**payload, "raw_text": value}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


async def test_validation_does_not_echo_document(
    client: AsyncClient, payload: dict[str, object]
) -> None:
    response = await client.post(
        "/documents", json={**payload, "raw_text": {"secret": "private text"}}
    )
    assert response.status_code == 422
    assert "private text" not in response.text


async def test_malformed_json(client: AsyncClient) -> None:
    response = await client.post(
        "/documents", content='{"raw_text":', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


async def test_database_error_sanitized(
    database_url: str, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(Settings(database_url=SecretStr(database_url)))

    async def failing_session() -> AsyncIterator[AsyncSession]:
        await session.execute(text("SELECT 1 / 0"))
        yield session

    app.dependency_overrides[get_session] = failing_session
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/documents/{uuid4()}")
    assert response.status_code == 500
    assert response.json() == {"detail": "Database operation failed"}
    assert "Database request failed (DataError)" in caplog.text
    assert "SELECT" not in caplog.text
    assert database_url not in caplog.text
