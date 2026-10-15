"""problem.yaml schema (PLAN.md §4.2). Version 1.

After Phase 1 ships, changes to this schema need a migration, not a breaking edit.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    mode: Literal["function", "stdio"] = "function"
    signature: Signature | None = None
    constraints: dict[str, dict[str, Any]] = Field(default_factory=dict)
    guarantees: list[str] = Field(default_factory=list)
    samples: list[Sample] = Field(default_factory=list)
    checker: str = "exact"
    refs: list[SourceFile] = Field(default_factory=list)
    brute: SourceFile | None = None
    oracle: Literal["ref", "brute-only", "promoted"] | None = None
    limits: Limits = Field(default_factory=Limits)
    input_format: str | None = None

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
        if self.mode == "function":
            if self.signature is None:
                raise ValueError(f"part {self.id}: function mode needs a signature")
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

    @property
    def sig(self) -> Signature:
        assert self.signature is not None, "stdio parts have no signature"
        return self.signature

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
