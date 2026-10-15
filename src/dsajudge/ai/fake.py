"""Recorded-response providers for tests and the cloud environment (no GPU, no Ollama).

``FakeLLM`` answers from a list of rules. A rule matches on the task tag of the system prompt
(``[task: segment]``), optional text that must appear in the prompt, and optionally the file name
of an attached image. Each rule holds ``responses`` that are handed out in order; the last one
repeats unless ``repeat_last`` is false, which makes a test fail if a loop asks too often.

File format (``fixtures/ai/*.json``)::

    {"llm": [{"task": "segment", "response": {...}},
             {"task": "transcribe_image", "image": "statement.png", "response": "text"}],
     "transcriber": {"voice.m4a": "recorded transcript"}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dsajudge.ai.base import AIError
from dsajudge.ai.prompts import task_of


class NoRecordedResponse(AIError):
    pass


@dataclass
class Call:
    task: str | None
    prompt: str
    system: str | None
    schema: dict[str, object] | None
    images: list[Path]


@dataclass
class Rule:
    task: str | None = None
    contains: str | None = None
    image: str | None = None
    responses: list[Any] = field(default_factory=list)
    repeat_last: bool = True
    used: int = 0

    def matches(self, call: Call) -> bool:
        if self.task is not None and self.task != call.task:
            return False
        if self.contains is not None and self.contains not in call.prompt:
            return False
        return not (
            self.image is not None and not any(p.name.endswith(self.image) for p in call.images)
        )

    def next(self) -> Any:
        if self.used < len(self.responses):
            value = self.responses[self.used]
        elif self.repeat_last and self.responses:
            value = self.responses[-1]
        else:
            raise NoRecordedResponse("recorded responses for this rule are used up")
        self.used += 1
        return value


def _rule(raw: dict[str, Any]) -> Rule:
    responses = raw["responses"] if "responses" in raw else [raw["response"]]
    return Rule(
        task=raw.get("task"),
        contains=raw.get("contains"),
        image=raw.get("image"),
        responses=list(responses),
        repeat_last=bool(raw.get("repeat_last", True)),
    )


class FakeLLM:
    def __init__(self, rules: list[dict[str, Any]] | None = None) -> None:
        self.rules = [_rule(r) for r in rules or []]
        self.calls: list[Call] = []

    @classmethod
    def from_file(cls, path: Path) -> FakeLLM:
        return cls(json.loads(path.read_text(encoding="utf-8")).get("llm", []))

    def add(self, **rule: Any) -> None:
        """Add a rule at the front (so a test can override a recorded one)."""
        self.rules.insert(0, _rule(rule))

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, object] | None = None,
        images: list[Path] | None = None,
    ) -> str:
        call = Call(task_of(system), prompt, system, schema, list(images or []))
        self.calls.append(call)
        for rule in self.rules:
            if rule.matches(call):
                try:
                    value = rule.next()
                except NoRecordedResponse as e:
                    raise NoRecordedResponse(f"{e} (task {call.task!r})") from e
                return value if isinstance(value, str) else json.dumps(value)
        raise NoRecordedResponse(
            f"no recorded response for task {call.task!r}"
            + (f" with image {call.images[0].name!r}" if call.images else "")
        )

    def tasks(self) -> list[str | None]:
        return [c.task for c in self.calls]


class FakeTranscriber:
    """Maps an audio file name (suffix match) to its recorded transcript."""

    def __init__(self, transcripts: dict[str, str] | None = None) -> None:
        self.transcripts = dict(transcripts or {})
        self.calls: list[Path] = []

    @classmethod
    def from_file(cls, path: Path) -> FakeTranscriber:
        return cls(json.loads(path.read_text(encoding="utf-8")).get("transcriber", {}))

    def transcribe(self, audio: Path) -> str:
        self.calls.append(audio)
        for key, text in self.transcripts.items():
            if audio.name.endswith(key):
                return text
        if "*" in self.transcripts:
            return self.transcripts["*"]
        raise NoRecordedResponse(f"no recorded transcript for {audio.name!r}")
