from __future__ import annotations

from pathlib import Path

import pytest

from dsajudge.judge.checkers import CheckerError, get_checker
from dsajudge.model.canon import (
    args_to_text,
    canon_equal,
    first_difference,
    read_args,
    text_to_args,
)


def test_args_roundtrip(tmp_path: Path) -> None:
    args = [[1, -2, 3], 'a"b\n', 2**62 + 1, None, [[1], []]]
    text = args_to_text(args)
    assert text.count("\n") == len(args)
    p = tmp_path / "x.in.json"
    p.write_text(text)
    assert read_args(p) == args
    # big integers stay exact (no float round trip)
    assert text_to_args("9007199254740993\n") == [9007199254740993]


def test_leetcode_custom_case_format() -> None:
    assert text_to_args("[2,7,11,15]\n9\n") == [[2, 7, 11, 15], 9]
    with pytest.raises(ValueError):
        text_to_args("[1,2\n")


def test_canon_equal() -> None:
    assert canon_equal(1, 1.0)
    assert not canon_equal(True, 1)
    assert not canon_equal(1, True)
    assert canon_equal([1, [2, "x"]], [1, [2, "x"]])
    assert not canon_equal([1, 2], [1, 2, 3])
    assert not canon_equal("1", 1)
    assert canon_equal(None, None)


def test_first_difference() -> None:
    assert first_difference([1, 2, 3], [1, 5, 3]) == "[1]: got 5, expected 2"
    assert "length" in (first_difference([1, 2], [1, 2, 3]) or "")
    assert first_difference(4, 4) is None


def test_checkers() -> None:
    assert get_checker("exact")([1, 2], [1, 2])
    assert not get_checker("exact")([1, 2], [2, 1])
    assert get_checker("unordered")([1, 2, 2], [2, 1, 2])
    assert not get_checker("unordered")([1, 2, 2], [2, 1, 1])
    assert get_checker("unordered_deep")([[1, 2], [3]], [[3], [2, 1]])
    assert not get_checker("unordered")([[1, 2], [3]], [[3], [2, 1]])
    f = get_checker("float:1e-6")
    assert f(0.1 + 0.2, 0.3)
    assert f([1.0, 2.0], [1.0000001, 2.0])
    assert not f(1.0, 1.001)
    with pytest.raises(CheckerError):
        get_checker("custom")
