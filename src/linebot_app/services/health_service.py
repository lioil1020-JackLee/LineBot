from __future__ import annotations

from dataclasses import asdict

from linebot_app.db import check_db
from linebot_app.repositories.llm_log_repository import LLMLogRepository

from .llm_service import LLMService


class HealthService:
    def __init__(
        self,
        *,
        llm_service: LLMService,
        llm_log_repository: LLMLogRepository,
        sqlite_path: str,
        line_configured: bool,
    ) -> None:
        self.llm_service = llm_service
        self.llm_log_repository = llm_log_repository
        self.sqlite_path = sqlite_path
        self.line_configured = line_configured

    def basic(self) -> dict[str, object]:
        return {
            "status": "ok",
            "line_configured": self.line_configured,
        }

    def detail(self) -> dict[str, object]:
        db_ok = check_db(self.sqlite_path)
        lm_ok = self.llm_service.is_available()
        status = "ok" if db_ok and lm_ok else "degraded"
        recent_logs = [asdict(item) for item in self.llm_log_repository.get_recent(limit=5)]
        return {
            "status": status,
            "line_configured": self.line_configured,
            "sqlite": {"ok": db_ok, "path": self.sqlite_path},
            "lm_studio": {"ok": lm_ok, "base_url": self.llm_service.base_url},
            "recent_llm_logs": recent_logs,
        }