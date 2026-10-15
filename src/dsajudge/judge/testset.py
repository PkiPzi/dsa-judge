"""Test collection, expected outputs from refs/brute, and auto time limits.

Phase 1 tests are the samples plus hand-written inputs in partN/tests/manual/*.in.json.
Expected outputs are never hand-written: samples carry theirs, and every other expected
output comes from running the part's refs (which must agree with each other and with the
samples) or, with no ref, the brute (reported as unverified). Results are cached by input
and source hashes under $XDG_CACHE_HOME/dsajudge/expected/. Phase 2's manifest replaces this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from dsajudge.config import JudgeConfig, cache_dir
from dsajudge.judge.cache import digest
from dsajudge.judge.checkers import get_checker
from dsajudge.judge.limits import MemoryBackend
from dsajudge.judge.runner import RunLimits, TestCase, run_tests
from dsajudge.lang import get_language
from dsajudge.lang.base import CompileError
from dsajudge.lang.codegen import TEMPLATES
from dsajudge.model.canon import args_to_text, dumps, read_args, short
from dsajudge.model.schema import Part, Problem, SourceFile

ORACLE_CACHE_VERSION = "1"
# Brute-force oracles are only run on inputs this small (serialized); bigger tests are run-only.
BRUTE_MAX_INPUT_BYTES = 20_000
BRUTE_TIMEOUT_S = 10.0
TL_MIN_MS, TL_MAX_MS, TL_DEFAULT_MS = 1000, 5000, 1000


class OracleError(Exception):
    """A ref or brute is broken (fails to compile, crashes, or disagrees)."""


@dataclass
class JudgeTest:
    id: str
    group: str  # sample | manual | custom
    path: Path
    expected: Any = None
    has_expected: bool = False
    unverified: bool = False

    @property
    def case(self) -> TestCase:
        return TestCase(self.id, self.path)

    def args(self) -> list[Any]:
        return read_args(self.path)


@dataclass
class OracleInfo:
    tl_base_ms: float
    source: str  # ref | brute | none
    notes: list[str] = field(default_factory=list)


def tests_cache_dir(slug: str, part_id: int) -> Path:
    return cache_dir() / "tests" / slug / f"part{part_id}"


def _write_input(dirpath: Path, name: str, args: list[Any]) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / f"{name}.in.json"
    text = args_to_text(args)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def sample_tests(slug: str, part: Part) -> list[JudgeTest]:
    d = tests_cache_dir(slug, part.id) / "samples"
    return [
        JudgeTest(
            id=f"sample/{i}",
            group="sample",
            path=_write_input(d, f"sample-{i}", s.args),
            expected=s.expected,
            has_expected=True,
        )
        for i, s in enumerate(part.samples, 1)
    ]


def manual_tests(problem_dir: Path, part: Part) -> list[JudgeTest]:
    d = problem_dir / f"part{part.id}" / "tests" / "manual"
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.in.json"))
    return [
        JudgeTest(id=f"manual/{f.name.removesuffix('.in.json')}", group="manual", path=f)
        for f in files
    ]


def custom_test(slug: str, part: Part, args: list[Any]) -> JudgeTest:
    d = tests_cache_dir(slug, part.id) / "custom"
    name = "custom-" + digest(args_to_text(args))[:12]
    return JudgeTest(id="custom", group="custom", path=_write_input(d, name, args))


@cache
def _templates_digest() -> str:
    files = sorted(p for p in TEMPLATES.iterdir() if p.is_file())
    return digest(*(f.read_bytes() for f in files))


def _source_key(problem_dir: Path, src: SourceFile) -> str:
    return digest(
        ORACLE_CACHE_VERSION,
        _templates_digest(),
        src.lang,
        (problem_dir / src.file).read_bytes(),
    )


@dataclass
class _SourceRun:
    status: str
    output: Any
    time_ms: float
    message: str


def _run_source(
    problem: Problem,
    problem_dir: Path,
    part: Part,
    src: SourceFile,
    tests: list[JudgeTest],
    cfg: JudgeConfig,
    backend: MemoryBackend,
    timeout_s: float,
) -> dict[str, _SourceRun]:
    """Run one ref/brute on tests, with a per-test result cache."""
    key = _source_key(problem_dir, src)
    cdir = cache_dir() / "expected" / problem.slug / f"part{part.id}" / key[:24]
    out: dict[str, _SourceRun] = {}
    todo: list[JudgeTest] = []
    for t in tests:
        f = cdir / (digest(t.path.read_bytes())[:32] + ".json")
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            out[t.id] = _SourceRun(d["status"], d["output"], d["time_ms"], d["message"])
        else:
            todo.append(t)
    if not todo:
        return out
    lang = get_language(src.lang)
    try:
        built = lang.build(problem_dir / src.file, problem.slug, part, cfg)
    except CompileError as e:
        raise OracleError(
            f"{src.file} does not compile:\n{e.render(problem_dir / src.file)}"
        ) from e
    limits = RunLimits(time_ms=None, memory_mb=None, wall_s=timeout_s)
    outcome = run_tests(built, [t.case for t in todo], limits, backend, stop_on_failure=False)
    by_id = {r.id: r for r in outcome.results}
    cdir.mkdir(parents=True, exist_ok=True)
    for t in todo:
        r = by_id.get(t.id)
        run = (
            _SourceRun(r.status, r.output, r.time_ms, r.message)
            if r
            else _SourceRun("crash", None, 0.0, "no result")
        )
        out[t.id] = run
        f = cdir / (digest(t.path.read_bytes())[:32] + ".json")
        f.write_text(
            json.dumps(
                {
                    "status": run.status,
                    "output": run.output,
                    "time_ms": run.time_ms,
                    "message": run.message,
                }
            ),
            encoding="utf-8",
        )
    return out


def resolve(
    problem: Problem,
    problem_dir: Path,
    part: Part,
    tests: list[JudgeTest],
    cfg: JudgeConfig,
    backend: MemoryBackend,
    *,
    calibration_tests: list[JudgeTest] | None = None,
) -> OracleInfo:
    """Fill in expected outputs for tests without them and compute the base time limit.

    ``calibration_tests`` (default: ``tests``) are the tests whose C++ ref timings set the
    auto time limit; pass the full suite so `run` and `submit` agree.
    """
    check = get_checker(part.checker)
    calib = calibration_tests if calibration_tests is not None else tests
    all_tests = {t.id: t for t in [*calib, *tests]}
    universe = list(all_tests.values())
    notes: list[str] = []

    if part.refs:
        runs = {
            r.file: _run_source(
                problem, problem_dir, part, r, universe, cfg, backend, cfg.ref_timeout_s
            )
            for r in part.refs
        }
        for ref in part.refs:
            for t in universe:
                run = runs[ref.file][t.id]
                if run.status != "ok":
                    raise OracleError(
                        f"ref {ref.file} failed on {t.id} ({run.status}): "
                        f"{run.message.strip()[:500]}"
                    )
                if t.has_expected and not check(t.expected, run.output):
                    raise OracleError(
                        f"ref {ref.file} disagrees with {t.id}: got {short(run.output)}, "
                        f"expected {short(t.expected)}"
                    )
        first = part.refs[0].file
        for t in universe:
            if t.has_expected:
                continue
            value = runs[first][t.id].output
            for other in part.refs[1:]:
                if not check(value, runs[other.file][t.id].output):
                    raise OracleError(
                        f"refs disagree on {t.id}: {first} gives {short(value)}, "
                        f"{other.file} gives {short(runs[other.file][t.id].output)}"
                    )
            t.expected, t.has_expected = value, True
        cpp_times = [
            runs[r.file][t.id].time_ms for r in part.refs if r.lang == "cpp" for t in calib
        ]
        base = (
            min(max(3 * max(cpp_times), TL_MIN_MS), TL_MAX_MS)
            if cpp_times
            else float(TL_DEFAULT_MS)
        )
        source = "ref"
    elif part.brute:
        small = [t for t in universe if t.path.stat().st_size <= BRUTE_MAX_INPUT_BYTES]
        runs1 = _run_source(
            problem, problem_dir, part, part.brute, small, cfg, backend, BRUTE_TIMEOUT_S
        )
        for t in small:
            run = runs1[t.id]
            if t.has_expected:
                if run.status == "ok" and not check(t.expected, run.output):
                    raise OracleError(
                        f"brute {part.brute.file} disagrees with {t.id}: got {short(run.output)}, "
                        f"expected {short(t.expected)}"
                    )
                continue
            if run.status == "ok":
                t.expected, t.has_expected, t.unverified = run.output, True, True
        run_only = [t.id for t in universe if not t.has_expected]
        if run_only:
            notes.append(
                f"{len(run_only)} test(s) too big for the brute are run-only "
                "(only TLE/MLE/RE can fail)"
            )
        base = float(TL_DEFAULT_MS)
        source = "brute"
    else:
        base = float(TL_DEFAULT_MS)
        source = "none"
        if any(not t.has_expected for t in tests):
            notes.append("no ref or brute: tests without expected outputs are run-only")

    if isinstance(part.limits.time_ms, int):
        base = float(part.limits.time_ms)
    return OracleInfo(tl_base_ms=base, source=source, notes=notes)


def describe_input(args: list[Any]) -> str:
    return " ".join(dumps(a) for a in args)
