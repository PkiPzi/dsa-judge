"""Deterministic fallbacks for what a small model gets wrong: method names, plain constraints."""

from __future__ import annotations

from dsajudge.ai.schemas import Extraction, ParamOut
from dsajudge.intake.structure import (
    apply_code_signature,
    constraints_from_text,
    method_from_code,
    normalize_type,
    signature_from_code,
)

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


def test_types_are_mapped_onto_the_tools_vocabulary() -> None:
    cases = {
        "vector<int>": "int[]", "vector<vector<int>>": "int[][]", "List[int]": "int[]",
        "List<Integer>": "int[]", "int[]": "int[]", "String": "string", "str": "string",
        "ListNode*": "ListNode", "Optional[TreeNode]": "TreeNode", "boolean": "bool",
        "long long": "long", "vector<string>&": "string[]", "char[][]": "char[][]",
        "float": "double", "void": "void", "weird<thing>": "weird<thing>",
    }  # fmt: skip
    for given, want in cases.items():
        assert normalize_type(given) == want, given


def _cls(header: str, body: str = "return 0;") -> str:
    return f"class Solution {{\npublic:\n    {header} {{\n        {body}\n    }}\n}};"


def test_signature_is_read_off_the_cpp_and_java_method_header() -> None:
    rotate = _cls("void rotate(vector<int>& nums, int k)", "k %= 3;")
    assert signature_from_code(rotate) == ("rotate", [("nums", "int[]"), ("k", "int")], "void")
    assert signature_from_code(CPP) == ("sumEven", [("nums", "int[]")], "long")
    assert signature_from_code(JAVA) == ("maxElement", [("nums", "int[]")], "int")
    grid = _cls("int f(vector<vector<char>>& g, string s)")
    assert signature_from_code(grid) == ("f", [("g", "char[][]"), ("s", "string")], "int")


def test_unreadable_or_ambiguous_signatures_give_none() -> None:
    assert signature_from_code(PY) is None  # python has no types to read
    two = _cls("int a(int x)") + "\n" + _cls("int b(int x)")
    assert signature_from_code(two) is None
    assert signature_from_code(_cls("int a(map<int, int>& m)")) is None


def test_the_models_param_names_survive_the_code_signature() -> None:
    ext = _ext([("arr", "string"), ("kk", "string")], [])
    ext.method, ext.returns = "wrong", "string"
    apply_code_signature(ext, [_cls("long long sumEven(vector<int>& a, int b)")])
    assert (ext.method, ext.returns) == ("sumEven", "long")
    assert [(p.name, p.type) for p in ext.params] == [("arr", "int[]"), ("kk", "int")]
