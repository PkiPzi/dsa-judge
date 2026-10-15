"""Load the built hidden test suite for a part (PLAN.md §4.1).

Small tests are read from the bank. Large tests are regenerated from their seeds into the
cache and checked against the manifest's input hash; their expected outputs come from the first
ref (cached by input hash) and are checked against the manifest's expected hash. A test whose
hash no longer matches is a judge error, never a silent pass or fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from dsajudge.config import Config
from dsajudge.gen.manifest import (
    Manifest,
    TestEntry,
    expected_sha,
    load_disputes,
    load_manifest,
    sha256_text,
    stored_name,
    stored_paths,
)
from dsajudge.gen.plan import TestSpec
from dsajudge.gen.suite import load_part_gen, spec_hash
from dsajudge.gen.values import GenError
from dsajudge.judge.limits import MemoryBackend
from dsajudge.judge.testset import JudgeTest, OracleError, generated_dir, run_source, write_input
from dsajudge.model.canon import args_to_text
from dsajudge.model.constraints import ConstraintError
from dsajudge.model.schema import Part, Problem


class SuiteError(Exception):
    """The built suite is damaged or stale in a way that needs `dsa rebuild`."""


@dataclass
class HiddenSuite:
    manifest: Manifest
    tests: list[JudgeTest]
    notes: list[str] = field(default_factory=list)


def _entry_spec(e: TestEntry) -> TestSpec:
    return TestSpec(
        id=e.id, group=e.group, index=e.index, size=e.size, flavor=e.flavor, seed=e.seed
    )


def disputed_ids(problem_dir: Path, part: Part) -> dict[str, str]:
    return load_disputes(problem_dir, part.id)


def load_hidden(
    problem_dir: Path, problem: Problem, part: Part, cfg: Config, backend: MemoryBackend
) -> HiddenSuite | None:
    """The part's built tests, or None if `dsa build` has not been run."""
    try:
        manifest = load_manifest(problem_dir, part.id)
    except Exception as first:
        raise SuiteError(str(first)) from first
    if manifest is None:
        return None
    notes: list[str] = []
    if manifest.spec_hash != spec_hash(problem_dir, part, cfg.plan):
        notes.append(
            "the hidden tests are out of date (constraints, generator or test plan changed "
            f"since the last build); run `dsa build {problem.slug}`"
        )
    tests: list[JudgeTest] = []
    regen: list[tuple[TestEntry, int]] = []
    for e in manifest.tests:
        has = e.expected_sha256 is not None
        expected: object = None
        if e.stored:
            in_path, out_path = stored_paths(problem_dir, part.id, e.id)
            try:
                text = in_path.read_text(encoding="utf-8")
                if sha256_text(text) != e.input_sha256:
                    raise SuiteError(f"stored test {e.id} was modified; run `dsa rebuild`")
                if has:
                    expected = json.loads(out_path.read_text(encoding="utf-8"))
                    if expected_sha(expected) != e.expected_sha256:
                        raise SuiteError(f"stored answer of {e.id} was modified; run `dsa rebuild`")
            except OSError as err:
                raise SuiteError(
                    f"stored test {e.id} is missing ({err}); run `dsa rebuild`"
                ) from err
            path = in_path
        else:
            path = generated_dir(problem.slug, part.id) / f"{stored_name(e.id)}.in.json"
            if not path.exists() or sha256_text(path.read_text(encoding="utf-8")) != e.input_sha256:
                regen.append((e, len(tests)))
        tests.append(
            JudgeTest(
                id=e.id,
                group=e.group,
                path=path,
                expected=expected,
                has_expected=has and e.stored,
                unverified=e.unverified,
                sealed=True,
            )
        )
    if regen:
        _regenerate(problem_dir, problem, part, cfg, [e for e, _ in regen])
    _fill_unstored_expected(problem_dir, problem, part, cfg, backend, manifest, tests)
    return HiddenSuite(manifest, tests, notes)


def _regenerate(
    problem_dir: Path, problem: Problem, part: Part, cfg: Config, entries: list[TestEntry]
) -> None:
    try:
        pg = load_part_gen(problem_dir, part, cfg.plan, cfg.judge)
        args_list = pg.generator.generate_many([_entry_spec(e) for e in entries])
    except (GenError, ConstraintError) as err:
        raise SuiteError(f"cannot regenerate large tests: {err}; run `dsa rebuild`") from err
    for e, args in zip(entries, args_list, strict=True):
        text = args_to_text(args)
        if sha256_text(text) != e.input_sha256:
            raise SuiteError(
                f"regenerating {e.id} from its seed gave a different input than at build time "
                "(generator or Python version changed); run `dsa rebuild`"
            )
        write_input(generated_dir(problem.slug, part.id), stored_name(e.id), args)


def _fill_unstored_expected(
    problem_dir: Path,
    problem: Problem,
    part: Part,
    cfg: Config,
    backend: MemoryBackend,
    manifest: Manifest,
    tests: list[JudgeTest],
) -> None:
    """Expected outputs of regenerated tests come from the first ref (cached by input hash)."""
    wanted = [
        (t, e)
        for t, e in zip(tests, manifest.tests, strict=True)
        if not e.stored and e.expected_sha256 is not None
    ]
    if not wanted:
        return
    ref = (
        next((r for r in part.refs if r.file == manifest.refs[0].file), None)
        if manifest.refs
        else None
    )
    if ref is None:
        raise SuiteError(
            "the ref the tests were built from is no longer in problem.yaml; run `dsa rebuild`"
        )
    try:
        runs = run_source(
            problem,
            problem_dir,
            part,
            ref,
            [t for t, _ in wanted],
            cfg.judge,
            backend,
            cfg.judge.ref_timeout_s,
        )
    except OracleError as err:
        raise SuiteError(str(err)) from err
    for t, e in wanted:
        run = runs[t.id]
        if run.status != "ok":
            raise SuiteError(f"ref {ref.file} failed on {t.id} ({run.status}); run `dsa build`")
        if expected_sha(run.output) != e.expected_sha256:
            raise SuiteError(
                f"ref {ref.file} no longer gives the answer recorded for {t.id}; "
                "if you changed it on purpose, run `dsa rebuild`"
            )
        t.expected, t.has_expected = run.output, True
