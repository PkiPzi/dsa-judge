"""`dsa build`: generate tests, validate them, run the oracles, stress, calibrate, write a manifest.

No AI is involved. Expected outputs come only from executing refs (or, with no ref, the brute,
labelled unverified). Every failure is a BuildError whose message says what to fix.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dsajudge.config import Config
from dsajudge.gen.manifest import (
    Manifest,
    SourceInfo,
    StressInfo,
    TestEntry,
    expected_sha,
    manifest_path,
    part_tests_dir,
    save_manifest,
    sha256_text,
    stored_name,
    stored_paths,
)
from dsajudge.gen.plan import STRESS_GROUP, TestSpec, make_plan, stress_plan
from dsajudge.gen.suite import PartGen, load_part_gen, spec_hash
from dsajudge.gen.values import GenError
from dsajudge.judge.cache import digest
from dsajudge.judge.checkers import CheckerError, part_checker
from dsajudge.judge.limits import MemoryBackend
from dsajudge.judge.runner import RunLimits
from dsajudge.judge.testset import (
    JudgeTest,
    OracleError,
    SourceRun,
    generated_dir,
    run_raw,
    run_source,
    sample_tests,
    tests_cache_dir,
    write_input,
)
from dsajudge.model.canon import args_to_text, dumps, short
from dsajudge.model.constraints import ConstraintError
from dsajudge.model.loader import constraint_line
from dsajudge.model.render import FormatError, check_format, render_input
from dsajudge.model.schema import Part, Problem, SourceFile
from dsajudge.oracle.shrink import shrink

TL_MIN_MS, TL_MAX_MS, TL_DEFAULT_MS = 1000, 5000, 1000
MIN_CUSTOM_GEN_CHECKS = 200


class BuildError(Exception):
    """The part cannot be built; the message explains what to fix."""


@dataclass
class BuildReport:
    slug: str
    part: int
    oracle: str
    counts: dict[str, int]
    stored: int
    tl_base_ms: float
    tl_source: str
    stress_cases: int
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class _Gen:
    spec: TestSpec
    args: list[Any]
    text: str
    test: JudgeTest

    @property
    def size_bytes(self) -> int:
        return len(self.text.encode("utf-8"))


def _where(problem_dir: Path, part: Part, param: str) -> str:
    line = constraint_line(problem_dir, part.id, param)
    loc = f"problem.yaml:{line}" if line else "problem.yaml"
    return f"{loc} (part {part.id}, constraints.{param})"


def _param_of(error: str) -> str | None:
    m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", error)
    return m.group(1) if m else None


def _describe_input(part: Part, args: list[Any]) -> str:
    if part.mode == "stdio":
        return "\n" + "\n".join("    " + ln for ln in render_input(part, args).rstrip().split("\n"))
    return ", ".join(
        f"{p.name} = {short(a, 300)}" for p, a in zip(part.sig.params, args, strict=True)
    )


def _src_info(problem_dir: Path, src: SourceFile) -> SourceInfo:
    data = (problem_dir / src.file).read_bytes()
    return SourceInfo(file=src.file, lang=src.lang, sha256=digest(data))


def _fail_message(what: str, t: JudgeTest, run: SourceRun) -> str:
    return f"{what} failed on {t.id} ({run.status}): {run.message.strip()[:600]}"


def build_part(
    problem_dir: Path,
    problem: Problem,
    part: Part,
    cfg: Config,
    backend: MemoryBackend,
    progress: Callable[[str], None] | None = None,
) -> BuildReport:
    started = time.monotonic()
    say = progress or (lambda _m: None)
    slug, plan, jcfg = problem.slug, cfg.plan, cfg.judge
    warnings: list[str] = []
    notes: list[str] = []

    try:
        check = part_checker(part)
    except (CheckerError, FormatError) as e:
        raise BuildError(f"part {part.id}: {e}") from e
    if part.mode == "stdio":
        names = [p.name for p in part.sig.params]
        try:
            check_format(part.input_format or "", names, "input_format")
            check_format(part.output_format or "{result}", ["result"], "output_format")
        except FormatError as e:
            raise BuildError(f"part {part.id}: {e}") from e

    if not part.refs and not part.brute:
        raise BuildError(
            f"part {part.id} has neither a ref nor a brute: add a reference solution under "
            f"part{part.id}/ref/ (listed in problem.yaml `refs`) or a brute.py (`brute`)"
        )

    try:
        pg = load_part_gen(problem_dir, part, plan, jcfg)
    except ConstraintError as e:
        raise BuildError(f"{e.args[0]}\n  at {_where(problem_dir, part, e.param)}") from e
    except GenError as e:
        raise BuildError(str(e)) from e

    # ---- samples must satisfy the constraints
    say("validating samples")
    sample_errs = pg.validate_many([s.args for s in part.samples])
    for i, (s, errs) in enumerate(zip(part.samples, sample_errs, strict=True), 1):
        if errs:
            param = _param_of(errs[0]) or ""
            raise BuildError(
                f"part {part.id} sample {i} breaks the constraints: {errs[0]}\n"
                f"  sample: {_describe_input(part, s.args)}\n"
                f"  constraint: {_where(problem_dir, part, param)}\n"
                "  A sample that fails its own constraints is usually a transcription error "
                "(10^5 read as 105); fix the constraint or the sample."
            )

    # ---- generate and validate
    specs = make_plan(slug, part.id, plan)
    say(f"generating {len(specs)} tests")
    gen_tests = _generate(pg, specs, part, slug, problem_dir)

    samples = sample_tests(slug, part)
    sample_inputs = {args_to_text(s.args) for s in part.samples}
    seen = set(sample_inputs)
    unique: list[_Gen] = []
    dups = 0
    for spec, args in gen_tests:
        text = args_to_text(args)
        if text in seen:
            dups += 1
            continue
        seen.add(text)
        path = write_input(generated_dir(slug, part.id), stored_name(spec.id), args)
        unique.append(_Gen(spec, args, text, JudgeTest(id=spec.id, group=spec.group, path=path)))
    if dups:
        notes.append(f"{dups} generated test(s) duplicated another input and were dropped")
    gens = unique
    jtests = [g.test for g in gens]
    by_id = {g.spec.id: g for g in gens}
    universe = [*samples, *jtests]

    # ---- oracle
    expected: dict[str, Any] = {t.id: t.expected for t in samples}
    unverified: set[str] = set()
    ref_times: dict[str, float] = {}
    cpp_times: list[float] = []
    try:
        if part.refs:
            say(f"running {len(part.refs)} ref(s) on {len(universe)} tests")
            oracle = "promoted" if any(r.origin == "promoted" for r in part.refs) else "ref"
            runs = {
                r.file: run_source(
                    problem, problem_dir, part, r, universe, jcfg, backend, jcfg.ref_timeout_s
                )
                for r in part.refs
            }
            for ref in part.refs:
                for t in universe:
                    run = runs[ref.file][t.id]
                    if run.status != "ok":
                        raise BuildError(_fail_message(f"ref {ref.file}", t, run))
                    if t.id in expected and not check(expected[t.id], run.output):
                        raise BuildError(
                            f"ref {ref.file} disagrees with {t.id}: got {short(run.output)}, "
                            f"expected {short(expected[t.id])}"
                        )
            first = part.refs[0].file
            for t in universe:
                value = runs[first][t.id].output
                for other in part.refs[1:]:
                    other_out = runs[other.file][t.id].output
                    if not check(value, other_out):
                        g = by_id.get(t.id)
                        shown = (
                            f"\n  input: {_describe_input(part, g.args)}"
                            if g and (g.size_bytes < 2000)
                            else ""
                        )
                        raise BuildError(
                            f"refs disagree on {t.id}: {first} gives {short(value)}, "
                            f"{other} gives {short(other_out)}{shown}"
                        )
                expected.setdefault(t.id, value)
            for r in part.refs:
                for t in universe:
                    ref_times[t.id] = max(ref_times.get(t.id, 0.0), runs[r.file][t.id].time_ms)
                    if r.lang == "cpp":
                        cpp_times.append(runs[r.file][t.id].time_ms)
        else:
            oracle = "brute-only"
            assert part.brute is not None
            say("no ref: using the brute as an unverified oracle")
            expected_b, unverified = _brute_oracle(
                problem, problem_dir, part, part.brute, samples, gens, check, cfg, backend
            )
            expected.update(expected_b)
            notes.append(
                "brute-only: "
                f"{len([g for g in gens if g.spec.id in expected])} generated tests have "
                "unverified expected outputs; max tests are run-only"
            )
    except OracleError as e:
        raise BuildError(str(e)) from e

    # ---- stress: ref vs brute
    stress_cases = 0
    if part.refs and part.brute:
        stress_cases = _stress(
            problem_dir, problem, part, pg, cfg, backend, check, samples, gens, expected, say
        )
    elif pg.custom:
        _selfcheck_custom(pg, slug, part, plan.stress_extra)

    # ---- time limit
    if isinstance(part.limits.time_ms, int):
        tl_base, tl_source = float(part.limits.time_ms), "fixed in problem.yaml"
    elif cpp_times:
        tl_base = float(min(max(3 * max(cpp_times), TL_MIN_MS), TL_MAX_MS))
        tl_source = "3 x slowest C++ ref test, clamped to 1-5 s"
    else:
        tl_base, tl_source = float(TL_DEFAULT_MS), "default: no C++ ref to calibrate from"

    # ---- weak tests
    if part.refs and part.brute:
        warn = _weak_check(
            problem_dir,
            problem,
            part,
            cfg,
            backend,
            [g.test for g in gens if g.spec.group == "max"],
            tl_base,
            say,
        )
        if warn:
            warnings.append(warn)

    # ---- write
    say("writing tests and manifest")
    tdir = part_tests_dir(problem_dir, part.id)
    manifest_path(problem_dir, part.id).unlink(missing_ok=True)
    shutil.rmtree(tdir / "small", ignore_errors=True)
    entries: list[TestEntry] = []
    stored = 0
    for g in gens:
        sid = g.spec.id
        has = sid in expected
        keep = g.spec.group != "max" and g.size_bytes <= plan.store_max_bytes
        entry = TestEntry(
            id=sid,
            group=g.spec.group,
            index=g.spec.index,
            seed=g.spec.seed,
            size=g.spec.size,
            flavor=g.spec.flavor,
            input_sha256=sha256_text(g.text),
            input_bytes=g.size_bytes,
            expected_sha256=expected_sha(expected[sid]) if has else None,
            stored=keep,
            unverified=sid in unverified,
            ref_time_ms=round(ref_times[sid], 3) if sid in ref_times else None,
        )
        entries.append(entry)
        if keep:
            in_path, out_path = stored_paths(problem_dir, part.id, sid)
            in_path.parent.mkdir(parents=True, exist_ok=True)
            in_path.write_text(g.text, encoding="utf-8")
            if has:
                out_path.write_text(dumps(expected[sid]) + "\n", encoding="utf-8")
            stored += 1
    manifest = Manifest(
        slug=slug,
        part=part.id,
        mode=part.mode,
        oracle=oracle,
        spec_hash=spec_hash(problem_dir, part, plan),
        generator="custom" if pg.custom else "dsl",
        tl_base_ms=tl_base,
        tl_source=tl_source,
        refs=[_src_info(problem_dir, r) for r in part.refs],
        brute=_src_info(problem_dir, part.brute) if part.brute else None,
        stress=StressInfo(cases=stress_cases, ref=part.refs[0].file, brute=part.brute.file)
        if stress_cases and part.brute
        else None,
        tests=entries,
        warnings=warnings,
    )
    save_manifest(problem_dir, manifest)
    return BuildReport(
        slug=slug,
        part=part.id,
        oracle=oracle,
        counts=manifest.counts(),
        stored=stored,
        tl_base_ms=tl_base,
        tl_source=tl_source,
        stress_cases=stress_cases,
        warnings=warnings,
        notes=notes,
        seconds=time.monotonic() - started,
    )


# ---------------------------------------------------------------- generation


def _generate(
    pg: PartGen, specs: list[TestSpec], part: Part, slug: str, problem_dir: Path
) -> list[tuple[TestSpec, list[Any]]]:
    try:
        args_list = pg.generator.generate_many(specs)
    except GenError as e:
        raise BuildError(f"part {part.id}: cannot generate tests: {e}") from e
    if len(args_list) != len(specs):
        raise BuildError(f"part {part.id}: generator returned {len(args_list)} of {len(specs)}")
    errors = pg.validate_many(args_list)
    for spec, errs in zip(specs, errors, strict=True):
        if errs:
            who = "gen.py produced" if pg.custom else "the generator produced"
            param = _param_of(errs[0]) or ""
            hint = (
                "fix gen.py or the constraint"
                if pg.custom
                else f"check {_where(problem_dir, part, param)}; this may be a generator bug"
            )
            raise BuildError(
                f"part {part.id}: {who} an invalid input for {spec.id} "
                f"({spec.size}/{spec.flavor}): {errs[0]}\n  {hint}"
            )
    return list(zip(specs, args_list, strict=True))


def _selfcheck_custom(pg: PartGen, slug: str, part: Part, extra: int) -> None:
    """A custom gen.py must pass the validator on 200 samples (PLAN.md §7.2)."""
    specs = stress_plan(slug, part.id, max(extra, MIN_CUSTOM_GEN_CHECKS))
    try:
        inputs = pg.generator.generate_many(specs)
    except GenError as e:
        raise BuildError(f"part {part.id}: gen.py self-check failed: {e}") from e
    for spec, errs in zip(specs, pg.validate_many(inputs), strict=True):
        if errs:
            raise BuildError(
                f"part {part.id}: gen.py output breaks the validator on {spec.id} "
                f"({spec.size}/{spec.flavor}): {errs[0]}"
            )


# ---------------------------------------------------------------- oracles


def _brute_oracle(
    problem: Problem,
    problem_dir: Path,
    part: Part,
    brute: SourceFile,
    samples: list[JudgeTest],
    gens: list[_Gen],
    check: Callable[[Any, Any], bool],
    cfg: Config,
    backend: MemoryBackend,
) -> tuple[dict[str, Any], set[str]]:
    """Expected outputs from the brute on inputs small enough for it; the rest is run-only."""
    plan = cfg.plan
    small = [
        g for g in gens if g.spec.group != "max" and g.size_bytes <= plan.brute_max_input_bytes
    ]
    tests = [*samples, *(g.test for g in small)]
    runs = run_source(
        problem, problem_dir, part, brute, tests, cfg.judge, backend, plan.brute_timeout_s
    )
    out: dict[str, Any] = {}
    unverified: set[str] = set()
    for t in samples:
        run = runs[t.id]
        if run.status != "ok":
            raise BuildError(_fail_message(f"brute {brute.file}", t, run))
        if not check(t.expected, run.output):
            raise BuildError(
                f"brute {brute.file} disagrees with {t.id}: got {short(run.output)}, "
                f"expected {short(t.expected)}. Fix the brute or the sample."
            )
    for g in small:
        run = runs[g.spec.id]
        if run.status != "ok":
            if g.spec.group in ("edge", "small_random"):
                raise BuildError(_fail_message(f"brute {brute.file}", g.test, run))
            continue  # too slow on a medium test: run-only
        out[g.spec.id] = run.output
        unverified.add(g.spec.id)
    return out, unverified


def _stress(
    problem_dir: Path,
    problem: Problem,
    part: Part,
    pg: PartGen,
    cfg: Config,
    backend: MemoryBackend,
    check: Callable[[Any, Any], bool],
    samples: list[JudgeTest],
    gens: list[_Gen],
    expected: dict[str, Any],
    say: Callable[[str], None],
) -> int:
    """Run ref against brute on the small groups plus extra small seeds; shrink a mismatch."""
    plan, jcfg, slug = cfg.plan, cfg.judge, problem.slug
    assert part.brute is not None and part.refs
    ref, brute = part.refs[0], part.brute
    extra_n = max(plan.stress_extra, MIN_CUSTOM_GEN_CHECKS if pg.custom else 0)
    extra_specs = stress_plan(slug, part.id, extra_n)
    extra_args: list[list[Any]] = []
    extra_tests: list[JudgeTest] = []
    if extra_specs:
        try:
            extra_args = pg.generator.generate_many(extra_specs)
        except GenError as e:
            raise BuildError(f"part {part.id}: cannot generate stress inputs: {e}") from e
        for spec, args, errs in zip(
            extra_specs, extra_args, pg.validate_many(extra_args), strict=True
        ):
            if errs:
                raise BuildError(
                    f"part {part.id}: invalid stress input {spec.id} "
                    f"({spec.size}/{spec.flavor}): {errs[0]}"
                )
            path = write_input(
                tests_cache_dir(slug, part.id) / STRESS_GROUP, stored_name(spec.id), args
            )
            extra_tests.append(JudgeTest(id=spec.id, group=STRESS_GROUP, path=path))
    base = [*samples, *(g.test for g in gens if g.spec.group in ("edge", "small_random"))]
    tests = [*base, *extra_tests]
    say(f"stress: ref vs brute on {len(tests)} small inputs")
    try:
        brute_runs = run_source(
            problem, problem_dir, part, brute, tests, jcfg, backend, plan.brute_timeout_s
        )
        ref_extra = run_source(
            problem, problem_dir, part, ref, extra_tests, jcfg, backend, jcfg.ref_timeout_s
        )
    except OracleError as e:
        raise BuildError(str(e)) from e
    failing: list[tuple[JudgeTest, Any, Any]] = []
    for t in tests:
        b = brute_runs[t.id]
        if b.status != "ok":
            raise BuildError(
                _fail_message(f"brute {brute.file}", t, b)
                + "\n  The brute must be correct and fast on small inputs."
            )
        if t.id in expected:
            ref_out = expected[t.id]
        else:
            r = ref_extra[t.id]
            if r.status != "ok":
                raise BuildError(_fail_message(f"ref {ref.file}", t, r))
            ref_out = r.output
        if not check(ref_out, b.output):
            failing.append((t, ref_out, b.output))
    if not failing:
        return len(tests)

    def size_of(t: JudgeTest) -> int:
        return t.path.stat().st_size

    worst, _, _ = min(failing, key=lambda f: (size_of(f[0]), f[0].id))
    args = worst.args()
    sdir = tests_cache_dir(slug, part.id) / "shrink"

    def materialize(cands: list[list[Any]]) -> list[JudgeTest]:
        return [
            JudgeTest(id=f"shrink/{i}", group="shrink", path=write_input(sdir, f"c{i}", c))
            for i, c in enumerate(cands)
        ]

    def valid(cands: list[list[Any]]) -> list[bool]:
        return [not e for e in pg.validate_many(cands)]

    def fails(cands: list[list[Any]]) -> list[bool]:
        ts = materialize(cands)
        rr = run_source(problem, problem_dir, part, ref, ts, jcfg, backend, jcfg.ref_timeout_s)
        bb = run_source(problem, problem_dir, part, brute, ts, jcfg, backend, plan.brute_timeout_s)
        return [
            rr[t.id].status == "ok"
            and bb[t.id].status == "ok"
            and not check(rr[t.id].output, bb[t.id].output)
            for t in ts
        ]

    say(f"stress: {len(failing)} mismatch(es); shrinking the smallest")
    small = shrink(args, valid, fails)
    (final,) = materialize([small])
    r_out = run_source(problem, problem_dir, part, ref, [final], jcfg, backend, jcfg.ref_timeout_s)[
        final.id
    ].output
    b_out = run_source(
        problem, problem_dir, part, brute, [final], jcfg, backend, plan.brute_timeout_s
    )[final.id].output
    raise BuildError(
        f"stress: ref and brute disagree on {len(failing)} of {len(tests)} small inputs. "
        "Smallest counterexample:\n"
        f"  input: {_describe_input(part, small)}\n"
        f"  ref   ({ref.file}): {short(r_out, 300)}\n"
        f"  brute ({brute.file}): {short(b_out, 300)}\n"
        "  One of them is wrong; check both against the statement."
    )


def _weak_check(
    problem_dir: Path,
    problem: Problem,
    part: Part,
    cfg: Config,
    backend: MemoryBackend,
    max_tests: list[JudgeTest],
    tl_base: float,
    say: Callable[[str], None],
) -> str | None:
    """Warn if the brute passes every max test within the time limit (PLAN.md §6.4)."""
    assert part.brute is not None
    if not max_tests:
        return None
    plan = cfg.plan
    tl = tl_base * cfg.judge.time_multipliers.get(part.brute.lang, 1.0)
    wall = min(tl / 1000 * 1.5 + 0.5, plan.weak_check_cap_s)
    say(f"checking that max tests defeat the brute (limit {tl:.0f} ms)")
    limits = RunLimits(time_ms=tl, memory_mb=None, wall_s=wall)
    try:
        outcome = run_raw(
            problem,
            problem_dir,
            part,
            part.brute,
            max_tests,
            cfg.judge,
            backend,
            limits,
            stop_on_failure=True,
        )
    except OracleError as e:
        raise BuildError(str(e)) from e
    done = [r for r in outcome.results if r.status == "ok"]
    if len(done) == len(max_tests):
        return (
            f"weak tests: the brute ({part.brute.file}) passes every max test within the "
            f"{tl:.0f} ms limit, so the tests do not separate its complexity from the ref's. "
            "Raise the maximum sizes in the constraints or lower limits.time_ms."
        )
    failure = next((r for r in outcome.results if r.status != "ok"), None)
    if failure is not None and failure.status == "tle" and tl > wall * 1000:
        return (
            f"weak tests?: the time limit is {tl / 1000:.0f} s for the brute's language, longer "
            f"than the {plan.weak_check_cap_s:.0f} s this check waits, so a brute-force solution "
            "may pass the max tests. The brute did not finish one in that time. Lower "
            "limits.time_ms if you want slow solutions to fail."
        )
    return None
