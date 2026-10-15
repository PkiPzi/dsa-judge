"""Loopback guard for AI endpoints."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


class NonLoopbackError(ValueError):
    pass


def is_loopback_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def ensure_loopback(url: str) -> str:
    """Return ``url`` unchanged if it points at a loopback host, else raise."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise NonLoopbackError(f"unsupported AI endpoint scheme in {url!r}")
    host = parts.hostname or ""
    if not is_loopback_host(host):
        raise NonLoopbackError(
            f"refusing non-loopback AI endpoint {url!r}: problem content never leaves this machine"
        )
    return url
