from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Any

import pystray
import uvicorn
from PIL import Image, ImageDraw

from .config import get_settings


def _icon_path() -> Path | None:
    candidates = [
        Path.cwd() / "lioil.ico",
        Path(getattr(sys, "_MEIPASS", "")) / "lioil.ico",
        Path(getattr(sys, "executable", "")).resolve().parent / "lioil.ico",
    ]
    for path in candidates:
        if path and path.exists():
            return path
    return None


def _load_tray_icon() -> Image.Image:
    icon_file = _icon_path()
    if icon_file is not None:
        try:
            return Image.open(icon_file)
        except Exception:
            pass

    # Fallback icon if file is missing or unreadable.
    image = Image.new("RGB", (64, 64), "#2d8cf0")
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 48, 48), fill="#ffffff")
    return image


class TrayLineBotApp:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.root = tk.Tk()
        self.root.title("LineBot")
        self.root.geometry("360x140")
        self.root.resizable(False, False)

        self.server: uvicorn.Server | None = None
        self.server_thread: threading.Thread | None = None
        self.tray_icon: pystray.Icon | None = None
        self.tray_thread: threading.Thread | None = None

        self._build_ui()
        self._start_server()

        self.root.bind("<Unmap>", self._on_unmap)
        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

    def _build_ui(self) -> None:
        status = tk.Label(
            self.root,
            text=f"LineBot running on {self.settings.app_host}:{self.settings.app_port}",
            padx=12,
            pady=16,
            justify="left",
            anchor="w",
        )
        status.pack(fill="x")

        hint = tk.Label(
            self.root,
            text="最小化後會縮到系統列，右鍵圖示可顯示視窗或結束程式。",
            padx=12,
            pady=4,
            justify="left",
            anchor="w",
        )
        hint.pack(fill="x")

        actions = tk.Frame(self.root)
        actions.pack(fill="x", padx=12, pady=10)

        minimize_btn = tk.Button(actions, text="最小化到系統列", command=self._minimize_to_tray)
        minimize_btn.pack(side="left")

        exit_btn = tk.Button(actions, text="結束", command=self._quit)
        exit_btn.pack(side="right")

    def _start_server(self) -> None:
        config = uvicorn.Config(
            "linebot_app.app:app",
            host=self.settings.app_host,
            port=self.settings.app_port,
            reload=False,
            factory=False,
            log_level=self.settings.log_level.lower(),
            # Windowed executable may have no stderr/stdout handles.
            # Disable uvicorn's default log formatter config to avoid isatty errors.
            log_config=None,
        )
        self.server = uvicorn.Server(config)
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()

    def _on_unmap(self, _: Any) -> None:
        # Only hide when user minimizes the window.
        if self.root.state() == "iconic":
            self._minimize_to_tray()

    def _minimize_to_tray(self) -> None:
        self.root.withdraw()
        self._ensure_tray_icon()

    def _ensure_tray_icon(self) -> None:
        if self.tray_icon is not None:
            return

        menu = pystray.Menu(
            pystray.MenuItem("顯示視窗", lambda: self._show_window()),
            pystray.MenuItem("結束", lambda: self._quit()),
        )
        self.tray_icon = pystray.Icon("linebot-app", _load_tray_icon(), "LineBot", menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def _show_window(self) -> None:
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)
        self.root.after(0, self.root.focus_force)

    def _quit(self) -> None:
        if self.server is not None:
            self.server.should_exit = True

        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None

        self.root.after(100, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()


def run_tray_app() -> None:
    app = TrayLineBotApp()
    app.run()
