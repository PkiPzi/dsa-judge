"""Write large manual test inputs for the max-subarray fixture (Phase 1 stand-in for the generator).

Usage: python tests/make_large_cases.py PROBLEM_DIR [--n 100000]

Only inputs are written (partN/tests/manual/large-*.in.json). Expected outputs are never
written by hand: the judge computes them by running the part's refs.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

LO, HI = -10_000, 10_000


def cases(n: int, seed: int) -> dict[str, list[int]]:
    rng = random.Random(seed)
    return {
        "large-01-random": [rng.randint(LO, HI) for _ in range(n)],
        "large-02-all-min": [LO] * n,
        "large-03-all-max": [HI] * n,
        "large-04-alternating": [HI if i % 2 == 0 else LO + 1 for i in range(n)],
    }


def write_cases(problem_dir: Path, parts: list[int], n: int = 100_000, seed: int = 1) -> list[Path]:
    written = []
    for part in parts:
        d = problem_dir / f"part{part}" / "tests" / "manual"
        d.mkdir(parents=True, exist_ok=True)
        for name, nums in cases(n, seed + part).items():
            path = d / f"{name}.in.json"
            path.write_text(json.dumps(nums, separators=(",", ":")) + "\n", encoding="utf-8")
            written.append(path)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("problem_dir", type=Path)
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--parts", type=int, nargs="+", default=[1, 2, 3])
    args = ap.parse_args()
    for p in write_cases(args.problem_dir, args.parts, args.n):
        print(p)


if __name__ == "__main__":
    main()
