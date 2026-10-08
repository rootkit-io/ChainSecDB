from enum import StrEnum


class ExtractionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class InvalidExtractionTransitionError(Exception):
    pass


def require_transition(current: ExtractionStatus, target: ExtractionStatus) -> None:
    if (current, target) not in {
        (ExtractionStatus.PENDING, ExtractionStatus.RUNNING),
        (ExtractionStatus.RUNNING, ExtractionStatus.SUCCEEDED),
        (ExtractionStatus.RUNNING, ExtractionStatus.FAILED),
    }:
        raise InvalidExtractionTransitionError("Extraction transition is not allowed")
