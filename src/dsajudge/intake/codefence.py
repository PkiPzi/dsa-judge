"""Fence code in a photo transcript that the vision model returned as bare lines.

The transcribe prompt asks for fenced blocks, but small vision models often skip them, and the
rest of intake (stitching, segmentation, reference solutions) only sees fenced code. This finds
runs of code-looking lines in a transcript with NO fence at all and wraps them, naming the
language. It is conservative: prose ("Constraints:", examples, "return the sum ...") must not
look like code, and a run needs several code-looking lines.
"""

from __future__ import annotations

import re

STARTS = re.compile(
    r"^\s*(#include|#define|using\s+namespace|class\s+\w+|public\s*:|private\s*:|protected\s*:"
    r"|public\s+\w|private\s+\w|static\s+\w|def\s+\w+\s*\(|import\s+\w|from\s+\w+\s+import"
    r"|return\b|for\s*\(|while\s*\(|if\s*\(|else\b|elif\b|try\s*:|except\b|print\s*\(|//|@\w+)"
)
CLOSERS = re.compile(r"^\s*[}\])]+\s*[;,]?\s*$")
OPERATORS = re.compile(r"->|::|\+\+|&&|\|\||[;{]\s*$")
PY_HEADER = re.compile(
    r"^\s*(for|while|if|elif|else|try|except|with|def|class)\b.*:\s*$|\)\s*:\s*$"
)
INDENTED = re.compile(r"^(?: {4,}|\t)\S.*[=()\[\]]")
MIN_CODE_LINES = 2
MAX_GAP = 2  # non-code lines tolerated inside a code run (blank line, OCR slip, wrapped line)

JAVA = re.compile(
    r"\bpublic\s+(static\s+)?(int|long|boolean|String|void|double|List|int\[\])\b"
    r"|System\.|ArrayList|HashMap|\bint\[\]|new\s+int\[|@Override|import\s+java"
)
CPP = re.compile(
    r"#include|using\s+namespace|vector<|std::|unordered_|\blong\s+long\b|public\s*:|\w+&\s+\w+"
)
PYTHON = re.compile(r"^\s*(def\s+\w+|import\s+\w+|from\s+\w+\s+import|elif\b)|\bself\b|:\s*$")


def is_code_line(line: str) -> bool:
    if not line.strip():
        return False
    return bool(
        STARTS.match(line)
        or CLOSERS.match(line)
        or OPERATORS.search(line)
        or PY_HEADER.search(line)
        or INDENTED.match(line)
    )


def guess_lang(code: str) -> str:
    if JAVA.search(code):
        return "java"
    if CPP.search(code):
        return "cpp"
    if PYTHON.search(code) and ";" not in code and "{" not in code:
        return "python"
    return "cpp" if ";" in code or "{" in code else "python"


def _runs(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line ranges (end exclusive) of code runs: code-looking lines at most
    MAX_GAP lines apart, at least MIN_CODE_LINES of them."""
    runs: list[list[int]] = []
    for i, ln in enumerate(lines):
        if not is_code_line(ln):
            continue
        if runs and i - runs[-1][-1] <= MAX_GAP + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return [(r[0], r[-1] + 1) for r in runs if len(r) >= MIN_CODE_LINES]


def fence_code(text: str) -> str:
    """``text`` with each run of code lines fenced; unchanged if it already has any fence."""
    if "```" in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    pos = 0
    for start, end in _runs(lines):
        out += lines[pos:start]
        code = "\n".join(lines[start:end]).rstrip("\n")
        out += [f"```{guess_lang(code)}", *code.split("\n"), "```"]
        pos = end
    out += lines[pos:]
    return "\n".join(out)
