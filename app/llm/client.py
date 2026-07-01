"""LLMClient: an ordered chain of providers with per-provider retries.

Design notes
------------
* The client owns *retry & fallback* logic. It walks the configured providers
  in priority order, retrying each with exponential backoff before moving on.
* `generate_json` is resilient by construction: if no provider yields valid
  JSON (or none are configured at all), it invokes a caller-supplied
  deterministic `fallback()` so the agent can still complete fully offline.
* Every call reports *which* provider actually produced the answer, so the
  orchestrator can surface real observability in its trace.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Tuple

from ..config import Settings, settings as default_settings
from .base import GeminiProvider, GroqProvider, LLMProvider, OllamaProvider

HEURISTIC = "heuristic-fallback"


class LLMError(Exception):
    """Raised when every configured provider fails and no fallback is given."""


def try_parse_json(raw: str) -> Any | None:
    """Best-effort extraction of a JSON value from a model response.

    Handles the three things models do wrong: wrap JSON in ```code fences```,
    add prose around it, or emit a trailing comma. Returns None on failure.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        # drop an optional leading language tag like "json\n"
        if "\n" in text:
            text = text.split("\n", 1)[1]
    # Fast path
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    # Slice to the outermost {...} or [...] span
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if 0 <= start < end:
            snippet = text[start : end + 1]
            try:
                return json.loads(snippet)
            except Exception:  # noqa: BLE001
                continue
    return None


class LLMClient:
    def __init__(self, settings: Settings = default_settings):
        self.settings = settings
        candidates: List[LLMProvider] = [
            GroqProvider(settings),
            GeminiProvider(settings),
            OllamaProvider(settings),
        ]
        # Probe availability once, at construction.
        self.providers: List[LLMProvider] = [p for p in candidates if p.available()]

    @property
    def has_llm(self) -> bool:
        return bool(self.providers)

    @property
    def provider_names(self) -> List[str]:
        return [p.name for p in self.providers]

    # ------------------------------------------------------------------ #
    def generate(
        self, system: str, user: str, *, json_mode: bool = False, temperature: float = 0.4
    ) -> Tuple[str, str]:
        """Return (text, provider_name). Raises LLMError if all providers fail."""
        errors: List[str] = []
        for provider in self.providers:
            for attempt in range(self.settings.max_retries + 1):
                try:
                    text = provider.complete(
                        system, user, json_mode=json_mode, temperature=temperature
                    )
                    if text and text.strip():
                        return text, provider.name
                    raise ValueError("empty response")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{provider.name}#{attempt + 1}: {exc}")
                    if attempt < self.settings.max_retries:
                        time.sleep(min(0.5 * (2 ** attempt), 4.0))  # backoff
        raise LLMError("; ".join(errors) or "no LLM providers configured")

    def generate_json(
        self,
        system: str,
        user: str,
        *,
        fallback: Callable[[], Dict[str, Any]],
        temperature: float = 0.3,
    ) -> Tuple[Dict[str, Any], str]:
        """Return (data, provider_name), guaranteeing a usable dict.

        Tries the provider chain; if nothing parses, calls `fallback()` and
        reports the provider as ``heuristic-fallback``.
        """
        if self.providers:
            try:
                raw, provider = self.generate(
                    system, user, json_mode=True, temperature=temperature
                )
                parsed = try_parse_json(raw)
                if isinstance(parsed, dict):
                    return parsed, provider
            except LLMError:
                pass  # fall through to deterministic path
        return fallback(), HEURISTIC
