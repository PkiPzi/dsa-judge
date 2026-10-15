"""fence_code: bare code in a photo transcript gets fenced; prose never does."""

from __future__ import annotations

from dsajudge.intake.codefence import fence_code, guess_lang

STATEMENT = (
    "Sum of even numbers\n"
    "Given an integer array nums, return the sum of its even elements.\n"
    "Constraints:\n"
    "1 <= nums.length <= 10^5\n"
    "Example 1:  Input: nums = [1, 2, 3, 4]   Output: 6"
)
CPP = "class Solution {\npublic:\n    int f(vector<int>& a) {\n        return a[0];\n    }\n};"
PY = "class Solution:\n    def f(self, a):\n        total = 0\n        return total"
JAVA = "class Solution {\n    public int f(int[] a) {\n        return a[0];\n    }\n}"


def test_prose_is_left_alone() -> None:
    assert fence_code(STATEMENT) == STATEMENT


def test_bare_code_is_fenced_with_its_language() -> None:
    assert fence_code(CPP) == f"```cpp\n{CPP}\n```"
    assert fence_code(PY) == f"```python\n{PY}\n```"
    assert fence_code(JAVA) == f"```java\n{JAVA}\n```"


def test_statement_then_code_only_fences_the_code() -> None:
    out = fence_code(STATEMENT + "\n" + CPP)
    assert out == f"{STATEMENT}\n```cpp\n{CPP}\n```"


def test_a_page_that_starts_in_the_middle_of_a_listing() -> None:
    tail = "        return s;\n    }\n};\nCount vowels\nGiven a string, count the vowels in it."
    out = fence_code(tail)
    assert out.startswith("```cpp\n        return s;\n    }\n};\n```\nCount vowels")


def test_existing_fences_are_trusted() -> None:
    text = f"```cpp\n{CPP}\n```\nbare = 1;\nmore = 2;"
    assert fence_code(text) == text


def test_one_stray_code_looking_line_is_not_enough() -> None:
    assert (
        fence_code("Note that x = 3;\nThe rest is prose.") == "Note that x = 3;\nThe rest is prose."
    )


def test_guess_lang_defaults() -> None:
    assert guess_lang("x = 1") == "python"
    assert guess_lang("int x = 1;") == "cpp"
