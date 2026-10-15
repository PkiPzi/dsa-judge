"""Phase 3 acceptance with fakes: dsa add / followup / ref, the review loop, resume, OCR fixes."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsajudge.ai import factory
from dsajudge.ai.fake import FakeLLM, FakeTranscriber
from dsajudge.ai.synth import render_text_image
from dsajudge.cli import app
from dsajudge.config import dsa_home
from dsajudge.gen.manifest import load_manifest
from dsajudge.intake.sources import IntakeError, classify, collect, natural_key, order_sources
from dsajudge.intake.transcribe import assemble, code_blocks, parse_sections, prepare_image
from dsajudge.model.loader import load_problem
from dsajudge.store.bank import problems_dir

from .conftest import REPO

runner = CliRunner()
RECORDED = REPO / "fixtures" / "ai" / "max-product.json"

STATEMENT_LINES = [
    "Maximum product of two numbers",
    "Given an integer array nums, return the largest product of two elements",
    "at different positions.",
    "Constraints:",
    "2 <= nums.length <= 10^{5}",
    "-10^{4} <= nums[i] <= 10^{4}",
    "Example 1:  Input: nums = [3, 5, -2, 4]   Output: 20",
]
REF_PY = """\
class Solution:
    def maxProductOfTwo(self, nums):
        nums = sorted(nums)
        if len(nums) == 2:
            return nums[0] * nums[1]
        return max(nums[0] * nums[1], nums[-1] * nums[-2])
"""
REF_CPP = """\
class Solution {
public:
    int maxProductOfThree_unused;
    long long maxProductOfThree(vector<int>& nums) {
        sort(nums.begin(), nums.end());
        int n = nums.size();
        long long a = 1LL * nums[n - 1] * nums[n - 2] * nums[n - 3];
        long long b = 1LL * nums[0] * nums[1] * nums[n - 1];
        return max(a, b);
    }
};
"""

# A scripted $EDITOR: each invocation applies the next round of [old, new] replacements to
# problem.yaml (the first argument) and records what it saw, so tests can inspect the header.
EDITOR_SCRIPT = """\
#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).parent
rounds = json.loads((here / "rounds.json").read_text())
counter = here / "count.txt"
i = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(i + 1))
if i >= len(rounds):
    sys.exit(3)
target = Path(sys.argv[1])
(here / f"seen-{{i}}.txt").write_text(target.read_text())
(here / f"files-{{i}}.txt").write_text("\\n".join(sys.argv[1:]))
for old, new in rounds[i]:
    for name in sys.argv[1:]:
        f = Path(name)
        if old in f.read_text():
            f.write_text(f.read_text().replace(old, new, 1))
            break
    else:
        sys.exit(f"editor script: {{old!r}} not found in round {{i}}")
"""


@pytest.fixture
def editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "editor"
    d.mkdir()
    script = d / "ed.py"
    script.write_text(EDITOR_SCRIPT.format(python=sys.executable))
    script.chmod(0o755)
    (d / "rounds.json").write_text("[[]]")
    monkeypatch.setenv("EDITOR", str(script))
    yield d


def set_rounds(editor: Path, rounds: list[list[list[str]]]) -> None:
    (editor / "rounds.json").write_text(json.dumps(rounds))
    (editor / "count.txt").unlink(missing_ok=True)


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    d = tmp_path / "in"
    d.mkdir()
    photo = render_text_image(STATEMENT_LINES, d / "1-statement.png")
    voice = d / "2-voice.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(voice),
        ],
        check=True,
    )
    code = d / "3-solution.py"
    code.write_text(REF_PY)
    return {"photo": photo, "voice": voice, "code": code}


@pytest.fixture
def ai(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeLLM, FakeTranscriber]:
    llm = FakeLLM.from_file(RECORDED)
    audio = FakeTranscriber.from_file(RECORDED)
    monkeypatch.setattr(factory, "PROVIDERS", factory.FixedProviders(llm, audio))
    return llm, audio


def recorded(task: str, nth: int = 0) -> dict[str, object]:
    rules = [r for r in json.loads(RECORDED.read_text())["llm"] if r["task"] == task]
    return rules[nth]


FOLLOWUP_SEGMENT = {
    "title": "Follow-up",
    "parts": [
        {
            "part": 1,
            "text": "Follow-up: three elements. Example 1: nums = [1, 2, 3, 4] -> 24\n"
            "Example 2: nums = [-10, -10, 1, 3, 2] -> 300",
        }
    ],
    "code": [],
}
PAIR_BRUTE = (
    "```python\nclass Solution:\n    def maxProductOfTwo(self, nums):\n        best = None\n"
    "        for i in range(len(nums)):\n            for j in range(i + 1, len(nums)):\n"
    "                p = nums[i] * nums[j]\n                if best is None or p > best:\n"
    "                    best = p\n        return best\n```"
)


def dsa(*args: str, input: str | None = None) -> tuple[int, str]:
    r = runner.invoke(app, list(args), input=input)
    return r.exit_code, r.output


# ---------------------------------------------------------------- acceptance


def test_add_photo_voice_and_code_builds_a_reviewed_problem(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    editor: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, audio = ai
    # the reviewer retitles the problem
    set_rounds(
        editor,
        [
            [
                [
                    "\ntitle: Maximum product of elements",
                    "\ntitle: Max product of two or three elements",
                ]
            ]
        ],
    )
    code, out = dsa("add", *map(str, inputs.values()), "--slug", "max-product")
    assert code == 0, out
    assert "added" in out and "max-product" in out

    # the pipeline ran each stage once, the retry path ran, and the AI wrote part 2's brute
    tasks = llm.tasks()
    assert tasks.count("transcribe_image") == 1 and tasks.count("segment") == 1
    assert tasks.count("extract_part") == 2
    assert tasks.count("fill_constraints") == 3  # part 1 needed a second try
    assert tasks.count("write_brute") == 2  # the first brute was wrong on the examples
    assert [c.name for c in audio.calls] == ["2-voice.wav"] or audio.calls[0].name.endswith(
        "voice.wav"
    )
    # the photo given to the vision model is the rendered statement, with ink on it
    photo = next(c.images[0] for c in llm.calls if c.task == "transcribe_image")
    assert photo.suffix == ".png"
    from PIL import Image

    with Image.open(photo) as im:
        dark = sum(1 for px in im.convert("L").tobytes() if px < 100)
    assert dark > 500
    # the retry carried the validator's complaint back to the model
    retry = [c for c in llm.calls if c.task == "fill_constraints"][1]
    assert "violates your constraints" in retry.prompt
    # part 2 was read against part 1
    ext2 = [c for c in llm.calls if c.task == "extract_part"][1]
    assert "follows part 1" in ext2.prompt and "maxProductOfTwo" in ext2.prompt

    bank = problems_dir() / "max-product"
    problem = load_problem(bank)
    assert problem.title == "Max product of two or three elements"  # the review's edit survived
    assert [p.id for p in problem.parts] == [1, 2]
    assert problem.part(1).sig.method == "maxProductOfTwo"
    assert problem.part(1).constraints == {"nums": {"len": [2, "10^5"], "each": ["-10^4", "10^4"]}}
    assert problem.part(2).sig.returns == "long"
    ref = problem.part(1).refs[0]
    assert (ref.lang, ref.file) == ("python", "part1/ref/ref.py")
    assert (bank / ref.file).read_text() == REF_PY  # indentation survived exactly
    brute = problem.part(2).brute
    assert brute and brute.origin == "llm" and not problem.part(2).refs
    assert "CHECKLIST" not in (bank / "problem.yaml").read_text()  # the review header is gone
    # originals and the transcript are kept with the problem
    assert (bank / "raw" / "01-1-statement.png").exists() and (bank / "transcript.md").exists()
    assert "10^5" in (bank / "transcript.md").read_text()

    m1, m2 = load_manifest(bank, 1), load_manifest(bank, 2)
    assert m1 and m1.oracle == "ref" and m2 and m2.oracle == "brute-only"
    assert m1.tests and m2.tests

    # and the result is playable: solve part 1 and unlock part 2
    assert dsa("open", "max-product", "--no-editor")[0] == 0
    monkeypatch.chdir(dsa_home() / "work" / "max-product" / "part1")
    (Path.cwd() / "sol.py").write_text(REF_PY)
    code, out = dsa("submit", "--lang", "python")
    assert code == 0 and "Accepted" in out, out


def test_review_is_mandatory_and_loops_until_the_draft_is_valid(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    editor: Path,
) -> None:
    # round 0 breaks sample 1 (an OCR-style slip), round 1 sees the complaint and fixes it
    set_rounds(
        editor,
        [
            [["{len: [2, 10^5]", "{len: [2, 3]"]],
            [["{len: [2, 3]", "{len: [2, 10^5]"]],
        ],
    )
    code, out = dsa("add", *map(str, inputs.values()), "--slug", "mp")
    assert code == 0, out
    assert (editor / "count.txt").read_text() == "2"
    first, second = (editor / "seen-0.txt").read_text(), (editor / "seen-1.txt").read_text()
    assert "REVIEW CHECKLIST" in first
    # the checklist shows what a human must verify
    for needle in (
        "signature: maxProductOfTwo(nums: int[]) -> int",
        "constraint nums:",
        "[2, 100000]",
        "sample 1:",
        "ref for part 1: part1/ref/ref.py",
        "brute for part 2: part2/brute.py (python, llm)",
    ):
        assert needle in first, needle
    assert "Assistant notes:" in first and "brute written by AI" in first
    # the second session opened with the problem spelled out
    assert "MUST FIX" in second and "sample" in second and "breaks the constraints" in second
    # the editor was handed the statements and code as well
    assert "statement.md" in (editor / "files-0.txt").read_text()
    assert (problems_dir() / "mp").exists()


def test_no_editor_means_no_build(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("DSA_EDITOR", raising=False)
    code, out = dsa("add", *map(str, inputs.values()), "--slug", "mp")
    assert code == 1 and "set $EDITOR" in out and "mandatory" in out
    assert not (problems_dir() / "mp").exists()


def test_unresolved_markers_block_the_build_until_fixed(
    home: Path, inputs: dict[str, Path], ai: tuple[FakeLLM, FakeTranscriber], editor: Path
) -> None:
    llm, _ = ai
    llm.add(task="transcribe_image", image="statement.png", response="Unreadable [??] photo")
    ext = json.loads(json.dumps(recorded("extract_part", 0)["response"]))
    ext["statement"] += "\nThe bound is [??]."
    llm.add(task="extract_part", contains="Text of part 1", response=ext)
    set_rounds(editor, [[], [["The bound is [??].", "The bound is 10^5."]]])
    code, out = dsa("add", *map(str, inputs.values()), "--slug", "mp")
    assert code == 0, out
    assert "unreadable spots" in (editor / "seen-1.txt").read_text()
    assert "[??]" not in (problems_dir() / "mp" / "part1" / "statement.md").read_text()


def test_aborting_the_review_keeps_the_draft_for_resume(
    home: Path, inputs: dict[str, Path], ai: tuple[FakeLLM, FakeTranscriber], editor: Path
) -> None:
    llm, _ = ai
    set_rounds(editor, [[]])
    (editor / "ed.py").write_text(
        (editor / "ed.py").read_text() + "\nPath(sys.argv[1]).write_text('')\n"
    )  # empties problem.yaml
    code, out = dsa("add", *map(str, inputs.values()), "--slug", "mp")
    assert code == 1 and "aborted" in out
    inbox = dsa_home() / "inbox" / "mp"
    assert (inbox / "draft" / "problem.yaml").exists() and not (problems_dir() / "mp").exists()
    # fix the editor and resume: nothing is transcribed or extracted twice
    (editor / "ed.py").write_text(EDITOR_SCRIPT.format(python=sys.executable))
    set_rounds(editor, [[]])
    calls_before = len(llm.calls)
    code, out = dsa("add", "--resume", "mp")
    assert code == 0, out
    new_tasks = [c.task for c in llm.calls[calls_before:]]
    assert "transcribe_image" not in new_tasks and "segment" not in new_tasks
    assert "extract_part" not in new_tasks
    assert (problems_dir() / "mp" / "problem.yaml").exists()
    # a finished inbox cannot be resumed again
    code, out = dsa("add", "--resume", "mp")
    assert code == 1 and "already built" in out


def test_resume_after_a_model_failure_skips_finished_stages(
    home: Path, inputs: dict[str, Path], ai: tuple[FakeLLM, FakeTranscriber], editor: Path
) -> None:
    llm, audio = ai
    full = list(llm.rules)
    llm.rules = [r for r in llm.rules if r.task != "extract_part"]  # the model "dies" here
    code, out = dsa("add", *map(str, inputs.values()), "--slug", "mp")
    assert code == 1 and "no recorded response" in out
    inbox = dsa_home() / "inbox" / "mp"
    assert len(list((inbox / "transcripts").glob("*.md"))) == 3
    assert (inbox / "segments.json").exists() and (inbox / "raw" / "01-1-statement.png").exists()
    llm.rules = full
    audio.calls.clear()
    before = len(llm.calls)
    code, out = dsa("add", "--resume", "mp")
    assert code == 0, out
    assert not audio.calls  # the voice note was not transcribed again
    assert "transcribe_image" not in [c.task for c in llm.calls[before:]]


def test_slug_collisions_and_unknown_inputs(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    editor: Path,
    tmp_path: Path,
) -> None:
    assert dsa("add", *map(str, inputs.values()), "--slug", "mp")[0] == 0
    code, out = dsa("add", str(inputs["code"]), "--slug", "mp")
    assert code == 1 and "already exists" in out
    weird = tmp_path / "notes.docx"
    weird.write_text("x")
    code, out = dsa("add", str(weird), "--slug", "other")
    assert code == 1 and "unsupported file type" in out
    code, out = dsa("add", "--slug", "empty")
    assert code == 1 and "nothing to add" in out


# ---------------------------------------------------------------- followup and ref


def add_part_one_only(editor: Path, inputs: dict[str, Path], llm: FakeLLM) -> None:
    seg = json.loads(json.dumps(recorded("segment")["response"]))
    seg["parts"] = seg["parts"][:1]
    seg["code"][0]["section"] = 2  # photo is section 1, the code file section 2
    llm.add(task="segment", response=seg)
    code, out = dsa("add", str(inputs["photo"]), str(inputs["code"]), "--slug", "mp")
    assert code == 0, out


def test_followup_appends_a_part_read_against_the_previous_one(
    home: Path, inputs: dict[str, Path], ai: tuple[FakeLLM, FakeTranscriber], editor: Path
) -> None:
    llm, _ = ai
    add_part_one_only(editor, inputs, llm)
    assert len(load_problem(problems_dir() / "mp").parts) == 1
    llm.add(task="segment", response=FOLLOWUP_SEGMENT)
    n = len(llm.calls)
    set_rounds(editor, [[], []])
    code, out = dsa("followup", "mp", str(inputs["voice"]))
    assert code == 0, out
    problem = load_problem(problems_dir() / "mp")
    assert [p.id for p in problem.parts] == [1, 2]
    assert problem.part(2).sig.method == "maxProductOfThree"
    new = llm.calls[n:]
    seg_call = next(c for c in new if c.task == "segment")
    assert "add follow-up" in seg_call.prompt and "1 part(s)" in seg_call.prompt
    ext_call = next(c for c in new if c.task == "extract_part")
    assert "Text of part 2" in ext_call.prompt and "follows part 1" in ext_call.prompt
    assert "maxProductOfTwo" in ext_call.prompt and "10^5" in ext_call.prompt
    # part 1 is untouched and still built; part 2 got its own brute and tests
    assert load_manifest(problems_dir() / "mp", 1) and load_manifest(problems_dir() / "mp", 2)
    assert (problems_dir() / "mp" / "raw" / "part2").is_dir()
    assert "REVIEW CHECKLIST" not in (problems_dir() / "mp" / "problem.yaml").read_text()
    # only the new part was put in front of the reviewer
    seen = (editor / "seen-1.txt").read_text() if (editor / "seen-1.txt").exists() else ""
    assert "--- part 2:" in seen or "--- part 2:" in (editor / "seen-0.txt").read_text()
    assert "--- part 1:" not in (editor / "seen-0.txt").read_text()


def test_followup_to_an_unknown_problem(
    home: Path, inputs: dict[str, Path], ai: tuple[FakeLLM, FakeTranscriber], editor: Path
) -> None:
    code, out = dsa("followup", "nope", str(inputs["voice"]))
    assert code == 1 and "no problem 'nope'" in out


def test_followup_that_cannot_be_built_leaves_the_problem_untouched(
    home: Path, inputs: dict[str, Path], ai: tuple[FakeLLM, FakeTranscriber], editor: Path
) -> None:
    llm, _ = ai
    add_part_one_only(editor, inputs, llm)
    before = (problems_dir() / "mp" / "problem.yaml").read_text()
    llm.add(task="segment", response=FOLLOWUP_SEGMENT)
    wrong = (
        "```python\nclass Solution:\n    def maxProductOfThree(self, nums):\n        return 0\n```"
    )
    llm.add(task="write_brute", response=wrong)  # never reproduces the examples
    set_rounds(editor, [[]])  # the reviewer changes nothing; the editor then fails on reopen
    code, out = dsa("followup", "mp", str(inputs["voice"]))
    assert code == 1 and "editor exited with an error" in out
    seen = (editor / "seen-0.txt").read_text()
    assert "could not finish" in seen and "neither a ref nor a brute" in seen
    assert (problems_dir() / "mp" / "problem.yaml").read_text() == before
    assert len(load_problem(problems_dir() / "mp").parts) == 1
    assert not (problems_dir() / "mp" / "part2").exists()
    # the draft is kept so the user can finish it
    assert (dsa_home() / "inbox" / "mp--followup" / "draft" / "part2" / "statement.md").exists()


def test_ref_attaches_and_agrees_with_the_oracle(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    editor: Path,
    tmp_path: Path,
) -> None:
    assert dsa("add", *map(str, inputs.values()), "--slug", "mp")[0] == 0
    cpp = tmp_path / "friend.cpp"
    cpp.write_text(REF_CPP)
    code, out = dsa("ref", "mp", str(cpp), "--part", "2")
    assert code == 0, out
    problem = load_problem(problems_dir() / "mp")
    ref = problem.part(2).refs[0]
    assert ref.lang == "cpp" and ref.origin == "friend" and ref.file.startswith("part2/ref/")
    m = load_manifest(problems_dir() / "mp", 2)
    assert m and m.oracle == "ref" and m.stress  # the ref was stress-tested against the brute
    yaml_before = (problems_dir() / "mp" / "problem.yaml").read_text()
    refs_before = sorted((problems_dir() / "mp" / "part2" / "ref").iterdir())
    # a ref that fails a sample is rejected at once
    bad = tmp_path / "bad.cpp"
    bad.write_text(REF_CPP.replace("max(a, b)", "a"))
    code, out = dsa("ref", "mp", str(bad), "--part", "2")
    assert code == 1 and "rejected" in out and "nothing was changed" in out
    assert "does not pass the samples" in out
    # a ref that passes the samples but disagrees with the oracle elsewhere shows the first
    # disagreement (it only goes wrong on arrays longer than six)
    subtle = tmp_path / "subtle.cpp"
    subtle.write_text(
        REF_CPP.replace("return max(a, b);", "return nums.size() > 6 ? a : max(a, b);")
    )
    code, out = dsa("ref", "mp", str(subtle), "--part", "2")
    assert code == 1 and "rejected" in out and "nothing was changed" in out
    assert "disagree" in out
    assert (problems_dir() / "mp" / "problem.yaml").read_text() == yaml_before
    assert sorted((problems_dir() / "mp" / "part2" / "ref").iterdir()) == refs_before
    # the language can be forced and unknown ones are refused
    assert dsa("ref", "mp", str(cpp), "--part", "2", "--lang", "rust")[0] == 1


# ---------------------------------------------------------------- transcribed code and OCR fixes

OCR_SLIP = REF_PY.replace("sorted(nums)", "sorted(nums")  # a dropped parenthesis
OCR_LOGIC_SLIP = REF_PY.replace("nums[-2]", "nums[-3]")  # 2 misread as 3


def photo_of_code(tmp_path: Path) -> Path:
    return render_text_image(
        ["class Solution:", "    def maxProductOfTwo(self, nums):"], tmp_path / "ref-photo.png"
    )


def ocr_llm(ai: tuple[FakeLLM, FakeTranscriber], read: str) -> FakeLLM:
    llm, _ = ai
    llm.add(task="transcribe_image", image="ref-photo.png", response="```python\n" + read + "```")
    llm.add(task="extract_code", response={"lang": "python", "code": read})
    llm.add(task="fix_ocr", response="```python\n" + REF_PY + "```")
    return llm


@pytest.mark.parametrize("slip", [OCR_SLIP, OCR_LOGIC_SLIP], ids=["syntax", "logic"])
def test_ocr_fix_is_shown_as_a_diff_and_applied_only_on_approval(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    editor: Path,
    tmp_path: Path,
    slip: str,
) -> None:
    llm = ocr_llm(ai, slip)
    assert dsa("add", *map(str, inputs.values()), "--slug", "mp")[0] == 0
    # a photo of the same solution with a transcription slip
    photo = photo_of_code(tmp_path)
    before = (problems_dir() / "mp" / "problem.yaml").read_text()
    # declined: nothing is applied
    code, out = dsa("ref", "mp", str(photo), "--part", "1", "--origin", "user", input="n\n")
    assert code == 1 and "declined" in out
    assert "--- part1/ref/ref_user1.py (transcribed)" in out and "+++" in out
    assert (problems_dir() / "mp" / "problem.yaml").read_text() == before
    assert not (problems_dir() / "mp" / "part1" / "ref" / "ref_user1.py").exists()
    # approved: the fix is applied, re-verified and the ref attached
    code, out = dsa("ref", "mp", str(photo), "--part", "1", "--origin", "user", input="y\n")
    assert code == 0, out
    attached = problems_dir() / "mp" / "part1" / "ref" / "ref_user1.py"
    assert attached.read_text() == REF_PY
    assert len([c for c in llm.calls if c.task == "fix_ocr"]) == 2


def test_a_correct_photographed_ref_needs_no_fix(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    editor: Path,
    tmp_path: Path,
) -> None:
    llm = ocr_llm(ai, REF_PY)
    assert dsa("add", *map(str, inputs.values()), "--slug", "mp")[0] == 0
    code, out = dsa("ref", "mp", str(photo_of_code(tmp_path)), "--part", "1")
    assert code == 0, out
    assert not [c for c in llm.calls if c.task == "fix_ocr"]


def test_a_broken_ref_file_is_not_fixed_by_the_model(
    home: Path,
    inputs: dict[str, Path],
    ai: tuple[FakeLLM, FakeTranscriber],
    editor: Path,
    tmp_path: Path,
) -> None:
    """Only code read from a photo or voice note gets AI repair proposals."""
    llm, _ = ai
    assert dsa("add", *map(str, inputs.values()), "--slug", "mp")[0] == 0
    broken = tmp_path / "broken.py"
    broken.write_text(OCR_LOGIC_SLIP)
    code, out = dsa("ref", "mp", str(broken), "--part", "1")
    assert code == 1 and "does not pass the samples" in out
    assert not [c for c in llm.calls if c.task == "fix_ocr"]


# ---------------------------------------------------------------- sources, images, transcript


def test_sources_are_classified_and_ordered(tmp_path: Path) -> None:
    files = {}
    for name in ("IMG_10.png", "IMG_2.png", "note.m4a", "a.cc", "b.java", "c.py", "t.txt", "u.md"):
        p = tmp_path / name
        p.write_bytes(b"x")
        files[name] = p
    kinds = {n: classify(p).kind for n, p in files.items()}
    assert kinds == {
        "IMG_10.png": "image",
        "IMG_2.png": "image",
        "note.m4a": "audio",
        "a.cc": "code",
        "b.java": "code",
        "c.py": "code",
        "t.txt": "text",
        "u.md": "text",
    }
    assert classify(files["a.cc"]).lang == "cpp" and classify(files["b.java"]).lang == "java"
    names = [p.name for p in order_sources(list(files.values()))]
    assert names.index("IMG_2.png") < names.index("IMG_10.png")  # numbers compare as numbers
    assert natural_key("a2") < natural_key("a10")
    with pytest.raises(IntakeError, match=r"unsupported file type \.tiff"):
        classify(tmp_path / "x.tiff")


def test_collect_keeps_originals_and_texts(tmp_path: Path) -> None:
    a = tmp_path / "b.txt"
    a.write_text("hello")
    recs = collect(tmp_path / "inbox" / "raw", [a], [("clipboard.txt", "from clip")])
    assert [r.name for r in recs] == ["01-b.txt", "02-clipboard.txt"]
    assert (tmp_path / "inbox" / "raw" / "01-b.txt").read_text() == "hello"
    assert recs[1].kind == "text" and recs[0].sha256 != recs[1].sha256


def test_exif_time_breaks_name_ties(tmp_path: Path) -> None:
    from PIL import Image

    a, b = tmp_path / "x", tmp_path / "y"
    a.mkdir()
    b.mkdir()
    early, late = a / "p.png", b / "p.png"
    for path, stamp in ((early, "2026:01:01 10:00:00"), (late, "2026:01:01 11:00:00")):
        im = Image.new("RGB", (4, 4), "white")
        exif = Image.Exif()
        exif[36867] = stamp
        im.save(path, exif=exif)
    assert order_sources([late, early]) == [early, late]


def test_large_photos_are_downscaled_and_normalized(tmp_path: Path) -> None:
    from PIL import Image

    big = tmp_path / "big.jpg"
    Image.new("RGB", (3200, 2400), "white").save(big)
    out = prepare_image(big, tmp_path / "work" / "big.png", 1600)
    with Image.open(out) as im:
        assert im.format == "PNG" and max(im.size) == 1600 and im.size == (1600, 1200)


def test_heic_photos_are_converted(tmp_path: Path) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    pillow_heif.register_heif_opener()
    src = tmp_path / "shot.heic"
    try:
        Image.new("RGB", (64, 48), "white").save(src, format="HEIF")
    except Exception as e:  # no HEIF encoder in this build
        pytest.skip(f"cannot write HEIC here: {e}")
    out = prepare_image(src, tmp_path / "shot.png", 1600)
    with Image.open(out) as im:
        assert im.size == (64, 48)


def test_transcript_sections_and_code_blocks_keep_indentation() -> None:
    from dsajudge.intake.sources import SourceRec

    recs = [
        SourceRec(n=1, name="01-a.png", original="a.png", kind="image", sha256="x"),
        SourceRec(n=2, name="02-c.py", original="c.py", kind="code", lang="python", sha256="y"),
    ]
    code = "```python\nclass A:\n    def f(self):\n        return 1\n```"
    text = assemble(recs, {1: "Statement\n\n```cpp\nint x;\n```", 2: code})
    sections = parse_sections(text)
    assert set(sections) == {1, 2}
    assert code_blocks(sections[1]) == [("cpp", "int x;")]
    assert code_blocks(sections[2]) == [("python", "class A:\n    def f(self):\n        return 1")]


def test_clipboard_and_typed_text_are_sources(
    home: Path, ai: tuple[FakeLLM, FakeTranscriber], editor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, _ = ai
    from dsajudge import cli

    monkeypatch.setattr(cli, "read_clipboard", lambda: "Clipboard statement text")
    seg = json.loads(json.dumps(recorded("segment")["response"]))
    seg["parts"], seg["code"] = seg["parts"][:1], []
    llm.add(task="segment", response=seg)
    llm.add(task="write_brute", response=PAIR_BRUTE)
    code, out = dsa("add", "--clip", "--slug", "clip-problem")
    assert code == 0, out
    raw = dsa_home() / "inbox" / "clip-problem" / "raw"
    assert (raw / "01-clipboard.txt").read_text() == "Clipboard statement text"
    assert "Clipboard statement text" in next(c for c in llm.calls if c.task == "segment").prompt
    m = load_manifest(problems_dir() / "clip-problem", 1)
    assert m and m.oracle == "brute-only"

    # --text opens the editor on an empty file; whatever is saved becomes a source
    set_rounds(editor, [[["", "Typed statement text"]], []])
    monkeypatch.setattr(cli, "read_clipboard", lambda: None)
    code, out = dsa("add", "--text", "--slug", "typed-problem")
    assert code == 0, out
    typed = dsa_home() / "inbox" / "typed-problem" / "raw" / "01-typed.md"
    assert typed.read_text() == "Typed statement text"
    # an empty clipboard is reported, not silently ignored
    code, out = dsa("add", "--clip", "--slug", "x")
    assert code == 2 and "clipboard is empty" in out
