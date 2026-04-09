from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _is_truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _resolve_runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _ensure_env_file(base_dir: Path) -> None:
    env_path = base_dir / ".env"
    example_path = base_dir / ".env.example"
    if not example_path.exists() and getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            bundled_example = Path(meipass) / ".env.example"
            if bundled_example.exists():
                example_path = bundled_example
    if env_path.exists() or not example_path.exists():
        return
    shutil.copyfile(example_path, env_path)


_BASE_DIR = _resolve_runtime_base_dir()
_ensure_env_file(_BASE_DIR)
load_dotenv(dotenv_path=_BASE_DIR / ".env")

logger = logging.getLogger(__name__)

_DEPRECATED_ENV_KEYS = (
    "ROLEPLAY_ENABLED",
    "ROLEPLAY_PERSONA_PROMPT",
    "ROLEPLAY_PRIORITY_MODE",
    "CODING_ASSISTANCE_ENABLED",
    "EXTERNAL_LLM_FALLBACK_ENABLED",
    "EXTERNAL_LLM_BASE_URL",
    "EXTERNAL_LLM_API_KEY",
    "EXTERNAL_LLM_MODEL",
)

_DEFAULT_SYSTEM_PROMPT = (
    "你是這個 LINE Bot 的助理。"
    "請優先使用繁體中文，回答要直接、清楚、避免虛構。"
    "遇到需要即時資訊的問題，應以可查到的資料為準；"
    "若資料不足，請明確說明限制。"
)


@dataclass(frozen=True)
class Settings:
    line_channel_access_token: str = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_channel_secret: str = os.getenv("LINE_CHANNEL_SECRET", "")
    line_bot_name: str = os.getenv("LINE_BOT_NAME", "")
    line_group_require_mention: bool = _is_truthy(
        os.getenv("LINE_GROUP_REQUIRE_MENTION", "true")
    )

    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    tray_ui_enabled: bool = _is_truthy(os.getenv("TRAY_UI_ENABLED", "false"))
    app_reload: bool = (
        False if getattr(sys, "frozen", False) else _is_truthy(os.getenv("APP_RELOAD", "true"))
    )

    lm_studio_base_url: str = os.getenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    lm_studio_exe_path: str = os.getenv("LM_STUDIO_EXE_PATH", "")
    lm_studio_chat_model: str = os.getenv("LM_STUDIO_CHAT_MODEL", "qwen/qwen3.5-9b")
    lm_studio_embed_model: str = os.getenv(
        "LM_STUDIO_EMBED_MODEL",
        "text-embedding-nomic-embed-text-v1.5",
    )
    lm_studio_timeout_seconds: int = int(os.getenv("LM_STUDIO_TIMEOUT_SECONDS", "20"))
    lm_studio_guard_timeout_seconds: int = int(
        os.getenv("LM_STUDIO_GUARD_TIMEOUT_SECONDS", "12")
    )
    lm_studio_max_tokens: int = int(os.getenv("LM_STUDIO_MAX_TOKENS", "256"))
    lm_studio_temperature: float = float(os.getenv("LM_STUDIO_TEMPERATURE", "0.3"))

    sqlite_path: str = os.getenv("SQLITE_PATH", "data/app.db")
    session_max_turns: int = int(os.getenv("SESSION_MAX_TURNS", "8"))

    response_guard_enabled: bool = _is_truthy(os.getenv("RESPONSE_GUARD_ENABLED", "true"))
    response_guard_rewrite_enabled: bool = _is_truthy(
        os.getenv("RESPONSE_GUARD_REWRITE_ENABLED", "true")
    )
    response_guard_max_input_chars: int = int(
        os.getenv("RESPONSE_GUARD_MAX_INPUT_CHARS", "4000")
    )

    max_context_chars: int = int(os.getenv("MAX_CONTEXT_CHARS", "6000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    rag_enabled: bool = _is_truthy(os.getenv("RAG_ENABLED", "false"))
    knowledge_dir: str = os.getenv("KNOWLEDGE_DIR", "data/knowledge")
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "3"))
    rag_chunk_size: int = int(os.getenv("RAG_CHUNK_SIZE", "500"))
    rag_chunk_overlap: int = int(os.getenv("RAG_CHUNK_OVERLAP", "100"))

    web_search_backend: str = os.getenv("WEB_SEARCH_BACKEND", "bing").strip().lower()
    web_search_enabled: bool = _is_truthy(os.getenv("WEB_SEARCH_ENABLED", "true"))
    web_search_timeout_seconds: int = int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "12"))

    system_prompt: str = os.getenv("SYSTEM_PROMPT", _DEFAULT_SYSTEM_PROMPT)

    @property
    def line_ready(self) -> bool:
        return bool(self.line_channel_access_token and self.line_channel_secret)


@lru_cache
def get_settings() -> Settings:
    deprecated_keys = [key for key in _DEPRECATED_ENV_KEYS if os.getenv(key) is not None]
    if deprecated_keys:
        logger.warning("Ignored deprecated .env keys: %s", ", ".join(sorted(deprecated_keys)))
    return Settings()
