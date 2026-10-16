"""Intake orchestration: `dsa add`, `dsa followup`, `dsa ref` (PLAN.md §3.1-3.3, §7).

collect -> transcribe -> structure -> (assist) -> review -> verify -> build -> publish

Every stage writes into ``$DSA_HOME/inbox/<key>/`` and is skipped on ``--resume`` when its output
exists, so a crash or Ctrl-C loses nothing:

    inbox/<key>/
      raw/NN-name.ext         originals, kept forever
      work/                   downscaled images
      transcripts/NN.md       one per source; transcript.md is the assembled text
      segments.json           pass (a)
      extraction/partN.json   pass (b) + constraints
      draft/                  the problem under review (a normal problem directory)
      state.json
"""

from __future__ import annotations

import difflib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dsajudge.ai.assist import AssistContext, AssistFailed, write_brute, write_checker, write_gen
from dsajudge.ai.base import LLM, AIError, Transcriber
from dsajudge.ai.factory import ProviderFactory
from dsajudge.ai.retry import ask_structured, extract_code
from dsajudge.ai.schemas import CodeOut, Extraction, Segmentation
from dsajudge.config import Config, dsa_home
from dsajudge.gen.suite import gen_script
from dsajudge.intake import structure
from dsajudge.intake.bundle import Group, drop_repeated_runs, group_pages, split_bundle
from dsajudge.intake.draft import (
    append_parts,
    examine,
    refresh_header,
    render_yaml,
    slugify,
    strip_header,
    update_part,
)
from dsajudge.intake.review import Editor, ReviewAborted, review_loop, run_editor
from dsajudge.intake.sources import IntakeError, SourceRec, classify, collect
from dsajudge.intake.stitch import stitch_pages
from dsajudge.intake.transcribe import (
    UNREADABLE,
    assemble,
    code_blocks,
    parse_sections,
    transcribe_halves,
    transcribe_one,
)
from dsajudge.judge.checkers import part_checker
from dsajudge.judge.limits import MemoryBackend
from dsajudge.judge.testset import OracleError, agree, run_source, sample_tests
from dsajudge.lang import get_language
from dsajudge.model.loader import ProblemError, append_ref, load_problem
from dsajudge.model.schema import LANGS, Lang, Part, Problem, SourceFile
from dsajudge.oracle.build import BuildError, BuildReport, build_part
from dsajudge.store.bank import problems_dir

STAGES = ("collect", "transcribe", "segment", "extract", "draft", "assist", "review", "build")
FENCE_LANG = {
    "c++": "cpp",
    "cpp": "cpp",
    "cc": "cpp",
    "java": "java",
    "py": "python",
    "python": "python",
    "python3": "python",
}


@dataclass
class Env:
    """Everything a pipeline run needs from the outside world."""

    cfg: Config
    providers: ProviderFactory
    backend: MemoryBackend
    say: Callable[[str], None] = lambda _m: None
    edit: Editor = run_editor
    confirm: Callable[[str], bool] = lambda _q: False
    fixes: list[str] = field(default_factory=list)  # solutions whose OCR slips the model fixed


class State(BaseModel):
    version: int = 1
    key: str
    mode: str  # add | followup
    slug: str = ""  # final problem slug (add: from --slug or the title)
    base_part: int = 0  # followup: parts that existed before
    sources: list[SourceRec] = Field(default_factory=list)
    done: list[str] = Field(default_factory=list)
    transcribed_files: list[str] = Field(default_factory=list)  # draft files from photos/voice
    notes: list[str] = Field(default_factory=list)
    single_question: bool = False  # bundle: the transcript is known to be one question
    title_hint: str = ""  # bundle: the title the splitter found, for when the model gives none


@dataclass
class Outcome:
    slug: str
    first_part: int
    parts: int
    reports: list[BuildReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pending: str = ""  # unattended run stopped before the review; what must be fixed


def inbox_dir(key: str) -> Path:
    return dsa_home() / "inbox" / key


def load_state(inbox: Path) -> State:
    return State.model_validate_json((inbox / "state.json").read_text(encoding="utf-8"))


def save_state(inbox: Path, state: State) -> None:
    (inbox / "state.json").write_text(state.model_dump_json(indent=1) + "\n", encoding="utf-8")


def _mark(inbox: Path, state: State, stage: str) -> None:
    if stage not in state.done:
        state.done.append(stage)
    save_state(inbox, state)


# ---------------------------------------------------------------- stages


# Retried in turn when the vision model fails on a photo (typically a repetition loop, which
# at temperature 0 would repeat identically): some randomness and a repeat penalty.
RETRY_SAMPLING = (
    {"temperature": 0.3, "repeat_penalty": 1.1, "num_predict": 3000},
    {"temperature": 0.7, "repeat_penalty": 1.2, "num_predict": 3000},
)


def _transcribe_robust(
    env: Env,
    state: State,
    rec: SourceRec,
    raw: Path,
    work: Path,
    vision: LLM,
    audio: Transcriber,
) -> str:
    """transcribe_one, but a photo the model cannot read becomes an explicit [??] page instead
    of aborting the whole run (the [??] keeps the unattended review from accepting it)."""
    tries = [vision]
    if rec.kind == "image" and hasattr(vision, "with_options"):
        tries += [vision.with_options(**o) for o in RETRY_SAMPLING]
    last: AIError | None = None
    for v in tries:
        try:
            return transcribe_one(rec, raw, work, v, audio, env.cfg.ai.max_image_side)
        except AIError as e:
            if rec.kind != "image" or "not reachable" in str(e):
                raise  # Ollama being down is a whole-run problem, left to `--resume`
            last = e
    if rec.kind == "image":  # last resort: the top and the bottom of the photo, one at a time
        for v in tries:
            try:
                return transcribe_halves(rec, raw, work, v, env.cfg.ai.max_image_side)
            except AIError as e:
                if "not reachable" in str(e):
                    raise
                last = e
    note = f"photo {rec.original}: the model could not read it ({len(tries)} tries); marked [??]"
    state.notes.append(note)
    env.say(note)
    assert last is not None
    return f"{UNREADABLE} this photo could not be transcribed"


def _transcribe(env: Env, inbox: Path, state: State) -> dict[int, str]:
    raw, work, tdir = inbox / "raw", inbox / "work", inbox / "transcripts"
    tdir.mkdir(exist_ok=True)
    vision = env.providers.vision(env.cfg)
    audio = env.providers.transcriber(env.cfg)
    sections: dict[int, str] = {}
    for rec in state.sources:
        cached = tdir / f"{rec.n:02d}.md"
        if cached.exists():
            sections[rec.n] = cached.read_text(encoding="utf-8")
            continue
        env.say(f"transcribing {rec.original} ({rec.kind})")
        text = _transcribe_robust(env, state, rec, raw, work, vision, audio)
        cached.write_text(text, encoding="utf-8")
        sections[rec.n] = text
    # Photos of one question: drop overlapping lines, join a code listing split across photos.
    stitched = stitch_pages(state.sources, sections)
    for note in stitched.notes:
        env.say(note)
        if note not in state.notes:
            state.notes.append(note)
    (inbox / "transcript.md").write_text(
        assemble(state.sources, stitched.sections), encoding="utf-8"
    )
    return stitched.sections


def _segment(
    env: Env, inbox: Path, state: State, sections: dict[int, str], context: str
) -> Segmentation:
    path = inbox / "segments.json"
    if path.exists():
        return Segmentation.model_validate_json(path.read_text(encoding="utf-8"))
    env.say("splitting the transcript into parts")
    transcript = (inbox / "transcript.md").read_text(encoding="utf-8")
    seg = structure.segment(
        env.providers.text(env.cfg), transcript, sections, env.cfg.ai.max_attempts, context
    )
    if state.single_question:
        seg = structure.merge_false_followups(seg)
    if not seg.title:
        seg.title = state.title_hint
    path.write_text(seg.model_dump_json(indent=1) + "\n", encoding="utf-8")
    return seg


class Extracted(BaseModel):
    extraction: Extraction
    constraints: dict[str, Any] = Field(default_factory=dict)
    constraints_error: str = ""
    constraints_note: str = ""  # a guess the tool made, for the reviewer


def _extract(
    env: Env,
    inbox: Path,
    seg: Segmentation,
    prev: tuple[Extraction, dict[str, Any], int] | None,
    base_part: int,
    sections: dict[int, str] | None = None,
) -> list[Extracted]:
    edir = inbox / "extraction"
    edir.mkdir(exist_ok=True)
    llm = env.providers.text(env.cfg)
    attempts = env.cfg.ai.max_attempts
    out: list[Extracted] = []
    for sp in seg.parts:
        path = edir / f"part{sp.part}.json"
        if path.exists():
            got = Extracted.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            part_id = base_part + sp.part
            env.say(f"extracting part {part_id}")
            context = structure.follow_up_context(*prev) if prev else ""
            refs = [
                code_blocks((sections or {}).get(c.section, ""))[c.block - 1][1]
                for c in seg.code
                if c.part == sp.part
                and c.role == "ref"
                and c.block <= len(code_blocks((sections or {}).get(c.section, "")))
            ]
            ext = structure.extract_part(llm, part_id, sp.text, context, attempts, refs)
            ext.method = structure.method_from_code(ext.method, refs)
            constraints: dict[str, Any] = {}
            error = ""
            try:
                constraints = structure.fill_constraints(llm, ext, part_id, attempts)
            except AIError as e:
                error = str(e)
            said = (ext.statement + " " + " ".join(ext.constraints_text)).lower()
            note = "; ".join(
                f"'{name}' has no stated alphabet: assumed {structure.ASSUMED_CHARSET}"
                for name, c in constraints.items()
                if c.get("charset") == structure.ASSUMED_CHARSET
                and structure.ASSUMED_CHARSET not in said
            )
            got = Extracted(
                extraction=ext,
                constraints=constraints,
                constraints_error=error,
                constraints_note=note,
            )
            path.write_text(got.model_dump_json(indent=1) + "\n", encoding="utf-8")
        out.append(got)
        prev = (got.extraction, got.constraints, base_part + sp.part)
    return out


def _fence_lang(tag: str, fallback: str) -> Lang:
    lang = FENCE_LANG.get(tag.lower(), fallback)
    return lang if lang in LANGS else "python"


def _write_parts(
    stage: Path,
    seg: Segmentation,
    extracted: list[Extracted],
    sections: dict[int, str],
    state: State,
    base_part: int,
) -> list[dict[str, Any]]:
    """Statement and code files for each new part; returns the problem.yaml part mappings."""
    by_section = {r.n: r for r in state.sources}
    parts: list[dict[str, Any]] = []
    for sp, got in zip(seg.parts, extracted, strict=True):
        pid = base_part + sp.part
        pdir = stage / f"part{pid}"
        pdir.mkdir(parents=True, exist_ok=True)
        stmt = f"part{pid}/statement.md"
        (stage / stmt).write_text(got.extraction.statement.strip() + "\n", encoding="utf-8")
        data = structure.part_data(got.extraction, got.constraints, pid, stmt)
        refs: list[dict[str, Any]] = []
        for c in (c for c in seg.code if c.part == sp.part):
            rec = by_section[c.section]
            tag, code = code_blocks(sections[c.section])[c.block - 1]
            lang = rec.lang if rec.kind == "code" and rec.lang else _fence_lang(tag, c.lang)
            ext = get_language(lang).ext
            if c.role == "brute":
                rel = f"part{pid}/brute.{ext}"
                data["brute"] = {"lang": lang, "file": rel, "origin": "user", "verified": False}
            else:
                n = len(refs) + 1
                rel = f"part{pid}/ref/ref{'' if n == 1 else n}.{ext}"
                refs.append({"lang": lang, "file": rel, "origin": "user", "verified": False})
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            (stage / rel).write_text(code.rstrip("\n") + "\n", encoding="utf-8")
            if rec.kind in ("image", "audio"):
                state.transcribed_files.append(rel)
        data["refs"] = refs
        data["limits"] = {"time_ms": "auto", "memory_mb": 256}
        parts.append(data)
    return parts


def _assist(env: Env, stage: Path, state: State, first_part: int) -> list[str]:
    """Write what is missing: gen.py for guarantees, a brute when there is no oracle, a checker."""
    notes: list[str] = []
    llm = env.providers.text(env.cfg)
    problem = load_problem(stage, check_files=False)
    ctx = AssistContext(stage, problem, env.cfg, env.backend)
    for part in problem.parts[first_part - 1 :]:
        pid = part.id
        try:
            if part.guarantees and gen_script(stage, part) is None:
                env.say(f"writing gen.py for part {pid} (guarantees)")
                w = write_gen(llm, ctx, part)
                notes += [f"part {pid}: gen.py written by AI ({n})" for n in w.notes]
            if not part.refs and not part.brute:
                env.say(f"writing a brute for part {pid}")
                w = write_brute(llm, ctx, part)
                update_part(
                    stage,
                    pid,
                    brute={
                        "lang": "python",
                        "file": w.path,
                        "origin": "llm",
                        "verified": w.verified,
                    },
                )
                notes.append(f"part {pid}: brute written by AI; {'; '.join(w.notes)}")
            if part.checker == "custom" and not (stage / f"part{pid}" / "checker.py").exists():
                env.say(f"writing checker.py for part {pid}")
                problem = load_problem(stage, check_files=False)
                ctx = AssistContext(stage, problem, env.cfg, env.backend)
                w = write_checker(llm, ctx, problem.part(pid))
                notes += [f"part {pid}: checker.py written by AI ({n})" for n in w.notes]
        except (AssistFailed, AIError, ProblemError) as e:
            notes.append(f"part {pid}: the assistant could not finish: {e}")
        problem = load_problem(stage, check_files=False)
        ctx = AssistContext(stage, problem, env.cfg, env.backend)
    shutil.rmtree(stage / ".candidates", ignore_errors=True)
    for part in problem.parts[first_part - 1 :]:
        shutil.rmtree(stage / f"part{part.id}" / ".candidates", ignore_errors=True)
    return notes


# ---------------------------------------------------------------- verifying transcribed code


def check_source(
    env: Env, problem_dir: Path, problem: Problem, part: Part, src: SourceFile
) -> str | None:
    """Does this code compile and reproduce the statement's samples? None means yes."""
    samples = sample_tests(problem.slug, part)
    check = part_checker(part, problem_dir, env.cfg.judge)
    try:
        runs = run_source(
            problem,
            problem_dir,
            part,
            src,
            samples,
            env.cfg.judge,
            env.backend,
            env.cfg.plan.brute_timeout_s,
        )
    except OracleError as e:
        return str(e)
    for t in samples:
        run = runs[t.id]
        if run.status != "ok":
            return f"it failed on {t.id} ({run.status}): {run.message.strip()[:400]}"
        if not agree(check, t, t.expected, run.output):
            return (
                f"on {t.id} it returned {json.dumps(run.output)[:200]} but the statement "
                f"says {json.dumps(t.expected)[:200]}"
            )
    return None


def fix_transcribed(
    env: Env, problem_dir: Path, problem: Problem, part: Part, src: SourceFile, transcribed: bool
) -> None:
    """A ref must compile and pass the samples. For code read from a photo or voice note the
    model may propose a fix; it is shown as a diff and applied only if the user approves."""
    problem_text = check_source(env, problem_dir, problem, part, src)
    path = problem_dir / src.file
    llm = env.providers.text(env.cfg)
    tries = 0
    while problem_text is not None:
        if not transcribed:
            raise IntakeError(f"{src.file} does not pass the samples: {problem_text}")
        if tries >= env.cfg.ai.max_attempts:
            raise IntakeError(
                f"{src.file} still fails after {tries} proposed fix(es): {problem_text}\n"
                "Fix it by hand in the draft (dsa add --resume)."
            )
        tries += 1
        old = path.read_text(encoding="utf-8")
        from dsajudge.ai.prompts import load_prompt

        system, user = load_prompt("fix_ocr").render(
            lang=src.lang, code=old.rstrip("\n"), problem=problem_text
        )
        new = extract_code(llm.complete(user, system=system), src.lang)
        if new.strip() == old.strip():
            raise IntakeError(f"the model proposed no change for {src.file}: {problem_text}")
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(True),
                new.splitlines(True),
                f"{src.file} (transcribed)",
                f"{src.file} (proposed)",
            )
        )
        env.say(f"{src.file} {problem_text}\nProposed fix:\n{diff}")
        if not env.confirm(f"Apply this fix to {src.file}?"):
            raise IntakeError(
                f"fix for {src.file} declined; edit the draft yourself and run dsa add --resume"
            )
        path.write_text(new, encoding="utf-8")
        env.fixes.append(src.file)
        problem_text = check_source(env, problem_dir, problem, part, src)


# ---------------------------------------------------------------- driving a run


def _progress(env: Env, pid: int) -> Callable[[str], None]:
    def say(msg: str) -> None:
        env.say(f"part {pid}: {msg}")

    return say


def _build_new(env: Env, stage: Path, first_part: int, transcribed: list[str]) -> list[BuildReport]:
    problem = load_problem(stage)
    for part in problem.parts[first_part - 1 :]:
        for ref in part.refs:
            fix_transcribed(env, stage, problem, part, ref, ref.file in transcribed)
        if part.brute and part.brute.file in transcribed:
            fix_transcribed(env, stage, problem, part, part.brute, True)
    reports = []
    for part in problem.parts[first_part - 1 :]:
        env.say(f"building the hidden tests for part {part.id}")
        try:
            reports.append(
                build_part(
                    stage,
                    problem,
                    part,
                    env.cfg,
                    env.backend,
                    progress=_progress(env, part.id),
                )
            )
        except BuildError as e:
            raise IntakeError(
                f"part {part.id} could not be built:\n{e}\n"
                "Fix the draft (the inbox keeps it) and run dsa add --resume."
            ) from e
    return reports


def _publish(env: Env, inbox: Path, state: State, stage: Path, first_part: int) -> None:
    path = stage / "problem.yaml"
    path.write_text(strip_header(path.read_text(encoding="utf-8")), encoding="utf-8")
    suffix = "" if state.mode == "add" else f"-part{first_part}"
    raw_dest = stage / ("raw" if state.mode == "add" else f"raw/part{first_part}")
    shutil.copytree(inbox / "raw", raw_dest, dirs_exist_ok=True)
    shutil.copyfile(inbox / "transcript.md", stage / f"transcript{suffix}.md")
    dest = problems_dir() / state.slug
    if state.mode == "add":
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage, dest)
    else:
        new, old = dest.with_name(dest.name + ".new"), dest.with_name(dest.name + ".old")
        shutil.rmtree(new, ignore_errors=True)
        shutil.copytree(stage, new)
        dest.rename(old)
        new.rename(dest)
        shutil.rmtree(old, ignore_errors=True)


def _run(
    env: Env,
    inbox: Path,
    state: State,
    texts_ctx: str,
    prev_for_context: Any,
    unattended: bool = False,
) -> Outcome:
    sections = (
        {
            rec.n: (inbox / "transcripts" / f"{rec.n:02d}.md").read_text(encoding="utf-8")
            for rec in state.sources
        }
        if "transcribe" in state.done
        else {}
    )
    if "transcribe" not in state.done:
        sections = _transcribe(env, inbox, state)
        _mark(inbox, state, "transcribe")
    seg = _segment(env, inbox, state, sections, texts_ctx)
    _mark(inbox, state, "segment")
    if state.mode == "add" and not state.slug:
        state.slug = _free_slug(slugify(seg.title))
        save_state(inbox, state)
    extracted = _extract(env, inbox, seg, prev_for_context, state.base_part, sections)
    _mark(inbox, state, "extract")

    stage = inbox / "draft"
    first_part = state.base_part + 1
    if "draft" not in state.done:
        shutil.rmtree(stage, ignore_errors=True)
        if state.mode == "add":
            stage.mkdir(parents=True)
        else:
            shutil.copytree(problems_dir() / state.slug, stage)
        parts = _write_parts(stage, seg, extracted, sections, state, state.base_part)
        if state.mode == "add":
            doc = {
                "schema": 1,
                "slug": state.slug,
                "title": seg.title or state.slug,
                "tags": [],
                "source": "intake",
                "parts": parts,
            }
            (stage / "problem.yaml").write_text(render_yaml(doc), encoding="utf-8")
        else:
            append_parts(stage, parts)
        state.notes += [
            f"part {state.base_part + sp.part}: constraints could not be filled in: "
            f"{got.constraints_error}"
            for sp, got in zip(seg.parts, extracted, strict=True)
            if got.constraints_error
        ]
        state.notes += [
            f"part {state.base_part + sp.part}: {got.constraints_note}"
            for sp, got in zip(seg.parts, extracted, strict=True)
            if got.constraints_note
        ]
        _mark(inbox, state, "draft")
    if "assist" not in state.done:
        state.notes += _assist(env, stage, state, first_part)
        _mark(inbox, state, "assist")
    if "review" not in state.done and unattended:
        # The tool's own gate (valid draft, no [??] left) stands in for the editor; anything it
        # flags is left for `--resume`, which opens the normal review.
        findings, _ = examine(stage, first_part)
        if findings.errors:
            return Outcome(
                state.slug,
                first_part,
                len(seg.parts),
                notes=state.notes,
                pending="; ".join(findings.errors),
            )
        state.notes.append("accepted without a human review (unattended run)")
        _mark(inbox, state, "review")
    if "review" not in state.done:
        review_loop(stage, env.edit, first_new_part=first_part, notes=state.notes, say=env.say)
        _mark(inbox, state, "review")
    reports = _build_new(env, stage, first_part, state.transcribed_files)
    _publish(env, inbox, state, stage, first_part)
    _mark(inbox, state, "build")
    return Outcome(state.slug, first_part, len(seg.parts), reports, state.notes)


def _free_slug(base: str) -> str:
    slug, n = base, 2
    while (problems_dir() / slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


# ---------------------------------------------------------------- public entry points


def add(
    env: Env,
    *,
    slug: str | None,
    files: list[Path],
    texts: list[tuple[str, str]],
    resume: str | None = None,
) -> Outcome:
    if resume:
        inbox = inbox_dir(resume)
        if not (inbox / "state.json").exists():
            raise IntakeError(f"no inbox {resume!r} to resume (looked in {inbox.parent})")
        state = load_state(inbox)
        if "build" in state.done:
            raise IntakeError(f"{resume} was already built and added as {state.slug!r}")
    else:
        key = slug or _timestamp_key()
        if slug and (problems_dir() / slug / "problem.yaml").exists():
            raise IntakeError(f"problem {slug!r} already exists; pick another --slug")
        inbox = inbox_dir(key)
        if inbox.exists():
            raise IntakeError(f"{inbox} exists: use --resume {key}, or remove it to start over")
        recs = collect(inbox / "raw", files, texts)
        state = State(key=key, mode="add", slug=slug or "", sources=recs, done=["collect"])
        save_state(inbox, state)
    return _run(env, inbox, state, "", None)


ONE_QUESTION = (
    "(This transcript is ONE interview question. Use a single part unless the text explicitly "
    "labels a follow-up. Constraints, examples and notes belong to the same part.)\n\n"
)


class BundleItem(BaseModel):
    """One question found in a bundle, and what became of it."""

    key: str
    title: str
    pages: list[str]
    status: str = "pending"  # built | needs-review | failed
    slug: str = ""
    detail: str = ""
    seconds: float = 0.0


def _bundle_inbox(inbox: Path, state: State, sections: dict[int, str], g: Group, key: str) -> Path:
    """A normal add-inbox holding only this question's pages, already transcribed."""
    sub = inbox_dir(key)
    if (sub / "state.json").exists():
        return sub
    by_n = {r.n: r for r in state.sources}
    (sub / "raw").mkdir(parents=True)
    (sub / "transcripts").mkdir()
    recs: list[SourceRec] = []
    texts: dict[int, str] = {}
    for new_n, (n, text) in enumerate(group_pages(g, sections), 1):
        rec = by_n[n]
        shutil.copyfile(inbox / "raw" / rec.name, sub / "raw" / rec.name)
        recs.append(rec.model_copy(update={"n": new_n}))
        texts[new_n] = text
        (sub / "transcripts" / f"{new_n:02d}.md").write_text(text, encoding="utf-8")
    (sub / "transcript.md").write_text(assemble(recs, texts), encoding="utf-8")
    sub_state = State(
        key=key,
        mode="add",
        sources=recs,
        done=["collect", "transcribe"],
        single_question=True,
        title_hint=g.title,
    )
    save_state(sub, sub_state)
    return sub


def bundle(
    env: Env,
    *,
    name: str | None,
    files: list[Path],
    resume: str | None = None,
    unattended: bool = False,
) -> list[BundleItem]:
    """Photos of several questions in one go: split them, then run each question alone.

    One question failing never stops the others; ``--resume NAME`` redoes only what is not built.
    """
    if resume:
        inbox = inbox_dir(resume)
        if not (inbox / "state.json").exists():
            raise IntakeError(f"no bundle {resume!r} to resume (looked in {inbox.parent})")
        state = load_state(inbox)
    else:
        key = name or f"bundle-{_timestamp_key()}"
        inbox = inbox_dir(key)
        if inbox.exists():
            raise IntakeError(f"{inbox} exists: use --resume {key}, or remove it to start over")
        state = State(
            key=key, mode="bundle", sources=collect(inbox / "raw", files, []), done=["collect"]
        )
        save_state(inbox, state)
    if "transcribe" not in state.done:
        _transcribe(env, inbox, state)
        _mark(inbox, state, "transcribe")
    sections = drop_repeated_runs(
        parse_sections((inbox / "transcript.md").read_text(encoding="utf-8"))
    )

    split_path = inbox / "split.json"
    if split_path.exists():
        groups = [Group.model_validate(g) for g in json.loads(split_path.read_text())]
    else:
        env.say("finding the questions in the bundle")
        groups = split_bundle(env.providers.text(env.cfg), sections, env.cfg.ai.max_attempts)
        split_path.write_text(json.dumps([g.model_dump() for g in groups], indent=1) + "\n")
    env.say(f"{len(groups)} question(s) found")

    names = {r.n: r.original for r in state.sources}
    items: list[BundleItem] = []
    for i, g in enumerate(groups, 1):
        key = f"{state.key}-q{i:02d}"
        item = BundleItem(
            key=key,
            title=g.title,
            pages=sorted({names[sp.section] for sp in g.spans}),
        )
        started = time.monotonic()
        fixes_before = len(env.fixes)
        try:
            sub = _bundle_inbox(inbox, state, sections, g, key)
            sub_state = load_state(sub)
            if "build" in sub_state.done:
                item.status, item.slug = "built", sub_state.slug
            else:
                env.say(f"question {i}/{len(groups)}")
                out = _run(env, sub, sub_state, ONE_QUESTION, None, unattended=unattended)
                item.slug = out.slug
                if out.pending:
                    item.status, item.detail = "needs-review", out.pending[:400]
                else:
                    item.status = "built"
        except Exception as e:  # one bad question must not stop the rest of the bundle
            item.status, item.detail = "failed", f"{type(e).__name__}: {str(e)[:400]}"
        if item.status == "built" and len(env.fixes) > fixes_before:
            item.detail = "the model fixed OCR slips in: " + ", ".join(env.fixes[fixes_before:])
        item.seconds = round(time.monotonic() - started, 1)
        items.append(item)
        (inbox / "bundle.json").write_text(
            json.dumps([x.model_dump() for x in items], indent=1) + "\n", encoding="utf-8"
        )
    return items


def followup(
    env: Env,
    slug: str,
    *,
    files: list[Path],
    texts: list[tuple[str, str]],
    resume: bool = False,
) -> Outcome:
    path = problems_dir() / slug
    if not (path / "problem.yaml").is_file():
        raise IntakeError(f"no problem {slug!r} in {problems_dir()} (see `dsa list`)")
    try:
        problem = load_problem(path)
    except ProblemError as e:
        raise IntakeError(str(e)) from e
    key = f"{slug}--followup"
    inbox = inbox_dir(key)
    if resume or ((inbox / "state.json").exists() and not files and not texts):
        if not (inbox / "state.json").exists():
            raise IntakeError(f"no follow-up in progress for {slug!r}")
        state = load_state(inbox)
    else:
        if inbox.exists():
            raise IntakeError(f"{inbox} exists: resume it (--resume) or remove it")
        recs = collect(inbox / "raw", files, texts)
        state = State(
            key=key,
            mode="followup",
            slug=slug,
            base_part=len(problem.parts),
            sources=recs,
            done=["collect"],
        )
        save_state(inbox, state)
    prev = _previous_context(inbox, problem)
    ctx = (
        f"These materials add follow-up part(s) to an existing question that already has "
        f"{state.base_part} part(s). Number the new parts 1, 2, ... (they are appended).\n\n"
    )
    return _run(env, inbox, state, ctx, prev)


def _previous_context(
    inbox: Path, problem: Problem
) -> tuple[Extraction, dict[str, Any], int] | None:
    """The last existing part as an Extraction, so the follow-up is read against it."""
    last = problem.parts[-1]
    if last.signature is None:
        return None
    sig = last.signature
    ext = Extraction(
        title=last.title,
        statement="",
        method=sig.method,
        params=[{"name": p.name, "type": p.type} for p in sig.params],
        returns=sig.returns,
        output_param=sig.output_param,
        constraints_text=[f"{k}: {json.dumps(v)}" for k, v in last.constraints.items()],
    )
    return ext, dict(last.constraints), last.id


def _timestamp_key() -> str:
    import time

    return time.strftime("new-%Y%m%d-%H%M%S")


def attach_ref(
    env: Env,
    slug: str,
    part_no: int,
    file: Path,
    *,
    lang: str | None = None,
    origin: str = "friend",
) -> BuildReport:
    """`dsa ref`: add a reference solution to a part. It must compile, pass the samples and
    agree with the existing oracle on every test, or it is rejected with the first
    disagreement shown and nothing changes."""
    path = problems_dir() / slug
    if not (path / "problem.yaml").is_file():
        raise IntakeError(f"no problem {slug!r} in {problems_dir()} (see `dsa list`)")
    try:
        problem = load_problem(path)
        part = problem.part(part_no)
    except (ProblemError, KeyError) as e:
        raise IntakeError(str(e)) from e
    c = classify(file)
    transcribed = False
    if c.kind == "code":
        chosen = lang or c.lang or "python"
        code = file.read_text(encoding="utf-8")
    elif c.kind == "image":
        from dsajudge.intake.transcribe import transcribe_one

        rec = SourceRec(n=1, name=file.name, original=file.name, kind="image", sha256="")
        work = dsa_home() / "inbox" / f"{slug}--ref" / "work"
        text = transcribe_one(
            rec,
            file.parent,
            work,
            env.providers.vision(env.cfg),
            env.providers.transcriber(env.cfg),
            env.cfg.ai.max_image_side,
        )
        out = ask_structured(
            env.providers.text(env.cfg),
            "extract_code",
            CodeOut,
            {"text": text},
            attempts=env.cfg.ai.max_attempts,
        )
        chosen, code, transcribed = lang or out.lang, out.code, True
    else:
        raise IntakeError("give a code file (.cpp .cc .java .py) or a photo of code")
    if chosen not in LANGS:
        raise IntakeError(f"unknown language {chosen!r}; use cpp, java or python")
    ext = get_language(chosen).ext
    n = 1
    while (path / f"part{part_no}" / "ref" / f"ref_{origin}{n}.{ext}").exists():
        n += 1
    rel = f"part{part_no}/ref/ref_{origin}{n}.{ext}"
    dest = path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(code.rstrip("\n") + "\n", encoding="utf-8")
    src = SourceFile(lang=chosen, file=rel, origin=origin)
    original = None
    try:
        fix_transcribed(env, path, problem, part, src, transcribed)
        original = append_ref(
            path, part_no, {"lang": chosen, "file": rel, "origin": origin, "verified": True}
        )
        fresh = load_problem(path)
        return build_part(path, fresh, fresh.part(part_no), env.cfg, env.backend, progress=env.say)
    except (BuildError, IntakeError) as e:
        if original is not None:
            (path / "problem.yaml").write_text(original, encoding="utf-8")
        dest.unlink(missing_ok=True)
        raise IntakeError(f"{rel} was rejected; nothing was changed:\n{e}") from e


__all__ = [
    "STAGES",
    "Env",
    "IntakeError",
    "Outcome",
    "ReviewAborted",
    "add",
    "attach_ref",
    "followup",
    "inbox_dir",
    "refresh_header",
]
