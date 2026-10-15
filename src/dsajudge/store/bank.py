"""The problem bank under $DSA_HOME (never in git)."""

from __future__ import annotations

import shutil
from pathlib import Path

from dsajudge.config import dsa_home
from dsajudge.model.loader import ProblemError, load_problem


def problems_dir() -> Path:
    return dsa_home() / "problems"


def work_root() -> Path:
    return dsa_home() / "work"


def db_path() -> Path:
    return dsa_home() / "db.sqlite"


def repo_fixtures_dir() -> Path | None:
    """The public sample problems: the repo's fixtures in a source checkout, otherwise the copy
    bundled in the installed wheel (src/dsajudge/store/bank.py -> repo root / package dir)."""
    root = Path(__file__).resolve().parents[3]
    d = root / "fixtures" / "problems"
    if d.is_dir() and (root / "pyproject.toml").exists():
        return d
    bundled = Path(__file__).resolve().parents[1] / "_fixtures" / "problems"
    return bundled if bundled.is_dir() else None


def list_slugs() -> list[str]:
    d = problems_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "problem.yaml").is_file())


def find_problem(slug: str, *, seed_from_fixtures: bool = True) -> tuple[Path, bool]:
    """Path to a bank problem. Returns (path, seeded) where seeded means it was just
    copied from the repo's public fixtures because the bank didn't have it."""
    path = problems_dir() / slug
    if (path / "problem.yaml").is_file():
        return path, False
    fixtures = repo_fixtures_dir() if seed_from_fixtures else None
    if fixtures and (fixtures / slug / "problem.yaml").is_file():
        import_problem(fixtures / slug)
        return path, True
    raise ProblemError(f"no problem {slug!r} in {problems_dir()} (try `dsa list`)")


def import_problem(src: Path, *, force: bool = False) -> str:
    """Copy a problem directory into the bank after validating it. Returns the slug."""
    problem = load_problem(src)
    dest = problems_dir() / problem.slug
    if dest.exists():
        if not force:
            raise ProblemError(f"{dest} already exists (use --force to replace it)")
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return problem.slug
