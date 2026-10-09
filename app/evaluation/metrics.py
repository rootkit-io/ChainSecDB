from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from app.evaluation.dataset import LoadedCase
from app.evaluation.matching import match_findings
from app.evaluation.schemas import Decision, Prediction, Span


def ratio(numerator: int | float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def calculate_metrics(
    cases: list[LoadedCase], predictions: dict[str, Prediction], decisions: Sequence[Decision] = ()
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    categories: dict[str, Counter[str]] = defaultdict(Counter)
    severities: dict[str, Counter[str]] = defaultdict(Counter)
    analysis: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    overlaps = 0.0
    for case in cases:
        case_id = case.metadata.case_id
        prediction = predictions[case_id]
        gold = case.gold.findings
        counts["cases"] += 1
        counts[prediction.run_status] += 1
        if prediction.failure_code:
            failures[prediction.failure_code] += 1
        counts["gold"] += len(gold)
        counts["predictions"] += len(prediction.findings)
        if case.metadata.kind == "negative":
            counts["negative"] += 1
            if prediction.run_status == "SUCCEEDED":
                counts["negative_succeeded"] += 1
                counts["negative_zero"] += not prediction.findings
                counts["negative_fp"] += bool(prediction.findings)
        matching = match_findings(
            prediction.findings, gold, [item for item in decisions if item.case_id == case_id]
        )
        counts["matched"] += len(matching.matches)
        matched_gold = {pair.gold_id: pair for pair in matching.matches}
        matched_predictions = {pair.prediction_index for pair in matching.matches}
        ambiguities.extend(
            {"case_id": case_id, **asdict(pair)} for pair in matching.ambiguous_pairs
        )
        for finding in gold:
            category = categories[finding.canonical_category]
            severity = severities[finding.severity]
            category["gold_count"] += 1
            severity["gold_count"] += 1
            pair = matched_gold.get(finding.gold_id)
            if pair is None:
                analysis.append(
                    {
                        "reason": "MISSED",
                        "case_id": case_id,
                        "gold_id": finding.gold_id,
                        "category": finding.canonical_category,
                        "severity": finding.severity,
                        "evidence_locations": locations(finding.evidence),
                        "run_status": prediction.run_status,
                    }
                )
                continue
            item = prediction.findings[pair.prediction_index]
            category["matched_count"] += 1
            severity["matched_count"] += 1
            same_category = item.canonical_category == finding.canonical_category
            same_severity = item.severity == finding.severity
            counts["correct_category"] += same_category
            counts["correct_severity"] += same_severity
            severity["correct_severity"] += same_severity
            counts["exact_evidence"] += any(
                left == right for left in item.evidence for right in finding.evidence
            )
            overlaps += pair.iou
            if not same_category or not same_severity:
                analysis.append(
                    {
                        "reason": "CLASSIFICATION_DISAGREEMENT",
                        "case_id": case_id,
                        "gold_id": finding.gold_id,
                        "prediction_index": pair.prediction_index,
                        "expected_category": finding.canonical_category,
                        "predicted_category": item.canonical_category,
                        "expected_severity": finding.severity,
                        "predicted_severity": item.severity,
                    }
                )
        for index, predicted in enumerate(prediction.findings):
            for span in predicted.evidence:
                counts["evidence_spans"] += 1
                counts["valid_spans"] += (
                    span.end_offset <= len(case.document)
                    and case.document[span.start_offset : span.end_offset] == span.source_excerpt
                )
            if index not in matched_predictions:
                analysis.append(
                    {
                        "reason": "UNMATCHED_PREDICTION",
                        "case_id": case_id,
                        "prediction_index": index,
                        "category": predicted.canonical_category,
                        "severity": predicted.severity,
                        "evidence_locations": locations(predicted.evidence),
                    }
                )
    precision = ratio(counts["matched"], counts["predictions"])
    recall = ratio(counts["matched"], counts["gold"])
    f1 = (
        None
        if precision is None or recall is None
        else 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "case_count": counts["cases"],
        "gold_count": counts["gold"],
        "prediction_count": counts["predictions"],
        "matched_count": counts["matched"],
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "unsupported_prediction_rate": ratio(
            counts["predictions"] - counts["matched"], counts["predictions"]
        ),
        "miss_rate": ratio(counts["gold"] - counts["matched"], counts["gold"]),
        "category_accuracy": ratio(counts["correct_category"], counts["matched"]),
        "severity_accuracy": ratio(counts["correct_severity"], counts["matched"]),
        "exact_evidence_match_rate": ratio(counts["exact_evidence"], counts["matched"]),
        "mean_best_evidence_iou": ratio(overlaps, counts["matched"]),
        "evidence_containment_validity": ratio(counts["valid_spans"], counts["evidence_spans"]),
        "reliability": {
            "total_cases": counts["cases"],
            "succeeded": counts["SUCCEEDED"],
            "failed": counts["FAILED"],
            "incomplete": counts["INCOMPLETE"],
            "failure_rate": ratio(counts["FAILED"] + counts["INCOMPLETE"], counts["cases"]),
            "failure_codes": dict(sorted(failures.items())),
        },
        "negative_cases": {
            "total": counts["negative"],
            "succeeded": counts["negative_succeeded"],
            "returning_zero_findings": counts["negative_zero"],
            "false_positive_cases": counts["negative_fp"],
            "false_positive_rate": ratio(counts["negative_fp"], counts["negative_succeeded"]),
        },
        "by_category": {
            name: {
                "gold_count": value["gold_count"],
                "matched_count": value["matched_count"],
                "recall": ratio(value["matched_count"], value["gold_count"]),
                "low_sample": value["gold_count"] <= 2,
            }
            for name, value in sorted(categories.items())
        },
        "by_severity": {
            name: {
                "gold_count": value["gold_count"],
                "matched_count": value["matched_count"],
                "recall": ratio(value["matched_count"], value["gold_count"]),
                "classification_accuracy": ratio(value["correct_severity"], value["matched_count"]),
                "low_sample": value["gold_count"] <= 2,
            }
            for name, value in sorted(severities.items())
        },
        "failure_analysis": analysis,
        "ambiguous_pairs": ambiguities,
    }


def locations(spans: list[Span]) -> list[dict[str, int]]:
    return [{"start_offset": span.start_offset, "end_offset": span.end_offset} for span in spans]
