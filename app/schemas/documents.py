from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
)

MAX_RAW_TEXT_BYTES = 1024 * 1024
MetadataText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: MetadataText
    source_url: str | None = Field(default=None, max_length=2083)
    document_type: MetadataText
    raw_text: str = Field(min_length=1, max_length=MAX_RAW_TEXT_BYTES)
    retrieved_at: AwareDatetime | None = None

    @field_validator("source_name", "document_type", "raw_text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must not be blank")
        if "\x00" in value:
            raise ValueError("NUL characters cannot be stored in PostgreSQL text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Text must be valid UTF-8") from None
        return value

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_RAW_TEXT_BYTES:
            raise ValueError("raw_text must not exceed 1 MiB of UTF-8 bytes")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            cls.validate_text(value)
            HttpUrl(value)
        return value

    @field_validator("retrieved_at")
    @classmethod
    def normalize_retrieved_at(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_name: str
    source_url: str | None
    document_type: str
    content_hash: str
    retrieved_at: AwareDatetime | None
    created_at: AwareDatetime

    @field_validator("retrieved_at", "created_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class DocumentDetail(DocumentMetadata):
    raw_text: str
