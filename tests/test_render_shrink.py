"""stdio rendering, token checkers and the counterexample shrinker (no toolchains needed)."""

from __future__ import annotations

from typing import Any

import pytest

from dsajudge.model.render import (
    FormatError,
    check_format,
    first_token_difference,
    get_text_checker,
    normalize_output,
    render_input,
    render_output,
)
from dsajudge.model.schema import Part
from dsajudge.oracle.shrink import candidates, shrink


def stdio_part(params: list[tuple[str, str]], returns: str, fmt: str, **extra: Any) -> Part:
    return Part.model_validate(
        {
            "id": 1,
            "title": "t",
            "statement": "s.md",
            "mode": "stdio",
            "signature": {
                "method": "solve",
                "params": [{"name": n, "type": t} for n, t in params],
                "returns": returns,
            },
            "input_format": fmt,
            **extra,
        }
    )


def test_render_input_layouts() -> None:
    p = stdio_part(
        [("n", "int"), ("a", "int[]"), ("g", "char[][]"), ("m", "int[][]"), ("s", "string")],
        "long",
        "{n}\n{a}\n{a:lines}\n{g:concat}\n{m}\n{s}\n{len(a)} {n - 1}",
    )
    text = render_input(p, [3, [1, 2, 3], [["a", "b"], ["c", "d"]], [[1, 2], [3, 4]], "hey"])
    assert text == "3\n1 2 3\n1\n2\n3\nab\ncd\n1 2\n3 4\nhey\n3 2\n"


def test_render_scalars_and_braces() -> None:
    p = stdio_part([("x", "double"), ("b", "bool")], "bool", "{{literal}} {x} {b}")
    assert render_input(p, [0.5, True]) == "{literal} 0.5 1\n"
    assert render_input(p, [1e-7, False]).startswith("{literal} 0.0000001 0")


def test_render_output_default_and_format() -> None:
    p = stdio_part([("x", "int")], "int[]", "{x}")
    assert render_output(p, [1, 2, 3]) == "1 2 3"
    q = stdio_part([("x", "int")], "bool", "{x}", output_format="{result}\nend")
    assert render_output(q, True) == "1\nend"


def test_normalize_output() -> None:
    assert normalize_output("1 2  \r\n3\n\n\n") == "1 2\n3"
    assert normalize_output("") == ""


@pytest.mark.parametrize(
    ("bad", "match"),
    [("{nope}", "unknown name"), ("{n:wide}", "unknown layout"), ("{n +}", "n")],
)
def test_check_format_errors(bad: str, match: str) -> None:
    with pytest.raises(FormatError, match=match):
        check_format(bad, ["n"], "input_format")


def test_text_checkers() -> None:
    exact = get_text_checker("exact")
    assert exact("1 2\n3", "1   2 3\n")
    assert not exact("1 2 3", "1 2 4")
    assert not exact("1 2 3", "1 2")
    flt = get_text_checker("float:1e-6")
    assert flt("1.0000001 2", "1.0 2")
    assert not flt("1.01 2", "1.0 2")
    assert flt("abc 1", "abc 1.0")
    assert not flt("abc 1", "abd 1")
    assert get_text_checker("unordered")("3 1 2", "1 2 3")
    assert first_token_difference("1 2 3", "1 9 3") == "token 2: got '9', expected '2'"
    assert first_token_difference("1 2 3", "1 2") == "got 2 token(s), expected 3"
    with pytest.raises(FormatError):
        get_text_checker("custom")


def test_stdio_needs_signature_and_format() -> None:
    with pytest.raises(ValueError, match="needs input_format"):
        Part.model_validate(
            {
                "id": 1,
                "title": "t",
                "statement": "s.md",
                "mode": "stdio",
                "signature": {"method": "f", "params": [], "returns": "int"},
            }
        )
    with pytest.raises(ValueError, match="needs a signature"):
        Part.model_validate(
            {"id": 1, "title": "t", "statement": "s.md", "mode": "stdio", "input_format": "x"}
        )


# ---------------------------------------------------------------- shrinker


def test_candidates_are_simpler_and_unique() -> None:
    args = [[5, 6, 7, 8], 4]
    cands = candidates(args)
    assert cands and args not in cands
    assert len({str(c) for c in cands}) == len(cands)
    assert any(c[0] == [5, 6] for c in cands)  # halves
    assert any(c[0] == [6, 7, 8] for c in cands)  # one element removed
    assert any(c[1] == 2 for c in cands)  # scalar halved


def test_shrink_finds_minimal_failing_array() -> None:
    # "fails" when some element is >= 100 and the array has at least 2 elements
    def fails(cs: list[list[Any]]) -> list[bool]:
        return [len(c[0]) >= 2 and max(c[0]) >= 100 for c in cs]

    def valid(cs: list[list[Any]]) -> list[bool]:
        return [len(c[0]) >= 1 for c in cs]

    out = shrink([[3, 250, 9, 41, 7, 8, 1000]], valid, fails)
    assert len(out[0]) == 2
    assert max(out[0]) >= 100
    assert min(out[0]) == 0  # the small element was simplified to zero


def test_shrink_respects_validity_of_parallel_arrays() -> None:
    # len(a) == len(b) is required: shrinking one array alone would be invalid
    def valid(cs: list[list[Any]]) -> list[bool]:
        return [len(c[0]) == len(c[1]) and len(c[0]) >= 1 for c in cs]

    def fails(cs: list[list[Any]]) -> list[bool]:
        return [sum(c[0]) + sum(c[1]) >= 10 for c in cs]

    out = shrink([[1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]], valid, fails)
    assert len(out[0]) == len(out[1])
    assert len(out[0]) <= 2
    assert sum(out[0]) + sum(out[1]) >= 10


def test_shrink_matrix_and_strings() -> None:
    def fails(cs: list[list[Any]]) -> list[bool]:
        return ["z" in c[0] and any(1 in row for row in c[1]) for c in cs]

    out = shrink(["abzcd", [[0, 0, 1], [2, 3, 4]]], lambda cs: [True] * len(cs), fails)
    assert out[0] == "z"
    assert out[1] == [[1]]
