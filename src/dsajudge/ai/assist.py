"""AI-written brute, gen.py and checker.py, each verified by execution (PLAN.md §7.2).

The AI never produces expected outputs or verdicts. A candidate is accepted only when running it
proves it right: a brute must reproduce the statement's examples (and agree with a ref when one
exists), a generator must satisfy the validator on 200 inputs, and a checker must accept the
ref's outputs and reject mutated ones. Failures are fed back to the model; attempts are capped.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dsajudge.ai.base import LLM, AIError
from dsajudge.ai.prompts import load_prompt
from dsajudge.ai.retry import extract_code
from dsajudge.config import Config
from dsajudge.gen.custom import CustomGenerator, CustomValidator
from dsajudge.gen.plan import stress_plan
from dsajudge.gen.suite import load_part_gen, validate_script
from dsajudge.gen.validator import validate_args
from dsajudge.gen.values import GenError
from dsajudge.judge.checkers import part_checker
from dsajudge.judge.custom_checker import CheckerFailure, CustomChecker
from dsajudge.judge.limits import MemoryBackend
from dsajudge.judge.testset import (
    JudgeTest,
    OracleError,
    SourceRun,
    agree,
    run_source,
    sample_tests,
    tests_cache_dir,
    write_input,
)
from dsajudge.model.canon import dumps, short
from dsajudge.model.constraints import ConstraintError, parse_constraints
from dsajudge.model.schema import Part, Problem, SourceFile

CANDIDATES = ".candidates"
GEN_CHECKS = 200
MUTATIONS = 20
AGREEMENT_INPUTS = 60


class AssistFailed(AIError):
    """Every attempt was rejected. ``last`` is the final rejection reason."""

    def __init__(self, what: str, attempts: int, last: str) -> None:
        super().__init__(
            f"{what}: no verified answer after {attempts} attempt(s). Last problem: {last}"
        )
        self.last = last


@dataclass
class Written:
    path: str  # relative to the problem dir
    attempts: int
    verified: bool | str
    notes: list[str] = field(default_factory=list)


@dataclass
class AssistContext:
    problem_dir: Path
    problem: Problem
    cfg: Config
    backend: MemoryBackend

    def statement(self, part: Part) -> str:
        return (self.problem_dir / part.statement).read_text(encoding="utf-8")

    def candidate(self, part: Part, name: str) -> Path:
        d = self.problem_dir / f"part{part.id}" / CANDIDATES
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def rel(self, path: Path) -> str:
        return path.relative_to(self.problem_dir).as_posix()


def signature_text(part: Part) -> str:
    if part.mode == "ops":
        d = part.dsg
        calls = "; ".join(
            f"{m.name}({', '.join(f'{p.name}: {p.type}' for p in m.params)}) -> {m.returns}"
            for m in d.methods
        )
        ctor = ", ".join(f"{p.name}: {p.type}" for p in d.constructor)
        return f"class {d.class_name}({ctor}): {calls}"
    sig = part.sig
    params = ", ".join(f"{p.name}: {p.type}" for p in sig.params)
    ret = sig.returns if not sig.ret.is_void else f"void (modifies {sig.output_param} in place)"
    return f"{sig.method}({params}) -> {ret}"


def samples_text(part: Part) -> str:
    return "\n".join(f"{dumps(s.args)}  ->  {dumps(s.expected)}" for s in part.samples) or "(none)"


def _constraints_text(part: Part) -> str:
    return "\n".join(f"{k}: {dumps(v)}" for k, v in part.constraints.items()) or "(none)"


def _first_line(text: str, limit: int = 500) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _loop(
    llm: LLM,
    prompt_name: str,
    values: dict[str, str],
    attempts: int,
    lang: str,
    verify: Callable[[str, int], str | None],
    what: str,
) -> tuple[str, int]:
    """Ask for code, verify it, and feed rejections back until accepted or out of attempts."""
    prompt = load_prompt(prompt_name)
    feedback = ""
    last = "no attempt"
    for n in range(1, max(attempts, 1) + 1):
        system, user = prompt.render(**values, feedback=feedback)
        code = extract_code(llm.complete(user, system=system), lang)
        problem = verify(code, n)
        if problem is None:
            return code, n
        last = problem
        feedback = (
            "\nYour previous code was rejected:\n"
            f"{problem}\nFix it and reply with the complete corrected code in one fenced block."
        )
    raise AssistFailed(what, attempts, last)


# ---------------------------------------------------------------- inputs for verification


def small_inputs(ctx: AssistContext, part: Part, count: int) -> list[JudgeTest]:
    """Valid small inputs (DSL generator, or the part's gen.py) for verification runs."""
    pg = load_part_gen(ctx.problem_dir, part, ctx.cfg.plan, ctx.cfg.judge)
    specs = stress_plan(ctx.problem.slug, part.id, count)
    inputs = pg.generator.generate_many(specs)
    out: list[JudgeTest] = []
    d = tests_cache_dir(ctx.problem.slug, part.id) / "assist"
    for spec, args, errs in zip(specs, inputs, pg.validate_many(inputs), strict=True):
        if not errs:
            path = write_input(d, spec.id.replace("/", "-"), args)
            out.append(JudgeTest(id=spec.id, group="assist", path=path))
    return out


# ---------------------------------------------------------------- brute


def write_brute(
    llm: LLM, ctx: AssistContext, part: Part, *, attempts: int | None = None
) -> Written:
    """A Python brute that reproduces every example (and agrees with a ref, if there is one)."""
    attempts = attempts or ctx.cfg.ai.max_attempts
    samples = sample_tests(ctx.problem.slug, part)
    if not samples:
        raise AssistFailed("brute", 0, "the part has no examples to verify a brute against")
    check = part_checker(part, ctx.problem_dir, ctx.cfg.judge)
    extra: list[JudgeTest] = []
    ref = part.refs[0] if part.refs else None
    ref_out: dict[str, SourceRun] = {}
    if ref is not None:
        try:
            extra = small_inputs(ctx, part, AGREEMENT_INPUTS)
            ref_out = run_source(
                ctx.problem,
                ctx.problem_dir,
                part,
                ref,
                extra,
                ctx.cfg.judge,
                ctx.backend,
                ctx.cfg.judge.ref_timeout_s,
            )
        except (GenError, ConstraintError, OracleError):
            extra = []  # constraints not usable yet: examples alone verify the brute

    cand = ctx.candidate(part, "brute.py")

    def verify(code: str, n: int) -> str | None:
        cand.write_text(code, encoding="utf-8")
        src = SourceFile(lang="python", file=ctx.rel(cand), origin="llm")
        tests = samples + [
            t for t in extra if ref_out.get(t.id, SourceRun("", 0, 0, "")).status == "ok"
        ]
        try:
            runs = run_source(
                ctx.problem,
                ctx.problem_dir,
                part,
                src,
                tests,
                ctx.cfg.judge,
                ctx.backend,
                ctx.cfg.plan.brute_timeout_s,
            )
        except OracleError as e:
            return _first_line(str(e))
        for t in tests:
            run = runs[t.id]
            if run.status != "ok":
                return f"it failed on {t.id} ({run.status}): {_first_line(run.message)}"
            want = t.expected if t.has_expected else ref_out[t.id].output
            if not agree(check, t, want, run.output):
                shown = f" for input {_first_line(dumps(t.args()), 200)}"
                return (
                    f"on {t.id}{shown} it returned {short(run.output)} "
                    f"but the answer is {short(want)}"
                )
        return None

    code, used = _loop(
        llm,
        "write_brute",
        {
            "statement": ctx.statement(part),
            "signature": signature_text(part),
            "samples": samples_text(part),
        },
        attempts,
        "python",
        verify,
        "brute",
    )
    final = ctx.problem_dir / f"part{part.id}" / "brute.py"
    final.write_text(code, encoding="utf-8")
    verified: bool | str = "stress-only" if extra and ref is not None else False
    notes = (
        ["agrees with the ref on generated small inputs"]
        if verified
        else ["verified on the statement's examples only"]
    )
    return Written(ctx.rel(final), used, verified, notes)


# ---------------------------------------------------------------- gen.py


def write_gen(llm: LLM, ctx: AssistContext, part: Part, *, attempts: int | None = None) -> Written:
    """A gen.py whose inputs pass the validator on 200 samples (and any validate.py)."""
    attempts = attempts or ctx.cfg.ai.max_attempts
    try:
        specs = parse_constraints(part)
    except ConstraintError as e:
        raise AssistFailed("gen.py", 0, f"the constraints do not parse yet: {e}") from e
    cand = ctx.candidate(part, "gen.py")
    plan = stress_plan(ctx.problem.slug, part.id, GEN_CHECKS)
    vscript = validate_script(ctx.problem_dir, part)
    validator = CustomValidator(vscript, ctx.cfg.judge) if vscript else None

    def verify(code: str, n: int) -> str | None:
        cand.write_text(code, encoding="utf-8")
        try:
            inputs = CustomGenerator(cand, ctx.cfg.judge).generate_many(plan)
        except GenError as e:
            return _first_line(str(e), 700)
        for spec, args in zip(plan, inputs, strict=True):
            errs = validate_args(specs, args)
            if errs:
                return (
                    f"generate(rng, {spec.coarse_size!r}, {spec.flavor!r}) returned "
                    f"{_first_line(dumps(args), 200)} which breaks the constraints: {errs[0]}"
                )
        if validator is not None:
            for spec, msg in zip(plan, validator.validate_many(inputs), strict=True):
                if msg:
                    return f"generate(rng, {spec.coarse_size!r}, ...) broke a guarantee: {msg}"
        return None

    code, used = _loop(
        llm,
        "write_gen",
        {
            "statement": ctx.statement(part),
            "signature": signature_text(part),
            "constraints": _constraints_text(part),
            "guarantees": "\n".join(f"- {g}" for g in part.guarantees) or "(none)",
        },
        attempts,
        "python",
        verify,
        "gen.py",
    )
    final = ctx.problem_dir / f"part{part.id}" / "gen.py"
    final.write_text(code, encoding="utf-8")
    notes = [f"valid on {GEN_CHECKS} generated inputs"]
    if not part.guarantees:
        notes.append("the part has no guarantees; the DSL generator would also do")
    elif validator is None:
        notes.append(f"guarantees are not machine-checked: add part{part.id}/validate.py")
    return Written(ctx.rel(final), used, "stress-only", notes)


# ---------------------------------------------------------------- checker.py


def mutate(value: Any, k: int) -> Any:
    """A deliberately wrong variant of an answer; the k-th kind of corruption."""
    v = copy.deepcopy(value)
    kind = k % 5
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return v + (1, -1, 7, 1_000_003, -(abs(v) + 1))[kind]
    if isinstance(v, float):
        return v + (1.0, -1.0, 0.5, 1e6, 3.0)[kind]
    if isinstance(v, str):
        if not v:
            return "x"
        return (v + "x", v[:-1], "?" + v[1:], v + v, "")[kind]
    if isinstance(v, list):
        if not v:
            return [0]
        if kind == 0:
            return v[:-1]
        if kind == 1:
            return [*v, v[0]]
        if kind == 2:
            return [*v[:-1], mutate(v[-1], 0)]
        if kind == 3:
            return [mutate(v[0], 2), *v[1:]]
        return [mutate(x, 1) for x in v]
    if v is None:
        return 0
    return v


def write_checker(
    llm: LLM, ctx: AssistContext, part: Part, *, attempts: int | None = None
) -> Written:
    """A checker.py that accepts the oracle's outputs on many inputs and rejects mutated ones."""
    attempts = attempts or ctx.cfg.ai.max_attempts
    oracle = part.refs[0] if part.refs else part.brute
    if oracle is None:
        raise AssistFailed("checker.py", 0, "the part has neither a ref nor a brute to learn from")
    tests = sample_tests(ctx.problem.slug, part)
    try:
        tests += small_inputs(ctx, part, AGREEMENT_INPUTS)
    except (GenError, ConstraintError):
        pass
    try:
        runs = run_source(
            ctx.problem,
            ctx.problem_dir,
            part,
            oracle,
            tests,
            ctx.cfg.judge,
            ctx.backend,
            ctx.cfg.judge.ref_timeout_s,
        )
    except OracleError as e:
        raise AssistFailed(
            "checker.py", 0, f"the oracle does not run: {_first_line(str(e))}"
        ) from e
    good = [(t, runs[t.id].output) for t in tests if runs[t.id].status == "ok"]
    mutants: list[tuple[JudgeTest, Any, Any]] = []
    for i in range(MUTATIONS):
        t, out = good[i % len(good)]
        bad = mutate(out, i // max(len(good), 1) + i)
        if bad != out:
            mutants.append((t, out, bad))
    cand = ctx.candidate(part, "checker.py")

    def verify(code: str, n: int) -> str | None:
        cand.write_text(code, encoding="utf-8")
        checker = CustomChecker(cand, ctx.cfg.judge.python_exe())
        try:
            for t, out in good:
                ok, why = checker.check(t.args(), out, out)
                if not ok:
                    return (
                        f"it rejected the correct answer {short(out)} for input "
                        f"{_first_line(dumps(t.args()), 200)} ({why or 'no reason'})"
                    )
            for t, out, bad in mutants:
                ok, _ = checker.check(t.args(), out, bad)
                if ok:
                    return (
                        f"it accepted the wrong answer {short(bad)} for input "
                        f"{_first_line(dumps(t.args()), 200)} (a correct answer is {short(out)})"
                    )
        except CheckerFailure as e:
            return _first_line(str(e), 700)
        finally:
            checker.close()
        return None

    code, used = _loop(
        llm,
        "write_checker",
        {
            "statement": ctx.statement(part),
            "signature": signature_text(part),
            "samples": samples_text(part),
        },
        attempts,
        "python",
        verify,
        "checker.py",
    )
    final = ctx.problem_dir / f"part{part.id}" / "checker.py"
    final.write_text(code, encoding="utf-8")
    notes = [f"accepts {len(good)} oracle outputs, rejects {len(mutants)} mutated ones"]
    return Written(ctx.rel(final), used, "stress-only", notes)
