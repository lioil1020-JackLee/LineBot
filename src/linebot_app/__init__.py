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
    should_run_tray = sys.platform == "win32"
    if should_run_tray:
        try:
            from .tray_app import run_tray_app

            run_tray_app()
            return
        except Exception:
            # Fallback to normal uvicorn run if tray mode fails unexpectedly.
            pass

    uvicorn_kwargs: dict[str, object] = {
        "host": settings.app_host,
        "port": settings.app_port,
        "reload": settings.app_reload,
        "factory": False,
    }
    if getattr(sys, "frozen", False) and (sys.stderr is None or sys.stdout is None):
        uvicorn_kwargs["log_config"] = None

    uvicorn.run(
        "linebot_app.app:app",
        **uvicorn_kwargs,
    )


__all__ = ["app", "main"]
