"""Providers, fakes, prompts and the verify-and-retry helper (no models, no network)."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from dsajudge.ai.base import AIError
from dsajudge.ai.fake import FakeLLM, FakeTranscriber, NoRecordedResponse
from dsajudge.ai.net import NonLoopbackError
from dsajudge.ai.ollama import OllamaLLM
from dsajudge.ai.prompts import PROMPTS, load_prompt, task_of
from dsajudge.ai.retry import ask_structured, extract_code, extract_json
from dsajudge.ai.schemas import Extraction, Segmentation, json_schema
from dsajudge.ai.whisper import WhisperCppTranscriber, find_model

# ---------------------------------------------------------------- Ollama


def mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_refuses_non_loopback_hosts() -> None:
    for url in ("http://10.0.0.5:11434", "https://api.example.com", "http://0.0.0.0:11434"):
        with pytest.raises(NonLoopbackError):
            OllamaLLM(url, "m")
    OllamaLLM("http://127.0.0.1:11434", "m")
    OllamaLLM("http://localhost:11434", "m")


def test_ollama_request_unloads_model_and_constrains_output(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "hi"}})

    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG-bytes")
    llm = OllamaLLM("http://127.0.0.1:11434", "qwen2.5vl:3b", client=mock_client(handler))
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    out = llm.complete("p", system="s", schema=schema, images=[img])
    body = seen["body"]
    assert out == "hi" and seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert body["keep_alive"] == 0  # one model at a time on a 4 GB GPU
    assert body["stream"] is False and body["format"] == schema
    assert body["model"] == "qwen2.5vl:3b" and body["options"]["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": "s"}
    assert body["messages"][1]["images"] == [base64.b64encode(b"\x89PNG-bytes").decode()]


def test_ollama_error_messages() -> None:
    def make(status: int) -> OllamaLLM:
        return OllamaLLM(
            "http://127.0.0.1:11434",
            "qwen3:4b",
            client=mock_client(lambda r: httpx.Response(status, text="boom")),
        )

    with pytest.raises(AIError, match="ollama pull qwen3:4b"):
        make(404).complete("p")
    with pytest.raises(AIError, match="HTTP 500"):
        make(500).complete("p")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(AIError, match="not reachable"):
        OllamaLLM("http://127.0.0.1:1", "m", client=mock_client(refuse)).complete("p")


def test_ollama_talks_to_loopback_even_with_a_proxy_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Problem content must not be routed through HTTP(S)_PROXY."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            hits.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"message": {"content": "pong"}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, "http://127.0.0.1:9")  # nothing listens there
        llm = OllamaLLM(f"http://127.0.0.1:{server.server_port}", "m")
        assert llm.complete("ping") == "pong"
        assert hits == ["/api/chat"]
    finally:
        server.shutdown()


# ---------------------------------------------------------------- whisper.cpp


def test_whisper_reports_missing_pieces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(AIError, match="whisper-cli not found"):
        WhisperCppTranscriber().transcribe(tmp_path / "a.wav")


def test_whisper_model_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert find_model("small", str(tmp_path / "none")) is None
    model = tmp_path / "models" / "ggml-small.bin"
    model.parent.mkdir()
    model.write_bytes(b"x")
    assert find_model("small", str(model.parent)) == model
    xdg = tmp_path / "data" / "whisper.cpp" / "ggml-medium.bin"
    xdg.parent.mkdir(parents=True)
    xdg.write_bytes(b"x")
    assert find_model("medium") == xdg


def test_whisper_runs_the_cli_via_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stand-in whisper-cli checks the arguments and writes the transcript file."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"x")
    cli = tmp_path / "bin" / "whisper-cli"
    cli.parent.mkdir()
    cli.write_text(
        "#!/bin/sh\n"
        'while [ $# -gt 0 ]; do case "$1" in -of) out="$2"; shift;; -f) wav="$2"; shift;; '
        '-l) lang="$2"; shift;; -bs) bs="$2"; shift;; -bo) bo="$2"; shift;; '
        '--prompt) prompt="$2"; shift;; esac; shift; done\n'
        '[ -s "$wav" ] || exit 3\n'
        'printf "  ab  chalo   array  ka sum\\n(lang=%s bs=%s bo=%s prompt=%s)\\n" '
        '"$lang" "$bs" "$bo" "$prompt" > "$out.txt"\n'
    )
    cli.chmod(0o755)
    audio = tmp_path / "note.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            "0.3",
            str(audio),
        ],
        check=True,
    )
    t = WhisperCppTranscriber(
        "small",
        binary=str(cli),
        model_dir=str(tmp_path),
        language="hi",
        beam_size=1,
        prompt="aaj main solve kar raha hoon",
    )
    assert t.transcribe(audio) == (
        "ab chalo array ka sum (lang=hi bs=1 bo=1 prompt=aaj main solve kar raha hoon)"
    )
    t.prompt = ""  # an empty prompt must not pass --prompt at all
    assert t.transcribe(audio).endswith("prompt=)")


# ---------------------------------------------------------------- prompts


def test_every_prompt_file_loads_and_tags_its_task() -> None:
    names = sorted(p.stem for p in PROMPTS.glob("*.md"))
    assert {
        "segment",
        "extract_part",
        "fill_constraints",
        "write_brute",
        "write_gen",
        "write_checker",
        "fix_ocr",
        "transcribe_image",
        "extract_code",
    } <= set(names)
    for name in names:
        p = load_prompt(name)
        assert p.system and p.user
        system, _ = p.render(**{v: "x" for v in _vars(p.system + p.user)})
        assert task_of(system) == name


def _vars(text: str) -> set[str]:
    import re

    return set(re.findall(r"\{\{(\w+)\}\}", text))


def test_prompt_render_requires_every_variable() -> None:
    with pytest.raises(KeyError, match="transcript"):
        load_prompt("segment").render()
    system, user = load_prompt("segment").render(transcript="T")
    assert user.endswith("T") and system.startswith("[task: segment]")


def test_prompts_name_the_privacy_relevant_rules() -> None:
    sys = load_prompt("transcribe_image").system
    assert "[??]" in sys and "10^5" in sys and "indentation" in sys
    assert "Hinglish" in load_prompt("segment").system


# ---------------------------------------------------------------- fakes


def test_fake_llm_matches_by_task_text_and_image(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            {"task": "segment", "response": {"a": 1}},
            {"task": "transcribe_image", "image": "two.png", "response": "TWO"},
            {"task": "transcribe_image", "response": "ANY"},
            {"task": "extract_part", "contains": "part 2", "response": "P2"},
        ]
    )
    system = load_prompt("segment").render(transcript="t")[0]
    assert json.loads(llm.complete("u", system=system)) == {"a": 1}
    img_system = load_prompt("transcribe_image").system
    tag = f"[task: transcribe_image]\n{img_system}"
    assert llm.complete("u", system=tag, images=[tmp_path / "01-two.png"]) == "TWO"
    assert llm.complete("u", system=tag, images=[tmp_path / "01-one.png"]) == "ANY"
    ext = f"[task: extract_part]\n{img_system}"
    assert llm.complete("this is part 2", system=ext) == "P2"
    with pytest.raises(NoRecordedResponse):
        llm.complete("this is part 1", system=ext)
    assert llm.tasks()[:2] == ["segment", "transcribe_image"]


def test_fake_llm_response_sequences() -> None:
    llm = FakeLLM([{"task": "t", "responses": ["a", "b"], "repeat_last": False}])
    sys = "[task: t]\nx"
    assert [llm.complete("p", system=sys) for _ in range(2)] == ["a", "b"]
    with pytest.raises(NoRecordedResponse, match="used up"):
        llm.complete("p", system=sys)
    llm2 = FakeLLM([{"task": "t", "responses": ["a", "b"]}])
    assert [llm2.complete("p", system=sys) for _ in range(3)] == ["a", "b", "b"]
    llm2.add(task="t", response="override")
    assert llm2.complete("p", system=sys) == "override"


def test_fake_files_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "rec.json"
    f.write_text(
        json.dumps({"llm": [{"task": "x", "response": "r"}], "transcriber": {"voice.m4a": "hello"}})
    )
    assert FakeLLM.from_file(f).complete("p", system="[task: x]\n") == "r"
    t = FakeTranscriber.from_file(f)
    assert t.transcribe(tmp_path / "02-voice.m4a") == "hello"
    with pytest.raises(NoRecordedResponse):
        t.transcribe(tmp_path / "other.m4a")
    assert FakeTranscriber({"*": "any"}).transcribe(tmp_path / "z.wav") == "any"


# ---------------------------------------------------------------- retry helper


def test_extract_json_and_code() -> None:
    assert json.loads(extract_json('Sure!\n```json\n{"a": [1, 2]}\n```\nbye')) == {"a": [1, 2]}
    assert json.loads(extract_json('noise {"a": 1} trailing')) == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no braces")
    assert (
        extract_code("x\n```python\ndef f():\n    return 1\n```\ny") == "def f():\n    return 1\n"
    )
    assert extract_code("```cpp\nint a;\n```\n```python\nb=1\n```", "python") == "b=1\n"
    assert extract_code("plain") == "plain\n"


def test_ask_structured_retries_with_feedback() -> None:
    llm = FakeLLM(
        [
            {
                "task": "extract_code",
                "responses": [
                    "not json at all",
                    {"lang": "python", "code": "x = 1"},
                    {"lang": "python", "code": "def f():\n    return 1\n"},
                ],
            }
        ]
    )
    from dsajudge.ai.schemas import CodeOut

    def check(o: CodeOut) -> str | None:
        return None if "def " in o.code else "no function defined"

    out = ask_structured(llm, "extract_code", CodeOut, {"text": "t"}, attempts=3, check=check)
    assert "def f" in out.code and len(llm.calls) == 3
    assert "rejected: the reply was not valid" in llm.calls[1].prompt
    assert "rejected: no function defined" in llm.calls[2].prompt


def test_ask_structured_gives_up_after_the_cap() -> None:
    llm = FakeLLM([{"task": "extract_code", "response": "garbage"}])
    from dsajudge.ai.schemas import CodeOut

    with pytest.raises(AIError, match="after 2 attempt"):
        ask_structured(llm, "extract_code", CodeOut, {"text": "t"}, attempts=2)
    assert len(llm.calls) == 2


def test_schemas_are_self_contained() -> None:
    for model in (Segmentation, Extraction):
        schema = json_schema(model)
        text = json.dumps(schema)
        assert "$ref" not in text and "$defs" not in text
        assert '"title": "' not in text  # annotations are gone...
    # ...but a property that happens to be called "title" is kept
    assert "title" in json_schema(Extraction)["properties"]
    assert "title" in json_schema(Extraction)["required"]
    assert json_schema(Segmentation)["properties"]["parts"]["minItems"] == 1
