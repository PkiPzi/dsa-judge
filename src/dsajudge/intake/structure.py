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
from dsajudge.ai.schemas import (
    CodeBlockRef,
    ConstraintsOut,
    Extraction,
    ParamOut,
    Segmentation,
    SegmentedPart,
)
from dsajudge.gen.validator import validate_args
from dsajudge.intake.codefence import guess_lang
from dsajudge.intake.transcribe import code_blocks
from dsajudge.model.canon import dumps
from dsajudge.model.constraints import ConstraintError, parse_constraints
from dsajudge.model.schema import Part
from dsajudge.model.types import parse_type

FENCE_LANGS = {"c++": "cpp", "cpp": "cpp", "java": "java", "py": "python", "python": "python"}


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


def attach_unlinked_code(seg: Segmentation, sections: dict[int, str]) -> Segmentation:
    """The segmenter sometimes links no code at all. With a single part, every fenced block in
    the transcript is then taken as a solution of it (the last one is the most optimized)."""
    if seg.code or len(seg.parts) != 1:
        return seg
    for n, text in sorted(sections.items()):
        for k, (tag, code) in enumerate(code_blocks(text), 1):
            lang = FENCE_LANGS.get(tag.lower()) or guess_lang(code)
            seg.code.append(CodeBlockRef(section=n, block=k, lang=lang, part=1))
    return seg


def final_refs(seg: Segmentation, sections: dict[int, str]) -> Segmentation:
    """Photographed solutions run from brute to optimized, often in several languages: of the
    solutions linked to a part only the last one that defines a function stays a ref (the
    optimized one); earlier ones would all have to agree with it and one OCR slip would sink
    the build. Brutes are kept."""
    keep: list[CodeBlockRef] = []
    for part in {c.part for c in seg.code}:
        refs = [c for c in seg.code if c.part == part and c.role == "ref"]
        defines = [c for c in refs if _defines_function(c, sections)]
        keep += (defines or refs)[-1:]
    seg.code = [c for c in seg.code if c.role != "ref" or c in keep]
    return seg


def _defines_function(c: CodeBlockRef, sections: dict[int, str]) -> bool:
    blocks = code_blocks(sections.get(c.section, ""))
    if not 1 <= c.block <= len(blocks):
        return False
    code = blocks[c.block - 1][1]
    named = (m.group("name") for m in SIG.finditer(code))
    return any(n not in NOT_METHODS and n != "main" for n in named) or bool(
        re.search(r"^\s*def\s+\w+", code, re.MULTILINE)
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
    re.compile(r"^[ \t]*def\s+(\w+)\s*\(", re.MULTILINE),  # python
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
    found = list(
        dict.fromkeys(
            m.group(1)
            for c in codes
            for rx in METHOD_DEFS
            for m in rx.finditer(c)
            if m.group(1) not in NOT_METHODS and not m.group(1).startswith("__")
        )
    )
    if len(found) > 1:
        # helpers are called by the solver: the one nobody calls is the entry point
        text = "\n".join(codes)
        roots = [n for n in found if len(re.findall(rf"\b{re.escape(n)}\s*\(", text)) <= 1]
        # bare functions (no class around them): helpers come first, the solver is last
        free = not re.search(r"\bclass\b", text)
        found = roots if len(roots) == 1 else (found[-1:] if free else [])
    return found[0] if len(found) == 1 else current


BOUND_LINE = re.compile(r"\s*(?:<=|<|\u2264)\s*")
LENGTH_OF = re.compile(r"^(?:(\w+)\.(?:length|size)(?:\(\))?|len\((\w+)\))$")
ELEMENT_OF = re.compile(r"^(\w+)\[\w+\]$")
ASSUMED_CHARSET = "lowercase"
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
    human review, then decides). A string with no stated alphabet is taken as lowercase. Callers
    must still validate the result."""
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
            if p.name not in lens or "" in lens[p.name]:
                return None
            # no alphabet in the text: the narrowest guess can only thin the tests out, while a
            # wider one could feed characters the real problem forbids
            out[p.name] = {"len": lens[p.name], "charset": charset or ASSUMED_CHARSET}
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


SCALAR_ALIASES = {
    "integer": "int", "int32": "int", "int32_t": "int", "short": "int", "uint": "int",
    "longlong": "long", "int64": "long", "int64_t": "long", "ll": "long",
    "float": "double", "boolean": "bool", "str": "string", "character": "char",
}  # fmt: skip
CONTAINER = re.compile(r"(?:vector|list|arraylist|array|sequence)\s*[<\[](.*)[>\]]", re.IGNORECASE)


def normalize_type(text: str) -> str:
    """Map the C++/Java/Python spelling of a type onto this tool's (``vector<int>``,
    ``List[int]``, ``String``, ``ListNode*``, ``Integer[]`` ...); unknown text is returned
    unchanged so the validator reports it."""
    s = re.sub(r"\bOptional\[(.*)\]", r"\1", text.strip())
    s = re.sub(r"\bconst\b|std::|[*&]", "", s).strip()
    depth = 0
    while True:
        if (m := CONTAINER.fullmatch(s)) or (m := re.fullmatch(r"(.*)\[\]", s)):
            s, depth = m.group(1).strip(), depth + 1
        else:
            break
    low = s.replace(" ", "").lower()
    base = {"listnode": "ListNode", "treenode": "TreeNode", "void": "void"}.get(low)
    base = base or SCALAR_ALIASES.get(low, low)
    return base + "[]" * depth


SIG = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|static|final)\s+)*"
    r"(?P<ret>[\w:<>,&*\[\]]+(?:[ \t]+[\w:<>,&*\[\]]+)*?)[ \t]+[&*]?(?P<name>\w+)\s*"
    r"\((?P<params>[^;{}()]*)\)\s*(?:const\s*)?(?:throws\s+[\w, ]+)?\{",
    re.MULTILINE,
)


def _split_params(text: str) -> list[str]:
    """Split a parameter list on the commas that are not inside <...>."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "<") - (ch == ">")
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def signature_from_code(
    code: str, prefer: str = ""
) -> tuple[str, list[tuple[str, str]], str] | None:
    """(method, [(param name, type)], return type) read off a C++ or Java solution's method
    header, with every type mapped onto the tool's types; None when the code has no single
    clear method or a type has no equivalent. Python has no types to read, so it is skipped."""
    found = [
        m
        for m in SIG.finditer(code)
        if m.group("name") not in NOT_METHODS and m.group("ret") not in NOT_METHODS
    ]
    if len(found) > 1:  # helpers around the solver: take the one the problem names
        found = [m for m in found if m.group("name") == prefer]
    if len(found) != 1:
        return None
    m = found[0]
    ret = normalize_type(m.group("ret"))
    params: list[tuple[str, str]] = []
    for raw in _split_params(m.group("params")):
        bits = re.sub(r"\bconst\b|\bfinal\b", "", raw).strip()
        mm = re.fullmatch(r"(.+?)\s*[&*]?\s*(\w+)((?:\[\])*)", bits)
        if not mm:
            return None
        params.append((mm.group(2), normalize_type(mm.group(1) + mm.group(3))))
    try:
        for _, typ in params:
            parse_type(typ)
        parse_type(ret, allow_void=True)
    except ValueError:
        return None
    return m.group("name"), params, ret


PY_DEF = re.compile(
    r"^[ \t]*def[ \t]+(\w+)[ \t]*\([ \t]*(?:self\b[ \t]*,?)?([^)]*)\)"
    r"[ \t]*(?:->[ \t]*([^:]+?))?[ \t]*:",
    re.MULTILINE,
)


def python_signature(
    code: str, prefer: str = ""
) -> tuple[str, list[tuple[str, str | None]], str | None] | None:
    """(method, [(param, annotation type or None)], return type or None) of the solution's
    method; what the code does not state stays None. ``prefer`` names the wanted method when the
    class has helpers; otherwise the first method is the answer."""
    defs = [m for m in PY_DEF.finditer(code) if not m.group(1).startswith("__")]
    if not defs:
        return None
    m = next((d for d in defs if d.group(1) == prefer), defs[0])
    params: list[tuple[str, str | None]] = []
    for raw in _split_params((m.group(2) or "").replace("[", "<").replace("]", ">")):
        raw = raw.split("=")[0].strip()
        if not raw:
            continue
        name, _, ann = raw.partition(":")
        typ = (
            normalize_type(ann.strip().replace("<", "[").replace(">", "]")) if ann.strip() else None
        )
        params.append((name.strip(), typ))
    ret = m.group(3)
    if ret is not None:
        ret = "void" if ret.strip() == "None" else normalize_type(ret)
    else:
        body = code[m.end() :].split("\n    def ")[0]
        if not re.search(r"^\s*return\s+\S", body, re.MULTILINE):
            ret = "void"  # nothing is ever returned: it works in place
    return m.group(1), params, ret


def apply_python_signature(ext: Extraction, codes: list[str]) -> str | None:
    """Adopt the python solution's parameters; a message when its untyped parameter list does
    not match the extracted one (the model is then asked again)."""
    for code in codes:
        sig = python_signature(code, ext.method)
        if sig is None:
            continue
        method, params, ret = sig
        ext.method = method
        if len(params) == len(ext.params):  # the model's names stay; the code's types win
            for p, (_, typ) in zip(ext.params, params, strict=True):
                if typ:
                    p.type = typ
        elif all(typ for _, typ in params):
            ext.params = [ParamOut(name=n, type=str(typ)) for n, typ in params]
        else:
            return (
                f"the solution defines {method}({', '.join(n for n, _ in params)}): list exactly "
                "those parameters, in that order, each with its type"
            )
        if ret:
            ext.returns = ret
            if ret != "void":
                ext.output_param = None
        return None
    return None


def apply_code_signature(ext: Extraction, codes: list[str]) -> str | None:
    """Make the extracted signature agree with the photographed solution (see above). The
    model's parameter names are kept when the count matches: the constraints use them."""
    if msg := apply_python_signature(ext, [c for c in codes if PY_DEF.search(c)]):
        return msg
    for code in codes:
        sig = signature_from_code(code, ext.method)
        if sig is None:
            continue
        method, params, ret = sig
        keep_names = len(params) == len(ext.params)
        ext.method = method
        ext.params = [
            ParamOut(name=ext.params[i].name if keep_names else n, type=typ)
            for i, (n, typ) in enumerate(params)
        ]
        ext.returns = ret
        if ret != "void":
            ext.output_param = None
        return None
    return None


EXAMPLE_WORDS = re.compile(r"example|sample|input|output|e\.g\.|=>|->", re.IGNORECASE)


def extract_part(
    llm: LLM,
    part_no: int,
    text: str,
    context: str,
    attempts: int,
    codes: list[str] | None = None,
) -> Extraction:
    def check(ext: Extraction) -> str | None:
        for p in ext.params:
            p.type = normalize_type(p.type)
        ext.returns = normalize_type(ext.returns)
        ext.method = method_from_code(ext.method, codes or [])
        if msg := apply_code_signature(ext, codes or []):
            return msg
        if ext.returns == "void" and not ext.output_param:
            # an in-place solution: the first array / list / tree parameter is what it changes
            ext.output_param = next(
                (p.name for p in ext.params if p.type.endswith("]") or p.type[0].isupper()), None
            )
        if not EXAMPLE_WORDS.search(text):
            ext.samples = []  # the text has no examples: whatever the model listed is invented
        try:
            part_model(ext, {}, part_no)
        except (ValidationError, ValueError) as e:
            msg = _pydantic_message(e) if isinstance(e, ValidationError) else str(e)
            if re.search(r"\bsample \d", msg) and ext.samples:
                # examples that do not fit the signature are dropped: the solution makes new ones
                ext.samples = []
                return check(ext)
            return msg
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


def infer_constraints(
    llm: LLM, ext: Extraction, code: str, part_no: int, attempts: int, feedback: str = ""
) -> dict[str, Any]:
    """Constraints for a statement that gives none, sized from the optimized solution's code."""

    def check(out: ConstraintsOut) -> str | None:
        try:
            parse_constraints(part_model(ext, out.constraints, part_no))
        except (ConstraintError, ValidationError, ValueError) as e:
            return str(e.message) if isinstance(e, ConstraintError) else str(e)
        return None

    return ask_structured(
        llm,
        "constraints_from_solution",
        ConstraintsOut,
        {"signature": signature_line(ext), "code": code.rstrip("\n"), "feedback": feedback},
        attempts=attempts,
        check=check,
        feedback_var="feedback",
    ).constraints
