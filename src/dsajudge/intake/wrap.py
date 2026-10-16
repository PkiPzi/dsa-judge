"""Photographed solutions are often bare functions (or carry a ``main``); the judge calls
``Solution().method(...)``. This puts a thin ``Solution`` around the function the problem names,
leaving the author's code untouched, so the same file runs as written.

shortcut: a template, or a method inside a class that is not named Solution, is not wrapped
(the build fails and the review catches it).
"""

from __future__ import annotations

import re

from dsajudge.intake.structure import SIG, _split_params

HAS_SOLUTION = re.compile(r"\bclass\s+Solution\b")
ANY_CLASS = re.compile(r"^\s*(?:public\s+)?(?:class|struct)\s+\w+", re.MULTILINE)
MAIN = {
    "cpp": re.compile(r"\b(?:int|void)\s+main\s*\([^)]*\)\s*\{"),
    "java": re.compile(r"(?:public\s+)?static\s+void\s+main\s*\([^)]*\)\s*\{"),
}


def cut_main(code: str, lang: str) -> str:
    """The program's ``main`` removed (matching braces), as the judge supplies its own."""
    rx = MAIN.get(lang)
    m = rx.search(code) if rx else None
    if not m:
        return code
    depth, i = 1, m.end()
    while i < len(code) and depth:
        depth += (code[i] == "{") - (code[i] == "}")
        i += 1
    return code[: m.start()] + code[i:]


def long_long(code: str) -> str:
    """``int64_t`` and ``long`` are ``long`` here but the driver passes ``long long``: vectors of
    one do not convert to the other, so the solution is spelled the driver's way."""
    code = re.sub(r"\b(?:std::)?uint64_t\b", "unsigned long long", code)
    code = re.sub(r"\b(?:std::)?int64_t\b", "long long", code)
    return re.sub(r"\blong\b(?!\s+(?:long|double))", "long long", code)


def wrap_solution(code: str, lang: str, method: str) -> str:
    if lang == "cpp":
        code = long_long(code)
    if HAS_SOLUTION.search(code):
        return code
    if lang == "python":
        if re.search(rf"^def\s+{re.escape(method)}\s*\(", code, re.MULTILINE):
            return (
                f"{code.rstrip()}\n\n\nclass Solution:\n"
                f"    def {method}(self, *args):\n        return {method}(*args)\n"
            )
        return code
    if lang == "java":
        if ANY_CLASS.search(code):
            return code
        lines = cut_main(code, lang).split("\n")
        imports = [ln for ln in lines if ln.startswith("import ")]
        rest = [ln for ln in lines if not ln.startswith("import ")]
        return "\n".join([*imports, "class Solution {", *rest, "}"]) + "\n"
    if lang != "cpp":
        return code
    code = cut_main(code, lang)
    found = [m for m in SIG.finditer(code) if m.group("name") == method]
    if not found:
        return code
    m = found[-1]
    ret, params = m.group("ret").strip(), m.group("params").strip()
    names = []
    for raw in _split_params(params):
        n = re.search(r"(\w+)\s*(?:\[\d*\]\s*)*$", raw.strip())
        if not n:
            return code
        names.append(n.group(1))
    call = f"::{method}({', '.join(names)})"
    body = f"{call};" if ret == "void" else f"return {call};"
    head = f"{ret} {method}({params})"
    return f"{code.rstrip()}\n\nclass Solution {{\npublic:\n    {head} {{ {body} }}\n}};\n"
