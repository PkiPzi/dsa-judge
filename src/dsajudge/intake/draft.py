"""The draft problem under review (PLAN.md §7.1 stage 4).

The draft is a normal problem directory. Its ``problem.yaml`` starts with a checklist header
that is regenerated before every editor session, listing what a human must verify: the
constraint numbers, the signature types, the samples, which code is a ref or brute for which
part, and any remaining ``[??]``.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from dsajudge.gen.validator import validate_args, validate_ops
from dsajudge.intake.transcribe import UNREADABLE
from dsajudge.model.canon import dumps
from dsajudge.model.constraints import (
    ConstraintError,
    parse_constraints,
    parse_design_constraints,
)
from dsajudge.model.loader import ProblemError, format_validation_error, read_yaml
from dsajudge.model.schema import Problem

HEADER_START = "=== REVIEW CHECKLIST"
HEADER_END = "=== END CHECKLIST"


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.width = 4096
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _short(obj: Any, limit: int = 90) -> bool:
    return len(json.dumps(obj, default=str)) <= limit


def to_node(obj: Any, *, top: bool = True) -> Any:
    """dict/list -> ruamel nodes; small containers use flow style so samples stay one line."""
    if isinstance(obj, dict):
        m = CommentedMap()
        for k, v in obj.items():
            m[k] = to_node(v, top=False)
        if not top and _short(obj):
            m.fa.set_flow_style()
        return m
    if isinstance(obj, list):
        s = CommentedSeq(to_node(v, top=False) for v in obj)
        if not top and _short(obj):
            s.fa.set_flow_style()
        return s
    return obj


def render_yaml(doc: dict[str, Any]) -> str:
    buf = io.StringIO()
    _yaml().dump(to_node(doc), buf)
    return buf.getvalue()


def strip_header(text: str) -> str:
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "".join(lines[i:])


def with_header(text: str, header: list[str]) -> str:
    block = [f"# {HEADER_START} (edit this file, save, quit) ===\n"]
    block += [f"# {ln}\n" if ln else "#\n" for ln in header]
    block += [f"# {HEADER_END}: the lines above are regenerated each time ===\n", "\n"]
    return "".join(block) + strip_header(text)


def _one_line(text: str, limit: int = 400) -> str:
    """Header lines are YAML comments: a message with line breaks would leave bare lines."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


@dataclass
class Findings:
    checklist: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # block the review from finishing
    notes: list[str] = field(default_factory=list)  # what the assistant could not do

    def header(self) -> list[str]:
        out = [
            "Check each item against the original photo / voice note. The raw files are kept in",
            "../raw/. Nothing is built until this file is valid and has no [??] left.",
            "",
        ]
        if self.errors:
            out.append("MUST FIX before this can be built:")
            out += [f"  ! {_one_line(e)}" for e in self.errors]
            out.append("")
        if self.notes:
            out.append("Assistant notes:")
            out += [f"  * {_one_line(n)}" for n in self.notes]
            out.append("")
        out += self.checklist
        return out


def draft_files(problem_dir: Path, problem: Problem | None, first_new_part: int = 1) -> list[Path]:
    """Files worth opening beside problem.yaml: statements, then refs/briefs, of the new parts."""
    files: list[Path] = []
    if problem is None:
        return files
    for part in problem.parts[first_new_part - 1 :]:
        files.append(problem_dir / part.statement)
        files += [problem_dir / r.file for r in part.refs]
        if part.brute:
            files.append(problem_dir / part.brute.file)
    return [f for f in files if f.is_file()]


def examine(problem_dir: Path, first_new_part: int = 1) -> tuple[Findings, Problem | None]:
    """Validate the draft and describe it for the reviewer."""
    f = Findings()
    path = problem_dir / "problem.yaml"
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        f.errors.append("problem.yaml is empty")
        return f, None
    try:
        data = read_yaml(path)
        problem = Problem.model_validate(data)
    except ProblemError as e:
        f.errors.append(str(e))
        return f, None
    except ValidationError as e:
        f.errors += format_validation_error(path, e).splitlines()[1:]
        return f, None
    except Exception as e:
        f.errors.append(f"problem.yaml: {e}")
        return f, None
    f.checklist.append(f'Problem: {problem.slug}  "{problem.title}"')

    marked: list[str] = []
    for part in problem.parts[first_new_part - 1 :]:
        pid = part.id
        f.checklist.append("")
        f.checklist.append(f"--- part {pid}: {part.title} ---")
        validate: Callable[[list[Any]], list[str]] | None = None
        if part.mode == "ops":
            d = part.dsg
            ctor = ", ".join(f"{p.name}: {p.type}" for p in d.constructor)
            f.checklist.append(
                f"[ ] design: {d.class_name}({ctor}); methods "
                + ", ".join(
                    f"{m.name}({', '.join(f'{p.name}: {p.type}' for p in m.params)}) -> {m.returns}"
                    for m in d.methods
                )
            )
            try:
                ops_specs = parse_design_constraints(part)
                f.checklist.append(f"[ ] calls: {_fmt_bound(ops_specs.calls)}")
                for s in ops_specs.ctor:
                    f.checklist.append(f"[ ] constructor {s.name}: {_bounds(s)}")
                for name, ps in ops_specs.methods.items():
                    for s in ps:
                        f.checklist.append(f"[ ] {name}.{s.name}: {_bounds(s)}")
                validate = lambda a, sp=ops_specs: validate_ops(sp, a)  # noqa: E731
            except ConstraintError as e:
                f.errors.append(f"part {pid}: {e}")
        else:
            sig = part.sig
            f.checklist.append(
                f"[ ] signature: {sig.method}"
                f"({', '.join(f'{p.name}: {p.type}' for p in sig.params)}) -> {sig.returns}"
            )
            specs = None
            try:
                specs = parse_constraints(part)
            except ConstraintError as e:
                f.errors.append(f"part {pid}: {e}")
            if specs is not None:
                for s in specs:
                    f.checklist.append(f"[ ] constraint {s.name}: {_bounds(s)}")
                validate = lambda a, sp=specs: validate_args(sp, a)  # noqa: E731
        for i, smp in enumerate(part.samples, 1):
            f.checklist.append(
                f"[ ] sample {i}: {dumps(smp.args)[:70]} -> {dumps(smp.expected)[:40]}"
            )
            if validate is not None:
                errs = validate(smp.args)
                if errs:
                    f.errors.append(f"part {pid} sample {i} breaks the constraints: {errs[0]}")
        if not part.samples:
            f.errors.append(f"part {pid} has no samples to check against")
        for r in part.refs:
            f.checklist.append(f"[ ] ref for part {pid}: {r.file} ({r.lang}, {r.origin})")
        if part.brute:
            b = part.brute
            f.checklist.append(f"[ ] brute for part {pid}: {b.file} ({b.lang}, {b.origin})")
        if not part.refs and not part.brute:
            f.errors.append(f"part {pid} has neither a ref nor a brute")
        for rel in [part.statement, *[r.file for r in part.refs]] + (
            [part.brute.file] if part.brute else []
        ):
            fp = problem_dir / rel
            if fp.is_file():
                n = fp.read_text(encoding="utf-8").count(UNREADABLE)
                if n:
                    marked.append(f"{rel}: {n}")
    text = path.read_text(encoding="utf-8")
    n = strip_header(text).count(UNREADABLE)
    if n:
        marked.append(f"problem.yaml: {n}")
    if marked:
        f.errors.append(f"unreadable spots ({UNREADABLE}) left in: " + ", ".join(marked))
    return f, problem


def _bounds(s: Any) -> str:
    parts: list[str] = []
    for key in ("range", "length", "cols", "each", "each_len"):
        b = getattr(s, key, None)
        if b is not None:
            label = {"length": "len/rows"}.get(key, key)
            parts.append(f"{label} {_fmt_bound(b)}")
    if s.graph is not None:
        parts.append(f"graph n {_fmt_bound(s.graph.n)} kind {s.graph.kind}")
    if s.charset:
        parts.append(f"charset {s.charset[:12]}{'...' if len(s.charset) > 12 else ''}")
    if s.distinct:
        parts.append("distinct")
    if s.sorted:
        parts.append(f"sorted {s.sorted}")
    if s.shape != "any":
        parts.append(f"shape {s.shape}")
    return ", ".join(parts) or "(any)"


def _fmt_bound(b: Any) -> str:
    try:
        if not b.names():
            lo, hi = b.eval_int({})
            return f"[{lo}, {hi}]"
    except Exception:
        pass
    return str(b)


def refresh_header(
    problem_dir: Path, first_new_part: int = 1, notes: list[str] | None = None
) -> Findings:
    findings, _ = examine(problem_dir, first_new_part)
    findings.notes = list(notes or [])
    path = problem_dir / "problem.yaml"
    if path.exists():
        path.write_text(
            with_header(path.read_text(encoding="utf-8"), findings.header()), encoding="utf-8"
        )
    return findings


_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    slug = _SLUG.sub("-", title.lower()).strip("-")
    return slug[:48].strip("-") or "problem"


def _load_rt(problem_dir: Path) -> Any:
    return _yaml().load((problem_dir / "problem.yaml").read_text(encoding="utf-8"))


def _save_rt(problem_dir: Path, data: Any) -> None:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    (problem_dir / "problem.yaml").write_text(buf.getvalue(), encoding="utf-8")


def append_parts(problem_dir: Path, parts: list[dict[str, Any]]) -> None:
    """Append parts to problem.yaml, keeping the user's comments and layout."""
    data = _load_rt(problem_dir)
    for part in parts:
        data["parts"].append(to_node(part))
    _save_rt(problem_dir, data)


def update_part(problem_dir: Path, part_id: int, **fields: Any) -> None:
    data = _load_rt(problem_dir)
    part = data["parts"][part_id - 1]
    for key, value in fields.items():
        part[key] = to_node(value, top=False)
    _save_rt(problem_dir, data)
