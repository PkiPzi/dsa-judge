"""Binary trees: structure generation, LeetCode level-order encoding, shape checks.

Everything is iterative: a line tree with 10^5 nodes must not hit Python's recursion limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from dsajudge.gen.rng import Rng
from dsajudge.gen.values import GenError, int_values

NONE = -1


@dataclass
class Tree:
    values: list[int]
    left: list[int]
    right: list[int]

    @property
    def n(self) -> int:
        return len(self.values)


# ---------------------------------------------------------------- structure


def _chain(rng: Rng, n: int, mode: str) -> tuple[list[int], list[int]]:
    left, right = [NONE] * n, [NONE] * n
    for i in range(n - 1):
        if mode == "right":
            go_right = True
        elif mode == "left":
            go_right = False
        elif mode == "zigzag":
            go_right = i % 2 == 0
        else:
            go_right = rng.chance(0.5)
        if go_right:
            right[i] = i + 1
        else:
            left[i] = i + 1
    return left, right


def _complete(n: int) -> tuple[list[int], list[int]]:
    left = [2 * i + 1 if 2 * i + 1 < n else NONE for i in range(n)]
    right = [2 * i + 2 if 2 * i + 2 < n else NONE for i in range(n)]
    return left, right


def _balanced(rng: Rng, n: int) -> tuple[list[int], list[int]]:
    left, right = [NONE] * n, [NONE] * n
    if n == 0:
        return left, right
    next_id = 1
    stack = [(0, n)]
    while stack:
        node, size = stack.pop()
        rest = size - 1
        a = rest // 2
        b = rest - a
        if rng.chance(0.5):
            a, b = b, a
        for side, count in ((0, a), (1, b)):
            if count > 0:
                child = next_id
                next_id += 1
                (left if side == 0 else right)[node] = child
                stack.append((child, count))
    return left, right


def _random_tree(rng: Rng, n: int) -> tuple[list[int], list[int]]:
    left, right = [NONE] * n, [NONE] * n
    slots: list[tuple[int, int]] = [(0, 0), (0, 1)] if n > 1 else []
    for i in range(1, n):
        j = rng.below(len(slots))
        slots[j], slots[-1] = slots[-1], slots[j]
        parent, side = slots.pop()
        (left if side == 0 else right)[parent] = i
        slots.append((i, 0))
        slots.append((i, 1))
    return left, right


def structure(rng: Rng, n: int, shape: str, flavor: str) -> tuple[list[int], list[int]]:
    """Child arrays (-1 = none) for an n-node tree; node 0 is the root."""
    if shape == "complete":
        return _complete(n)
    if shape == "balanced":
        return _balanced(rng, n)
    if shape == "line":
        mode = {"asc": "right", "desc": "left", "alternating": "zigzag"}.get(flavor, "random")
        return _chain(rng, n, mode)
    # any / bst: flavors pick the classic worst cases
    if flavor == "asc":
        return _chain(rng, n, "right")
    if flavor == "desc":
        return _chain(rng, n, "left")
    if flavor == "alternating":
        return _chain(rng, n, "zigzag")
    if flavor == "extremes":
        return _balanced(rng, n)
    return _random_tree(rng, n)


def inorder(left: list[int], right: list[int]) -> list[int]:
    out: list[int] = []
    if not left:
        return out
    stack: list[int] = []
    cur = 0
    while stack or cur != NONE:
        while cur != NONE:
            stack.append(cur)
            cur = left[cur]
        cur = stack.pop()
        out.append(cur)
        cur = right[cur]
    return out


def generate_tree(
    rng: Rng,
    n: int,
    lo: int,
    hi: int,
    shape: str,
    flavor: str,
    distinct: bool,
    what: str,
) -> Tree:
    if n == 0:
        return Tree([], [], [])
    if (shape == "bst" or distinct) and hi - lo + 1 < n:
        raise GenError(f"{what}: {n} distinct values do not fit in [{lo}, {hi}]")
    left, right = structure(rng, n, shape, flavor)
    if shape == "bst":
        # distinct values handed out in in-order position
        vals = sorted(int_values(rng, n, lo, hi, "random", distinct=True, what=what))
        values = [0] * n
        for pos, node in enumerate(inorder(left, right)):
            values[node] = vals[pos]
    else:
        # `values` are indexed by node id; ids are creation order, which is BFS-ish for random
        # trees, so flavors like asc/desc still give structured value patterns.
        values = int_values(rng, n, lo, hi, flavor, distinct=distinct, what=what)
    return Tree(values, left, right)


# ---------------------------------------------------------------- level order


def encode(tree: Tree) -> list[int | None]:
    """LeetCode level-order with nulls, trailing nulls trimmed."""
    if tree.n == 0:
        return []
    out: list[int | None] = []
    queue = [0]
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        if node == NONE:
            out.append(None)
            continue
        out.append(tree.values[node])
        queue.append(tree.left[node])
        queue.append(tree.right[node])
    while out and out[-1] is None:
        out.pop()
    return out


def decode(arr: list[int | None]) -> Tree:
    """Inverse of encode. Raises ValueError if the array is not a valid level-order tree."""
    if not arr:
        return Tree([], [], [])
    if arr[0] is None:
        raise ValueError("root is null")
    values: list[int] = [arr[0]]
    left: list[int] = [NONE]
    right: list[int] = [NONE]
    queue = [0]
    head = 0
    idx = 1
    while head < len(queue) and idx < len(arr):
        node = queue[head]
        head += 1
        for side in (0, 1):
            if idx >= len(arr):
                break
            v = arr[idx]
            idx += 1
            if v is None:
                continue
            values.append(v)
            left.append(NONE)
            right.append(NONE)
            child = len(values) - 1
            (left if side == 0 else right)[node] = child
            queue.append(child)
    if idx < len(arr):
        raise ValueError(f"entry {idx} has no parent slot (extra values after the tree ended)")
    return Tree(values, left, right)


# ---------------------------------------------------------------- shape checks


def is_bst(tree: Tree) -> bool:
    order = inorder(tree.left, tree.right)
    vals = [tree.values[i] for i in order]
    return all(a < b for a, b in pairwise(vals))


def is_line(tree: Tree) -> bool:
    return all((tree.left[i] != NONE) + (tree.right[i] != NONE) <= 1 for i in range(tree.n))


def heights(tree: Tree) -> list[int]:
    """Height of each node's subtree; children always have larger ids than their parents."""
    h = [1] * tree.n
    for i in range(tree.n - 1, -1, -1):
        a = h[tree.left[i]] if tree.left[i] != NONE else 0
        b = h[tree.right[i]] if tree.right[i] != NONE else 0
        h[i] = 1 + max(a, b)
    return h


def is_balanced(tree: Tree) -> bool:
    h = heights(tree)
    for i in range(tree.n):
        a = h[tree.left[i]] if tree.left[i] != NONE else 0
        b = h[tree.right[i]] if tree.right[i] != NONE else 0
        if abs(a - b) > 1:
            return False
    return True


def is_complete(arr: list[int | None]) -> bool:
    """A complete tree's trimmed level order contains no nulls."""
    return all(v is not None for v in arr)
