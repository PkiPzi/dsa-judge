"""Content-addressed compile cache under $XDG_CACHE_HOME/dsajudge/build/."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path

from dsajudge.config import cache_dir
from dsajudge.errors import BUILD_PLACEHOLDER, CompileError

OK_MARKER = "ok"
CE_FILE = "ce.txt"


def digest(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        b = p.encode("utf-8") if isinstance(p, str) else p
        h.update(len(b).to_bytes(8, "little"))
        h.update(b)
    return h.hexdigest()


@cache
def tool_version(exe: str) -> str:
    """First line of ``exe --version`` (or ``-version`` for java tools), for cache keys."""
    for flag in ("--version", "-version"):
        try:
            out = subprocess.run(
                [exe, flag], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (out.stdout or out.stderr).strip()
        lines = [ln for ln in text.splitlines() if not ln.startswith("Picked up")]
        if out.returncode == 0 and lines:
            return lines[0]
    return exe


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def build_cached(
    lang: str,
    files: dict[str, str],
    key_extra: list[str],
    compile_fn: Callable[[Path], None],
) -> Path:
    """Return a build dir holding ``files`` after ``compile_fn(dir)`` succeeded.

    Results (including compile errors) are cached by a hash of the files and ``key_extra``.
    ``compile_fn`` raises CompileError with raw tool output; paths to the temporary build dir
    are rewritten to <build>/ before caching.
    """
    key = digest(lang, *key_extra, *(x for k in sorted(files) for x in (k, files[k])))
    root = cache_dir() / "build" / lang
    final = root / key[:40]
    if (final / OK_MARKER).exists():
        return final
    if (final / CE_FILE).exists():
        raise CompileError((final / CE_FILE).read_text(encoding="utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
    try:
        for name, content in files.items():
            (tmp / name).write_text(content, encoding="utf-8")
        error: CompileError | None = None
        try:
            compile_fn(tmp)
        except CompileError as e:
            error = CompileError(e.output.replace(str(tmp), BUILD_PLACEHOLDER))
            (tmp / CE_FILE).write_text(error.output, encoding="utf-8")
        else:
            (tmp / OK_MARKER).write_text("", encoding="utf-8")
        try:
            tmp.rename(final)
        except OSError:
            # Another process finished the same build first; use theirs.
            shutil.rmtree(tmp, ignore_errors=True)
        if error is not None:
            raise error
        return final
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def run_tool(argv: list[str], cwd: Path, timeout: float = 120.0) -> None:
    """Run a compiler; raise CompileError with its output on failure."""
    try:
        out = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as e:
        raise CompileError(f"{argv[0]} not found; run `dsa doctor`") from e
    except subprocess.TimeoutExpired as e:
        raise CompileError(f"compilation timed out after {timeout:.0f}s") from e
    if out.returncode != 0:
        text = "\n".join(
            ln
            for ln in (out.stderr + out.stdout).splitlines()
            if not ln.startswith("Picked up JAVA_TOOL_OPTIONS")
        )
        raise CompileError(text.strip() or f"{argv[0]} exited with {out.returncode}")
