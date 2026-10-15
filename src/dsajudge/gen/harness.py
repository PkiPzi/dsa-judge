"""Subprocess harness for a part's custom ``gen.py`` / ``validate.py`` (run with ``python -I``).

Usage: ``python -I harness.py gen|validate FILE`` with a JSON request on stdin; prints one JSON
line on stdout. Kept free of dsajudge imports so it runs under an isolated interpreter.

* gen:      {"requests": [{"size": "small", "flavor": "random", "seed": 1}, ...]}
            -> {"results": [[arg, ...], ...]}      (gen.py: generate(rng, size, flavor) -> list)
* check:    a line-oriented server: each stdin line is {"args": [...], "expected": v, "got": v};
            each reply line is {"ok": bool, "msg": str} or {"error": "traceback"}
            (checker.py: check(args, expected, got) -> (bool, str))
* validate: {"inputs": [[arg, ...], ...]}
            -> {"results": [null | "message", ...]} (validate.py: validate(args) -> None | str)
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
import traceback


def _load(path: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("part_script", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def serve_checks(path: str) -> int:
    mod = _load(path)
    fn = getattr(mod, "check", None)
    if not callable(fn):
        sys.stdout.write(json.dumps({"error": "checker.py must define check(args, expected, got)"}))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            res = fn(req["args"], req["expected"], req["got"])
            if isinstance(res, tuple | list):
                ok, msg = bool(res[0]), str(res[1]) if len(res) > 1 else ""
            else:
                ok, msg = bool(res), ""
            reply: dict[str, object] = {"ok": ok, "msg": msg}
        except Exception:
            reply = {"error": traceback.format_exc(limit=6)}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    kind, path = sys.argv[1], sys.argv[2]
    if kind == "check":
        return serve_checks(path)
    try:
        request = json.loads(sys.stdin.read())
        mod = _load(path)
        results: list[object] = []
        if kind == "gen":
            fn = getattr(mod, "generate", None)
            if not callable(fn):
                raise AttributeError("gen.py must define generate(rng, size, flavor)")
            for i, req in enumerate(request["requests"]):
                try:
                    out = fn(random.Random(req["seed"]), req["size"], req["flavor"])
                except Exception:
                    json.dump({"error": traceback.format_exc(limit=8), "index": i}, sys.stdout)
                    return 0
                if not isinstance(out, list | tuple):
                    json.dump(
                        {
                            "error": "generate() must return the argument list, got "
                            + type(out).__name__,
                            "index": i,
                        },
                        sys.stdout,
                    )
                    return 0
                results.append(list(out))
        else:
            fn = getattr(mod, "validate", None)
            if not callable(fn):
                raise AttributeError("validate.py must define validate(args)")
            for i, args in enumerate(request["inputs"]):
                try:
                    res = fn(args)
                except Exception:
                    json.dump({"error": traceback.format_exc(limit=8), "index": i}, sys.stdout)
                    return 0
                if res is None or res is True or res == "":
                    results.append(None)
                elif res is False:
                    results.append("validate() returned False")
                else:
                    results.append(str(res))
        json.dump({"results": results}, sys.stdout, allow_nan=False)
    except Exception:
        json.dump({"error": traceback.format_exc(limit=8), "index": -1}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
