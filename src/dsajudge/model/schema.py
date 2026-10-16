"""problem.yaml schema (PLAN.md §4.2). Version 1.

After Phase 1 ships, changes to this schema need a migration, not a breaking edit.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dsajudge.model.expr import ExprError, parse_expr
from dsajudge.model.types import TypeNode, check_value, java_list_depth, parse_type

Lang = Literal["cpp", "java", "python"]
LANGS: tuple[Lang, ...] = ("cpp", "java", "python")

CHECKER_RE = re.compile(r"^(exact|unordered|unordered_deep|custom|float:[0-9.eE+-]+)$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Param(_Model):
    name: str
    type: str

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"param name {v!r} is not a valid identifier")
        return v

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        parse_type(v)
        return v

    @property
    def tnode(self) -> TypeNode:
        return parse_type(self.type)


class Signature(_Model):
    method: str
    params: list[Param]
    returns: str
    output_param: str | None = None
    lang_types: dict[Lang, dict[str, str]] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"method name {v!r} is not a valid identifier")
        return v

    @field_validator("returns")
    @classmethod
    def _returns(cls, v: str) -> str:
        parse_type(v, allow_void=True)
        return v

    @model_validator(mode="after")
    def _check(self) -> Signature:
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate param names in {names}")
        if self.ret.is_void:
            if not self.output_param:
                raise ValueError("returns: void needs output_param (the in-place parameter)")
            if self.output_param not in names:
                raise ValueError(f"output_param {self.output_param!r} is not a param")
        elif self.output_param:
            raise ValueError("output_param is only allowed with returns: void")
        for lang, overrides in self.lang_types.items():
            for key, override in overrides.items():
                if key == "return":
                    t = self.ret
                elif key in names:
                    t = self.param(key).tnode
                else:
                    raise ValueError(f"lang_types.{lang}: unknown key {key!r}")
                if lang == "java":
                    java_list_depth(t, override)
                else:
                    raise ValueError(f"lang_types.{lang}: overrides are only supported for java")
        return self

    @property
    def ret(self) -> TypeNode:
        return parse_type(self.returns, allow_void=True)

    @property
    def output_type(self) -> TypeNode:
        """Type of the value the driver prints: the return, or output_param for in-place."""
        if self.ret.is_void:
            assert self.output_param
            return self.param(self.output_param).tnode
        return self.ret

    def param(self, name: str) -> Param:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)

    def override(self, lang: Lang, key: str) -> str | None:
        return self.lang_types.get(lang, {}).get(key)


# Names of constraint entries in an ops part that are not methods.
OPS_RESERVED = ("calls", "ctor")


class Method(_Model):
    """One method of a design class. ``requires``/``effects`` drive the call-sequence generator:
    a method may only be called while every counter in ``requires`` is at least that value, and
    adds its ``effects`` to the counters afterwards (a stack's ``pop`` needs ``size >= 1``)."""

    name: str
    params: list[Param] = Field(default_factory=list)
    returns: str = "void"
    requires: dict[str, int] = Field(default_factory=dict)
    effects: dict[str, int] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"method name {v!r} is not a valid identifier")
        return v

    @field_validator("returns")
    @classmethod
    def _returns(cls, v: str) -> str:
        parse_type(v, allow_void=True)
        return v

    @property
    def ret(self) -> TypeNode:
        return parse_type(self.returns, allow_void=True)


class Design(_Model):
    """A design problem (``mode: ops``): a class built by a constructor and driven by a
    sequence of method calls, in LeetCode's wire format (see PLAN.md §4.3)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_name: str = Field(alias="class")
    constructor: list[Param] = Field(default_factory=list)
    # Counters the generator tracks: name -> initial value (an int, or an expression over the
    # constructor's parameters such as "len(nums)").
    counters: dict[str, int | str] = Field(default_factory=dict)
    methods: list[Method]

    @field_validator("class_name")
    @classmethod
    def _cls(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"class name {v!r} is not a valid identifier")
        return v

    @model_validator(mode="after")
    def _check(self) -> Design:
        if not self.methods:
            raise ValueError("a design needs at least one method")
        ctor = [p.name for p in self.constructor]
        if len(set(ctor)) != len(ctor):
            raise ValueError(f"duplicate constructor params in {ctor}")
        names = [m.name for m in self.methods]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate method names in {names}")
        for n in names:
            if n in OPS_RESERVED:
                raise ValueError(f"method name {n!r} is reserved (used in constraints)")
            if n == self.class_name:
                raise ValueError(f"method {n!r} has the same name as the class")
        for m in self.methods:
            pn = [p.name for p in m.params]
            if len(set(pn)) != len(pn):
                raise ValueError(f"method {m.name}: duplicate param names in {pn}")
            for key in [*m.requires, *m.effects]:
                if key not in self.counters:
                    raise ValueError(
                        f"method {m.name}: unknown counter {key!r} "
                        f"(declare it under counters; have {sorted(self.counters)})"
                    )
        for cname, init in self.counters.items():
            if isinstance(init, str):
                try:
                    unknown = parse_expr(init).names() - set(ctor)
                except ExprError as e:
                    raise ValueError(f"counter {cname}: {e}") from e
                if unknown:
                    raise ValueError(
                        f"counter {cname}: {sorted(unknown)[0]!r} is not a constructor param"
                    )
        return self

    def method(self, name: str) -> Method:
        for m in self.methods:
            if m.name == name:
                return m
        raise KeyError(name)


class Sample(_Model):
    args: list[Any]
    expected: Any


class SourceFile(_Model):
    lang: Lang
    file: str
    origin: Literal["user", "friend", "llm", "promoted"] = "user"
    verified: bool | Literal["stress-only"] = False


class Limits(_Model):
    time_ms: int | Literal["auto"] = "auto"
    memory_mb: int = 256

    @field_validator("time_ms")
    @classmethod
    def _tl(cls, v: int | str) -> int | str:
        if isinstance(v, int) and v <= 0:
            raise ValueError("time_ms must be positive")
        return v

    @field_validator("memory_mb")
    @classmethod
    def _ml(cls, v: int) -> int:
        if v < 16:
            raise ValueError("memory_mb must be at least 16")
        return v


class Part(_Model):
    id: int
    title: str
    statement: str
    editorial: str | None = None  # file with the solution's explanation
    mode: Literal["function", "stdio", "ops"] = "function"
    signature: Signature | None = None
    design: Design | None = None
    constraints: dict[str, dict[str, Any]] = Field(default_factory=dict)
    guarantees: list[str] = Field(default_factory=list)
    samples: list[Sample] = Field(default_factory=list)
    checker: str = "exact"
    refs: list[SourceFile] = Field(default_factory=list)
    brute: SourceFile | None = None
    oracle: Literal["ref", "brute-only", "promoted"] | None = None
    limits: Limits = Field(default_factory=Limits)
    input_format: str | None = None
    # stdio mode: how the expected result is printed; default "{result}".
    output_format: str | None = None

    @field_validator("checker")
    @classmethod
    def _checker(cls, v: str) -> str:
        if not CHECKER_RE.match(v):
            raise ValueError(
                f"checker {v!r}: expected exact, unordered, unordered_deep, float:EPS or custom"
            )
        if v.startswith("float:"):
            float(v.split(":", 1)[1])
        return v

    @model_validator(mode="after")
    def _check(self) -> Part:
        if self.mode == "ops":
            return self._check_ops()
        if self.design is not None:
            raise ValueError(f"part {self.id}: design is only for mode: ops")
        if self.mode == "function" and self.signature is None:
            raise ValueError(f"part {self.id}: function mode needs a signature")
        if self.mode == "stdio":
            if self.signature is None:
                raise ValueError(
                    f"part {self.id}: stdio mode needs a signature "
                    "(its params and returns type describe the generated input and expected output)"
                )
            if not self.input_format:
                raise ValueError(f"part {self.id}: stdio mode needs input_format")
        if self.signature is not None:
            sig = self.signature
            for i, s in enumerate(self.samples, 1):
                if len(s.args) != len(sig.params):
                    raise ValueError(
                        f"part {self.id} sample {i}: {len(s.args)} args, "
                        f"signature has {len(sig.params)} params"
                    )
                for p, a in zip(sig.params, s.args, strict=True):
                    err = check_value(p.tnode, a, f"part {self.id} sample {i} {p.name}")
                    if err:
                        raise ValueError(err)
                err = check_value(
                    sig.output_type, s.expected, f"part {self.id} sample {i} expected"
                )
                if err:
                    raise ValueError(err)
        return self

    def _check_ops(self) -> Part:
        from dsajudge.model.design import check_sequence

        if self.design is None:
            raise ValueError(f"part {self.id}: ops mode needs a design (class, methods)")
        if self.signature is not None:
            raise ValueError(
                f"part {self.id}: ops mode describes the class in design, not signature"
            )
        if self.input_format or self.output_format:
            raise ValueError(f"part {self.id}: input_format/output_format are for stdio mode")
        if self.checker in ("unordered", "unordered_deep"):
            raise ValueError(
                f"part {self.id}: the result list of an ops part is ordered; "
                "use exact, float:EPS or custom"
            )
        for i, smp in enumerate(self.samples, 1):
            if len(smp.args) != 2:
                raise ValueError(
                    f"part {self.id} sample {i}: args must be [operations, arguments] "
                    f"(two lists), got {len(smp.args)} value(s)"
                )
            err = check_sequence(
                self.design, smp.args[0], smp.args[1], smp.expected, f"part {self.id} sample {i}"
            )
            if err:
                raise ValueError(err)
        return self

    @property
    def sig(self) -> Signature:
        assert self.signature is not None, f"{self.mode} parts have no signature"
        return self.signature

    @property
    def dsg(self) -> Design:
        assert self.design is not None, f"{self.mode} parts have no design"
        return self.design

    @property
    def wire_names(self) -> list[str]:
        """Names of the lines of a test input file (the input is one JSON value per line)."""
        if self.mode == "ops":
            return ["ops", "args"]
        return [p.name for p in self.sig.params]

    def derived_oracle(self) -> Literal["ref", "brute-only", "promoted"] | None:
        if any(r.origin == "promoted" for r in self.refs):
            return "promoted"
        if self.refs:
            return "ref"
        if self.brute:
            return "brute-only"
        return None


class Problem(_Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schema")
    slug: str
    title: str
    tags: list[str] = Field(default_factory=list)
    source: str | None = None
    parts: list[Part]

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", v):
            raise ValueError(f"slug {v!r}: use lowercase letters, digits and dashes")
        return v

    @model_validator(mode="after")
    def _parts(self) -> Problem:
        if not self.parts:
            raise ValueError("a problem needs at least one part")
        ids = [p.id for p in self.parts]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError(f"part ids must be 1..n in order, got {ids}")
        return self

    def part(self, n: int) -> Part:
        if not 1 <= n <= len(self.parts):
            raise KeyError(f"{self.slug} has no part {n}")
        return self.parts[n - 1]
