from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.evaluation.dataset import EvaluationError
from app.evaluation.schemas import Decision, EvaluationFinding, GoldFinding, Span

IOU_THRESHOLD = 0.50


def span_iou(left: Span, right: Span) -> float:
    intersection = max(
        0, min(left.end_offset, right.end_offset) - max(left.start_offset, right.start_offset)
    )
    union = (
        left.end_offset - left.start_offset + right.end_offset - right.start_offset - intersection
    )
    return intersection / union


def best_iou(prediction: EvaluationFinding, gold: GoldFinding) -> float:
    return max(span_iou(left, right) for left in prediction.evidence for right in gold.evidence)


@dataclass(frozen=True)
class Match:
    prediction_index: int
    gold_id: str
    iou: float
    human_override: bool = False


@dataclass(frozen=True)
class Matching:
    matches: list[Match]
    ambiguous_pairs: list[Match]


def match_findings(
    predictions: list[EvaluationFinding],
    gold: list[GoldFinding],
    decisions: Sequence[Decision] = (),
) -> Matching:
    gold_by_id = {finding.gold_id: finding for finding in gold}
    used_predictions: set[int] = set()
    used_gold: set[str] = set()
    matches = []
    for decision in decisions:
        index, gold_id = decision.prediction_index, decision.gold_id
        if index is not None:
            if index >= len(predictions) or index in used_predictions:
                raise EvaluationError("Invalid or repeated adjudication prediction")
            used_predictions.add(index)
        if gold_id is not None:
            if gold_id not in gold_by_id or gold_id in used_gold:
                raise EvaluationError("Invalid or repeated adjudication gold target")
            used_gold.add(gold_id)
        if decision.action == "MATCH":
            assert index is not None and gold_id is not None
            matches.append(
                Match(index, gold_id, best_iou(predictions[index], gold_by_id[gold_id]), True)
            )

    candidates = []
    for index, prediction in enumerate(predictions):
        for finding in gold:
            overlap = best_iou(prediction, finding)
            if overlap >= IOU_THRESHOLD:
                candidates.append(Match(index, finding.gold_id, overlap))
    candidates.sort(key=lambda pair: (-pair.iou, pair.gold_id, pair.prediction_index))
    by_prediction = Counter(pair.prediction_index for pair in candidates)
    by_gold = Counter(pair.gold_id for pair in candidates)
    ambiguous = [
        pair
        for pair in candidates
        if by_prediction[pair.prediction_index] > 1 or by_gold[pair.gold_id] > 1
    ]
    for pair in candidates:
        if pair.prediction_index not in used_predictions and pair.gold_id not in used_gold:
            matches.append(pair)
            used_predictions.add(pair.prediction_index)
            used_gold.add(pair.gold_id)
    return Matching(matches, ambiguous)
