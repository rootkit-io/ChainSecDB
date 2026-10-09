from json import JSONDecodeError

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.extraction.prompts import FINDING_EXTRACTION_INSTRUCTIONS, PROMPT_VERSION
from app.extraction.providers.base import ExtractionProviderError
from app.extraction.schemas import SCHEMA_VERSION, ExtractionOutput
from app.schemas.documents import DocumentCreate, MetadataText


class ProviderConfigurationError(Exception):
    pass


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENAI_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    api_key: SecretStr
    model: MetadataText
    timeout_seconds: float = Field(default=180, ge=1, le=600, allow_inf_nan=False)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("OPENAI_API_KEY must be nonblank")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        return DocumentCreate.validate_text(value)


class OpenAIExtractionProvider:
    provider_name = "openai"
    prompt_version = PROMPT_VERSION
    schema_version = SCHEMA_VERSION

    def __init__(
        self, settings: OpenAISettings | None = None, *, client: AsyncOpenAI | None = None
    ) -> None:
        try:
            self._settings = settings or OpenAISettings()  # type: ignore[call-arg]
        except ValidationError:
            raise ProviderConfigurationError(
                "OpenAI extraction requires nonblank OPENAI_API_KEY and OPENAI_MODEL; "
                "OPENAI_TIMEOUT_SECONDS must be between 1 and 600"
            ) from None
        self.model = self._settings.model
        self._client = (
            client.with_options(max_retries=0, timeout=self._settings.timeout_seconds)
            if client is not None
            else None
        )

    async def extract(self, raw_text: str) -> ExtractionOutput:
        try:
            if self._client is not None:
                return await self._extract(self._client, raw_text)
            async with AsyncOpenAI(
                api_key=self._settings.api_key.get_secret_value(),
                base_url="https://api.openai.com/v1",
                timeout=self._settings.timeout_seconds,
                max_retries=0,
            ) as client:
                return await self._extract(client, raw_text)
        except APITimeoutError:
            raise ExtractionProviderError("PROVIDER_TIMEOUT") from None
        except RateLimitError:
            raise ExtractionProviderError("PROVIDER_RATE_LIMIT") from None
        except (AuthenticationError, PermissionDeniedError):
            raise ExtractionProviderError("PROVIDER_AUTH") from None
        except APIConnectionError:
            raise ExtractionProviderError("PROVIDER_CONNECTION") from None
        except (ValidationError, APIResponseValidationError, JSONDecodeError):
            raise ExtractionProviderError("INVALID_OUTPUT") from None
        except ExtractionProviderError:
            raise
        except Exception:
            raise ExtractionProviderError("PROVIDER_ERROR") from None

    async def _extract(self, client: AsyncOpenAI, raw_text: str) -> ExtractionOutput:
        response = await client.responses.parse(
            model=self.model,
            instructions=FINDING_EXTRACTION_INSTRUCTIONS,
            input=raw_text,
            text_format=ExtractionOutput,
            store=False,
            truncation="disabled",
        )
        if any(
            content.type == "refusal"
            for item in response.output
            if item.type == "message"
            for content in item.content
        ):
            raise ExtractionProviderError("PROVIDER_REFUSAL")
        if response.status != "completed" or not isinstance(
            response.output_parsed, ExtractionOutput
        ):
            raise ExtractionProviderError("INVALID_OUTPUT")
        return response.output_parsed
