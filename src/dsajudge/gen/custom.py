"""Custom ``gen.py`` / ``validate.py`` for parts the constraint DSL cannot describe.

These are user- or AI-written Python files, so they run in a separate isolated interpreter
(``python -I``), never inside the judge process. Interface (PLAN.md §7.2):

* ``gen.py``: ``def generate(rng: random.Random, size: str, flavor: str) -> list`` returning the
  arguments in signature order. ``size`` is edge | small | medium | max.
* ``validate.py`` (optional): ``def validate(args) -> str | None`` returning an error message for
  inputs that break a ``guarantee`` the DSL cannot express.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from dsajudge.config import JudgeConfig
from dsajudge.gen.plan import TestSpec
from dsajudge.gen.values import GenError

HARNESS = Path(__file__).with_name("harness.py")
TIMEOUT_S = 300.0


def _run(cfg: JudgeConfig, kind: str, script: Path, payload: dict[str, Any]) -> list[Any]:
    try:
        out = subprocess.run(
            [cfg.python_exe(), "-I", str(HARNESS), kind, str(script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            cwd=script.parent,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise GenError(f"{script.name} did not finish within {TIMEOUT_S:.0f}s") from e
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        tail = (out.stderr or out.stdout).strip()[-800:]
        raise GenError(f"{script.name} produced no usable output: {tail}") from e
    if "error" in data:
        raise GenError(f"{script.name} failed:\n{data['error'].rstrip()}")
    results = data["results"]
    assert isinstance(results, list)
    return results


class CustomGenerator:
    def __init__(self, script: Path, cfg: JudgeConfig) -> None:
        self.script = script
        self.cfg = cfg

    def generate_many(self, tests: list[TestSpec]) -> list[list[Any]]:
        reqs = [{"size": t.coarse_size, "flavor": t.flavor, "seed": t.seed} for t in tests]
        if not reqs:
            return []
        return _run(self.cfg, "gen", self.script, {"requests": reqs})


class CustomValidator:
    def __init__(self, script: Path, cfg: JudgeConfig) -> None:
        self.script = script
        self.cfg = cfg

    def validate_many(self, inputs: list[list[Any]]) -> list[str | None]:
        if not inputs:
            return []
        return _run(self.cfg, "validate", self.script, {"inputs": inputs})
