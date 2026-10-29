"""HUD panelleri için sistem, hava, not ve takvim verisi."""

from __future__ import annotations

import heapq
import json
import math
import os
import platform
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal, Slot

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis" / "data"
WEATHER_CACHE = DATA_DIR / "weather.json"
HUD_STORE = DATA_DIR / "hud.json"

LOCATION = {"name": "Derince", "region": "Turkey", "lat": 40.757, "lon": 29.815}
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_TTL_SECONDS = 300
GEOCODE_TTL_SECONDS = 86_400

WEATHER_TEXT = {
    0: "Açık", 1: "Az bulutlu", 2: "Parçalı bulutlu", 3: "Bulutlu",
    45: "Sisli", 48: "Kırağılı sis",
    51: "Hafif çisenti", 53: "Çisenti", 55: "Yoğun çisenti",
    56: "Dondurucu çisenti", 57: "Yoğun dondurucu çisenti",
    61: "Hafif yağmur", 63: "Yağmur", 65: "Şiddetli yağmur",
    66: "Dondurucu yağmur", 67: "Şiddetli dondurucu yağmur",
    71: "Hafif kar", 73: "Kar", 75: "Yoğun kar", 77: "Kar taneleri",
    80: "Hafif sağanak", 81: "Sağanak", 82: "Şiddetli sağanak",
    85: "Kar sağanağı", 86: "Yoğun kar sağanağı",
    95: "Gök gürültülü fırtına", 96: "Dolulu fırtına", 99: "Şiddetli dolulu fırtına",
}
WEATHER_KIND = {
    0: "clear", 1: "partly", 2: "partly", 3: "cloud",
    45: "fog", 48: "fog",
    51: "rain", 53: "rain", 55: "rain", 56: "rain", 57: "rain",
    61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "rain", 81: "rain", 82: "rain", 85: "snow", 86: "snow",
    95: "storm", 96: "storm", 99: "storm",
}
WEEKDAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
MOON_PHASES = [
    "YENİ AY", "BÜYÜYEN HİLAL", "İLK DÖRDÜN", "BÜYÜYEN ŞİŞKİN AY",
    "DOLUNAY", "KÜÇÜLEN ŞİŞKİN AY", "SON DÖRDÜN", "KÜÇÜLEN HİLAL",
]
CARDINALS = ["K", "KKD", "KD", "DKD", "D", "DGD", "GD", "GGD", "G", "GGB", "GB", "BGB", "B", "BKB", "KB", "KKB"]


def wind_direction(degrees: Any) -> str:
    try:
        value = float(degrees) % 360
    except (TypeError, ValueError):
        return "—"
    return CARDINALS[int((value + 11.25) // 22.5) % 16]


def weather_text(code: Any) -> str:
    try:
        return WEATHER_TEXT.get(int(code), "Bilinmiyor")
    except (TypeError, ValueError):
        return "Bilinmiyor"


def weather_kind(code: Any) -> str:
    try:
        return WEATHER_KIND.get(int(code), "cloud")
    except (TypeError, ValueError):
        return "cloud"


def moon_phase(moment: Optional[datetime] = None) -> str:
    moment = moment or datetime.now()
    reference = datetime(2000, 1, 6, 18, 14)
    days = (moment - reference).total_seconds() / 86400.0
    fraction = (days % 29.530588853) / 29.530588853
    index = int((fraction * 8 + 0.5)) % 8
    return MOON_PHASES[index]


def turkish_date(moment: Optional[datetime] = None) -> str:
    moment = moment or datetime.now()
    return f"{WEEKDAYS[moment.weekday()]}, {moment.day} {MONTHS[moment.month - 1]}"


def _local_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def local_moment(value: Any) -> Optional[datetime]:
    """ISO metnini yerel, saat dilimi bilgisi olmayan datetime'a çevirir."""
    try:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is not None:
            moment = moment.astimezone().replace(tzinfo=None)
        return moment
    except (TypeError, ValueError):
        return None


_WIN_PROCESS_SAMPLER = None
_WIN_PROCESS_SAMPLER_READY = False


def _build_win_process_sampler():
    """Windows'ta süreçleri tek geçişte toplar; psutil'e göre ~100 kat hızlı."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        access = 0x1000 | 0x0400 | 0x0010  # QUERY_LIMITED | QUERY_INFORMATION | VM_READ
    except Exception:
        return None

    def sample():
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return None
        try:
            entry = ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            rows = []
            valid = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while valid:
                pid = entry.th32ProcessID
                name = entry.szExeFile
                if pid and name and not name.casefold().startswith("system idle"):
                    handle = kernel32.OpenProcess(access, False, pid)
                    if handle:
                        try:
                            memory = ProcessMemoryCounters()
                            memory.cb = ctypes.sizeof(memory)
                            rss = memory.WorkingSetSize if psapi.GetProcessMemoryInfo(
                                handle, ctypes.byref(memory), memory.cb
                            ) else 0
                            created, exited, kernel, user = (
                                wintypes.FILETIME(), wintypes.FILETIME(),
                                wintypes.FILETIME(), wintypes.FILETIME(),
                            )
                            ticks = 0
                            if kernel32.GetProcessTimes(
                                handle, ctypes.byref(created), ctypes.byref(exited),
                                ctypes.byref(kernel), ctypes.byref(user),
                            ):
                                ticks = (
                                    (kernel.dwHighDateTime << 32) + kernel.dwLowDateTime
                                    + (user.dwHighDateTime << 32) + user.dwLowDateTime
                                )
                            rows.append((name, pid, int(rss), ticks / 10_000_000.0))
                        finally:
                            kernel32.CloseHandle(handle)
                valid = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return rows
        finally:
            kernel32.CloseHandle(snapshot)

    return sample


def _win_process_table():
    global _WIN_PROCESS_SAMPLER, _WIN_PROCESS_SAMPLER_READY
    if not _WIN_PROCESS_SAMPLER_READY:
        _WIN_PROCESS_SAMPLER_READY = True
        _WIN_PROCESS_SAMPLER = _build_win_process_sampler()
    if _WIN_PROCESS_SAMPLER is None:
        return None
    try:
        return _WIN_PROCESS_SAMPLER()
    except Exception:
        return None


class SystemSampler:
    PROCESS_REFRESH_SECONDS = 8.0

    def __init__(self) -> None:
        self._last_net: Optional[tuple[float, int, int]] = None
        self._last_proc_sample = 0.0
        self._cached_processes: List[dict] = []
        self._proc_cpu: Dict[tuple, float] = {}

    def _network(self, psutil) -> Dict[str, Any]:
        counters = psutil.net_io_counters()
        now = time.monotonic()
        up_rate = down_rate = 0.0
        if self._last_net:
            previous, previous_up, previous_down = self._last_net
            elapsed = max(0.2, now - previous)
            up_rate = max(0.0, (counters.bytes_sent - previous_up) / elapsed)
            down_rate = max(0.0, (counters.bytes_recv - previous_down) / elapsed)
        self._last_net = (now, counters.bytes_sent, counters.bytes_recv)
        return {
            "up_rate": up_rate,
            "down_rate": down_rate,
            "total_up": counters.bytes_sent,
            "total_down": counters.bytes_recv,
        }

    def _processes(self, psutil, now: float) -> List[dict]:
        if self._cached_processes and now - self._last_proc_sample < self.PROCESS_REFRESH_SECONDS:
            return self._cached_processes
        elapsed = max(0.5, now - self._last_proc_sample) if self._last_proc_sample else 0.0
        cores = psutil.cpu_count() or 1
        rows = _win_process_table()
        if rows is not None:
            current_cpu: Dict[Any, float] = {}
            processes: List[dict] = []
            for name, pid, memory, total in rows:
                previous = self._proc_cpu.get(pid)
                cpu = max(0.0, (total - previous) / elapsed * 100.0) if previous is not None and elapsed else 0.0
                current_cpu[pid] = total
                processes.append({
                    "name": name,
                    "memory": memory,
                    "cpu": min(100.0, cpu / cores),
                })
            self._proc_cpu = current_cpu
            self._cached_processes = processes
            self._last_proc_sample = now
            return processes
        current_cpu = {}
        processes = []
        try:
            iterator = psutil.process_iter(["pid", "name", "memory_info", "cpu_times", "create_time"])
        except Exception:
            return self._cached_processes
        for process in iterator:
            try:
                info = process.info
                name = info.get("name")
                memory_info = info.get("memory_info")
                if not name or memory_info is None:
                    continue
                if (info.get("pid") or 0) == 0 or name.casefold().startswith("system idle"):
                    continue
                key = (info.get("pid"), round(float(info.get("create_time") or 0.0), 3))
                times = info.get("cpu_times")
                total = float(times.user + times.system) if times else 0.0
                previous = self._proc_cpu.get(key)
                cpu = max(0.0, (total - previous) / elapsed * 100.0) if previous is not None and elapsed else 0.0
                current_cpu[key] = total
                processes.append({
                    "name": name,
                    "memory": memory_info.rss,
                    "cpu": min(100.0, cpu / cores),
                })
            except Exception:
                continue
        self._proc_cpu = current_cpu
        self._cached_processes = processes
        self._last_proc_sample = now
        return processes

    def snapshot(self) -> Dict[str, Any]:
        try:
            import psutil
        except ImportError:
            return {"available": False, "error": "psutil kurulu değil"}
        cpu = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(Path.home().anchor or "C:\\")
        battery = psutil.sensors_battery()
        uptime = max(0, time.time() - psutil.boot_time())
        processes = self._processes(psutil, time.monotonic())
        top_memory = heapq.nlargest(7, processes, key=lambda item: item["memory"])
        top_cpu = heapq.nlargest(7, processes, key=lambda item: item["cpu"])
        return {
            "available": True,
            "cpu": cpu,
            "cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
            "threads": psutil.cpu_count() or 1,
            "cpu_name": platform.processor() or "İşlemci",
            "host": platform.node() or "BILGISAYAR",
            "os": f"{platform.system()} {platform.release()} {platform.version()}",
            "memory_percent": memory.percent,
            "memory_used": memory.used,
            "memory_total": memory.total,
            "disk_percent": disk.percent,
            "disk_used": disk.used,
            "disk_total": disk.total,
            "battery_percent": battery.percent if battery else None,
            "battery_plugged": battery.power_plugged if battery else None,
            "uptime": uptime,
            "network": self._network(psutil),
            "processes": top_memory,
            "top_memory": top_memory,
            "top_cpu": top_cpu,
        }

    @staticmethod
    def uptime_text(seconds: float) -> str:
        seconds = int(max(0, seconds))
        days, rest = divmod(seconds, 86400)
        hours, rest = divmod(rest, 3600)
        minutes = rest // 60
        if days:
            return f"{days} GÜN {hours} SA {minutes} DK"
        return f"{hours} SA {minutes} DK"

    @staticmethod
    def bytes_text(value: float) -> str:
        value = float(max(0, value))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024


class WeatherService:
    def __init__(self, settings: Any = None) -> None:
        self.settings = settings
        self.cached = self._load_cache()
        self._geo_cache: Dict[str, tuple[float, dict]] = {}

    def _location(self, city: str = "") -> dict:
        city = " ".join(str(city or "").split())
        if city:
            return self._geocode(city)
        if self.settings is not None:
            try:
                home = self.settings.home_location
                return {
                    "name": str(home.get("name") or "Evim"),
                    "region": str(home.get("region") or "Kocaeli"),
                    "lat": float(home.get("lat")),
                    "lon": float(home.get("lon")),
                }
            except (AttributeError, TypeError, ValueError):
                pass
        return dict(LOCATION)

    def _geocode(self, city: str) -> dict:
        if len(city) > 80:
            raise ValueError("Şehir adı çok uzun.")
        key = city.casefold()
        cached = self._geo_cache.get(key)
        if cached and time.time() - cached[0] < GEOCODE_TTL_SECONDS:
            return dict(cached[1])
        import requests

        response = requests.get(
            GEOCODE_URL,
            params={"name": city, "count": 1, "language": "tr", "format": "json"},
            timeout=(5, 10),
            headers={"User-Agent": "Jarvis-HUD/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None
        if not result:
            raise ValueError(f"'{city}' için konum bulunamadı; şehir adını kontrol et.")
        try:
            lat = float(result.get("latitude"))
            lon = float(result.get("longitude"))
        except (TypeError, ValueError):
            raise ValueError(f"'{city}' için geçerli koordinat alınamadı.")
        location = {
            "name": str(result.get("name") or city),
            "region": str(result.get("admin1") or result.get("country") or "").strip(),
            "lat": lat,
            "lon": lon,
        }
        self._geo_cache[key] = (time.time(), location)
        if len(self._geo_cache) > 64:
            oldest = min(self._geo_cache.items(), key=lambda item: item[1][0])[0]
            self._geo_cache.pop(oldest, None)
        return dict(location)

    def _cache_matches_location(self, location: dict) -> bool:
        if not isinstance(self.cached, dict):
            return False
        try:
            return (
                abs(float(self.cached.get("lat")) - location["lat"]) < 0.02
                and abs(float(self.cached.get("lon")) - location["lon"]) < 0.02
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _load_cache() -> Optional[dict]:
        try:
            return json.loads(WEATHER_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save_cache(self, data: dict) -> None:
        try:
            WEATHER_CACHE.parent.mkdir(parents=True, exist_ok=True)
            WEATHER_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _fresh_cache(self, location: dict) -> Optional[dict]:
        if not self._cache_matches_location(location):
            return None
        try:
            age = time.time() - float(self.cached.get("fetched_at"))
        except (TypeError, ValueError):
            return None
        if age < 0 or age > WEATHER_TTL_SECONDS:
            return None
        data = dict(self.cached)
        data["stale"] = False
        data["cached"] = True
        return data

    def fetch(self, city: str = "") -> dict:
        import requests

        location = self._location(city)
        disk = self._load_cache()
        if disk is not None:
            self.cached = disk
        fresh = self._fresh_cache(location)
        if fresh is not None:
            return fresh
        params = {
            "latitude": location["lat"],
            "longitude": location["lon"],
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
                "weather_code,cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,visibility"
            ),
            "hourly": "temperature_2m,precipitation_probability,weather_code,uv_index",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max",
            "timezone": "auto",
            "forecast_days": 3,
        }
        try:
            response = requests.get(
                WEATHER_URL,
                params=params,
                timeout=(5, 12),
                headers={"User-Agent": "Jarvis-HUD/1.0"},
            )
            response.raise_for_status()
            raw = response.json()
            data = self._parse(raw, location)
            data["stale"] = False
            self.cached = data
            self._save_cache(data)
            return data
        except Exception as error:
            if self.cached and self._cache_matches_location(location):
                stale = dict(self.cached)
                stale["stale"] = True
                stale["error"] = str(error)
                return stale
            raise

    @staticmethod
    def _parse(raw: dict, location: Optional[dict] = None) -> dict:
        location = dict(location or LOCATION)
        label = f"{location['name']} · {location['region']}" if location.get("region") else str(location.get("name") or "Konum")
        current = raw.get("current") or {}
        hourly = raw.get("hourly") or {}
        daily = raw.get("daily") or {}
        now = datetime.now()
        times = [_local_iso(value) for value in hourly.get("time") or []]
        start = 0
        for index, moment in enumerate(times):
            if moment and moment >= now.replace(minute=0, second=0, microsecond=0):
                start = index
                break
        hours = []
        for index in range(start, min(start + 8, len(times))):
            moment = times[index]
            if moment is None:
                continue
            hours.append({
                "label": "Şimdi" if moment.hour == now.hour and moment.day == now.day else moment.strftime("%H:%M"),
                "temp": round(float((hourly.get("temperature_2m") or [0])[index])),
                "precip": int((hourly.get("precipitation_probability") or [0])[index] or 0),
                "code": (hourly.get("weather_code") or [0])[index],
            })
        days = []
        for index, value in enumerate((daily.get("time") or [])[:3]):
            moment = _local_iso(value)
            if moment is None:
                continue
            days.append({
                "label": "Bugün" if index == 0 else ("Yarın" if index == 1 else WEEKDAYS[moment.weekday()]),
                "date": f"{moment.day} {MONTHS[moment.month - 1]}",
                "max": round(float((daily.get("temperature_2m_max") or [0])[index])),
                "min": round(float((daily.get("temperature_2m_min") or [0])[index])),
                "code": (daily.get("weather_code") or [0])[index],
                "kind": weather_kind((daily.get("weather_code") or [0])[index]),
                "text": weather_text((daily.get("weather_code") or [0])[index]),
            })
        code = current.get("weather_code", 0)
        visibility = current.get("visibility")
        return {
            "updated": now.strftime("%H:%M"),
            "fetched_at": time.time(),
            "location": label,
            "lat": location["lat"],
            "lon": location["lon"],
            "current": {
                "temp": round(float(current.get("temperature_2m") or 0)),
                "feels": round(float(current.get("apparent_temperature") or 0)),
                "humidity": int(current.get("relative_humidity_2m") or 0),
                "wind": round(float(current.get("wind_speed_10m") or 0)),
                "wind_dir": wind_direction(current.get("wind_direction_10m")),
                "pressure": round(float(current.get("pressure_msl") or 0)),
                "visibility": round(float(visibility) / 1000, 1) if visibility is not None else None,
                "uv": round(float((daily.get("uv_index_max") or [0])[0] or 0)),
                "cloud": int(current.get("cloud_cover") or 0),
                "precip": float(current.get("precipitation") or 0),
                "code": code,
                "text": weather_text(code),
                "kind": weather_kind(code),
                "sunrise": str((daily.get("sunrise") or [""])[0])[11:16],
                "sunset": str((daily.get("sunset") or [""])[0])[11:16],
                "moon": moon_phase(now),
            },
            "hourly": hours,
            "daily": days,
        }


class WeatherWorker(QObject):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, service: WeatherService) -> None:
        super().__init__()
        self.service = service

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self.service.fetch())
        except Exception as error:
            self.error.emit(str(error))


class HudStore:
    """Notlar, hatırlatıcılar ve takvim etkinlikleri için küçük yerel depo."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else HUD_STORE
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("notes", "events", "reminders"):
                    if not isinstance(data.get(key), list):
                        data[key] = []
                return data
        except (OSError, ValueError):
            pass
        return {"notes": [], "events": [], "reminders": []}

    def save(self) -> None:
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def add_note(self, text: str) -> dict:
        item = {
            "id": str(time.time_ns()),
            "text": text.strip(),
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            self.data["notes"].append(item)
            self.save()
        return item

    def delete_note(self, note_id: str) -> None:
        with self._lock:
            self.data["notes"] = [item for item in self.data["notes"] if item.get("id") != note_id]
            self.save()

    def update_note(self, note_id: str, text: str) -> None:
        with self._lock:
            for item in self.data["notes"]:
                if item.get("id") == note_id:
                    item["text"] = text.strip()
                    item["edited"] = datetime.now().isoformat(timespec="seconds")
                    break
            self.save()

    def add_event(self, title: str, when: str) -> dict:
        item = {
            "id": str(time.time_ns()),
            "title": title.strip(),
            "when": when,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            self.data["events"].append(item)
            self.save()
        return item

    def delete_event(self, event_id: str) -> bool:
        with self._lock:
            before = len(self.data["events"])
            self.data["events"] = [item for item in self.data["events"] if item.get("id") != event_id]
            changed = len(self.data["events"]) != before
            if changed:
                self.save()
        return changed

    def notes(self) -> List[dict]:
        with self._lock:
            return list(self.data.get("notes") or [])

    def events(self) -> List[dict]:
        with self._lock:
            return list(self.data.get("events") or [])

    def upcoming_events(self) -> List[dict]:
        upcoming = []
        for event in self.events():
            moment = local_moment(event.get("when"))
            if moment is None or moment >= datetime.now() - timedelta(minutes=1):
                upcoming.append(event)
        return sorted(upcoming, key=lambda item: str(item.get("when", "")))

    # -- hatırlatıcılar -----------------------------------------------------
    def add_reminder(self, text: str, when: str) -> dict:
        text = str(text or "").strip()
        moment = local_moment(when)
        if not text:
            raise ValueError("Hatırlatıcı metni boş olamaz.")
        if moment is None:
            raise ValueError("Hatırlatıcı için geçerli bir tarih/saat gerekli.")
        item = {
            "id": str(time.time_ns()),
            "text": text[:1000],
            "when": moment.replace(second=0, microsecond=0).isoformat(timespec="minutes"),
            "created": datetime.now().isoformat(timespec="seconds"),
            "notified": False,
            "done": False,
        }
        with self._lock:
            self.data["reminders"].append(item)
            self.save()
        return dict(item)

    def delete_reminder(self, reminder_id: str) -> bool:
        with self._lock:
            before = len(self.data["reminders"])
            self.data["reminders"] = [
                item for item in self.data["reminders"] if item.get("id") != reminder_id
            ]
            changed = len(self.data["reminders"]) != before
            if changed:
                self.save()
        return changed

    def complete_reminder(self, reminder_id: str) -> bool:
        with self._lock:
            for item in self.data["reminders"]:
                if item.get("id") == reminder_id:
                    item["done"] = True
                    item["completed"] = datetime.now().isoformat(timespec="seconds")
                    self.save()
                    return True
        return False

    def mark_reminder_notified(self, reminder_id: str) -> bool:
        with self._lock:
            for item in self.data["reminders"]:
                if item.get("id") == reminder_id:
                    item["notified"] = True
                    item["notified_at"] = datetime.now().isoformat(timespec="seconds")
                    self.save()
                    return True
        return False

    def clear_completed_reminders(self) -> int:
        with self._lock:
            before = len(self.data["reminders"])
            self.data["reminders"] = [
                item for item in self.data["reminders"] if not item.get("done")
            ]
            removed = before - len(self.data["reminders"])
            if removed:
                self.save()
        return removed

    def reminders(self) -> List[dict]:
        with self._lock:
            return list(self.data.get("reminders") or [])

    def pending_reminders(self) -> List[dict]:
        pending = [item for item in self.reminders() if not item.get("done")]
        return sorted(
            pending,
            key=lambda item: (
                local_moment(item.get("when")) is None,
                local_moment(item.get("when")) or datetime.max,
            ),
        )

    def due_reminders(self, now: Optional[datetime] = None) -> List[dict]:
        now = now or datetime.now()
        due = []
        for item in self.reminders():
            if item.get("done") or item.get("notified"):
                continue
            moment = local_moment(item.get("when"))
            if moment is not None and moment <= now:
                due.append(item)
        return sorted(due, key=lambda item: str(item.get("when", "")))

    def upcoming_reminders(self, limit: int = 3) -> List[dict]:
        return self.pending_reminders()[: max(1, int(limit))]


class TimerEngine(QObject):
    """HUD zamanlayıcıları; bitiş zamanını QTimer ile izler."""

    changed = Signal()
    expired = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.items: List[dict] = []
        self._counter = 0

    def add(self, minutes: int, label: str = "") -> dict:
        self._counter += 1
        item = {
            "id": self._counter,
            "label": label.strip() or f"{minutes} dk",
            "end": time.monotonic() + max(1, int(minutes)) * 60,
            "minutes": max(1, int(minutes)),
        }
        self.items.append(item)
        self.changed.emit()
        return item

    def remove(self, timer_id: int) -> None:
        self.items = [item for item in self.items if item.get("id") != timer_id]
        self.changed.emit()

    def clear(self) -> None:
        self.items = []
        self.changed.emit()

    def tick(self) -> None:
        now = time.monotonic()
        active = []
        expired = []
        for item in self.items:
            if item["end"] <= now:
                expired.append(item)
            else:
                active.append(item)
        if expired:
            self.items = active
            for item in expired:
                self.expired.emit(str(item["label"]))
            self.changed.emit()

    def remaining(self, item: dict) -> int:
        return max(0, int(math.ceil(item["end"] - time.monotonic())))


def format_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
