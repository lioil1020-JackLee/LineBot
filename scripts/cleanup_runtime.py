from __future__ import annotations

import argparse

from linebot_app.config import get_settings
from linebot_app.repositories.llm_log_repository import LLMLogRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup runtime data")
    parser.add_argument("--llm-log-days", type=int, default=7, help="Retain llm logs for N days")
    args = parser.parse_args()

    settings = get_settings()
    repo = LLMLogRepository(settings.sqlite_path)
    deleted = repo.delete_older_than_days(days=args.llm_log_days)
    print(f"Deleted {deleted} llm log rows older than {args.llm_log_days} days")


if __name__ == "__main__":
    main()
