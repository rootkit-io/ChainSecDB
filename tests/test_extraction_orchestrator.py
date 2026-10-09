import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import httpx2
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.extraction_run import ExtractionRun
from app.db.models.finding_evidence import FindingEvidence
from app.db.models.raw_document import RawDocument
from app.db.models.security_finding import SecurityFinding
from app.extraction.orchestrator import extract_document
from app.extraction.prompts import PROMPT_VERSION
from app.extraction.providers.base import ExtractionProviderError
from app.extraction.providers.openai import OpenAIExtractionProvider, OpenAISettings
from app.extraction.schemas import SCHEMA_VERSION, ExtractionOutput
from app.extraction.service import (
    FAILURE_MESSAGES,
    ExtractionDuplicateSourceError,
    ExtractionEvidenceError,
    ExtractionOutputValidationError,
    ExtractionPersistenceError,
)
from app.extraction.transitions import ExtractionStatus
from app.services.findings import DocumentNotFoundError
from app.taxonomy.categories import VerificationStatus


@pytest.fixture
def sdk_client(extraction_output: dict) -> Mock:
    client = Mock(spec=AsyncOpenAI)
    client.with_options.return_value = client
    client.responses = Mock()
    client.responses.parse = AsyncMock(
        return_value=Mock(
            status="completed",
            output=[],
            output_parsed=ExtractionOutput.model_validate(extraction_output),
        )
    )
    return client


@pytest.fixture
def provider(sdk_client: Mock) -> OpenAIExtractionProvider:
    return OpenAIExtractionProvider(
        OpenAISettings(api_key=SecretStr("test-placeholder"), model="chosen-model", _env_file=None),
        client=sdk_client,
    )


async def test_application_and_manual_api_work_without_provider_configuration(
    client: httpx.AsyncClient, raw_document: RawDocument
) -> None:
    response = await client.post(
        f"/documents/{raw_document.id}/findings",
        json={
            "title": "Manual",
            "severity": "LOW",
            "canonical_category": "OTHER",
        },
    )
    assert response.status_code == 201
    assert response.json()["extraction_run_id"] is None
    assert response.json()["verification_status"] == "UNREVIEWED"
    assert (await client.get(f"/documents/{raw_document.id}")).status_code == 200
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert not any("extract" in path for path in paths)


async def test_openai_success_uses_existing_completion_and_persisted_provenance(
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
) -> None:
    run = await extract_document(session_factory, raw_document.id, provider)
    assert run.status == ExtractionStatus.SUCCEEDED
    assert sdk_client.responses.parse.await_count == 1
    assert sdk_client.responses.parse.call_args.kwargs["input"] == raw_document.raw_text
    async with session_factory() as session:
        saved = await session.get(ExtractionRun, run.id)
        assert (saved.provider, saved.model, saved.prompt_version, saved.schema_version) == (
            "openai",
            "chosen-model",
            PROMPT_VERSION,
            SCHEMA_VERSION,
        )
        finding = await session.scalar(select(SecurityFinding))
        assert finding.extraction_run_id == run.id
        assert finding.raw_document_id == raw_document.id
        assert finding.verification_status == VerificationStatus.UNREVIEWED
        evidence = await session.scalar(select(FindingEvidence))
        assert (
            raw_document.raw_text[evidence.start_offset : evidence.end_offset]
            == evidence.source_excerpt
        )
        assert saved.completed_at >= saved.started_at


@pytest.mark.parametrize(
    "code",
    [
        "PROVIDER_TIMEOUT",
        "PROVIDER_RATE_LIMIT",
        "PROVIDER_AUTH",
        "PROVIDER_CONNECTION",
        "PROVIDER_REFUSAL",
        "INVALID_OUTPUT",
        "PROVIDER_ERROR",
    ],
)
async def test_provider_failure_persists_only_sanitized_failed_run(
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
    code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    body = {"message": "sensitive-provider-body"}
    errors = {
        "PROVIDER_TIMEOUT": APITimeoutError(request),
        "PROVIDER_RATE_LIMIT": RateLimitError(
            "sensitive-provider-body", response=httpx2.Response(429, request=request), body=body
        ),
        "PROVIDER_AUTH": AuthenticationError(
            "sensitive-provider-body", response=httpx2.Response(401, request=request), body=body
        ),
        "PROVIDER_CONNECTION": APIConnectionError(
            message="sensitive-provider-body", request=request
        ),
        "PROVIDER_REFUSAL": ExtractionProviderError("PROVIDER_REFUSAL"),
        "INVALID_OUTPUT": ExtractionProviderError("INVALID_OUTPUT"),
        "PROVIDER_ERROR": InternalServerError(
            "sensitive-provider-body", response=httpx2.Response(500, request=request), body=body
        ),
    }
    sdk_client.responses.parse.side_effect = errors[code]
    with pytest.raises(ExtractionProviderError) as caught:
        await extract_document(session_factory, raw_document.id, provider)
    assert caught.value.code == code
    assert sdk_client.responses.parse.await_count == 1
    async with session_factory() as session:
        run = await session.scalar(select(ExtractionRun))
        assert run.status == ExtractionStatus.FAILED
        assert run.failure_code == code and run.failure_message == FAILURE_MESSAGES[code]
        assert run.completed_at >= run.started_at
        assert "test-placeholder" not in repr(run.__dict__)
        assert "sensitive-provider-body" not in repr(run.__dict__)
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0
        assert await session.scalar(select(func.count()).select_from(FindingEvidence)) == 0
    assert "sensitive-provider-body" not in str(caught.value) + caplog.text
    assert "test-placeholder" not in str(caught.value) + caplog.text


@pytest.mark.parametrize("invalid", ["evidence", "duplicate", "mutated"])
async def test_deterministic_rejection_not_retried_or_repaired(
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
    extraction_output: dict,
    invalid: str,
) -> None:
    payload = deepcopy(extraction_output)
    if invalid == "evidence":
        payload["findings"][0]["evidence"][0].update(start_offset=3, end_offset=10)
        error, code = ExtractionEvidenceError, "EVIDENCE_MISMATCH"
    elif invalid == "duplicate":
        payload["findings"].append(deepcopy(payload["findings"][0]))
        error, code = ExtractionDuplicateSourceError, "DUPLICATE_SOURCE_FINDING"
    else:
        error, code = ExtractionOutputValidationError, "INVALID_OUTPUT"
    output = ExtractionOutput.model_validate(payload)
    if invalid == "mutated":
        output.findings[0].evidence.clear()
    sdk_client.responses.parse.return_value.output_parsed = output
    with pytest.raises(error):
        await extract_document(session_factory, raw_document.id, provider)
    assert sdk_client.responses.parse.await_count == 1
    async with session_factory() as session:
        run = await session.scalar(select(ExtractionRun))
        assert run.status == ExtractionStatus.FAILED and run.failure_code == code
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0


async def test_missing_document_never_calls_provider(
    session_factory: async_sessionmaker[AsyncSession],
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
) -> None:
    with pytest.raises(DocumentNotFoundError):
        await extract_document(session_factory, uuid4(), provider)
    sdk_client.responses.parse.assert_not_awaited()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExtractionRun)) == 0


async def test_invalid_provider_metadata_rejected_before_run_creation(
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
) -> None:
    provider.model = " "
    with pytest.raises(ValidationError):
        await extract_document(session_factory, raw_document.id, provider)
    sdk_client.responses.parse.assert_not_awaited()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExtractionRun)) == 0


async def test_no_connection_transaction_or_lock_held_during_provider_wait(
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
) -> None:
    waiting, release = asyncio.Event(), asyncio.Event()
    response = sdk_client.responses.parse.return_value

    async def wait_for_release(**kwargs: object) -> object:
        assert kwargs["input"] == raw_document.raw_text
        assert session_factory.kw["bind"].pool.checkedout() == 0
        waiting.set()
        await release.wait()
        return response

    sdk_client.responses.parse.side_effect = wait_for_release
    task = asyncio.create_task(extract_document(session_factory, raw_document.id, provider))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        async with session_factory() as observer, observer.begin():
            await observer.execute(text("SET LOCAL lock_timeout = '500ms'"))
            run = await observer.scalar(select(ExtractionRun).with_for_update())
            assert run.status == ExtractionStatus.RUNNING
            assert await observer.scalar(select(RawDocument).with_for_update()) is not None
            assert await observer.scalar(select(func.count()).select_from(SecurityFinding)) == 0
        release.set()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.status == ExtractionStatus.SUCCEEDED
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_cancellation_leaves_run_running_without_false_failure(
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
) -> None:
    waiting = asyncio.Event()

    async def wait_forever(**kwargs: object) -> None:
        waiting.set()
        await asyncio.Event().wait()

    sdk_client.responses.parse.side_effect = wait_forever
    task = asyncio.create_task(extract_document(session_factory, raw_document.id, provider))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
    finally:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with session_factory() as session:
        run = await session.scalar(select(ExtractionRun))
        assert run.status == ExtractionStatus.RUNNING
        assert run.failure_code is None and run.completed_at is None
        assert await session.scalar(select(func.count()).select_from(SecurityFinding)) == 0


async def test_completion_commit_error_does_not_attempt_another_transition(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
) -> None:
    complete = AsyncMock(side_effect=ExtractionPersistenceError())
    fail = AsyncMock()
    monkeypatch.setattr("app.extraction.orchestrator.complete_extraction_run", complete)
    monkeypatch.setattr("app.extraction.orchestrator.fail_extraction_run", fail)
    with pytest.raises(ExtractionPersistenceError):
        await extract_document(session_factory, raw_document.id, provider)
    complete.assert_awaited_once()
    fail.assert_not_awaited()
    assert sdk_client.responses.parse.await_count == 1
    async with session_factory() as session:
        assert (await session.scalar(select(ExtractionRun))).status == ExtractionStatus.RUNNING


async def test_source_load_database_error_sanitized_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
    raw_document: RawDocument,
    provider: OpenAIExtractionProvider,
    sdk_client: Mock,
) -> None:
    monkeypatch.setattr(
        "app.extraction.orchestrator.get_document",
        AsyncMock(side_effect=SQLAlchemyError("sensitive-database-details")),
    )
    with pytest.raises(ExtractionPersistenceError) as caught:
        await extract_document(session_factory, raw_document.id, provider)
    assert "sensitive-database-details" not in str(caught.value)
    assert caught.value.__suppress_context__
    sdk_client.responses.parse.assert_not_awaited()
    async with session_factory() as session:
        assert (await session.scalar(select(ExtractionRun))).status == ExtractionStatus.RUNNING
