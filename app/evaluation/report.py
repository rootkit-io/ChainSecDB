import json
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.evaluation.dataset import REPOSITORY, Dataset, EvaluationError, read_bytes
from app.evaluation.metrics import calculate_metrics
from app.evaluation.schemas import METRIC_VERSION, Adjudication, Decision, Predictions


def git_provenance() -> tuple[str, bool]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPOSITORY, stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=REPOSITORY, stderr=subprocess.DEVNULL, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        raise EvaluationError("Git provenance unavailable; run from a Git checkout") from None
    return commit, bool(dirty)


def load_predictions(path: Path) -> tuple[Predictions, str]:
    raw = read_bytes(path)
    try:
        return Predictions.model_validate_json(raw), sha256(raw).hexdigest()
    except ValidationError:
        raise EvaluationError("Invalid normalized prediction artifact") from None


def load_adjudication(path: Path, predictions_sha256: str) -> Adjudication:
    try:
        review = Adjudication.model_validate_json(read_bytes(path))
    except ValidationError:
        raise EvaluationError("Invalid human adjudication artifact") from None
    if review.predictions_sha256 != predictions_sha256:
        raise EvaluationError("Adjudication does not identify this prediction artifact")
    return review


def build_report(
    dataset: Dataset, artifact: Predictions, review: Adjudication | None = None
) -> dict[str, Any]:
    if artifact.manifest_sha256 != dataset.manifest_sha256:
        raise EvaluationError("Predictions do not identify this dataset manifest")
    predictions = {case.case_id: case for case in artifact.cases}
    if len(predictions) != len(artifact.cases):
        raise EvaluationError("Duplicate prediction case ID")
    cases = [case for case in dataset.cases if case.metadata.case_id in predictions]
    if len(cases) != len(predictions):
        raise EvaluationError("Predictions contain unknown case IDs")
    decisions = review.decisions if review else []
    if any(item.case_id not in predictions for item in decisions):
        raise EvaluationError("Adjudication contains unexecuted case IDs")
    if any(predictions[item.case_id].run_status != "SUCCEEDED" for item in decisions):
        raise EvaluationError("Adjudication requires a successful extraction")

    def groups(overrides: list[Decision]) -> dict[str, Any]:
        synthetic = [case for case in cases if case.metadata.redistribution_status == "SYNTHETIC"]
        real = [
            case
            for case in cases
            if case.metadata.redistribution_status != "SYNTHETIC" and case.metadata.human_reviewed
        ]
        unreviewed = [case for case in cases if case not in synthetic and case not in real]
        selected = {
            "synthetic": synthetic,
            "real_world": real,
            "combined": synthetic + real,
            "real_world_unreviewed": unreviewed,
        }
        return {
            name: {
                "all": calculate_metrics(subset, predictions, overrides),
                "dev": calculate_metrics(
                    [case for case in subset if case.metadata.split == "dev"],
                    predictions,
                    overrides,
                ),
                "holdout": calculate_metrics(
                    [case for case in subset if case.metadata.split == "holdout"],
                    predictions,
                    overrides,
                ),
            }
            for name, subset in selected.items()
        }

    commit, dirty = git_provenance()
    automatic = groups([])
    return {
        "dataset_version": dataset.manifest.dataset_version,
        "manifest_sha256": dataset.manifest_sha256,
        "metric_version": METRIC_VERSION,
        "git_commit": commit,
        "working_tree_dirty": dirty,
        "prediction_git_commit": artifact.git_commit,
        "prediction_working_tree_dirty": artifact.working_tree_dirty,
        "provider": artifact.provider,
        "model": artifact.model,
        "prompt_version": artifact.prompt_version,
        "schema_version": artifact.schema_version,
        "timestamp": datetime.now(UTC).isoformat(),
        "prediction_timestamp": artifact.timestamp.isoformat(),
        "mode": artifact.mode,
        "case_count": len(cases),
        "gold_count": sum(len(case.gold.findings) for case in cases),
        "executed_case_ids": [case.metadata.case_id for case in cases],
        "partial_selection": len(cases) != len(dataset.cases),
        "human_overrides_applied": bool(decisions),
        "automated_metrics": automatic,
        "adjudicated_metrics": groups(decisions) if decisions else None,
        "adjudication": review.model_dump(mode="json") if review else None,
        "quality_conclusion": "INCONCLUSIVE"
        if artifact.mode == "OFFLINE" or not automatic["real_world"]["all"]["case_count"]
        else "BASELINE_REQUIRES_HUMAN_INTERPRETATION",
    }


def write_reports(report: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Extraction evaluation",
        "",
        f"Mode: {report['mode']}. "
        "Offline fixtures and synthetic cases do not establish real-world model quality.",
        "",
    ]
    for key in (
        "dataset_version",
        "manifest_sha256",
        "metric_version",
        "git_commit",
        "working_tree_dirty",
        "provider",
        "model",
        "prompt_version",
        "schema_version",
        "timestamp",
        "case_count",
        "gold_count",
        "partial_selection",
        "executed_case_ids",
        "human_overrides_applied",
        "quality_conclusion",
    ):
        lines.append(f"- {key}: {escape(str(report[key]))}")
    lines += [
        "",
        "## Automated metrics",
        "",
        "JSON is authoritative. Undefined ratios are null. Failed runs are reported separately; "
        "their missing gold contributes to end-to-end recall. Ambiguities require review.",
        "",
    ]
    keys = (
        "precision",
        "recall",
        "f1",
        "category_accuracy",
        "severity_accuracy",
        "exact_evidence_match_rate",
        "mean_best_evidence_iou",
        "evidence_containment_validity",
    )
    for group, splits in report["automated_metrics"].items():
        for split, metrics in splits.items():
            lines += [
                f"### {group} / {split}",
                "",
                f"Cases: {metrics['case_count']}; gold: {metrics['gold_count']}; "
                f"predictions: {metrics['prediction_count']}.",
                "",
            ]
            lines.extend(f"- {key}: {metrics[key]}" for key in keys)
            lines += [
                f"- negative-case FP rate: {metrics['negative_cases']['false_positive_rate']}",
                f"- run failure rate: {metrics['reliability']['failure_rate']}",
                "",
            ]
    for title, key in (
        ("Failure analysis, reliability, and sample sizes", "automated_metrics"),
        ("Human adjudication", "adjudication"),
        ("Adjudicated metrics (automated scores remain above)", "adjudicated_metrics"),
    ):
        lines += [f"## {title}", "", "```json", json.dumps(report[key], indent=2), "```", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
