"""Ollama reachability check. The full OllamaLLM provider arrives in Phase 3."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from dsajudge.ai.net import ensure_loopback


def ping(base_url: str, timeout: float = 1.0) -> str | None:
    """Return the Ollama version if it answers on ``base_url``, else None.

    Raises NonLoopbackError for non-loopback URLs before any connection is attempted.
    """
    url = ensure_loopback(base_url).rstrip("/") + "/api/version"
    # Bypass any proxy settings: this must only ever talk to the local host directly.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    version = body.get("version") if isinstance(body, dict) else None
    return str(version) if version else "unknown"
