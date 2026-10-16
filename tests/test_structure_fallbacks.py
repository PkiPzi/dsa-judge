"""Deterministic fallbacks for what a small model gets wrong: method names, plain constraints."""

from __future__ import annotations

from dsajudge.ai.schemas import Extraction, ParamOut
from dsajudge.intake.structure import constraints_from_text, method_from_code

CPP = (
    "class Solution {\npublic:\n    long long sumEven(vector<int>& nums) {\n"
    "        for (int x : nums) {\n            if (x % 2 == 0) s += x;\n        }\n"
    "        return s;\n"
    "    }\n};"
)
JAVA = (
    "class Solution {\n    public int maxElement(int[] nums) {\n        return nums[0];\n    }\n}"
)
PY = "class Solution:\n    def countVowels(self, s):\n        return 0"


def test_method_name_follows_the_solution_code() -> None:
    assert method_from_code("sumEvenElements", [CPP]) == "sumEven"
    assert method_from_code("maxValue", [JAVA]) == "maxElement"
    assert method_from_code("vowels", [PY]) == "countVowels"


def test_a_name_that_is_defined_or_ambiguous_is_kept() -> None:
    assert method_from_code("sumEven", [CPP]) == "sumEven"
    two = "class Solution:\n    def a(self, x):\n        pass\n    def b(self, x):\n        pass"
    assert method_from_code("c", [two]) == "c"
    assert method_from_code("c", []) == "c"


def _ext(params: list[tuple[str, str]], lines: list[str], statement: str = "") -> Extraction:
    return Extraction(
        title="t",
        statement=statement,
        method="f",
        params=[ParamOut(name=n, type=t) for n, t in params],
        returns="int",
        constraints_text=lines,
    )


def test_constraints_are_read_off_plain_lines() -> None:
    ext = _ext(
        [("nums", "int[]"), ("k", "int")],
        ["1 <= nums.length <= 10^5", "-10^4 <= nums[i] <= 10^{4}", "1 <= k <= 100"],
    )
    assert constraints_from_text(ext) == {
        "nums": {"len": ["1", "10^5"], "each": ["-10^4", "10^4"]},
        "k": {"range": ["1", "100"]},
    }


def test_string_constraints_need_a_charset_from_the_wording() -> None:
    ext = _ext([("s", "string")], ["1 <= s.length <= 10^5"], "A string s of lowercase letters.")
    assert constraints_from_text(ext) == {"s": {"len": ["1", "10^5"], "charset": "lowercase"}}
    # no alphabet stated anywhere: the narrowest one is assumed
    assert constraints_from_text(_ext([("s", "string")], ["1 <= s.length <= 10^5"])) == {
        "s": {"len": ["1", "10^5"], "charset": "lowercase"}
    }


def test_anything_unreadable_gives_none() -> None:
    assert constraints_from_text(_ext([("nums", "int[]")], ["1 <= nums.length <= 10^5"])) is None
    assert constraints_from_text(_ext([("g", "int[][]")], ["1 <= g.length <= 5"])) is None
