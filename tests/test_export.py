"""Export of stdio parts to HackerRank / HackerEarth test-case zips."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsajudge import export
from dsajudge.cli import app

from .conftest import simple_part, write_problem
from .test_stdio import BRUTE_PY, REF_CPP, stdio_problem

runner = CliRunner()


def dsa(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def et(group: str, i: int = 0) -> export.ExportTest:
    return export.ExportTest(f"{group}/{i:02d}", group, "1\n", "1\n")


# ------------------------------------------------------------------ units


def test_clean_text() -> None:
    assert export.clean_text("a  \r\nb\t\n\n\n") == "a\nb\n"
    assert export.clean_text("x") == "x\n"


def test_input_rules_per_platform() -> None:
    he, hr = export.FORMATS["hackerearth"], export.FORMATS["hackerrank"]
    assert export.input_problem(he, "3\n1 2 3\n") is None
    assert "empty line" in (export.input_problem(he, "0\n\n5\n") or "")
    assert "whitespace" in (export.input_problem(he, " 3\n1\n") or "")
    assert export.input_problem(hr, "0\n\n5\n") is None


def test_file_names() -> None:
    hr, he = export.FORMATS["hackerrank"], export.FORMATS["hackerearth"]
    assert export.file_names(hr, 3, 10) == ("input03.txt", "output03.txt")
    assert export.file_names(he, 0, 10) == ("in00.txt", "out00.txt")
    assert export.file_names(he, 7, 150) == ("in007.txt", "out007.txt")  # numbers stay aligned


def test_scores_follow_hackerearth_example() -> None:
    # 2 easy + 3 medium + 5 hard = 5 / 5 / 15 each, as in HackerEarth's own guide
    tests = (
        [et("sample", i) for i in range(2)]
        + [et("small_random", i) for i in range(3)]
        + [et("max", i) for i in range(5)]
    )
    export.assign_scores(tests)
    assert [t.score for t in tests] == [5, 5, 5, 5, 5, 15, 15, 15, 15, 15]
    odd = [et("edge", i) for i in range(3)]
    export.assign_scores(odd)
    assert sum(t.score or 0 for t in odd) == 100


def test_select_keeps_samples_and_spreads_groups() -> None:
    pool = (
        [et("sample", i) for i in range(2)]
        + [et("edge", i) for i in range(18)]
        + [et("small_random", i) for i in range(40)]
        + [et("medium_random", i) for i in range(10)]
        + [et("max", i) for i in range(6)]
    )
    assert export.select(pool, None) is pool
    got = export.select(pool, 10)
    assert len(got) == 10
    assert [t.dsa_id for t in got[:2]] == ["sample/00", "sample/01"]
    assert {t.group for t in got} == {"sample", "edge", "small_random", "medium_random", "max"}
    assert len({t.dsa_id for t in got}) == 10
    got = export.select(pool, 15)
    assert len(got) == 15 and sum(t.group == "max" for t in got) >= 5
    assert sum(t.group == "max" for t in export.select(pool, 10)) == 3
    assert [t.group for t in export.select(pool, 1)] == ["sample"]
    # asking for more than exists returns everything
    assert len(export.select(pool, 500)) == len(pool)


def test_select_with_missing_groups() -> None:
    pool = [et("sample"), *[et("small_random", i) for i in range(5)]]
    assert len(export.select(pool, 4)) == 4
    assert len(export.select([et("sample")], 3)) == 1


def test_zip_is_flat_and_deterministic(tmp_path: Path) -> None:
    files = [("input00.txt", "1\n"), ("output00.txt", "2\n")]
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    export.write_zip(a, files)
    export.write_zip(b, files)
    assert a.read_bytes() == b.read_bytes()
    with zipfile.ZipFile(a) as zf:
        assert zf.namelist() == ["input00.txt", "output00.txt"]
        assert zf.read("output00.txt") == b"2\n"
        assert all(not i.is_dir() and "/" not in i.filename for i in zf.infolist())


# ------------------------------------------------------------------ end to end


def read_zip(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n).decode() for n in zf.namelist()}


def solve(text: str) -> str:
    nums = text.split()
    return str(sum(int(x) for x in nums[1 : 1 + int(nums[0])]))


@pytest.fixture
def built(home: Path) -> Path:
    path = stdio_problem(home)
    code, out = dsa("build", "stdio-sum")
    assert code == 0, out
    return path


def test_export_hackerrank_and_hackerearth(built: Path, home: Path) -> None:
    code, out = dsa("export", "stdio-sum", "--format", "both", "--count", "10")
    assert code == 0, out
    d = home / "export" / "stdio-sum"
    for fmt, i_pre, o_pre, tier in (
        ("hackerrank", "input", "output", "HackerRank"),
        ("hackerearth", "in", "out", "HackerEarth"),
    ):
        files = read_zip(d / f"stdio-sum-part1-{fmt}.zip")
        names = list(files)
        assert len(names) == 20
        for i in range(10):
            assert f"{i_pre}{i:02d}.txt" in files and f"{o_pre}{i:02d}.txt" in files
        for i in range(10):
            inp, outp = files[f"{i_pre}{i:02d}.txt"], files[f"{o_pre}{i:02d}.txt"]
            assert outp == solve(inp) + "\n"  # every answer matches what the ref computes
            assert inp.endswith("\n") and not inp.endswith("\n\n")
            assert all(ln == ln.rstrip() for ln in inp.splitlines())
        # the two samples from problem.yaml come first (visible to candidates)
        assert files[f"{i_pre}00.txt"] == "3\n1 2 3\n" and files[f"{o_pre}00.txt"] == "6\n"
        assert files[f"{i_pre}01.txt"] == "1\n-5\n"
        notes = (d / f"stdio-sum-part1-{fmt}.notes.txt").read_text()
        assert tier in notes and "sample" in notes
    notes = (d / "stdio-sum-part1-hackerearth.notes.txt").read_text()
    scores = [
        int(m)
        for m in re.findall(r"^in\d+\.txt\s+\S+\s+\S+\s+(?:yes|no)\s+(\d+)\s", notes, re.MULTILINE)
    ]
    assert len(scores) == 10
    assert sum(scores) == 100


def test_export_is_reproducible(built: Path, home: Path, tmp_path: Path) -> None:
    dsa("export", "stdio-sum", "--out", str(tmp_path / "one"))
    dsa("export", "stdio-sum", "--out", str(tmp_path / "two"))
    a = (tmp_path / "one" / "stdio-sum-part1-hackerrank.zip").read_bytes()
    assert a == (tmp_path / "two" / "stdio-sum-part1-hackerrank.zip").read_bytes()


def test_export_all_includes_max_tests(built: Path, home: Path) -> None:
    code, out = dsa("export", "stdio-sum", "--count", "all", "-f", "hackerearth")
    assert code == 0 and "HackerEarth recommends exactly 10" in out
    files = read_zip(home / "export" / "stdio-sum" / "stdio-sum-part1-hackerearth.zip")
    assert len(files) > 100
    assert max(len(v) for v in files.values()) > 500  # a max-size input is in there


def test_hackerearth_skips_inputs_with_blank_lines(home: Path) -> None:
    ref = REF_CPP.replace("while (n--)", "int k; while (n--)").replace(
        "cout << s", "int extra; cin >> extra; cout << s + extra"
    )
    # nums may be empty, which renders an empty line between n and k
    part = simple_part(
        1,
        "solve",
        [("nums", "int[]"), ("k", "int")],
        "long",
        [([[1, 2], 3], 6)],
        mode="stdio",
        input_format="{len(nums)}\n{nums}\n{k}",
        constraints={"nums": {"len": [0, 5], "each": [-9, 9]}, "k": {"range": [0, 9]}},
        refs=[{"lang": "cpp", "file": "part1/ref/ref.cpp", "origin": "user", "verified": True}],
    )
    ref = (
        "#include <bits/stdc++.h>\nusing namespace std;\nint main(){int n;cin>>n;long long s=0;"
        "while(n--){long long x;cin>>x;s+=x;}long long k;cin>>k;cout<<s+k<<endl;}\n"
    )
    write_problem(
        home / "problems",
        {"schema": 1, "slug": "blank-lines", "title": "Blank", "parts": [part]},
        {"part1/ref/ref.cpp": ref, "part1/brute.py": BRUTE_PY},
    )
    code, out = dsa("build", "blank-lines")
    assert code == 0, out
    code, out = dsa("export", "blank-lines", "-f", "both", "--count", "all")
    assert code == 0, out
    he = read_zip(home / "export" / "blank-lines" / "blank-lines-part1-hackerearth.zip")
    hr = read_zip(home / "export" / "blank-lines" / "blank-lines-part1-hackerrank.zip")
    assert all("\n\n" not in v for n, v in he.items() if n.startswith("in"))
    assert any("\n\n" in v for n, v in hr.items() if n.startswith("input"))
    assert len(he) < len(hr)
    assert "empty line" in out


def test_export_errors(home: Path) -> None:
    code, out = dsa("export", "max-subarray")
    assert code == 2 and "no stdio part" in out
    code, out = dsa("export", "max-subarray", "--part", "1")
    assert code == 1 and "function mode" in out
    stdio_problem(home)
    code, out = dsa("export", "stdio-sum")
    assert code == 1 and "no built tests" in out
    dsa("build", "stdio-sum")
    code, out = dsa("export", "stdio-sum", "--format", "codeforces")
    assert code == 2 and "unknown format" in out
    code, out = dsa("export", "stdio-sum", "--count", "lots")
    assert code == 2 and "--count" in out
    code, out = dsa("export", "stdio-sum", "--part", "4")
    assert code == 2 and "parts 1..1" in out
