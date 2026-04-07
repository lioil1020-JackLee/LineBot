from __future__ import annotations

from dataclasses import asdict

from linebot_app.config import get_settings
from linebot_app.repositories.llm_log_repository import LLMLogRepository
from linebot_app.services.health_service import HealthService
from linebot_app.services.llm_service import LLMService


def main() -> None:
    settings = get_settings()
    llm_service = LLMService(
        base_url=settings.lm_studio_base_url,
        chat_model=settings.lm_studio_chat_model,
        embed_model=settings.lm_studio_embed_model,
        timeout_seconds=settings.lm_studio_timeout_seconds,
        max_tokens=settings.lm_studio_max_tokens,
        temperature=settings.lm_studio_temperature,
    )
    log_repo = LLMLogRepository(settings.sqlite_path)
    health_service = HealthService(
        llm_service=llm_service,
        llm_log_repository=log_repo,
        sqlite_path=settings.sqlite_path,
        line_configured=settings.line_ready,
    )

    detail = health_service.detail()
    print("=== Health Detail ===")
    print(f"status: {detail['status']}")
    print(f"line_configured: {detail['line_configured']}")
    print(f"sqlite: {detail['sqlite']}")
    print(f"lm_studio: {detail['lm_studio']}")

    print("\n=== Recent LLM Logs ===")
    for item in log_repo.get_recent(limit=10):
        print(asdict(item))


if __name__ == "__main__":
    main()
