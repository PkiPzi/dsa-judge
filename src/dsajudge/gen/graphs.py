"""Graph generation: edge lists for trees, connected graphs, DAGs and arbitrary simple graphs.

Graphs are simple (no self loops, no parallel edges). Edges are ``[u, v]`` or ``[u, v, w]``
with node labels ``base .. base + n - 1``.
"""

from __future__ import annotations

from dsajudge.gen.rng import Rng
from dsajudge.gen.values import GenError, check_total, int_values

Edge = tuple[int, int]


def max_edges(n: int, directed: bool, kind: str) -> int:
    pairs = n * (n - 1) // 2
    return pairs * 2 if directed and kind != "dag" else pairs


def default_m_bounds(n: int, kind: str, directed: bool) -> tuple[int, int]:
    cap = max_edges(n, directed, kind)
    if kind == "tree":
        return max(n - 1, 0), max(n - 1, 0)
    if kind == "connected":
        return max(n - 1, 0), min(cap, max(n - 1, 2 * n))
    return 0, min(cap, 2 * n)


def _tree_pairs(rng: Rng, n: int, flavor: str) -> list[Edge]:
    """Parent/child pairs of a spanning tree on labels 0..n-1."""
    if n <= 1:
        return []
    if flavor == "asc":
        return [(i - 1, i) for i in range(1, n)]
    if flavor == "equal":
        return [(0, i) for i in range(1, n)]
    if flavor == "alternating":  # caterpillar: a spine with leaves hanging off it
        spine = max(2, n // 2)
        pairs = [(i - 1, i) for i in range(1, spine)]
        pairs += [(rng.below(spine), i) for i in range(spine, n)]
        return pairs
    if flavor == "extremes":  # binary tree
        return [((i - 1) // 2, i) for i in range(1, n)]
    return [(rng.below(i), i) for i in range(1, n)]


def _key(u: int, v: int, directed: bool) -> Edge:
    return (u, v) if directed or u < v else (v, u)


def _extra_edges(
    rng: Rng,
    n: int,
    present: set[Edge],
    count: int,
    directed: bool,
    forward_only: bool,
) -> list[Edge]:
    """``count`` new distinct edges. ``forward_only`` keeps u < v (DAG positions)."""
    if count <= 0:
        return []
    cap = max_edges(n, directed and not forward_only, "dag" if forward_only else "any")
    free = cap - len(present)
    if count > free:
        raise GenError(f"cannot add {count} more edges to a graph with {n} nodes")
    out: list[Edge] = []
    if free <= 2 * count and cap <= 2_000_000:
        pool = [
            (u, v)
            for u in range(n)
            for v in range(n)
            if u != v
            and (not forward_only or u < v)
            and (directed or u < v)
            and (u, v) not in present
        ]
        rng.shuffle(pool)
        return pool[:count]
    seen = set(present)
    while len(out) < count:
        u, v = rng.below(n), rng.below(n)
        if u == v:
            continue
        if forward_only and u > v:
            u, v = v, u
        k = _key(u, v, directed)
        if k in seen:
            continue
        seen.add(k)
        out.append(k if directed else (u, v))
    return out


def generate_graph(
    rng: Rng,
    n: int,
    kind: str,
    directed: bool,
    m_lo: int,
    m_hi: int,
    flavor: str,
    size: str,
    base: int,
    weights: tuple[int, int] | None,
    what: str,
) -> list[list[int]]:
    if n < 0:
        raise GenError(f"{what}: negative node count")
    cap = max_edges(n, directed, kind)
    if kind == "tree":
        m_lo = m_hi = max(n - 1, 0)
    elif kind == "connected":
        m_lo = max(m_lo, n - 1)
    m_hi = min(m_hi, cap)
    if m_lo > m_hi:
        raise GenError(f"{what}: needs at least {m_lo} edges but {n} nodes allow {m_hi}")
    if kind == "connected" and n >= 1 and m_hi < n - 1:
        raise GenError(f"{what}: {n} nodes cannot be connected with at most {m_hi} edges")
    if size == "min":
        m = m_lo
    elif size == "min1":
        m = min(m_lo + 1, m_hi)
    elif size == "max" or flavor == "extremes":
        m = m_hi
    else:
        m = rng.randint(m_lo, min(m_hi, max(m_lo, 3 * n)))
    check_total(m, what)

    edges: list[Edge]
    if kind == "dag":
        # positions in a topological order; edges go from lower to higher position
        backbone: list[Edge] = []
        if flavor == "asc":
            backbone = [(i, i + 1) for i in range(n - 1)]
        elif flavor == "equal":
            backbone = [(0, i) for i in range(1, n)]
        backbone = backbone[:m]
        present = set(backbone)
        pos_edges = backbone + _extra_edges(rng, n, present, m - len(backbone), True, True)
        order = list(range(n))
        if flavor != "asc":
            rng.shuffle(order)
        edges = [(order[a], order[b]) for a, b in pos_edges]
    else:
        pairs: list[Edge] = []
        if kind in ("tree", "connected") or flavor in ("asc", "equal"):
            pairs = _tree_pairs(rng, n, flavor)
        pairs = pairs[:m]
        labels = list(range(n))
        if flavor != "asc":
            rng.shuffle(labels)
        pairs = [(labels[a], labels[b]) for a, b in pairs]
        oriented = [(a, b) if not directed or rng.chance(0.5) else (b, a) for a, b in pairs]
        present = {_key(a, b, directed) for a, b in oriented}
        extra = _extra_edges(rng, n, present, m - len(oriented), directed, False)
        edges = oriented + extra
        if not directed:
            edges = [(a, b) if rng.chance(0.5) else (b, a) for a, b in edges]

    if flavor != "asc":
        rng.shuffle(edges)
    ws = int_values(rng, len(edges), weights[0], weights[1], flavor) if weights else None
    out: list[list[int]] = []
    for i, (u, v) in enumerate(edges):
        row = [u + base, v + base]
        if ws is not None:
            row.append(ws[i])
        out.append(row)
    return out
