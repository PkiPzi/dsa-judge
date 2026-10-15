from __future__ import annotations

from pathlib import Path

import pytest

from dsajudge.ai.net import NonLoopbackError, ensure_loopback
from dsajudge.ai.ollama import ping
from dsajudge.config import ConfigError, cache_dir, dsa_home, load_config
from dsajudge.doctor import run_checks
from dsajudge.judge import limits


def test_dsa_home_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DSA_HOME", raising=False)
    assert dsa_home() == (Path.home() / "dsa-bank").resolve()


def test_dsa_home_env(home: Path) -> None:
    assert dsa_home() == home.resolve()


def test_cache_dir_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_dir() == (tmp_path / "dsajudge").resolve()


def test_config_defaults_and_file(home: Path) -> None:
    cfg = load_config()
    assert cfg.default_lang == "cpp"
    assert cfg.judge.time_multipliers["python"] == 5.0
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'default_lang = "java"\n[judge]\nmemory_backend = "poll"\n[judge.time_multipliers]\n'
        "java = 3.0\n"
    )
    cfg = load_config()
    assert cfg.default_lang == "java"
    assert cfg.judge.memory_backend == "poll"
    assert cfg.judge.time_multipliers == {"java": 3.0}


def test_config_errors(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "config.toml").write_text("nonsense = 1\n")
    with pytest.raises(ConfigError, match="nonsense"):
        load_config()


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434", "http://127.0.0.5"],
)
def test_loopback_allowed(url: str) -> None:
    assert ensure_loopback(url) == url


@pytest.mark.parametrize(
    "url",
    ["http://10.0.0.2:11434", "https://api.example.com", "http://0.0.0.0:11434", "ftp://127.0.0.1"],
)
def test_non_loopback_rejected(url: str) -> None:
    with pytest.raises(NonLoopbackError):
        ensure_loopback(url)


def test_ping_refuses_remote_before_connecting() -> None:
    with pytest.raises(NonLoopbackError):
        ping("http://192.168.1.10:11434")


def test_ping_unreachable_returns_none() -> None:
    # Port 9 (discard) on loopback is closed in test environments.
    assert ping("http://127.0.0.1:9", timeout=0.5) is None


def test_doctor_missing_tools_are_warnings(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dsajudge.doctor.shutil.which", lambda name: None)
    monkeypatch.setattr(limits, "probe_systemd", lambda: (False, "systemd-run not found"))
    monkeypatch.setattr("dsajudge.doctor.ping", lambda url: None)
    checks = {c.name: c for c in run_checks(load_config())}
    for name in ("g++", "javac", "java", "ffmpeg", "whisper.cpp", "ollama"):
        assert checks[name].status == "warn", name
    assert checks["memory limits"].detail.startswith("poll")


def test_doctor_real_env(home: Path) -> None:
    checks = {c.name: c for c in run_checks(load_config())}
    # The cloud env and CI have these toolchains.
    for name in ("g++", "javac", "java", "python"):
        assert checks[name].status == "ok", checks[name]


def test_memory_backend_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    limits._cache.clear()
    monkeypatch.setattr(limits, "probe_systemd", lambda: (True, "systemd-run ok"))
    b = limits.detect_backend("auto")
    assert b.kind == "systemd"
    wrapped = limits.wrap_command(["./main", "3"], b, 300)
    assert wrapped[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert "MemoryMax=300M" in wrapped and "MemorySwapMax=0" in wrapped
    assert wrapped[-2:] == ["./main", "3"]
    assert limits.detect_backend("poll").kind == "poll"
    limits._cache.clear()
    monkeypatch.setattr(limits, "probe_systemd", lambda: (False, "no bus"))
    assert limits.detect_backend("systemd").kind == "poll"
    assert limits.wrap_command(["x"], limits.detect_backend("auto"), 300) == ["x"]
    limits._cache.clear()
