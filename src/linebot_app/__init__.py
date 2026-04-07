from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

try:
    from .app import app
    from .config import get_settings
except ImportError:
    # Support running `python src/linebot_app/__init__.py` directly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from linebot_app.app import app
    from linebot_app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "linebot_app.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
        factory=False,
    )


__all__ = ["app", "main"]
