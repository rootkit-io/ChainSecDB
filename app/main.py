import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.documents import router
from app.core.body_limit import BodyLimitMiddleware
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        config = settings or get_settings()
        engine = create_async_engine(
            config.database_url.get_secret_value(),
            hide_parameters=True,
        )
        try:
            try:
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            except SQLAlchemyError as exc:
                logger.error("Database startup failed (%s)", type(exc).__name__)
                raise RuntimeError(
                    "Database unavailable; check DATABASE_URL and PostgreSQL"
                ) from None
            application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
            yield
        finally:
            await engine.dispose()

    application = FastAPI(title="Security Research Documents", lifespan=lifespan)
    application.add_middleware(BodyLimitMiddleware)
    application.include_router(router)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("Database request failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Database operation failed"})

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Default validation errors echo inputs, which can include the entire raw document.
        errors = [
            {"loc": error["loc"], "type": error["type"], "msg": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    return application


app = create_app()
