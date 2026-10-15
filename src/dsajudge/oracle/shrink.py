"""Shrink a failing input to a small counterexample (PLAN.md §6.4).

Candidates are simplifications of one argument at a time: shorter arrays (halves, one element
removed), fewer rows/columns, smaller numbers, shorter strings. A candidate counts only if it
still satisfies the part's constraints and the caller's predicate says it still fails.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dsajudge.model.canon import dumps

MAX_CANDIDATES = 150
MAX_ROUNDS = 25


def _positions(n: int, limit: int = 24) -> list[int]:
    if n <= limit:
        return list(range(n))
    step = n / limit
    return sorted({int(i * step) for i in range(limit)} | {0, n - 1})


def _simpler_scalar(v: Any) -> list[Any]:
    if isinstance(v, bool):
        return [False] if v else []
    if isinstance(v, int):
        out: list[int] = []
        for c in (0, 1, v // 2, v - 1 if v > 0 else v + 1):
            if c != v and abs(c) < abs(v) and c not in out:
                out.append(c)
        return out
    if isinstance(v, float):
        return [c for c in (0.0, float(int(v / 2))) if abs(c) < abs(v)]
    if isinstance(v, str):
        return [v[:-1]] if len(v) > 1 else []
    return []


def _list_candidates(v: list[Any]) -> list[list[Any]]:
    out: list[list[Any]] = []
    n = len(v)
    if n >= 2:
        out += [v[: n // 2], v[n // 2 :]]
    for i in _positions(n):
        out.append(v[:i] + v[i + 1 :])
    for i in _positions(n, 12):
        if isinstance(v[i], list | dict):
            continue
        for c in _simpler_scalar(v[i])[:2]:
            out.append([*v[:i], c, *v[i + 1 :]])
    return out


def _matrix_candidates(v: list[list[Any]]) -> list[list[list[Any]]]:
    out: list[list[list[Any]]] = _list_candidates(v)
    cols = max((len(r) for r in v), default=0)
    for j in _positions(cols, 12):
        out.append([r[:j] + r[j + 1 :] for r in v])
    return out


def _parallel_candidates(args: list[Any]) -> list[list[Any]]:
    """Drop the same position from every 1-D list/string argument of equal length at once.

    Needed for constraints like len(a) == len(b), where shrinking one array breaks validity.
    """
    flat = [
        i
        for i, v in enumerate(args)
        if isinstance(v, list | str) and not (v and isinstance(v[0], list))
    ]
    out: list[list[Any]] = []
    for n in {len(args[i]) for i in flat}:
        group = [i for i in flat if len(args[i]) == n]
        if n < 2 or len(group) < 2:
            continue
        for pos in _positions(n):
            cand = list(args)
            for i in group:
                cand[i] = args[i][:pos] + args[i][pos + 1 :]
            out.append(cand)
    return out


def candidates(args: list[Any]) -> list[list[Any]]:
    out: list[list[Any]] = _parallel_candidates(args)
    for i, v in enumerate(args):
        if isinstance(v, str):
            options: list[Any] = ["".join(c) for c in _list_candidates(list(v))]
            if len(v) > 1:
                options = [x for x in options if x]
        elif isinstance(v, list):
            if v and all(isinstance(r, list) for r in v):
                options = _matrix_candidates(v)
            else:
                options = _list_candidates(v)
        else:
            options = _simpler_scalar(v)
        for o in options:
            out.append([*args[:i], o, *args[i + 1 :]])
    seen: set[str] = set()
    uniq: list[list[Any]] = []
    for c in out:
        key = dumps(c)
        if key in seen or c == args:
            continue
        seen.add(key)
        uniq.append(c)
    uniq.sort(key=lambda c: len(dumps(c)))
    return uniq[:MAX_CANDIDATES]


def ops_candidates(args: list[Any]) -> list[list[Any]]:
    """Simplifications of a call sequence ``[ops, arglists]``: drop calls (a chunk, then single
    calls) or simplify one call's scalar arguments. The constructor call always stays."""
    ops, arglists = args
    n = len(ops)
    out: list[list[Any]] = []

    def without(lo: int, hi: int) -> list[Any]:
        return [ops[:lo] + ops[hi:], arglists[:lo] + arglists[hi:]]

    size = (n - 1) // 2
    while size >= 1:
        for lo in _positions(n - size + 1, 16):
            if lo >= 1:
                out.append(without(lo, lo + size))
        size //= 2
    for i in _positions(n, 24):
        if i >= 1:
            out.append(without(i, i + 1))
    for i in _positions(n, 16):
        for j, v in enumerate(arglists[i]):
            for c in _simpler_scalar(v)[:2]:
                if isinstance(v, list | dict):
                    continue
                new_args = [*arglists[i][:j], c, *arglists[i][j + 1 :]]
                out.append([ops, [*arglists[:i], new_args, *arglists[i + 1 :]]])
    seen: set[str] = set()
    uniq: list[list[Any]] = []
    for c in out:
        key = dumps(c)
        if key in seen or c == args:
            continue
        seen.add(key)
        uniq.append(c)
    uniq.sort(key=lambda c: len(dumps(c)))
    return uniq[:MAX_CANDIDATES]


def shrink(
    args: list[Any],
    valid: Callable[[list[list[Any]]], list[bool]],
    fails: Callable[[list[list[Any]]], list[bool]],
    propose: Callable[[list[Any]], list[list[Any]]] = candidates,
) -> list[Any]:
    """Greedy shrink: take the smallest valid candidate that still fails, repeat."""
    current = args
    for _ in range(MAX_ROUNDS):
        cands = propose(current)
        if not cands:
            break
        ok = valid(cands)
        cands = [c for c, good in zip(cands, ok, strict=True) if good]
        if not cands:
            break
        flags = fails(cands)
        nxt = next((c for c, f in zip(cands, flags, strict=True) if f), None)
        if nxt is None:
            break
        current = nxt
    return current
