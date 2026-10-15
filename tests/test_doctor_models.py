"""`dsa doctor --models` with fakes and with nothing running."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from dsajudge.ai import factory
from dsajudge.ai.fake import FakeLLM, FakeTranscriber
from dsajudge.cli import app

runner = CliRunner()

SEGMENT = {"title": "t", "parts": [{"part": 1, "text": "x"}], "code": []}
EXTRACT = {
    "title": "Sum of two numbers",
    "statement": "s",
    "method": "twoSum",
    "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
    "returns": "int[]",
    "constraints_text": ["2 <= nums.length <= 10^5"],
    "samples": [{"args": [[2, 7, 11, 15], 9], "expected": [0, 1]}],
}
CONSTRAINTS = {
    "constraints": {
        "nums": {"len": [2, "10^5"], "each": ["-10^9", "10^9"]},
        "target": {"range": ["-10^9", "10^9"]},
    }
}


def test_models_report_usable_rates_and_latency(
    monkeypatch: pytest.MonkeyPatch, home: object
) -> None:
    llm = FakeLLM(
        [
            {
                "task": "transcribe_image",
                "response": "Constraints 2 <= nums.length <= 10^5; nums, target; Output: [0, 1]",
            },
            {"task": "segment", "response": SEGMENT},
            {"task": "extract_part", "responses": [EXTRACT, "not json", EXTRACT]},
            {"task": "fill_constraints", "response": CONSTRAINTS},
        ]
    )
    monkeypatch.setattr(
        factory, "PROVIDERS", factory.FixedProviders(llm, FakeTranscriber({"*": "ok"}))
    )
    result = runner.invoke(app, ["doctor", "--models"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "models" in out and "vision:ocr" in out and "text:segment" in out
    assert "3/3" in out  # segment, constraints and OCR all usable every time
    assert "2/3" in out  # one extraction in three was not valid JSON
    assert "whisper:runs" in out
    # every text model call carried a JSON schema (constrained decoding)
    assert all(c.schema is not None for c in llm.calls if c.task in ("segment", "extract_part"))
    # the vision call got a real rendered image
    img = next(c.images[0] for c in llm.calls if c.task == "transcribe_image")
    assert img.suffix == ".png"


def test_models_flags_a_vision_model_that_misreads_the_exponent(
    monkeypatch: pytest.MonkeyPatch, home: object
) -> None:
    llm = FakeLLM(
        [
            {"task": "transcribe_image", "response": "nums target [0, 1] and 105"},  # 10^5 -> 105
            {"task": "segment", "response": SEGMENT},
            {"task": "extract_part", "response": EXTRACT},
            {"task": "fill_constraints", "response": CONSTRAINTS},
        ]
    )
    monkeypatch.setattr(
        factory, "PROVIDERS", factory.FixedProviders(llm, FakeTranscriber({"*": "ok"}))
    )
    out = runner.invoke(app, ["doctor", "--models"]).output
    assert "0/3" in out and "missing in the transcript: 10^5" in out


def test_models_with_nothing_running_does_not_crash(home: object) -> None:
    # the real providers pointed at a closed loopback port
    (home / "config.toml").parent.mkdir(parents=True, exist_ok=True)  # type: ignore[operator]
    (home / "config.toml").write_text('[ai]\nollama_url = "http://127.0.0.1:1"\n')  # type: ignore[operator]
    result = runner.invoke(app, ["doctor", "--models"])
    assert result.exit_code == 0, result.output
    assert "not reachable" in result.output or "Ollama" in result.output
    assert json.dumps  # keep import used
