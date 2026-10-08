from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.documents import MAX_RAW_TEXT_BYTES, DocumentCreate
from app.taxonomy.categories import (
    CanonicalCategory,
    EvidenceType,
    FindingField,
    Severity,
    VerificationStatus,
)

Label = Annotated[str, Field(max_length=255)]
Description = Annotated[str, Field(max_length=MAX_RAW_TEXT_BYTES)]
PositiveLine = Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
Offset = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]


class EvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: EvidenceType = EvidenceType.SOURCE_TEXT
    field_name: FindingField | None = None
    source_excerpt: str = Field(min_length=1, max_length=MAX_RAW_TEXT_BYTES)
    start_offset: Offset | None = None
    end_offset: Offset | None = None

    @field_validator("source_excerpt")
    @classmethod
    def validate_excerpt(cls, value: str) -> str:
        return DocumentCreate.validate_text(value)

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("Both evidence offsets must be supplied together")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("end_offset must be greater than start_offset")
        return self


class FindingFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_finding_id: Label | None = None
    source_title: str | None = Field(default=None, max_length=500)
    source_severity: Label | None = None
    source_category: Label | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    severity: Severity
    canonical_category: CanonicalCategory
    summary: Description | None = None
    root_cause: Description | None = None
    impact: Description | None = None
    recommendation: Description | None = None
    affected_contract: Label | None = None
    affected_function: Label | None = None
    source_file: str | None = Field(default=None, max_length=1024)
    line_start: PositiveLine | None = None
    line_end: PositiveLine | None = None
    protocol_name: Label | None = None
    language: Label | None = None

    @field_validator(
        "source_finding_id",
        "source_title",
        "source_severity",
        "source_category",
        "title",
        "summary",
        "root_cause",
        "impact",
        "recommendation",
        "affected_contract",
        "affected_function",
        "source_file",
        "protocol_name",
        "language",
    )
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return DocumentCreate.validate_text(value) if value is not None else None

    @model_validator(mode="after")
    def validate_lines(self) -> Self:
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class FindingCreate(FindingFields):
    evidence: list[EvidenceCreate] = Field(default_factory=list, max_length=100)


class EvidenceDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    finding_id: UUID
    evidence_type: EvidenceType
    field_name: FindingField | None
    source_excerpt: str
    start_offset: int
    end_offset: int
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class FindingDetail(FindingFields):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    raw_document_id: UUID
    extraction_run_id: UUID | None
    verification_status: VerificationStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    evidence: list[EvidenceDetail]

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
