from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.documents import DocumentCreate, MetadataText
from app.schemas.findings import EvidenceCreate, FindingFields, Offset


class ExtractionRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    provider: MetadataText
    model: MetadataText
    prompt_version: MetadataText
    schema_version: MetadataText

    @field_validator("provider", "model", "prompt_version", "schema_version")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return DocumentCreate.validate_text(value)


class ExtractionEvidence(EvidenceCreate):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    start_offset: Offset = Field(...)
    end_offset: Offset = Field(...)


class ExtractedFinding(FindingFields):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    evidence: list[ExtractionEvidence] = Field(min_length=1, max_length=100)


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    findings: list[ExtractedFinding] = Field(max_length=100)
