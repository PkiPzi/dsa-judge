"""Export a stdio part's tests as a HackerRank or HackerEarth test-case zip (PLAN.md §11, Phase 4).

Upload formats, from the vendors' help centres (checked 2026-10-15):

* **HackerRank** (support.hackerrank.com, "How Do I Upload All Test Cases at Once?"): a zip of
  ``inputNN.txt`` / ``outputNN.txt`` pairs whose numbers match, in one flat directory with no
  sub-directories. Every test gets 10 points on upload; scores are edited in the UI.
* **HackerEarth** (help.hackerearth.com, "Creating custom programming questions" and "Test case
  format"): ``inNN.txt`` / ``outNN.txt`` files zipped *without* a folder. Inputs must not contain
  empty lines, leading whitespace on the first non-empty line, or trailing whitespace on any
  line. They recommend 10 tests worth 100 points: 2 easy and 3 medium at 5 each, 5 hard at 15.

Only expected outputs that came from executing a ref (or the brute, flagged) are exported; the
AI never produces them.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from dsajudge.config import Config
from dsajudge.judge.limits import MemoryBackend
from dsajudge.judge.suite import SuiteError, disputed_ids, load_hidden
from dsajudge.judge.testset import JudgeTest, sample_tests
from dsajudge.model.render import normalize_output, render_input
from dsajudge.model.schema import Part, Problem

MAX_TEST_BYTES = 8 * 1024 * 1024  # HackerEarth: an individual test case must not exceed 8 MB


class ExportError(Exception):
    pass


@dataclass(frozen=True)
class Format:
    name: str
    in_prefix: str
    out_prefix: str
    # How many tests to export by default (the vendor's own guidance).
    default_count: int
    # HackerEarth rejects blank lines and surrounding whitespace in input files.
    strict_input: bool
    scored: bool
    guidance: str


FORMATS: dict[str, Format] = {
    "hackerrank": Format(
        "hackerrank",
        "input",
        "output",
        default_count=15,
        strict_input=False,
        scored=False,
        guidance=(
            "HackerRank: open the question's Test Cases tab, choose 'Bulk Upload as ZIP' and pick "
            "the zip. Every test gets 10 points on upload; edit scores and mark samples in the UI."
        ),
    ),
    "hackerearth": Format(
        "hackerearth",
        "in",
        "out",
        default_count=10,
        strict_input=True,
        scored=True,
        guidance=(
            "HackerEarth: in the question's test cases step choose 'Upload zip/tar', then assign "
            "the scores below (they total 100) and tick 'Visible to candidates' for the samples."
        ),
    ),
}

# Score weights per group, so a set of 2 easy + 3 medium + 5 hard tests gets HackerEarth's
# recommended 5/5/15 split.
TIER_WEIGHT = {"sample": 1, "edge": 1, "small_random": 1, "medium_random": 1, "max": 3}
# How the non-sample slots are shared between groups when fewer tests are asked for than exist.
# Weighted toward `max`: those are the tests that separate complexities.
GROUP_SHARE = {"edge": 1, "small_random": 2, "medium_random": 2, "max": 4}
GROUP_ORDER = ("sample", "edge", "small_random", "medium_random", "max")


@dataclass
class ExportTest:
    dsa_id: str
    group: str
    input_text: str
    output_text: str
    unverified: bool = False
    score: int | None = None

    @property
    def visible(self) -> bool:
        return self.group == "sample"


@dataclass
class ExportResult:
    format: str
    zip_path: Path
    notes_path: Path
    tests: list[ExportTest]
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def clean_text(text: str) -> str:
    """LF endings, no trailing whitespace, exactly one final newline."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def input_problem(fmt: Format, text: str) -> str | None:
    """Why a platform would reject this input file, or None."""
    if len(text.encode("utf-8")) > MAX_TEST_BYTES and fmt.strict_input:
        return "larger than 8 MB"
    if not fmt.strict_input:
        return None
    lines = text.rstrip("\n").split("\n")
    if any(not ln.strip() for ln in lines):
        return "has an empty line (HackerEarth rejects blank lines in input files)"
    if lines and lines[0][:1].isspace():
        return "starts with whitespace"
    return None


def select(tests: list[ExportTest], count: int | None) -> list[ExportTest]:
    """Keep all samples, then share the remaining slots between groups, spread evenly."""
    if count is None or count >= len(tests):
        return tests
    samples = [t for t in tests if t.group == "sample"]
    rest = [t for t in tests if t.group != "sample"]
    if count <= len(samples):
        return samples[:count]
    slots = count - len(samples)
    groups = {g: [t for t in rest if t.group == g] for g in GROUP_SHARE}
    live = {g: ts for g, ts in groups.items() if ts}
    if not live:
        return samples
    total_w = sum(GROUP_SHARE[g] for g in live)
    quota = {g: slots * GROUP_SHARE[g] / total_w for g in live}
    take = {g: min(int(q), len(live[g])) for g, q in quota.items()}
    # Every group with tests gets at least one slot while slots last, then largest remainders.
    for g in sorted(live, key=lambda g: -GROUP_SHARE[g]):
        if sum(take.values()) < slots and take[g] == 0:
            take[g] = 1
    while sum(take.values()) < slots:
        open_ = [g for g in live if take[g] < len(live[g])]
        if not open_:
            break
        g = max(open_, key=lambda g: (quota[g] - take[g], GROUP_SHARE[g]))
        take[g] += 1
    picked = list(samples)
    for g in GROUP_SHARE:
        if g in live and take[g]:
            picked += _spread(live[g], take[g])
    return picked


def _spread(items: list[ExportTest], k: int) -> list[ExportTest]:
    if k >= len(items):
        return list(items)
    if k == 1:
        return [items[len(items) // 2]]
    idx = sorted({round(i * (len(items) - 1) / (k - 1)) for i in range(k)})
    return [items[i] for i in idx]


def assign_scores(tests: list[ExportTest], total: int = 100) -> None:
    """Integer scores proportional to tier weight that sum to ``total`` (largest remainder)."""
    weights = [TIER_WEIGHT.get(t.group, 1) for t in tests]
    wsum = sum(weights)
    raw = [total * w / wsum for w in weights]
    floors = [int(r) for r in raw]
    left = total - sum(floors)
    order = sorted(range(len(tests)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:left]:
        floors[i] += 1
    for t, sc in zip(tests, floors, strict=True):
        t.score = sc


def file_names(fmt: Format, index: int, n: int) -> tuple[str, str]:
    width = max(2, len(str(n - 1)))
    num = f"{index:0{width}d}"
    return f"{fmt.in_prefix}{num}.txt", f"{fmt.out_prefix}{num}.txt"


def write_zip(path: Path, files: list[tuple[str, str]]) -> None:
    """A flat, deterministic zip (fixed timestamps and permissions, no directory entries)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, content.encode("utf-8"))


def _candidates(
    problem_dir: Path,
    problem: Problem,
    part: Part,
    cfg: Config,
    backend: MemoryBackend,
) -> tuple[list[JudgeTest], list[str], list[tuple[str, str]]]:
    try:
        suite = load_hidden(problem_dir, problem, part, cfg, backend)
    except SuiteError as e:
        raise ExportError(str(e)) from e
    if suite is None:
        raise ExportError(f"part {part.id} has no built tests: run `dsa build {problem.slug}`")
    tests = [*sample_tests(problem.slug, part), *suite.tests]
    skipped: list[tuple[str, str]] = []
    disputes = disputed_ids(problem_dir, part)
    keep: list[JudgeTest] = []
    for t in tests:
        if t.id in disputes:
            skipped.append((t.id, "disputed"))
        elif not t.has_expected:
            skipped.append((t.id, "no expected output (too big for the brute oracle)"))
        else:
            keep.append(t)
    return keep, suite.notes, skipped


def export_part(
    problem_dir: Path,
    problem: Problem,
    part: Part,
    cfg: Config,
    backend: MemoryBackend,
    fmt_name: str,
    out_dir: Path,
    *,
    count: int | str | None = "auto",
) -> ExportResult:
    """Write ``<slug>-part<N>-<format>.zip`` plus a notes file into ``out_dir``."""
    if fmt_name not in FORMATS:
        raise ExportError(f"unknown format {fmt_name!r}; use {', '.join(FORMATS)}")
    fmt = FORMATS[fmt_name]
    if part.mode != "stdio":
        raise ExportError(
            f"part {part.id} is {part.mode} mode: only stdio parts (full programs reading stdin) "
            "can be exported. Set `mode: stdio` and an `input_format` on the part."
        )
    wanted = fmt.default_count if count == "auto" else count
    assert wanted is None or isinstance(wanted, int)

    candidates, notes, skipped = _candidates(problem_dir, problem, part, cfg, backend)
    warnings = list(notes)
    pool: list[ExportTest] = []
    for t in candidates:
        text = clean_text(render_input(part, t.args()))
        why = input_problem(fmt, text)
        out = clean_text(normalize_output(str(t.expected)))
        if why is None and fmt.strict_input and len(out.encode("utf-8")) > MAX_TEST_BYTES:
            why = "expected output is larger than 8 MB"
        if why:
            skipped.append((t.id, why))
            continue
        pool.append(ExportTest(t.id, t.group, text, out, unverified=t.unverified))
    if not pool:
        raise ExportError("no exportable tests: " + "; ".join(f"{i}: {w}" for i, w in skipped[:3]))
    tests = select(pool, wanted)
    if wanted is not None and len(pool) < wanted:
        warnings.append(f"only {len(pool)} exportable tests, fewer than the {wanted} asked for")
    if any(t.unverified for t in tests):
        warnings.append(
            "brute-only part: some expected outputs come from the unverified brute-force oracle"
        )
    if fmt.name == "hackerearth" and len(tests) != fmt.default_count:
        warnings.append(
            f"HackerEarth recommends exactly {fmt.default_count} tests worth 100 points; "
            f"this export has {len(tests)} (use --count {fmt.default_count})"
        )
    if fmt.name == "hackerrank" and not 3 <= len(tests) <= 15:
        warnings.append("HackerRank's quality guidance is 3-15 tests per question")
    if fmt.scored:
        assign_scores(tests)

    files: list[tuple[str, str]] = []
    for i, et in enumerate(tests):
        in_name, out_name = file_names(fmt, i, len(tests))
        files += [(in_name, et.input_text), (out_name, et.output_text)]
    base = f"{problem.slug}-part{part.id}-{fmt.name}"
    zip_path = out_dir / f"{base}.zip"
    write_zip(zip_path, files)
    notes_path = out_dir / f"{base}.notes.txt"
    res = ExportResult(fmt.name, zip_path, notes_path, tests, skipped, warnings)
    notes_path.write_text(render_notes(fmt, problem, part, res), encoding="utf-8")
    return res


def render_notes(fmt: Format, problem: Problem, part: Part, res: ExportResult) -> str:
    lines = [
        f"{problem.slug} part {part.id} ({part.title}) exported for {fmt.name}",
        "",
        fmt.guidance,
        "",
        f"{'file':<14}{'dsa test':<20}{'group':<15}{'visible':<9}{'score':<7}bytes (in/out)",
    ]
    n = len(res.tests)
    for i, t in enumerate(res.tests):
        in_name, _ = file_names(fmt, i, n)
        score = str(t.score) if t.score is not None else "-"
        flag = " (unverified oracle)" if t.unverified else ""
        sizes = f"{len(t.input_text)}/{len(t.output_text)}"
        lines.append(
            f"{in_name:<14}{t.dsa_id:<20}{t.group:<15}{'yes' if t.visible else 'no':<9}"
            f"{score:<7}{sizes}{flag}"
        )
    if res.skipped:
        lines += ["", "not exported:"] + [f"  {i}: {why}" for i, why in res.skipped]
    if res.warnings:
        lines += ["", "warnings:"] + [f"  {w}" for w in res.warnings]
    lines += [
        "",
        "Time and memory limits are not part of the zip: set them in the question "
        f"(dsa limits: {part.limits.time_ms} ms, {part.limits.memory_mb} MB).",
    ]
    return "\n".join(lines) + "\n"
