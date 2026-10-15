from __future__ import annotations

import pytest

from dsajudge.model.types import (
    TypeError_,
    check_value,
    cpp_param,
    cpp_value,
    java_list_depth,
    java_type,
    parse_type,
    py_type,
)

TABLE = [
    # spec, C++ param, Java, Python  (PLAN.md §4.3)
    ("int", "int", "int", "int"),
    ("long", "long long", "long", "int"),
    ("double", "double", "double", "float"),
    ("bool", "bool", "boolean", "bool"),
    ("char", "char", "char", "str"),
    ("string", "string", "String", "str"),
    ("int[]", "vector<int>&", "int[]", "List[int]"),
    ("long[]", "vector<long long>&", "long[]", "List[int]"),
    ("string[]", "vector<string>&", "String[]", "List[str]"),
    ("int[][]", "vector<vector<int>>&", "int[][]", "List[List[int]]"),
    ("char[][]", "vector<vector<char>>&", "char[][]", "List[List[str]]"),
    ("ListNode", "ListNode*", "ListNode", "Optional[ListNode]"),
    ("TreeNode", "TreeNode*", "TreeNode", "Optional[TreeNode]"),
]


@pytest.mark.parametrize(("spec", "cpp", "java", "py"), TABLE)
def test_type_table(spec: str, cpp: str, java: str, py: str) -> None:
    t = parse_type(spec)
    assert str(t) == spec
    assert cpp_param(t) == cpp
    assert java_type(t) == java
    assert py_type(t) == py


def test_return_types_by_value() -> None:
    assert cpp_value(parse_type("int[][]")) == "vector<vector<int>>"
    assert cpp_value(parse_type("void", allow_void=True)) == "void"


@pytest.mark.parametrize("bad", ["integer", "int[][][]", "List<int>", "", "Int"])
def test_bad_types(bad: str) -> None:
    with pytest.raises(TypeError_):
        parse_type(bad)


def test_void_only_for_returns() -> None:
    with pytest.raises(TypeError_):
        parse_type("void")


def test_java_overrides() -> None:
    t = parse_type("int[][]")
    assert java_list_depth(t, "List<List<Integer>>") == 2
    assert java_list_depth(t, "List<int[]>") == 1
    assert java_list_depth(t, None) == 0
    assert java_type(t, "List<List<Integer>>") == "List<List<Integer>>"
    assert java_type(parse_type("string[]"), "List<String>") == "List<String>"
    with pytest.raises(TypeError_):
        java_list_depth(t, "List<Long>")
    with pytest.raises(TypeError_):
        java_list_depth(parse_type("int"), "Integer")


@pytest.mark.parametrize(
    ("spec", "value", "ok"),
    [
        ("int", 5, True),
        ("int", 2**31, False),
        ("int", True, False),
        ("long", 2**62, True),
        ("long", 2**63, False),
        ("double", 1, True),
        ("double", "1", False),
        ("bool", 0, False),
        ("char", "a", True),
        ("char", "ab", False),
        ("char", "é", False),
        ("string", "", True),
        ("int[]", [1, 2], True),
        ("int[]", [1, "2"], False),
        ("int[][]", [[1], []], True),
        ("ListNode", [1, 2], True),
        ("TreeNode", [1, None, 2], True),
        ("TreeNode", [None], False),
        ("TreeNode", [], True),
    ],
)
def test_check_value(spec: str, value: object, ok: bool) -> None:
    assert (check_value(parse_type(spec), value) is None) is ok
