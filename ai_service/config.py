"""Environment-only configuration for the independent AI service."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_SILICONFLOW_MODEL = "Qwen/Qwen3-32B"


def _chat_completions_url(value: str) -> str:
    """Accept an OpenAI base URL or a complete chat-completions endpoint."""
    parsed = urlparse(value.strip())
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return parsed._replace(path=path).geturl()


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class AIServiceConfig:
    session_db_path: Path
    session_ttl_seconds: int = 86_400
    business_api_url: str = "http://127.0.0.1:8000/api/analyze"
    business_timeout_seconds: float = 90.0
    llm_api_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 110.0
    llm_max_context_chars: int = 24_000
    history_max_messages: int = 24
    host: str = "127.0.0.1"
    port: int = 8010

    @classmethod
    def from_env(cls) -> "AIServiceConfig":
        default_db = Path(tempfile.gettempdir()) / "weather_multi_agent_ai" / "sessions.db"
        # The independent service keeps its own AI_LLM_* contract.  For local
        # deployments it may safely reuse the already-established legacy
        # credential without copying it into a file or the frontend.
        llm_key = (
            (os.getenv("AI_LLM_API_KEY") or "").strip()
            or (os.getenv("COORDINATOR_API_KEY") or "").strip()
            or None
        )
        configured_url = (os.getenv("AI_LLM_API_URL") or "").strip()
        if not configured_url and llm_key:
            configured_url = (
                (os.getenv("COORDINATOR_BASE_URL") or "").strip()
                or DEFAULT_SILICONFLOW_BASE_URL
            )
        llm_url = _chat_completions_url(configured_url) if configured_url else None
        configured_model = (
            (os.getenv("AI_LLM_MODEL") or "").strip()
            or (os.getenv("COORDINATOR_MODEL") or "").strip()
        )
        llm_model = configured_model or (DEFAULT_SILICONFLOW_MODEL if llm_url else None)
        config = cls(
            session_db_path=Path(os.getenv("AI_SESSION_DB_PATH", str(default_db))).expanduser(),
            session_ttl_seconds=_positive_int("AI_SESSION_TTL_SECONDS", 86_400),
            business_api_url=os.getenv(
                "AI_BUSINESS_API_URL", "http://127.0.0.1:8000/api/analyze"
            ).strip(),
            business_timeout_seconds=_positive_float("AI_BUSINESS_TIMEOUT_SECONDS", 90.0),
            llm_api_url=llm_url,
            llm_api_key=llm_key,
            llm_model=llm_model,
            llm_timeout_seconds=_positive_float("AI_LLM_TIMEOUT_SECONDS", 110.0),
            llm_max_context_chars=_positive_int("AI_LLM_MAX_CONTEXT_CHARS", 24_000),
            history_max_messages=_positive_int("AI_HISTORY_MAX_MESSAGES", 24),
            host=os.getenv("AI_SERVICE_HOST", "127.0.0.1").strip(),
            port=_positive_int("AI_SERVICE_PORT", 8010),
        )
        config.validate()
        return config

    def validate(self) -> None:
        parsed = urlparse(self.business_api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("AI_BUSINESS_API_URL must be a configured HTTP(S) URL")
        if self.llm_api_url:
            llm = urlparse(self.llm_api_url)
            if llm.scheme not in {"http", "https"} or not llm.netloc:
                raise ValueError("AI_LLM_API_URL must be an HTTP(S) URL")
            if not self.llm_model:
                raise ValueError("AI_LLM_MODEL is required when AI_LLM_API_URL is configured")
