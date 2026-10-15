"""C++ backend: g++ with a hand-written JSON driver."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from dsajudge.config import JudgeConfig, cache_dir
from dsajudge.judge.cache import build_cached, digest, file_lock, run_tool, tool_version
from dsajudge.lang.base import Built
from dsajudge.lang.codegen import render, static, uses
from dsajudge.model.schema import Lang, Part
from dsajudge.model.types import cpp_param, cpp_value

DEBUG_FLAGS = [
    "-std=c++20",
    "-g",
    "-O0",
    "-fno-omit-frame-pointer",
    "-fsanitize=address,undefined",
    "-D_GLIBCXX_DEBUG",
]


def _ctx(slug: str, part: Part) -> dict[str, Any]:
    sig = part.sig
    params = [
        {
            "name": p.name,
            "var": f"a{i}",
            "cpp_value": cpp_value(p.tnode),
            "cpp_param": cpp_param(p.tnode),
        }
        for i, p in enumerate(sig.params)
    ]
    types = [p.type for p in sig.params] + [sig.returns]
    out_var = ""
    if sig.ret.is_void:
        idx = [p.name for p in sig.params].index(sig.output_param or "")
        out_var = f"a{idx}"
    return {
        "slug": slug,
        "part_id": part.id,
        "title": part.title,
        "method": sig.method,
        "params": params,
        "params_decl": ", ".join(f"{p['cpp_param']} {p['name']}" for p in params),
        "call_args": ", ".join(p["var"] for p in params),
        "ret_cpp": cpp_value(sig.ret),
        "is_void": sig.ret.is_void,
        "output_var": out_var,
        "uses_listnode": uses(types, "ListNode"),
        "uses_treenode": uses(types, "TreeNode"),
    }


def _pch_dir(cfg: JudgeConfig) -> Path | None:
    """Precompiled <bits/stdc++.h> for the release flags; built once, best effort."""
    if not cfg.use_pch:
        return None
    key = digest(tool_version(cfg.cxx), *cfg.cxx_flags)[:24]
    root = cache_dir() / "pch" / key
    gch = root / "bits" / "stdc++.h.gch"
    if gch.exists():
        return root
    with file_lock(cache_dir() / "pch" / f"{key}.lock"):
        if gch.exists():
            return root
        tmp = root.with_name(key + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / "bits").mkdir(parents=True)
        (tmp / "pch.h").write_text("#include <bits/stdc++.h>\n", encoding="utf-8")
        try:
            out = subprocess.run(
                [cfg.cxx, *cfg.cxx_flags, "-x", "c++-header", "pch.h", "-o", "bits/stdc++.h.gch"],
                cwd=tmp,
                capture_output=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        if out.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        shutil.rmtree(root, ignore_errors=True)
        tmp.rename(root)
    return root


class Cpp:
    name: Lang = "cpp"
    ext = "cpp"
    sol_filename = "sol.cpp"

    def stub(self, slug: str, part: Part) -> str:
        return render("cpp_stub.cpp.j2", **_ctx(slug, part))

    def driver_files(self, slug: str, part: Part) -> dict[str, str]:
        return {
            "main.cpp": render("cpp_driver.cpp.j2", **_ctx(slug, part)),
            "dsa_json.hpp": static("dsa_json.hpp"),
        }

    def build(
        self, src: Path, slug: str, part: Part, cfg: JudgeConfig, *, debug: bool = False
    ) -> Built:
        files = self.driver_files(slug, part)
        files["sol.cpp"] = src.read_text(encoding="utf-8")
        flags = DEBUG_FLAGS if debug else list(cfg.cxx_flags)
        pch = None if debug else _pch_dir(cfg)

        def compile_fn(d: Path) -> None:
            include = ["-I", str(pch)] if pch else []
            # Absolute paths so diagnostics name the files; they are mapped back to the
            # user's sol.cpp when shown.
            run_tool([cfg.cxx, *flags, *include, "-o", "main", str(d / "main.cpp")], cwd=d)

        # The PCH only speeds things up; it is not part of the cache key.
        out = build_cached("cpp", files, [tool_version(cfg.cxx), *flags], compile_fn)
        env = {"ASAN_OPTIONS": "detect_leaks=0:abort_on_error=0"} if debug else {}
        return Built(
            lang="cpp",
            argv=[str(out / "main"), "{fd}"],
            cwd=out,
            mem_overhead_mb=16,
            startup_s=1.0,
            debug=debug,
            env=env,
        )
