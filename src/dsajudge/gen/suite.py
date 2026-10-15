"""Per-part generator and validator wiring (DSL by default, gen.py / validate.py if present)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from dsajudge.config import JudgeConfig, PlanConfig
from dsajudge.gen.custom import CustomGenerator, CustomValidator
from dsajudge.gen.generate import DslGenerator
from dsajudge.gen.manifest import sha256_text
from dsajudge.gen.ops import OpsGenerator
from dsajudge.gen.plan import TestSpec
from dsajudge.gen.validator import validate_args, validate_ops
from dsajudge.gen.values import GenError
from dsajudge.model.constraints import (
    DesignSpecs,
    ParamSpec,
    parse_constraints,
    parse_design_constraints,
)
from dsajudge.model.schema import Part

# Bump when generator output for the same seed changes: old manifests then read as stale.
GEN_VERSION = "1"


class Generator(Protocol):
    def generate_many(self, tests: list[TestSpec]) -> list[list[Any]]: ...


class _DslMany:
    def __init__(self, gen: DslGenerator) -> None:
        self.gen = gen

    def generate_many(self, tests: list[TestSpec]) -> list[list[Any]]:
        out: list[list[Any]] = []
        for t in tests:
            try:
                out.append(self.gen.generate(t))
            except GenError as e:
                raise GenError(f"{t.id} ({t.size}/{t.flavor}): {e}") from e
        return out


class _OpsMany:
    def __init__(self, gen: OpsGenerator) -> None:
        self.gen = gen

    def generate_many(self, tests: list[TestSpec]) -> list[list[Any]]:
        out: list[list[Any]] = []
        for t in tests:
            try:
                out.append(self.gen.generate(t))
            except GenError as e:
                raise GenError(f"{t.id} ({t.size}/{t.flavor}): {e}") from e
        return out


@dataclass
class PartGen:
    specs: list[ParamSpec]
    generator: Generator
    custom: bool
    custom_validator: CustomValidator | None
    ops: DesignSpecs | None = None

    def validate_many(self, inputs: list[list[Any]]) -> list[list[str]]:
        """Constraint violations for each input (empty list = valid)."""
        if self.ops is not None:
            ops = self.ops
            errs = [validate_ops(ops, a) for a in inputs]
        else:
            errs = [validate_args(self.specs, a) for a in inputs]
        if self.custom_validator:
            todo = [i for i, e in enumerate(errs) if not e]
            for i, msg in zip(
                todo, self.custom_validator.validate_many([inputs[i] for i in todo]), strict=True
            ):
                if msg:
                    errs[i] = [f"guarantee violated: {msg}"]
        return errs


def gen_script(problem_dir: Path, part: Part) -> Path | None:
    p = problem_dir / f"part{part.id}" / "gen.py"
    return p if p.is_file() else None


def validate_script(problem_dir: Path, part: Part) -> Path | None:
    p = problem_dir / f"part{part.id}" / "validate.py"
    return p if p.is_file() else None


def load_part_gen(problem_dir: Path, part: Part, plan: PlanConfig, cfg: JudgeConfig) -> PartGen:
    """Specs plus the generator to use. Raises ConstraintError / GenError."""
    ops = parse_design_constraints(part) if part.mode == "ops" else None
    specs = [] if ops is not None else parse_constraints(part)
    script = gen_script(problem_dir, part)
    if part.guarantees and script is None:
        raise GenError(
            f"part {part.id} has guarantees the constraint DSL cannot express "
            f"({'; '.join(part.guarantees)}); write part{part.id}/gen.py "
            "(def generate(rng, size, flavor) -> list) so every generated input satisfies them"
        )
    vscript = validate_script(problem_dir, part)
    dsl: Generator = (
        _OpsMany(OpsGenerator(ops, plan))
        if ops is not None
        else _DslMany(DslGenerator(specs, plan))
    )
    generator: Generator = CustomGenerator(script, cfg) if script else dsl
    return PartGen(
        specs=specs,
        generator=generator,
        custom=script is not None,
        custom_validator=CustomValidator(vscript, cfg) if vscript else None,
        ops=ops,
    )


def spec_hash(problem_dir: Path, part: Part, plan: PlanConfig) -> str:
    """Hash of everything that decides which tests get generated."""
    checker = problem_dir / f"part{part.id}" / "checker.py"
    scripts = [
        gen_script(problem_dir, part),
        validate_script(problem_dir, part),
        checker if checker.is_file() else None,
    ]
    parts: dict[str, Any] = {
        "gen_version": GEN_VERSION,
        "mode": part.mode,
        "signature": part.signature.model_dump(mode="json") if part.signature else None,
        "constraints": part.constraints,
        "guarantees": part.guarantees,
        "checker": part.checker,
        "input_format": part.input_format,
        "output_format": part.output_format,
        "plan": plan.model_dump(mode="json"),
        "scripts": [sha256_text(s.read_text(encoding="utf-8")) if s else None for s in scripts],
    }
    if part.design is not None:  # only ops parts, so older manifests keep their hash
        parts["design"] = part.design.model_dump(mode="json", by_alias=True)
    return sha256_text(json.dumps(parts, sort_keys=True, default=str))
