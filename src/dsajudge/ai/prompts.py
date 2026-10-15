"""Prompt files live in ``ai/prompts/*.md`` (CLAUDE.md: never inline strings).

Each file has a ``## system`` and a ``## user`` section; ``{{name}}`` marks a variable.
The rendered system prompt starts with ``[task: <file name>]`` so recorded responses
(``FakeLLM``) and logs can tell the tasks apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"
_VAR = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str
    system: str
    user: str

    def render(self, **values: str) -> tuple[str, str]:
        """(system, user) with variables filled in. Missing variables are an error."""

        def fill(text: str) -> str:
            def repl(m: re.Match[str]) -> str:
                key = m.group(1)
                if key not in values:
                    raise KeyError(f"prompt {self.name!r} needs variable {key!r}")
                return values[key]

            return _VAR.sub(repl, text)

        return f"[task: {self.name}]\n" + fill(self.system), fill(self.user)


@cache
def load_prompt(name: str) -> Prompt:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line in ("## system", "## user"):
            current = line[3:]
            sections[current] = []
        elif current:
            sections[current].append(line)
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"prompt {name!r} needs '## system' and '## user' sections")
    return Prompt(name, "\n".join(sections["system"]).strip(), "\n".join(sections["user"]).strip())


def task_of(system: str | None) -> str | None:
    """The ``[task: name]`` tag of a rendered system prompt."""
    if not system:
        return None
    m = re.match(r"\[task: ([\w-]+)\]", system)
    return m.group(1) if m else None
