"""Several photos of one question: overlaps are dropped, a split code listing is joined."""

from __future__ import annotations

from pathlib import Path

from dsajudge.intake.sources import SourceRec
from dsajudge.intake.stitch import block_is_open, drop_overlap, join_code, stitch_pages
from dsajudge.intake.transcribe import code_blocks


def rec(n: int, kind: str = "image") -> SourceRec:
    return SourceRec(n=n, name=f"{n}.png", original=f"p{n}.png", kind=kind, sha256="x")  # type: ignore[arg-type]


def test_overlapping_lines_are_dropped() -> None:
    a = (
        "Given an array nums of n integers\n"
        "return the largest sum of a contiguous subarray.\nConstraints:"
    )
    b = "return the largest sum of a contiguous subarray.\nConstraints:\n1 <= n <= 10^5\nExample 1:"
    out, dropped = drop_overlap(a, b)
    assert dropped == 2
    assert out == "1 <= n <= 10^5\nExample 1:"


def test_whitespace_differences_do_not_hide_an_overlap() -> None:
    out, dropped = drop_overlap("x\n  return   a + b;\n", "return a + b;\nnext line")
    assert dropped == 0  # one short line is not trusted ...
    out, dropped = drop_overlap(
        "x\nthe quick brown fox jumps over the lazy dog\n",
        "the  quick brown fox   jumps over the lazy dog\nnext",
    )
    assert dropped == 1 and out == "next"  # ... but a long distinctive one is


def test_short_single_line_repeats_are_kept() -> None:
    out, dropped = drop_overlap("a\n}", "}\nint y;")
    assert dropped == 0 and out == "}\nint y;"


def test_unrelated_pages_are_untouched() -> None:
    out, dropped = drop_overlap("first page text here", "second page entirely different")
    assert dropped == 0 and out == "second page entirely different"


def test_overlap_inside_a_code_block_ignores_the_fences() -> None:
    a = "```cpp\nint f(int x) {\n    int y = x * 2;\n    int z = y + 1;\n```"
    b = "```cpp\n    int y = x * 2;\n    int z = y + 1;\n    return z;\n}\n```"
    out, dropped = drop_overlap(a, b)
    assert dropped == 2
    assert out == "```cpp\n    return z;\n}\n```"


def test_block_is_open() -> None:
    assert block_is_open("cpp", "int f() {\n  if (x) {")
    assert not block_is_open("cpp", "int f() { return 1; }")
    assert not block_is_open("cpp", 'string s = "{";')  # braces in strings and comments
    assert not block_is_open("cpp", "int a; // }{{")
    assert block_is_open("python", "def f(x):")
    assert block_is_open("python", "y = foo(1,\n")
    assert not block_is_open("python", "def f(x):\n    return x")


def test_split_cpp_listing_is_joined() -> None:
    a = "Reference:\n```cpp\nclass Solution {\npublic:\n    int f(int x) {\n        int y = x;\n```"
    b = "```cpp\n        return y;\n    }\n};\n```\nNotes after."
    new_a, new_b, joined = join_code(a, b)
    assert joined
    assert new_b == "Notes after."
    ((lang, code),) = code_blocks(new_a)
    assert lang == "cpp"
    assert code.endswith("        return y;\n    }\n};")
    assert code.count("{") == code.count("}")  # one complete class


def test_split_python_listing_keeps_indentation() -> None:
    a = (
        "```python\nclass Solution:\n    def f(self, nums):\n"
        "        best = 0\n        for x in nums:\n```"
    )
    b = "```python\n            best = max(best, x)\n        return best\n```"
    new_a, new_b, joined = join_code(a, b)
    assert joined and new_b == ""
    ((_, code),) = code_blocks(new_a)
    compile(code, "<joined>", "exec")  # valid Python again
    assert "            best = max(best, x)" in code


def test_complete_blocks_are_not_merged() -> None:
    a = "```cpp\nint f() { return 1; }\n```"
    b = "```cpp\nint g() { return 2; }\n```"
    new_a, new_b, joined = join_code(a, b)
    assert not joined and (new_a, new_b) == (a, b)
    # different languages never join, even if the first looks open
    _, _, joined = join_code("```cpp\nint f() {\n```", "```python\nx = 1\n```")
    assert not joined


def test_stitch_pages_only_touches_adjacent_images() -> None:
    sections = {
        1: "Statement line one is long enough to be distinctive here\nline two of the statement",
        2: "line two of the statement\nConstraints: 1 <= n <= 10^5",
        3: "voice note text",
        4: "line two of the statement\nsomething else",
    }
    recs = [rec(1), rec(2), rec(3, "audio"), rec(4)]
    res = stitch_pages(recs, sections)
    # the repeated line is short, so it is not trusted as an overlap
    assert res.sections[2] == sections[2]
    assert res.sections[3] == sections[3]
    assert res.sections[4] == sections[4]  # not adjacent to another image: untouched
    assert res.sections[1] == sections[1]


def test_stitch_pages_reports_what_it_did_and_is_idempotent() -> None:
    sections = {
        1: "Intro of the question\nThe array nums holds n integers between -10^4 and 10^4.",
        2: "The array nums holds n integers between -10^4 and 10^4.\nReturn the maximum.",
        3: "```java\nclass Solution {\n    int f(int[] a) {\n```",
        4: "```java\n        return a[0];\n    }\n}\n```",
    }
    recs = [rec(i) for i in (1, 2, 3, 4)]
    res = stitch_pages(recs, sections)
    assert res.sections[2] == "Return the maximum."
    assert res.sections[4] == ""
    assert "return a[0];" in res.sections[3] and res.sections[3].count("```") == 2
    assert len(res.notes) == 2  # the 1-2 overlap and the 3-4 code join
    again = stitch_pages(recs, sections)
    assert again.sections == res.sections  # deterministic from the raw per-photo transcripts


# ------------------------------------------------------------------ through the pipeline

PAGE1 = (
    "Maximum product of two numbers\n"
    "Given an integer array nums, return the largest product of two elements.\n"
    "Constraints:\n2 <= nums.length <= 10^5\n\n"
    "Reference solution:\n"
    "```cpp\nclass Solution {\npublic:\n    long long f(vector<int>& nums) {\n"
    "        sort(nums.begin(), nums.end());\n```"
)
PAGE2 = (
    "```cpp\n        sort(nums.begin(), nums.end());\n"
    "        return 1LL * nums.back() * nums[nums.size() - 2];\n    }\n};\n```\n"
    "Example 1: nums = [3, 5, -2, 4] -> 20"
)


def test_two_photos_of_one_question_become_one_transcript(tmp_path: Path) -> None:
    from dsajudge.ai import factory
    from dsajudge.ai.fake import FakeLLM, FakeTranscriber
    from dsajudge.ai.synth import render_text_image
    from dsajudge.config import Config
    from dsajudge.intake.pipeline import Env, State, _transcribe
    from dsajudge.intake.sources import collect
    from dsajudge.intake.transcribe import code_blocks, parse_sections
    from dsajudge.judge.limits import detect_backend

    pics = tmp_path / "pics"
    pics.mkdir()
    p1 = render_text_image(["page one"], pics / "page1.png")
    p2 = render_text_image(["page two"], pics / "page2.png")
    llm = FakeLLM(
        [
            {"task": "transcribe_image", "image": "page1.png", "response": PAGE1},
            {"task": "transcribe_image", "image": "page2.png", "response": PAGE2},
        ]
    )
    env = Env(
        cfg=Config(),
        providers=factory.FixedProviders(llm, FakeTranscriber()),
        backend=detect_backend("auto"),
    )
    inbox = tmp_path / "inbox"
    recs = collect(inbox / "raw", [p2, p1], [])  # given out of order: name order wins
    state = State(key="k", mode="add", sources=recs, done=["collect"])

    sections = _transcribe(env, inbox, state)

    assert [c.images[0].name for c in llm.calls] == ["01-page1.png", "02-page2.png"]  # one each
    ((lang, code),) = code_blocks(sections[1])  # the listing is one block, in the first photo
    assert lang == "cpp" and code.count("sort(") == 1  # the repeated line was dropped
    assert code.rstrip().endswith("};") and code.count("{") == code.count("}")
    assert "Example 1" in sections[2] and "```" not in sections[2]
    assert any("overlap" in n for n in state.notes) and any("joined" in n for n in state.notes)
    transcript = (inbox / "transcript.md").read_text()
    assert parse_sections(transcript) == sections
    # the per-photo transcripts are kept untouched, so a resume stitches the same way
    assert (inbox / "transcripts" / "02.md").read_text() == PAGE2
    assert _transcribe(env, inbox, state) == sections and len(llm.calls) == 2
