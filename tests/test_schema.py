from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from dsajudge.model.loader import ProblemError, load_problem
from dsajudge.model.schema import Problem

from .conftest import FIXTURE_PROBLEM, simple_part, write_problem


def test_fixture_loads() -> None:
    p = load_problem(FIXTURE_PROBLEM)
    assert p.slug == "max-subarray"
    assert [part.id for part in p.parts] == [1, 2, 3]
    assert p.parts[0].refs[0].lang == "cpp"
    assert p.parts[1].refs[0].origin == "friend"
    assert p.parts[2].refs == []
    assert p.parts[2].brute is not None and p.parts[2].brute.verified is False
    assert [part.derived_oracle() for part in p.parts] == ["ref", "ref", "brute-only"]
    assert p.parts[0].limits.time_ms == "auto"
    assert p.parts[2].limits.time_ms == 1000
    assert p.parts[0].constraints["nums"]["len"][1] == 100000


BASE: dict[str, Any] = {
    "schema": 1,
    "slug": "demo",
    "title": "Demo",
    "parts": [simple_part(1, "f", [("nums", "int[]")], "int", [([[1, 2]], 3)])],
}


def _with(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE)
    part = data["parts"][0]
    for key, value in changes.items():
        if key.startswith("sig_"):
            part["signature"][key[4:]] = value
        else:
            part[key] = value
    return data


def test_valid_synthetic(tmp_path: Path) -> None:
    d = write_problem(tmp_path, BASE)
    assert load_problem(d).parts[0].sig.method == "f"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sig_returns": "integer"}, "unknown type"),
        ({"sig_returns": "void"}, "output_param"),
        ({"sig_output_param": "nums"}, "only allowed with returns: void"),
        ({"samples": [{"args": [], "expected": 1}]}, "1 params"),
        ({"samples": [{"args": [[1, "x"]], "expected": 1}]}, "expected an integer"),
        ({"checker": "fuzzy"}, "checker"),
        ({"bogus_field": 1}, "Extra inputs"),
        ({"id": 2}, "1..n"),
        ({"sig_lang_types": {"java": {"nums": "List<Long>"}}}, "does not fit"),
    ],
)
def test_invalid(tmp_path: Path, changes: dict[str, Any], message: str) -> None:
    d = write_problem(tmp_path, _with(**changes))
    with pytest.raises(ProblemError, match=message):
        load_problem(d)


def test_missing_files(tmp_path: Path) -> None:
    data = _with(refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user"}])
    d = write_problem(tmp_path, data)
    with pytest.raises(ProblemError, match="missing files"):
        load_problem(d)


def test_void_inplace_ok() -> None:
    data = copy.deepcopy(BASE)
    data["parts"][0]["signature"].update(returns="void", output_param="nums")
    data["parts"][0]["samples"] = [{"args": [[2, 1]], "expected": [1, 2]}]
    p = Problem.model_validate(data)
    assert str(p.parts[0].sig.output_type) == "int[]"


def test_schema_version_required() -> None:
    data = copy.deepcopy(BASE)
    data["schema"] = 2
    with pytest.raises(ValueError):
        Problem.model_validate(data)
