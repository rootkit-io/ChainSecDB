from copy import deepcopy
from itertools import product

import pytest
from pydantic import ValidationError

from app.extraction.schemas import ExtractionOutput, ExtractionRunCreate
from app.extraction.transitions import (
    ExtractionStatus,
    InvalidExtractionTransitionError,
    require_transition,
)


@pytest.mark.parametrize("field", ["provider", "model", "prompt_version", "schema_version"])
@pytest.mark.parametrize("value", ["", " \t\n ", "nul\x00text", "\ud800", "x" * 256])
def test_metadata_validation(
    extraction_metadata: ExtractionRunCreate, field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        ExtractionRunCreate.model_validate({**extraction_metadata.model_dump(), field: value})


@pytest.mark.parametrize(
    "field", ["id", "status", "raw_document_id", "started_at", "completed_at", "failure_message"]
)
def test_run_metadata_rejects_server_fields(
    extraction_metadata: ExtractionRunCreate, field: str
) -> None:
    with pytest.raises(ValidationError):
        ExtractionRunCreate.model_validate({**extraction_metadata.model_dump(), field: "untrusted"})


@pytest.mark.parametrize("current,target", list(product(ExtractionStatus, repeat=2)))
def test_transition_matrix(current: ExtractionStatus, target: ExtractionStatus) -> None:
    allowed = {
        (ExtractionStatus.PENDING, ExtractionStatus.RUNNING),
        (ExtractionStatus.RUNNING, ExtractionStatus.SUCCEEDED),
        (ExtractionStatus.RUNNING, ExtractionStatus.FAILED),
    }
    if (current, target) in allowed:
        require_transition(current, target)
    else:
        with pytest.raises(InvalidExtractionTransitionError):
            require_transition(current, target)


@pytest.mark.parametrize(
    "changes",
    [
        {"severity": "Major"},
        {"canonical_category": "oracle"},
        {"title": " \n "},
        {"verification_status": "VERIFIED"},
        {"verification_status": "REJECTED"},
        {"verification_status": "UNREVIEWED"},
        {"extraction_run_id": "untrusted"},
        {"unknown": "untrusted"},
        {"evidence": []},
        {"evidence": None},
    ],
)
def test_invalid_finding_output(extraction_output: dict[str, object], changes: dict) -> None:
    extraction_output["findings"][0].update(changes)
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(extraction_output)


@pytest.mark.parametrize(
    "changes",
    [
        {"start_offset": -1},
        {"start_offset": None},
        {"end_offset": None},
        {"start_offset": True},
        {"start_offset": "2"},
        {"end_offset": 2},
        {"end_offset": 1},
        {"end_offset": 2**31},
        {"field_name": "invented"},
        {"evidence_type": "URL"},
        {"unknown": "untrusted"},
    ],
)
def test_invalid_evidence_output(extraction_output: dict[str, object], changes: dict) -> None:
    extraction_output["findings"][0]["evidence"][0].update(changes)
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(extraction_output)


@pytest.mark.parametrize("field", ["start_offset", "end_offset"])
def test_explicit_offsets_required(extraction_output: dict[str, object], field: str) -> None:
    del extraction_output["findings"][0]["evidence"][0][field]
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(extraction_output)


def test_output_limits_and_empty_success(extraction_output: dict[str, object]) -> None:
    assert ExtractionOutput.model_validate({"findings": []}).findings == []
    finding = deepcopy(extraction_output["findings"][0])
    assert len(ExtractionOutput.model_validate({"findings": [finding] * 100}).findings) == 100
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate({"findings": [finding] * 101})
    finding["evidence"] *= 100
    assert len(ExtractionOutput.model_validate({"findings": [finding]}).findings[0].evidence) == 100
    finding["evidence"].append(finding["evidence"][0])
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate({"findings": [finding]})


def test_root_unknown_fields_rejected(extraction_output: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate({**extraction_output, "raw_model_response": "untrusted"})


def test_mutated_nested_models_revalidated(extraction_output: dict[str, object]) -> None:
    output = ExtractionOutput.model_validate(extraction_output)
    output.findings[0].evidence[0].start_offset = -1
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(output)
