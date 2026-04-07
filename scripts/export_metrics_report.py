from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

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

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": health_service.metrics(limit=500),
        "recent_logs": [item.__dict__ for item in log_repo.get_recent(limit=20)],
    }

    output_dir = Path("data/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"metrics_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report exported: {output_path}")


if __name__ == "__main__":
    main()
