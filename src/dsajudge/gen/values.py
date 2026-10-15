"""Value generators for scalars, arrays and strings.

A *flavor* shapes the values (see plan.FLAVORS): ``random``; ``narrow`` (few distinct values,
to force duplicates); ``asc`` / ``desc`` (sorted); ``equal``; ``alternating`` (alternating signs
or extremes); ``extremes`` (only the bounds); ``all_min`` / ``all_max``; ``zero``. A flavor that
makes no sense for a constraint (``equal`` with ``distinct``) falls back to ``random``.
"""

from __future__ import annotations

from dsajudge.gen.rng import Rng

MAX_ELEMENTS = 5_000_000


class GenError(Exception):
    """The generator cannot satisfy a constraint (empty range, impossible combination)."""


def pick_dim(rng: Rng, lo: int, hi: int, size: str, caps: tuple[int, int], what: str) -> int:
    """A length/rows/cols/node count for the test's size class."""
    if hi < lo:
        raise GenError(f"{what}: empty range [{lo}, {hi}]")
    if size == "min":
        return lo
    if size == "min1":
        return min(lo + 1, hi)
    if size == "tiny":
        return rng.randint(lo, min(hi, lo + 3))
    if size == "small":
        return rng.randint(lo, min(hi, max(lo, caps[0])))
    if size == "medium":
        return rng.randint(lo, min(hi, max(lo, caps[1])))
    return hi


def check_total(n: int, what: str) -> None:
    if n > MAX_ELEMENTS:
        raise GenError(f"{what}: {n} elements is above the {MAX_ELEMENTS} limit")


# ---------------------------------------------------------------- integers


def int_scalar(rng: Rng, lo: int, hi: int, flavor: str, what: str = "value") -> int:
    if hi < lo:
        raise GenError(f"{what}: empty range [{lo}, {hi}]")
    if flavor in ("all_min", "asc"):
        return lo
    if flavor in ("all_max", "desc"):
        return hi
    if flavor == "zero":
        return 0 if lo <= 0 <= hi else lo
    if flavor == "extremes":
        return lo if rng.chance(0.5) else hi
    if flavor == "alternating":
        pool = [lo, hi] + ([0] if lo <= 0 <= hi else [])
        return rng.choice(pool)
    if flavor == "narrow":
        span = min(hi - lo, rng.randint(1, 4))
        base = rng.randint(lo, hi - span)
        return rng.randint(base, base + span)
    return rng.randint(lo, hi)


def _distinct(rng: Rng, n: int, lo: int, hi: int, flavor: str) -> list[int]:
    size = hi - lo + 1
    if flavor == "narrow":
        width = min(size, n + rng.randint(0, 3))
        base = rng.randint(lo, hi - width + 1)
        vals = [base + v for v in rng.sample_range(0, width - 1, n)]
    elif flavor == "extremes" and n >= 2 and size - 2 >= n - 2:
        vals = [lo, hi] + (rng.sample_range(lo + 1, hi - 1, n - 2) if n > 2 else [])
        rng.shuffle(vals)
    else:
        vals = rng.sample_range(lo, hi, n)
    if flavor == "asc":
        vals.sort()
    elif flavor == "desc":
        vals.sort(reverse=True)
    elif flavor == "alternating":
        vals.sort()
        out: list[int] = []
        i, j = 0, len(vals) - 1
        while i <= j:
            out.append(vals[i])
            i += 1
            if i <= j:
                out.append(vals[j])
                j -= 1
        vals = out
    return vals


def permutation(rng: Rng, n: int, base: int, flavor: str) -> list[int]:
    vals = list(range(base, base + n))
    if flavor == "asc":
        return vals
    if flavor == "desc":
        return vals[::-1]
    if flavor == "alternating":
        out: list[int] = []
        i, j = 0, n - 1
        while i <= j:
            out.append(vals[i])
            i += 1
            if i <= j:
                out.append(vals[j])
                j -= 1
        return out
    rng.shuffle(vals)
    return vals


def int_values(
    rng: Rng,
    n: int,
    lo: int,
    hi: int,
    flavor: str,
    *,
    distinct: bool = False,
    what: str = "values",
) -> list[int]:
    """``n`` integers in [lo, hi]. With ``distinct``, n is capped by the range size."""
    if n == 0:
        return []
    if hi < lo:
        raise GenError(f"{what}: empty range [{lo}, {hi}]")
    check_total(n, what)
    if distinct:
        return _distinct(rng, min(n, hi - lo + 1), lo, hi, flavor)
    if flavor == "narrow":
        span = min(hi - lo, rng.randint(1, 4))
        base = rng.randint(lo, hi - span)
        return [rng.randint(base, base + span) for _ in range(n)]
    if flavor in ("asc", "desc"):
        vals = [rng.randint(lo, hi) for _ in range(n)]
        vals.sort(reverse=flavor == "desc")
        return vals
    if flavor == "equal":
        return [rng.randint(lo, hi)] * n
    if flavor == "all_min":
        return [lo] * n
    if flavor == "all_max":
        return [hi] * n
    if flavor == "zero":
        return [0 if lo <= 0 <= hi else lo] * n
    if flavor == "extremes":
        return [lo if rng.chance(0.5) else hi for _ in range(n)]
    if flavor == "alternating":
        if lo < 0 < hi:
            pos, neg = (max(lo, 1), hi), (lo, min(hi, -1))
            return [rng.randint(*pos) if i % 2 == 0 else rng.randint(*neg) for i in range(n)]
        return [lo if i % 2 else hi for i in range(n)]
    return [rng.randint(lo, hi) for _ in range(n)]


def sort_values(vals: list[int], mode: str) -> list[int]:
    """Apply a ``sorted`` constraint (asc/desc are strict: callers also set distinct)."""
    return sorted(vals, reverse=mode in ("desc", "noninc"))


# ---------------------------------------------------------------- floats


def float_scalar(rng: Rng, lo: float, hi: float, decimals: int, flavor: str, what: str) -> float:
    if hi < lo:
        raise GenError(f"{what}: empty range [{lo}, {hi}]")
    if flavor in ("all_min", "asc"):
        v = lo
    elif flavor in ("all_max", "desc"):
        v = hi
    elif flavor == "zero":
        v = 0.0 if lo <= 0 <= hi else lo
    elif flavor == "extremes":
        v = lo if rng.chance(0.5) else hi
    elif flavor == "alternating":
        v = rng.choice([lo, hi])
    elif flavor == "narrow":
        mid = lo + (hi - lo) * rng.random()
        w = min(hi - lo, 1.0) / 2
        v = max(lo, min(hi, mid + (rng.random() - 0.5) * w))
    else:
        v = lo + (hi - lo) * rng.random()
    v = round(v, decimals)
    return float(max(lo, min(hi, v)))


def float_values(
    rng: Rng, n: int, lo: float, hi: float, decimals: int, flavor: str, what: str
) -> list[float]:
    check_total(n, what)
    if flavor == "equal":
        return [float_scalar(rng, lo, hi, decimals, "random", what)] * n
    if flavor in ("asc", "desc"):
        vals = [float_scalar(rng, lo, hi, decimals, "random", what) for _ in range(n)]
        vals.sort(reverse=flavor == "desc")
        return vals
    if flavor == "alternating":
        return [lo if i % 2 else hi for i in range(n)]
    return [float_scalar(rng, lo, hi, decimals, flavor, what) for _ in range(n)]


# ---------------------------------------------------------------- strings


def string_value(rng: Rng, n: int, chars: str, flavor: str, what: str = "string") -> str:
    if n == 0:
        return ""
    check_total(n, what)
    k = len(chars)
    if flavor == "narrow":
        picks = rng.sample_range(0, k - 1, min(k, 1 + rng.below(2)))
        sub = "".join(chars[i] for i in picks)
        return "".join(sub[rng.below(len(sub))] for _ in range(n))
    if flavor in ("asc", "desc"):
        s = sorted(chars[rng.below(k)] for _ in range(n))
        return "".join(s[::-1] if flavor == "desc" else s)
    if flavor == "equal":
        return chars[rng.below(k)] * n
    if flavor in ("all_min", "zero"):
        return chars[0] * n
    if flavor == "all_max":
        return chars[-1] * n
    if flavor == "alternating":
        a, b = chars[0], chars[1 if k > 1 else 0]
        return "".join(a if i % 2 == 0 else b for i in range(n))
    if flavor == "extremes":
        return "".join(chars[0] if rng.chance(0.5) else chars[-1] for _ in range(n))
    return "".join(chars[rng.below(k)] for _ in range(n))


def char_scalar(rng: Rng, chars: str, flavor: str) -> str:
    return string_value(rng, 1, chars, flavor)
