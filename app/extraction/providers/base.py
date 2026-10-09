from typing import Protocol

from app.extraction.schemas import ExtractionOutput
from app.extraction.service import FAILURE_MESSAGES


class ExtractionProviderError(Exception):
    def __init__(self, code: str) -> None:
        if code not in FAILURE_MESSAGES:
            raise ValueError("Unsupported provider failure code")
        self.code = code
        super().__init__(FAILURE_MESSAGES[code])


class ExtractionProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def schema_version(self) -> str: ...

    async def extract(self, raw_text: str) -> ExtractionOutput: ...
