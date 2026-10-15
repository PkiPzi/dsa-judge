"""Java backend: javac + generated Main.java with a big-stack thread."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dsajudge.config import JudgeConfig
from dsajudge.judge.cache import build_cached, run_tool, tool_version
from dsajudge.lang.base import Built, Program
from dsajudge.lang.codegen import render, static, stdio_ctx, uses
from dsajudge.model.schema import Lang, Param, Part
from dsajudge.model.types import TypeNode, java_list_depth, java_type

# Prefixed onto line 1 of the user's file so line numbers in errors stay right.
IMPLICIT_IMPORTS = "import java.util.*;import java.util.function.*;import java.util.stream.*;"

_ARR_FN = {
    "int": "intArr",
    "long": "longArr",
    "double": "doubleArr",
    "bool": "boolArr",
    "char": "charArr",
    "string": "strArr",
}
_SCALAR_FN = {
    "int": "toInt",
    "long": "toLong",
    "double": "toDouble",
    "bool": "toBool",
    "char": "toChar",
    "string": "toStr",
}


def java_conv(t: TypeNode, list_depth: int, expr: str) -> str:
    """Java expression converting parsed JSON ``expr`` to the Java type of ``t``."""
    if t.kind == "listnode":
        return f"DsaJson.listNode({expr})"
    if t.kind == "treenode":
        return f"DsaJson.treeNode({expr})"
    if t.kind == "scalar":
        return f"DsaJson.{_SCALAR_FN[t.base]}({expr})"
    if list_depth == 0:
        suffix = "2" if t.depth == 2 else ""
        return f"DsaJson.{_ARR_FN[t.base]}{suffix}({expr})"
    var = f"x{t.depth}"
    inner = java_conv(t.elem(), list_depth - 1, var)
    return f"DsaJson.list({expr}, {var} -> {inner})"


def _ctx(slug: str, part: Part) -> dict[str, Any]:
    sig = part.sig
    params = []
    for i, p in enumerate(sig.params):
        override = sig.override("java", p.name)
        t = p.tnode
        params.append(
            {
                "name": p.name,
                "var": f"a{i}",
                "java_type": java_type(t, override),
                "java_conv": java_conv(t, java_list_depth(t, override), f"inp.get({i})"),
            }
        )
    types = [p.type for p in sig.params] + [sig.returns]
    out_var = ""
    if sig.ret.is_void:
        idx = [p.name for p in sig.params].index(sig.output_param or "")
        out_var = f"a{idx}"
    writer = {"listnode": "writeListNode", "treenode": "writeTreeNode"}.get(
        sig.output_type.kind, "write"
    )
    return {
        "writer": writer,
        "slug": slug,
        "part_id": part.id,
        "title": part.title,
        "method": sig.method,
        "params": params,
        "params_decl": ", ".join(f"{p['java_type']} {p['name']}" for p in params),
        "call_args": ", ".join(p["var"] for p in params),
        "ret_java": java_type(sig.ret, sig.override("java", "return")),
        "is_void": sig.ret.is_void,
        "output_var": out_var,
        "uses_listnode": uses(types, "ListNode"),
        "uses_treenode": uses(types, "TreeNode"),
    }


def _ops_ctx(slug: str, part: Part) -> dict[str, Any]:
    d = part.dsg
    types = [p.type for p in d.constructor]

    def params(ps: list[Param], prefix: str) -> list[dict[str, str]]:
        return [
            {
                "name": p.name,
                "var": f"{prefix}{i}",
                "java_type": java_type(p.tnode),
                "java_conv": java_conv(p.tnode, 0, f"ja.get({i})"),
            }
            for i, p in enumerate(ps)
        ]

    ctor = params(d.constructor, "c")
    methods = []
    for m in d.methods:
        ps = params(m.params, "a")
        types += [p.type for p in m.params] + [m.returns]
        writer = {"listnode": "writeListNode", "treenode": "writeTreeNode"}.get(m.ret.kind, "write")
        methods.append(
            {
                "name": m.name,
                "params": ps,
                "params_decl": ", ".join(f"{p['java_type']} {p['name']}" for p in ps),
                "call_args": ", ".join(p["var"] for p in ps),
                "doc_args": ", ".join(p["name"] for p in ps),
                "ret_java": java_type(m.ret),
                "is_void": m.ret.is_void,
                "writer": writer,
            }
        )
    return {
        "slug": slug,
        "part_id": part.id,
        "title": part.title,
        "class_name": d.class_name,
        "ctor": ctor,
        "ctor_decl": ", ".join(f"{p['java_type']} {p['name']}" for p in ctor),
        "ctor_call": ", ".join(p["var"] for p in ctor),
        "ctor_doc": ", ".join(p["name"] for p in ctor),
        "methods": methods,
        "uses_listnode": uses(types, "ListNode"),
        "uses_treenode": uses(types, "TreeNode"),
    }


class Java:
    name: Lang = "java"
    ext = "java"
    sol_filename = "Solution.java"

    def stub(self, slug: str, part: Part) -> str:
        if part.mode == "ops":
            return render("java_ops_stub.java.j2", **_ops_ctx(slug, part))
        return render("java_stub.java.j2", **_ctx(slug, part))

    def driver_files(self, slug: str, part: Part) -> dict[str, str]:
        main = (
            render("Main_ops.java.j2", **_ops_ctx(slug, part))
            if part.mode == "ops"
            else render("Main.java.j2", **_ctx(slug, part))
        )
        return {
            "Main.java": main,
            "DsaJson.java": static("DsaJson.java"),
            "ListNode.java": static("ListNode.java"),
            "TreeNode.java": static("TreeNode.java"),
        }

    def build(
        self, src: Path, slug: str, part: Part, cfg: JudgeConfig, *, debug: bool = False
    ) -> Built:
        files = self.driver_files(slug, part)
        files["Solution.java"] = IMPLICIT_IMPORTS + src.read_text(encoding="utf-8")

        def compile_fn(d: Path) -> None:
            run_tool(
                [
                    cfg.javac,
                    "-encoding",
                    "UTF-8",
                    "-nowarn",
                    "-d",
                    "classes",
                    *(
                        str(d / f)
                        for f in (
                            "Main.java",
                            "DsaJson.java",
                            "ListNode.java",
                            "TreeNode.java",
                            "Solution.java",
                        )
                    ),
                ],
                cwd=d,
            )

        out = build_cached("java", files, [tool_version(cfg.javac)], compile_fn)
        return Built(
            lang="java",
            argv=[
                cfg.java,
                "-Xmx{mem}m",
                "-XX:+UseSerialGC",
                "-Dfile.encoding=UTF-8",
                "-cp",
                str(out / "classes"),
                "Main",
                "{fd}",
            ],
            cwd=out,
            mem_overhead_mb=192,
            startup_s=3.0,
            debug=debug,
        )

    def stdio_stub(self, slug: str, part: Part) -> str:
        return render("stdio_stub.java.j2", **stdio_ctx(slug, part))

    def build_program(self, src: Path, cfg: JudgeConfig, *, debug: bool = False) -> Program:
        files = {"Solution.java": IMPLICIT_IMPORTS + src.read_text(encoding="utf-8")}

        def compile_fn(d: Path) -> None:
            run_tool(
                [
                    cfg.javac,
                    "-encoding",
                    "UTF-8",
                    "-nowarn",
                    "-d",
                    "classes",
                    str(d / "Solution.java"),
                ],
                cwd=d,
            )

        out = build_cached("java-stdio", files, [tool_version(cfg.javac)], compile_fn)
        return Program(
            lang="java",
            argv=[
                cfg.java,
                "-Xmx{mem}m",
                "-Xss512m",
                "-XX:+UseSerialGC",
                "-Dfile.encoding=UTF-8",
                "-cp",
                str(out / "classes"),
                "Solution",
            ],
            cwd=out,
            mem_overhead_mb=192,
            startup_ms=250.0,
            debug=debug,
        )
