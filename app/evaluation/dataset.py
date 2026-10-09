from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.schemas import Case, Gold, Manifest
from app.schemas.documents import DocumentCreate

REPOSITORY = Path(__file__).resolve().parents[2]


class EvaluationError(Exception):
    """Safe errors only: never include input bodies or underlying exceptions."""


@dataclass(frozen=True)
class LoadedCase:
    metadata: Case
    document: str
    gold: Gold


@dataclass(frozen=True)
class Dataset:
    manifest: Manifest
    manifest_sha256: str
    cases: list[LoadedCase]


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise EvaluationError("Dataset/artifact file missing or unreadable") from None


def case_path(root: Path, path: str, local_only: bool) -> Path:
    resolved = (root / path).resolve()
    if local_only:
        if not Path(path).is_absolute() or resolved.is_relative_to(REPOSITORY):
            raise EvaluationError("LOCAL_ONLY documents and gold must remain outside repository")
    elif not resolved.is_relative_to(root.resolve()):
        raise EvaluationError("Redistributable fixture paths must stay inside dataset")
    return resolved


def load_dataset(root: Path) -> Dataset:
    raw_manifest = read_bytes(root / "manifest.json")
    try:
        manifest = Manifest.model_validate_json(raw_manifest)
    except ValidationError:
        raise EvaluationError("Invalid manifest schema or dataset version") from None
    if len({case.case_id for case in manifest.cases}) != len(manifest.cases):
        raise EvaluationError("Duplicate case ID")
    loaded = []
    for case in manifest.cases:
        local = case.redistribution_status == "LOCAL_ONLY"
        raw_document = read_bytes(case_path(root, case.document_path, local))
        raw_gold = read_bytes(case_path(root, case.gold_path, local))
        if sha256(raw_document).hexdigest() != case.document_sha256:
            raise EvaluationError(f"Document hash mismatch: {case.case_id}")
        if sha256(raw_gold).hexdigest() != case.gold_sha256:
            raise EvaluationError(f"Gold hash mismatch: {case.case_id}")
        try:
            document = raw_document.decode("utf-8")
            DocumentCreate(
                source_name=case.source_name,
                source_url=case.source_url,
                document_type=case.document_type,
                retrieved_at=case.retrieved_at,
                raw_text=document,
            )
            gold = Gold.model_validate_json(raw_gold)
        except (UnicodeError, ValidationError):
            raise EvaluationError(f"Invalid document or gold schema: {case.case_id}") from None
        if case.redistribution_status != "SYNTHETIC" and (
            not case.source_url or case.retrieved_at is None
        ):
            raise EvaluationError("Real cases require URL and retrieval provenance")
        if len({finding.gold_id for finding in gold.findings}) != len(gold.findings):
            raise EvaluationError(f"Duplicate gold ID: {case.case_id}")
        if (case.kind == "positive") != bool(gold.findings):
            raise EvaluationError(f"Positive/negative gold mismatch: {case.case_id}")
        for finding in gold.findings:
            for span in finding.evidence:
                if (
                    span.end_offset > len(document)
                    or document[span.start_offset : span.end_offset] != span.source_excerpt
                ):
                    raise EvaluationError(f"Gold evidence mismatch: {case.case_id}")
        loaded.append(LoadedCase(case, document, gold))
    return Dataset(manifest, sha256(raw_manifest).hexdigest(), loaded)


def select_cases(dataset: Dataset, split: str, ids: list[str] | None) -> list[LoadedCase]:
    known = {case.metadata.case_id for case in dataset.cases}
    if ids and (len(ids) != len(set(ids)) or not set(ids) <= known):
        raise EvaluationError("Unknown or duplicate selected case IDs")
    cases = [
        case
        for case in dataset.cases
        if (split == "all" or case.metadata.split == split)
        and (not ids or case.metadata.case_id in ids)
    ]
    if not cases or (ids and {case.metadata.case_id for case in cases} != set(ids)):
        raise EvaluationError("Case selection is empty or conflicts with split")
    return cases
