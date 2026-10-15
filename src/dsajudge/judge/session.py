"""High-level judging used by the CLI: run, submit, debug."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dsajudge.config import Config
from dsajudge.judge.checkers import part_checker
from dsajudge.judge.limits import MemoryBackend, detect_backend, raise_stack_limit
from dsajudge.judge.runner import RawResult, RunLimits, run_tests
from dsajudge.judge.testset import (
    JudgeTest,
    OracleError,
    custom_test,
    manual_tests,
    resolve,
    sample_tests,
)
from dsajudge.judge.verdicts import Report, TestVerdict, Verdict, overall
from dsajudge.lang import get_language
from dsajudge.lang.base import Built, CompileError
from dsajudge.model.canon import text_to_args
from dsajudge.model.loader import load_problem
from dsajudge.model.schema import Lang, Part, Problem
from dsajudge.model.types import check_value
from dsajudge.store.bank import db_path
from dsajudge.store.db import DB, Attempt

Mode = Literal["run", "submit"]


class JudgeError(Exception):
    pass


@dataclass
class Target:
    """What to judge: a part of a bank problem and a solution file."""

    problem: Problem
    problem_dir: Path
    part: Part
    lang: Lang
    src: Path


def make_target(problem_dir: Path, part_id: int, lang: Lang, src: Path) -> Target:
    problem = load_problem(problem_dir)
    part = problem.part(part_id)
    if part.mode != "function":
        raise JudgeError(f"part {part_id} uses mode {part.mode!r}; stdio mode arrives in Phase 2")
    if not src.is_file():
        raise JudgeError(f"{src} not found (create it with `dsa open --lang {lang}`)")
    return Target(problem, problem_dir, part, lang, src)


def parse_custom_case(part: Part, text: str) -> list[Any]:
    try:
        args = text_to_args(text.replace("\\n", "\n"))
    except ValueError as e:
        raise JudgeError(str(e)) from e
    params = part.sig.params
    if len(args) != len(params):
        raise JudgeError(
            f"custom case has {len(args)} value(s); {part.sig.method} takes {len(params)} "
            f"({', '.join(p.name for p in params)}), one JSON value per line"
        )
    for p, a in zip(params, args, strict=True):
        err = check_value(p.tnode, a, p.name)
        if err:
            raise JudgeError(f"custom case: {err}")
    return args


def time_limit_ms(base_ms: float, lang: Lang, cfg: Config) -> float:
    return base_ms * cfg.judge.time_multipliers.get(lang, 1.0)


def _to_verdict(t: JudgeTest, r: RawResult, part: Part, *, with_io: bool) -> TestVerdict:
    names = [p.name for p in part.sig.params]
    v = TestVerdict(
        id=t.id,
        verdict=Verdict.AC,
        time_ms=r.time_ms,
        mem_kb=r.mem_kb,
        param_names=names,
        expected=t.expected,
        has_expected=t.has_expected,
        message=r.message,
    )
    if r.status == "ok":
        v.got, v.has_got = r.output, True
        if not t.has_expected:
            v.run_only = True
        elif not part_checker(part)(t.expected, r.output):
            v.verdict = Verdict.WA_UNVERIFIED if t.unverified else Verdict.WA
    elif r.status == "tle":
        v.verdict = Verdict.TLE
    elif r.status in ("mle", "oom"):
        v.verdict = Verdict.MLE
    elif r.status == "input":
        v.verdict = Verdict.JE
    else:
        v.verdict = Verdict.RE
        if r.status == "stackoverflow" and not v.message:
            v.message = "stack overflow"
    if with_io or not v.passed:
        try:
            v.args = t.args()
        except (OSError, ValueError):
            v.args = None
    return v


def _relocate(results: list[RawResult], built: Built, src: Path) -> None:
    """Point paths in error messages at the user's file instead of the build cache."""
    for name in ("sol.py", "sol.cpp", "Solution.java"):
        cached = str(built.cwd / name)
        for r in results:
            r.message = r.message.replace(cached, str(src))


def _backend(cfg: Config) -> MemoryBackend:
    raise_stack_limit()
    return detect_backend(cfg.judge.memory_backend)


def judge(
    target: Target,
    cfg: Config,
    *,
    mode: Mode,
    custom: list[Any] | None = None,
    all_tests: bool = False,
) -> Report:
    part, problem = target.part, target.problem
    backend = _backend(cfg)
    language = get_language(target.lang)

    samples = sample_tests(problem.slug, part)
    manual = manual_tests(target.problem_dir, part)
    tests: list[JudgeTest] = list(samples)
    if mode == "submit":
        tests += manual
    if custom is not None:
        tests.append(custom_test(problem.slug, part, custom))

    try:
        built: Built = language.build(target.src, problem.slug, part, cfg.judge)
    except CompileError as e:
        return Report(verdict=Verdict.CE, total=len(tests), compile_output=e.render(target.src))

    try:
        info = resolve(
            problem,
            target.problem_dir,
            part,
            tests,
            cfg.judge,
            backend,
            calibration_tests=samples + manual,
        )
    except OracleError as e:
        raise JudgeError(f"oracle problem in {problem.slug} part {part.id}: {e}") from e

    tl = time_limit_ms(info.tl_base_ms, target.lang, cfg)
    limits = RunLimits(time_ms=tl, memory_mb=part.limits.memory_mb)
    outcome = run_tests(
        built, [t.case for t in tests], limits, backend, stop_on_failure=not all_tests
    )
    by_id = {t.id: t for t in tests}
    _relocate(outcome.results, built, target.src)
    results = [_to_verdict(by_id[r.id], r, part, with_io=(mode == "run")) for r in outcome.results]
    report = Report(
        verdict=overall(results, len(tests)),
        total=len(tests),
        results=results,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        tl_ms=tl,
        memory_mb=part.limits.memory_mb,
        all_tests=all_tests,
        notes=info.notes,
    )
    if report.verdict == Verdict.JE and len(results) < len(tests) and not report.first_failure:
        report.notes.append("the solution process stopped before every test ran")
    return report


def debug_build(target: Target, cfg: Config) -> Built:
    return get_language(target.lang).build(
        target.src, target.problem.slug, target.part, cfg.judge, debug=True
    )


def find_test(target: Target, test_id: str) -> JudgeTest:
    for t in sample_tests(target.problem.slug, target.part) + manual_tests(
        target.problem_dir, target.part
    ):
        if t.id == test_id or t.id.split("/", 1)[-1] == test_id:
            return t
    raise JudgeError(f"no test {test_id!r} in {target.problem.slug} part {target.part.id}")


def debug_test(target: Target, cfg: Config, test_id: str) -> tuple[Report, str]:
    """Re-run one test with a debug build (C++: ASan/UBSan, -O0, _GLIBCXX_DEBUG)."""
    backend = _backend(cfg)
    t = find_test(target, test_id)
    try:
        built = debug_build(target, cfg)
    except CompileError as e:
        return Report(verdict=Verdict.CE, total=1, compile_output=e.render(target.src)), ""
    try:
        info = resolve(target.problem, target.problem_dir, target.part, [t], cfg.judge, backend)
    except OracleError as e:
        raise JudgeError(str(e)) from e
    tl = time_limit_ms(info.tl_base_ms, target.lang, cfg)
    # Sanitizers are slow: give the debug run 20x the limit and no memory cap.
    limits = RunLimits(time_ms=None, memory_mb=None, wall_s=tl / 1000 * 20 + 5)
    outcome = run_tests(built, [t.case], limits, backend)
    _relocate(outcome.results, built, target.src)
    outcome.stderr = outcome.stderr.replace(str(built.cwd / "sol.cpp"), str(target.src))
    results = [_to_verdict(t, r, target.part, with_io=True) for r in outcome.results]
    report = Report(
        verdict=overall(results, 1),
        total=1,
        results=results,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        tl_ms=tl,
        memory_mb=target.part.limits.memory_mb,
        all_tests=True,
    )
    return report, outcome.stderr


def code_sha(src: Path) -> str:
    return hashlib.sha256(src.read_bytes()).hexdigest()[:16]


def record(target: Target, report: Report, *, kind: Mode, strict: bool = False) -> Report:
    """Log the attempt and fill in the minutes since `dsa open`."""
    with DB(db_path()) as db:
        started = db.session_started(target.problem.slug, target.part.id)
        elapsed = time.time() - started if started else None
        failure = report.first_failure
        db.log_attempt(
            Attempt(
                slug=target.problem.slug,
                part=target.part.id,
                lang=target.lang,
                kind=kind,
                verdict=report.verdict.value,
                passed=report.passed,
                total=report.total,
                max_time_ms=report.max_time_ms,
                max_mem_kb=report.max_mem_kb,
                elapsed_s=elapsed,
                failed_test=failure.id if failure else None,
                strict=strict,
                code_sha=code_sha(target.src),
            )
        )
    report.elapsed_min = elapsed / 60 if elapsed is not None else None
    return report
