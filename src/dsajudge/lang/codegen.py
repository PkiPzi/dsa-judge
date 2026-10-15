"""Template rendering for stubs and drivers. Deterministic: never LLM-generated."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import jinja2

TEMPLATES = Path(__file__).parent / "templates"


@cache
def _env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    env.filters["pyrepr"] = repr
    return env


def render(name: str, **ctx: Any) -> str:
    return _env().get_template(name).render(**ctx)


def static(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def uses(part_types: list[str], kind: str) -> bool:
    return any(t.replace(" ", "") == kind for t in part_types)
