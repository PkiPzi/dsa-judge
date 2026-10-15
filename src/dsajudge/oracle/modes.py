"""Oracle modes beyond `ref` (PLAN.md §6.5): `promote` and `dispute` for brute-only parts."""

from __future__ import annotations

import shutil
from pathlib import Path

from dsajudge.config import Config
from dsajudge.gen.manifest import load_disputes, load_manifest, save_disputes
from dsajudge.judge.limits import MemoryBackend
from dsajudge.judge.session import code_sha
from dsajudge.lang import get_language
from dsajudge.model.loader import append_ref, load_problem
from dsajudge.model.schema import Lang, Part, Problem
from dsajudge.oracle.build import BuildError, BuildReport, build_part
from dsajudge.store.db import DB


class ModeError(Exception):
    """promote/dispute cannot apply; the message says why."""


def _is_brute_only(problem_dir: Path, part: Part) -> bool:
    manifest = load_manifest(problem_dir, part.id)
    if manifest is not None:
        return manifest.oracle == "brute-only"
    return part.derived_oracle() == "brute-only"


def dispute(
    problem_dir: Path,
    part: Part,
    test_id: str,
    note: str = "",
    *,
    clear: bool = False,
) -> str:
    """Mark the brute oracle as suspect for one test. Returns a one-line summary."""
    if not _is_brute_only(problem_dir, part):
        raise ModeError(
            f"part {part.id} is not brute-only: with a ref, the ref is the authority "
            "(fix the ref, or run `dsa build` to compare it against the brute)"
        )
    disputes = load_disputes(problem_dir, part.id)
    if clear:
        if test_id not in disputes:
            raise ModeError(f"{test_id} is not disputed")
        del disputes[test_id]
        save_disputes(problem_dir, part.id, disputes)
        return f"{test_id}: dispute cleared"
    if test_id.startswith("sample/"):
        raise ModeError(
            "samples come from the statement, not from the brute: fix problem.yaml instead"
        )
    manifest = load_manifest(problem_dir, part.id)
    known = {t.id for t in manifest.tests} if manifest else set()
    manual = problem_dir / f"part{part.id}" / "tests" / "manual"
    if manual.is_dir():
        known |= {f"manual/{f.name.removesuffix('.in.json')}" for f in manual.glob("*.in.json")}
    if test_id not in known:
        raise ModeError(f"no hidden or manual test {test_id!r} in part {part.id}")
    disputes[test_id] = note or "oracle suspected wrong"
    save_disputes(problem_dir, part.id, disputes)
    return (
        f"{test_id}: oracle marked suspect; the test is run-only now and part {part.id} "
        "needs review (fix or replace the brute, then `dsa rebuild`)"
    )


def promote(
    problem_dir: Path,
    problem: Problem,
    part: Part,
    src: Path,
    lang: Lang,
    cfg: Config,
    backend: MemoryBackend,
    db: DB,
) -> BuildReport:
    """Turn the user's AC solution of a brute-only part into the ref, then rebuild."""
    if part.refs:
        raise ModeError(
            f"part {part.id} already has a ref; promote only applies to brute-only parts"
        )
    if not part.brute:
        raise ModeError(f"part {part.id} has no brute to cross-check the promoted solution against")
    if not src.is_file():
        raise ModeError(f"{src} not found")
    if not db.has_accepted(problem.slug, part.id, code_sha(src)):
        raise ModeError(
            f"{src.name} has not been accepted by `dsa submit` in its current form: "
            "submit it (and get AC) first, then promote"
        )
    ext = get_language(lang).ext
    rel = f"part{part.id}/ref/promoted.{ext}"
    dest = problem_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup = dest.read_bytes() if dest.exists() else None
    shutil.copyfile(src, dest)
    original = append_ref(
        problem_dir, part.id, {"lang": lang, "file": rel, "origin": "promoted", "verified": True}
    )
    try:
        fresh = load_problem(problem_dir)
        return build_part(problem_dir, fresh, fresh.part(part.id), cfg, backend)
    except BuildError as e:
        (problem_dir / "problem.yaml").write_text(original, encoding="utf-8")
        if backup is None:
            dest.unlink(missing_ok=True)
        else:
            dest.write_bytes(backup)
        raise ModeError(
            f"your accepted solution does not hold up as a ref:\n{e}\n"
            "Nothing was changed. If you think the brute is wrong, use `dsa dispute TEST_ID`."
        ) from e
