"""Provider interface + concrete HTTP providers (Groq, Gemini, Ollama).

Every provider speaks the same tiny interface: `available()` and `complete()`.
We deliberately call the raw HTTP endpoints with `httpx` instead of each
vendor's SDK so the dependency surface stays small and version-stable.
"""
from __future__ import annotations

from typing import Protocol

import httpx

from ..config import Settings


class LLMProviderError(Exception):
    """Raised by a provider when a single completion attempt fails."""


class LLMProvider(Protocol):
    name: str

    def available(self) -> bool:  # pragma: no cover - trivial
        ...

    def complete(self, system: str, user: str, *, json_mode: bool, temperature: float) -> str:
        ...


class GroqProvider:
    """Groq's OpenAI-compatible chat completions endpoint."""

    name = "groq"
    _URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, settings: Settings):
        self.key = settings.groq_api_key
        self.model = settings.groq_model
        self.timeout = settings.request_timeout

    def available(self) -> bool:
        return bool(self.key)

    def complete(self, system: str, user: str, *, json_mode: bool, temperature: float) -> str:
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = httpx.post(
                self._URL,
                headers={"Authorization": f"Bearer {self.key}"},
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - normalise to our error type
            raise LLMProviderError(f"groq: {exc}") from exc


class GeminiProvider:
    """Google Gemini `generateContent` REST endpoint (free tier friendly)."""

    name = "gemini"
    _BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, settings: Settings):
        self.key = settings.gemini_api_key
        self.model = settings.gemini_model
        self.timeout = settings.request_timeout

    def available(self) -> bool:
        return bool(self.key)

    def complete(self, system: str, user: str, *, json_mode: bool, temperature: float) -> str:
        url = f"{self._BASE}/{self.model}:generateContent?key={self.key}"
        gen_config = {"temperature": temperature}
        if json_mode:
            gen_config["responseMimeType"] = "application/json"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}],
            "generationConfig": gen_config,
        }
        try:
            r = httpx.post(url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"gemini: {exc}") from exc


class OllamaProvider:
    """Local Ollama server (`ollama serve`)."""

    name = "ollama"

    def __init__(self, settings: Settings):
        self.enabled = settings.use_ollama
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = settings.request_timeout

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:  # one cheap reachability probe at startup
            httpx.get(f"{self.host}/api/tags", timeout=1.5).raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            return False

    def complete(self, system: str, user: str, *, json_mode: bool, temperature: float) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["format"] = "json"
        try:
            r = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"ollama: {exc}") from exc
