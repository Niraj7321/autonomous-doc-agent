"""Application configuration, loaded from environment variables / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # optional dependency; app still works without a .env file
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is best-effort
    pass


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Runtime settings resolved once at import time."""

    # Providers
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip())
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))

    gemini_api_key: str = field(
        default_factory=lambda: (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    )
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))

    use_ollama: bool = field(default_factory=lambda: _flag("USE_OLLAMA", "0"))
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1"))

    # Runtime
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "generated"))
    request_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "45")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))

    # Guardrails
    min_request_chars: int = 3
    max_request_chars: int = 4000


settings = Settings()
