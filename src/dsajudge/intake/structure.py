"""Structure stage (PLAN.md §7.1): two narrow, schema-constrained passes, then the constraint DSL.

(a) segmentation: which text is which part, which code blocks are refs or brutes;
(b) per-part extraction: title, statement, signature, samples, checker, guarantees;
(c) constraint filling: free-text constraints -> the DSL, verified against the samples.
Small models do much better on narrow prompts, and every answer is checked in code.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from dsajudge.ai.base import LLM, AIError
from dsajudge.ai.retry import ask_structured
from dsajudge.ai.schemas import ConstraintsOut, Extraction, Segmentation
from dsajudge.gen.validator import validate_args
from dsajudge.intake.transcribe import code_blocks
from dsajudge.model.canon import dumps
from dsajudge.model.constraints import ConstraintError, parse_constraints
from dsajudge.model.schema import Part


def segment(
    llm: LLM, transcript: str, sections: dict[int, str], attempts: int, context: str = ""
) -> Segmentation:
    def check(seg: Segmentation) -> str | None:
        nums = [p.part for p in seg.parts]
        if nums != list(range(1, len(nums) + 1)):
            return f"parts must be numbered 1, 2, 3, ... in order; you gave {nums}"
        for c in seg.code:
            blocks = code_blocks(sections.get(c.section, ""))
            if not blocks:
                return f"section {c.section} has no code block"
            if not 1 <= c.block <= len(blocks):
                return f"section {c.section} has {len(blocks)} code block(s), not block {c.block}"
            if c.part not in nums:
                return f"code in section {c.section} refers to part {c.part}, which does not exist"
        return None

    return ask_structured(
        llm,
        "segment",
        Segmentation,
        {"transcript": context + transcript},
        attempts=attempts,
        check=check,
    )


def part_model(ext: Extraction, constraints: dict[str, Any], part_id: int) -> Part:
    """The Part this extraction describes (validates types, samples against the signature)."""
    return Part.model_validate(part_data(ext, constraints, part_id, f"part{part_id}/statement.md"))


def part_data(
    ext: Extraction, constraints: dict[str, Any], part_id: int, statement: str
) -> dict[str, Any]:
    sig: dict[str, Any] = {
        "method": ext.method,
        "params": [{"name": p.name, "type": p.type} for p in ext.params],
        "returns": ext.returns,
    }
    if ext.output_param:
        sig["output_param"] = ext.output_param
    data: dict[str, Any] = {
        "id": part_id,
        "title": ext.title,
        "statement": statement,
        "mode": "function",
        "signature": sig,
        "constraints": constraints,
        "samples": [{"args": s.args, "expected": s.expected} for s in ext.samples],
        "checker": ext.checker,
    }
    if ext.guarantees:
        data["guarantees"] = ext.guarantees
    return data


def _pydantic_message(e: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(x) for x in err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
        for err in e.errors()
    )


def extract_part(llm: LLM, part_no: int, text: str, context: str, attempts: int) -> Extraction:
    def check(ext: Extraction) -> str | None:
        try:
            part_model(ext, {}, part_no)
        except ValidationError as e:
            return _pydantic_message(e)
        except ValueError as e:
            return str(e)
        return None

    return ask_structured(
        llm,
        "extract_part",
        Extraction,
        {"part": str(part_no), "text": text, "context": context},
        attempts=attempts,
        check=check,
    )


def signature_line(ext: Extraction) -> str:
    params = ", ".join(f"{p.name}: {p.type}" for p in ext.params)
    return f"{ext.method}({params}) -> {ext.returns}"


def follow_up_context(prev: Extraction, prev_constraints: dict[str, Any], prev_no: int) -> str:
    lines = [f"This part follows part {prev_no}, whose method is {signature_line(prev)}."]
    if prev.constraints_text:
        lines.append("Its constraints were:")
        lines += [f"- {c}" for c in prev.constraints_text]
    lines.append("A follow-up usually changes only a little; keep what the text does not change.")
    return "\n".join(lines) + "\n\n"


def fill_constraints(llm: LLM, ext: Extraction, part_no: int, attempts: int) -> dict[str, Any]:
    """The constraint DSL for a part, verified: it must parse and accept every sample."""
    if not ext.constraints_text:
        raise AIError("the text states no constraints")

    def check(out: ConstraintsOut) -> str | None:
        try:
            part = part_model(ext, out.constraints, part_no)
            specs = parse_constraints(part)
        except (ConstraintError, ValidationError, ValueError) as e:
            return str(e.message) if isinstance(e, ConstraintError) else str(e)
        for i, s in enumerate(part.samples, 1):
            errs = validate_args(specs, s.args)
            if errs:
                return (
                    f"example {i} {dumps(s.args)[:200]} violates your constraints: {errs[0]}. "
                    "Either the constraint is wrong or you misread a number."
                )
        return None

    out = ask_structured(
        llm,
        "fill_constraints",
        ConstraintsOut,
        {
            "signature": signature_line(ext),
            "constraints": "\n".join(ext.constraints_text),
            "feedback": "",
        },
        attempts=attempts,
        check=check,
        feedback_var="feedback",
    )
    return out.constraints
