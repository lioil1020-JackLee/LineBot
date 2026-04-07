from __future__ import annotations

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


@dataclass(frozen=True)
class Settings:
    line_channel_access_token: str = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_channel_secret: str = os.getenv("LINE_CHANNEL_SECRET", "")
    line_bot_name: str = os.getenv("LINE_BOT_NAME", "")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    # Frozen executable must disable reload to avoid watchdog respawn loops.
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
    lm_studio_timeout_seconds: int = int(os.getenv("LM_STUDIO_TIMEOUT_SECONDS", "90"))
    lm_studio_max_tokens: int = int(os.getenv("LM_STUDIO_MAX_TOKENS", "1024"))
    lm_studio_temperature: float = float(os.getenv("LM_STUDIO_TEMPERATURE", "0.7"))
    sqlite_path: str = os.getenv("SQLITE_PATH", "data/app.db")
    session_max_turns: int = int(os.getenv("SESSION_MAX_TURNS", "8"))
    session_memory_enabled: bool = _is_truthy(os.getenv("SESSION_MEMORY_ENABLED", "true"))
    session_memory_trigger_messages: int = int(os.getenv("SESSION_MEMORY_TRIGGER_MESSAGES", "6"))
    session_memory_window_messages: int = int(os.getenv("SESSION_MEMORY_WINDOW_MESSAGES", "12"))
    session_memory_max_chars: int = int(os.getenv("SESSION_MEMORY_MAX_CHARS", "1200"))
    coding_assistance_enabled: bool = _is_truthy(os.getenv("CODING_ASSISTANCE_ENABLED", "false"))
    response_guard_enabled: bool = _is_truthy(os.getenv("RESPONSE_GUARD_ENABLED", "true"))
    response_guard_rewrite_enabled: bool = _is_truthy(
        os.getenv("RESPONSE_GUARD_REWRITE_ENABLED", "true")
    )
    max_context_chars: int = int(os.getenv("MAX_CONTEXT_CHARS", "6000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    rag_enabled: bool = _is_truthy(os.getenv("RAG_ENABLED", "false"))
    knowledge_dir: str = os.getenv("KNOWLEDGE_DIR", "data/knowledge")
    agent_enabled: bool = _is_truthy(os.getenv("AGENT_ENABLED", "true"))
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "3"))
    rag_chunk_size: int = int(os.getenv("RAG_CHUNK_SIZE", "500"))
    rag_chunk_overlap: int = int(os.getenv("RAG_CHUNK_OVERLAP", "100"))
    web_search_provider: str = os.getenv("WEB_SEARCH_PROVIDER", "duckduckgo")
    perplexity_base_url: str = os.getenv("PERPLEXITY_BASE_URL", "https://api.perplexity.ai")
    perplexity_api_key: str = os.getenv("PERPLEXITY_API_KEY", "")
    perplexity_model: str = os.getenv("PERPLEXITY_MODEL", "sonar")
    external_llm_fallback_enabled: bool = _is_truthy(
        os.getenv(
            "EXTERNAL_LLM_FALLBACK_ENABLED",
            "false",
        )
    )
    external_llm_base_url: str = os.getenv("EXTERNAL_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    external_llm_api_key: str = os.getenv("EXTERNAL_LLM_API_KEY", "")
    external_llm_models: str = os.getenv(
        "EXTERNAL_LLM_MODELS",
        "openai/gpt-5-mini,google/gemini-2.5-flash",
    )
    external_llm_timeout_seconds: int = int(os.getenv("EXTERNAL_LLM_TIMEOUT_SECONDS", "45"))
    image_ocr_enabled: bool = _is_truthy(os.getenv("IMAGE_OCR_ENABLED", "true"))
    file_parser_enabled: bool = _is_truthy(os.getenv("FILE_PARSER_ENABLED", "true"))
    # 假訊息查證功能
    factcheck_enabled: bool = _is_truthy(os.getenv("FACTCHECK_ENABLED", "true"))
    factcheck_max_search_queries: int = int(os.getenv("FACTCHECK_MAX_SEARCH_QUERIES", "2"))
    factcheck_max_results_per_query: int = int(os.getenv("FACTCHECK_MAX_RESULTS_PER_QUERY", "4"))
    system_prompt: str = os.getenv(
        "SYSTEM_PROMPT",
        (
            "你是 LINE 萬事通助理，請使用繁體中文回答，"
            "擅長日常知識、學習、工作、生活建議與資訊整理。"
            "回覆需清楚、實用、結構化；若資訊不足，請誠實說明限制。"
        ),
    )

    @property
    def line_ready(self) -> bool:
        return bool(self.line_channel_access_token and self.line_channel_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
