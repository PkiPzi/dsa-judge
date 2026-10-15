"""Spec type system for function-mode problems (PLAN.md §4.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

SCALARS = ("int", "long", "double", "bool", "char", "string")
Kind = Literal["scalar", "array", "listnode", "treenode", "void"]

INT_MIN, INT_MAX = -(2**31), 2**31 - 1
LONG_MIN, LONG_MAX = -(2**63), 2**63 - 1


class TypeError_(ValueError):
    """Invalid spec type or language override."""


@dataclass(frozen=True)
class TypeNode:
    kind: Kind
    base: str = ""  # scalar name, for scalar and array kinds
    depth: int = 0  # 1 for T[], 2 for T[][]

    def __str__(self) -> str:
        if self.kind == "scalar":
            return self.base
        if self.kind == "array":
            return self.base + "[]" * self.depth
        return {"listnode": "ListNode", "treenode": "TreeNode", "void": "void"}[self.kind]

    @property
    def is_void(self) -> bool:
        return self.kind == "void"

    def elem(self) -> TypeNode:
        """Element type of an array (one level down)."""
        assert self.kind == "array"
        if self.depth == 1:
            return TypeNode("scalar", self.base)
        return TypeNode("array", self.base, self.depth - 1)


def parse_type(text: str, *, allow_void: bool = False) -> TypeNode:
    s = text.replace(" ", "")
    if s == "void":
        if not allow_void:
            raise TypeError_("'void' is only allowed as a return type")
        return TypeNode("void")
    if s == "ListNode":
        return TypeNode("listnode")
    if s == "TreeNode":
        return TypeNode("treenode")
    m = re.fullmatch(r"([a-z]+)((?:\[\])*)", s)
    if not m or m.group(1) not in SCALARS:
        raise TypeError_(
            f"unknown type {text!r}; expected one of {', '.join(SCALARS)}, "
            "T[], T[][], ListNode, TreeNode"
        )
    depth = len(m.group(2)) // 2
    if depth == 0:
        return TypeNode("scalar", m.group(1))
    if depth > 2:
        raise TypeError_(f"{text!r}: at most two array dimensions are supported")
    return TypeNode("array", m.group(1), depth)


# ---------------------------------------------------------------- C++

_CPP_SCALAR = {
    "int": "int",
    "long": "long long",
    "double": "double",
    "bool": "bool",
    "char": "char",
    "string": "string",
}


def cpp_value(t: TypeNode) -> str:
    """C++ type used for values (return types, locals)."""
    if t.kind == "scalar":
        return _CPP_SCALAR[t.base]
    if t.kind == "array":
        inner = _CPP_SCALAR[t.base]
        for _ in range(t.depth):
            inner = f"vector<{inner}>"
        return inner
    if t.kind == "listnode":
        return "ListNode*"
    if t.kind == "treenode":
        return "TreeNode*"
    return "void"


def cpp_param(t: TypeNode) -> str:
    """C++ parameter type, LeetCode style: vectors by reference."""
    v = cpp_value(t)
    return v + "&" if t.kind == "array" else v


# ---------------------------------------------------------------- Java

_JAVA_SCALAR = {
    "int": "int",
    "long": "long",
    "double": "double",
    "bool": "boolean",
    "char": "char",
    "string": "String",
}
_JAVA_BOXED = {
    "int": "Integer",
    "long": "Long",
    "double": "Double",
    "bool": "Boolean",
    "char": "Character",
    "string": "String",
}


def java_native(t: TypeNode) -> str:
    if t.kind == "scalar":
        return _JAVA_SCALAR[t.base]
    if t.kind == "array":
        return _JAVA_SCALAR[t.base] + "[]" * t.depth
    if t.kind == "listnode":
        return "ListNode"
    if t.kind == "treenode":
        return "TreeNode"
    return "void"


def java_list_type(t: TypeNode, list_depth: int) -> str:
    """Java type where the outer ``list_depth`` array levels are List<> instead of arrays."""
    assert t.kind == "array" and 1 <= list_depth <= t.depth
    native_levels = t.depth - list_depth
    if native_levels:
        inner = _JAVA_SCALAR[t.base] + "[]" * native_levels
    else:
        inner = _JAVA_BOXED[t.base]
    for _ in range(list_depth):
        inner = f"List<{inner}>"
    return inner


def java_list_depth(t: TypeNode, override: str | None) -> int:
    """How many outer array levels a Java override turns into List<>; 0 for no override."""
    if not override:
        return 0
    o = override.replace(" ", "")
    if t.kind != "array":
        if o == java_native(t):
            return 0
        raise TypeError_(f"Java override {override!r} does not fit spec type {t}")
    for d in range(0, t.depth + 1):
        candidate = java_native(t) if d == 0 else java_list_type(t, d)
        if o == candidate.replace(" ", ""):
            return d
    raise TypeError_(f"Java override {override!r} does not fit spec type {t}")


def java_type(t: TypeNode, override: str | None = None) -> str:
    d = java_list_depth(t, override)
    return java_list_type(t, d) if d else java_native(t)


# ---------------------------------------------------------------- Python

_PY_SCALAR = {
    "int": "int",
    "long": "int",
    "double": "float",
    "bool": "bool",
    "char": "str",
    "string": "str",
}


def py_type(t: TypeNode) -> str:
    if t.kind == "scalar":
        return _PY_SCALAR[t.base]
    if t.kind == "array":
        inner = _PY_SCALAR[t.base]
        for _ in range(t.depth):
            inner = f"List[{inner}]"
        return inner
    if t.kind == "listnode":
        return "Optional[ListNode]"
    if t.kind == "treenode":
        return "Optional[TreeNode]"
    return "None"


# ---------------------------------------------------------------- canonical values


def check_value(t: TypeNode, v: Any, path: str = "value") -> str | None:
    """Return an error message if ``v`` is not a canonical JSON value of type ``t``."""
    if t.kind == "scalar":
        return _check_scalar(t.base, v, path)
    if t.kind == "array":
        if not isinstance(v, list):
            return f"{path}: expected an array for {t}"
        for i, x in enumerate(v):
            err = check_value(t.elem(), x, f"{path}[{i}]")
            if err:
                return err
        return None
    if t.kind == "listnode":
        if not isinstance(v, list):
            return f"{path}: expected an array for ListNode"
        for i, x in enumerate(v):
            err = _check_scalar("int", x, f"{path}[{i}]")
            if err:
                return err
        return None
    if t.kind == "treenode":
        if not isinstance(v, list):
            return f"{path}: expected a level-order array for TreeNode"
        if v and v[0] is None:
            return f"{path}: TreeNode root must not be null (use [] for an empty tree)"
        for i, x in enumerate(v):
            if x is None:
                continue
            err = _check_scalar("int", x, f"{path}[{i}]")
            if err:
                return err
        return None
    return None


def _check_scalar(base: str, v: Any, path: str) -> str | None:
    if base in ("int", "long"):
        if isinstance(v, bool) or not isinstance(v, int):
            return f"{path}: expected an integer, got {v!r}"
        lo, hi = (INT_MIN, INT_MAX) if base == "int" else (LONG_MIN, LONG_MAX)
        if not lo <= v <= hi:
            return f"{path}: {v} is out of range for {base}"
        return None
    if base == "double":
        if isinstance(v, bool) or not isinstance(v, int | float):
            return f"{path}: expected a number, got {v!r}"
        return None
    if base == "bool":
        return None if isinstance(v, bool) else f"{path}: expected true/false, got {v!r}"
    if base == "char":
        # ASCII only: a C++ char is one byte.
        if isinstance(v, str) and len(v) == 1 and ord(v) < 128:
            return None
        return f"{path}: expected a one-character ASCII string, got {v!r}"
    if base == "string":
        return None if isinstance(v, str) else f"{path}: expected a string, got {v!r}"
    return f"{path}: unknown scalar {base}"
