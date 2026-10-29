"""Jarvis için sırları gizleyen yerel tanılama ve gizlilik raporu."""

from __future__ import annotations

import importlib.util
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis" / "data"
VERSION = "1.0"
MAX_REPORT_CHARS = 12_000

DEPENDENCIES = (
    ("psutil", "Sistem izleme"),
    ("duckdb", "Yerel mekân indeksi"),
    ("cv2", "Kamera görüntüsü"),
    ("mediapipe", "El/yüz izleme"),
    ("numpy", "Görüntü işleme"),
    ("edge_tts", "Doğal ses"),
    ("pyttsx3", "Yerel ses yedeği"),
    ("speech_recognition", "Mikrofon"),
    ("pywinauto", "Masaüstü kontrolü"),
    ("winrt.windows.media.ocr", "Windows OCR"),
    ("PIL", "Görüntü araçları"),
    ("pypdf", "PDF okuma"),
    ("PySide6.QtMultimedia", "Ses çalma"),
    ("send2trash", "Geri dönüşüm"),
)

SECRET_PATTERNS = (
    (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "[GİZLENDİ]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[GİZLENDİ]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[GİZLENDİ]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|parola)\b\s*[:=]\s*\S+"),
        r"\1=[GİZLENDİ]",
    ),
)


def redact(text: Any) -> str:
    value = str(text or "")
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _size_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "yok"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024.0
    return "—"


def _dependency_available(name: str, checker: Optional[Callable[[str], bool]]) -> bool:
    if checker is not None:
        try:
            return bool(checker(name))
        except Exception:
            return False
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _qversion() -> str:
    try:
        from PySide6 import __version__ as version

        return str(version)
    except Exception:
        return "yok"


def _flag_text(value: Any) -> str:
    if isinstance(value, bool):
        return "açık" if value else "kapalı"
    return str(value or "—")


def _recent_errors(path: Optional[Path]) -> str:
    if path is None:
        return "Başlatma hatası kaydı yok."
    try:
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
    except OSError:
        return "Başlatma hatası kaydı yok."
    if not lines:
        return "Başlatma hatası kaydı yok."
    return redact(" | ".join(lines[-4:]))[:900]


def build_report(
    settings: Any = None,
    data_dir: Optional[Path] = None,
    flags: Optional[Mapping[str, Any]] = None,
    dependency_check: Optional[Callable[[str], bool]] = None,
    startup_log: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> str:
    """Yerel rapor üretir; API anahtarı ve ortam değişkenleri rapora girmez."""
    now = now or datetime.now()
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    flags = dict(flags or {})

    provider = "—"
    model = "—"
    access_mode = "—"
    strict_web = None
    if settings is not None:
        try:
            provider = {
                "gemini": "GEMINI",
                "ollama": "OLLAMA",
                "tokenharbor": "TOKEN HARBOR",
            }.get(settings.provider, settings.provider.upper())
            model = {
                "gemini": settings.model,
                "ollama": settings.ollama_model,
                "tokenharbor": settings.tokenharbor_model or "otomatik (:free)",
            }.get(settings.provider, "—")
            access_mode = {
                "approval": "Onaylı",
                "step": "Adım adım",
                "full": "Tam erişim",
            }.get(str(settings.access_mode), str(settings.access_mode))
            strict_web = bool(settings.strict_after_web)
        except Exception:
            pass

    files = [
        ("jarvis.db", root / "jarvis.db"),
        ("hud.json", root / "hud.json"),
        ("news.json", root / "news.json"),
        ("weather.json", root / "weather.json"),
        ("wiki.json", root / "wiki.json"),
        ("companion.json", root / "companion.json"),
        ("poi.sqlite3", root / "poi.sqlite3"),
    ]

    lines = [
        "JARVIS TANILAMA VE GİZLİLİK RAPORU",
        f"{now:%d.%m.%Y %H:%M} · yerel rapor · dış servise gönderilmedi",
        "",
        "SİSTEM",
        f"{platform.system()} {platform.release()} · {platform.machine() or '—'}",
        f"Python {platform.python_version()} · PySide6 {_qversion()}",
        f"Jarvis {VERSION}",
        "",
        "YAPILANDIRMA",
        f"Sağlayıcı: {provider} · Model: {model}",
        f"Erişim: {access_mode}"
        + ("" if strict_web is None else f" · Web sonrası katı onay: {'açık' if strict_web else 'kapalı'}"),
        "",
    ]

    labels = {
        "screen_context": "Ekran bağlamı",
        "microphone": "Mikrofon",
        "camera": "Kamera",
        "companion": "Eşlikçi",
        "hand_control": "El kontrolü",
        "browser": "Uygulama içi tarayıcı",
    }
    if flags:
        lines.append("DURUM")
        for key, label in labels.items():
            if key in flags:
                lines.append(f"{label}: {_flag_text(flags[key])}")
        lines.append("")

    lines.extend(["VERİ", f"Klasör: {root}"])
    for name, path in files:
        if path.exists():
            lines.append(f"{name}: {_size_text(path)}")
    lines.append("")

    lines.append("BİLEŞENLER")
    for module, label in DEPENDENCIES:
        mark = "VAR" if _dependency_available(module, dependency_check) else "YOK"
        lines.append(f"[{mark}] {label} ({module})")
    lines.append("")

    screen_context = bool(flags.get("screen_context", False))
    lines.extend([
        "GİZLİLİK",
        "API anahtarı, token, parola ve ortam değişkenleri bu rapora yazılmaz.",
        "Kamera kareleri varsayılan olarak yerel işlenir; buluta gönderilmez.",
        (
            "Ekran görüntüsü isteklere ekleniyor; bunu Ayarlar > Güvenlik bölümünden kapatabilirsin."
            if screen_context
            else "Ekran görüntüsü varsayılan olarak isteklere eklenmez."
        ),
        "",
        "SON BAŞLATMA HATASI",
        _recent_errors(startup_log),
    ])
    report = "\n".join(lines).strip()
    return redact(report[:MAX_REPORT_CHARS]) + "\n"
