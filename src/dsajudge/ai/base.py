"""Provider protocols (PLAN.md §7.3). All AI goes through objects of these types."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class AIError(Exception):
    """An AI provider failed (unreachable, model missing, bad output). Message says what to do."""


class LLM(Protocol):
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, object] | None = None,
        images: list[Path] | None = None,
    ) -> str: ...


class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> str: ...
