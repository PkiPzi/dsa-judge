"""Python backend: generated driver.py that execs the solution in a LeetCode-like namespace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dsajudge.config import JudgeConfig
from dsajudge.judge.cache import build_cached, tool_version
from dsajudge.lang.base import Built, CompileError, Program
from dsajudge.lang.codegen import render, stdio_ctx, uses
from dsajudge.model.schema import Lang, Part
from dsajudge.model.types import py_type


def _ctx(slug: str, part: Part) -> dict[str, Any]:
    sig = part.sig
    types = [p.type for p in sig.params] + [sig.returns]
    out_index = None
    if sig.ret.is_void:
        out_index = [p.name for p in sig.params].index(sig.output_param or "")
    return {
        "slug": slug,
        "part_id": part.id,
        "title": part.title,
        "method": sig.method,
        "param_types": [str(p.tnode) for p in sig.params],
        "out_type": str(sig.output_type),
        "out_index": out_index,
        "params_decl": "".join(f", {p.name}: {py_type(p.tnode)}" for p in sig.params),
        "ret_py": py_type(sig.ret),
        "output_param": sig.output_param,
        "uses_listnode": uses(types, "ListNode"),
        "uses_treenode": uses(types, "TreeNode"),
    }


def _ops_ctx(slug: str, part: Part) -> dict[str, Any]:
    d = part.dsg
    types = [p.type for p in d.constructor]
    methods = []
    for m in d.methods:
        types += [p.type for p in m.params] + [m.returns]
        methods.append(
            {
                "name": m.name,
                "params_decl": "".join(f", {p.name}: {py_type(p.tnode)}" for p in m.params),
                "ret_py": py_type(m.ret),
                "is_void": m.ret.is_void,
                "call_args": ", ".join(p.name for p in m.params),
            }
        )
    return {
        "slug": slug,
        "part_id": part.id,
        "title": part.title,
        "class_name": d.class_name,
        "ctor_types": [str(p.tnode) for p in d.constructor],
        "ctor_decl": "".join(f", {p.name}: {py_type(p.tnode)}" for p in d.constructor),
        "ctor_call": ", ".join(p.name for p in d.constructor),
        "methods": methods,
        "uses_listnode": uses(types, "ListNode"),
        "uses_treenode": uses(types, "TreeNode"),
    }


def _syntax_check(code: str) -> None:
    try:
        compile(code, "<build>/sol.py", "exec")
    except SyntaxError as e:
        line = f"<build>/sol.py:{e.lineno}:{e.offset or 0}: "
        text = (e.text or "").rstrip("\n")
        caret = " " * max((e.offset or 1) - 1, 0) + "^"
        raise CompileError(f"{line}SyntaxError: {e.msg}\n    {text}\n    {caret}") from e


class Python:
    name: Lang = "python"
    ext = "py"
    sol_filename = "sol.py"

    def stub(self, slug: str, part: Part) -> str:
        if part.mode == "ops":
            return render("py_ops_stub.py.j2", **_ops_ctx(slug, part))
        return render("py_stub.py.j2", **_ctx(slug, part))

    def driver_files(self, slug: str, part: Part) -> dict[str, str]:
        if part.mode == "ops":
            ctx = _ops_ctx(slug, part)
            ctx["methods"] = {
                m.name: {"params": [str(p.tnode) for p in m.params], "returns": str(m.ret)}
                for m in part.dsg.methods
            }
            return {"driver.py": render("py_ops_driver.py.j2", **ctx)}
        return {"driver.py": render("py_driver.py.j2", **_ctx(slug, part))}

    def build(
        self, src: Path, slug: str, part: Part, cfg: JudgeConfig, *, debug: bool = False
    ) -> Built:
        code = src.read_text(encoding="utf-8")
        _syntax_check(code)
        files = self.driver_files(slug, part)
        files["sol.py"] = code
        exe = cfg.python_exe()
        out = build_cached("python", files, [tool_version(exe)], lambda d: None)
        return Built(
            lang="python",
            argv=[exe, "-X", "utf8", "-B", str(out / "driver.py"), "{fd}", str(out / "sol.py")],
            cwd=out,
            mem_overhead_mb=32,
            startup_s=1.0,
            debug=debug,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"},
        )

    def stdio_stub(self, slug: str, part: Part) -> str:
        return render("stdio_stub.py.j2", **stdio_ctx(slug, part))

    def build_program(self, src: Path, cfg: JudgeConfig, *, debug: bool = False) -> Program:
        code = src.read_text(encoding="utf-8")
        _syntax_check(code)
        exe = cfg.python_exe()
        out = build_cached("python-stdio", {"sol.py": code}, [tool_version(exe)], lambda d: None)
        return Program(
            lang="python",
            argv=[exe, "-X", "utf8", "-B", str(out / "sol.py")],
            cwd=out,
            mem_overhead_mb=32,
            startup_ms=40.0,
            debug=debug,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"},
        )
