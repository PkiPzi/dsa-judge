"""Ask an LLM, validate the answer, and retry with the error fed back (PLAN.md §7.2)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from dsajudge.ai.base import LLM, AIError
from dsajudge.ai.prompts import load_prompt
from dsajudge.ai.schemas import json_schema

M = TypeVar("M", bound=BaseModel)
_FENCE = re.compile(r"```[ \t]*([\w+#-]*)[ \t]*\n(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """The JSON object in a model reply (fenced or bare)."""
    fenced = _FENCE.search(text)
    if fenced and fenced.group(1).lower() in ("", "json"):
        text = fenced.group(2)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in the reply")
    return text[start : end + 1]


def extract_code(text: str, lang_hint: str = "") -> str:
    """The first fenced code block of a reply (a bare reply is taken as code)."""
    blocks = _FENCE.findall(text)
    if blocks:
        if lang_hint:
            for lang, body in blocks:
                if lang.lower() in (lang_hint, {"cpp": "c++", "python": "py"}.get(lang_hint, "")):
                    return str(body).rstrip("\n") + "\n"
        return str(blocks[0][1]).rstrip("\n") + "\n"
    return text.strip("\n") + "\n"


def ask_structured(
    llm: LLM,
    prompt_name: str,
    model: type[M],
    values: dict[str, str],
    *,
    attempts: int = 3,
    check: Callable[[M], str | None] | None = None,
    feedback_var: str | None = None,
) -> M:
    """Call the LLM with a JSON schema; retry (up to ``attempts``) on bad JSON or a failed check.

    On a retry the previous error is appended to the user prompt (or put in ``feedback_var``
    if the prompt has one).
    """
    prompt = load_prompt(prompt_name)
    schema = json_schema(model)
    feedback = ""
    last = ""
    for _ in range(max(attempts, 1)):
        vals = dict(values)
        if feedback_var:
            vals[feedback_var] = feedback
        system, user = prompt.render(**vals)
        if feedback and not feedback_var:
            user += feedback
        reply = llm.complete(user, system=system, schema=schema)
        try:
            obj = model.model_validate(json.loads(extract_json(reply)))
        except (ValueError, ValidationError) as e:
            last = f"the reply was not valid: {_short(str(e))}"
        else:
            problem = check(obj) if check else None
            if not problem:
                return obj
            last = problem
        feedback = f"\n\nYour previous answer was rejected: {last}\nFix it and answer again."
    raise AIError(f"{prompt_name}: no usable answer after {attempts} attempt(s): {last}")


def _short(text: str, limit: int = 600) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."
