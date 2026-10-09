from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.extraction.service import FAILURE_MESSAGES
from app.schemas.documents import MetadataText
from app.schemas.findings import FindingFields, Offset

DATASET_VERSION = "chainsec-eval-v1"
METRIC_VERSION = "chainsec-metrics-v1"
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Span(StrictModel):
    source_excerpt: str = Field(min_length=1)
    start_offset: Offset
    end_offset: Offset

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_offset <= self.start_offset:
            raise ValueError("Evidence end must follow start")
        return self


class EvaluationFinding(FindingFields):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    evidence: list[Span] = Field(min_length=1, max_length=100)


class GoldFinding(EvaluationFinding):
    gold_id: Identifier
    notes: str | None = None


class Gold(StrictModel):
    findings: list[GoldFinding] = Field(max_length=100)


class Case(StrictModel):
    case_id: Identifier
    split: Literal["dev", "holdout"]
    kind: Literal["positive", "negative"]
    document_path: str
    gold_path: str
    document_sha256: Digest
    gold_sha256: Digest
    source_name: MetadataText
    source_url: str | None
    document_type: MetadataText
    redistribution_status: Literal["SYNTHETIC", "REDISTRIBUTABLE", "LOCAL_ONLY"]
    human_reviewed: bool = Field(strict=True)
    license_or_terms_note: MetadataText
    retrieved_at: AwareDatetime | None
    notes: str


class Manifest(StrictModel):
    dataset_version: Literal["chainsec-eval-v1"]
    cases: list[Case] = Field(min_length=1)


class Prediction(StrictModel):
    case_id: Identifier
    run_status: Literal["SUCCEEDED", "FAILED", "INCOMPLETE"]
    failure_code: str | None = None
    findings: list[EvaluationFinding] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def lifecycle(self) -> Self:
        if self.run_status == "SUCCEEDED":
            if self.failure_code is not None:
                raise ValueError("Successful cases have no failure code")
        elif self.findings or self.failure_code not in {*FAILURE_MESSAGES, "EVAL_INCOMPLETE"}:
            raise ValueError("Unsuccessful cases have a fixed failure code and no findings")
        return self


class Predictions(StrictModel):
    dataset_version: Literal["chainsec-eval-v1"]
    manifest_sha256: Digest
    mode: Literal["OFFLINE", "LIVE"]
    git_commit: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|fixture)$")]
    working_tree_dirty: bool = Field(strict=True)
    provider: MetadataText
    model: MetadataText
    prompt_version: Literal["extract-findings-v1"]
    schema_version: Literal["finding-output-v1"]
    timestamp: AwareDatetime
    cases: list[Prediction] = Field(min_length=1)


class Decision(StrictModel):
    case_id: Identifier
    action: Literal["MATCH", "VALID_NEW_FINDING", "UNSUPPORTED_FALSE_POSITIVE", "MISSED"]
    prediction_index: Annotated[int, Field(strict=True, ge=0)] | None = None
    gold_id: Identifier | None = None

    @model_validator(mode="after")
    def required_targets(self) -> Self:
        needs_prediction = self.action != "MISSED"
        needs_gold = self.action in {"MATCH", "MISSED"}
        if (self.prediction_index is not None) != needs_prediction:
            raise ValueError("Invalid adjudication prediction target")
        if (self.gold_id is not None) != needs_gold:
            raise ValueError("Invalid adjudication gold target")
        return self


class Adjudication(StrictModel):
    predictions_sha256: Digest
    reviewer: MetadataText
    reviewed_at: AwareDatetime
    human_reviewed: bool = Field(strict=True)
    decisions: list[Decision] = Field(min_length=1)

    @model_validator(mode="after")
    def reviewed(self) -> Self:
        if self.human_reviewed is not True:
            raise ValueError("Adjudication requires explicit human review")
        return self
