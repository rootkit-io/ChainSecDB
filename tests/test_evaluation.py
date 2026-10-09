import hashlib
import json
import shutil
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.evaluation.cli import main, output_path
from app.evaluation.dataset import REPOSITORY, EvaluationError, load_dataset, select_cases
from app.evaluation.matching import best_iou, match_findings, span_iou
from app.evaluation.metrics import calculate_metrics
from app.evaluation.report import (
    build_report,
    load_adjudication,
    load_predictions,
    write_reports,
)
from app.evaluation.runner import evaluation_database_url
from app.evaluation.schemas import Adjudication, Decision, EvaluationFinding, GoldFinding, Span
from app.extraction.providers.openai import OpenAISettings

CORPUS = REPOSITORY / "evals/datasets/v1"
FIXTURE = REPOSITORY / "evals/fixtures/predictions.json"


@pytest.fixture
def editable_dataset(tmp_path):
    destination = tmp_path / "dataset"
    shutil.copytree(CORPUS, destination)
    return destination


def change_manifest(root, mutate):
    path = root / "manifest.json"
    data = json.loads(path.read_bytes())
    mutate(data)
    path.write_text(json.dumps(data))


def change_gold(root, mutate):
    path = root / "cases/single.gold.json"
    gold = json.loads(path.read_bytes())
    mutate(gold)
    path.write_text(json.dumps(gold))
    change_manifest(
        root,
        lambda manifest: manifest["cases"][0].update(
            gold_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        ),
    )


def span(start=0, end=10, excerpt=None):
    return Span(start_offset=start, end_offset=end, source_excerpt=excerpt or "x" * (end - start))


def finding(*spans, gold_id=None, category="ACCESS_CONTROL", severity="HIGH"):
    values = dict(
        title="Finding", canonical_category=category, severity=severity, evidence=list(spans)
    )
    return GoldFinding(gold_id=gold_id, **values) if gold_id else EvaluationFinding(**values)


def fixture_data():
    return load_dataset(CORPUS), load_predictions(FIXTURE)[0]


def one_case():
    dataset, artifact = fixture_data()
    case = dataset.cases[0]
    return case, artifact.cases[0].model_copy(deep=True)


def metrics_for(case, prediction):
    return calculate_metrics([case], {case.metadata.case_id: prediction})


def test_committed_corpus_integrity():
    dataset, artifact = fixture_data()
    assert len(dataset.cases) == 12
    assert sum(len(case.gold.findings) for case in dataset.cases) == 21
    assert sum(case.metadata.kind == "negative" for case in dataset.cases) == 3
    assert all(case.metadata.redistribution_status == "SYNTHETIC" for case in dataset.cases)
    assert not any(case.metadata.human_reviewed for case in dataset.cases)
    assert artifact.manifest_sha256 == dataset.manifest_sha256
    assert {"unicode", "repeated", "injection", "long"} <= {
        case.metadata.case_id for case in dataset.cases
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("dataset_version", "chainsec-eval-v2"),
        ("cases", []),
        ("unexpected", "forbidden"),
    ],
)
def test_manifest_rejected(editable_dataset, field, value):
    change_manifest(editable_dataset, lambda data: data.update({field: value}))
    with pytest.raises(EvaluationError, match="manifest"):
        load_dataset(editable_dataset)


def test_duplicate_case(editable_dataset):
    change_manifest(editable_dataset, lambda data: data["cases"].append(data["cases"][0]))
    with pytest.raises(EvaluationError, match="Duplicate case"):
        load_dataset(editable_dataset)


@pytest.mark.parametrize("field", ["human_reviewed", "document_sha256", "gold_sha256"])
def test_required_manifest_fields(editable_dataset, field):
    change_manifest(editable_dataset, lambda data: data["cases"][0].pop(field))
    with pytest.raises(EvaluationError):
        load_dataset(editable_dataset)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_review_flag_is_explicit_boolean(editable_dataset, value):
    change_manifest(editable_dataset, lambda data: data["cases"][0].update(human_reviewed=value))
    with pytest.raises(EvaluationError):
        load_dataset(editable_dataset)


@pytest.mark.parametrize("file", ["single.md", "single.gold.json"])
def test_content_hash_drift(editable_dataset, file):
    with (editable_dataset / "cases" / file).open("ab") as target:
        target.write(b" ")
    with pytest.raises(EvaluationError, match="hash mismatch"):
        load_dataset(editable_dataset)


@pytest.mark.parametrize("file", ["single.md", "single.gold.json"])
def test_missing_content(editable_dataset, file):
    (editable_dataset / "cases" / file).unlink()
    with pytest.raises(EvaluationError, match="missing"):
        load_dataset(editable_dataset)


def test_malformed_manifest(editable_dataset):
    (editable_dataset / "manifest.json").write_text("{broken")
    with pytest.raises(EvaluationError, match="manifest"):
        load_dataset(editable_dataset)


def test_duplicate_gold_id(editable_dataset):
    change_gold(editable_dataset, lambda data: data["findings"].append(data["findings"][0]))
    with pytest.raises(EvaluationError, match="Duplicate gold"):
        load_dataset(editable_dataset)


@pytest.mark.parametrize(
    "field,value",
    [
        ("severity", "MAJOR"),
        ("canonical_category", "OVERFLOW"),
        ("evidence", []),
        ("verification_status", "VERIFIED"),
        ("title", " "),
    ],
)
def test_strict_gold_schema(editable_dataset, field, value):
    change_gold(editable_dataset, lambda data: data["findings"][0].update({field: value}))
    with pytest.raises(EvaluationError, match="gold schema"):
        load_dataset(editable_dataset)


@pytest.mark.parametrize(
    "field,value",
    [
        ("end_offset", 2**30),
        ("start_offset", -1),
        ("start_offset", 0),
        ("source_excerpt", "Wrong slice"),
        ("end_offset", 0),
        ("start_offset", True),
    ],
)
def test_invalid_evidence(editable_dataset, field, value):
    change_gold(
        editable_dataset, lambda data: data["findings"][0]["evidence"][0].update({field: value})
    )
    with pytest.raises(EvaluationError):
        load_dataset(editable_dataset)


def test_negative_with_gold(editable_dataset):
    change_manifest(editable_dataset, lambda data: data["cases"][0].update(kind="negative"))
    with pytest.raises(EvaluationError, match="Positive/negative"):
        load_dataset(editable_dataset)


def test_positive_without_gold(editable_dataset):
    change_gold(editable_dataset, lambda data: data.update(findings=[]))
    with pytest.raises(EvaluationError, match="Positive/negative"):
        load_dataset(editable_dataset)


def test_external_synthetic_path_rejected(editable_dataset):
    change_manifest(
        editable_dataset, lambda data: data["cases"][0].update(document_path="/etc/hosts")
    )
    with pytest.raises(EvaluationError, match="inside dataset"):
        load_dataset(editable_dataset)


def test_local_only_inside_repository_rejected(editable_dataset):
    change_manifest(
        editable_dataset,
        lambda data: data["cases"][0].update(
            redistribution_status="LOCAL_ONLY", document_path=str(CORPUS / "cases/single.md")
        ),
    )
    with pytest.raises(EvaluationError, match="outside repository"):
        load_dataset(editable_dataset)


def test_local_only_external_metadata_and_gold(editable_dataset):
    def update(data):
        case = data["cases"][0]
        case.update(
            redistribution_status="LOCAL_ONLY",
            document_path=str(editable_dataset / case["document_path"]),
            gold_path=str(editable_dataset / case["gold_path"]),
            source_url="https://example.com/report",
            retrieved_at="2026-10-09T00:00:00Z",
        )

    change_manifest(editable_dataset, update)
    assert load_dataset(editable_dataset).cases[0].metadata.redistribution_status == "LOCAL_ONLY"


@pytest.mark.parametrize("field,value", [("source_url", None), ("retrieved_at", None)])
def test_real_case_requires_provenance(editable_dataset, field, value):
    change_manifest(
        editable_dataset,
        lambda data: data["cases"][0].update(
            redistribution_status="REDISTRIBUTABLE", **{field: value}
        ),
    )
    with pytest.raises(EvaluationError, match="provenance"):
        load_dataset(editable_dataset)


def test_crlf_is_preserved(editable_dataset):
    # Deliberately change line endings and update offsets and hashes as a corpus editor would.
    path = editable_dataset / "cases/single.md"
    document = path.read_bytes().decode().replace("\n", "\r\n")
    path.write_bytes(document.encode())

    def update(data):
        evidence = data["findings"][0]["evidence"][0]
        evidence["start_offset"] = document.index(evidence["source_excerpt"])
        evidence["end_offset"] = evidence["start_offset"] + len(evidence["source_excerpt"])

    change_gold(editable_dataset, update)
    change_manifest(
        editable_dataset,
        lambda data: data["cases"][0].update(
            document_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        ),
    )
    assert load_dataset(editable_dataset).cases[0].document == document


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ((0, 10), (0, 10), 1),
        ((0, 10), (5, 15), 1 / 3),
        ((0, 10), (10, 20), 0),
        ((0, 10), (0, 5), 0.5),
        ((0, 10), (0, 4), 0.4),
        ((0, 10), (2, 8), 0.6),
    ],
)
def test_span_overlap(left, right, expected):
    assert span_iou(span(*left), span(*right)) == pytest.approx(expected)


@pytest.mark.parametrize("end,matched", [(5, True), (4, False)])
def test_threshold_boundary(end, matched):
    assert (
        bool(match_findings([finding(span(0, end))], [finding(span(), gold_id="g")]).matches)
        == matched
    )


def test_multi_span_uses_best_overlap():
    predicted = finding(span(20, 30), span())
    gold = finding(span(40, 50), span(0, 5), gold_id="g")
    assert best_iou(predicted, gold) == 0.5
    assert len(match_findings([predicted], [gold]).matches) == 1


def test_one_prediction_cannot_cover_two_gold_and_ties_use_gold_id():
    result = match_findings(
        [finding(span())], [finding(span(), gold_id="z"), finding(span(), gold_id="a")]
    )
    assert [(item.prediction_index, item.gold_id) for item in result.matches] == [(0, "a")]
    assert len(result.ambiguous_pairs) == 2


def test_two_predictions_cannot_cover_one_gold_and_ties_use_prediction_index():
    result = match_findings([finding(span()), finding(span())], [finding(span(), gold_id="g")])
    assert [pair.prediction_index for pair in result.matches] == [0]
    assert len(result.ambiguous_pairs) == 2


def test_greedy_prefers_overlap_before_identifier():
    result = match_findings(
        [finding(span())], [finding(span(0, 5), gold_id="a"), finding(span(), gold_id="z")]
    )
    assert result.matches[0].gold_id == "z"


def test_unicode_offsets_are_character_offsets():
    dataset, artifact = fixture_data()
    case = next(case for case in dataset.cases if case.metadata.case_id == "unicode")
    prediction = next(item for item in artifact.cases if item.case_id == "unicode")
    result = match_findings(prediction.findings, case.gold.findings)
    assert len(result.matches) == 2
    evidence = case.gold.findings[0].evidence[0]
    assert case.document[evidence.start_offset : evidence.end_offset] == evidence.source_excerpt
    assert (
        case.document.encode()[: evidence.start_offset].decode(errors="replace")
        != case.document[: evidence.start_offset]
    )


def test_repeated_phrase_occurrence_does_not_match():
    dataset, artifact = fixture_data()
    case = next(case for case in dataset.cases if case.metadata.case_id == "repeated")
    prediction = next(item for item in artifact.cases if item.case_id == "repeated").model_copy(
        deep=True
    )
    evidence = prediction.findings[0].evidence[0]
    evidence.start_offset = case.document.index(evidence.source_excerpt)
    evidence.end_offset = evidence.start_offset + len(evidence.source_excerpt)
    assert case.document[evidence.start_offset : evidence.end_offset] == evidence.source_excerpt
    assert match_findings(prediction.findings, case.gold.findings).matches == []
    assert metrics_for(case, prediction)["evidence_containment_validity"] == 1
    assert metrics_for(case, prediction)["recall"] == 0


def test_titles_do_not_affect_matching():
    gold = finding(span(), gold_id="g")
    predicted = finding(span(20, 30))
    assert predicted.title == gold.title
    assert match_findings([predicted], [gold]).matches == []


def test_perfect_fixture_metrics():
    dataset, artifact = fixture_data()
    result = calculate_metrics(dataset.cases, {item.case_id: item for item in artifact.cases})
    assert result["gold_count"] == result["matched_count"] == result["prediction_count"] == 21
    for key in (
        "precision",
        "recall",
        "f1",
        "category_accuracy",
        "severity_accuracy",
        "exact_evidence_match_rate",
        "mean_best_evidence_iou",
        "evidence_containment_validity",
    ):
        assert result[key] == 1
    assert result["negative_cases"]["returning_zero_findings"] == 3
    assert result["negative_cases"]["false_positive_rate"] == 0
    assert result["reliability"]["failure_rate"] == 0
    assert result["by_category"]["REENTRANCY"]["low_sample"] is True


def test_all_missed_safe_zero_denominators():
    case, prediction = one_case()
    prediction.findings = []
    result = metrics_for(case, prediction)
    assert result["precision"] is None
    assert result["recall"] == 0
    assert result["f1"] is None
    assert result["miss_rate"] == 1
    assert result["unsupported_prediction_rate"] is None
    assert result["failure_analysis"][0]["reason"] == "MISSED"


def test_all_predictions_false_positive():
    case, prediction = one_case()
    prediction.findings[0].evidence = [span(0, 10)]
    result = metrics_for(case, prediction)
    assert result["precision"] == result["recall"] == result["f1"] == 0
    assert result["unsupported_prediction_rate"] == 1
    assert result["evidence_containment_validity"] == 0
    assert {item["reason"] for item in result["failure_analysis"]} == {
        "MISSED",
        "UNMATCHED_PREDICTION",
    }


def test_empty_negative_and_recall_denominator():
    dataset, artifact = fixture_data()
    case, prediction = dataset.cases[-1], artifact.cases[-1]
    result = metrics_for(case, prediction)
    assert result["precision"] is result["recall"] is result["f1"] is None
    assert result["negative_cases"]["returning_zero_findings"] == 1
    assert result["negative_cases"]["false_positive_rate"] == 0


def test_f1_counts_duplicate_prediction_as_unsupported():
    case, prediction = one_case()
    prediction.findings.append(prediction.findings[0].model_copy(deep=True))
    result = metrics_for(case, prediction)
    assert result["precision"] == 0.5
    assert result["recall"] == 1
    assert result["f1"] == pytest.approx(2 / 3)
    assert result["unsupported_prediction_rate"] == 0.5


@pytest.mark.parametrize(
    "field,value,key",
    [
        ("canonical_category", "REENTRANCY", "category_accuracy"),
        ("severity", "LOW", "severity_accuracy"),
    ],
)
def test_classification_exact_enum(field, value, key):
    case, prediction = one_case()
    setattr(prediction.findings[0], field, value)
    result = metrics_for(case, prediction)
    assert result[key] == 0
    assert result["recall"] == 1
    assert result["failure_analysis"][0]["reason"] == "CLASSIFICATION_DISAGREEMENT"
    assert result["by_severity"]["HIGH"]["classification_accuracy"] == (
        0 if key == "severity_accuracy" else 1
    )


def test_partial_evidence_quality():
    case, prediction = one_case()
    evidence = prediction.findings[0].evidence[0]
    length = len(evidence.source_excerpt)
    evidence.source_excerpt = evidence.source_excerpt[: length - 1]
    evidence.end_offset -= 1
    result = metrics_for(case, prediction)
    assert result["exact_evidence_match_rate"] == 0
    assert result["mean_best_evidence_iou"] == pytest.approx((length - 1) / length)
    assert result["evidence_containment_validity"] == 1


def test_negative_case_false_positive_rate():
    dataset, artifact = fixture_data()
    case = dataset.cases[-1]
    prediction = artifact.cases[-1].model_copy(deep=True)
    prediction.findings = [finding(span())]
    result = metrics_for(case, prediction)
    assert result["negative_cases"]["false_positive_rate"] == 1
    assert result["recall"] is None
    assert result["precision"] == 0


@pytest.mark.parametrize(
    "status,code", [("FAILED", "PROVIDER_TIMEOUT"), ("INCOMPLETE", "PERSISTENCE_ERROR")]
)
def test_failed_run_accounting_and_negative_not_credited(status, code):
    dataset, artifact = fixture_data()
    prediction = artifact.cases[-1].model_copy(update={"run_status": status, "failure_code": code})
    result = metrics_for(dataset.cases[-1], prediction)
    assert result["reliability"]["failure_rate"] == 1
    assert result["reliability"]["failure_codes"] == {code: 1}
    assert result["negative_cases"]["returning_zero_findings"] == 0
    assert result["negative_cases"]["false_positive_rate"] is None
    assert result["prediction_count"] == 0


def test_failed_positive_counts_as_missed_not_hallucination():
    case, prediction = one_case()
    prediction = prediction.model_copy(
        update={"run_status": "FAILED", "failure_code": "PROVIDER_RATE_LIMIT", "findings": []}
    )
    result = metrics_for(case, prediction)
    assert result["recall"] == 0
    assert result["unsupported_prediction_rate"] is None
    assert result["failure_analysis"][0]["run_status"] == "FAILED"


def test_report_provenance_and_groups(tmp_path):
    dataset, artifact = fixture_data()
    report = build_report(dataset, artifact)
    assert report["dataset_version"] == "chainsec-eval-v1"
    assert report["metric_version"] == "chainsec-metrics-v1"
    assert report["model"] == "deterministic-fixture"
    assert report["prompt_version"] == "extract-findings-v1"
    assert report["schema_version"] == "finding-output-v1"
    assert len(report["git_commit"]) == 40
    assert report["manifest_sha256"] == dataset.manifest_sha256
    assert report["case_count"] == 12 and report["gold_count"] == 21
    assert report["partial_selection"] is False
    assert report["quality_conclusion"] == "INCONCLUSIVE"
    assert report["automated_metrics"]["real_world"]["all"]["case_count"] == 0
    assert report["automated_metrics"]["synthetic"]["all"]["case_count"] == 12
    assert report["automated_metrics"]["synthetic"]["holdout"]["case_count"] == 6
    assert report["human_overrides_applied"] is False
    write_reports(report, tmp_path)
    assert json.loads((tmp_path / "report.json").read_text()) == report
    markdown = (tmp_path / "report.md").read_text()
    assert "real-world model quality" in markdown
    assert "Failure analysis" in markdown


def test_report_partial_selection():
    dataset, artifact = fixture_data()
    artifact.cases = artifact.cases[:1]
    report = build_report(dataset, artifact)
    assert report["partial_selection"] is True
    assert report["executed_case_ids"] == ["single"]


def test_only_human_reviewed_real_cases_in_headline():
    dataset, artifact = fixture_data()
    dataset.cases[0].metadata.redistribution_status = "REDISTRIBUTABLE"
    dataset.cases[1].metadata.redistribution_status = "REDISTRIBUTABLE"
    dataset.cases[1].metadata.human_reviewed = True
    report = build_report(dataset, artifact)
    assert report["automated_metrics"]["real_world"]["all"]["case_count"] == 1
    assert report["automated_metrics"]["real_world_unreviewed"]["all"]["case_count"] == 1
    assert report["automated_metrics"]["combined"]["all"]["case_count"] == 11
    assert report["quality_conclusion"] == "INCONCLUSIVE"  # Still offline fixtures.


@pytest.mark.parametrize("change", ["hash", "duplicate", "unknown"])
def test_prediction_identity_rejected(change):
    dataset, artifact = fixture_data()
    if change == "hash":
        artifact.manifest_sha256 = "0" * 64
    elif change == "duplicate":
        artifact.cases.append(artifact.cases[0])
    else:
        artifact.cases[0].case_id = "unknown"
    with pytest.raises(EvaluationError):
        build_report(dataset, artifact)


@pytest.mark.parametrize(
    "field", ["api_key", "raw_provider_exception", "response_payload", "headers"]
)
def test_artifact_rejects_private_fields_and_sanitizes_errors(tmp_path, capsys, field):
    data = json.loads(FIXTURE.read_bytes())
    data[field] = "sk-secret-raw-error-do-not-leak"
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps(data))
    assert main(["score", "--dataset", str(CORPUS), "--predictions", str(path)]) == 1
    output = capsys.readouterr()
    assert "sk-secret" not in output.err + output.out
    assert "Invalid normalized prediction artifact" in output.err


def test_report_does_not_read_environment_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-never-report")
    monkeypatch.setenv("EVAL_DATABASE_URL", "postgresql+psycopg://user:secret@localhost/db")
    dataset, artifact = fixture_data()
    serialized = json.dumps(build_report(dataset, artifact))
    assert "sk-secret" not in serialized
    assert "user:secret" not in serialized


@pytest.mark.parametrize(
    "status,code,findings",
    [
        ("FAILED", "raw sensitive exception", []),
        ("SUCCEEDED", "PROVIDER_ERROR", []),
        ("FAILED", "PROVIDER_ERROR", ["finding"]),
    ],
)
def test_prediction_lifecycle_and_codes_strict(status, code, findings, tmp_path):
    data = json.loads(FIXTURE.read_bytes())
    data["cases"][0].update(
        run_status=status,
        failure_code=code,
        findings=[data["cases"][0]["findings"][0]] if findings else [],
    )
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with pytest.raises(EvaluationError):
        load_predictions(path)


def test_validate_cli_offline(capsys):
    assert main(["validate", "--dataset", str(CORPUS)]) == 0
    assert "12 cases, 21 gold findings" in capsys.readouterr().out


def test_cli_score_outputs_both_reports(monkeypatch, tmp_path):
    monkeypatch.setattr("app.evaluation.cli.output_path", lambda requested: tmp_path)
    assert main(["score", "--dataset", str(CORPUS), "--predictions", str(FIXTURE)]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["automated_metrics"]["synthetic"]["all"]["f1"] == 1
    assert (tmp_path / "report.md").exists()


def test_default_cli_requires_command():
    with pytest.raises(SystemExit):
        main([])


def test_run_without_live_cannot_construct_provider(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("Provider must not be constructed")

    monkeypatch.setattr("app.evaluation.cli.OpenAIExtractionProvider", forbidden)
    assert main(["run", "--dataset", str(CORPUS)]) == 1
    assert "--live" in capsys.readouterr().err


def test_missing_eval_database_never_falls_back(monkeypatch, capsys):
    monkeypatch.delenv("EVAL_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost/development"
    )
    assert main(["run", "--dataset", str(CORPUS), "--live"]) == 1
    assert "EVAL_DATABASE_URL" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sqlite:///tmp/db",
        "postgresql://localhost/eval",
        "not-a-url",
        "postgresql+psycopg://localhost",
    ],
)
def test_evaluation_url_validation(monkeypatch, value):
    monkeypatch.setenv("EVAL_DATABASE_URL", value)
    with pytest.raises(EvaluationError):
        evaluation_database_url()


def test_missing_openai_config_before_database_or_provider(monkeypatch, capsys):
    monkeypatch.setenv("EVAL_DATABASE_URL", "postgresql+psycopg://localhost/not_connected")
    monkeypatch.setattr(
        "app.extraction.providers.openai.OpenAISettings.model_config",
        {
            **OpenAISettings.model_config,
            "env_file": None,
        },
    )
    assert main(["run", "--dataset", str(CORPUS), "--live"]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_entire_dataset_validated_before_selection_or_live_request(editable_dataset, monkeypatch):
    (editable_dataset / "cases/negative-injection.md").unlink()

    def forbidden():
        raise AssertionError("Provider must not be constructed")

    monkeypatch.setattr("app.evaluation.cli.OpenAIExtractionProvider", forbidden)
    assert main(["run", "--dataset", str(editable_dataset), "--live", "--case-id", "single"]) == 1


@pytest.mark.parametrize(
    "split,ids,count",
    [("all", None, 12), ("dev", None, 6), ("holdout", None, 6), ("all", ["unicode"], 1)],
)
def test_case_selection(split, ids, count):
    assert len(select_cases(load_dataset(CORPUS), split, ids)) == count


@pytest.mark.parametrize(
    "split,ids", [("all", ["unknown"]), ("dev", ["long"]), ("all", ["single", "single"])]
)
def test_invalid_selection(split, ids):
    with pytest.raises(EvaluationError):
        select_cases(load_dataset(CORPUS), split, ids)


def test_generated_outputs_stay_ignored_and_do_not_overwrite(tmp_path):
    with pytest.raises(EvaluationError, match="gitignored"):
        output_path(tmp_path / "output")
    path = REPOSITORY / "evals/runs"
    path.mkdir(exist_ok=True)
    with pytest.raises(EvaluationError, match="exists"):
        output_path(path)


def review_for(artifact, decisions):
    return Adjudication(
        predictions_sha256="0" * 64,
        reviewer="Test human reviewer",
        reviewed_at=datetime.now(UTC),
        human_reviewed=True,
        decisions=decisions,
    )


def test_human_override_separate_from_automated_metrics():
    dataset, artifact = fixture_data()
    artifact.cases = artifact.cases[:1]
    artifact.cases[0].findings[0].evidence = [span(0, 10)]
    original = artifact.model_dump_json()
    review = review_for(
        artifact, [Decision(case_id="single", action="MATCH", prediction_index=0, gold_id="g01")]
    )
    report = build_report(dataset, artifact, review)
    assert report["automated_metrics"]["synthetic"]["all"]["recall"] == 0
    assert report["adjudicated_metrics"]["synthetic"]["all"]["recall"] == 1
    assert report["human_overrides_applied"] is True
    assert report["adjudication"]["reviewer"] == "Test human reviewer"
    assert artifact.model_dump_json() == original


@pytest.mark.parametrize("action", ["VALID_NEW_FINDING", "UNSUPPORTED_FALSE_POSITIVE"])
def test_human_prediction_annotations_do_not_invent_gold_matches(action):
    dataset, artifact = fixture_data()
    review = review_for(artifact, [Decision(case_id="single", action=action, prediction_index=0)])
    report = build_report(dataset, artifact, review)
    assert report["automated_metrics"]["synthetic"]["all"]["matched_count"] == 21
    assert report["adjudicated_metrics"]["synthetic"]["all"]["matched_count"] == 20
    assert report["adjudication"]["decisions"][0]["action"] == action


def test_explicit_human_missed_removes_automatic_match():
    dataset, artifact = fixture_data()
    review = review_for(artifact, [Decision(case_id="single", action="MISSED", gold_id="g01")])
    assert (
        build_report(dataset, artifact, review)["adjudicated_metrics"]["synthetic"]["all"][
            "matched_count"
        ]
        == 20
    )


@pytest.mark.parametrize(
    "decisions",
    [
        [{"case_id": "single", "action": "MATCH", "prediction_index": 5, "gold_id": "g01"}],
        [{"case_id": "single", "action": "MATCH", "prediction_index": 0, "gold_id": "missing"}],
        [{"case_id": "missing", "action": "MISSED", "gold_id": "g01"}],
        [{"case_id": "single", "action": "MISSED", "gold_id": "g01"}] * 2,
        [{"case_id": "single", "action": "VALID_NEW_FINDING", "prediction_index": 0}] * 2,
    ],
)
def test_invalid_adjudication_targets(decisions):
    dataset, artifact = fixture_data()
    review = review_for(artifact, [Decision.model_validate(item) for item in decisions])
    with pytest.raises(EvaluationError):
        build_report(dataset, artifact, review)


def test_adjudication_hash_binding(tmp_path):
    _, digest = load_predictions(FIXTURE)
    review = review_for(None, [Decision(case_id="single", action="MISSED", gold_id="g01")])
    path = tmp_path / "review.json"
    path.write_text(review.model_dump_json())
    with pytest.raises(EvaluationError, match="identify"):
        load_adjudication(path, digest)
    review.predictions_sha256 = digest
    path.write_text(review.model_dump_json())
    assert load_adjudication(path, digest).reviewer == "Test human reviewer"


@pytest.mark.parametrize(
    "values",
    [
        {"case_id": "single", "action": "MATCH", "prediction_index": 0},
        {"case_id": "single", "action": "MISSED", "gold_id": "g01", "prediction_index": 0},
        {
            "case_id": "single",
            "action": "VALID_NEW_FINDING",
            "prediction_index": 0,
            "gold_id": "g01",
        },
    ],
)
def test_adjudication_schema_required_targets(values):
    with pytest.raises(ValidationError):
        Decision.model_validate(values)


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_adjudication_requires_explicit_true_boolean(value):
    with pytest.raises(ValidationError):
        Adjudication.model_validate(
            {
                "predictions_sha256": "0" * 64,
                "reviewer": "Human reviewer",
                "reviewed_at": "2026-10-09T00:00:00Z",
                "human_reviewed": value,
                "decisions": [{"case_id": "single", "action": "MISSED", "gold_id": "g01"}],
            }
        )


def test_adjudication_of_failed_run_rejected():
    dataset, artifact = fixture_data()
    artifact.cases[0] = artifact.cases[0].model_copy(
        update={
            "run_status": "FAILED",
            "failure_code": "PROVIDER_ERROR",
            "findings": [],
        }
    )
    review = review_for(artifact, [Decision(case_id="single", action="MISSED", gold_id="g01")])
    with pytest.raises(EvaluationError, match="successful extraction"):
        build_report(dataset, artifact, review)


def test_error_fixture_metrics_have_expected_nontrivial_values():
    dataset = load_dataset(CORPUS)
    artifact, _ = load_predictions(REPOSITORY / "evals/fixtures/predictions-with-errors.json")
    report = build_report(dataset, artifact)
    metrics = report["automated_metrics"]["synthetic"]["all"]
    assert report["partial_selection"] is True
    assert metrics["case_count"] == 3
    assert metrics["gold_count"] == 5
    assert metrics["prediction_count"] == 6
    assert metrics["matched_count"] == 4
    assert metrics["precision"] == pytest.approx(2 / 3)
    assert metrics["recall"] == 0.8
    assert metrics["f1"] == pytest.approx(8 / 11)
    assert metrics["category_accuracy"] == metrics["severity_accuracy"] == 0.75
    assert metrics["unsupported_prediction_rate"] == pytest.approx(1 / 3)
    assert metrics["miss_rate"] == 0.2
    assert metrics["exact_evidence_match_rate"] == metrics["mean_best_evidence_iou"] == 1
    assert metrics["negative_cases"]["false_positive_rate"] == 1
    assert metrics["reliability"]["failure_rate"] == pytest.approx(1 / 3)
    assert metrics["reliability"]["failure_codes"] == {"PROVIDER_TIMEOUT": 1}
