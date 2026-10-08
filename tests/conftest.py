import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import Settings
from app.main import create_app


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required: integration tests must exercise PostgreSQL")
    Settings(database_url=SecretStr(url))
    schema = f"test_documents_{uuid4().hex}"
    engine = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = (
        make_url(url)
        .update_query_dict({"options": f"-csearch_path={schema} -ctimezone=UTC"})
        .render_as_string(hide_password=False)
    )
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", scoped_url)
            command.upgrade(Config("alembic.ini"), "head")
        yield scoped_url
    finally:
        with engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM raw_documents"))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(
    database_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(database_url=SecretStr(database_url)))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@pytest.fixture
def payload() -> dict[str, object]:
    return {
        "source_name": " manual ",
        "source_url": " https://example.com/security-report ",
        "document_type": " audit_report ",
        "raw_text": " \r\nSecurity report: café 🔐\n\tSELECT * FROM secrets;\n ",
        "retrieved_at": None,
    }
