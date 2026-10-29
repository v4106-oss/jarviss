"""Yerel Jarvis ayarları; API anahtarı proje klasörüne yazılmaz."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from app.theme.themes import normalize_theme

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:3b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TOKENHARBOR_URL = "https://tokenharbor.ai/v1"
DEFAULT_VOICE_STYLE = "jarvis"
VOICE_STYLES = ("jarvis", "natural", "warm", "male")

_DEFAULT_HOME = {
    "name": "Derince",
    "region": "Kocaeli",
    "lat": 40.757,
    "lon": 29.815,
}


def _clock_text(value: object, default: str) -> str:
    text = " ".join(str(value or "").split())
    try:
        hour, minute = text.split(":", 1)
        hour_value, minute_value = int(hour), int(minute)
    except (TypeError, ValueError):
        return default
    if 0 <= hour_value <= 23 and 0 <= minute_value <= 59:
        return f"{hour_value:02d}:{minute_value:02d}"
    return default


class Settings:
    def __init__(self) -> None:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "settings.json"
        self._data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self) -> None:
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def model(self) -> str:
        return str(self._data.get("gemini_model") or DEFAULT_MODEL).strip()

    @property
    def api_key(self) -> str:
        key = os.environ.get("GEMINI_API_KEY") or str(self._data.get("gemini_api_key") or "")
        return key.strip()

    @property
    def provider(self) -> str:
        value = self._data.get("ai_provider")
        return value if value in ("gemini", "ollama", "tokenharbor") else "gemini"

    @property
    def provider_label(self) -> str:
        return {
            "ollama": "Ollama",
            "tokenharbor": "Token Harbor",
        }.get(self.provider, "Gemini")

    @property
    def active_model(self) -> str:
        if self.provider == "tokenharbor":
            return self.tokenharbor_model
        return self.ollama_model if self.provider == "ollama" else self.model

    @property
    def ollama_url(self) -> str:
        return str(self._data.get("ollama_url") or DEFAULT_OLLAMA_URL).strip().rstrip("/")

    @property
    def ollama_model(self) -> str:
        return str(self._data.get("ollama_model") or DEFAULT_OLLAMA_MODEL).strip()

    @property
    def tokenharbor_url(self) -> str:
        return str(self._data.get("tokenharbor_url") or DEFAULT_TOKENHARBOR_URL).strip().rstrip("/")

    @property
    def tokenharbor_model(self) -> str:
        return str(self._data.get("tokenharbor_model") or "").strip()

    @property
    def tokenharbor_api_key(self) -> str:
        key = os.environ.get("TOKENHARBOR_API_KEY") or str(self._data.get("tokenharbor_api_key") or "")
        return key.strip()

    @property
    def data_dir(self) -> Path:
        return self.path.parent

    @property
    def theme(self) -> str:
        return normalize_theme(self._data.get("theme"))

    def set_theme(self, name: str) -> None:
        self._data["theme"] = normalize_theme(name)
        self._write()

    @property
    def access_mode(self) -> str:
        value = self._data.get("access_mode")
        return value if value in ("approval", "step", "full") else "approval"

    @property
    def strict_after_web(self) -> bool:
        return bool(self._data.get("strict_after_web", True))

    @property
    def screen_context(self) -> bool:
        """Ekran görüntüsü her isteğe eklensin mi; varsayılan kapalı."""
        return bool(self._data.get("screen_context", False))

    def set_screen_context(self, enabled: bool) -> None:
        self._data["screen_context"] = bool(enabled)
        self._write()

    @property
    def voice_replies(self) -> bool:
        """Sesli yanıt açık mı; yazılı komutların yanıtı da okunur."""
        return bool(self._data.get("voice_replies", True))

    def set_voice_replies(self, enabled: bool) -> None:
        self._data["voice_replies"] = bool(enabled)
        self._write()

    @property
    def companion_enabled(self) -> bool:
        return bool(self._data.get("companion_enabled", True))

    @property
    def companion_interval(self) -> int:
        try:
            value = int(self._data.get("companion_interval", 90))
        except (TypeError, ValueError):
            value = 90
        return max(15, min(value, 480))

    @property
    def companion_quiet_start(self) -> str:
        return _clock_text(self._data.get("companion_quiet_start"), "23:00")

    @property
    def companion_quiet_end(self) -> str:
        return _clock_text(self._data.get("companion_quiet_end"), "08:00")

    @property
    def companion_hydration(self) -> bool:
        return bool(self._data.get("companion_hydration", True))

    @property
    def companion_speak(self) -> bool:
        return bool(self._data.get("companion_speak", False))

    @property
    def mic_device(self) -> str:
        """Kalıcı mikrofon adı; boşsa sistem varsayılanı kullanılır."""
        return str(self._data.get("mic_device") or "").strip()

    @property
    def speaker_device(self) -> str:
        """Kalıcı hoparlör/kulaklık adı; boşsa sistem varsayılanı kullanılır."""
        return str(self._data.get("speaker_device") or "").strip()

    @property
    def camera_index(self) -> int:
        try:
            value = int(self._data.get("camera_index", 0))
        except (TypeError, ValueError):
            value = 0
        return max(0, min(value, 16))

    @property
    def voice_style(self) -> str:
        value = str(self._data.get("voice_style") or "").strip().casefold()
        return value if value in VOICE_STYLES else DEFAULT_VOICE_STYLE

    @property
    def voice_rate(self) -> int:
        try:
            value = int(self._data.get("voice_rate", -3))
        except (TypeError, ValueError):
            value = -3
        return max(-20, min(value, 20))

    @property
    def voice_volume(self) -> int:
        try:
            value = int(self._data.get("voice_volume", 90))
        except (TypeError, ValueError):
            value = 90
        return max(0, min(value, 100))

    @property
    def voice_headphones(self) -> bool:
        """Kulaklık modu; açıkken Jarvis konuşurken mikrofon araya girmeyi dinler."""
        return bool(self._data.get("voice_headphones", True))

    def set_audio(
        self,
        mic_device: str = "",
        speaker_device: str = "",
        camera_index: int = 0,
        voice_style: str = DEFAULT_VOICE_STYLE,
        voice_rate: int = -3,
        voice_volume: int = 90,
        headphones: bool = True,
    ) -> None:
        self._data["mic_device"] = str(mic_device or "").strip()
        self._data["speaker_device"] = str(speaker_device or "").strip()
        try:
            index = int(camera_index)
        except (TypeError, ValueError):
            index = 0
        self._data["camera_index"] = max(0, min(index, 16))
        style = str(voice_style or "").strip().casefold()
        self._data["voice_style"] = style if style in VOICE_STYLES else DEFAULT_VOICE_STYLE
        try:
            rate = int(voice_rate)
        except (TypeError, ValueError):
            rate = -3
        self._data["voice_rate"] = max(-20, min(rate, 20))
        try:
            volume = int(voice_volume)
        except (TypeError, ValueError):
            volume = 90
        self._data["voice_volume"] = max(0, min(volume, 100))
        self._data["voice_headphones"] = bool(headphones)
        self._write()

    def set_companion(
        self,
        enabled: Optional[bool] = None,
        interval: Optional[int] = None,
        quiet_start: Optional[str] = None,
        quiet_end: Optional[str] = None,
        hydration: Optional[bool] = None,
        speak: Optional[bool] = None,
    ) -> None:
        if enabled is not None:
            self._data["companion_enabled"] = bool(enabled)
        if interval is not None:
            try:
                value = int(interval)
            except (TypeError, ValueError):
                value = 90
            self._data["companion_interval"] = max(15, min(value, 480))
        if quiet_start is not None:
            self._data["companion_quiet_start"] = _clock_text(quiet_start, "23:00")
        if quiet_end is not None:
            self._data["companion_quiet_end"] = _clock_text(quiet_end, "08:00")
        if hydration is not None:
            self._data["companion_hydration"] = bool(hydration)
        if speak is not None:
            self._data["companion_speak"] = bool(speak)
        self._write()

    def _limit(self, key: str) -> int:
        try:
            return max(0, int(self._data.get(key, 0)))
        except (TypeError, ValueError):
            return 0

    @property
    def agent_step_limit(self) -> int:
        """Ajanın en fazla kaç araç adımı atacağı; 0 = sınırsız."""
        return self._limit("agent_step_limit")

    @property
    def agent_seconds_limit(self) -> int:
        """Tek istek için duvar saati bütçesi (saniye); 0 = sınırsız."""
        return self._limit("agent_seconds_limit")

    @property
    def agent_loop_limit(self) -> int:
        """Aynı aracın değişmeyen sonuçla üst üste tekrar sınırı; 0 = döngü koruması kapalı."""
        return self._limit("agent_loop_limit")

    def set_agent_limits(self, step: int = 0, seconds: int = 0, loop: int = 0) -> None:
        self._data["agent_step_limit"] = max(0, int(step))
        self._data["agent_seconds_limit"] = max(0, int(seconds))
        self._data["agent_loop_limit"] = max(0, int(loop))
        self._write()

    @property
    def extra_roots(self) -> list:
        roots = self._data.get("extra_roots") or []
        return [str(r) for r in roots if isinstance(r, str) and r.strip()]

    @property
    def floating_hud_position(self):
        data = self._data.get("floating_hud_position")
        if not isinstance(data, dict):
            return None
        try:
            return int(data["x"]), int(data["y"])
        except (KeyError, TypeError, ValueError):
            return None

    def save_floating_hud_position(self, x: int, y: int) -> None:
        self._data["floating_hud_position"] = {"x": int(x), "y": int(y)}
        self._write()

    def clear_floating_hud_position(self) -> None:
        if self._data.pop("floating_hud_position", None) is not None:
            self._write()

    @property
    def home_is_set(self) -> bool:
        data = self._data.get("home_location")
        if not isinstance(data, dict):
            return False
        try:
            lat, lon = float(data.get("lat")), float(data.get("lon"))
        except (TypeError, ValueError):
            return False
        return -90 <= lat <= 90 and -180 <= lon <= 180

    @property
    def home_location(self) -> dict:
        data = self._data.get("home_location")
        if not isinstance(data, dict):
            return dict(_DEFAULT_HOME)
        try:
            lat = float(data.get("lat"))
            lon = float(data.get("lon"))
        except (TypeError, ValueError):
            return dict(_DEFAULT_HOME)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return dict(_DEFAULT_HOME)
        name = str(data.get("name") or "").strip() or "Evim"
        region = str(data.get("region") or "").strip() or "Kocaeli"
        return {"name": name, "region": region, "lat": lat, "lon": lon}

    def set_home(self, lat: float, lon: float, name: str = "Evim", region: str = "Kocaeli") -> dict:
        lat, lon = float(lat), float(lon)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Ev konumu için geçerli enlem/boylam gerekli.")
        home = {
            "name": str(name or "").strip()[:80] or "Evim",
            "region": str(region or "").strip()[:80] or "Kocaeli",
            "lat": lat,
            "lon": lon,
        }
        self._data["home_location"] = home
        self._write()
        return dict(home)

    def set_access(self, mode: str, strict_after_web: bool = True) -> None:
        self._data["access_mode"] = mode if mode in ("approval", "step", "full") else "approval"
        self._data["strict_after_web"] = bool(strict_after_web)
        self._write()

    def add_root(self, path: str) -> None:
        roots = self.extra_roots
        if path not in roots:
            self._data["extra_roots"] = roots + [path]
            self._write()

    def remove_root(self, path: str) -> None:
        self._data["extra_roots"] = [r for r in self.extra_roots if r != path]
        self._write()

    def set_roots(self, roots: list) -> None:
        self._data["extra_roots"] = list(dict.fromkeys(str(path).strip() for path in roots if str(path).strip()))
        self._write()

    def save(
        self,
        api_key: str,
        model: str,
        provider: str = "gemini",
        ollama_url: str = DEFAULT_OLLAMA_URL,
        ollama_model: str = DEFAULT_OLLAMA_MODEL,
        tokenharbor_api_key: str = "",
        tokenharbor_model: str = "",
        tokenharbor_url: str = DEFAULT_TOKENHARBOR_URL,
    ) -> None:
        self._data["gemini_api_key"] = api_key.strip()
        self._data["gemini_model"] = model.strip() or DEFAULT_MODEL
        self._data["ai_provider"] = (
            provider if provider in ("gemini", "ollama", "tokenharbor") else "gemini"
        )
        self._data["ollama_url"] = ollama_url.strip().rstrip("/") or DEFAULT_OLLAMA_URL
        self._data["ollama_model"] = ollama_model.strip() or DEFAULT_OLLAMA_MODEL
        self._data["tokenharbor_api_key"] = tokenharbor_api_key.strip()
        self._data["tokenharbor_model"] = tokenharbor_model.strip()
        self._data["tokenharbor_url"] = (
            tokenharbor_url.strip().rstrip("/") or DEFAULT_TOKENHARBOR_URL
        )
        self._write()

    def set_model(self, model: str) -> None:
        """Jarvis uygun modeli kendisi bulduğunda yalnızca modeli günceller."""
        self._data["gemini_model"] = model.strip() or DEFAULT_MODEL
        self._write()
