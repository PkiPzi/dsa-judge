"""stdio mode (PLAN.md §6.6): full programs reading stdin and writing stdout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsajudge.cli import app
from dsajudge.gen.manifest import load_manifest
from dsajudge.lang import get_language

from .conftest import simple_part, write_problem

runner = CliRunner()

REF_CPP = """\
#include <bits/stdc++.h>
using namespace std;
int main() {
    int n; cin >> n;
    long long s = 0;
    while (n--) { long long x; cin >> x; s += x; }
    cout << s << "\\n";
}
"""
BRUTE_PY = """\
import sys
data = sys.stdin.read().split()
n = int(data[0])
total = 0
for i in range(n):
    total += int(data[1 + i])
print(total)
"""

SOLUTIONS = {
    "cpp": {
        "ac": REF_CPP,
        "wa": REF_CPP.replace("cout << s", "cout << s + (s > 1000 ? 1 : 0)"),
        "re": REF_CPP.replace('cout << s << "\\n";', "return 3;"),
        "tle": REF_CPP.replace("long long s = 0;", "long long s = 0; for (;;) {}"),
        "ce": "int main() { this is not c++ }",
    },
    "python": {
        "ac": BRUTE_PY,
        "wa": BRUTE_PY.replace("print(total)", "print(total + (1 if total > 1000 else 0))"),
        "re": BRUTE_PY.replace("print(total)", "raise SystemExit(4)"),
        "tle": BRUTE_PY.replace("total = 0", "total = 0\nwhile True:\n    pass"),
        "ce": "def broken(:\n",
    },
    "java": {
        "ac": """\
import java.util.*;
public class Solution {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        long s = 0;
        for (int i = 0; i < n; i++) s += sc.nextLong();
        System.out.println(s);
    }
}
""",
    },
}
SOLUTIONS["java"]["wa"] = SOLUTIONS["java"]["ac"].replace(
    "println(s)", "println(s + (s > 1000 ? 1 : 0))"
)


def stdio_problem(home: Path, **extra: object) -> Path:
    part = simple_part(
        1,
        "solve",
        [("nums", "int[]")],
        "long",
        [([[1, 2, 3]], 6), ([[-5]], -5)],
        mode="stdio",
        input_format="{len(nums)}\n{nums}",
        constraints={"nums": {"len": [1, 300], "each": [-1000, 1000]}},
        refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user", "verified": True}],
        brute={"lang": "python", "file": "part1/brute.py", "origin": "user", "verified": True},
        **extra,
    )
    part["limits"] = {"time_ms": 400, "memory_mb": 256}
    data = {"schema": 1, "slug": "stdio-sum", "title": "Sum via stdin", "parts": [part]}
    return write_problem(
        home / "problems", data, {"part1/ref/ref.cpp": REF_CPP, "part1/brute.py": BRUTE_PY}
    )


def dsa(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


@pytest.fixture
def built_stdio(home: Path) -> Path:
    path = stdio_problem(home)
    code, out = dsa("build", "stdio-sum")
    assert code == 0, out
    return path


def work(monkeypatch: pytest.MonkeyPatch, home: Path, lang: str, code: str | None) -> Path:
    c, out = dsa("open", "stdio-sum", "--lang", lang, "--no-editor")
    assert c == 0, out
    wd = home / "work" / "stdio-sum" / "part1"
    sol = wd / get_language(lang).sol_filename
    if code is not None:
        sol.write_text(code, encoding="utf-8")
    monkeypatch.chdir(wd)
    return sol


def test_stdio_build_stores_text_answers(built_stdio: Path) -> None:
    manifest = load_manifest(built_stdio, 1)
    assert manifest and manifest.mode == "stdio" and manifest.oracle == "ref"
    assert manifest.stress and manifest.stress.cases >= 300
    out_file = built_stdio / "part1" / "tests" / "small" / "edge-01.out.json"
    answer = json.loads(out_file.read_text())
    assert isinstance(answer, str) and answer.lstrip("-").isdigit()
    # the input file is the canonical args; the program's stdin is rendered at run time
    first = (built_stdio / "part1" / "tests" / "small" / "edge-01.in.json").read_text()
    assert first.startswith("[")


@pytest.mark.parametrize("lang", ["cpp", "python", "java"])
def test_stdio_accepts_a_correct_program(
    built_stdio: Path, home: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    work(monkeypatch, home, lang, SOLUTIONS[lang]["ac"])
    code, out = dsa("submit")
    assert code == 0 and "Accepted" in out, out
    manifest = load_manifest(built_stdio, 1)
    assert manifest and f"passed {2 + len(manifest.tests)}/{2 + len(manifest.tests)}" in out


@pytest.mark.parametrize("lang", ["cpp", "python", "java"])
def test_stdio_wrong_answer_shows_text_input(
    built_stdio: Path, home: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    work(monkeypatch, home, lang, SOLUTIONS[lang]["wa"])
    code, out = dsa("submit")
    assert code == 1 and "Wrong Answer" in out, out
    assert "input:" in out and "expected:" in out and "got:" in out
    assert "first difference: token 1" in out


@pytest.mark.parametrize(
    ("lang", "variant", "label"),
    [
        ("cpp", "re", "Runtime Error"),
        ("cpp", "tle", "Time Limit Exceeded"),
        ("cpp", "ce", "Compile Error"),
        ("python", "re", "Runtime Error"),
        ("python", "tle", "Time Limit Exceeded"),
        ("python", "ce", "Compile Error"),
    ],
)
def test_stdio_other_verdicts(
    built_stdio: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    lang: str,
    variant: str,
    label: str,
) -> None:
    work(monkeypatch, home, lang, SOLUTIONS[lang][variant])
    code, out = dsa("submit")
    assert code == 1 and label in out.splitlines()[0], out


def test_stdio_stub_compiles_and_documents_the_input(
    built_stdio: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for lang in ("cpp", "python", "java"):
        sol = work(monkeypatch, home, lang, None)
        text = sol.read_text(encoding="utf-8")
        assert "Input format" in text and "{len(nums)}" in text
        code, out = dsa("run")
        # the stub reads nothing and prints nothing: it builds, then fails the samples
        assert code == 1 and "Wrong Answer" in out, (lang, out)


def test_stdio_unordered_and_float_checkers(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = """\
#include <bits/stdc++.h>
using namespace std;
int main() { int n; cin >> n; double s = 0; while (n--) { double x; cin >> x; s += x; }
  printf("%.9f\\n", s / 3); }
"""
    brute = """\
import sys
d = sys.stdin.read().split()
n = int(d[0])
print(sum(float(x) for x in d[1:1 + n]) / 3)
"""
    part = simple_part(
        1,
        "solve",
        [("xs", "double[]")],
        "double",
        [([[3.0, 3.0]], 2.0)],
        mode="stdio",
        input_format="{len(xs)}\n{xs}",
        constraints={"xs": {"len": [1, 50], "each": [-100, 100]}},
        checker="float:1e-6",
        refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user", "verified": True}],
        brute={"lang": "python", "file": "part1/brute.py", "origin": "user", "verified": True},
    )
    write_problem(
        home / "problems",
        {"schema": 1, "slug": "float-stdio", "title": "f", "parts": [part]},
        {"part1/ref/ref.cpp": ref, "part1/brute.py": brute},
    )
    code, out = dsa("build", "float-stdio")
    assert code == 0, out  # 9 decimals from C++ vs repr() from Python agree within 1e-6


def test_stdio_bad_format_is_a_build_error(home: Path) -> None:
    stdio_problem(home, output_format="{nope}")
    code, out = dsa("build", "stdio-sum")
    assert code == 1 and "unknown name" in out
