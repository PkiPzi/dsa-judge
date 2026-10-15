"""Deterministic randomness that does not depend on the Python version.

``random.Random`` guarantees the Mersenne Twister stream (``random()``, ``getrandbits()``)
but not the algorithms of ``randrange``, ``shuffle`` or ``sample``, which have changed between
releases. Test seeds must reproduce byte-identical inputs on any machine, so everything here is
built on ``getrandbits`` only.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import MutableSequence, Sequence
from typing import TypeVar

T = TypeVar("T")


def seed_for(slug: str, part: int, group: str, index: int) -> int:
    """seed = H(slug, part, group, index) (PLAN.md §6.2)."""
    h = hashlib.sha256(f"{slug}\x00{part}\x00{group}\x00{index}".encode()).digest()
    return int.from_bytes(h[:8], "big")


class Rng:
    def __init__(self, seed: int) -> None:
        self._r = random.Random(seed)

    def bits(self, k: int) -> int:
        return self._r.getrandbits(k) if k > 0 else 0

    def below(self, n: int) -> int:
        """Uniform integer in [0, n)."""
        if n <= 1:
            return 0
        k = n.bit_length()
        while True:
            r = self._r.getrandbits(k)
            if r < n:
                return r

    def randint(self, lo: int, hi: int) -> int:
        """Uniform integer in [lo, hi]."""
        if hi < lo:
            raise ValueError(f"empty range [{lo}, {hi}]")
        return lo + self.below(hi - lo + 1)

    def random(self) -> float:
        return self._r.getrandbits(53) / (1 << 53)

    def chance(self, p: float) -> bool:
        return self.random() < p

    def choice(self, seq: Sequence[T]) -> T:
        return seq[self.below(len(seq))]

    def shuffle(self, items: MutableSequence[T]) -> None:
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]

    def sample_range(self, lo: int, hi: int, k: int) -> list[int]:
        """``k`` distinct integers from [lo, hi], in random order."""
        size = hi - lo + 1
        if k < 0 or k > size:
            raise ValueError(f"cannot pick {k} distinct values from [{lo}, {hi}]")
        if k == 0:
            return []
        if size <= max(4 * k, 1024):
            pool = list(range(lo, hi + 1))
            for i in range(k):
                j = i + self.below(size - i)
                pool[i], pool[j] = pool[j], pool[i]
            return pool[:k]
        seen: set[int] = set()
        out: list[int] = []
        while len(out) < k:
            v = lo + self.below(size)
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out
