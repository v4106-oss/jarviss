"""Akıllı ev simülasyonu: ışıklar ve kahve makineleri; bulut veya ağ kullanmaz."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

TURK_BREW_SECONDS = 75
FILTER_SECONDS_PER_CUP = 40

TONES = {"sicak": "Sıcak", "notr": "Nötr", "soguk": "Soğuk"}
FOAMS = {"az": "Az köpüklü", "orta": "Orta köpüklü", "bol": "Bol köpüklü"}
SUGARS = {"sade": "Sade", "az": "Az şekerli", "orta": "Orta şekerli", "cok": "Çok şekerli"}
LIGHT_ROOMS = {"salon": "Salon ışığı", "calisma": "Çalışma odası ışığı"}
COFFEE_NAMES = {"turk": "Türk kahvesi", "filtre": "Filtre kahve"}

_TURKISH_NUMBERS = {
    "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5,
    "on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50,
    "altmis": 60, "yetmis": 70, "seksen": 80, "doksan": 90, "yuz": 100,
}


def fold_turkish(text: str) -> str:
    folded = str(text or "").casefold()
    for source, target in (
        ("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"),
        ("ö", "o"), ("ç", "c"), ("â", "a"), ("î", "i"), ("û", "u"),
    ):
        folded = folded.replace(source, target)
    return re.sub(r"[^a-z0-9%]+", " ", folded).strip()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _number(text: str) -> Optional[int]:
    match = re.search(r"\b(\d{1,3})\b", text)
    if match is not None:
        return int(match.group(1))
    match = re.search(r"yuzde\s+([a-z0-9 ]+)", text)
    if match is None:
        match = re.search(r"%\s*([a-z0-9 ]+)", text)
    if match is not None:
        raw = match.group(1).strip()
        if raw.isdigit():
            return int(raw)
        total = 0
        for word in raw.split():
            value = _TURKISH_NUMBERS.get(word)
            if value is None:
                if total:
                    break
                continue
            total += value
        return total or None
    for word, value in _TURKISH_NUMBERS.items():
        if re.search(rf"\b{word}\b", text):
            return value
    return None


@dataclass
class Light:
    key: str
    name: str
    on: bool = False
    brightness: int = 70
    tone: str = "notr"

    def snapshot(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "on": self.on,
            "brightness": self.brightness,
            "tone": self.tone,
            "tone_label": TONES.get(self.tone, TONES["notr"]),
        }

    def describe(self) -> str:
        if not self.on:
            return f"{self.name} kapalı."
        return f"{self.name} açık · %{self.brightness} · {TONES.get(self.tone, 'Nötr')}."


@dataclass
class Coffee:
    kind: str
    cups: int = 1
    foam: str = "orta"
    sugar: str = "orta"
    started_at: Optional[float] = None
    ready_at: Optional[float] = None
    ready: bool = False
    brewed_at: Optional[float] = None
    keep_warm: bool = True

    @property
    def name(self) -> str:
        return COFFEE_NAMES.get(self.kind, "Kahve")

    def snapshot(self, now: float) -> dict:
        status = "bos"
        remaining = 0
        if self.started_at is not None and not self.ready:
            status = "demleniyor"
            remaining = max(0, round((self.ready_at or now) - now))
        elif self.ready:
            status = "hazir"
        return {
            "kind": self.kind,
            "name": self.name,
            "cups": self.cups,
            "foam": self.foam,
            "foam_label": FOAMS.get(self.foam, FOAMS["orta"]),
            "sugar": self.sugar,
            "sugar_label": SUGARS.get(self.sugar, SUGARS["orta"]),
            "status": status,
            "remaining": remaining,
            "ready": self.ready,
            "keep_warm": self.keep_warm,
        }

    def describe(self, now: float) -> str:
        if self.started_at is not None and not self.ready:
            remaining = max(0, round((self.ready_at or now) - now))
            extra = ""
            if self.kind == "filtre":
                extra = f" · {self.cups} fincan"
            return f"{self.name} demleniyor · {remaining} sn{extra}"
        if self.ready:
            if self.kind == "turk":
                detail = f"{FOAMS.get(self.foam, FOAMS['orta'])}, {SUGARS.get(self.sugar, SUGARS['orta']).lower()}"
            else:
                detail = f"{self.cups} fincan"
                if self.keep_warm:
                    detail += " · sıcak tutuluyor"
            return f"{self.name} hazır · {detail}"
        return f"{self.name} makinesi boş."


class SmartHome:
    """Cihaz durumlarını tutar; kahve süreleri gerçek zamanla ilerler."""

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock or time.monotonic
        # Arayüz zamanlayıcısı ve ajan çalışanı aynı ev durumunu paylaşır.
        self._lock = threading.RLock()
        self.lights = {key: Light(key, name) for key, name in LIGHT_ROOMS.items()}
        self.coffees = {"turk": Coffee("turk"), "filtre": Coffee("filtre", cups=2)}

    def _now(self) -> float:
        return float(self._clock())

    def now(self) -> float:
        return self._now()

    def light(self, key: str) -> Light:
        return self.lights.get(key) or self.lights["salon"]

    def set_light(
        self,
        key: str,
        on: Optional[bool] = None,
        brightness: Optional[int] = None,
        tone: Optional[str] = None,
    ) -> Light:
        with self._lock:
            light = self.light(key)
            if brightness is not None:
                light.brightness = int(_clamp(int(brightness), 0, 100))
                if on is None:
                    light.on = light.brightness > 0
            if tone in TONES:
                light.tone = tone
            if on is not None:
                light.on = bool(on)
            return light

    def set_all_lights(
        self,
        on: Optional[bool] = None,
        brightness: Optional[int] = None,
        tone: Optional[str] = None,
    ) -> list[Light]:
        with self._lock:
            return [self.set_light(key, on=on, brightness=brightness, tone=tone) for key in self.lights]

    def brew(
        self,
        kind: str,
        cups: Optional[int] = None,
        foam: Optional[str] = None,
        sugar: Optional[str] = None,
    ) -> tuple[bool, Coffee, float]:
        with self._lock:
            kind = kind if kind in self.coffees else "turk"
            coffee = self.coffees[kind]
            now = self._now()
            self._advance(coffee, now)
            if coffee.started_at is not None and not coffee.ready:
                return False, coffee, 0.0
            if kind == "filtre":
                coffee.cups = int(_clamp(int(cups or coffee.cups or 2), 1, 4))
                seconds = FILTER_SECONDS_PER_CUP * coffee.cups
            else:
                coffee.cups = 1
                seconds = TURK_BREW_SECONDS
                if foam in FOAMS:
                    coffee.foam = foam
                if sugar in SUGARS:
                    coffee.sugar = sugar
            coffee.started_at = now
            coffee.ready_at = now + seconds
            coffee.ready = False
            coffee.brewed_at = None
            return True, coffee, seconds

    def coffee(self, kind: str) -> Coffee:
        with self._lock:
            coffee = self.coffees.get(kind) or self.coffees["turk"]
            self._advance(coffee, self._now())
            return coffee

    def cancel_coffee(self, kind: str) -> Coffee:
        with self._lock:
            coffee = self.coffees.get(kind) or self.coffees["turk"]
            coffee.started_at = None
            coffee.ready_at = None
            coffee.ready = False
            coffee.brewed_at = None
            return coffee

    def _advance(self, coffee: Coffee, now: float) -> None:
        if coffee.started_at is None or coffee.ready:
            return
        if coffee.ready_at is not None and now >= coffee.ready_at:
            coffee.ready = True
            coffee.started_at = None
            coffee.brewed_at = coffee.ready_at

    def snapshot(self) -> dict:
        with self._lock:
            now = self._now()
            return {
                "lights": {key: light.snapshot() for key, light in self.lights.items()},
                "coffees": {key: coffee.snapshot(now) for key, coffee in self.coffees.items()},
                "now": now,
            }


def home_voice_intent(text: str) -> Optional[dict]:
    """Akıllı ev komutlarını yerel sınıflandırır; belirsiz metin AI'ya kalır."""
    normalized = fold_turkish(text)
    if not normalized:
        return None
    words = normalized.split()
    padded = f" {normalized} "
    if any(connector in padded for connector in (" sonra ", " ardindan ")):
        return None

    if any(phrase in normalized for phrase in ("ev panel", "akilli ev panel", "akilli evi ac", "evi yonet")):
        return {"action": "panel"}

    rooms = []
    if "salon" in normalized or "oturma odasi" in normalized:
        rooms.append("salon")
    if "calisma" in normalized or "ofis" in normalized:
        rooms.append("calisma")

    coffee_request = any(root in normalized for root in ("kahve", "kahvem", "fincan"))
    light_request = any(root in normalized for root in ("isik", "isig", "aydinlat", "lamba"))
    if not light_request and rooms and any(
        root in normalized for root in ("parlak", "yuzde", "%", "sicak", "soguk", "notr")
    ):
        light_request = True
    if not light_request and not coffee_request:
        return None

    if light_request and coffee_request:
        return None

    if coffee_request:
        kind = "filtre" if "filtre" in normalized else "turk"
        if any(word.startswith(("hazir", "durum", "bitti", "tamam")) for word in words) or "ne zaman" in normalized:
            return {"action": "coffee_status", "kind": kind}
        if any(word.startswith(("iptal", "durdur", "bosalt")) for word in words):
            return {"action": "coffee_cancel", "kind": kind}
        if any(word.startswith(("yap", "demle", "pisir", "hazirla", "koy")) for word in words):
            intent = {"action": "coffee_brew", "kind": kind}
            number = _number(normalized)
            if kind == "filtre":
                intent["cups"] = int(_clamp(number or 2, 1, 4))
            if "bol" in words:
                intent["foam"] = "bol"
            elif "az" in words:
                intent["foam"] = "az"
            elif "orta" in words:
                intent["foam"] = "orta"
            if "sade" in words:
                intent["sugar"] = "sade"
            elif "sekerli" in words or "seker" in words:
                if "cok" in words:
                    intent["sugar"] = "cok"
                elif "az" in words:
                    intent["sugar"] = "az"
                else:
                    intent["sugar"] = "orta"
            return intent
        if "hazir mi" in normalized or "demleniyor mu" in normalized:
            return {"action": "coffee_status", "kind": kind}
        return None

    if "tum" in words or "butun" in words or "hepsi" in words:
        room = "all"
    elif len(rooms) > 1:
        room = "all"
    elif rooms:
        room = rooms[0]
    else:
        room = "all" if any(word.startswith("isiklar") for word in words) else "salon"

    if "acik mi" in normalized or "kapali mi" in normalized or "isiklar nasil" in normalized:
        return {"action": "light_status", "room": room}

    on: Optional[bool] = None
    if any(
        word.startswith("kapat") or word == "kapa" or word.startswith("sondur")
        for word in words
    ):
        on = False
    elif any(
        word.startswith("ac") and not word.startswith(("acik", "acaba", "acikl"))
        for word in words
    ):
        on = True

    brightness = _number(normalized) if any(root in normalized for root in ("parlak", "yuzde", "%")) else None
    tone = None
    for key in TONES:
        if key in words or (key == "notr" and "notr" in normalized):
            tone = key
    if on is None and brightness is None and tone is None:
        return None
    return {
        "action": "light",
        "room": room,
        "on": on,
        "brightness": int(_clamp(brightness, 0, 100)) if brightness is not None else None,
        "tone": tone,
    }
