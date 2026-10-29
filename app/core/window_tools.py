"""Pencere yönetimi: agent-desktop'ın pencere komutlarının Jarvis uyarlaması.

Açık pencereleri listeler; odaklama, küçültme, büyütme, geri yükleme, taşıma,
boyutlandırma ve nazik kapatma yapar. Kapatma WM_CLOSE ile istenir; kaydedilmemiş
belge varsa uygulamanın kendi onay penceresi açılır, zorla sonlandırma yapılmaz.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, List, Optional, Tuple

from app.core.safety import ActionForbidden
from app.core.screen_agent import normalize_text, secure_desktop_reason

MAX_WINDOWS = 100
MAX_COORDINATE = 30_000
MIN_WINDOW_SIZE = 120
MAX_WINDOW_SIZE = 20_000
CLOSE_WAIT_SECONDS = 2.5

CRITICAL_PROCESSES = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "dwm.exe",
    "explorer.exe",
    "consent.exe",
    "logonui.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "taskhostw.exe",
}

ACTION_ALIASES = {
    "list": "list",
    "list_windows": "list",
    "focus": "focus",
    "focus_window": "focus",
    "minimize": "minimize",
    "maximize": "maximize",
    "restore": "restore",
    "move": "move",
    "resize": "resize",
    "set_bounds": "set_bounds",
    "close": "close",
    "close_app": "close",
}


def _int_arg(args: dict, key: str, minimum: int, maximum: int) -> int:
    value = args.get(key)
    if value is None:
        raise ActionForbidden(f"Pencere işlemi için {key} gerekli.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ActionForbidden(f"{key} sayı olmalı: {value!r}")
    if not minimum <= number <= maximum:
        raise ActionForbidden(f"{key} {minimum} ile {maximum} arasında olmalı.")
    return number


def _rect_of(window) -> Optional[Tuple[int, int, int, int]]:
    try:
        rectangle = window.rectangle()
        rect = (
            int(rectangle.left),
            int(rectangle.top),
            int(rectangle.right),
            int(rectangle.bottom),
        )
    except Exception:
        return None
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    return rect


class WindowTools:
    def __init__(self, ui, desktop_factory=None) -> None:
        self.ui = ui
        self._desktop_factory = desktop_factory

    # -- toplama -------------------------------------------------------------
    def _desktop(self):
        if self._desktop_factory is not None:
            return self._desktop_factory()
        try:
            from pywinauto import Desktop
        except ImportError:
            raise ActionForbidden(
                "Pencere yönetimi için pywinauto gerekli; install_dependency ile kurulabilir."
            )
        try:
            return Desktop(backend="uia")
        except Exception as error:
            raise ActionForbidden(f"Masaüstü pencereleri okunamadı: {error}")

    def _windows(self) -> List[Any]:
        try:
            rows = list(self._desktop().windows())
        except ActionForbidden:
            raise
        except Exception as error:
            raise ActionForbidden(f"Pencereler listelenemedi: {error}")
        return [
            window
            for window in rows
            if str(getattr(window, "window_text", lambda: "")() or "").strip()
        ]

    @staticmethod
    def _window_title(window) -> str:
        try:
            return str(window.window_text() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _window_handle(window) -> int:
        try:
            return int(getattr(window, "handle", 0) or 0)
        except Exception:
            return 0

    @staticmethod
    def _process_id(window) -> int:
        try:
            return int(window.process_id() or 0)
        except Exception:
            return 0

    @staticmethod
    def _process_name(window) -> str:
        try:
            import psutil

            return str(psutil.Process(WindowTools._process_id(window)).name() or "").casefold()
        except Exception:
            return ""

    @staticmethod
    def _foreground_handle() -> int:
        try:
            import ctypes

            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    @staticmethod
    def _window_state(window) -> Tuple[bool, bool]:
        minimized = maximized = False
        try:
            minimized = bool(window.is_minimized())
        except Exception:
            pass
        try:
            maximized = bool(window.is_maximized())
        except Exception:
            pass
        return minimized, maximized

    def _describe(self, window, foreground: int = 0) -> dict:
        handle = self._window_handle(window)
        minimized, maximized = self._window_state(window)
        rect = _rect_of(window)
        return {
            "handle": handle,
            "title": self._window_title(window),
            "process": self._process_name(window),
            "process_id": self._process_id(window),
            "rect": list(rect) if rect else [],
            "minimized": minimized,
            "maximized": maximized,
            "foreground": bool(handle and handle == foreground),
        }

    def _resolve(self, args: dict):
        handle = 0
        if args.get("handle") not in (None, ""):
            try:
                handle = int(args["handle"])
            except (TypeError, ValueError):
                raise ActionForbidden("handle sayı olmalı.")
        title = str(args.get("title") or "").strip()
        if handle:
            for window in self._windows():
                if self._window_handle(window) == handle:
                    return window
            raise ActionForbidden(f"Handle {handle} ile açık pencere bulunamadı.")
        if title:
            try:
                return self.ui._window(title)
            except ActionForbidden:
                raise
            except Exception as error:
                raise ActionForbidden(f"Pencere bulunamadı: {title} ({error})")
        raise ActionForbidden("Pencere işlemi için title veya handle gerekli.")

    @staticmethod
    def _ensure_safe(window, action: str) -> None:
        title = WindowTools._window_title(window)
        process = WindowTools._process_name(window)
        reason = secure_desktop_reason(title, process)
        if reason:
            raise ActionForbidden(reason)
        if action == "close":
            if process in CRITICAL_PROCESSES:
                raise ActionForbidden(f"Kritik sistem süreci kapatılamaz: {process}")
            if WindowTools._process_id(window) == os.getpid():
                raise ActionForbidden("Jarvis'in kendi penceresi bu araçla kapatılamaz.")

    # -- eylemler ------------------------------------------------------------
    def list_windows(self, args: Optional[dict] = None) -> str:
        args = args or {}
        query = normalize_text(str(args.get("filter") or ""))
        try:
            limit = int(args.get("limit", 40) or 40)
        except (TypeError, ValueError):
            limit = 40
        limit = min(max(limit, 1), MAX_WINDOWS)
        foreground = self._foreground_handle()
        rows = []
        for window in self._windows():
            title = self._window_title(window)
            process = self._process_name(window)
            if query and query not in normalize_text(f"{title} {process}"):
                continue
            rows.append(self._describe(window, foreground))
        rows.sort(key=lambda item: (not item["foreground"], normalize_text(item["title"])))
        rows = rows[:limit]
        return json.dumps(
            {
                "count": len(rows),
                "windows": rows,
                "note": (
                    "Pencereler canlı sistemden okundu. İşlem için title veya handle kullan; "
                    "başlıklar değişebilir, işlem öncesi listeyi yenile."
                ),
            },
            ensure_ascii=False,
        )

    def run(self, args: Optional[dict] = None) -> str:
        args = args or {}
        action = ACTION_ALIASES.get(str(args.get("action") or "list").strip().casefold())
        if action is None:
            raise ActionForbidden(
                "window_control action: list, focus, minimize, maximize, restore, move, resize, "
                "set_bounds veya close olmalı."
            )
        if action == "list":
            return self.list_windows(args)
        window = self._resolve(args)
        self._ensure_safe(window, action)
        before = self._describe(window, self._foreground_handle())
        mechanism = ""
        if action == "focus":
            try:
                focused = bool(self.ui.focus(window))
            except Exception as error:
                raise ActionForbidden(f"Pencere öne getirilemedi: {error}")
            if not focused:
                raise ActionForbidden("Pencere öne getirilemedi; işlem durduruldu.")
            mechanism = "foreground_verified"
        elif action == "minimize":
            window.minimize()
            mechanism = "minimize"
        elif action == "maximize":
            window.maximize()
            mechanism = "maximize"
        elif action == "restore":
            window.restore()
            mechanism = "restore"
        elif action in {"move", "resize", "set_bounds"}:
            rect = _rect_of(window) or (0, 0, 800, 600)
            x, y = rect[0], rect[1]
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            if action in {"move", "set_bounds"}:
                x = _int_arg(args, "x", -MAX_COORDINATE, MAX_COORDINATE)
                y = _int_arg(args, "y", -MAX_COORDINATE, MAX_COORDINATE)
            if action in {"resize", "set_bounds"}:
                width = _int_arg(args, "width", MIN_WINDOW_SIZE, MAX_WINDOW_SIZE)
                height = _int_arg(args, "height", MIN_WINDOW_SIZE, MAX_WINDOW_SIZE)
            try:
                window.move_window(x=x, y=y, width=width, height=height, repaint=True)
            except TypeError:
                window.move_window(x, y, width, height)
            mechanism = action
        elif action == "close":
            try:
                window.close()
            except Exception as error:
                raise ActionForbidden(f"Pencere kapatma isteği gönderilemedi: {error}")
            mechanism = "wm_close"

        time.sleep(0.25 if action in {"focus", "minimize", "maximize", "restore"} else 0.35)
        after = self._describe(window, self._foreground_handle())
        if action == "close":
            closed = False
            deadline = time.monotonic() + CLOSE_WAIT_SECONDS
            while time.monotonic() < deadline:
                try:
                    exists = bool(window.exists())
                except Exception:
                    exists = False
                if not exists:
                    closed = True
                    break
                time.sleep(0.15)
            payload = {
                "action": action,
                "mechanism": mechanism,
                "window": before,
                "closed": closed,
                "note": (
                    "Kapatma istendi. Uygulama kaydedilmemiş belge için kendi onay penceresini "
                    "açmış olabilir; zorla sonlandırma yapılmadı."
                    if not closed
                    else "Pencere kapandı."
                ),
            }
            return json.dumps(payload, ensure_ascii=False)

        verified = True
        if action == "focus":
            verified = after["foreground"]
        elif action == "minimize":
            verified = after["minimized"]
        elif action == "maximize":
            verified = after["maximized"]
        elif action == "restore":
            verified = not after["minimized"] and not after["maximized"]
        elif action in {"move", "resize", "set_bounds"}:
            expected = {
                "x": int(args.get("x", before["rect"][0] if before["rect"] else 0)),
                "y": int(args.get("y", before["rect"][1] if before["rect"] else 0)),
                "width": int(args.get("width", (before["rect"][2] - before["rect"][0]) if before["rect"] else 0)),
                "height": int(args.get("height", (before["rect"][3] - before["rect"][1]) if before["rect"] else 0)),
            }
            if after["rect"]:
                actual = {
                    "x": after["rect"][0],
                    "y": after["rect"][1],
                    "width": after["rect"][2] - after["rect"][0],
                    "height": after["rect"][3] - after["rect"][1],
                }
                verified = all(abs(actual[key] - expected[key]) <= 12 for key in expected)
        return json.dumps(
            {
                "action": action,
                "mechanism": mechanism,
                "window": before,
                "result": after,
                "verified": verified,
                "note": (
                    "Pencere durumu işlem sonrası yeniden okunarak doğrulandı."
                    if verified
                    else "Pencere komutu gönderildi ancak durum doğrulanamadı; yeni list_windows ile kontrol et."
                ),
            },
            ensure_ascii=False,
        )
