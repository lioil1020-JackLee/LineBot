from __future__ import annotations

from linebot_app.config import get_settings
from linebot_app.db import init_db


def main() -> None:
    settings = get_settings()
    init_db(settings.sqlite_path)
    print(f"Database initialized at {settings.sqlite_path}")


if __name__ == "__main__":
    main()
