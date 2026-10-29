"""Jarvis'in güvenli, çok adımlı masaüstü eylem katmanı.

Model yalnızca bir eylem planı önerebilir; dosya sistemi veya uygulama üzerinde
işlem yapma yetkisi bu modüldeki doğrulanmış araçlarla sınırlıdır. Bu yaklaşım,
bir sohbet modelinin "yaptım" demesi ile işlemin gerçekten yapılması arasındaki
farkı kapatır.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote_plus, urlparse

from app.core.safety import (
    ActionForbidden,
    PlanAssessment,
    Risk,
    SafetyError,
    SafetyServices,
    build_services,
    classify_action,
)
from app.core.settings import Settings
from app.core.project_tools import ProjectTools
from app.core.desktop import CapabilityTools, DesktopTools
from app.core.map_tools import MapTools
from app.core.book_tools import BookTools
from app.core.ocr_tools import ScreenOCR, grab_region
from app.core.github_tools import shared_github_tools
from app.core.home_tools import FOAMS, SUGARS, TONES, SmartHome
from app.core.hud_data import WeatherService
from app.core.market_tools import shared_market_service
from app.core.media_tools import shared_youtube_service, youtube_target
from app.core.memory_tools import MemoryTools
from app.core.news_tools import shared_news_service
from app.core.photo_tools import shared_photo_service
from app.core.personal_tools import (
    PersonalTools,
    SystemControls,
    clear_clipboard,
    read_clipboard_text,
    write_clipboard_text,
)
from app.core.skill_library import SkillLibrary
from app.core.steam_tools import SteamWorkshop
from app.core.weather_tools import format_weather
from app.core.web_tools import BrowserReader, DownloadTools, WebReader
from app.core.wiki_tools import shared_wiki_service
from app.core.screen_agent import ScreenAgent, UIA_RICH_ACTIONS, apply_control_action
from app.core.window_tools import WindowTools


@dataclass
class PlannedAction:
    action_type: str
    arguments: dict[str, Any]
    description: str
    risk: str = "routine"


@dataclass
class ActionPlan:
    actions: list[PlannedAction] = field(default_factory=list)
    source: str = "deterministic"
    reply: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.actions

    @property
    def requires_confirmation(self) -> bool:
        return any(action.risk != "routine" for action in self.actions)

    def summary(self) -> str:
        return "\n".join(
            f"{index}. {action.description}"
            for index, action in enumerate(self.actions, start=1)
        )


class ActionPlanner:
    """Türkçe komutları belirlenebilir araç planlarına çevirir."""

    NUMBER_WORDS = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5}

    def __init__(self) -> None:
        self.desktop = Path.home() / "Desktop"

    @staticmethod
    def _clean_name(value: str) -> str:
        value = value.strip().strip(" \t\n\r\"'“”")
        value = re.sub(r"\s+", " ", value)
        return value.strip(" .")

    @staticmethod
    def _with_text_extension(name: str) -> str:
        return name if Path(name).suffix else f"{name}.txt"

    def _desktop_path(self, *parts: str) -> Path:
        return self.desktop.joinpath(*parts)

    def _count(self, raw: Optional[str]) -> int:
        if not raw:
            return 1
        raw = raw.lower().strip()
        if raw.isdigit():
            return min(max(int(raw), 1), 100)
        return self.NUMBER_WORDS.get(raw, 1)

    def plan(self, prompt: str) -> Optional[ActionPlan]:
        text = prompt.strip()
        lower = text.lower()
        if not text:
            return None

        if re.fullmatch(
            r"(?:(?:son\s+)?(?:işlemi|değişikliği)|yaptığını|yaptığın\s+şeyi)?\s*geri\s+al[.!]*",
            lower,
        ) or lower in {"son işlemi geri al", "geri al"}:
            return ActionPlan([PlannedAction("undo_last", {}, "Son değişikliği geri al")])

        complex_plan = self._plan_folder_and_document(text)
        if complex_plan:
            return complex_plan

        open_note = self._plan_notepad_text(text)
        if open_note:
            return open_note

        folder_match = re.search(
            r"(?:^|\b)(?P<name>[\wçğıöşüÇĞİÖŞÜ _.-]+?)\s+ad(?:ı|i)nda\s+"
            r"(?:bir\s+)?(?:klas[oö]r|dizin|folder)\s+(?:oluştur|yarat|aç)",
            text,
            re.IGNORECASE,
        )
        if folder_match:
            name = self._clean_name(folder_match.group("name"))
            if name:
                path = self._desktop_path(name)
                return ActionPlan([
                    PlannedAction("create_folder", {"path": str(path)}, f"Masaüstünde '{name}' klasörünü oluştur")
                ])

        file_match = re.search(
            r"(?:^|\b)(?P<name>[\wçğıöşüÇĞİÖŞÜ _.-]+?)\s+ad(?:ı|i)nda\s+"
            r"(?:bir\s+)?(?:metin belgesi|txt dosyası|dosya)\s+(?:oluştur|yarat)",
            text,
            re.IGNORECASE,
        )
        if file_match or re.search(r"(?:metin belgesi|txt dosyası)\s+(?:oluştur|yarat)", lower):
            raw_name = self._clean_name(file_match.group("name")) if file_match else "yeni_dosya"
            name = self._with_text_extension(raw_name or "yeni_dosya")
            path = self._desktop_path(name)
            return ActionPlan([
                PlannedAction(
                    "write_text_file",
                    {"path": str(path), "content": "", "overwrite": False},
                    f"Masaüstünde '{name}' adlı boş metin belgesini oluştur",
                )
            ])

        search_match = re.search(r"(?:google(?:'da)?\s+ara|internette\s+ara)\s+(?P<query>.+)", text, re.I)
        if search_match:
            query = self._clean_name(search_match.group("query"))
            if query:
                return ActionPlan([
                    PlannedAction("search_web", {"query": query}, f"Google'da '{query}' ara")
                ])

        app_map = {
            "not defteri": "notepad",
            "notepad": "notepad",
            "hesap makinesi": "calc",
            "calculator": "calc",
            "dosya gezgini": "explorer",
            "explorer": "explorer",
            "vs code": "code",
            "vscode": "code",
            "chrome": "chrome",
        }
        for spoken_name, command in app_map.items():
            if spoken_name in lower and re.search(r"\b(?:aç|başlat)\b", lower):
                return ActionPlan([
                    PlannedAction("open_application", {"application": command}, f"{spoken_name.title()} uygulamasını aç")
                ])

        delete_match = re.search(r"(?:dosya|klas[oö]r)\s+['\"“”]?(?P<name>[^'\"“”]+?)['\"“”]?\s+(?:sil|kaldır)", text, re.I)
        if delete_match:
            name = self._clean_name(delete_match.group("name"))
            if name:
                return ActionPlan([
                    PlannedAction(
                        "delete_path",
                        {"path": str(self._desktop_path(name))},
                        f"Masaüstündeki '{name}' öğesini Geri Dönüşüm Kutusuna taşı",
                        "destructive",
                    )
                ])
        return None

    def _plan_folder_and_document(self, text: str) -> Optional[ActionPlan]:
        """Bir cümlede klasör, dosya ve dosya içeriği istenmesini çözer."""
        folder_match = re.search(
            r"(?P<folder>[\wçğıöşüÇĞİÖŞÜ _.-]+?)\s+ad(?:ı|i)nda\s+"
            r"(?:bir\s+)?(?:klas[oö]r|dizin|folder)\s+(?:oluştur|yarat)",
            text,
            re.IGNORECASE,
        )
        if not folder_match:
            return None
        folder = self._clean_name(folder_match.group("folder"))
        if not folder:
            return None

        folder_path = self._desktop_path(folder)
        actions = [
            PlannedAction("create_folder", {"path": str(folder_path)}, f"Masaüstünde '{folder}' klasörünü oluştur")
        ]
        file_match = re.search(
            r"(?:içine|içerisinde)\s*[\"“']?(?P<file>[\wçğıöşüÇĞİÖŞÜ _.-]+?)[\"”']?\s+"
            r"ad(?:ı|i)nda\s+(?:bir\s+)?(?:metin belgesi|txt dosyası|dosya)\s+(?:oluştur|yarat)",
            text,
            re.IGNORECASE,
        )
        if not file_match:
            return ActionPlan(actions)

        file_name = self._with_text_extension(self._clean_name(file_match.group("file")) or "yeni_dosya")
        content_match = re.search(
            r"(?:belgenin\s+)?içeri(?:ğ|g)i(?:nde)?\s+(?:(?P<count>\d+|bir|iki|üç|dört|beş)\s*"
            r"(?:kere|kez)\s*)?[\"“'](?P<content>[^\"”']+)[\"”']\s*(?:yaz|ekle)",
            text,
            re.IGNORECASE,
        )
        content = ""
        if content_match:
            repetitions = self._count(content_match.group("count"))
            content = "\n".join([content_match.group("content").strip()] * repetitions)

        actions.append(
            PlannedAction(
                "write_text_file",
                {"path": str(folder_path / file_name), "content": content, "overwrite": False},
                f"'{folder}' klasörüne '{file_name}' dosyasını oluştur ve istenen metni yaz",
            )
        )
        return ActionPlan(actions)

    def _plan_notepad_text(self, text: str) -> Optional[ActionPlan]:
        if not re.search(r"(?:not defteri|notepad).*?(?:aç|başlat)", text, re.I):
            return None
        content_match = re.search(r"(?:içine\s+)?[\"“'](?P<content>[^\"”']+)[\"”']\s*(?:yaz|ekle)", text, re.I)
        if not content_match:
            return None
        path = self._desktop_path("Jarvis_Notu.txt")
        content = content_match.group("content").strip()
        return ActionPlan([
            PlannedAction("write_text_file", {"path": str(path), "content": content, "overwrite": True}, "Masaüstünde Jarvis_Notu.txt dosyasını hazırla"),
            PlannedAction("open_path", {"path": str(path)}, "Hazırlanan notu Not Defteri ile aç"),
        ])


class ApplicationLauncher:
    SYSTEM_APPS = {
        "notepad": "notepad.exe",
        "not defteri": "notepad.exe",
        "calc": "calc.exe",
        "hesap makinesi": "calc.exe",
        "explorer": "explorer.exe",
        "dosya gezgini": "explorer.exe",
    }
    IGNORED_SHORTCUT_WORDS = ("uninstall", "kaldir", "remove", "yardim", "help", "readme")
    GENERIC_WORDS = {
        "windows", "microsoft", "google", "the", "of", "and", "ve",
        "app", "application", "client", "launcher", "game", "games",
        "oyun", "uygulama", "desktop",
    }
    PLAY_LABELS = {
        "play", "oyna", "play now", "oyna simdi", "launch", "launch game",
        "start game", "oyunu oyna", "oyunu baslat",
    }

    EXE_SKIP_WORDS = (
        "unins", "uninstall", "update", "updater", "setup", "installer", "crash",
        "report", "helper", "service", "test", "benchmark", "diagnostic", "mux",
        "inject", "page",
    )

    def __init__(
        self,
        start_menu_dirs: Optional[list[Path]] = None,
        winget_root: Optional[Path] = None,
    ) -> None:
        self.start_menu_dirs = start_menu_dirs or [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        self.winget_root = (
            Path(winget_root)
            if winget_root is not None
            else Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        )

    @staticmethod
    def normalize(value: str) -> str:
        value = unicodedata.normalize("NFKD", value.casefold().replace("ı", "i"))
        return " ".join("".join(char for char in value if not unicodedata.combining(char)).replace("-", " ").split())

    def _score(self, query: str, shortcut: Path) -> float:
        name = self.normalize(shortcut.stem)
        if query == name:
            return 1.0
        if name.startswith(query) or query.startswith(name):
            return 0.9
        query_tokens = query.split()
        name_tokens = name.split()
        ratio = difflib.SequenceMatcher(None, query, name).ratio()
        # İlk anlamlı kelime eşleşmiyorsa benzer son ek yeterli değildir:
        # "obs studio" sorgusu "Roblox Studio" kısayoluna kaymamalı.
        if query_tokens and query_tokens[0] not in name_tokens:
            if len(query_tokens) == 1 and ratio >= 0.85:
                return ratio
            return 0.0
        if query_tokens and all(token in name_tokens for token in query_tokens):
            return 0.85
        return ratio

    def _find_shortcut(self, application: str) -> Optional[Path]:
        query = self.normalize(application)
        matches = []
        for directory in self.start_menu_dirs:
            if not directory.is_dir():
                continue
            for shortcut in directory.rglob("*.lnk"):
                name = self.normalize(shortcut.stem)
                if any(word in name.split() for word in self.IGNORED_SHORTCUT_WORDS):
                    continue
                score = self._score(query, shortcut)
                if score >= 0.55:
                    matches.append((score, shortcut))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        if matches[0][0] < 1.0 and len(matches) > 1 and abs(matches[0][0] - matches[1][0]) < 0.03:
            names = ", ".join(item[1].stem for item in matches[:4])
            raise ActionForbidden(f"Uygulama adı belirsiz: {application}. Olası eşleşmeler: {names}")
        return matches[0][1]

    def _find_registered_app(self, application: str) -> Optional[tuple[str, str]]:
        try:
            import winreg
        except ImportError:
            return None
        query = self.normalize(application)
        matches = []
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for prefix in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"):
                try:
                    with winreg.OpenKey(hive, prefix) as root:
                        for index in range(winreg.QueryInfoKey(root)[0]):
                            key_name = winreg.EnumKey(root, index)
                            with winreg.OpenKey(root, key_name) as key:
                                executable = str(winreg.QueryValueEx(key, "")[0]).strip('"')
                            score = self._score(query, Path(key_name))
                            if score >= 0.55 and Path(executable).is_file():
                                matches.append((score, executable, Path(key_name).stem))
                except OSError:
                    continue
        if not matches:
            return None
        matches.sort(reverse=True)
        _, executable, name = matches[0]
        return executable, name

    def _find_store_app(self, application: str) -> Optional[tuple[str, str]]:
        command = "Get-StartApps | ConvertTo-Json -Compress"
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
            )
            data = json.loads(result.stdout or "[]")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
        if isinstance(data, dict):
            data = [data]
        query = self.normalize(application)
        matches = []
        for item in data:
            name = str(item.get("Name", ""))
            score = self._score(query, Path(name))
            if score >= 0.55:
                matches.append((score, str(item.get("AppID", "")), name))
        if not matches:
            return None
        matches.sort(reverse=True)
        _, app_id, name = matches[0]
        return (app_id, name) if app_id else None

    @staticmethod
    def _package_product(package: Path) -> str:
        identifier = package.name.split("_", 1)[0]
        return ApplicationLauncher.normalize(identifier.rsplit(".", 1)[-1])

    def _winget_executable(
        self, package: Path, compact_query: str, tokens: list[str]
    ) -> Optional[str]:
        candidates = []
        seen = set()
        for pattern in ("*.exe", "*/*.exe", "bin/**/*.exe", "**/bin/*.exe"):
            try:
                for path in package.glob(pattern):
                    if path in seen:
                        continue
                    seen.add(path)
                    if len(seen) > 250:
                        break
                    stem = self.normalize(path.stem).replace(" ", "")
                    if not stem or any(word in stem for word in self.EXE_SKIP_WORDS):
                        continue
                    if stem == compact_query:
                        score = 100.0
                    elif stem.startswith(compact_query):
                        score = 90.0
                    elif compact_query in stem:
                        score = 80.0
                    elif any(stem.startswith(token) for token in tokens):
                        score = 60.0 - min(len(stem), 20)
                    else:
                        continue
                    candidates.append((score, len(path.name), str(path)))
            except OSError:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        return candidates[0][2]

    def _find_winget_app(self, application: str) -> Optional[tuple[str, str]]:
        root = self.winget_root
        try:
            if not root.is_dir():
                return None
            packages = [path for path in root.iterdir() if path.is_dir()]
        except OSError:
            return None
        query = self.normalize(application)
        compact = query.replace(" ", "")
        if len(compact) < 3:
            return None
        tokens = self._app_tokens(application) or query.split()
        matches = []
        for package in packages:
            product = self._package_product(package)
            if not product:
                continue
            if not (product == compact or product.startswith(compact) or compact in product):
                # Kullanıcı ürün adı yerine exe adını söyleyebilir (ör. "obs64");
                # yalnızca aynı başlangıçla eşleşen paketleri tara.
                if len(compact) < 3 or not product.startswith(compact[:3]):
                    continue
            executable = self._winget_executable(package, compact, tokens)
            if executable is None:
                continue
            try:
                modified = package.stat().st_mtime_ns
            except OSError:
                modified = 0
            matches.append((modified, product, executable))
        if not matches:
            return None
        matches.sort(reverse=True)
        _, name, executable = matches[0]
        return executable, name

    @staticmethod
    def _shortcut_target(shortcut: Path) -> Optional[str]:
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            return str(shell.CreateShortcut(str(shortcut)).TargetPath) or None
        except Exception:
            return None

    @staticmethod
    def _validate_application(application: str) -> str:
        application = application.strip()
        if not application or any(char in application for char in "\x00\r\n"):
            raise ActionForbidden("Geçerli bir uygulama adı gerekli.")
        if application.casefold().endswith(".lnk") or "/" in application or "\\" in application:
            raise ActionForbidden("Uygulama yolu yerine yalnızca uygulama adı kullanılmalı.")
        return application

    @classmethod
    def _tokens(cls, text: str) -> list[str]:
        return [token for token in re.split(r"[\W_]+", cls.normalize(text)) if token]

    @classmethod
    def _app_tokens(cls, application: str) -> list[str]:
        tokens: list[str] = []
        for token in cls._tokens(application):
            if len(token) < 2 or token in cls.GENERIC_WORDS or token in tokens:
                continue
            tokens.append(token)
        return tokens

    @classmethod
    def _window_matches(cls, application: str, title: str) -> bool:
        """Pencere başlığının istenen uygulamayı gerçekten temsil edip etmediğini ölçer."""
        tokens = cls._app_tokens(application)
        if not tokens:
            normalized = cls.normalize(application)
            return bool(normalized) and normalized in cls.normalize(title)
        title_tokens = set(cls._tokens(title))
        matched = [token for token in tokens if token in title_tokens]
        if len(matched) == len(tokens):
            return True
        needed = max(1, (len(tokens) + 1) // 2)
        return tokens[0] in matched and len(matched) >= needed

    @classmethod
    def _is_play_label(cls, text: str) -> bool:
        key = " ".join(cls._tokens(text))
        if not key:
            return False
        if key in cls.PLAY_LABELS:
            return True
        return len(key) <= 20 and key.startswith(("play ", "oyna ", "oyunu "))

    @classmethod
    def _find_play_control(cls, window):
        try:
            controls = window.descendants()
        except Exception:
            return None
        for control in controls:
            try:
                text = control.window_text().strip()
                if not text or not control.is_visible() or not control.is_enabled():
                    continue
            except Exception:
                continue
            if cls._is_play_label(text):
                return control
        return None

    @classmethod
    def _click_play(cls, window) -> str:
        control = cls._find_play_control(window)
        if control is not None:
            label = control.window_text().strip()
            try:
                control.invoke()
                return label
            except Exception:
                try:
                    control.click_input()
                    return label
                except Exception:
                    pass
        return cls._click_play_ocr(window)

    @classmethod
    def _click_play_ocr(cls, window) -> str:
        """UIA düğme sunmayan istemcilerde (ör. Riot Client) Oyna/Play metnini OCR ile bulur."""
        if not ScreenOCR.available():
            return ""
        if not WindowsUI.focus(window):
            return ""
        try:
            rectangle = window.rectangle()
            path, origin, _ = grab_region((rectangle.left, rectangle.top, rectangle.right, rectangle.bottom))
        except Exception:
            return ""
        try:
            data = ScreenOCR().read_lines(path, origin, limit=200)
        except Exception:
            return ""
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        candidates = []
        for line in data.get("lines") or []:
            text = str(line.get("text", "")).strip()
            if not cls._is_play_label(text):
                continue
            key = " ".join(cls._tokens(text))
            candidates.append((0 if key in cls.PLAY_LABELS else 1, len(key), line))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: (item[0], item[1], item[2].get("center_y", 0)))
        chosen = candidates[0][2]
        try:
            from pywinauto import mouse

            mouse.click(coords=(int(chosen["center_x"]), int(chosen["center_y"])))
        except Exception:
            return ""
        return str(chosen.get("text", "")).strip()

    @staticmethod
    def _process_names() -> list[str]:
        try:
            import psutil

            return [str(process.info.get("name") or "") for process in psutil.process_iter(["name"])]
        except Exception:
            return []

    @classmethod
    def _matching_processes(cls, application: str) -> list[str]:
        tokens = cls._app_tokens(application)
        if not tokens:
            return []
        matches = []
        for name in cls._process_names():
            stem = "".join(cls._tokens(Path(name).stem))
            if stem and any(stem.startswith(token) for token in tokens):
                matches.append(name)
        return matches

    @staticmethod
    def _window_for_process(process_hint: str) -> Optional[str]:
        hint = ApplicationLauncher.normalize(process_hint)
        if not hint:
            return None
        try:
            import psutil
            from pywinauto import Desktop
        except ImportError:
            return None
        try:
            for window in Desktop(backend="uia").windows():
                try:
                    title = window.window_text().strip()
                    if not title or not window.is_visible():
                        continue
                    name = psutil.Process(window.process_id()).name()
                except Exception:
                    continue
                if hint in ApplicationLauncher.normalize(Path(name).stem):
                    return title
        except Exception:
            return None
        return None

    def _wait_for_target(
        self,
        application: str,
        timeout: float,
        should_stop: Optional[Callable[[], bool]] = None,
        processes: bool = True,
    ) -> str:
        deadline = time.monotonic() + timeout
        checks = 0
        while time.monotonic() < deadline:
            if should_stop and should_stop():
                raise ActionForbidden("İşlem kullanıcı tarafından durduruldu.")
            for title in self._visible_windows():
                if self._window_matches(application, title):
                    return title
            if processes and checks % 3 == 0:
                matches = self._matching_processes(application)
                if matches:
                    return f"{application} (süreç: {matches[0]})"
            checks += 1
            time.sleep(0.8)
        return ""

    def _start_from_launcher(
        self,
        application: str,
        launcher_title: str,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> tuple[str, str]:
        """İstemcideki Oyna/Play düğmesini tıklar ve gerçek oyun penceresini/sürecini bekler."""
        try:
            window = WindowsUI._window(launcher_title)
        except ActionForbidden:
            return "", f"{launcher_title} penceresi artık bulunamıyor"
        if not WindowsUI.focus(window):
            return "", f"{launcher_title} penceresi öne getirilemedi"
        last_label = ""
        for timeout in (45.0, 15.0):
            label = self._click_play(window)
            if not label:
                if last_label:
                    break
                return "", f"{launcher_title} içinde Oyna/Play düğmesi bulunamadı"
            last_label = label
            target = self._wait_for_target(application, timeout, should_stop=should_stop)
            if target:
                return (
                    f"{target} ({launcher_title} açıldı, '{label}' düğmesine tıklandı ve sonuç doğrulandı)",
                    "",
                )
            try:
                window = WindowsUI._window(launcher_title)
            except ActionForbidden:
                break
        final = self._wait_for_target(application, 5.0, should_stop=should_stop)
        if final:
            return (
                f"{final} ({launcher_title} açıldı, '{last_label}' düğmesine tıklandı ve sonuç doğrulandı)",
                "",
            )
        return "", f"'{last_label}' düğmesine tıklandı ancak {application} penceresi veya süreci açılmadı"

    def _settle_window(
        self,
        application: str,
        title: str,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> str:
        if self._window_matches(application, title):
            return title
        grace = self._wait_for_target(application, 4.0, should_stop=should_stop, processes=False)
        if grace:
            return f"{grace} ({title} açıldıktan sonra doğrulandı)"
        verified, reason = self._start_from_launcher(application, title, should_stop=should_stop)
        if verified:
            return verified
        running = self._matching_processes(application)
        if running:
            return f"{application} (süreç çalışıyor: {running[0]}; görünür pencere doğrulanamadı)"
        raise ActionForbidden(
            f"{reason}. Kullanıcıya sormadan inspect_ui veya find_text_on_screen ile "
            "'Oyna'/'Play' düğmesini tıklamayı ve oyun penceresini/sürecini doğrulamayı dene."
        )

    @staticmethod
    def _focus_running(application: str) -> Optional[str]:
        try:
            from pywinauto import Desktop
            needle = application.casefold()
            for window in Desktop(backend="uia").windows():
                title = window.window_text().strip()
                if not title or needle not in title.casefold() or not window.is_visible():
                    continue
                try:
                    if window.is_minimized():
                        window.restore()
                    window.set_focus()
                except Exception:
                    continue
                return title
        except Exception:
            return None
        return None

    @staticmethod
    def _visible_windows() -> dict[str, int]:
        try:
            from pywinauto import Desktop
            ignored = {"Worker Window", "Shell_TrayWnd", "Progman", "CEF-OSC-WIDGET"}
            windows: Dict[str, int] = {}
            for window in Desktop(backend="uia").windows():
                title = window.window_text().strip()
                try:
                    if not title or not window.is_visible() or window.class_name() in ignored:
                        continue
                    rectangle = window.rectangle()
                except Exception:
                    continue
                area = max(0, rectangle.right - rectangle.left) * max(0, rectangle.bottom - rectangle.top)
                windows[title] = max(windows.get(title, 0), area)
            return windows
        except Exception:
            return {}

    def _wait_for_new_window(self, known: dict[str, int], timeout: float = 12.0) -> Optional[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self._visible_windows()
            new = sorted(
                ((area, title) for title, area in current.items() if title not in known),
                reverse=True,
            )
            if new:
                return new[0][1]
            time.sleep(0.3)
        return None

    def _finish_launch(
        self,
        known: dict[str, int],
        application: str,
        process_hint: str = "",
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> str:
        title = self._wait_for_new_window(known)
        if not title and process_hint:
            title = self._window_for_process(process_hint)
        if title:
            return self._settle_window(application, title, should_stop=should_stop)
        running = self._focus_running(application)
        if running:
            return running
        if self._matching_processes(application):
            return f"{application} (çalışan süreç doğrulandı)"
        raise ActionForbidden(f"Uygulama başlatıldı ama penceresi açılmadı: {application}")

    def launch(self, application: str, should_stop: Optional[Callable[[], bool]] = None) -> str:
        application = self._validate_application(application)
        running = self._focus_running(application)
        if running:
            return running
        known = self._visible_windows()
        system_command = self.SYSTEM_APPS.get(application.casefold())
        if system_command:
            subprocess.Popen([system_command])
            return self._finish_launch(
                known, application, process_hint=Path(system_command).stem, should_stop=should_stop
            )
        errors: list[str] = []
        shortcut = self._find_shortcut(application)
        if shortcut:
            target = self._shortcut_target(shortcut)
            hint = Path(target).stem if target else shortcut.stem
            try:
                os.startfile(str(shortcut))
            except OSError as error:
                errors.append(f"kısayol açılamadı: {error}")
            else:
                return self._finish_launch(
                    known, application, process_hint=hint, should_stop=should_stop
                )
        registered = self._find_registered_app(application)
        if registered:
            executable, name = registered
            try:
                subprocess.Popen([executable], cwd=str(Path(executable).parent))
            except OSError as error:
                errors.append(f"kayıtlı uygulama çalıştırılamadı: {error}")
            else:
                return self._finish_launch(
                    known, application, process_hint=Path(executable).stem, should_stop=should_stop
                )
        store_app = self._find_store_app(application)
        if store_app:
            app_id, name = store_app
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
            return self._finish_launch(known, application, should_stop=should_stop)
        winget_app = self._find_winget_app(application)
        if winget_app:
            executable, name = winget_app
            try:
                subprocess.Popen([executable], cwd=str(Path(executable).parent))
            except OSError as error:
                errors.append(f"winget uygulaması çalıştırılamadı: {error}")
            else:
                return self._finish_launch(
                    known, application, process_hint=Path(executable).stem, should_stop=should_stop
                )
        detail = f" Ayrıntı: {'; '.join(errors)}." if errors else ""
        raise ActionForbidden(
            f"'{application}' adıyla eşleşen bir kısayol veya kayıt bulunamadı.{detail} "
            "Bu sonuç uygulamanın kurulu olmadığını kanıtlamaz; tam adla tekrar dene, "
            "check_capabilities veya install_dependency ile kurulum durumunu doğrula. "
            "Kullanıcıya 'kurulu değil' demeden önce alternatif keşif yöntemlerini uygula."
        )

    def open_url(self, application: str, url: str) -> str:
        application = self._validate_application(application)
        if urlparse(url).scheme not in {"http", "https"}:
            raise ActionForbidden("Yalnızca http/https adresleri açılabilir.")
        shortcut = self._find_shortcut(application)
        executable = self._shortcut_target(shortcut) if shortcut else None
        name = shortcut.stem if shortcut else application
        if not executable:
            registered = self._find_registered_app(application)
            if registered:
                executable, name = registered
        if not executable or not Path(executable).is_file():
            raise ActionForbidden(f"Bu uygulamanın çalıştırılabilir yolu bulunamadı: {application}")
        subprocess.Popen([executable, url])
        return f"{name}: {url}"

    @staticmethod
    def _validate_system_uri(uri: str) -> str:
        uri = uri.strip()
        if not uri or len(uri) > 2000 or any(char in uri for char in "\x00\r\n\t"):
            raise ActionForbidden("Geçerli ve tek satırlık bir uygulama bağlantısı gerekli.")
        scheme = urlparse(uri).scheme.casefold()
        if scheme != "steam":
            raise ActionForbidden("Yalnızca steam:// bağlantıları açılabilir.")
        if uri.casefold().startswith("steam://openurl/"):
            target = uri[len("steam://openurl/"):]
            if urlparse(target).scheme.casefold() not in {"http", "https"}:
                raise ActionForbidden("steam://openurl yalnızca http/https adresi açabilir.")
        return uri

    def open_system_uri(self, uri: str) -> str:
        uri = self._validate_system_uri(uri)
        os.startfile(uri)
        return uri


class WindowsUI:
    @staticmethod
    def _window(title: str):
        from pywinauto import Desktop
        windows = [window for window in Desktop(backend="uia").windows() if window.window_text().strip()]
        exact = [window for window in windows if window.window_text().casefold() == title.casefold()]
        matches = exact or [window for window in windows if title.casefold() in window.window_text().casefold()]
        if not matches:
            raise ActionForbidden(f"Açık pencere bulunamadı: {title}")
        if len(matches) > 1:
            names = ", ".join(window.window_text() for window in matches[:4])
            raise ActionForbidden(f"Pencere adı belirsiz: {names}")
        return matches[0]

    @staticmethod
    def focus(window) -> bool:
        """Pencereyi öne getirir ve gerçekten etkin olduğunu doğrular.

        Fare/klavye göndermeden önce bu doğrulama şart: aksi halde odaklanmamış
        bir pencerede koordinatlar öndeki başka uygulamaya gidebilir.
        """
        try:
            if window.is_minimized():
                window.restore()
            window.set_focus()
        except Exception:
            pass
        time.sleep(0.25)
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            user32.BringWindowToTop.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]

            hwnd = int(window.handle)
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                return True
            foreground = int(user32.GetForegroundWindow() or 0)
            process_id = wintypes.DWORD()
            target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            foreground_thread = user32.GetWindowThreadProcessId(foreground, ctypes.byref(process_id))
            if target_thread and foreground_thread and target_thread != foreground_thread:
                user32.AttachThreadInput(foreground_thread, target_thread, True)
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(foreground_thread, target_thread, False)
            else:
                user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            return int(user32.GetForegroundWindow() or 0) == hwnd
        except Exception:
            return False

    def inspect(self, window_title: str, limit: int = 100, name_filter: str = "") -> str:
        window = self._window(window_title)
        try:
            if window.is_minimized():
                window.restore()
            window.set_focus()
        except Exception:
            pass
        interactive = {
            "Button", "ListItem", "SplitButton", "TabItem", "MenuItem", "Edit",
            "ComboBox", "CheckBox", "RadioButton", "Slider", "TreeItem",
        }
        rows = []
        seen = set()
        for control in window.descendants():
            name = control.window_text().strip()
            if not name or (name_filter and name_filter.casefold() not in name.casefold()):
                continue
            try:
                rectangle = control.rectangle()
                center_x = (rectangle.left + rectangle.right) // 2
                center_y = (rectangle.top + rectangle.bottom) // 2
            except Exception:
                continue
            row = {
                "name": name[:200],
                "type": control.friendly_class_name(),
                "center_x": center_x,
                "center_y": center_y,
            }
            key = (row["name"].casefold(), row["type"], center_x, center_y)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        rows.sort(key=lambda row: row["type"] not in interactive)
        rows = rows[:min(max(limit, 1), 200)]
        window_rectangle = window.rectangle()
        window_area = max(1, (window_rectangle.right - window_rectangle.left) * (window_rectangle.bottom - window_rectangle.top))
        content_rect = None
        best_area = 0
        for control in window.descendants():
            if control.window_text().strip() or control.friendly_class_name() != "Pane":
                continue
            try:
                rectangle = control.rectangle()
            except Exception:
                continue
            area = max(0, rectangle.right - rectangle.left) * max(0, rectangle.bottom - rectangle.top)
            if area > best_area and area >= window_area * 0.2:
                best_area = area
                content_rect = [rectangle.left, rectangle.top, rectangle.right, rectangle.bottom]
        details = {
            "window": window.window_text(),
            "window_rect": [window_rectangle.left, window_rectangle.top, window_rectangle.right, window_rectangle.bottom],
        }
        note = "Tıklama için center_x/center_y gerçek ekran koordinatlarıdır; etkileşimli öğeler önce listelenir."
        if content_rect:
            details["content_rect"] = content_rect
            note += " content_rect büyük içerik/çizim alanıdır; çizimi onun içinde yap."
        if not rows:
            details["uia_empty"] = True
            note += (
                " Bu pencere UIA denetimleri sunmuyor (Steam, tarayıcı veya oyun olabilir)."
                " Metin düğmeler için find_text_on_screen kullan: window_title ve text ver, dönen"
                " center_x/center_y tam koordinattır; click=true ile güvenli biçimde tıklatabilirsin."
                " Görsel hedefler için capture_screen + mouse_control kullan; her tıklamadan sonra"
                " yeniden capture_screen ile sonucu doğrula."
            )
        details["note"] = note
        details["controls"] = rows
        return json.dumps(details, ensure_ascii=False)

    def find_text(self, args: dict) -> str:
        query = str(args.get("text", "")).strip()
        if not query:
            raise ActionForbidden("OCR için aranacak metin gerekli.")
        click = bool(args.get("click", False))
        title = str(args.get("window_title", "")).strip()
        button = str(args.get("button", "left")).strip().casefold()
        if button not in {"left", "right", "middle"}:
            raise ActionForbidden("OCR tıklaması için button left/right/middle olmalı.")
        rectangle = None
        if title:
            window = self._window(title)
            if not self.focus(window):
                raise ActionForbidden(
                    "Pencere öne getirilemedi; yanlış uygulamaya tıklamayı önlemek için OCR işlemi durduruldu."
                )
            bounds = window.rectangle()
            rectangle = (bounds.left, bounds.top, bounds.right, bounds.bottom)
        elif click:
            raise ActionForbidden("OCR ile tıklamak için window_title zorunlu.")
        if not ScreenOCR.available():
            raise ActionForbidden(
                "Windows OCR bileşeni kurulu değil. install_dependency ile winrt-Windows.Media.Ocr ve "
                "winrt-Windows.Storage paketlerini kur."
            )
        path, origin, size = grab_region(rectangle)
        try:
            result = ScreenOCR().find(path, query, origin, str(args.get("language", "")))
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        result["image_size"] = list(size)
        matches = result.get("matches") or []
        try:
            index = int(args.get("index", 0) or 0)
        except (TypeError, ValueError):
            index = 0
        if index < 0 or (matches and index >= len(matches)):
            raise ActionForbidden(f"OCR eşleşme sırası geçersiz: {index} (bulunan: {len(matches)})")
        selected = matches[index] if matches else None
        result["selected_index"] = index if selected else None
        if click:
            if not selected:
                result["clicked"] = False
                return json.dumps(result, ensure_ascii=False)
            from pywinauto import mouse

            mouse.click(button=button, coords=(selected["center_x"], selected["center_y"]))
            result["clicked"] = {
                "x": selected["center_x"], "y": selected["center_y"], "text": selected["text"],
            }
            result["note"] += " Tıklama uygulandı; capture_screen ile sonucu doğrula."
        return json.dumps(result, ensure_ascii=False)

    def act(self, window_title: str, control_name: str, action: str, text: str = "", direction: str = "down", amount: int = 3) -> str:
        window = self._window(window_title)
        controls = [control for control in window.descendants() if control.window_text().strip()]
        exact = [control for control in controls if control.window_text().casefold() == control_name.casefold()]
        matches = exact or [control for control in controls if control_name.casefold() in control.window_text().casefold()]
        if not matches:
            raise ActionForbidden(f"Arayüz öğesi bulunamadı: {control_name}")
        if len(matches) > 1:
            names = ", ".join(control.window_text() for control in matches[:4])
            raise ActionForbidden(f"Arayüz öğesi belirsiz, daha tam ad kullan: {names}")
        control = matches[0]
        if not self.focus(window):
            raise ActionForbidden("Pencere öne getirilemedi; yanlış uygulamaya tıklamayı önlemek için işlem durduruldu.")
        key = action.strip().casefold()
        if key == "click":
            try:
                control.invoke()
            except Exception:
                control.click_input()
        elif key == "type":
            try:
                control.set_edit_text(text)
            except Exception:
                control.click_input()
                control.type_keys(text, with_spaces=True)
        elif key == "focus":
            control.set_focus()
        elif key in UIA_RICH_ACTIONS:
            mechanism, verified, verification = apply_control_action(
                control, key, text, direction, amount
            )
            return (
                f"{control.window_text()} üzerinde {key} uygulandı "
                f"({mechanism}, doğrulandı={verified}); {verification}"
            )
        else:
            raise ActionForbidden(f"Desteklenmeyen arayüz eylemi: {action}")
        return f"{control.window_text()} üzerinde {action} uygulandı"

    def keys(self, window_title: str, keys: str = "", text: str = "") -> str:
        from pywinauto.keyboard import send_keys
        window = self._window(window_title)
        if not self.focus(window):
            raise ActionForbidden("Pencere öne getirilemedi; klavye girişi gönderilmedi.")
        if keys:
            send_keys(keys, pause=0.08)
        if text:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            send_keys("^v", pause=0.08)
        if not keys and not text:
            raise ActionForbidden("Gönderilecek kısayol veya metin gerekli.")
        return "Klavye girişi uygulandı"

    SHAPE_ALIASES = {
        "rectangle": {"dikdörtgen", "rectangle"},
        "triangle": {"üçgen", "triangle"},
        "ellipse": {"oval", "ellipse"},
        "line": {"çizgi", "line"},
        "rounded_rectangle": {"yuvarlatılmış dikdörtgen", "rounded rectangle"},
        "pentagon": {"beşgen", "pentagon"},
    }

    @staticmethod
    def _find_shape_tool(window, aliases):
        for control in window.descendants():
            try:
                if control.friendly_class_name() != "ListItem":
                    continue
                name = control.window_text().strip()
            except Exception:
                continue
            if name.casefold() in aliases:
                return control
        return None

    def draw_shape(
        self,
        window_title: str,
        shape: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
    ) -> str:
        from pywinauto import mouse

        key = shape.strip().casefold().replace("-", "_").replace(" ", "_")
        aliases = self.SHAPE_ALIASES.get(key)
        if not aliases:
            raise ActionForbidden(f"Desteklenmeyen şekil: {shape}")
        window = self._window(window_title)
        if not self.focus(window):
            raise ActionForbidden("Pencere öne getirilemedi; yanlış uygulamaya çizmeyi önlemek için işlem durduruldu.")
        control = self._find_shape_tool(window, aliases)
        if control is None:
            for tab in window.descendants():
                try:
                    if tab.friendly_class_name() == "TabItem" and tab.window_text().strip().casefold() in {"giriş", "home"}:
                        tab.click_input()
                        time.sleep(0.3)
                        break
                except Exception:
                    continue
            control = self._find_shape_tool(window, aliases)
        if control is None:
            available = sorted({
                item.window_text().strip()
                for item in window.descendants()
                if item.friendly_class_name() == "ListItem" and item.window_text().strip()
            })
            raise ActionForbidden(
                f"Şekil aracı bulunamadı: {shape}. Mevcut şekiller: {', '.join(available[:20])}"
            )
        try:
            control.click_input()
        except Exception:
            control.invoke()
        time.sleep(0.2)
        mouse.press(coords=(start_x, start_y))
        steps = 24
        for index in range(1, steps + 1):
            mouse.move(coords=(
                start_x + (end_x - start_x) * index // steps,
                start_y + (end_y - start_y) * index // steps,
            ))
            time.sleep(0.012)
        mouse.release(coords=(end_x, end_y))
        time.sleep(0.25)
        return (
            f"✅ {control.window_text().strip()} çizildi: "
            f"({start_x}, {start_y}) → ({end_x}, {end_y})"
        )

    @staticmethod
    def _find_save_controls(window):
        filename = save = None
        for control in window.descendants():
            try:
                kind = control.friendly_class_name()
                automation_id = control.automation_id()
            except Exception:
                continue
            if kind == "Edit" and automation_id == "1001" and filename is None:
                filename = control
            elif kind == "Button" and automation_id == "1" and save is None:
                save = control
            if filename is not None and save is not None:
                break
        return filename, save

    @staticmethod
    def _click_affirmative(window) -> bool:
        for control in window.descendants():
            try:
                if control.friendly_class_name() != "Button":
                    continue
                name = control.window_text().strip().casefold()
            except Exception:
                continue
            if name in {"evet", "yes", "tamam", "ok"}:
                try:
                    control.invoke()
                except Exception:
                    control.click_input()
                return True
        return False

    def _wait_save_controls(self, window, seconds: float = 5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            filename, save = self._find_save_controls(window)
            if filename is not None and save is not None:
                return filename, save
            time.sleep(0.2)
        return None, None

    def save_file(self, window_title: str, path: Path) -> str:
        from pywinauto import Desktop
        from pywinauto.keyboard import send_keys
        path = Path(path)
        before = (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
        window = self._window(window_title) if window_title else Desktop(backend="uia").active_window()
        if not self.focus(window):
            raise ActionForbidden("Kaydedilecek pencere öne getirilemedi; Ctrl+S gönderilmedi.")
        send_keys("^s", pause=0.08)
        filename, save = self._wait_save_controls(window)
        if filename is None or save is None:
            after = (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
            if after is not None and after != before:
                return f"✅ Dosya güncellendi: {path} ({path.stat().st_size} bayt)"
            if after is not None and path.name.casefold() in window.window_text().casefold():
                return f"✅ Dosya kaydedilmiş: {path} ({path.stat().st_size} bayt)"
            # Ctrl+S mevcut belgeyi kaydetti; farklı bir hedef için Farklı Kaydet gerekir.
            send_keys("{F12}", pause=0.08)
            filename, save = self._wait_save_controls(window)
        if filename is None or save is None:
            after = (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
            if after is not None and after != before:
                return f"✅ Dosya güncellendi: {path} ({path.stat().st_size} bayt)"
            if after is not None:
                return f"✅ Dosya kaydedilmiş: {path} ({path.stat().st_size} bayt)"
            raise ActionForbidden("Standart kaydetme penceresi bulunamadı; dosya kaydedilmedi.")
        try:
            filename.set_edit_text(str(path))
        except Exception:
            filename.click_input()
            send_keys("^a", pause=0.05)
            filename.type_keys(str(path), with_spaces=True)
        try:
            save.invoke()
        except Exception:
            save.click_input()
        deadline = time.monotonic() + 6
        confirmed = False
        while time.monotonic() < deadline:
            if path.is_file():
                return f"✅ Dosya kaydedildi: {path} ({path.stat().st_size} bayt)"
            if not confirmed and self._click_affirmative(window):
                confirmed = True
            time.sleep(0.2)
        raise ActionForbidden(f"Kaydetme tamamlanmadı: {path}")


class ActionExecutor:
    """Planlanmış eylemleri güvenlik çekirdeğinden geçirerek gerçekleştirir.

    Onay kararı arayüzün işidir (``assess``); yürütücü ise her eylemi çalıştırmadan
    önce yasak/yol kurallarını yeniden denetler, değişikliği yedekler ve günlüğe yazar.
    """

    MAX_WRITE_CHARS = 5_000_000

    def __init__(
        self,
        services: Optional[SafetyServices] = None,
        on_screenshot: Optional[Callable[[str], None]] = None,
        map_listener: Optional[Callable[[dict], None]] = None,
        book_listener: Optional[Callable[[dict], None]] = None,
        browser_listener: Optional[Callable[[str], None]] = None,
        personal_store=None,
        storage=None,
        skills=None,
        home_service=None,
    ) -> None:
        self.services = services or build_services(Settings())
        self.browser_listener = browser_listener
        self.applications = ApplicationLauncher()
        self.ui = WindowsUI()
        self.projects = ProjectTools(self.services.policy, self.services.undo, Settings())
        self.desktop = DesktopTools(self.services.audit.path.parent.parent)
        self.capabilities = CapabilityTools()
        self.web = WebReader()
        self.browser = BrowserReader()
        self.news = shared_news_service()
        self.markets = shared_market_service()
        self.wiki = shared_wiki_service()
        self.github = shared_github_tools()
        self.youtube = shared_youtube_service()
        self.photos = shared_photo_service()
        self.weather = WeatherService(getattr(self.services, "settings", None))
        self.skills = skills or SkillLibrary()
        self.memory = MemoryTools(storage)
        self.downloads = DownloadTools(
            self.services.policy,
            self.services.undo,
            approval=self.services.approval,
            audit=self.services.audit,
        )
        self.steam = SteamWorkshop()
        self.maps = MapTools(getattr(self.services, "settings", None), listener=map_listener)
        self.books = BookTools(getattr(self.services, "settings", None), listener=book_listener)
        self.home = home_service or SmartHome()
        self.personal = PersonalTools(personal_store) if personal_store is not None else None
        self.system = SystemControls()
        self.screens = ScreenAgent(self.ui, self.desktop.directory.parent)
        self.window_tools = WindowTools(self.ui)
        self.on_screenshot = on_screenshot

    def _capture_for_model(self, delay: float = 0.35) -> str:
        """Eylemden sonra ekranı yakalar ve görüntüyü modelin sonraki adımına ekler."""
        if delay:
            time.sleep(delay)
        path, _ = self.desktop.capture({})
        if self.on_screenshot:
            self.on_screenshot(path)
        return path

    @staticmethod
    def _image_fingerprint(image) -> int:
        """Kaba ortalama-hash: eylem öncesi/sonrası ekranın değişip değişmediğini ölçer."""
        try:
            pixels = image.convert("L").resize((32, 32)).tobytes()
        except Exception:
            return 0
        if not pixels:
            return 0
        average = sum(pixels) / len(pixels)
        value = 0
        for index, pixel in enumerate(pixels):
            if pixel >= average:
                value |= 1 << index
        return value

    @classmethod
    def _screen_fingerprint(cls) -> int:
        try:
            from PIL import ImageGrab

            return cls._image_fingerprint(ImageGrab.grab(all_screens=True))
        except Exception:
            return 0

    @classmethod
    def _visual_change(cls, before: int, path: str) -> tuple[Optional[bool], str]:
        """Değişmeyen ekranı model adımına deterministik bir uyarı olarak bildirir."""
        try:
            from PIL import Image

            with Image.open(path) as image:
                after = cls._image_fingerprint(image)
        except Exception:
            return None, ""
        if not before or not after:
            return None, ""
        if (before ^ after).bit_count() <= 24:
            return False, (
                "\n⚠️ Görsel yansıma: eylemden sonra ekran neredeyse hiç değişmedi. "
                "Aynı eylemi körü körüne tekrarlama; hedefi ve güncel ekran haritasını doğrula."
            )
        return True, ""

    def _find_text_and_verify(self, args: dict) -> str:
        result = self.ui.find_text(args)
        if not args.get("click"):
            return result
        try:
            path = self._capture_for_model()
            data = json.loads(result)
            data["screenshot"] = path
            data["note"] = (
                str(data.get("note", ""))
                + " Tıklama sonrası doğrulama ekran görüntüsü modelin sonraki adımına eklendi; "
                "sayfanın gerçekten değiştiğini bu görüntüden kontrol et."
            )
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return result

    # -- plan değerlendirme --------------------------------------------------
    def assess(self, plan: ActionPlan, tainted: bool = False) -> PlanAssessment:
        highest = Risk.READ
        reasons: list[str] = []
        decision = "allow"
        for index, action in enumerate(plan.actions, start=1):
            try:
                risk = classify_action(action.action_type, action.arguments, self.services.policy)
            except SafetyError as error:
                return PlanAssessment("deny", Risk.FORBIDDEN, [f"{index}. adım: {error}"])
            highest = max(highest, risk)
            verdict = self.services.gate.decide(risk, tainted)
            if verdict.action == "deny":
                return PlanAssessment("deny", risk, [f"{index}. adım: {verdict.reason}"])
            if verdict.action == "ask":
                decision = "ask"
                if verdict.reason not in reasons:
                    reasons.append(verdict.reason)
        return PlanAssessment(decision, highest, reasons)

    def execute_plan(
        self,
        plan: ActionPlan,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> list[str]:
        results = []
        for index, action in enumerate(plan.actions, start=1):
            if self.services.kill.is_set():
                results.append("⛔ Durduruldu: kalan adımlar yapılmadı.")
                break
            if on_progress:
                on_progress(f"{index}/{len(plan.actions)}: {action.description}")
            result = self.execute(action)
            results.append(result)
            if result.startswith("❌"):
                break
        return results

    # -- tek eylem ---------------------------------------------------------------
    def execute(self, action: PlannedAction) -> str:
        risk = None
        try:
            risk = classify_action(action.action_type, action.arguments, self.services.policy)
            result = self._run(action)
        except SafetyError as error:
            result = f"❌ {error}"
        except FileExistsError:
            result = f"❌ Hedef zaten var: {action.arguments.get('path', '')}"
        except Exception as error:
            result = f"❌ İşlem başarısız: {error}"
        self.services.audit.record(tool=action.action_type, args=action.arguments, risk=risk, result=result)
        return result

    def undo_last(self) -> str:
        outcome = self.services.undo.undo_last()
        header = "✅ Son değişiklik geri alındı" if outcome.ok else "❌ Geri alma tamamlanamadı"
        if outcome.label is None:
            return f"❌ {outcome.messages[0]}"
        return header + f" ({outcome.label}):\n" + "\n".join(outcome.messages)

    @staticmethod
    def _make_parents(directory: Path, changes) -> None:
        """Eksik üst klasörleri oluşturur ve geri alınabilmeleri için kaydeder."""
        missing = []
        current = directory
        while not current.exists() and current.parent != current:
            missing.append(current)
            current = current.parent
        for folder in reversed(missing):
            folder.mkdir()
            changes.created(folder)

    def _run_command(self, args: dict) -> str:
        language = str(args.get("language", "")).casefold()
        code = str(args.get("code", ""))
        if language not in {"powershell", "python"} or not code.strip():
            raise ActionForbidden("run_command için powershell/python ve boş olmayan kod gerekli.")
        forbidden = re.compile(
            r"\b(reg(?:\.exe)?\s+(?:add|delete)|regedit|net\s+(?:user|localgroup)|takeown|icacls|"
            r"bcdedit|diskpart|runas|remove-itemproperty|set-itemproperty|new-itemproperty)\b|"
            r"-verb\s+runas",
            re.IGNORECASE,
        )
        lowered = code.casefold().replace("/", "\\")
        zones = [str(zone).casefold() for zone, _ in self.services.policy.forbidden]
        if forbidden.search(code) or any(zone in lowered for zone in zones):
            raise ActionForbidden("Komut sistem, kayıt defteri, hesap veya Jarvis korumalı alanına dokunuyor.")
        automation = re.compile(
            r"\b(?:import|from)\s+(?:pyautogui|pywinauto|pynput|autopy|keyboard|mouse|"
            r"win32gui|win32api|win32con|win32com)\b|"
            r"\b(?:pyautogui|pywinauto|pynput|autopy|win32gui|win32api|win32con|win32com)\s*\.|"
            r"\b(?:SendKeys|SendInput|SetForegroundWindow|SetCursorPos|mouse_event|keybd_event)\b",
            re.IGNORECASE,
        )
        if automation.search(code):
            raise ActionForbidden(
                "Masaüstü otomasyonu run_command içinde kullanılamaz; mouse_control, ui_control "
                "veya send_keys araçlarını kullan."
            )
        working_directory = self.services.policy.resolve(args.get("working_directory") or ".")
        if not working_directory.is_dir():
            raise ActionForbidden(f"Çalışma klasörü bulunamadı: {working_directory}")
        if language == "powershell":
            command = [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + code,
            ]
        else:
            executable = "python" if getattr(sys, "frozen", False) else sys.executable
            command = [executable, "-c", code]
        environment = os.environ.copy()
        environment.pop("GEMINI_API_KEY", None)
        environment["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen(
            command,
            cwd=str(working_directory),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        timeout = min(max(float(args.get("timeout_seconds", 30)), 1), 120)
        deadline = time.monotonic() + timeout
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if self.services.kill.is_set():
                    process.kill()
                    process.communicate()
                    return "❌ Komut Durdur düğmesiyle iptal edildi."
                if time.monotonic() >= deadline:
                    process.kill()
                    process.communicate()
                    return f"❌ Komut {int(timeout)} saniyede zaman aşımına uğradı."
        output = ((stdout or "") + (("\nHata:\n" + stderr) if stderr else "")).strip()
        if len(output) > 12000:
            output = output[:12000] + "\n… Çıktı kısaltıldı."
        status = "✅" if process.returncode == 0 else "❌"
        return f"{status} Komut çıkış kodu: {process.returncode}\n{output or 'Çıktı yok.'}"

    def _run_home(self, args: dict) -> str:
        action = str(args.get("action") or "status").strip().casefold()
        room = str(args.get("room") or "salon").strip().casefold()
        if room not in {"salon", "calisma"}:
            room = "salon"
        kind = str(args.get("kind") or "turk").strip().casefold()
        if kind not in {"turk", "filtre"}:
            kind = "turk"

        if action == "status":
            data = self.home.snapshot()
            lines = []
            for light in data["lights"].values():
                if light["on"]:
                    lines.append(f"• {light['name']}: açık, %{light['brightness']}, {light['tone_label']}")
                else:
                    lines.append(f"• {light['name']}: kapalı")
            for coffee in data["coffees"].values():
                if coffee["status"] == "demleniyor":
                    detail = f"demleniyor, {coffee['remaining']} sn kaldı"
                elif coffee["status"] == "hazir":
                    if coffee["kind"] == "turk":
                        detail = f"hazır, {coffee['foam_label']}, {coffee['sugar_label']}"
                    else:
                        detail = f"hazır, {coffee['cups']} fincan"
                else:
                    detail = "makine boş"
                lines.append(f"• {coffee['name']}: {detail}")
            return "✅ Akıllı ev durumu (simülasyon):\n" + "\n".join(lines)

        if action in {"light", "all_lights"}:
            on = args.get("on")
            if isinstance(on, str):
                on = on.strip().casefold() in {"true", "1", "evet", "acik", "açık"}
            elif on is not None:
                on = bool(on)
            brightness = args.get("brightness")
            try:
                brightness = int(brightness) if brightness is not None else None
            except (TypeError, ValueError):
                brightness = None
            tone = str(args.get("tone") or "").strip().casefold()
            tone = tone if tone in TONES else None
            if on is None and brightness is None and tone is None:
                return "❌ Işık için on, brightness veya tone bilgisi gerekli."
            if action == "all_lights":
                lights = self.home.set_all_lights(on=on, brightness=brightness, tone=tone)
                return (
                    "✅ Tüm ışıklar güncellendi (simülasyon): "
                    + " · ".join(light.describe() for light in lights)
                )
            light = self.home.set_light(room, on=on, brightness=brightness, tone=tone)
            return f"✅ {light.describe()} (simülasyon)"

        if action == "coffee_brew":
            cups = args.get("cups")
            try:
                cups = int(cups) if cups is not None else None
            except (TypeError, ValueError):
                cups = None
            foam = str(args.get("foam") or "").strip().casefold()
            foam = foam if foam in FOAMS else None
            sugar = str(args.get("sugar") or "").strip().casefold()
            sugar = sugar if sugar in SUGARS else None
            ok, coffee, seconds = self.home.brew(kind, cups=cups, foam=foam, sugar=sugar)
            if not ok:
                remaining = max(0, round((coffee.ready_at or 0) - self.home.now()))
                return f"❌ {coffee.name} zaten demleniyor · {remaining} sn kaldı."
            extra = f" · {coffee.cups} fincan" if kind == "filtre" else ""
            return f"✅ {coffee.name} başladı · {int(seconds)} sn{extra}. Süre dolunca hazır olacak. (simülasyon)"

        if action == "coffee_cancel":
            coffee = self.home.cancel_coffee(kind)
            return f"✅ {coffee.name} iptal edildi. (simülasyon)"

        coffee = self.home.coffee(kind)
        return f"✅ {coffee.describe(self.home.now())} (simülasyon)"

    def _run(self, action: PlannedAction) -> str:
        kind, args = action.action_type, action.arguments
        policy, undo = self.services.policy, self.services.undo

        if kind == "undo_last":
            return self.undo_last()

        if kind == "create_folder":
            path = policy.resolve(args["path"])
            if path.exists():
                raise FileExistsError(str(path))
            changes = undo.begin(kind)
            try:
                self._make_parents(path, changes)
                changes.commit()
            except BaseException:
                changes.discard()
                raise
            return f"✅ Klasör oluşturuldu: {path}"

        if kind in {"write_text_file", "append_text_file"}:
            path = policy.resolve(args["path"])
            content = str(args.get("content", ""))
            if len(content) > self.MAX_WRITE_CHARS:
                return "❌ İçerik çok büyük (en fazla 5 milyon karakter)."
            overwrite = bool(args.get("overwrite", False))
            if path.is_dir():
                return f"❌ Bu bir klasör, dosya yazılamaz: {path}"
            existed = path.exists()
            if existed and not overwrite and kind == "write_text_file":
                return f"❌ Dosya zaten var, üzerine yazılmadı: {path}"
            changes = undo.begin(kind)
            try:
                if existed:
                    changes.backup(path)
                else:
                    self._make_parents(path.parent, changes)
                with path.open("a" if kind == "append_text_file" else "w", encoding="utf-8") as stream:
                    stream.write(content)
                if not path.exists():
                    changes.discard()
                    return f"❌ Dosya oluşturulamadı: {path}"
                if not existed:
                    changes.created(path)
                changes.commit()
            except BaseException:
                changes.discard()
                raise
            return f"✅ Dosya yazıldı: {path}"

        if kind in {"copy_path", "move_path"}:
            source = policy.resolve(args["source"])
            destination = policy.resolve(args["destination"])
            verb = "Kopyalanacak" if kind == "copy_path" else "Taşınacak"
            if not source.exists():
                return f"❌ {verb} öğe bulunamadı: {source}"
            if destination.exists():
                return f"❌ Hedef zaten var, üzerine yazılmadı: {destination}"
            changes = undo.begin(kind)
            try:
                self._make_parents(destination.parent, changes)
                if kind == "copy_path":
                    if source.is_dir():
                        shutil.copytree(source, destination)
                    else:
                        shutil.copy2(source, destination)
                    changes.created(destination)
                else:
                    shutil.move(str(source), str(destination))
                    changes.moved(source, destination)
                changes.commit()
            except BaseException:
                changes.discard()
                raise
            return f"✅ {'Kopyalandı' if kind == 'copy_path' else 'Taşındı'}: {source} → {destination}"

        if kind == "list_project_files":
            return self.projects.list_files(args)

        if kind == "read_project_file":
            return self.projects.read_file(args)

        if kind == "search_project":
            return self.projects.search(args)

        if kind == "preview_edit":
            return self.projects.preview_replace(args)

        if kind == "replace_exact":
            return self.projects.replace(args)

        if kind == "run_tests":
            return self.projects.run_tests(args)

        if kind == "create_project":
            return self.projects.create_project(args)

        if kind == "prepare_git_branch":
            return self.projects.prepare_git_branch(args)

        if kind == "prepare_self_workspace":
            return self.projects.prepare_self_workspace(args)

        if kind == "diff_self_workspace":
            return self.projects.diff_self_workspace(args)

        if kind == "apply_self_changes":
            return self.projects.apply_self_changes(args)

        if kind == "open_application":
            application = str(args["application"])
            launched = self.applications.launch(application, should_stop=self.services.kill.is_set)
            return (
                f"✅ Uygulama hazır: {launched}. Başlatma gerçek pencere/süreç ile doğrulandı. "
                "Uygulama içinde ek bir işlem gerekiyorsa inspect_ui ile arayüzü incele."
            )

        if kind == "open_web_page":
            opened = self.applications.open_url(str(args["application"]), str(args["url"]))
            return f"✅ Sayfa belirtilen uygulamada açıldı: {opened}"

        if kind == "open_system_uri":
            opened = self.applications.open_system_uri(str(args["uri"]))
            return f"✅ Uygulama bağlantısı açıldı: {opened}"

        if kind == "window_control":
            return self.window_tools.run(args)

        if kind == "inspect_ui":
            details = self.ui.inspect(
                str(args["window_title"]), int(args.get("limit", 100)), str(args.get("name_filter", ""))
            )
            return f"✅ Uygulama arayüzü:\n{details}"

        if kind == "find_text_on_screen":
            return self._find_text_and_verify(args)

        if kind == "ui_control":
            result = self.ui.act(
                str(args["window_title"]),
                str(args["control_name"]),
                str(args.get("action", "click")),
                str(args.get("text", "")),
                str(args.get("direction", "down")),
                int(args.get("amount", 3) or 3),
            )
            return f"✅ Uygulama içi işlem yapıldı: {result}"

        if kind == "send_keys":
            result = self.ui.keys(
                str(args["window_title"]), str(args.get("keys", "")), str(args.get("text", ""))
            )
            message = f"✅ Uygulama içi klavye işlemi yapıldı: {result}"
            if self.on_screenshot:
                try:
                    path = self._capture_for_model()
                    message += f"\nDoğrulama görüntüsü modelin sonraki adımına eklendi: {path}"
                except Exception:
                    pass
            return message

        if kind == "save_file_dialog":
            path = policy.resolve(args["path"])
            if not path.parent.is_dir():
                raise ActionForbidden(f"Kaydetme klasörü bulunamadı: {path.parent}")
            existed = path.is_file()
            changes = undo.begin("save_file_dialog")
            try:
                if existed:
                    changes.backup(path)
                result = self.ui.save_file(str(args.get("window_title", "")).strip(), path)
                if not existed:
                    changes.created(path)
                changes.commit()
            except BaseException:
                changes.discard()
                raise
            return result

        if kind == "capture_screen":
            note = ""
            window_title = str(args.get("window_title", "")).strip()
            if window_title:
                try:
                    window = self.ui._window(window_title)
                    if window.is_minimized():
                        window.restore()
                    window.set_focus()
                    time.sleep(0.2)
                except Exception as error:
                    note = f"\n⚠️ Pencere öne getirilemedi: {error}"
            path, message = self.desktop.capture(args)
            if self.on_screenshot:
                self.on_screenshot(path)
            if args.get("read_text"):
                try:
                    from PIL import Image
                    import ctypes

                    user32 = ctypes.windll.user32
                    with Image.open(path) as image:
                        scale = user32.GetSystemMetrics(78) / max(1, image.width)
                    origin = (int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77)))
                    text_map = ScreenOCR().read_lines(
                        Path(path), origin, str(args.get("language", "")), scale=scale
                    )
                    message += "\nOCR metin haritası:\n" + json.dumps(text_map, ensure_ascii=False)
                except Exception as error:
                    message += f"\n⚠️ OCR metni okunamadı: {error}"
            return message + note

        if kind == "inspect_image":
            path = policy.resolve(args["path"])
            return self.desktop.inspect_image(path, self.on_screenshot)

        if kind == "screen_map":
            result = self.screens.map_screen(args)
            try:
                data = json.loads(result)
                image = str(data.get("image") or "").strip()
                if image and self.on_screenshot:
                    self.on_screenshot(image)
            except (TypeError, ValueError):
                pass
            return result

        if kind == "screen_wait":
            return self.screens.wait_for(args)

        if kind == "screen_act":
            before = self._screen_fingerprint() if self.on_screenshot else 0
            result = self.screens.screen_act(args)
            if self.on_screenshot:
                try:
                    path = self._capture_for_model()
                    data = json.loads(result)
                    data["screenshot"] = path
                    changed, reflection = self._visual_change(before, path)
                    if changed is not None:
                        data["visual_change"] = changed
                    data["note"] = (
                        str(data.get("note", ""))
                        + " Eylem sonrası doğrulama görüntüsü sonraki modele eklendi."
                        + reflection
                    )
                    return json.dumps(data, ensure_ascii=False)
                except Exception:
                    pass
            return result

        if kind == "mouse_control":
            action = str(args.get("action", "click"))
            text = str(args.get("text", "")).strip()
            if text and args.get("x") is None and args.get("y") is None and action in {"click", "right_click"}:
                click_args = {
                    "text": text,
                    "window_title": str(args.get("window_title", "")).strip(),
                    "button": "right" if action == "right_click" else str(args.get("button", "left")),
                    "index": args.get("index", 0),
                    "click": True,
                    "language": str(args.get("language", "")),
                }
                return self._find_text_and_verify(click_args)
            window_title = str(args.get("window_title", "")).strip()
            if window_title:
                if not self.ui.focus(self.ui._window(window_title)):
                    raise ActionForbidden(
                        "Pencere öne getirilemedi; yanlış uygulamaya tıklamayı önlemek için fare işlemi durduruldu."
                    )
                time.sleep(0.15)
            before = (
                self._screen_fingerprint()
                if action in {"click", "double_click", "right_click"} and self.on_screenshot
                else 0
            )
            message = self.desktop.mouse(args)
            if action in {"click", "double_click", "right_click"} and self.on_screenshot:
                try:
                    path = self._capture_for_model()
                    message += f"\nDoğrulama görüntüsü modelin sonraki adımına eklendi: {path}"
                    _, reflection = self._visual_change(before, path)
                    message += reflection
                except Exception:
                    pass
            return message

        if kind == "draw_shape":
            return self.ui.draw_shape(
                str(args["window_title"]),
                str(args["shape"]),
                int(args["start_x"]),
                int(args["start_y"]),
                int(args["end_x"]),
                int(args["end_y"]),
            )

        if kind == "check_capabilities":
            return self.capabilities.check()

        if kind == "install_dependency":
            return self.capabilities.install(args)

        if kind == "run_command":
            return self._run_command(args)

        if kind == "open_path":
            path = policy.resolve(args["path"])
            if not path.exists():
                return f"❌ Açılacak dosya bulunamadı: {path}"
            os.startfile(str(path))
            return f"✅ Dosya açıldı: {path}"

        if kind == "map":
            return self.maps.run(args)

        if kind == "book":
            return self.books.run(args)

        if kind == "home":
            return self._run_home(args)

        if kind == "personal":
            if self.personal is None:
                raise ActionForbidden("Kişisel veri deposu bu oturumda kullanılamıyor.")
            return self.personal.run(args)

        if kind == "memory":
            if self.memory is None:
                raise ActionForbidden("Kalıcı hafıza bu oturumda kullanılamıyor.")
            return self.memory.run(args)

        if kind == "skill":
            return self.skills.run(args)

        if kind == "news":
            return self.news.get(args)

        if kind == "markets":
            return self.markets.get()

        if kind == "wiki":
            return self.wiki.get(args)

        if kind == "github":
            return self.github.run(args)

        if kind == "weather":
            try:
                return format_weather(self.weather.fetch(str(args.get("city") or "")))
            except Exception as error:
                return f"❌ Hava durumu alınamadı: {error}. Değer uydurma; kullanıcıya bağlantı sorununu söyle."

        if kind == "youtube":
            action = str(args.get("action") or "open").strip().casefold()
            if action in {"latest", "highlights"}:
                return self.youtube.get({**args, "action": action})
            target = youtube_target(
                str(args.get("query") or ""),
                str(args.get("url") or ""),
            )
            if self.browser_listener:
                self.browser_listener(target)
                return f"✅ YouTube Jarvis tarayıcısında açıldı: {target}"
            webbrowser.open_new_tab(target)
            return f"✅ YouTube varsayılan tarayıcıda açıldı: {target}"

        if kind == "photo":
            return self.photos.get(args)

        if kind == "system_control":
            try:
                seconds = int(args.get("seconds", 60) or 60)
            except (TypeError, ValueError):
                seconds = 60
            return self.system.action(
                str(args.get("action", "")),
                seconds,
                str(args.get("reason", "Jarvis")),
            )

        if kind == "clipboard":
            action = str(args.get("action", "read")).strip().casefold()
            if action == "read":
                text = read_clipboard_text()
                if not text:
                    return "ℹ️ Pano boş."
                preview = text if len(text) <= 4000 else text[:4000] + "… (kısaltıldı)"
                return f"✅ Pano içeriği ({len(text)} karakter):\n{preview}"
            if action == "write":
                text = str(args.get("text") or "")
                if not text:
                    raise ValueError("Panoya yazılacak metin boş olamaz.")
                write_clipboard_text(text)
                return f"✅ Panoya yazıldı ({len(text)} karakter)."
            if action == "clear":
                clear_clipboard()
                return "✅ Pano temizlendi."
            raise ActionForbidden("clipboard için action: read, write veya clear olmalı.")

        if kind == "read_page":
            return self.web.read_page(args)

        if kind == "browser_read":
            return self.browser.read(args)

        if kind == "browser_open":
            target = str(args["url"]).strip()
            if urlparse(target).scheme not in {"http", "https"}:
                raise ActionForbidden("Tarayıcı yalnızca http/https adresleri açabilir.")
            if self.browser_listener:
                self.browser_listener(target)
                return f"✅ Jarvis tarayıcısında açıldı: {target}"
            webbrowser.open_new_tab(target)
            return f"✅ Sayfa açıldı: {target}"

        if kind == "read_rss":
            return self.web.read_rss(args)

        if kind == "download_file":
            return self.downloads.download(args)

        if kind == "steam_workshop":
            return self.steam.run(args)

        if kind == "search_web":
            query = str(args["query"]).strip()
            url = f"https://www.google.com/search?q={quote_plus(query)}"
            application = str(args.get("application", "")).strip()
            if application:
                opened = self.applications.open_url(application, url)
                return f"✅ Arama belirtilen uygulamada açıldı: {opened}"
            if self.browser_listener:
                self.browser_listener(url)
                return f"✅ Arama Jarvis tarayıcısında açıldı: {query}"
            webbrowser.open_new_tab(url)
            return f"✅ Varsayılan tarayıcıda arama açıldı: {query}"

        if kind == "delete_path":
            path = policy.resolve(args["path"])
            if not path.exists():
                return f"❌ Silinecek öğe bulunamadı: {path}"
            try:
                from send2trash import send2trash
            except ImportError:
                return "❌ Güvenli silme bileşeni kurulu değil."
            send2trash(str(path))
            changes = undo.begin(kind)
            changes.trashed(path)
            changes.commit()
            return f"✅ Geri Dönüşüm Kutusuna taşındı: {path}"

        raise ActionForbidden(f"Desteklenmeyen işlem: {kind}")
