"""Load and check problem.yaml files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from dsajudge.model.schema import Problem


class ProblemError(Exception):
    pass


def _yaml() -> YAML:
    y = YAML(typ="safe", pure=True)
    return y


def read_yaml(path: Path) -> Any:
    try:
        return _yaml().load(path.read_text(encoding="utf-8"))
    except YAMLError as e:
        raise ProblemError(f"{path}: invalid YAML: {e}") from e


def format_validation_error(path: Path, e: ValidationError) -> str:
    lines = [f"{path}: invalid problem file"]
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        lines.append(f"  {loc or '<root>'}: {msg}")
    return "\n".join(lines)


def load_problem(problem_dir: Path, *, check_files: bool = True) -> Problem:
    path = problem_dir / "problem.yaml"
    if not path.exists():
        raise ProblemError(f"{path} not found")
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise ProblemError(f"{path}: expected a mapping at the top level")
    try:
        problem = Problem.model_validate(data)
    except ValidationError as e:
        raise ProblemError(format_validation_error(path, e)) from e
    if check_files:
        missing: list[str] = []
        for part in problem.parts:
            files = [part.statement] + [r.file for r in part.refs]
            if part.brute:
                files.append(part.brute.file)
            missing += [f for f in files if not (problem_dir / f).is_file()]
        if missing:
            raise ProblemError(f"{path}: missing files: {', '.join(missing)}")
    return problem


def constraint_line(problem_dir: Path, part_id: int, param: str) -> int | None:
    """1-based line of ``parts[part_id].constraints.<param>`` in problem.yaml, if findable."""
    try:
        data = YAML(typ="rt").load((problem_dir / "problem.yaml").read_text(encoding="utf-8"))
        constraints = data["parts"][part_id - 1]["constraints"]
        return int(constraints.lc.key(param)[0]) + 1
    except Exception:
        return None


def append_ref(problem_dir: Path, part_id: int, entry: dict[str, Any]) -> str:
    """Add a ref to ``parts[part_id].refs`` keeping the file's comments. Returns the old text."""
    from ruamel.yaml.comments import CommentedMap

    path = problem_dir / "problem.yaml"
    original = path.read_text(encoding="utf-8")
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096
    data = y.load(original)
    part = data["parts"][part_id - 1]
    node = CommentedMap(entry)
    node.fa.set_flow_style()
    refs = part.get("refs")
    if refs is None:
        part["refs"] = [node]
    else:
        refs.append(node)
    with path.open("w", encoding="utf-8") as fh:
        y.dump(data, fh)
    return original
