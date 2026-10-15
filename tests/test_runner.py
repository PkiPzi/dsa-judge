"""Integration: batch runner behaviour (TLE/crash attribution, stdout, CE, memory, debug)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dsajudge.config import Config, JudgeConfig
from dsajudge.judge.session import JudgeError, debug_test, judge, make_target, parse_custom_case
from dsajudge.judge.verdicts import Verdict
from dsajudge.model.schema import Lang

from .conftest import simple_part, write_problem

SAMPLES = [([1], 1), ([2], 2), ([3], 3)]


@pytest.fixture
def ident(home: Path) -> Path:
    part = simple_part(1, "f", [("x", "int")], "int", SAMPLES)
    part["limits"] = {"time_ms": 500, "memory_mb": 64}
    data = {"schema": 1, "slug": "ident", "title": "Identity", "parts": [part]}
    return write_problem(home / "problems", data)


def run(problem: Path, tmp_path: Path, lang: Lang, code: str, *, cfg: Config | None = None, **kw):  # type: ignore[no-untyped-def]
    name = {"cpp": "sol.cpp", "java": "Solution.java", "python": "sol.py"}[lang]
    src = tmp_path / name
    src.write_text(code)
    return judge(make_target(problem, 1, lang, src), cfg or Config(), mode="run", **kw)


def cpp(body: str, prelude: str = "") -> str:
    return prelude + "class Solution { public: int f(int x) { " + body + " } };\n"


def test_tle_mid_batch_lands_on_right_test(ident: Path, tmp_path: Path) -> None:
    code = cpp("if (x == 2) { volatile long long s = 0; while (true) s++; } return x;")
    report = run(ident, tmp_path, "cpp", code, all_tests=True)
    assert [(r.id, r.verdict) for r in report.results] == [
        ("sample/1", Verdict.AC),
        ("sample/2", Verdict.TLE),
        ("sample/3", Verdict.AC),
    ]
    assert report.verdict == Verdict.TLE
    assert report.passed == 2


def test_stop_at_first_failure(ident: Path, tmp_path: Path) -> None:
    code = cpp("if (x == 2) { volatile long long s = 0; while (true) s++; } return x;")
    report = run(ident, tmp_path, "cpp", code)
    assert [r.id for r in report.results] == ["sample/1", "sample/2"]
    assert report.passed == 1 and report.total == 3


def test_crash_mid_batch_rerun_in_isolation(ident: Path, tmp_path: Path) -> None:
    code = cpp("if (x == 2) { volatile int* p = nullptr; *p = 1; } return x;")
    report = run(ident, tmp_path, "cpp", code, all_tests=True)
    verdicts = [(r.id, r.verdict) for r in report.results]
    assert verdicts == [
        ("sample/1", Verdict.AC),
        ("sample/2", Verdict.RE),
        ("sample/3", Verdict.AC),
    ]
    assert "SIGSEGV" in report.results[1].message


def test_isolated_rerun_is_authoritative(ident: Path, tmp_path: Path) -> None:
    # Crashes only on the second call within one process: state leaked between tests.
    code = cpp(
        "static int calls = 0; if (++calls == 2) { volatile int* p = nullptr; *p = 1; } return x;"
    )
    report = run(ident, tmp_path, "cpp", code, all_tests=True)
    assert report.verdict == Verdict.AC
    assert [r.id for r in report.results] == ["sample/1", "sample/2", "sample/3"]


def test_user_stdout_does_not_corrupt_results(ident: Path, tmp_path: Path) -> None:
    code = cpp(
        'cout << "{\\"id\\":\\"sample/9\\",\\"status\\":\\"ok\\"}" << endl; '
        'cerr << "debug " << x << endl; return x;'
    )
    report = run(ident, tmp_path, "cpp", code)
    assert report.verdict == Verdict.AC
    assert '"sample/9"' in report.stdout


@pytest.mark.parametrize(
    ("lang", "code", "needle"),
    [
        ("cpp", "class Solution { public: int f(int x) { return x } };", "sol.cpp:1:"),
        ("java", "class Solution { public int f(int x) { return x } }", "Solution.java:1:"),
        ("python", "class Solution:\n    def f(self, x):\n        return x +\n", "sol.py:3:"),
    ],
)
def test_compile_errors(ident: Path, tmp_path: Path, lang: Lang, code: str, needle: str) -> None:
    report = run(ident, tmp_path, lang, code)
    assert report.verdict == Verdict.CE
    assert needle in report.compile_output
    assert str(tmp_path) in report.compile_output  # paths point at the user's file
    assert "<build>" not in report.compile_output


@pytest.mark.parametrize(
    ("lang", "code", "needle"),
    [
        (
            "cpp",
            cpp('if (x == 3) throw runtime_error("boom"); return x;'),
            "uncaught exception: boom",
        ),
        (
            "java",
            "class Solution { public int f(int x) { "
            'if (x == 3) throw new IllegalStateException("boom"); return x; } }',
            "IllegalStateException: boom",
        ),
        (
            "python",
            "class Solution:\n    def f(self, x):\n        if x == 3:\n"
            "            raise ValueError('boom')\n        return x\n",
            "ValueError: boom",
        ),
    ],
)
def test_runtime_exceptions(
    ident: Path, tmp_path: Path, lang: Lang, code: str, needle: str
) -> None:
    report = run(ident, tmp_path, lang, code)
    assert report.verdict == Verdict.RE
    failure = report.first_failure
    assert failure is not None and failure.id == "sample/3"
    assert needle in failure.message
    if lang == "python":
        assert str(tmp_path / "sol.py") in failure.message


def test_poll_backend_mle(ident: Path, tmp_path: Path) -> None:
    cfg = Config(judge=JudgeConfig(memory_backend="poll"))
    code = cpp("vector<char> v(400u << 20, 1); return x + v[x] - 1;")
    report = run(ident, tmp_path, "cpp", code, cfg=cfg)
    assert report.verdict == Verdict.MLE
    assert report.first_failure is not None and report.first_failure.id == "sample/1"


def test_time_multipliers(ident: Path, tmp_path: Path) -> None:
    py = "class Solution:\n    def f(self, x):\n        return x\n"
    assert run(ident, tmp_path, "python", py).tl_ms == 2500
    java = "class Solution { public int f(int x) { return x; } }"
    assert run(ident, tmp_path, "java", java).tl_ms == 1000


def test_custom_case(ident: Path, tmp_path: Path) -> None:
    from dsajudge.model.loader import load_problem

    part = load_problem(ident).part(1)
    args = parse_custom_case(part, "42")
    report = run(ident, tmp_path, "cpp", cpp("return x;"), custom=args)
    custom = [r for r in report.results if r.id == "custom"]
    assert custom and custom[0].got == 42 and not custom[0].has_expected
    with pytest.raises(JudgeError, match="takes 1"):
        parse_custom_case(part, "1\n2")
    with pytest.raises(JudgeError, match="expected an integer"):
        parse_custom_case(part, '"x"')


def test_debug_build_catches_out_of_bounds(ident: Path, tmp_path: Path) -> None:
    src = tmp_path / "sol.cpp"
    src.write_text(cpp("vector<int> v(2); return v[x + 5] + x;"))
    report, stderr = debug_test(make_target(ident, 1, "cpp", src), Config(), "sample/1")
    assert report.verdict == Verdict.RE
    assert "out-of-bounds" in stderr or "__n < this->size()" in stderr or "Error" in stderr
