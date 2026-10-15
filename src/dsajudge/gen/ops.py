"""Call-sequence generator for design problems (``mode: ops``).

Builds ``[ops, args]`` (see model/design.py): the constructor call with generated arguments,
then ``calls`` method calls picked by the ``mix`` weights among the methods whose ``requires``
hold in the current counter state. Test flavors steer the shape of the sequence:

* ``asc``: builds state up (methods that raise counters are 4x as likely), so max tests make the
  structure big and expose slow operations;
* ``desc``: builds up for the first half, then drains;
* any other flavor keeps the ``mix`` and shapes the argument values instead (``extremes``,
  ``narrow``, ``all_min`` ...).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from dsajudge.config import PlanConfig
from dsajudge.gen.generate import DslGenerator
from dsajudge.gen.plan import TestSpec
from dsajudge.gen.rng import Rng
from dsajudge.gen.values import GenError, pick_dim
from dsajudge.model.constraints import DesignSpecs
from dsajudge.model.design import allowed, apply_effects, initial_counters
from dsajudge.model.expr import ExprError

# A "small" sequence is this many times longer than a small array: stateful bugs need a few
# dozen calls to show up.
CALLS_CAP_FACTOR = 4
BIAS = 4


class OpsGenerator:
    def __init__(self, specs: DesignSpecs, plan: PlanConfig) -> None:
        self.specs = specs
        self.plan = plan
        self.inner = DslGenerator([], plan)

    def generate(self, test: TestSpec) -> list[Any]:
        spec, design = self.specs, self.specs.design
        rng = Rng(test.seed)
        # asc/desc shape the sequence; they are not meaningful for a single scalar argument.
        values = test if test.flavor not in ("asc", "desc") else replace(test, flavor="random")

        env: dict[str, Any] = {}
        ctor_args: list[Any] = []
        for p in spec.ctor:
            v = self.inner.value(rng, p, env, values)
            env[p.name] = v
            ctor_args.append(v)

        try:
            lo, hi = spec.calls.eval_int(env)
        except ExprError as e:
            raise GenError(f"calls: {e}") from e
        caps = (self.plan.small_cap * CALLS_CAP_FACTOR, self.plan.medium_cap * CALLS_CAP_FACTOR)
        n = pick_dim(rng, lo, hi, test.size, caps, "calls.len")

        counters = initial_counters(design, ctor_args)
        ops = [design.class_name]
        args: list[Any] = [ctor_args]
        growers = {m.name for m in design.methods if sum(m.effects.values()) > 0}
        shrinkers = {m.name for m in design.methods if sum(m.effects.values()) < 0}
        for i in range(n):
            options = allowed(design, counters)
            weights = {name: spec.mix[name] for name in options if spec.mix[name] > 0}
            if not weights:
                break  # nothing may be called now; a shorter sequence is still a valid one
            if test.flavor == "asc":
                favoured = growers
            elif test.flavor == "desc":
                favoured = growers if i < n // 2 else shrinkers
            else:
                favoured = set()
            for name in weights:
                if name in favoured:
                    weights[name] *= BIAS
            name = self._pick(rng, weights)
            call_env = dict(env)
            call_args: list[Any] = []
            for p in spec.methods[name]:
                v = self.inner.value(rng, p, call_env, values)
                call_env[p.name] = v
                call_args.append(v)
            apply_effects(design, counters, name)
            ops.append(name)
            args.append(call_args)
        return [ops, args]

    @staticmethod
    def _pick(rng: Rng, weights: dict[str, int]) -> str:
        total = sum(weights.values())
        r = rng.below(total)
        for name, w in weights.items():
            if r < w:
                return name
            r -= w
        raise AssertionError("unreachable")
