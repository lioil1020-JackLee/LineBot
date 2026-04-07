from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

import httpx
import pystray
import uvicorn
from PIL import Image, ImageDraw

from .config import get_settings

_DEFAULT_PRESETS = ["default", "virtual_partner", "close_friend", "mentor", "secretary"]
_BUILTIN_PRESET_LABELS = {
    "default": "預設（關閉角色）",
    "virtual_partner": "虛擬情人",
    "close_friend": "好友",
    "mentor": "導師",
    "secretary": "秘書",
    "travel_guide": "旅遊顧問",
}


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
        self.root.geometry("460x320")
        self.root.resizable(True, False)
        self.root.minsize(430, 320)

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

        self.preset_names: list[str] = list(_DEFAULT_PRESETS)
        self.preset_prompt_map: dict[str, str] = {}
        self.persona_var = tk.StringVar(value=self._label_for_preset("default"))
        self.new_role_name_var = tk.StringVar(value="")
        self.persona_status_var = tk.StringVar(value="角色狀態：未套用")

        self._build_ui()
        self._start_server()
        self.root.after(1200, self._sync_persona_from_server)

        self.root.bind("<Unmap>", self._on_unmap)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    def _label_for_preset(self, preset: str) -> str:
        key = preset.strip().lower()
        return _BUILTIN_PRESET_LABELS.get(key, key)

    def _preset_from_label(self, label: str) -> str:
        selected = label.strip()
        for key, display in _BUILTIN_PRESET_LABELS.items():
            if display == selected:
                return key
        return selected.lower()

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

        role_frame = tk.LabelFrame(self.root, text="角色扮演設定", padx=12, pady=8)
        role_frame.pack(fill="x", padx=12, pady=10)

        preset_row = tk.Frame(role_frame)
        preset_row.pack(fill="x", pady=(0, 6))

        tk.Label(preset_row, text="預設角色：", width=10, anchor="w").pack(side="left")
        preset_box = ttk.Combobox(
            preset_row,
            textvariable=self.persona_var,
            values=[self._label_for_preset(name) for name in self.preset_names],
            state="readonly",
            width=24,
        )
        self.preset_box = preset_box
        preset_box.bind("<<ComboboxSelected>>", self._on_preset_selected)
        preset_box.pack(side="left", padx=(0, 8))

        tk.Button(preset_row, text="套用角色", command=self._apply_preset).pack(side="left")
        tk.Button(
            preset_row,
            text="清除角色",
            command=self._clear_persona,
        ).pack(side="left", padx=(8, 0))

        custom_row = tk.Frame(role_frame)
        custom_row.pack(fill="x")
        tk.Label(custom_row, text="自訂人設：", width=10, anchor="nw").pack(side="left")

        self.custom_persona_text = tk.Text(custom_row, height=4, width=44)
        self.custom_persona_text.pack(side="left", fill="x", expand=True)

        save_row = tk.Frame(role_frame)
        save_row.pack(fill="x", pady=(8, 0))
        tk.Label(save_row, text="角色名稱：", width=10, anchor="w").pack(side="left")
        tk.Entry(save_row, textvariable=self.new_role_name_var, width=20).pack(side="left")
        tk.Button(
            save_row,
            text="儲存角色",
            command=self._save_custom_role,
        ).pack(side="left", padx=(8, 0))

        io_row = tk.Frame(role_frame)
        io_row.pack(fill="x", pady=(8, 0))
        tk.Button(io_row, text="匯出角色", command=self._export_roles).pack(side="left")
        tk.Button(
            io_row,
            text="匯入角色",
            command=self._import_roles,
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            role_frame,
            textvariable=self.persona_status_var,
            anchor="w",
            justify="left",
            fg="#1f6f43",
        ).pack(fill="x", pady=(8, 0))

    def _api_base_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.app_port}"

    def _post_persona(self, payload: dict[str, str]) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=4.0) as client:
                response = client.post(f"{self._api_base_url()}/admin/persona", json=payload)
            if response.status_code >= 400:
                return False, f"設定失敗：HTTP {response.status_code}"

            data = response.json()
            persona_text = str(data.get("persona_prompt") or "").strip()
            if persona_text:
                return True, f"角色已套用：{persona_text[:48]}"
            return True, "角色已清除（回到預設）"
        except Exception as exc:
            return False, f"設定失敗：{exc}"

    def _sync_persona_from_server(self) -> None:
        try:
            with httpx.Client(timeout=4.0) as client:
                response = client.get(f"{self._api_base_url()}/admin/persona")
                presets_response = client.get(f"{self._api_base_url()}/admin/persona/presets")
            if response.status_code >= 400 or presets_response.status_code >= 400:
                self.persona_status_var.set("角色狀態：尚未同步")
                return

            data = response.json()
            prompt = str(data.get("persona_prompt") or "").strip()
            presets_payload = presets_response.json()
            raw_items = presets_payload.get("items") or []
            if isinstance(raw_items, list):
                names: list[str] = []
                mapping: dict[str, str] = {}
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    role_prompt = str(item.get("prompt") or "").strip()
                    if not name:
                        continue
                    names.append(name)
                    mapping[name] = role_prompt
                if names:
                    self.preset_names = names
                    self.preset_prompt_map = mapping
                    self.preset_box.configure(
                        values=[self._label_for_preset(name) for name in self.preset_names]
                    )

            active_preset = str(data.get("active_preset") or "default").strip() or "default"
            if active_preset in self.preset_names:
                self.persona_var.set(self._label_for_preset(active_preset))

            if prompt:
                self.custom_persona_text.delete("1.0", tk.END)
                self.custom_persona_text.insert("1.0", prompt)
                self.persona_status_var.set(f"目前角色：{prompt[:48]}")
            else:
                self.persona_status_var.set("目前角色：預設")

            if active_preset in _BUILTIN_PRESET_LABELS:
                self.new_role_name_var.set("")
            else:
                self.new_role_name_var.set(active_preset)
        except Exception:
            self.persona_status_var.set("角色狀態：無法連線到本機服務")

    def _on_preset_selected(self, _: Any) -> None:
        preset = self._preset_from_label(self.persona_var.get())
        if not preset:
            return

        prompt = self.preset_prompt_map.get(preset, "").strip()
        self.custom_persona_text.delete("1.0", tk.END)
        if prompt:
            self.custom_persona_text.insert("1.0", prompt)

        if preset in _BUILTIN_PRESET_LABELS:
            self.new_role_name_var.set("")
        else:
            self.new_role_name_var.set(preset)

    def _apply_preset(self) -> None:
        selected_label = self.persona_var.get().strip()
        preset = self._preset_from_label(selected_label) or "default"
        ok, message = self._post_persona({"preset": preset})
        self.persona_status_var.set(message)
        if ok and preset == "default":
            self.custom_persona_text.delete("1.0", tk.END)

    def _clear_persona(self) -> None:
        ok, message = self._post_persona({})
        self.persona_status_var.set(message)
        if ok:
            self.persona_var.set(self._label_for_preset("default"))
            self.custom_persona_text.delete("1.0", tk.END)

    def _save_custom_role(self) -> None:
        role_name = self.new_role_name_var.get().strip().lower()
        custom_prompt = self.custom_persona_text.get("1.0", tk.END).strip()
        if not role_name:
            self.persona_status_var.set("請先輸入角色名稱")
            return
        if not custom_prompt:
            self.persona_status_var.set("請先輸入自訂人設內容")
            return

        try:
            with httpx.Client(timeout=4.0) as client:
                response = client.post(
                    f"{self._api_base_url()}/admin/persona/presets",
                    json={
                        "name": role_name,
                        "prompt": custom_prompt,
                        "set_active": False,
                    },
                )
            if response.status_code >= 400:
                self.persona_status_var.set(f"儲存失敗：HTTP {response.status_code}")
                return
        except Exception as exc:
            self.persona_status_var.set(f"儲存失敗：{exc}")
            return

        self.persona_status_var.set(f"已儲存角色：{role_name}，請用『套用角色』啟用")
        self._sync_persona_from_server()

    def _export_roles(self) -> None:
        save_path = filedialog.asksaveasfilename(
            title="匯出角色設定",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not save_path:
            return
        try:
            with httpx.Client(timeout=6.0) as client:
                response = client.get(f"{self._api_base_url()}/admin/persona/export")
            if response.status_code >= 400:
                self.persona_status_var.set(f"匯出失敗：HTTP {response.status_code}")
                return
            data = response.json()
            with open(save_path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            self.persona_status_var.set(f"已匯出角色：{save_path}")
        except Exception as exc:
            self.persona_status_var.set(f"匯出失敗：{exc}")

    def _import_roles(self) -> None:
        file_path = filedialog.askopenfilename(
            title="匯入角色設定",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not file_path:
            return
        try:
            with open(file_path, encoding="utf-8") as fp:
                payload = json.load(fp)

            if not isinstance(payload, dict):
                self.persona_status_var.set("匯入失敗：JSON 格式不正確")
                return
            items = payload.get("items")
            if not isinstance(items, list) or not items:
                self.persona_status_var.set("匯入失敗：找不到 items")
                return

            with httpx.Client(timeout=8.0) as client:
                response = client.post(
                    f"{self._api_base_url()}/admin/persona/import",
                    json={"items": items, "preserve_active": False},
                )
            if response.status_code >= 400:
                self.persona_status_var.set(f"匯入失敗：HTTP {response.status_code}")
                return
            result = response.json()
            self.persona_status_var.set(
                "匯入完成：成功 "
                f"{result.get('imported', 0)}、略過 {result.get('skipped', 0)}，"
                "請用『套用角色』切換"
            )
            self._sync_persona_from_server()
        except Exception as exc:
            self.persona_status_var.set(f"匯入失敗：{exc}")

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
