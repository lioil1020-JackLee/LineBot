from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Any

import httpx
import pystray
import uvicorn
from PIL import Image, ImageDraw

from .config import get_settings


def _icon_path() -> Path | None:
    exe_parent = Path(getattr(sys, "executable", "")).resolve().parent
    candidates = [
        Path.cwd() / "lioil.ico",
        Path(getattr(sys, "_MEIPASS", "")) / "lioil.ico",
        exe_parent / "lioil.ico",
        exe_parent / "_internal" / "lioil.ico",
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

    image = Image.new("RGB", (64, 64), "#2d8cf0")
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 48, 48), fill="#ffffff")
    return image


class TrayLineBotApp:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.root = tk.Tk()
        self.root.title("LineBot")
        self.root.geometry("500x280")
        self.root.resizable(True, False)
        self.root.minsize(450, 280)

        icon_file = _icon_path()
        if icon_file is not None:
            try:
                self.root.iconbitmap(default=str(icon_file))
            except Exception:
                pass

        self.server: uvicorn.Server | None = None
        self.server_thread: threading.Thread | None = None
        self.tray_icon: pystray.Icon | None = None
        self.tray_thread: threading.Thread | None = None
        self.status_var = tk.StringVar(value="正在啟動服務...")

        self._build_ui()
        self._start_server()
        self.root.after(1200, self._refresh_health_status)

        self.root.bind("<Unmap>", self._on_unmap)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

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
            text="視窗最小化後會縮到系統匣，你可以從圖示重新打開視窗或直接結束程式。",
            padx=12,
            pady=4,
            justify="left",
            anchor="w",
        )
        hint.pack(fill="x")

        ops_frame = tk.LabelFrame(self.root, text="服務狀態", padx=12, pady=8)
        ops_frame.pack(fill="x", padx=12, pady=10)

        tk.Label(
            ops_frame,
            textvariable=self.status_var,
            anchor="w",
            justify="left",
            fg="#1f6f43",
        ).pack(fill="x")

        action_row = tk.Frame(ops_frame)
        action_row.pack(fill="x", pady=(8, 0))
        tk.Button(
            action_row,
            text="重新整理狀態",
            command=self._refresh_health_status,
        ).pack(side="left")
        tk.Button(action_row, text="結束程式", command=self._quit).pack(side="left", padx=(8, 0))

    def _api_base_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.app_port}"

    def _refresh_health_status(self) -> None:
        try:
            with httpx.Client(timeout=4.0) as client:
                response = client.get(f"{self._api_base_url()}/health")
            if response.status_code >= 400:
                self.status_var.set(f"服務狀態異常 (HTTP {response.status_code})")
                return

            data = response.json()
            status = str(data.get("status") or "unknown").strip()
            self.status_var.set(f"服務狀態：{status}")
        except Exception:
            self.status_var.set("服務狀態：無法連線到本機 API")

    def _start_server(self) -> None:
        config = uvicorn.Config(
            "linebot_app.app:app",
            host=self.settings.app_host,
            port=self.settings.app_port,
            reload=False,
            factory=False,
            log_level=self.settings.log_level.lower(),
            log_config=None,
        )
        self.server = uvicorn.Server(config)
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()

    def _on_unmap(self, _: Any) -> None:
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
            pystray.MenuItem("結束程式", lambda: self._quit()),
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
