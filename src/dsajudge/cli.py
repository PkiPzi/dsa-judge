"""The `dsa` command line."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from dsajudge import __version__
from dsajudge.ai import factory as ai_factory
from dsajudge.ai.base import AIError
from dsajudge.config import Config, ConfigError, load_config
from dsajudge.doctor import run_checks
from dsajudge.gen.manifest import load_disputes, load_manifest, part_tests_dir
from dsajudge.intake import pipeline
from dsajudge.intake.review import run_editor
from dsajudge.intake.sources import IntakeError, read_clipboard
from dsajudge.judge.limits import detect_backend, raise_stack_limit
from dsajudge.judge.session import (
    JudgeError,
    Target,
    debug_test,
    judge,
    make_target,
    parse_custom_case,
    record,
)
from dsajudge.judge.verdicts import Verdict, render_report
from dsajudge.lang import get_language
from dsajudge.model.canon import dumps
from dsajudge.model.loader import ProblemError, load_problem
from dsajudge.model.schema import LANGS, Lang, Problem
from dsajudge.oracle.build import BuildError, BuildReport, build_part
from dsajudge.oracle.modes import ModeError
from dsajudge.oracle.modes import dispute as dispute_test
from dsajudge.oracle.modes import promote as promote_part
from dsajudge.store.bank import db_path, find_problem, import_problem, list_slugs
from dsajudge.store.db import DB
from dsajudge.store.workdir import WorkInfo, find_workdir, open_part

app = typer.Typer(
    help="Local-first DSA practice judge. Problems live in $DSA_HOME (default ~/dsa-bank).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
console = Console(highlight=False)

PRIVACY_NOTE = (
    "Privacy: AI editor extensions (Copilot and similar) upload open files. Practice in nvim "
    'or a VS Code profile without AI extensions (DSA_EDITOR="code --profile Practice").'
)

LangOpt = Annotated[
    str | None, typer.Option("--lang", "-l", help="cpp, java or python (default: the work dir's)")
]


def fail(msg: str, code: int = 2) -> NoReturn:
    console.print(f"[red]error:[/] {escape(msg)}")
    raise typer.Exit(code)


def _config() -> Config:
    try:
        return load_config()
    except ConfigError as e:
        fail(str(e))


def _lang(value: str | None, default: Lang) -> Lang:
    if value is None:
        return default
    if value not in LANGS:
        fail(f"unknown language {value!r}; use cpp, java or python")
    return value


def _load(slug: str) -> tuple[Path, Problem]:
    try:
        path, seeded = find_problem(slug)
        if seeded:
            console.print(f"[dim]copied public fixture {slug!r} into {path}[/]")
        return path, load_problem(path)
    except ProblemError as e:
        fail(str(e))


def _here() -> WorkInfo:
    info = find_workdir(Path.cwd())
    if info is None:
        fail("not inside a work directory; run `dsa open SLUG` and cd into the path it prints")
    return info


def _target(info: WorkInfo, lang: Lang) -> Target:
    try:
        return make_target(info.bank, info.part, lang, info.sol_path(lang))
    except (JudgeError, ProblemError) as e:
        fail(str(e))


@app.callback(invoke_without_command=True)
def _root(
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
) -> None:
    if version:
        console.print(f"dsa-judge {__version__}")
        raise typer.Exit()


# ------------------------------------------------------------------ maintenance


@app.command()
def doctor(
    models: Annotated[
        bool,
        typer.Option("--models", help="Also run each configured model on fixture inputs"),
    ] = False,
) -> None:
    """Check toolchains, Ollama/whisper reachability and resource-limit support."""
    cfg = _config()
    table = Table(show_header=False, box=None, pad_edge=False)
    for c in run_checks(cfg):
        mark = "[green]ok[/]  " if c.status == "ok" else "[yellow]warn[/]"
        detail = escape(c.detail) + (f"  [dim]→ {escape(c.hint)}[/]" if c.hint else "")
        table.add_row(mark, c.name, detail)
    console.print(table)
    console.print("[dim]warnings are fine until you need that feature[/]")
    if models:
        _bench_models(cfg)


def _bench_models(cfg: Config) -> None:
    from dsajudge.ai.bench import run_bench

    console.print("\n[bold]models[/] [dim](3 runs each; the first call also loads the model)[/]")
    try:
        rows = run_bench(cfg, ai_factory.PROVIDERS)
    except AIError as e:
        fail(str(e))
    table = Table("check", "model", "usable", "avg time", "note")
    for r in rows:
        rate = f"{r.ok}/{r.runs}" if r.runs else "-"
        style = "green" if r.runs and r.ok == r.runs else ("yellow" if r.ok else "red")
        table.add_row(
            r.role,
            escape(r.model),
            f"[{style}]{rate}[/]",
            f"{r.latency:.1f} s" if r.seconds else "-",
            escape(r.note),
        )
    console.print(table)
    console.print(
        "[dim]usable = schema-valid and sane. Below 3/3 on text checks, try a larger "
        "models.text (a 7-8B model partly on CPU is fine for one-time intake).[/]"
    )


@app.command("import")
def import_cmd(
    path: Annotated[Path, typer.Argument(help="A problem directory containing problem.yaml")],
    force: Annotated[bool, typer.Option("--force", help="Replace an existing problem")] = False,
) -> None:
    """Copy a problem directory into the bank."""
    try:
        slug = import_problem(path, force=force)
    except ProblemError as e:
        fail(str(e))
    console.print(f"imported [bold]{slug}[/]; start with `dsa open {slug}`")


def _print_build(report: BuildReport) -> None:
    counts = " · ".join(f"{g} {n}" for g, n in report.counts.items())
    console.print(
        f"[green]built[/] part {report.part} [dim]({report.seconds:.0f} s)[/]: "
        f"oracle [bold]{report.oracle}[/] · {counts} · {report.stored} stored"
    )
    console.print(
        f"  time limit {report.tl_base_ms:.0f} ms [dim]({escape(report.tl_source)})[/]"
        + (
            f" · stress {report.stress_cases} inputs, ref and brute agree"
            if report.stress_cases
            else ""
        )
    )
    for note in report.notes:
        console.print(f"  [dim]{escape(note)}[/]")
    for w in report.warnings:
        console.print(f"  [yellow]warning:[/] {escape(w)}")


def _progress(pid: int) -> Callable[[str], None]:
    def say(msg: str) -> None:
        console.print(f"[dim]part {pid}: {escape(msg)}[/]")

    return say


def _build_parts(slug: str, part: int | None) -> None:
    cfg = _config()
    path, problem = _load(slug)
    ids = [part] if part else [p.id for p in problem.parts]
    for pid in ids:
        if not 1 <= pid <= len(problem.parts):
            fail(f"{slug} has parts 1..{len(problem.parts)}")
    raise_stack_limit()
    backend = detect_backend(cfg.judge.memory_backend)
    failed = False
    for pid in ids:
        pt = problem.part(pid)
        try:
            report = build_part(path, problem, pt, cfg, backend, progress=_progress(pid))
        except BuildError as e:
            failed = True
            console.print(f"[red]build failed[/] part {pid}:")
            console.print(escape(str(e)))
            continue
        _print_build(report)
    if failed:
        raise typer.Exit(1)


@app.command()
def build(
    slug: Annotated[str, typer.Argument(help="Problem slug")],
    part: Annotated[int | None, typer.Option("--part", "-p", help="Only this part")] = None,
) -> None:
    """(Re)generate the hidden tests, check the refs and oracles, calibrate the time limit."""
    _build_parts(slug, part)


@app.command()
def rebuild(
    slug: Annotated[str, typer.Argument(help="Problem slug")],
    part: Annotated[int | None, typer.Option("--part", "-p", help="Only this part")] = None,
) -> None:
    """Wipe the generated tests and caches for a problem and build again from the seeds."""
    import shutil

    from dsajudge.config import cache_dir

    path, problem = _load(slug)
    ids = [part] if part else [p.id for p in problem.parts]
    for pid in ids:
        if not 1 <= pid <= len(problem.parts):
            fail(f"{slug} has parts 1..{len(problem.parts)}")
        tdir = part_tests_dir(path, pid)
        shutil.rmtree(tdir / "small", ignore_errors=True)
        (tdir / "manifest.json").unlink(missing_ok=True)
        shutil.rmtree(cache_dir() / "tests" / slug / f"part{pid}", ignore_errors=True)
        shutil.rmtree(cache_dir() / "expected" / slug / f"part{pid}", ignore_errors=True)
    _build_parts(slug, part)


@app.command("dispute")
def dispute_cmd(
    test_id: Annotated[str, typer.Argument(help="Test id, e.g. small_random/07")],
    note: Annotated[str, typer.Option("--note", help="Why you think the oracle is wrong")] = "",
    clear: Annotated[bool, typer.Option("--clear", help="Withdraw an earlier dispute")] = False,
) -> None:
    """Brute-only parts: mark the oracle as suspect for one test."""
    info = _here()
    _, problem = _load(info.slug)
    try:
        msg = dispute_test(info.bank, problem.part(info.part), test_id, note, clear=clear)
    except ModeError as e:
        fail(str(e))
    console.print(escape(msg))


@app.command()
def promote(
    part: Annotated[
        int | None, typer.Option("--part", "-p", help="Default: this work dir's")
    ] = None,
    lang: LangOpt = None,
) -> None:
    """Brute-only parts: make your accepted solution the ref, then rebuild with full verdicts."""
    from dsajudge.store.workdir import part_dir

    cfg = _config()
    info = _here()
    chosen = _lang(lang, info.lang)
    pid = part or info.part
    path, problem = _load(info.slug)
    if not 1 <= pid <= len(problem.parts):
        fail(f"{info.slug} has parts 1..{len(problem.parts)}")
    src = part_dir(info.slug, pid) / get_language(chosen).sol_filename
    raise_stack_limit()
    backend = detect_backend(cfg.judge.memory_backend)
    try:
        with DB(db_path()) as db:
            report = promote_part(path, problem, problem.part(pid), src, chosen, cfg, backend, db)
    except ModeError as e:
        fail(str(e))
    console.print(f"[green]promoted[/] {src.name} to the ref of part {pid}")
    _print_build(report)


# ------------------------------------------------------------------ intake


def _intake_env(cfg: Config) -> pipeline.Env:
    raise_stack_limit()
    return pipeline.Env(
        cfg=cfg,
        providers=ai_factory.PROVIDERS,
        backend=detect_backend(cfg.judge.memory_backend),
        say=lambda m: console.print(f"[dim]{escape(m)}[/]"),
        edit=run_editor,
        confirm=lambda q: typer.confirm(q, default=False),
    )


def _gather_texts(clip: bool, typed: bool) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    if clip:
        text = read_clipboard()
        if not text:
            fail("the clipboard is empty (needs wl-paste or xclip)")
        texts.append(("clipboard.txt", text))
    if typed:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="dsa-text-") as tmp:
            path = Path(tmp) / "pasted.md"
            path.write_text("", encoding="utf-8")
            if run_editor([path]) != 0:
                fail("the editor exited with an error")
            content = path.read_text(encoding="utf-8")
        if not content.strip():
            fail("nothing was typed")
        texts.append(("typed.md", content))
    return texts


def _report_outcome(out: pipeline.Outcome) -> None:
    console.print(
        f"[green]added[/] [bold]{out.slug}[/]: {out.parts} part(s) built "
        f"(parts {out.first_part}..{out.first_part + out.parts - 1})"
    )
    for r in out.reports:
        _print_build(r)
    console.print(f"next: dsa open {out.slug}")


def _intake_call(fn: Callable[[], object]) -> object:
    try:
        return fn()
    except (IntakeError, AIError) as e:
        fail(str(e), 1)


@app.command()
def add(
    files: Annotated[
        list[Path] | None,
        typer.Argument(help="Photos, voice notes, .txt/.md, or code (.cpp .cc .java .py)"),
    ] = None,
    slug: Annotated[str | None, typer.Option("--slug", help="Name for the problem")] = None,
    clip: Annotated[bool, typer.Option("--clip", help="Use the clipboard text")] = False,
    text: Annotated[bool, typer.Option("--text", help="Open $EDITOR to paste text")] = False,
    resume: Annotated[
        str | None, typer.Option("--resume", help="Continue an interrupted add (its inbox name)")
    ] = None,
) -> None:
    """Add a question from photos, voice notes, text and code (local AI, then a review)."""
    cfg = _config()
    env = _intake_env(cfg)
    texts = [] if resume else _gather_texts(clip, text)
    out = _intake_call(
        lambda: pipeline.add(env, slug=slug, files=list(files or []), texts=texts, resume=resume)
    )
    assert isinstance(out, pipeline.Outcome)
    _report_outcome(out)


@app.command()
def followup(
    slug: Annotated[str, typer.Argument(help="Problem to extend")],
    files: Annotated[
        list[Path] | None, typer.Argument(help="Photos, voice notes, text, code")
    ] = None,
    clip: Annotated[bool, typer.Option("--clip", help="Use the clipboard text")] = False,
    text: Annotated[bool, typer.Option("--text", help="Open $EDITOR to paste text")] = False,
    resume: Annotated[
        bool, typer.Option("--resume", help="Continue an interrupted follow-up")
    ] = False,
) -> None:
    """Append part N+1 to a problem, read against the previous part's signature and constraints."""
    cfg = _config()
    env = _intake_env(cfg)
    texts = [] if resume else _gather_texts(clip, text)
    out = _intake_call(
        lambda: pipeline.followup(env, slug, files=list(files or []), texts=texts, resume=resume)
    )
    assert isinstance(out, pipeline.Outcome)
    _report_outcome(out)


@app.command("ref")
def ref_cmd(
    slug: Annotated[str, typer.Argument(help="Problem slug")],
    file: Annotated[Path, typer.Argument(help="A code file or a photo of code")],
    part: Annotated[int, typer.Option("--part", "-p", help="Part number")] = 1,
    lang: LangOpt = None,
    origin: Annotated[
        str, typer.Option("--origin", help="user, friend or promoted (default friend)")
    ] = "friend",
) -> None:
    """Attach a reference solution to a part; it must agree with the existing oracle."""
    cfg = _config()
    env = _intake_env(cfg)
    if origin not in ("user", "friend"):
        fail("--origin must be user or friend")
    report = _intake_call(
        lambda: pipeline.attach_ref(env, slug, part, file, lang=lang, origin=origin)
    )
    assert isinstance(report, BuildReport)
    console.print(f"[green]attached[/] the ref to part {part} of {slug}")
    _print_build(report)


# ------------------------------------------------------------------ browsing


@app.command("list")
def list_cmd(
    tag: Annotated[str | None, typer.Option(help="Only problems with this tag")] = None,
    unsolved: Annotated[bool, typer.Option(help="Only problems with unsolved parts")] = False,
    part_status: Annotated[bool, typer.Option(help="Show every part's status")] = False,
) -> None:
    """List problems in the bank."""
    slugs = list_slugs()
    if not slugs:
        console.print(
            "the bank is empty; `dsa import DIR` (or `dsa open max-subarray` in a checkout)"
        )
        return
    table = Table("slug", "title", "tags", "progress")
    with DB(db_path()) as db:
        for slug in slugs:
            try:
                p = load_problem(Path(find_problem(slug, seed_from_fixtures=False)[0]))
            except ProblemError as e:
                table.add_row(slug, f"[red]{escape(str(e).splitlines()[0])}[/]", "", "")
                continue
            if tag and tag not in p.tags:
                continue
            solved = db.solved_parts(slug)
            if unsolved and len(solved) >= len(p.parts):
                continue
            if part_status:
                unlocked = db.unlocked_part(slug, len(p.parts))
                progress = " ".join(
                    f"[green]{i}✓[/]"
                    if i in solved
                    else (f"{i}…" if i == unlocked else f"[dim]{i}🔒[/]")
                    for i in range(1, len(p.parts) + 1)
                )
            else:
                progress = f"{len(solved & set(range(1, len(p.parts) + 1)))}/{len(p.parts)}"
            table.add_row(slug, escape(p.title), ", ".join(p.tags), progress)
    console.print(table)


@app.command()
def show(
    slug: Annotated[str | None, typer.Argument(help="Default: the current work dir's")] = None,
) -> None:
    """Parts, oracle mode, status and best times."""
    if slug is None:
        slug = _here().slug
    _, problem = _load(slug)
    with DB(db_path()) as db:
        solved = db.solved_parts(slug)
        unlocked = db.unlocked_part(slug, len(problem.parts))
        best = db.best_times(slug)
    console.print(f"[bold]{escape(problem.title)}[/]  [dim]{slug} · {', '.join(problem.tags)}[/]")
    table = Table("part", "title", "status", "oracle", "refs", "tests", "TL", "best")
    path, _ = find_problem(slug)
    for part in problem.parts:
        if part.id in solved:
            status = "[green]AC[/]"
        elif part.id == unlocked:
            status = "open"
        else:
            status = "[dim]locked[/]"
        if load_disputes(path, part.id):
            status += " [yellow]needs-review[/]"
        refs = ", ".join(r.lang for r in part.refs) or "-"
        if part.brute:
            refs += f" (+brute {part.brute.lang})"
        b = best.get(part.id)
        try:
            manifest = load_manifest(path, part.id)
        except Exception:
            manifest = None
        if manifest:
            tests = f"{len(manifest.tests)} hidden"
            oracle: str = manifest.oracle
            tl = f"{manifest.tl_base_ms:.0f} ms"
        else:
            tests = "[dim]not built[/]"
            oracle = part.derived_oracle() or "-"
            tl = str(part.limits.time_ms)
        table.add_row(
            str(part.id),
            escape(part.title),
            status,
            oracle,
            refs,
            tests,
            tl,
            f"{b[1]:.0f} ms ({b[0]})" if b else "-",
        )
    console.print(table)


# ------------------------------------------------------------------ practice


def _launch_editor(info: WorkInfo, sol: Path) -> None:
    editor = os.environ.get("DSA_EDITOR") or os.environ.get("EDITOR")
    if not editor:
        return
    argv = shlex.split(editor)
    base = Path(argv[0]).name
    args = [str(info.dir), str(sol)] if base in ("code", "codium", "code-oss") else [str(sol)]
    try:
        subprocess.run([*argv, *args], cwd=info.dir, check=False)
    except FileNotFoundError:
        console.print(f"[yellow]editor {argv[0]!r} not found[/]")


@app.command("open")
def open_cmd(
    slug: Annotated[str | None, typer.Argument(help="Problem slug")] = None,
    next_: Annotated[bool, typer.Option("--next", help="Open the first unsolved problem")] = False,
    lang: LangOpt = None,
    part: Annotated[int | None, typer.Option("--part", "-p", help="Part number")] = None,
    force: Annotated[bool, typer.Option("--force", help="Peek at a locked part")] = False,
    no_editor: Annotated[
        bool, typer.Option("--no-editor", help="Only create the work dir")
    ] = False,
) -> None:
    """Open the current unlocked part of a problem in your editor."""
    cfg = _config()
    if next_ and slug is None:
        with DB(db_path()) as db:
            for s in list_slugs():
                try:
                    p = load_problem(find_problem(s, seed_from_fixtures=False)[0])
                except ProblemError:
                    continue
                if len(db.solved_parts(s)) < len(p.parts):
                    slug = s
                    break
        if slug is None:
            fail("every problem in the bank is solved")
    if slug is None:
        fail("give a SLUG or --next")
    path, problem = _load(slug)
    chosen = _lang(lang, cfg.default_lang)
    with DB(db_path()) as db:
        unlocked = db.unlocked_part(slug, len(problem.parts))
        part_id = part or unlocked
        if not 1 <= part_id <= len(problem.parts):
            fail(f"{slug} has parts 1..{len(problem.parts)}")
        if part_id > unlocked and not force:
            fail(f"part {part_id} is locked: get AC on part {unlocked} first (or --force to peek)")
        info = open_part(path, problem, part_id, chosen, dsa_command=cfg.dsa_command)
        db.start_session(slug, part_id, chosen)
        first = db.get_meta("privacy_note_shown") is None
        if first:
            db.set_meta("privacy_note_shown", "1")
    sol = info.sol_path()
    pt = problem.part(part_id)
    console.print(f"[bold]{escape(problem.title)}[/] · part {part_id}: {escape(pt.title)}")
    console.print(f"  cd {info.dir}")
    console.print(f"  edit {sol.name}, then `dsa run` / `dsa submit`")
    if part_id > unlocked:
        console.print(
            "[yellow]peeking at a locked part: submits here won't unlock anything early[/]"
        )
    if first:
        console.print(f"[dim]{escape(PRIVACY_NOTE)}[/]")
    if not no_editor:
        _launch_editor(info, sol)


@app.command()
def run(
    case: Annotated[
        str | None,
        typer.Option("--case", help="Custom input: one JSON value per line (or \\n-separated)"),
    ] = None,
    case_file: Annotated[
        Path | None, typer.Option("--case-file", help="File with a custom input")
    ] = None,
    lang: LangOpt = None,
) -> None:
    """Run the samples (and an optional custom case)."""
    cfg = _config()
    info = _here()
    target = _target(info, _lang(lang, info.lang))
    custom = None
    if case is not None or case_file is not None:
        text = case if case is not None else Path(case_file or "").read_text(encoding="utf-8")
        try:
            custom = parse_custom_case(target.part, text)
        except JudgeError as e:
            fail(str(e))
    try:
        report = judge(target, cfg, mode="run", custom=custom, all_tests=True)
    except JudgeError as e:
        fail(str(e))
    record(target, report, kind="run")
    render_report(console, report, label=f"run · {target.lang}")
    if custom is not None:
        res = next((r for r in report.results if r.id == "custom"), None)
        if res is not None and res.has_got:
            console.print(
                f"custom case output: {escape(dumps(res.got))}"
                + ("" if res.has_expected else "  [dim](no oracle)[/]")
            )
    raise typer.Exit(0 if report.verdict == Verdict.AC else 1)


@app.command()
def submit(
    strict: Annotated[
        bool, typer.Option("--strict", help="Interview/OA mode: verdict and pass count only")
    ] = False,
    all_: Annotated[
        bool, typer.Option("--all", help="Run every test instead of stopping at the first failure")
    ] = False,
    lang: LangOpt = None,
) -> None:
    """Judge against the full hidden suite."""
    cfg = _config()
    info = _here()
    target = _target(info, _lang(lang, info.lang))
    try:
        report = judge(target, cfg, mode="submit", all_tests=all_)
    except JudgeError as e:
        fail(str(e))
    with DB(db_path()) as db:
        # Only an AC on the part that is currently open unlocks the next one (not a peek).
        unlocks = db.unlocked_part(target.problem.slug, len(target.problem.parts)) == target.part.id
        unlocks = unlocks and target.part.id not in db.solved_parts(target.problem.slug)
    record(target, report, kind="submit", strict=strict)
    render_report(console, report, strict=strict, label=f"submit · {target.lang}")
    if report.verdict == Verdict.AC and unlocks:
        nxt = target.part.id + 1
        if nxt <= len(target.problem.parts):
            console.print(
                f"[green]part {nxt} unlocked:[/] dsa open {target.problem.slug}"
                + (f" --lang {target.lang}" if target.lang != cfg.default_lang else "")
            )
        else:
            console.print(f"[green]all parts of {target.problem.slug} solved[/]")
    raise typer.Exit(0 if report.verdict == Verdict.AC else 1)


@app.command()
def debug(
    test_id: Annotated[str | None, typer.Argument(help="Default: the last failing test")] = None,
    lang: LangOpt = None,
) -> None:
    """Re-run a failing test with sanitizers (C++) or a full traceback (Java/Python)."""
    cfg = _config()
    info = _here()
    target = _target(info, _lang(lang, info.lang))
    if test_id is None:
        with DB(db_path()) as db:
            test_id = db.last_failed_test(target.problem.slug, target.part.id)
        if test_id is None:
            fail("no failing test recorded yet; pass a TEST_ID like sample/1 or manual/large-01")
        if test_id == "custom":
            fail("the last failure was a custom case; rerun it with `dsa run --case ...`")
    try:
        report, stderr = debug_test(target, cfg, test_id)
    except JudgeError as e:
        fail(str(e))
    render_report(console, report, label=f"debug · {target.lang}")
    failure = report.first_failure
    if stderr.strip() and stderr.strip() not in (failure.message if failure else ""):
        console.print("[bold]stderr:[/]")
        console.print(escape(stderr.rstrip()))
    raise typer.Exit(0 if report.verdict == Verdict.AC else 1)


@app.command()
def watch(lang: LangOpt = None) -> None:
    """Re-run the samples every time the solution file is saved."""
    from watchfiles import watch as watch_files

    cfg = _config()
    info = _here()
    chosen = _lang(lang, info.lang)
    sol = info.sol_path(chosen).resolve()

    def once() -> None:
        console.rule(f"{sol.name}")
        try:
            target = make_target(info.bank, info.part, chosen, sol)
            report = judge(target, cfg, mode="run", all_tests=True)
            record(target, report, kind="run")
            render_report(console, report, label=f"run · {chosen}")
        except (JudgeError, ProblemError) as e:
            console.print(f"[red]error:[/] {escape(str(e))}")

    once()
    console.print(f"[dim]watching {sol} (Ctrl-C to stop)[/]")
    try:
        for changes in watch_files(info.dir):
            if any(Path(p).resolve() == sol for _, p in changes):
                once()
    except KeyboardInterrupt:
        pass


def main() -> None:
    get_language("cpp")  # fail fast if templates are missing
    app()
