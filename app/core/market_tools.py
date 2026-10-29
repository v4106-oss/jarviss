"""TCMB döviz kurları: son değer, önceki iş gününe göre değişim.

TCMB'nin herkese açık XML bültenleri kullanılır; API anahtarı gerekmez.
Ağ hatasında son başarılı değer "önbellek" olarak işaretlenerek döndürülür.
"""

from __future__ import annotations

import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import requests

TODAY_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
ARCHIVE_URL = "https://www.tcmb.gov.tr/kurlar/{year}{month:02d}/{day:02d}{month:02d}{year}.xml"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JarvisMarkets/1.0"
TIMEOUT = (8, 25)
TODAY_CACHE_SECONDS = 1_800
HISTORY_CACHE_SECONDS = 21_600
TRACKED = ("USD", "EUR", "GBP")
MAX_BYTES = 1_000_000
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis" / "data"
MARKET_CACHE = DATA_DIR / "market.json"


def _parse_date(value: Any) -> Optional[date]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _number(value: Any) -> Optional[float]:
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _download(url: str) -> bytes:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    response.raise_for_status()
    raw = response.content
    if len(raw) > MAX_BYTES:
        raise ValueError("TCMB bülteni beklenenden büyük.")
    return raw


def parse_bulletin(raw: bytes) -> Tuple[Optional[date], Dict[str, dict]]:
    root = ET.fromstring(raw)
    bulletin_date = None
    stamp = str(root.attrib.get("Tarih") or "").strip()
    if stamp:
        try:
            bulletin_date = datetime.strptime(stamp, "%d.%m.%Y").date()
        except ValueError:
            bulletin_date = None
    values: Dict[str, dict] = {}
    for node in root.iter():
        if _local(node.tag) != "currency":
            continue
        code = str(node.attrib.get("CurrencyCode") or node.attrib.get("Kod") or "").upper()
        if not code:
            continue
        fields = {_local(child.tag): "".join(child.itertext()).strip() for child in node}
        unit = _number(fields.get("unit")) or 1.0
        selling = _number(fields.get("forexselling")) or _number(fields.get("banknoteselling"))
        if selling is None:
            continue
        values[code] = {
            "code": code,
            "name": fields.get("isim") or fields.get("currencyname") or code,
            "unit": unit,
            "selling": selling,
        }
    return bulletin_date, values


def _previous_weekday(day: date) -> date:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


class MarketService:
    def __init__(
        self,
        fetcher: Optional[Callable[[str], bytes]] = None,
        cache_path: Optional[Path] = None,
    ) -> None:
        self._fetch = fetcher or _download
        self.cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.Lock()
        self._today: Optional[dict] = None
        self._history: Dict[date, Tuple[float, Dict[str, dict]]] = {}
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        data = payload.get("today") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("values"), dict) or not data["values"]:
            return
        try:
            at = float(data.get("at"))
        except (TypeError, ValueError):
            return
        day = _parse_date(data.get("date")) or date.today()
        previous = data.get("previous") if isinstance(data.get("previous"), dict) else {}
        previous_day = _parse_date(data.get("previous_date"))
        with self._lock:
            self._today = {
                "at": at,
                "date": day,
                "values": data["values"],
                "previous_date": previous_day,
                "previous": previous,
                "stale": bool(data.get("stale")),
            }

    def _save_disk_cache(self, data: dict) -> None:
        if self.cache_path is None:
            return
        payload = {
            "version": 1,
            "today": {
                "at": data.get("at"),
                "date": data["date"].isoformat() if isinstance(data.get("date"), date) else None,
                "values": data.get("values") or {},
                "previous_date": (
                    data["previous_date"].isoformat()
                    if isinstance(data.get("previous_date"), date)
                    else None
                ),
                "previous": data.get("previous") or {},
                "stale": bool(data.get("stale")),
            },
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.cache_path)
        except OSError:
            pass

    def _load_previous(self, day: date) -> Tuple[Optional[date], Dict[str, dict]]:
        candidate = _previous_weekday(day)
        for _ in range(7):
            cached = self._history.get(candidate)
            if cached and time.time() - cached[0] < HISTORY_CACHE_SECONDS:
                return candidate, cached[1]
            url = ARCHIVE_URL.format(year=candidate.year, month=candidate.month, day=candidate.day)
            try:
                bulletin_date, values = parse_bulletin(self._fetch(url))
            except Exception:
                values = {}
                bulletin_date = None
            if values:
                actual = bulletin_date or candidate
                self._history[actual] = (time.time(), values)
                return actual, values
            candidate = _previous_weekday(candidate)
        return None, {}

    def snapshot(self) -> dict:
        with self._lock:
            cached = self._today
            if cached and time.time() - cached["at"] < TODAY_CACHE_SECONDS:
                return cached
            try:
                bulletin_date, values = parse_bulletin(self._fetch(TODAY_URL))
            except Exception as error:
                if cached:
                    stale = dict(cached)
                    stale["stale"] = True
                    stale["error"] = str(error)
                    return stale
                raise
            if not values:
                raise ValueError("TCMB bülteninde döviz satış değeri bulunamadı.")
            day = bulletin_date or date.today()
            previous_date, previous = self._load_previous(day)
            data = {
                "at": time.time(),
                "date": day,
                "values": values,
                "previous_date": previous_date,
                "previous": previous,
                "stale": False,
            }
            self._today = data
            self._save_disk_cache(data)
            return data

    def get(self) -> str:
        try:
            data = self.snapshot()
        except Exception as error:
            return f"❌ TCMB döviz verisi alınamadı: {error}. Kur uydurma; kullanıcıya bağlantı sorununu söyle."
        values = data.get("values") or {}
        previous = data.get("previous") or {}
        day = data.get("date")
        lines = [
            "✅ TCMB döviz satış kurları"
            + (f" · {day:%d.%m.%Y}" if isinstance(day, date) else "")
            + (" · ÖNBELLEK" if data.get("stale") else ""),
        ]
        for code in TRACKED:
            row = values.get(code)
            if not row:
                continue
            unit = float(row.get("unit") or 1)
            unit_text = f"{unit:.0f} " if unit != 1 else ""
            value = float(row["selling"])
            line = f"{unit_text}{code} · {value:.4f} ₺".replace(".", ",")
            old = previous.get(code)
            if old and old.get("selling"):
                old_value = float(old["selling"])
                change = (value - old_value) / old_value * 100 if old_value else 0
                sign = "+" if change >= 0 else ""
                line += f" · {sign}{change:.2f}".replace(".", ",") + "%"
                previous_day = data.get("previous_date")
                if isinstance(previous_day, date):
                    old_text = f"{old_value:.4f}".replace(".", ",")
                    line += f" (önceki iş günü {previous_day:%d.%m.%Y}: {old_text} ₺)"
            lines.append(line)
        if len(lines) == 1:
            return "❌ TCMB bülteninde USD/EUR/GBP satış kuru bulunamadı; kur uydurma."
        lines.append("Kaynak: TCMB (tcmb.gov.tr/kurlar)")
        return "\n".join(lines)


_default_service: Optional[MarketService] = None
_default_lock = threading.Lock()


def shared_market_service() -> MarketService:
    """Aynı süreçteki tüm ajan turlarının önbelleği paylaşmasını sağlar."""
    global _default_service
    if _default_service is None:
        with _default_lock:
            if _default_service is None:
                _default_service = MarketService(cache_path=MARKET_CACHE)
    return _default_service
