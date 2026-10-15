"""Integration: every spec type survives JSON -> solution -> JSON in each language."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dsajudge.config import Config
from dsajudge.judge.session import judge, make_target
from dsajudge.judge.verdicts import Verdict
from dsajudge.lang import get_language
from dsajudge.model.loader import load_problem
from dsajudge.model.schema import Lang

from .conftest import simple_part, write_problem

LANGS: list[Lang] = ["cpp", "java", "python"]

ECHO_SAMPLES: dict[str, list[Any]] = {
    "int": [0, -(2**31), 2**31 - 1],
    "long": [2**53 + 1, -(2**63), 2**62 + 12345],
    "double": [0.1, -2.5, 1e-9, 123456789.125, 3],
    "bool": [True, False],
    "char": ["a", '"', "\\", " "],
    "string": ["hello", "", 'q " b \\ n \n t \t /', "unicode é 😀 中文", "\u0001ctrl"],
    "int[]": [[], [1, -2, 3]],
    "long[]": [[2**53 + 1, -1, -(2**63)]],
    "double[]": [[0.5, -1.25, 2]],
    "bool[]": [[True, False, True]],
    "char[]": [["a", "b", "\\"]],
    "string[]": [[], ["", "x y", "é"]],
    "int[][]": [[], [[1, 2], [3]], [[]]],
    "long[][]": [[[2**62], [-(2**62), 0]]],
    "double[][]": [[[0.25], [1.5, -3.0]]],
    "bool[][]": [[[True], [False, True]]],
    "char[][]": [[["1", "0"], ["0", "1"]]],
    "string[][]": [[["ab", "c"], []]],
    "ListNode": [[], [1, 2, 3], [-5]],
    "TreeNode": [[], [1, None, 2, 3], [5, 4, 8, 11, None, 13, 4, 7, 2, None, None, None, 1]],
}

ECHO_TYPES = list(ECHO_SAMPLES)


def _body(lang: Lang, stub: str, body: dict[Lang, str]) -> str:
    if lang == "python":
        return stub.replace("        pass\n", f"        {body[lang]}\n")
    return stub.replace("{\n\n    }", "{\n        " + body[lang] + "\n    }")


def _parts() -> list[dict[str, Any]]:
    parts = []
    for i, t in enumerate(ECHO_TYPES, 1):
        parts.append(simple_part(i, "echo", [("x", t)], t, [([v], v) for v in ECHO_SAMPLES[t]]))
    n = len(parts)
    parts.append(
        simple_part(
            n + 1,
            "reverseList",
            [("head", "ListNode")],
            "ListNode",
            [([[1, 2, 3]], [3, 2, 1]), ([[]], [])],
        )
    )
    inplace = simple_part(n + 2, "sortIt", [("nums", "int[]")], "void", [([[3, 1, 2]], [1, 2, 3])])
    inplace["signature"]["output_param"] = "nums"
    parts.append(inplace)
    over = simple_part(
        n + 3,
        "pairs",
        [("nums", "int[]"), ("words", "string[]")],
        "int[][]",
        [([[1, 2], ["a", "b"]], [[1, 2], [2, 1]])],
    )
    over["signature"]["lang_types"] = {
        "java": {"nums": "List<Integer>", "words": "List<String>", "return": "List<List<Integer>>"}
    }
    parts.append(over)
    return parts


PARTS = _parts()
N_ECHO = len(ECHO_TYPES)

BODIES: dict[int, dict[Lang, str]] = {
    N_ECHO + 1: {
        "cpp": "ListNode* p = nullptr; while (head) { ListNode* n = head->next; head->next = p; "
        "p = head; head = n; } return p;",
        "java": "ListNode p = null; while (head != null) { ListNode n = head.next; head.next = p; "
        "p = head; head = n; } return p;",
        "python": "p = None\n        while head:\n"
        "            head.next, p, head = p, head, head.next\n        return p",
    },
    N_ECHO + 2: {
        "cpp": "sort(nums.begin(), nums.end());",
        "java": "Arrays.sort(nums);",
        "python": "nums.sort()",
    },
    N_ECHO + 3: {
        "cpp": "return {nums, {nums[1], nums[0]}};",
        "java": "return List.of(nums, List.of(nums.get(1), nums.get(0)));",
        "python": "return [nums, [nums[1], nums[0]]]",
    },
}


@pytest.fixture
def types_problem(home: Path) -> Path:
    data = {"schema": 1, "slug": "types", "title": "Type round trips", "parts": PARTS}
    return write_problem(home / "problems", data)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("part_id", range(1, len(PARTS) + 1))
def test_roundtrip(types_problem: Path, tmp_path: Path, lang: Lang, part_id: int) -> None:
    problem = load_problem(types_problem)
    part = problem.part(part_id)
    language = get_language(lang)
    body = BODIES.get(part_id, {"cpp": "return x;", "java": "return x;", "python": "return x"})
    src = tmp_path / language.sol_filename
    src.write_text(_body(lang, language.stub(problem.slug, part), body))
    report = judge(make_target(types_problem, part_id, lang, src), Config(), mode="run")
    f = report.first_failure
    detail = f and (f.id, f.got, f.expected, f.message)
    assert report.verdict == Verdict.AC, (
        f"{part.sig.method}({part.sig.params[0].type}) in {lang}: {report.verdict} "
        f"{report.compile_output} {detail}"
    )
    assert report.passed == len(part.samples)
