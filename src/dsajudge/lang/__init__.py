"""Language backends: stub and driver codegen, compile, run commands."""

from __future__ import annotations

from dsajudge.lang.base import Language
from dsajudge.lang.cpp import Cpp
from dsajudge.lang.java import Java
from dsajudge.lang.python import Python
from dsajudge.model.schema import Lang

_LANGS: dict[str, Language] = {"cpp": Cpp(), "java": Java(), "python": Python()}

EXT_TO_LANG: dict[str, Lang] = {".cpp": "cpp", ".cc": "cpp", ".java": "java", ".py": "python"}


def get_language(name: str) -> Language:
    try:
        return _LANGS[name]
    except KeyError:
        raise ValueError(f"unknown language {name!r}; use cpp, java or python") from None
