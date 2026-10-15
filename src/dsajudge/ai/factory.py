"""Build the configured providers. Commands call these (not the classes) so tests can swap in
fakes by replacing ``PROVIDERS``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dsajudge.ai.base import LLM, Transcriber
from dsajudge.ai.ollama import OllamaLLM
from dsajudge.ai.whisper import WhisperCppTranscriber
from dsajudge.config import Config


class ProviderFactory(Protocol):
    def text(self, cfg: Config) -> LLM: ...

    def vision(self, cfg: Config) -> LLM: ...

    def transcriber(self, cfg: Config) -> Transcriber: ...


@dataclass
class LocalProviders:
    """Ollama on loopback and whisper.cpp."""

    def _llm(self, cfg: Config, model: str) -> LLM:
        return OllamaLLM(cfg.ai.ollama_url, model, timeout=cfg.ai.timeout_s, num_ctx=cfg.ai.num_ctx)

    def text(self, cfg: Config) -> LLM:
        return self._llm(cfg, cfg.ai.models.text)

    def vision(self, cfg: Config) -> LLM:
        return self._llm(cfg, cfg.ai.models.vision)

    def transcriber(self, cfg: Config) -> Transcriber:
        return WhisperCppTranscriber(
            cfg.ai.models.whisper,
            binary=cfg.ai.whisper_bin,
            model_dir=cfg.ai.whisper_model_dir,
            language=cfg.ai.whisper_language,
        )


@dataclass
class FixedProviders:
    """One LLM for everything (a FakeLLM in tests) and one transcriber."""

    llm: LLM
    audio: Transcriber

    def text(self, cfg: Config) -> LLM:
        return self.llm

    def vision(self, cfg: Config) -> LLM:
        return self.llm

    def transcriber(self, cfg: Config) -> Transcriber:
        return self.audio


PROVIDERS: ProviderFactory = LocalProviders()
