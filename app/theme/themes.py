"""Jarvis tema paletleri ve seçili temanın okunması.

Paletler sade, düşük kontrastlı yüzeyler ve tek yumuşak vurgu rengi üzerine kuruludur.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BLACK = {
    "NAME": "Grafit",
    "LABEL": "Grafit / Sade",
    "IS_LIGHT": False,
    "BACKGROUND": "#0c0c0d",
    "BACKGROUND_2": "#111112",
    "PANEL": "#151516",
    "PANEL_2": "#1b1b1d",
    "PANEL_HOVER": "#222224",
    "INPUT": "#18181a",
    "RED": "#d8c7ac",
    "RED_BRIGHT": "#f0e6d2",
    "RED_SOFT": "#9c8c74",
    "RED_DIM": "#574e40",
    "RED_GLOW": "#fff3dd",
    "GOLD": "#c9a97f",
    "GOLD_DIM": "#6a5943",
    "TEXT": "#f4f2ee",
    "TEXT_SOFT": "#d3d0c9",
    "MUTED": "#a29e96",
    "DIM": "#79756d",
    "FAINT": "#4c4943",
    "BORDER": "#262624",
    "BORDER_RED": "#3b352b",
    "GRID": "#111112",
    "FONT": "Segoe UI",
    "MONO": "Consolas",
    "WINDOW_RADIUS": 18,
}

MIDNIGHT = {
    "NAME": "Gece",
    "LABEL": "Gece / Sakin",
    "IS_LIGHT": False,
    "BACKGROUND": "#0c0e11",
    "BACKGROUND_2": "#101318",
    "PANEL": "#151920",
    "PANEL_2": "#1b2028",
    "PANEL_HOVER": "#232a34",
    "INPUT": "#171c23",
    "RED": "#a9c1d6",
    "RED_BRIGHT": "#d3e2f0",
    "RED_SOFT": "#738ca3",
    "RED_DIM": "#3b4c5c",
    "RED_GLOW": "#e6f2fb",
    "GOLD": "#c6b892",
    "GOLD_DIM": "#625a46",
    "TEXT": "#f1f4f8",
    "TEXT_SOFT": "#cfd6de",
    "MUTED": "#98a2ae",
    "DIM": "#6e7885",
    "FAINT": "#454c57",
    "BORDER": "#242a33",
    "BORDER_RED": "#33404e",
    "GRID": "#10141a",
    "FONT": "Segoe UI",
    "MONO": "Consolas",
    "WINDOW_RADIUS": 18,
}

EMBER = {
    "NAME": "Kum",
    "LABEL": "Kum / Sıcak",
    "IS_LIGHT": False,
    "BACKGROUND": "#0e0c0a",
    "BACKGROUND_2": "#12100d",
    "PANEL": "#181512",
    "PANEL_2": "#1e1a16",
    "PANEL_HOVER": "#26211c",
    "INPUT": "#1a1613",
    "RED": "#c9a27a",
    "RED_BRIGHT": "#e8c8a3",
    "RED_SOFT": "#8f6f50",
    "RED_DIM": "#4c3a29",
    "RED_GLOW": "#f1d8b8",
    "GOLD": "#bd9e6d",
    "GOLD_DIM": "#5f4e35",
    "TEXT": "#f5f0e9",
    "TEXT_SOFT": "#d9cfc2",
    "MUTED": "#a99c8b",
    "DIM": "#7d7264",
    "FAINT": "#4e463c",
    "BORDER": "#2b2520",
    "BORDER_RED": "#443629",
    "GRID": "#14110e",
    "FONT": "Segoe UI",
    "MONO": "Consolas",
    "WINDOW_RADIUS": 18,
}

FOREST = {
    "NAME": "Adaçayı",
    "LABEL": "Adaçayı / Doğal",
    "IS_LIGHT": False,
    "BACKGROUND": "#0b0d0c",
    "BACKGROUND_2": "#0f1211",
    "PANEL": "#141816",
    "PANEL_2": "#191e1b",
    "PANEL_HOVER": "#212722",
    "INPUT": "#161b18",
    "RED": "#a3bfae",
    "RED_BRIGHT": "#cbe2d5",
    "RED_SOFT": "#718c7b",
    "RED_DIM": "#3b4a40",
    "RED_GLOW": "#e3f2e9",
    "GOLD": "#c2b58f",
    "GOLD_DIM": "#5f5841",
    "TEXT": "#f1f5f2",
    "TEXT_SOFT": "#cfdad2",
    "MUTED": "#98a69c",
    "DIM": "#6f7b72",
    "FAINT": "#454f48",
    "BORDER": "#242a26",
    "BORDER_RED": "#33453a",
    "GRID": "#101412",
    "FONT": "Segoe UI",
    "MONO": "Consolas",
    "WINDOW_RADIUS": 18,
}

WHITE = {
    "NAME": "Aydınlık",
    "LABEL": "Aydınlık / Temiz",
    "IS_LIGHT": True,
    "BACKGROUND": "#f4f4f1",
    "BACKGROUND_2": "#ecece8",
    "PANEL": "#ffffff",
    "PANEL_2": "#f7f7f4",
    "PANEL_HOVER": "#efefea",
    "INPUT": "#ffffff",
    "RED": "#2c2c2e",
    "RED_BRIGHT": "#111113",
    "RED_SOFT": "#6b6b6e",
    "RED_DIM": "#d9d9d4",
    "RED_GLOW": "#4a4a4d",
    "GOLD": "#8c7a5b",
    "GOLD_DIM": "#cfc5b2",
    "TEXT": "#17171a",
    "TEXT_SOFT": "#3c3c40",
    "MUTED": "#6e6e73",
    "DIM": "#96969b",
    "FAINT": "#c7c7c2",
    "BORDER": "#e1e1dc",
    "BORDER_RED": "#d1d1cb",
    "GRID": "#eaeae5",
    "FONT": "Segoe UI",
    "MONO": "Consolas",
    "WINDOW_RADIUS": 18,
}

THEMES = {
    "black": BLACK,
    "midnight": MIDNIGHT,
    "ember": EMBER,
    "forest": FOREST,
    "white": WHITE,
}
THEME_ORDER = ("black", "midnight", "ember", "forest", "white")


def rgba_css(color: str, alpha: float) -> str:
    value = str(color).lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    try:
        red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    except (ValueError, IndexError):
        red, green, blue = 232, 57, 79
    return f"rgba({red}, {green}, {blue}, {alpha})"


_THEME_ALIASES = {
    "siyah": "black", "kirmizi": "black", "kırmızı": "black", "ultron": "black",
    "grafit": "black", "sade": "black",
    "mavi": "midnight", "gece": "midnight", "midnight": "midnight",
    "kor": "ember", "amber": "ember", "turuncu": "ember", "ember": "ember",
    "kum": "ember", "sicak": "ember", "sıcak": "ember",
    "yesil": "forest", "yeşil": "forest", "zumrut": "forest", "zümrüt": "forest",
    "orman": "forest", "forest": "forest", "adacayi": "forest", "adaçayı": "forest",
    "beyaz": "white", "aydinlik": "white", "aydınlık": "white", "light": "white",
}


def normalize_theme(name: object) -> str:
    value = str(name or "").strip().lower()
    value = _THEME_ALIASES.get(value, value)
    return value if value in THEMES else "black"


def _settings_file() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis"
    return root / "settings.json"


def saved_theme_name() -> str:
    override = os.environ.get("JARVIS_THEME", "").strip().lower()
    if override in THEMES:
        return override
    try:
        data = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "black"
    return normalize_theme(data.get("theme") if isinstance(data, dict) else "")


def palette(name: object = None) -> dict:
    return dict(THEMES[normalize_theme(name if name is not None else saved_theme_name())])
