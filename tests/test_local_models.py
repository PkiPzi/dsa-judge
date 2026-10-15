"""Real-model tests. Skipped by default; run on your machine with `uv run pytest -m local`.

They need Ollama on 127.0.0.1 with the configured models pulled, and (for the voice test)
whisper.cpp with a model. See LOCAL_TESTING.md. Nothing here is downloaded automatically.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dsajudge.ai.bench import STATEMENT_LINES, VISION_EXPECT, run_bench
from dsajudge.ai.factory import LocalProviders
from dsajudge.ai.ollama import OllamaLLM, ping
from dsajudge.ai.synth import render_text_image
from dsajudge.ai.whisper import find_binary, find_model
from dsajudge.config import Config, load_config

pytestmark = pytest.mark.local


@pytest.fixture
def cfg(request: pytest.FixtureRequest) -> Config:
    # Read the user's real config.toml (model names, whisper_model_dir) before `home`
    # points DSA_HOME at a temp bank; otherwise these tests always see the defaults.
    real = load_config()
    request.getfixturevalue("home")
    return real


def require_ollama(cfg: Config, *models: str) -> None:
    if ping(cfg.ai.ollama_url) is None:
        pytest.skip(f"Ollama is not running at {cfg.ai.ollama_url}")
    installed = OllamaLLM(cfg.ai.ollama_url, "x").installed_models()
    missing = [m for m in models if m not in installed]
    if missing:
        pytest.skip(f"models not pulled: {', '.join(missing)} (ollama pull ...)")


def test_vision_model_reads_a_rendered_statement(cfg: Config, tmp_path: Path) -> None:
    require_ollama(cfg, cfg.ai.models.vision)
    img = render_text_image(STATEMENT_LINES, tmp_path / "statement.png")
    from dsajudge.ai.prompts import load_prompt

    system, user = load_prompt("transcribe_image").render()
    text = LocalProviders().vision(cfg).complete(user, system=system, images=[img])
    missing = [t for t in VISION_EXPECT if t not in text]
    assert not missing, f"the transcript lacks {missing}:\n{text}"


def test_text_model_structures_a_statement(cfg: Config) -> None:
    require_ollama(cfg, cfg.ai.models.text)
    rows = [r for r in run_bench(cfg, LocalProviders(), runs=3) if r.role.startswith("text:")]
    for r in rows:
        assert r.ok >= 2, f"{r.role}: only {r.ok}/{r.runs} usable ({r.note})"


def test_whisper_cpp_runs(cfg: Config, tmp_path: Path) -> None:
    if not find_binary(cfg.ai.whisper_bin):
        pytest.skip("whisper-cli not installed")
    if find_model(cfg.ai.models.whisper, cfg.ai.whisper_model_dir) is None:
        pytest.skip(f"no ggml-{cfg.ai.models.whisper}.bin model found")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    wav = tmp_path / "silence.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "2",
            str(wav),
        ],
        check=True,
    )
    assert isinstance(LocalProviders().transcriber(cfg).transcribe(wav), str)


def test_the_whole_intake_with_real_models(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rendered statement photo + a Python solution through `dsa add`, reviewed by a no-op
    editor. Small models can fail to structure it; then the error must explain what to do."""
    require_ollama(cfg, cfg.ai.models.text, cfg.ai.models.vision)
    from typer.testing import CliRunner

    from dsajudge.cli import app

    photo = render_text_image(
        [
            "Maximum product of two numbers",
            "Given an integer array nums, return the largest product of two elements",
            "at different positions.",
            "Constraints:  2 <= nums.length <= 10^{5}   and   -10^{4} <= nums[i] <= 10^{4}",
            "Example 1:  Input: nums = [3, 5, -2, 4]   Output: 20",
            "Example 2:  Input: nums = [2, 3]   Output: 6",
        ],
        tmp_path / "statement.png",
    )
    code = tmp_path / "solution.py"
    code.write_text(
        "class Solution:\n    def maxProductOfTwo(self, nums):\n"
        "        nums = sorted(nums)\n        if len(nums) == 2:\n"
        "            return nums[0] * nums[1]\n"
        "        return max(nums[0] * nums[1], nums[-1] * nums[-2])\n"
    )
    monkeypatch.setenv("EDITOR", "true")  # accept the draft as-is
    result = CliRunner().invoke(app, ["add", str(photo), str(code), "--slug", "local-check"])
    assert result.exit_code in (0, 1), result.output
    if result.exit_code == 1:
        # a draft the reviewer would have to fix by hand: the message must say so
        assert "editor" in result.output or "error" in result.output
