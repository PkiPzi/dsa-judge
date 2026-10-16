"""Ollama on loopback: reachability check and the OllamaLLM provider."""

from __future__ import annotations

import base64
import copy
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dsajudge.ai.base import AIError
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


class OllamaLLM:
    """Chat completions from a local Ollama. Refuses any non-loopback host at construction.

    ``keep_alive: 0`` unloads the model after every call: a 4 GB GPU holds one model at a time,
    and intake alternates between vision and text models.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = 900.0,
        num_ctx: int = 8192,
        client: Any = None,
    ) -> None:
        self.base_url = ensure_loopback(base_url).rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.options: dict[str, Any] = {}
        self._client = client

    def with_options(self, **options: Any) -> OllamaLLM:
        """A copy that sends these sampling options too (e.g. temperature, repeat_penalty)."""
        clone = copy.copy(self)
        clone.options = {**self.options, **options}
        return clone

    def _http(self) -> Any:
        if self._client is None:
            try:
                import httpx
            except ImportError as e:  # pragma: no cover - the [ai] extra provides httpx
                raise AIError("httpx is missing: run `uv sync --all-extras`") from e
            # trust_env=False: never route problem content through a configured proxy.
            self._client = httpx.Client(timeout=self.timeout, trust_env=False)
        return self._client

    def request_body(
        self,
        prompt: str,
        system: str | None,
        schema: dict[str, object] | None,
        images: list[Path] | None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        user: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user["images"] = [base64.b64encode(p.read_bytes()).decode("ascii") for p in images]
        messages.append(user)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": 0,
            "options": {"temperature": 0, "num_ctx": self.num_ctx, **self.options},
        }
        if schema is not None:
            body["format"] = schema
        return body

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, object] | None = None,
        images: list[Path] | None = None,
    ) -> str:
        body = self.request_body(prompt, system, schema, images)
        http = self._http()
        try:
            resp = http.post(self.base_url + "/api/chat", json=body)
        except Exception as e:
            raise AIError(
                f"Ollama is not reachable at {self.base_url} ({type(e).__name__}); "
                "start it with `systemctl start ollama`"
            ) from e
        if resp.status_code == 404:
            raise AIError(f"model {self.model!r} is not installed: run `ollama pull {self.model}`")
        if resp.status_code >= 400:
            raise AIError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            content = resp.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise AIError(f"unexpected Ollama response: {resp.text[:300]}") from e
        return str(content)

    def installed_models(self) -> list[str]:
        resp = self._http().get(self.base_url + "/api/tags")
        return [str(m.get("name")) for m in resp.json().get("models", [])]
