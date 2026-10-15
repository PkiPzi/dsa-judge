"""Work directories: where the user writes code ($DSA_HOME/work/<slug>/part<N>/)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from dsajudge.lang import get_language
from dsajudge.model.schema import Lang, Problem
from dsajudge.store.bank import work_root

MARKER = ".dsa.json"


@dataclass
class WorkInfo:
    dir: Path
    slug: str
    part: int
    lang: Lang
    bank: Path

    def sol_path(self, lang: Lang | None = None) -> Path:
        return self.dir / get_language(lang or self.lang).sol_filename


def part_dir(slug: str, part: int) -> Path:
    return work_root() / slug / f"part{part}"


def find_workdir(start: Path) -> WorkInfo | None:
    for d in [start, *start.parents]:
        marker = d / MARKER
        if marker.is_file():
            data = json.loads(marker.read_text(encoding="utf-8"))
            return WorkInfo(
                dir=d,
                slug=str(data["slug"]),
                part=int(data["part"]),
                lang=data["lang"],
                bank=Path(data["bank"]),
            )
    return None


def tasks_json(dsa_command: str) -> str:
    def task(label: str, args: str, group: object) -> dict[str, object]:
        return {
            "label": label,
            "type": "shell",
            "command": f"{dsa_command} {args}",
            "options": {"cwd": "${workspaceFolder}"},
            "group": group,
            "problemMatcher": ["$gcc"],
            "presentation": {"reveal": "always", "panel": "dedicated", "clear": True},
        }

    data = {
        "version": "2.0.0",
        "tasks": [
            task("dsa: run", "run", {"kind": "build", "isDefault": True}),
            task("dsa: submit", "submit", {"kind": "test", "isDefault": True}),
            task("dsa: debug", "debug", "none"),
            task("dsa: watch", "watch", "none"),
        ],
    }
    return json.dumps(data, indent=2) + "\n"


def open_part(
    problem_dir: Path, problem: Problem, part_id: int, lang: Lang, *, dsa_command: str = "dsa"
) -> WorkInfo:
    """Create or refresh the work dir for a part. Never overwrites the user's solution."""
    part = problem.part(part_id)
    language = get_language(lang)
    wd = part_dir(problem.slug, part_id)
    wd.mkdir(parents=True, exist_ok=True)

    sol = wd / language.sol_filename
    if not sol.exists():
        stub = (
            language.stdio_stub(problem.slug, part)
            if part.mode == "stdio"
            else language.stub(problem.slug, part)
        )
        sol.write_text(stub, encoding="utf-8")

    shutil.copyfile(problem_dir / part.statement, wd / "statement.md")

    if part_id > 1:
        prev_src = part_dir(problem.slug, part_id - 1) / language.sol_filename
        if prev_src.exists():
            shutil.copyfile(prev_src, wd / f"prev.{language.ext}")

    info = WorkInfo(dir=wd, slug=problem.slug, part=part_id, lang=lang, bank=problem_dir)
    (wd / MARKER).write_text(
        json.dumps(
            {"slug": problem.slug, "part": part_id, "lang": lang, "bank": str(problem_dir)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    vscode = wd / ".vscode"
    vscode.mkdir(exist_ok=True)
    (vscode / "tasks.json").write_text(tasks_json(dsa_command), encoding="utf-8")
    return info
