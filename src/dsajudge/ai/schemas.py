"""Structured outputs of the intake passes. Used as Ollama ``format`` schemas and to validate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CodeBlockRef(_M):
    section: int
    block: int = 1
    lang: Literal["cpp", "java", "python"]
    role: Literal["ref", "brute"] = "ref"
    part: int = 1


class SegmentedPart(_M):
    part: int
    text: str


class Segmentation(_M):
    title: str = ""
    parts: list[SegmentedPart] = Field(min_length=1)
    code: list[CodeBlockRef] = Field(default_factory=list)


class BundleStart(_M):
    first_line: str  # the first line of the new question, copied from the page
    title: str = ""


class BundleScan(_M):
    """Where, on one page of a photo bundle, a new question begins (none: the page continues)."""

    starts: list[BundleStart] = Field(default_factory=list)


class ParamOut(_M):
    name: str
    type: str


class SampleOut(_M):
    args: list[Any]
    expected: Any = None


class Extraction(_M):
    title: str
    statement: str
    method: str
    params: list[ParamOut]
    returns: str
    output_param: str | None = None
    constraints_text: list[str] = Field(default_factory=list)
    samples: list[SampleOut] = Field(default_factory=list)
    checker: str = "exact"
    guarantees: list[str] = Field(default_factory=list)


class ConstraintsOut(_M):
    constraints: dict[str, dict[str, Any]]


class CodeOut(_M):
    lang: Literal["cpp", "java", "python"]
    code: str


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Self-contained JSON schema (no $ref/$defs), which constrained decoders handle best."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(defs[node["$ref"].rsplit("/", 1)[-1]])
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "title" and isinstance(v, str):
                    continue  # pydantic's annotation, not a property named "title"
                if k == "properties":
                    out[k] = {name: inline(sub) for name, sub in v.items()}
                else:
                    out[k] = inline(v)
            return out
        if isinstance(node, list):
            return [inline(x) for x in node]
        return node

    out = inline(schema)
    assert isinstance(out, dict)
    return out
