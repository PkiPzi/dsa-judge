"""Golden-file tests for stub and driver codegen. Refresh with DSA_UPDATE_GOLDEN=1."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from dsajudge.lang import get_language
from dsajudge.model.loader import load_problem
from dsajudge.model.schema import Part

from .conftest import FIXTURE_PROBLEM, simple_part

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("DSA_UPDATE_GOLDEN") == "1"


def _part(**kw: Any) -> Part:
    return Part.model_validate(kw)


CASES: dict[str, Part] = {
    "fixture-part1": load_problem(FIXTURE_PROBLEM).parts[0],
    "all-types": _part(
        **simple_part(
            1,
            "allTypes",
            [
                ("a", "int"),
                ("b", "long"),
                ("c", "double"),
                ("d", "bool"),
                ("e", "char"),
                ("f", "string"),
                ("g", "int[]"),
                ("h", "string[]"),
                ("i", "char[][]"),
                ("j", "long[][]"),
                ("k", "ListNode"),
                ("l", "TreeNode"),
            ],
            "double[][]",
            [],
        )
    ),
    "inplace-void": _part(
        **simple_part(
            1,
            "sortColors",
            [("nums", "int[]")],
            "void",
            [([[2, 0, 1]], [0, 1, 2])],
            signature={
                "method": "sortColors",
                "params": [{"name": "nums", "type": "int[]"}],
                "returns": "void",
                "output_param": "nums",
            },
        )
    ),
    "java-list-override": _part(
        **simple_part(
            1,
            "groupThem",
            [],
            "string[][]",
            [],
            signature={
                "method": "groupThem",
                "params": [
                    {"name": "strs", "type": "string[]"},
                    {"name": "sizes", "type": "int[]"},
                ],
                "returns": "string[][]",
                "lang_types": {"java": {"return": "List<List<String>>", "sizes": "List<Integer>"}},
            },
        )
    ),
}

FILES = {
    "cpp": ("sol.cpp", "main.cpp"),
    "java": ("Solution.java", "Main.java"),
    "python": ("sol.py", "driver.py"),
}


def _outputs(slug: str, part: Part) -> dict[str, str]:
    out: dict[str, str] = {}
    for lang, (stub_name, driver_name) in FILES.items():
        language = get_language(lang)
        out[f"{lang}-{stub_name}"] = language.stub(slug, part)
        out[f"{lang}-{driver_name}"] = language.driver_files(slug, part)[driver_name]
    return out


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden(case: str) -> None:
    outputs = _outputs("demo", CASES[case])
    d = GOLDEN / case
    if UPDATE:
        d.mkdir(parents=True, exist_ok=True)
        for name, text in outputs.items():
            (d / name).write_text(text, encoding="utf-8")
    for name, text in outputs.items():
        golden = d / name
        assert golden.exists(), f"missing {golden}; run with DSA_UPDATE_GOLDEN=1"
        assert text == golden.read_text(encoding="utf-8"), f"{case}/{name} changed"


def test_rendering_is_deterministic() -> None:
    part = CASES["all-types"]
    assert _outputs("demo", part) == _outputs("demo", part)
