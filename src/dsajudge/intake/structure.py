"""Structure stage (PLAN.md §7.1): two narrow, schema-constrained passes, then the constraint DSL.

(a) segmentation: which text is which part, which code blocks are refs or brutes;
(b) per-part extraction: title, statement, signature, samples, checker, guarantees;
(c) constraint filling: free-text constraints -> the DSL, verified against the samples.
Small models do much better on narrow prompts, and every answer is checked in code.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from dsajudge.ai.base import LLM, AIError
from dsajudge.ai.retry import ask_structured
from dsajudge.ai.schemas import ConstraintsOut, Extraction, Segmentation, SegmentedPart
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


FOLLOWUP = re.compile(
    r"^\W*(follow[- ]?up|part\s*\d|now\b|what if|can you|could you|extend|modify|suppose"
    r"|instead|variation|next\b)",
    re.IGNORECASE,
)


def merge_false_followups(seg: Segmentation) -> Segmentation:
    """For a transcript known to be ONE question: a later part is kept only if its text opens like
    a labelled follow-up. A small model often cuts the statement from its constraints/examples
    and calls the rest "part 2"; those pieces are joined back into the part before them."""
    parts: list[SegmentedPart] = []
    where: dict[int, int] = {}
    for sp in seg.parts:
        if parts and not FOLLOWUP.match(sp.text.lstrip()):
            parts[-1] = SegmentedPart(part=parts[-1].part, text=f"{parts[-1].text}\n{sp.text}")
        else:
            parts.append(SegmentedPart(part=len(parts) + 1, text=sp.text))
        where[sp.part] = len(parts)
    code = []
    taken: set[tuple[int, str, str]] = set()
    for c in seg.code:
        c = c.model_copy(update={"part": where.get(c.part, 1)})
        key = (c.part, c.lang, c.role)
        if key not in taken:  # two blocks for the same part/language/role: keep the first
            taken.add(key)
            code.append(c)
    return Segmentation(title=seg.title, parts=parts, code=code)


_TYPE_TOKEN = r"[\w:<>,&*\[\]]+"
METHOD_DEFS = (
    re.compile(r"^[ \t]*def\s+(\w+)\s*\(\s*self\b", re.MULTILINE),  # python
    re.compile(  # c++ / java: "<modifiers> <type> name(args) {" (also const / throws)
        rf"^[ \t]*(?:(?:public|private|protected|static)\s+)*{_TYPE_TOKEN}(?:[ \t]+{_TYPE_TOKEN})*"
        r"[ \t]+[&*]?(\w+)\s*\([^;{}]*\)\s*(?:const\s*)?(?:throws\s+[\w, ]+)?\{",
        re.MULTILINE,
    ),
)
NOT_METHODS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "else",
    "sizeof",
    "main",
    "Solution",
}


def method_from_code(current: str, codes: list[str]) -> str:
    """The method the solution code defines, when the extracted name appears nowhere in it.

    The model reads the method name off the statement; the photographed solution is the truth
    (a model that writes ``sumEvenElements`` for code defining ``sumEven`` breaks every build).
    Only an unambiguous case is changed: the name is absent and the code defines one method.
    """
    if any(re.search(rf"\b{re.escape(current)}\s*\(", c) for c in codes):
        return current
    found = {
        m.group(1)
        for c in codes
        for rx in METHOD_DEFS
        for m in rx.finditer(c)
        if m.group(1) not in NOT_METHODS and not m.group(1).startswith("__")
    }
    return found.pop() if len(found) == 1 else current


BOUND_LINE = re.compile(r"\s*(?:<=|<|\u2264)\s*")
LENGTH_OF = re.compile(r"^(?:(\w+)\.(?:length|size)(?:\(\))?|len\((\w+)\))$")
ELEMENT_OF = re.compile(r"^(\w+)\[\w+\]$")
CHARSETS = (
    ("lowercase", "lowercase"),
    ("uppercase", "uppercase"),
    ("alphanumeric", "alnum"),
    ("digits", "digits"),
    ("english letters", "letters"),
    ("letters", "letters"),
    ("binary", "binary"),
)


def _bound(text: str) -> str:
    return re.sub(r"\^\{(\d+)\}", r"^\1", text.strip().replace(" ", ""))


def constraints_from_text(ext: Extraction) -> dict[str, Any] | None:
    """The constraint DSL read straight off ``lo <= nums.length <= hi`` style lines, for the
    simple parameter types; None when something needed is missing (the model's answer, or a
    human review, then decides). Callers must still validate the result."""
    lens: dict[str, list[str]] = {}
    ranges: dict[str, list[str]] = {}  # element / scalar bounds
    for line in ext.constraints_text:
        pieces = [p for p in BOUND_LINE.split(line.strip().rstrip(".")) if p]
        if len(pieces) == 3:
            lo, mid, hi = (_bound(p) for p in pieces)
        elif (
            len(pieces) == 2 and re.match(r"^[-+\w]", pieces[0].strip()) and BOUND_LINE.search(line)
        ):
            first = _bound(pieces[0])
            if re.fullmatch(r"[-+]?[\d^e*]+", first):  # "lo <= x"
                lo, mid, hi = first, _bound(pieces[1]), ""
            else:  # "x <= hi"
                lo, mid, hi = "", first, _bound(pieces[1])
        else:
            continue
        if m := LENGTH_OF.match(mid):
            lens[m.group(1) or m.group(2)] = [lo or "1", hi]
        elif m := ELEMENT_OF.match(mid):
            ranges[m.group(1)] = [lo, hi]
        elif re.fullmatch(r"\w+", mid):
            ranges[mid] = [lo, hi]
    text = (ext.statement + " " + " ".join(ext.constraints_text)).lower()
    charset = next((cs for word, cs in CHARSETS if word in text), None)
    out: dict[str, Any] = {}
    for p in ext.params:
        if p.type == "bool":
            continue
        if p.type in ("int", "long", "double"):
            if p.name not in ranges or "" in ranges[p.name]:
                return None
            out[p.name] = {"range": ranges[p.name]}
        elif p.type == "string":
            if p.name not in lens or "" in lens[p.name] or not charset:
                return None
            out[p.name] = {"len": lens[p.name], "charset": charset}
        elif p.type in ("int[]", "long[]", "double[]"):
            rng = ranges.get(p.name)
            if p.name not in lens or "" in lens[p.name] or not rng or "" in rng:
                return None
            out[p.name] = {"len": lens[p.name], "each": rng}
        else:
            return None
    return out


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

    try:
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
    except AIError:
        # the model could not do it: read the plain "lo <= x <= hi" lines in code, but only
        # accept the result if it passes the very same checks
        guess = constraints_from_text(ext)
        if guess is not None and check(ConstraintsOut(constraints=guess)) is None:
            return guess
        raise
    return out.constraints
