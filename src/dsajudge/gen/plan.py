"""Test plan (PLAN.md §6.2): which tests to generate, with deterministic seeds."""

from __future__ import annotations

from dataclasses import dataclass

from dsajudge.config import PlanConfig
from dsajudge.gen.rng import seed_for

# Groups that are generated and stored/regenerated. `samples` come from problem.yaml.
GROUPS = ("edge", "small_random", "medium_random", "max")
STRESS_GROUP = "stress"

# size classes: edge tests use min/min1/tiny; the rest are scaled by the plan's caps.
SIZES = ("min", "min1", "tiny", "small", "medium", "max")

# Value/structure flavors (see gen/values.py for what each does per type).
FLAVORS = (
    "random",
    "narrow",
    "asc",
    "desc",
    "equal",
    "alternating",
    "extremes",
    "all_min",
    "all_max",
    "zero",
)

EDGE_PLAN: tuple[tuple[str, str], ...] = (
    ("min", "all_min"),
    ("min", "all_max"),
    ("min", "zero"),
    ("min", "equal"),
    ("min", "extremes"),
    ("min", "random"),
    ("min1", "all_min"),
    ("min1", "all_max"),
    ("min1", "zero"),
    ("min1", "equal"),
    ("min1", "alternating"),
    ("min1", "extremes"),
    ("tiny", "all_min"),
    ("tiny", "all_max"),
    ("tiny", "alternating"),
    ("tiny", "asc"),
    ("tiny", "desc"),
    ("tiny", "narrow"),
)
SMALL_FLAVORS = (
    "random",
    "random",
    "narrow",
    "random",
    "extremes",
    "random",
    "narrow",
    "asc",
    "desc",
    "alternating",
)
MEDIUM_FLAVORS = (
    "random",
    "random",
    "narrow",
    "asc",
    "desc",
    "random",
    "extremes",
    "equal",
    "alternating",
    "random",
)
# Random, sorted, reverse, all-equal, plus structural worst cases (alternating signs, line
# trees, star graphs, ...) and extreme values.
MAX_FLAVORS = ("random", "asc", "desc", "equal", "alternating", "extremes")
STRESS_FLAVORS = ("random", "random", "narrow", "random", "extremes", "asc", "alternating")


@dataclass(frozen=True)
class TestSpec:
    __test__ = False  # not a pytest class

    id: str
    group: str
    index: int
    size: str
    flavor: str
    seed: int

    @property
    def coarse_size(self) -> str:
        """Size name passed to custom gen.py: edge | small | medium | max."""
        return "edge" if self.size in ("min", "min1", "tiny") else self.size


def _spec(slug: str, part: int, group: str, index: int, size: str, flavor: str) -> TestSpec:
    return TestSpec(
        id=f"{group}/{index:02d}",
        group=group,
        index=index,
        size=size,
        flavor=flavor,
        seed=seed_for(slug, part, group, index),
    )


def make_plan(slug: str, part: int, cfg: PlanConfig) -> list[TestSpec]:
    specs: list[TestSpec] = []
    if cfg.edge:
        for i, (size, flavor) in enumerate(EDGE_PLAN, 1):
            specs.append(_spec(slug, part, "edge", i, size, flavor))
    for i in range(cfg.small_random):
        size = "tiny" if i % 10 == 9 else "small"
        specs.append(_spec(slug, part, "small_random", i + 1, size, SMALL_FLAVORS[i % 10]))
    for i in range(cfg.medium_random):
        specs.append(_spec(slug, part, "medium_random", i + 1, "medium", MEDIUM_FLAVORS[i % 10]))
    for i in range(cfg.max):
        specs.append(_spec(slug, part, "max", i + 1, "max", MAX_FLAVORS[i % len(MAX_FLAVORS)]))
    return specs


def stress_plan(slug: str, part: int, count: int) -> list[TestSpec]:
    """Extra small seeds for ref-vs-brute stress (not stored)."""
    out: list[TestSpec] = []
    for i in range(count):
        size = "tiny" if i % 4 == 3 else "small"
        out.append(
            _spec(slug, part, STRESS_GROUP, i + 1, size, STRESS_FLAVORS[i % len(STRESS_FLAVORS)])
        )
    return out
