"""Ücretsiz harita, rota, mekân ve Kocaeli toplu taşıma araçları.

Harita karoları ve arama OpenStreetMap, rota OSRM, mekânlar Overpass; Kocaeli
otobüs verisi resmî e-komobil servisinden okunur. API anahtarı gerekmez.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from difflib import SequenceMatcher
from html import unescape
from typing import Callable, Optional
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import requests

from app.core.poi_store import OVERTURE_RELEASE, PoiStore

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OSM_API_URL = "https://api.openstreetmap.org/api/0.6/map"
OSRM_URLS = {
    "driving": "https://router.project-osrm.org/route/v1/driving",
    "walking": "https://routing.openstreetmap.de/routed-foot/route/v1/driving",
    "cycling": "https://routing.openstreetmap.de/routed-bike/route/v1/driving",
}
MODE_LABELS = {"driving": "Araç", "walking": "Yaya", "cycling": "Bisiklet"}
KOCAELI_LINES_URL = "https://www.kocaeli.bel.tr/hatlar/"
EKOMOBIL_BASE = "https://e-komobil.com/"
EKOMOBIL_OPS = EKOMOBIL_BASE + "yolcu_bilgilendirme_operations.php"
EKOMOBIL_NEAREST = EKOMOBIL_BASE + "yakin_duraklar.php"
EKOMOBIL_SECRET = "ekomobil_web_2024_secure_key"
USER_AGENT = "JarvisDesktop/1.0 (Windows desktop assistant; keyless OSM client)"

DEFAULT_HOME = {"name": "Derince", "region": "Kocaeli", "lat": 40.757, "lon": 29.815}

CLICK_POI_KEYS = (
    "shop", "amenity", "tourism", "leisure", "office", "craft", "historic",
    "natural", "military", "government", "building", "landuse", "man_made",
    "place",
)

_MODE_ALIASES = {
    "driving": ("driving", "drive", "car", "arac", "araç", "araba", "otomobil"),
    "walking": ("walking", "walk", "foot", "yaya", "yuruyerek", "yürüyerek", "yürüyüş"),
    "cycling": ("cycling", "cycle", "bike", "bisiklet", "bisikletle"),
}

_CATEGORY_TAGS = (
    ("alışveriş merkezi", ("shop=mall",)),
    ("alisveris merkezi", ("shop=mall",)),
    ("avm", ("shop=mall", "shop=department_store")),
    ("supermarket", ("shop=supermarket", "shop=convenience")),
    ("süpermarket", ("shop=supermarket", "shop=convenience")),
    ("market", ("shop=supermarket", "shop=convenience", "shop=grocery")),
    ("bakkal", ("shop=convenience",)),
    ("eczane", ("amenity=pharmacy",)),
    ("eczacı", ("amenity=pharmacy",)),
    ("kafe", ("amenity=cafe",)),
    ("kahve", ("amenity=cafe",)),
    ("restoran", ("amenity=restaurant", "amenity=fast_food")),
    ("lokanta", ("amenity=restaurant",)),
    ("yemek", ("amenity=restaurant", "amenity=fast_food")),
    ("fırın", ("shop=bakery",)),
    ("firin", ("shop=bakery",)),
    ("kasap", ("shop=butcher",)),
    ("manav", ("shop=greengrocer",)),
    ("şarküteri", ("shop=deli",)),
    ("giyim", ("shop=clothes", "shop=shoes", "shop=fashion")),
    ("mağaza", ("shop=clothes", "shop=shoes", "shop=department_store")),
    ("elektronik", ("shop=electronics", "shop=computer", "shop=mobile_phone")),
    ("kitap", ("shop=books",)),
    ("oyuncak", ("shop=toys",)),
    ("mobilya", ("shop=furniture",)),
    ("hırdavat", ("shop=hardware", "shop=doityourself")),
    ("çiçek", ("shop=florist",)),
    ("kuaför", ("shop=hairdresser", "shop=beauty")),
    ("berber", ("shop=hairdresser",)),
    ("otel", ("tourism=hotel", "tourism=guest_house")),
    ("sinema", ("amenity=cinema",)),
    ("tiyatro", ("amenity=theatre",)),
    ("spor", ("leisure=sports_centre", "leisure=fitness_centre")),
    ("park", ("leisure=park",)),
    ("hastane", ("amenity=hospital",)),
    ("klinik", ("amenity=clinic", "amenity=doctors")),
    ("diş", ("amenity=dentist",)),
    ("veteriner", ("amenity=veterinary",)),
    ("banka", ("amenity=bank",)),
    ("atm", ("amenity=atm",)),
    ("benzin", ("amenity=fuel",)),
    ("otopark", ("amenity=parking",)),
    ("taksi", ("amenity=taxi",)),
    ("ptt", ("amenity=post_office",)),
    ("okul", ("amenity=school",)),
    ("üniversite", ("amenity=university", "amenity=college")),
    ("cami", ("amenity=place_of_worship",)),
    ("belediye", ("office=government",)),
)

_MALL_WORDS = (
    "alışveriş merkezi", "alisveris merkezi", "avm", "plaza", "pasaj", "outlet",
    "mall", "çarşı", "carsi", "arasta",
)
_MALL_LIST_WORDS = (
    "avmler", "avm'ler", "avmlar", "alışveriş merkezleri", "alisveris merkezleri",
    "plazalar", "pasajlar", "avm haritası", "avm haritasi",
)
_MALL_SUFFIX_WORDS = (
    "alışveriş merkezi", "alisveris merkezi", "avm", "plaza", "pasaj", "outlet",
    "çarşı", "carsi",
)
_MALL_INTENT_PATTERN = re.compile(
    r"(?i)\b(?:mağaza|magaza|store|dükkan|dukkan|mağazalar|magazalar)\w*|"
    r"\b(?:içindeki|icindeki|içinde|icinde|içerisindeki|icerisindeki|olan|bulunan|"
    r"listesi|listesini|neler|var|hangi|göster|goster|bana)\b"
)
_MALL_TYPES = {
    "mall", "shopping_centre", "shopping_center", "department_store", "marketplace",
}
_MALL_NAME_HINTS = (
    "alışveriş merkezi", "alisveris merkezi", "avm", "shopping centre", "shopping center",
    "pasaj", "outlet", "mall",
)

_SHOP_LABELS = {
    "mall": "AVM", "shopping_centre": "AVM", "shopping_center": "AVM",
    "supermarket": "Süpermarket", "convenience": "Market", "grocery": "Bakkal",
    "department_store": "Büyük mağaza", "clothes": "Giyim", "fashion": "Moda",
    "shoes": "Ayakkabı", "jewelry": "Takı", "watches": "Saat", "bag": "Çanta",
    "accessories": "Aksesuar", "cosmetics": "Kozmetik", "chemist": "Kozmetik",
    "beauty": "Güzellik", "hairdresser": "Kuaför", "barber": "Berber",
    "electronics": "Elektronik", "computer": "Bilgisayar", "mobile_phone": "Telefon",
    "books": "Kitap", "stationery": "Kırtasiye", "toys": "Oyuncak", "sports": "Spor",
    "outdoor": "Outdoor", "furniture": "Mobilya", "houseware": "Ev gereçleri",
    "kitchen": "Mutfak", "hardware": "Hırdavat", "doityourself": "Yapı market",
    "garden_centre": "Bahçe", "florist": "Çiçek", "bakery": "Fırın",
    "butcher": "Kasap", "greengrocer": "Manav", "deli": "Şarküteri",
    "confectionery": "Tatlı", "coffee": "Kahve", "tea": "Çay",
    "beverages": "İçecek", "alcohol": "İçki", "optician": "Optik",
    "medical_supply": "Medikal", "pharmacy": "Eczane", "pet": "Evcil hayvan",
    "pet_grooming": "Pet bakım", "music": "Müzik", "musical_instrument": "Müzik aleti",
    "gift": "Hediye", "variety_store": "Çeşitli", "general": "Mağaza",
    "car": "Otomobil", "car_repair": "Oto servis", "bicycle": "Bisiklet",
    "laundry": "Kuru temizleme", "dry_cleaning": "Kuru temizleme", "tailor": "Terzi",
    "fabric": "Kumaş", "interior_decoration": "Dekorasyon", "travel_agency": "Seyahat acentesi",
    "estate_agent": "Emlak", "insurance": "Sigorta", "tattoo": "Dövme",
    "games": "Oyun", "video_games": "Video oyun", "photo": "Fotoğraf",
    "frame": "Çerçeve", "craft": "El sanatları", "second_hand": "İkinci el",
    "vacant": "Boş",
}
_AMENITY_LABELS = {
    "cafe": "Kafe", "restaurant": "Restoran", "fast_food": "Fast food",
    "food_court": "Yeme-içme alanı", "ice_cream": "Dondurma", "bar": "Bar",
    "pub": "Pub", "bank": "Banka", "atm": "ATM", "pharmacy": "Eczane",
    "cinema": "Sinema", "theatre": "Tiyatro", "marketplace": "Pazar",
}
_LEISURE_LABELS = {
    "fitness_centre": "Spor salonu", "sports_centre": "Spor merkezi",
    "bowling_alley": "Bowling", "amusement_arcade": "Oyun salonu",
}
_CRAFT_LABELS = {
    "carpenter": "Marangoz", "photographer": "Fotoğrafçı", "jeweller": "Kuyumcu",
    "tailor": "Terzi", "bakery": "Fırın", "shoemaker": "Ayakkabı tamircisi",
}
_POI_LABELS = {
    **_SHOP_LABELS, **_AMENITY_LABELS, **_LEISURE_LABELS,
    "park": "Park", "playground": "Oyun alanı", "garden": "Bahçe",
    "hospital": "Hastane", "clinic": "Klinik", "doctors": "Doktor",
    "dentist": "Diş kliniği", "veterinary": "Veteriner",
    "fuel": "Benzin istasyonu", "parking": "Otopark", "taxi": "Taksi durağı",
    "post_office": "PTT", "school": "Okul", "university": "Üniversite",
    "college": "Kolej", "place_of_worship": "İbadet yeri",
    "government": "Kamu dairesi", "townhall": "Belediye",
    "hotel": "Otel", "guest_house": "Pansiyon", "hostel": "Hostel",
    "museum": "Müze", "attraction": "Gezilecek yer", "viewpoint": "Manzara noktası",
    "memorial": "Anıt", "artwork": "Sanat eseri",
}
_STORE_AMENITIES = (
    "cafe", "restaurant", "fast_food", "food_court", "ice_cream", "bar", "pub",
    "bank", "atm", "pharmacy", "cinema", "theatre", "marketplace",
)
_STORE_LEISURE = ("fitness_centre", "sports_centre", "bowling_alley", "amusement_arcade")


def _word_in(text: str, word: str) -> bool:
    return bool(re.search(rf"(?i)\b{re.escape(word)}\b", text or ""))


def _strip_mall_intent(text: str) -> str:
    result = re.sub(r"(?i)['’](?:deki|daki|teki|taki|ndeki|ndaki)\b", "", text or "")
    result = re.sub(r"(?i)['’](?:de|da|te|ta)\b", "", result)
    result = _MALL_INTENT_PATTERN.sub(" ", result)
    return re.sub(r"\s+", " ", result).strip(" .,;:-")


def _strip_mall_suffix(text: str) -> str:
    result = text or ""
    for word in sorted(_MALL_SUFFIX_WORDS, key=len, reverse=True):
        result = re.sub(rf"(?i)\b{re.escape(word)}\b", " ", result)
    return re.sub(r"\s+", " ", result).strip(" .,;:-")


def _mall_name_core(text: str) -> str:
    text = _strip_mall_suffix(_strip_mall_intent(text))
    text = re.sub(r"(?i)\b(?:mall|shopping centre|shopping center)\b", " ", text)
    return re.sub(r"[^0-9a-zçğıöşü]+", " ", _fold(text)).strip()


def _mall_level_text(tags: dict) -> str:
    raw = str(tags.get("level") or tags.get("addr:floor") or "").strip()
    if not raw:
        return ""
    compact = re.sub(r"\s*;\s*", ", ", raw)[:24]
    if compact == "0":
        return "Zemin"
    if re.fullmatch(r"-\d+", compact):
        return f"Bodrum {abs(int(compact))}"
    return f"Kat {compact}"


class MapError(Exception):
    pass


def _fold(text: str) -> str:
    return str(text or "").replace("İ", "i").replace("I", "ı").lower().strip()


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _parse_rows(html_text: str) -> list[list[str]]:
    rows = []
    for row in re.findall(r"<tr\b.*?</tr>", html_text or "", re.S | re.I):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if cells:
            rows.append([_clean(cell) for cell in cells])
    return rows


class MapTools:
    def __init__(
        self,
        settings=None,
        listener: Optional[Callable[[dict], None]] = None,
        session: Optional[requests.Session] = None,
        timeout: float = 22.0,
        sleep: Callable[[float], None] = time.sleep,
        store: Optional[PoiStore] = None,
    ) -> None:
        self.settings = settings
        self.listener = listener
        self.timeout = timeout
        self._sleep = sleep
        self._session = session or requests.Session()
        self._store = store if store is not None else PoiStore.default(settings)
        try:
            self._session.headers.setdefault("User-Agent", USER_AGENT)
        except Exception:
            pass
        self._cache: dict[str, tuple[float, object]] = {}
        self._last_nominatim = 0.0
        self._overpass_retry_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ ortak
    def _cache_get(self, key: str, ttl: float):
        with self._lock:
            item = self._cache.get(key)
        if item and time.monotonic() - item[0] < ttl:
            return item[1]
        return None

    def _cache_put(self, key: str, value) -> None:
        with self._lock:
            self._cache[key] = (time.monotonic(), value)

    def _emit(self, payload: dict) -> None:
        if self.listener:
            try:
                self.listener(payload)
            except Exception:
                pass

    def home(self) -> dict:
        home = None
        if self.settings is not None:
            try:
                home = self.settings.home_location
            except Exception:
                home = None
        home = dict(home or DEFAULT_HOME)
        home["name"] = str(home.get("name") or "Evim")
        home["region"] = str(home.get("region") or "Kocaeli")
        home["lat"] = float(home["lat"])
        home["lon"] = float(home["lon"])
        return home

    def _home_is_set(self) -> bool:
        try:
            return bool(self.settings is not None and self.settings.home_is_set)
        except Exception:
            return False

    def _public_place(self, place: dict) -> dict:
        return {
            "name": str(place.get("name") or "Konum"),
            "lat": float(place["lat"]),
            "lon": float(place["lon"]),
        }

    def _request(self, method: str, url: str, timeout: Optional[float] = None, **kwargs):
        retries = int(kwargs.pop("retries", 2))
        retry_statuses = kwargs.pop("retry_statuses", None) or (429, 500, 501, 502, 503, 504)
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._session.request(
                    method, url, timeout=timeout or self.timeout, **kwargs
                )
            except requests.Timeout:
                if attempt > retries:
                    raise MapError(
                        "Harita servisi yanıt vermedi (zaman aşımı); "
                        "birkaç saniye sonra aynı isteği yeniden dene."
                    )
                self._sleep(min(1.5 * attempt, 6.0))
                continue
            except requests.RequestException as error:
                raise MapError(f"Harita servisine ulaşılamadı: {error}") from error
            status = getattr(response, "status_code", None)
            if isinstance(status, int) and status in retry_statuses:
                if attempt > retries:
                    raise MapError(
                        f"Harita servisi geçici olarak yanıt veremedi (HTTP {status}); "
                        "birkaç saniye sonra aynı isteği yeniden dene."
                    )
                self._sleep(self._retry_delay(response, attempt))
                continue
            try:
                response.raise_for_status()
            except requests.RequestException as error:
                raise MapError(f"Harita servisine ulaşılamadı: {error}") from error
            return response

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        headers = getattr(response, "headers", None) or {}
        retry_after = str(headers.get("Retry-After") or "").strip()
        if retry_after:
            seconds = 0.0
            try:
                seconds = float(retry_after)
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime

                    seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except Exception:
                    seconds = 0.0
            if seconds > 0:
                return min(seconds, 12.0)
        return min(1.5 * attempt, 6.0)

    def _security_headers(self) -> dict:
        timestamp = str(int(time.time() * 1000))
        token = hashlib.md5((timestamp + EKOMOBIL_SECRET).encode("utf-8")).hexdigest()
        return {"X-Security-Token": token, "X-Security-Timestamp": timestamp}

    def _ekomobil(self, command: str, params: dict, method: str = "GET"):
        headers = self._security_headers()
        headers["Referer"] = EKOMOBIL_BASE
        headers["Origin"] = EKOMOBIL_BASE.rstrip("/")
        if method == "POST":
            return self._request("POST", f"{EKOMOBIL_OPS}?cmd={command}", data=params, headers=headers)
        return self._request("GET", f"{EKOMOBIL_OPS}?cmd={command}", params=params, headers=headers)

    # ------------------------------------------------------------------ geocode
    def geocode_all(self, query: str, limit: int = 3) -> list[dict]:
        text = str(query or "").strip()
        if not text:
            raise MapError("Aranacak yer adı boş.")
        key = f"geo:{_fold(text)}:{limit}"
        cached = self._cache_get(key, 86400)
        if cached:
            return list(cached)
        wait = 1.0 - (time.monotonic() - self._last_nominatim)
        if wait > 0:
            self._sleep(wait)
        self._last_nominatim = time.monotonic()
        response = self._request(
            "GET",
            NOMINATIM_URL,
            params={
                "q": text,
                "format": "jsonv2",
                "limit": max(1, min(int(limit), 5)),
                "addressdetails": 1,
                "accept-language": "tr",
            },
            headers={"User-Agent": USER_AGENT},
        )
        try:
            data = response.json()
        except ValueError as error:
            raise MapError("Adres servisi geçersiz yanıt döndü.") from error
        places = []
        for item in data or []:
            try:
                lat, lon = float(item["lat"]), float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            display = str(item.get("display_name") or text)
            address = item.get("address") or {}
            region = (
                address.get("state")
                or address.get("province")
                or address.get("city")
                or address.get("town")
                or ""
            )
            name = (
                item.get("name")
                or address.get("mall")
                or address.get("shop")
                or address.get("amenity")
                or display.split(",")[0]
                or address.get("neighbourhood")
                or address.get("suburb")
            )
            road = (
                address.get("road")
                or address.get("pedestrian")
                or address.get("footway")
                or ""
            )
            street = " ".join(
                part for part in (road, address.get("house_number")) if part
            ).strip()
            district = (
                address.get("suburb")
                or address.get("neighbourhood")
                or address.get("district")
                or ""
            )
            town = (
                address.get("town")
                or address.get("city")
                or address.get("municipality")
                or address.get("county")
                or ""
            )
            address_line = ", ".join(
                part for part in dict.fromkeys((street, district, town)) if part
            )
            bbox = []
            for value in item.get("boundingbox") or []:
                try:
                    bbox.append(float(value))
                except (TypeError, ValueError):
                    bbox = []
                    break
            places.append({
                "name": str(name or text).strip(),
                "label": display,
                "region": str(region).strip(),
                "lat": lat,
                "lon": lon,
                "kind": item.get("type") or "",
                "category": str(item.get("class") or item.get("category") or ""),
                "osm_type": str(item.get("osm_type") or "").lower(),
                "osm_id": str(item.get("osm_id") or ""),
                "address": address_line,
                "postcode": str(address.get("postcode") or ""),
                "boundingbox": bbox if len(bbox) == 4 else [],
            })
        if not places:
            raise MapError(f"'{text}' için konum bulunamadı. Daha açık bir adres veya semt adı dene.")
        self._cache_put(key, places)
        return places

    def geocode(self, query: str) -> dict:
        return self.geocode_all(query, 1)[0]

    def _resolve_place(self, value, fallback_home: bool = True) -> dict:
        if isinstance(value, dict):
            try:
                return {
                    "name": str(value.get("name") or "Konum"),
                    "lat": float(value["lat"]),
                    "lon": float(value["lon"]),
                }
            except (KeyError, TypeError, ValueError) as error:
                raise MapError("Geçersiz konum bilgisi.") from error
        text = str(value or "").strip()
        if not text:
            if fallback_home:
                return self.home()
            raise MapError("Konum belirtilmedi.")
        if _fold(text) in {"evim", "ev", "home", "benim evim", "eve"}:
            return self.home()
        match = re.match(r"^\s*(-?\d+(?:[.,]\d+)?)\s*[,;]\s*(-?\d+(?:[.,]\d+)?)\s*$", text)
        if match:
            lat = float(match.group(1).replace(",", "."))
            lon = float(match.group(2).replace(",", "."))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return {"name": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon}
        return self.geocode(text)

    def _set_home(self, place: dict, name: str = "") -> dict:
        home = {
            "name": str(name or place.get("name") or "Evim")[:80],
            "region": str(place.get("region") or "Kocaeli")[:80],
            "lat": float(place["lat"]),
            "lon": float(place["lon"]),
        }
        if self.settings is not None:
            try:
                home = self.settings.set_home(home["lat"], home["lon"], home["name"], home["region"])
            except Exception as error:
                raise MapError(f"Ev konumu kaydedilemedi: {error}") from error
        self._emit({"kind": "home", "home": home, "center": home, "zoom": 13})
        return home

    # ------------------------------------------------------------------- rota
    @staticmethod
    def normalize_mode(mode: str) -> str:
        folded = _fold(mode)
        for key, aliases in _MODE_ALIASES.items():
            if folded in aliases:
                return key
        return "driving"

    def route(self, origin, destination, mode: str = "driving") -> dict:
        mode = self.normalize_mode(mode)
        start = self._resolve_place(origin) if not isinstance(origin, dict) else self._resolve_place(origin)
        end = self._resolve_place(destination) if not isinstance(destination, dict) else self._resolve_place(destination)
        coordinates = f"{start['lon']:.6f},{start['lat']:.6f};{end['lon']:.6f},{end['lat']:.6f}"
        response = self._request(
            "GET",
            OSRM_URLS[mode] + "/" + coordinates,
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "true",
                "alternatives": "false",
            },
        )
        try:
            data = response.json()
        except ValueError as error:
            raise MapError("Rota servisi geçersiz yanıt döndü.") from error
        if data.get("code") != "Ok" or not data.get("routes"):
            raise MapError("Bu iki nokta arasında uygun bir rota bulunamadı.")
        best = data["routes"][0]
        points = []
        geometry = (best.get("geometry") or {}).get("coordinates") or []
        for lon, lat in geometry:
            points.append([round(float(lat), 6), round(float(lon), 6)])
        if not points:
            points = [[start["lat"], start["lon"]], [end["lat"], end["lon"]]]
        steps = []
        for leg in best.get("legs") or []:
            for step in leg.get("steps") or []:
                text = self._step_text(step)
                if text:
                    steps.append(text)
        distance_km = round(float(best.get("distance", 0)) / 1000, 1)
        duration_min = max(1, int(round(float(best.get("duration", 0)) / 60)))
        result = {
            "mode": mode,
            "mode_label": MODE_LABELS[mode],
            "origin": self._public_place(start),
            "destination": self._public_place(end),
            "points": points,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "steps": steps[:14],
        }
        self._emit({"kind": "route", **result})
        return result

    @staticmethod
    def _step_text(step: dict) -> str:
        name = str(step.get("name") or "").strip()
        distance = int(round(float(step.get("distance", 0) or 0)))
        maneuver = step.get("maneuver") or {}
        kind = str(maneuver.get("type") or "")
        modifier = str(maneuver.get("modifier") or "")
        labels = {
            "depart": "Yola çık",
            "arrive": "Hedefe var",
            "turn": "Dön",
            "new name": "Devam et",
            "continue": "Devam et",
            "merge": "Katıl",
            "fork": "Ayrıl",
            "roundabout": "Dönel kavşaktan çık",
            "rotary": "Dönel kavşaktan çık",
            "end of road": "Yol sonunda dön",
        }
        label = labels.get(kind, "Devam et")
        side = {
            "left": "sola", "right": "sağa", "slight left": "hafif sola",
            "slight right": "hafif sağa", "sharp left": "keskin sola",
            "sharp right": "keskin sağa", "uturn": "geriye",
        }.get(modifier, "")
        if kind == "arrive":
            return "Hedefe vardın."
        if distance < 50 and kind not in {"depart", "roundabout"}:
            return ""
        target = f"{side} " if side else ""
        road = f" {name}" if name else ""
        return f"{label} {target}{road} · {distance} m".replace("  ", " ").strip()

    def format_route(self, route: dict) -> str:
        mode = route.get("mode_label", "Araç")
        start, end = route["origin"], route["destination"]
        lines = [
            f"✅ {mode} rotası: {start['name']} → {end['name']}",
            f"Mesafe: {route['distance_km']} km · Tahmini süre: {route['duration_min']} dk",
        ]
        if route.get("steps"):
            lines.append("Adımlar:")
            lines.extend(f"{index}. {step}" for index, step in enumerate(route["steps"], 1))
        lines.append("Rota uygulama içi haritada çizildi.")
        return "\n".join(lines)

    # ----------------------------------------------------------------- mekân
    @staticmethod
    def _category_for(query: str):
        folded = _fold(query)
        matches = [item for item in _CATEGORY_TAGS if item[0] in folded]
        if not matches:
            return None
        matches.sort(key=lambda item: len(item[0]), reverse=True)
        return matches[0]

    @staticmethod
    def _photo_url(tags: dict) -> str:
        raw = str(tags.get("image") or tags.get("wikimedia_commons") or "").strip()
        if not raw:
            return ""
        if raw.startswith(("http://", "https://")):
            try:
                host = urlsplit(raw).netloc.casefold()
            except Exception:
                return ""
            if host.endswith("wikimedia.org") or host.endswith("wikipedia.org"):
                return raw
            return ""
        name = raw.split("|", 1)[0].strip()
        if name.casefold().startswith(("file:", "image:")):
            name = name.split(":", 1)[1].strip()
        if not name or any(char in name for char in "/\\<>\"'`"):
            return ""
        return (
            "https://commons.wikimedia.org/wiki/Special:FilePath/"
            + quote(name.replace(" ", "_"), safe="")
            + "?width=480"
        )

    # ------------------------------------------------------- yerel POI indeksi
    def _warm_local_area(self, lat: float, lon: float, radius_m: float) -> None:
        if self._store is None:
            return
        try:
            half = min(max(float(radius_m) + 1500.0, 3000.0), 12000.0)
            dlat = half / 111_320.0
            dlon = half / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
            self._store.ensure_import_async(lat - dlat, lat + dlat, lon - dlon, lon + dlon)
        except Exception:
            pass

    def warm_home(self) -> None:
        """Harita açıldığında ev çevresini arka planda yerel indekse hazırlar."""
        home = self.home()
        self._warm_local_area(home["lat"], home["lon"], 2500)

    def _local_place(self, row: dict) -> dict:
        lat, lon = float(row["lat"]), float(row["lon"])
        release = str(row.get("release") or OVERTURE_RELEASE)
        return {
            "name": str(row.get("name") or "Mekân"),
            "lat": lat,
            "lon": lon,
            "distance_m": int(row.get("distance_m") or 0),
            "address": str(row.get("address") or ""),
            "open": "",
            "phone": str(row.get("phone") or ""),
            "website": str(row.get("website") or ""),
            "rating": "",
            "price": "",
            "photo": "",
            "kind": str(row.get("kind") or "place"),
            "category": str(row.get("category") or "Mekân"),
            "operator": "",
            "brand": "",
            "description": "",
            "wheelchair": "",
            "level": "",
            "status": str(row.get("status") or ""),
            "updated": str(row.get("updated") or ""),
            "release": release,
            "source": "Overture",
            "source_label": f"Overture {release} (aylık açık veri)",
            "maps_url": f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}",
        }

    def _local_places(
        self, lat: float, lon: float, radius: float, term: str = "", limit: int = 60
    ) -> list[dict]:
        if self._store is None:
            return []
        try:
            rows = self._store.search_near(
                lat, lon, radius, term=term, limit=limit
            )
        except Exception:
            return []
        return [self._local_place(row) for row in rows]

    @staticmethod
    def _name_key(name: str) -> str:
        text = re.sub(r"[^0-9a-zçğıöşü]+", " ", _fold(name))
        for word in (
            "avm", "market", "mağazası", "magazasi", "şubesi", "subesi",
            "outlet", "plaza", "pasaj", "store", "merkezi",
        ):
            text = re.sub(rf"\b{re.escape(word)}\b", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _same_place_name(self, first: str, second: str) -> bool:
        if not first or not second:
            return False
        left, right = _fold(first), _fold(second)
        if left == right:
            return True
        left_key, right_key = self._name_key(first), self._name_key(second)
        if not left_key or not right_key:
            return False
        if left_key == right_key:
            return True
        if abs(len(left_key) - len(right_key)) > 8:
            return False
        return SequenceMatcher(None, left_key, right_key).ratio() >= 0.9

    def _same_place(self, first: dict, second: dict) -> bool:
        try:
            distance = _distance_m(
                float(first["lat"]), float(first["lon"]),
                float(second["lat"]), float(second["lon"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
        if distance > 90:
            return False
        return self._same_place_name(str(first.get("name") or ""), str(second.get("name") or ""))

    def _merge_place_items(
        self, osm_items: list[dict], local_items: list[dict], limit: int
    ) -> list[dict]:
        merged: list[dict] = []
        local_used: set[int] = set()
        for osm in osm_items:
            match_index = None
            for index, local in enumerate(local_items):
                if index in local_used:
                    continue
                if self._same_place(osm, local):
                    match_index = index
                    break
            if match_index is None:
                merged.append(dict(osm))
                continue
            local_used.add(match_index)
            base = dict(local_items[match_index])
            for field in (
                "open", "phone", "website", "rating", "price", "photo",
                "wheelchair", "level", "description", "operator", "brand",
            ):
                if osm.get(field):
                    base[field] = osm[field]
            if osm.get("kind"):
                base["kind"] = osm["kind"]
            if osm.get("category"):
                base["category"] = osm["category"]
            base["distance_m"] = min(
                int(base.get("distance_m") or 10 ** 9),
                int(osm.get("distance_m") or 10 ** 9),
            )
            base["source"] = "Overture + OpenStreetMap"
            base["source_label"] = (
                f"Overture {base.get('release') or OVERTURE_RELEASE} "
                "+ OpenStreetMap (canlı saat/iletişim)"
            )
            base["detail"] = "Canlı OSM bilgileriyle zenginleştirildi."
            merged.append(base)
        for index, local in enumerate(local_items):
            if index not in local_used:
                merged.append(local)
        merged.sort(key=lambda item: item.get("distance_m") or 10 ** 9)
        result, seen = [], set()
        for item in merged:
            fingerprint = (
                self._name_key(str(item.get("name") or "")),
                round(float(item.get("lat") or 0), 4),
                round(float(item.get("lon") or 0), 4),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            result.append(item)
        return result[:max(1, min(int(limit), 40))]

    def _place_item(self, tags: dict, lat: float, lon: float, origin_lat: float, origin_lon: float) -> dict:
        name = str(tags.get("name") or tags.get("brand") or tags.get("operator") or "").strip()
        if not name:
            return {}
        address = " ".join(
            value for value in (
                tags.get("addr:street"), tags.get("addr:housenumber")
            ) if value
        ).strip()
        place = {
            "name": name,
            "lat": lat,
            "lon": lon,
            "distance_m": int(round(_distance_m(origin_lat, origin_lon, lat, lon))),
            "address": address,
            "open": str(tags.get("opening_hours") or ""),
            "phone": str(tags.get("phone") or tags.get("contact:phone") or ""),
            "website": str(tags.get("website") or tags.get("contact:website") or ""),
            "rating": str(tags.get("rating") or tags.get("stars") or ""),
            "price": str(tags.get("price") or tags.get("price:range") or ""),
            "photo": self._photo_url(tags),
            "kind": str(tags.get("shop") or tags.get("amenity") or tags.get("tourism") or tags.get("leisure") or ""),
            "category": self._poi_category(tags),
            "operator": str(tags.get("operator") or "").strip()[:80],
            "brand": str(tags.get("brand") or "").strip()[:80],
            "description": str(tags.get("description") or tags.get("note") or "").strip()[:300],
            "wheelchair": {
                "yes": "Erişilebilir",
                "limited": "Kısıtlı erişim",
                "no": "Erişilebilir değil",
            }.get(_fold(tags.get("wheelchair")), ""),
            "level": str(tags.get("level") or "").strip()[:40],
            "status": "",
            "updated": "",
            "source": "OpenStreetMap",
            "source_label": "OpenStreetMap (canlı)",
        }
        place["maps_url"] = (
            "https://www.google.com/maps/search/?api=1&query="
            f"{lat:.6f},{lon:.6f}"
        )
        return place

    def nearby(self, query: str, center=None, radius: int = 1500, limit: int = 12,
               retries: int = 2) -> dict:
        text = str(query or "").strip()
        if not text:
            raise MapError("Aranacak mekân veya kategori belirtilmedi.")
        origin = self._resolve_place(center or "evim")
        try:
            radius = max(100, min(int(radius), 10000))
        except (TypeError, ValueError):
            radius = 1500
        category = self._category_for(text)
        pure_category = bool(
            category and _fold(text).replace(category[0], "").strip(" .:-/") == ""
        )
        clauses = []
        if pure_category:
            for tag in category[1]:
                key, value = tag.split("=", 1)
                clauses.append(f'nwr["{key}"="{value}"](around:{radius},{origin["lat"]},{origin["lon"]});')
            title = category[0]
        else:
            pattern = re.sub(r"[^\w\sğüşıöçĞÜŞİÖÇ.-]", " ", text, flags=re.UNICODE).strip()
            pattern = re.sub(r"\s+", " ", pattern)[:60]
            if not pattern:
                raise MapError("Arama metni geçersiz.")
            escaped = pattern.replace("\\", "\\\\").replace('"', '\\"')
            for key in ("name", "brand", "operator"):
                clauses.append(
                    f'nwr["{key}"~"{escaped}",i](around:{radius},{origin["lat"]},{origin["lon"]});'
                )
            title = text
        self._warm_local_area(origin["lat"], origin["lon"], radius)
        local_items = self._local_places(
            origin["lat"], origin["lon"], radius,
            term=category[0] if pure_category else text,
            limit=max(40, min(int(limit) * 4, 120)),
        )
        if pure_category and _fold(category[0]) in (
            "avm", "alışveriş merkezi", "alisveris merkezi",
        ):
            local_items = [
                item for item in local_items
                if any(_word_in(str(item.get("name") or ""), hint) for hint in _MALL_NAME_HINTS)
            ]
        overpass = "[out:json][timeout:25];\n(\n  " + "\n  ".join(clauses) + "\n);\nout center tags 80;"
        try:
            payload = self._overpass(overpass, retries=retries)
        except MapError:
            if not local_items:
                raise
            payload = {}
        items = []
        seen = set()
        for element in payload.get("elements") or []:
            tags = element.get("tags") or {}
            center_point = element.get("center") or element
            try:
                lat, lon = float(center_point["lat"]), float(center_point["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(tags.get("name") or tags.get("brand") or tags.get("operator") or "").strip()
            if not name:
                continue
            fingerprint = (name.casefold(), round(lat, 4), round(lon, 4))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            place = self._place_item(tags, lat, lon, origin["lat"], origin["lon"])
            if not place:
                continue
            items.append(place)
        limit = max(1, min(int(limit), 30))
        items = self._merge_place_items(items, local_items, limit)
        result = {
            "title": title,
            "center": self._public_place(origin),
            "items": items,
            "radius": radius,
        }
        self._emit({"kind": "places", **result})
        return result

    def format_nearby(self, result: dict) -> str:
        center = result["center"]
        items = result["items"]
        if not items:
            return (
                f"❌ {center['name']} çevresinde {result['radius']} m içinde '{result['title']}' bulunamadı. "
                "Arama kelimesini veya yarıçapı değiştirerek yeniden dene."
            )
        lines = [
            f"📍 {center['name']} çevresinde {result['radius']} m içinde '{result['title']}': {len(items)} sonuç"
        ]
        for index, item in enumerate(items[:10], 1):
            details = [f"{item['distance_m']} m"]
            if item["address"]:
                details.append(item["address"])
            if item["open"]:
                details.append(item["open"])
            if item["phone"]:
                details.append(f"☎ {item['phone']}")
            if item["rating"]:
                details.append(f"puan {item['rating']}")
            if item["price"]:
                details.append(f"fiyat {item['price']}")
            lines.append(f"{index}. {item['name']} — " + " · ".join(details))
            if item["website"]:
                lines.append(f"   Web: {item['website']}")
            elif index <= 3:
                lines.append(f"   Yorum/fiyat: {item['maps_url']}")
        lines.append(
            "ℹ️ Kaynak: Overture Maps aylık açık veri + OpenStreetMap canlı katkıları. "
            "Çoğu işletmenin yorumu, puanı veya menü fiyatı bu verilerde yoktur; "
            "olmayan bilgi uydurulmaz. Ayrıntı için işletme sitesi veya verilen bağlantı kullanılabilir."
        )
        return "\n".join(lines)

    def reverse(self, lat: float, lon: float) -> dict:
        key = f"rev:{lat:.5f},{lon:.5f}"
        cached = self._cache_get(key, 86400)
        if cached:
            return dict(cached)
        wait = 1.0 - (time.monotonic() - self._last_nominatim)
        if wait > 0:
            self._sleep(wait)
        self._last_nominatim = time.monotonic()
        response = self._request(
            "GET",
            NOMINATIM_URL.rsplit("/", 1)[0] + "/reverse",
            params={
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "format": "jsonv2",
                "addressdetails": 1,
                "zoom": 18,
                "accept-language": "tr",
            },
            headers={"User-Agent": USER_AGENT},
        )
        try:
            data = response.json()
        except ValueError as error:
            raise MapError("Adres servisi geçersiz yanıt döndü.") from error
        if not data:
            raise MapError("Bu nokta için adres bilgisi bulunamadı.")
        display = str(data.get("display_name") or "")
        address = data.get("address") or {}
        road = (
            address.get("road")
            or address.get("pedestrian")
            or address.get("footway")
            or ""
        )
        street = " ".join(
            part for part in (road, address.get("house_number")) if part
        ).strip()
        district = (
            address.get("suburb")
            or address.get("neighbourhood")
            or address.get("district")
            or ""
        )
        town = (
            address.get("town")
            or address.get("city")
            or address.get("municipality")
            or address.get("county")
            or ""
        )
        address_line = ", ".join(
            part for part in dict.fromkeys((street, district, town)) if part
        )
        place = {
            "name": str(
                data.get("name")
                or address.get("amenity")
                or address.get("shop")
                or address.get("leisure")
                or address.get("tourism")
                or (display.split(",")[0] if display else "")
                or f"{lat:.5f}, {lon:.5f}"
            ).strip(),
            "label": display,
            "address": address_line,
            "category": str(data.get("category") or data.get("type") or ""),
            "lat": lat,
            "lon": lon,
        }
        self._cache_put(key, place)
        return place

    def click_nearby(self, lat: float, lon: float, zoom: float = 15) -> dict:
        """Haritada tıklanan noktanın çevresindeki adı kayıtlı mekânları döndürür."""
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError) as error:
            raise MapError("Geçersiz konum.") from error
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise MapError("Geçersiz konum.")
        radius = 300
        try:
            zoom_value = int(float(zoom))
            radius = int(max(60, min(1200, 300 * (2 ** (15 - zoom_value)))))
        except (TypeError, ValueError):
            zoom_value = 15
        cache_key = f"click:{lat:.4f}:{lon:.4f}:{zoom_value}"
        cached = self._cache_get(cache_key, 300)
        if cached:
            return dict(cached)
        scan_radius = min(radius, 180)
        self._warm_local_area(lat, lon, radius)
        local_items = self._local_places(lat, lon, radius, limit=40)
        elements: list[dict] = []
        api_failed = False
        try:
            elements = self._osm_api_elements(lat, lon, scan_radius, click=True)
        except MapError:
            api_failed = True
        if not elements and api_failed and not local_items:
            clauses = "\n".join(
                f'  nwr(around:{radius},{lat:.6f},{lon:.6f})["{key}"]["name"];'
                for key in CLICK_POI_KEYS
            )
            overpass = f"[out:json][timeout:12];\n(\n{clauses}\n);\nout center tags 80;"
            payload = self._overpass(overpass, timeout=min(self.timeout, 8.0), retries=0)
            elements = payload.get("elements") or []
        items: list[dict] = []
        seen = set()
        for element in elements:
            tags = element.get("tags") or {}
            point = element.get("center") or element
            try:
                plat, plon = float(point["lat"]), float(point["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            item = self._place_item(tags, plat, plon, lat, lon)
            if not item:
                continue
            fingerprint = (item["name"].casefold(), round(plat, 4), round(plon, 4))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            items.append(item)
        items = self._merge_place_items(items, local_items, 12)
        center_name = items[0]["name"] if items else f"{lat:.5f}, {lon:.5f}"
        result = {
            "title": "Tıklanan nokta çevresi",
            "center": {"name": center_name, "lat": lat, "lon": lon},
            "items": items,
            "radius": scan_radius if elements and not api_failed and not local_items else radius,
        }
        self._cache_put(cache_key, result)
        self._emit({"kind": "places", **result})
        return result

    def _overpass(self, query: str, timeout: Optional[float] = None, retries: int = 2) -> dict:
        if time.monotonic() < self._overpass_retry_at:
            raise MapError("Mekân servisi kısa süre önce yanıt vermedi; kısa süre sonra tekrar dene.")
        last_error = None
        for url in OVERPASS_URLS:
            try:
                response = self._request(
                    "POST", url, data={"data": query}, timeout=timeout,
                    retries=retries,
                    retry_statuses=(406, 429, 500, 501, 502, 503, 504),
                )
                self._overpass_retry_at = 0.0
                return response.json()
            except (MapError, ValueError) as error:
                last_error = error
        self._overpass_retry_at = time.monotonic() + 20.0
        raise MapError(f"Mekân servisi yanıt vermedi: {last_error}")

    def _osm_api_elements(
        self, lat: float, lon: float, radius: int, click: bool = False
    ) -> list[dict]:
        """OSM ana API'sinden yakın kayıtları okur (hızlı, tek istek)."""
        dlat = radius / 111_320.0
        dlon = radius / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
        bbox = f"{lon - dlon:.6f},{lat - dlat:.6f},{lon + dlon:.6f},{lat + dlat:.6f}"
        response = self._request(
            "GET", OSM_API_URL, params={"bbox": bbox}, timeout=min(self.timeout, 12.0)
        )
        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as error:
            raise MapError("OSM harita verisi çözümlenemedi.") from error
        nodes: dict[str, tuple[float, float]] = {}
        for node in root.findall("node"):
            try:
                nodes[str(node.get("id"))] = (float(node.get("lat")), float(node.get("lon")))
            except (TypeError, ValueError):
                continue
        elements: list[dict] = []
        for item in root.findall("node") + root.findall("way"):
            tags = {
                str(tag.get("k")): str(tag.get("v"))
                for tag in item.findall("tag")
                if tag.get("k")
            }
            if not tags.get("name"):
                continue
            if click:
                if not any(tags.get(key) for key in CLICK_POI_KEYS):
                    continue
            elif not (
                tags.get("shop")
                or tags.get("office")
                or tags.get("craft")
                or _fold(tags.get("amenity")) in _STORE_AMENITIES
                or _fold(tags.get("leisure")) in _STORE_LEISURE
            ):
                continue
            if item.tag == "node":
                try:
                    point = (float(item.get("lat")), float(item.get("lon")))
                except (TypeError, ValueError):
                    continue
            else:
                points = [
                    nodes[str(ref.get("ref"))]
                    for ref in item.findall("nd")
                    if str(ref.get("ref")) in nodes
                ]
                if not points:
                    continue
                point = (
                    sum(value[0] for value in points) / len(points),
                    sum(value[1] for value in points) / len(points),
                )
            if _distance_m(lat, lon, point[0], point[1]) > radius + 40:
                continue
            elements.append({
                "type": item.tag,
                "id": item.get("id"),
                "lat": point[0],
                "lon": point[1],
                "tags": tags,
            })
        return elements

    # ------------------------------------------------------------------ AVM
    @staticmethod
    def _is_mall_query(query: str) -> bool:
        text = str(query or "").strip()
        if not text:
            return False
        folded = _fold(text)
        if any(word in folded for word in _MALL_LIST_WORDS):
            return False
        if not any(_word_in(text, word) for word in _MALL_WORDS):
            return False
        return len(_mall_name_core(text)) >= 2

    @staticmethod
    def _is_mall_place(place: dict) -> bool:
        if _fold(place.get("kind")) in _MALL_TYPES:
            return True
        text = f"{place.get('name') or ''} {place.get('label') or ''}"
        return any(_word_in(text, word) for word in _MALL_NAME_HINTS)

    @staticmethod
    def _pick_mall_candidate(query: str, places: list[dict]) -> Optional[dict]:
        core = _mall_name_core(query)
        best, best_score = None, -1.0
        for place in places:
            if not MapTools._is_mall_place(place):
                continue
            place_core = _mall_name_core(
                str(place.get("name") or place.get("label") or "")
            )
            ratio = (
                SequenceMatcher(None, core, place_core).ratio()
                if core and place_core else 0.0
            )
            score = 10.0 + ratio * 6.0
            if core and core in _fold(str(place.get("label") or "")):
                score += 3.0
            if score > best_score:
                best, best_score = place, score
        return best

    @staticmethod
    def _bbox_radius(place: dict, default: int = 350) -> int:
        try:
            south, north, west, east = [float(value) for value in place["boundingbox"]]
            lat, lon = float(place["lat"]), float(place["lon"])
            corners = ((south, west), (south, east), (north, west), (north, east))
            # Açık havalı AVM'lerde mağaza düğümleri bina dışına taşabildiği için alt sınır 220 m.
            radius = max(_distance_m(lat, lon, a, b) for a, b in corners) + 80
            return int(max(220, min(radius, 900)))
        except (KeyError, TypeError, ValueError):
            return default

    @staticmethod
    def _poi_category(tags: dict) -> str:
        for key in ("shop", "amenity", "tourism", "leisure", "office", "craft", "historic"):
            value = _fold(tags.get(key))
            if value:
                return _POI_LABELS.get(value, "İşletme")
        if _fold(tags.get("landuse")) == "military" or tags.get("military"):
            return "Askeri tesis"
        if tags.get("government"):
            return "Kamu dairesi"
        if tags.get("building"):
            return "Bina"
        if tags.get("man_made"):
            return "Yapı"
        if tags.get("natural"):
            return "Doğal alan"
        if tags.get("place"):
            return "Yer"
        if tags.get("waterway"):
            return "Su yolu"
        if tags.get("railway"):
            return "Raylı sistem"
        return "Mekân"

    @staticmethod
    def _store_category(tags: dict) -> tuple[str, str]:
        shop = _fold(tags.get("shop"))
        if shop:
            return _SHOP_LABELS.get(shop, "Mağaza"), f"shop={shop}"
        amenity = _fold(tags.get("amenity"))
        if amenity:
            return _AMENITY_LABELS.get(amenity, "Hizmet"), f"amenity={amenity}"
        leisure = _fold(tags.get("leisure"))
        if leisure:
            return _LEISURE_LABELS.get(leisure, "Spor/eğlence"), f"leisure={leisure}"
        craft = _fold(tags.get("craft"))
        if craft:
            return _CRAFT_LABELS.get(craft, "El sanatları"), f"craft={craft}"
        if tags.get("office"):
            return "Ofis", f"office={_fold(tags.get('office'))}"
        return "İşletme", ""

    @classmethod
    def _parse_mall_stores(cls, elements, mall_name: str) -> list[dict]:
        stores: dict[str, dict] = {}
        folded_mall = _fold(mall_name)
        for element in elements or []:
            tags = element.get("tags") or {}
            if _fold(tags.get("shop")) == "mall":
                continue
            name = str(tags.get("name") or "").strip()
            if not name or _fold(name) == folded_mall:
                continue
            category, category_key = cls._store_category(tags)
            if category_key == "shop=vacant":
                continue
            point = element.get("center") or element
            try:
                lat, lon = float(point["lat"]), float(point["lon"])
            except (KeyError, TypeError, ValueError):
                lat, lon = None, None
            wheelchair = {
                "yes": "Erişilebilir",
                "limited": "Kısıtlı erişim",
                "no": "Erişilebilir değil",
            }.get(_fold(tags.get("wheelchair")), "")
            item = {
                "name": name[:120],
                "category": category,
                "category_key": category_key,
                "level": _mall_level_text(tags),
                "price": str(tags.get("price") or tags.get("price:range") or "").strip()[:40],
                "photo": cls._photo_url(tags),
                "phone": str(tags.get("phone") or tags.get("contact:phone") or "").strip()[:60],
                "website": str(
                    tags.get("website")
                    or tags.get("contact:website")
                    or tags.get("brand:website")
                    or ""
                ).strip()[:200],
                "open": str(tags.get("opening_hours") or "").strip()[:120],
                "brand": str(tags.get("brand") or "").strip()[:80],
                "operator": str(tags.get("operator") or "").strip()[:80],
                "wheelchair": wheelchair,
                "description": str(tags.get("description") or "").strip()[:220],
                "lat": round(lat, 6) if lat is not None else None,
                "lon": round(lon, 6) if lon is not None else None,
            }
            key = _fold(name)
            if key in stores:
                for field, value in item.items():
                    if not stores[key].get(field) and value:
                        stores[key][field] = value
                continue
            stores[key] = item
        return sorted(stores.values(), key=lambda item: _fold(item["name"]))

    @classmethod
    def _mall_own_tags(cls, elements, mall: dict) -> dict:
        """Mağaza sorgusundan gelen elemanlar arasından AVM'nin kendi etiketlerini bulur."""
        osm_type = _fold(mall.get("osm_type"))
        osm_id = str(mall.get("osm_id") or "").strip()
        name = _fold(str(mall.get("name") or ""))
        fallback = None
        for element in elements or []:
            tags = element.get("tags") or {}
            if not tags.get("name"):
                continue
            if osm_type and osm_id.isdigit() and \
                    _fold(element.get("type")) == osm_type and str(element.get("id")) == osm_id:
                return tags
            if _fold(tags.get("shop")) == "mall":
                if name and _fold(str(tags.get("name"))) == name:
                    return tags
                if fallback is None:
                    fallback = tags
        return fallback or {}

    def _mall_store_elements(self, mall: dict, radius: int) -> tuple[list, bool]:
        try:
            lat, lon = float(mall["lat"]), float(mall["lon"])
        except (KeyError, TypeError, ValueError) as error:
            raise MapError("AVM konumu okunamadı.") from error
        osm_type = _fold(mall.get("osm_type"))
        try:
            osm_id = int(str(mall.get("osm_id") or "").strip())
        except ValueError:
            osm_id = 0
        key = f"mallstores:{osm_type}:{osm_id}:{radius}:{lat:.4f},{lon:.4f}"
        cached = self._cache_get(key, 21600)
        if cached:
            return list(cached[0]), bool(cached[1])
        amenities = "|".join(_STORE_AMENITIES)
        leisure = "|".join(_STORE_LEISURE)
        filters = (
            'nwr["shop"]["name"]',
            f'nwr["amenity"~"^({amenities})$"]["name"]',
            'nwr["office"]["name"]',
            'nwr["craft"]["name"]',
            f'nwr["leisure"~"^({leisure})$"]["name"]',
        )
        # Overpass alan indeksi çoğu AVM binası (way) için oluşmadığından ve her
        # başarısız alan sorgusu iki uç noktayı da meşgul ettiğinden yalnızca
        # relation'larda alan sorgusu denenir; way'lerde yarıçap yeterli.
        area_id = 3_600_000_000 + osm_id if osm_type == "relation" and osm_id > 0 else 0
        if area_id:
            body = "\n".join(f"  {item}(area.mall);" for item in filters)
            query = (
                "[out:json][timeout:30];\n"
                f"area({area_id})->.mall;\n(\n{body}\n);\nout center tags 400;"
            )
            try:
                payload = self._overpass(query, timeout=min(self.timeout, 10.0))
                elements = payload.get("elements") or []
                if elements:
                    self._cache_put(key, (elements, False))
                    return elements, False
            except MapError:
                pass
        try:
            elements = self._osm_api_elements(lat, lon, radius)
        except MapError:
            body = "\n".join(
                f"  {item}(around:{radius},{lat:.6f},{lon:.6f});" for item in filters
            )
            query = f"[out:json][timeout:30];\n(\n{body}\n);\nout center tags 400;"
            elements = self._overpass(query, timeout=min(self.timeout, 10.0)).get("elements") or []
        self._cache_put(key, (elements, True))
        return elements, True

    def mall(self, query: str, radius: int = 0, places: Optional[list[dict]] = None) -> dict:
        text = str(query or "").strip()
        if not text:
            raise MapError("Mağazaları listelemek için AVM/plaza adı gerekli.")
        search_text = _strip_mall_intent(text)
        core_text = _strip_mall_suffix(search_text)
        if not _mall_name_core(text):
            raise MapError("Mağaza listesi için AVM veya plaza adı belirt.")
        attempts = [search_text or text]
        if core_text and _fold(core_text) != _fold(search_text):
            attempts.append(core_text)
        candidate = self._pick_mall_candidate(text, list(places or []))
        for attempt in ([] if candidate else attempts):
            try:
                found = self.geocode_all(attempt, 5)
            except MapError:
                continue
            candidate = self._pick_mall_candidate(attempt, found)
            if candidate:
                break
        if not candidate:
            raise MapError(
                f"'{text}' için bir AVM/plaza bulunamadı. Adı veya adresi biraz daha açık yaz."
            )
        try:
            lat, lon = float(candidate["lat"]), float(candidate["lon"])
        except (KeyError, TypeError, ValueError) as error:
            raise MapError("AVM konumu okunamadı.") from error
        name = str(candidate.get("name") or search_text or text).strip()
        try:
            wanted = int(radius)
        except (TypeError, ValueError):
            wanted = 0
        radius_m = (
            max(120, min(wanted, 1000)) if wanted > 0 else self._bbox_radius(candidate)
        )
        mall = {
            "name": name[:120],
            "lat": lat,
            "lon": lon,
            "address": str(candidate.get("address") or "").strip()[:200],
            "region": str(candidate.get("region") or "").strip()[:80],
            "kind": str(candidate.get("kind") or "mall"),
            "osm_type": str(candidate.get("osm_type") or ""),
            "osm_id": str(candidate.get("osm_id") or ""),
            "radius_m": radius_m,
        }
        store_error = ""
        try:
            elements, used_radius = self._mall_store_elements(mall, radius_m)
        except MapError as error:
            elements, used_radius = [], False
            store_error = str(error)
        mall_tags = self._mall_own_tags(elements, mall)
        mall["photo"] = self._photo_url(mall_tags)
        stores = self._parse_mall_stores(elements, name)
        mall["store_count"] = len(stores)
        result = {
            "mall": mall,
            "stores": stores,
            "source": "radius" if used_radius else "area",
            "store_error": store_error,
        }
        self._emit({
            "kind": "mall",
            "mall": mall,
            "stores": stores,
            "store_error": store_error,
            "center": {"name": name, "lat": lat, "lon": lon},
            "zoom": 16,
        })
        return result

    def format_mall(self, result: dict) -> str:
        mall = result["mall"]
        stores = result["stores"]
        header = f"🛍 {mall['name']}"
        if mall.get("address"):
            header += f" · {mall['address']}"
        lines = [header]
        if stores:
            counts: dict[str, int] = {}
            for store in stores:
                counts[store["category"]] = counts.get(store["category"], 0) + 1
            top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]
            lines.append(
                f"{len(stores)} mağaza/işletme bulundu. Kategoriler: "
                + ", ".join(f"{label} ({count})" for label, count in top)
            )
            for store in stores[:30]:
                detail = [store["category"]]
                if store.get("level"):
                    detail.append(store["level"])
                if store.get("phone"):
                    detail.append(f"☎ {store['phone']}")
                if store.get("price"):
                    detail.append(f"fiyat {store['price']}")
                lines.append(f"• {store['name']} — " + " · ".join(detail))
            if len(stores) > 30:
                lines.append(f"… ve {len(stores) - 30} işletme daha")
            lines.append(
                "Haritadaki AVM logosuna tıkla; açılan listeden bir mağaza seçerek "
                "bilgi kartını aç."
            )
            if result.get("source") == "radius":
                lines.append(
                    "ℹ️ AVM bina sınırı okunamadığı için yakın çevredeki işletmeler listelendi."
                )
        elif result.get("store_error"):
            lines.append(
                "Mağaza servisi şu an yanıt vermedi; liste alınamadı. Haritadaki AVM logosu yine "
                "görünür. Birkaç dakika sonra aynı soruyu tekrar dene."
            )
        else:
            lines.append(
                "OSM'de bu AVM için mağaza kaydı bulunamadı. Haritadaki AVM logosu yine görünür; "
                "yeni kayıtlar eklendikçe liste güncellenir."
            )
        lines.append(
            "ℹ️ Mağaza adı, kat, telefon gibi bilgiler OpenStreetMap katkılarıdır; eksik veya güncel "
            "olmayabilir. Bilinmeyen bilgi uydurulmaz."
        )
        return "\n".join(lines)

    # --------------------------------------------------------- Kocaeli otobüs
    def _nearest_stops(self, lat: float, lon: float) -> list[dict]:
        key = f"near:{lat:.4f},{lon:.4f}"
        cached = self._cache_get(key, 120)
        if cached:
            return list(cached)
        headers = self._security_headers()
        headers["Referer"] = EKOMOBIL_BASE
        headers["Origin"] = EKOMOBIL_BASE.rstrip("/")
        response = self._request(
            "POST",
            EKOMOBIL_NEAREST,
            data={"func": "ns", "lat": f"{lat:.7f}", "lon": f"{lon:.7f}"},
            headers=headers,
        )
        try:
            data = json.loads(response.text)
        except ValueError as error:
            raise MapError("Kocaeli durak servisi geçersiz yanıt döndü.") from error
        stops = self.parse_nearest_stops(data)
        self._cache_put(key, stops)
        return stops

    @staticmethod
    def parse_nearest_stops(data) -> list[dict]:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return []
        names = data.get("name") or []
        stops = []
        for index, name in enumerate(names):
            try:
                stop_id = str((data.get("id") or [])[index])
                lat = float((data.get("lat") or [])[index])
                lon = float((data.get("lon") or [])[index])
                distance_km = float((data.get("dist") or [])[index])
            except (IndexError, TypeError, ValueError):
                continue
            stops.append({
                "stop_id": stop_id,
                "name": str(name).strip(),
                "lat": lat,
                "lon": lon,
                "distance_m": int(round(distance_km * 1000)),
            })
        stops.sort(key=lambda item: item["distance_m"])
        return stops

    @staticmethod
    def _input_values(html_text: str) -> list[str]:
        return [unescape(value) for value in re.findall(r'<input[^>]+value="([^"]+)"', html_text or "", re.I)]

    def _stop_details(self, stop_id: str) -> dict:
        stop_id = str(stop_id or "").strip()
        if not stop_id:
            raise MapError("Durak numarası belirtilmedi.")
        key = f"stop:{stop_id}"
        cached = self._cache_get(key, 45)
        if cached:
            return dict(cached)
        info_response = self._ekomobil("searchSmartStopJSON", {"stop_id": stop_id}, "POST")
        try:
            info = json.loads(info_response.text)
        except ValueError as error:
            raise MapError("Durak bilgisi okunamadı.") from error
        if not info or not info.get("busStopid"):
            raise MapError(f"{stop_id} numaralı durak bulunamadı.")
        passing_response = self._ekomobil("searchSmartStop", {"stop_id": stop_id})
        live_response = self._ekomobil("searchBusesontheStop", {"stop_id": stop_id})
        result = {
            "stop": {
                "id": str(info.get("busStopid") or stop_id),
                "name": str(info.get("busStopName") or "Durak"),
                "lat": float(info.get("lat")),
                "lon": float(info.get("lng")),
            },
            "lines": self.parse_pass_lines(passing_response.text),
            "live": self.parse_live_buses(live_response.text),
        }
        self._cache_put(key, result)
        return result

    @staticmethod
    def parse_pass_lines(html_text: str) -> list[dict]:
        lines = []
        seen = set()
        for code, label in re.findall(
            r"openRouteLinkMain\('([^']+)'\)[^>]*>\s*([^<]+?)\s*</a>",
            html_text or "",
            re.S | re.I,
        ):
            code = unescape(code).strip()
            if not code or code in seen:
                continue
            seen.add(code)
            lines.append({"code": code, "label": _clean(label)})
        return lines

    @staticmethod
    def parse_live_buses(html_text: str) -> list[dict]:
        buses = []
        for value in MapTools._input_values(html_text):
            match = re.match(
                r"^(-?[\d.]+)\+(-?[\d.]+)\+<center>(.*?)<br\s*/?>(.*?)</center>"
                r"\+([^+]*)\+([^+]*)\+([^+]*)\+([^+]*)$",
                value,
                re.S | re.I,
            )
            if not match:
                continue
            try:
                lat, lon = float(match.group(1)), float(match.group(2))
            except ValueError:
                continue
            label = _clean(match.group(3))
            plate = _clean(match.group(4))
            route_code = match.group(6).strip()
            if not route_code:
                continue
            try:
                stops_away = int(float(match.group(7)))
                eta_min = int(float(match.group(8)))
            except ValueError:
                stops_away, eta_min = None, None
            buses.append({
                "route_code": route_code,
                "label": label,
                "vehicle": plate,
                "lat": lat,
                "lon": lon,
                "stops_away": stops_away,
                "eta_min": eta_min,
            })
        buses.sort(key=lambda item: item["eta_min"] if item["eta_min"] is not None else 999)
        return buses

    def stop_details(self, stop_id: str) -> dict:
        result = self._stop_details(stop_id)
        stop = result["stop"]
        lines = result["lines"]
        live = result["live"]
        distance = _distance_m(self.home()["lat"], self.home()["lon"], stop["lat"], stop["lon"])
        payload_items = [{
            "name": f"{stop['id']} · {stop['name']}",
            "lat": stop["lat"],
            "lon": stop["lon"],
            "kind": "stop",
            "category": "Durak",
            "stop_id": stop["id"],
            "lines": [line["code"] for line in lines[:8]],
            "live": [
                {"code": bus["route_code"], "eta_min": bus["eta_min"]}
                for bus in live[:4] if bus.get("eta_min") is not None
            ],
            "detail": (
                "Canlı: " + ", ".join(f"{bus['route_code']} {bus['eta_min']} dk" for bus in live[:3])
                if live else "Geçen hatlar: " + ", ".join(line["code"] for line in lines[:8])
            ),
        }]
        self._emit({
            "kind": "places",
            "title": f"Durak {stop['id']}",
            "center": {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"]},
            "items": payload_items,
            "zoom": 16,
        })
        return {
            "stop": stop,
            "distance_m": int(round(distance)),
            "lines": lines,
            "live": live,
        }

    def format_stop_details(self, result: dict) -> str:
        stop = result["stop"]
        lines = result["lines"]
        live = result["live"]
        header = f"🚏 {stop['id']} · {stop['name']} · evime {result['distance_m']} m"
        output = [header]
        if live:
            output.append("Canlı yaklaşan otobüsler:")
            for bus in live[:8]:
                extra = []
                if bus["stops_away"] is not None:
                    extra.append(f"{bus['stops_away']} durak")
                output.append(
                    f"• {bus['label']} — yaklaşık {bus['eta_min']} dk"
                    + (f" ({', '.join(extra)})" if extra else "")
                )
        else:
            output.append("Şu an bu durağa yaklaşan otobüs bilgisi yok.")
        if lines:
            output.append("Bu duraktan geçen hatlar: " + ", ".join(line["code"] for line in lines))
        output.append(
            "ℹ️ Varış süreleri resmî e-komobil canlı verisidir; trafik ve sefer durumuna göre değişebilir."
        )
        return "\n".join(output)

    def bus_stops(self, center=None, radius: int = 1800, enrich: int = 4) -> dict:
        origin = self._resolve_place(center or "evim")
        try:
            radius = max(100, min(int(radius), 5000))
        except (TypeError, ValueError):
            radius = 1800
        try:
            stops = self._nearest_stops(origin["lat"], origin["lon"])
        except MapError:
            stops = []
        if not stops:
            stops = self._osm_bus_stops(origin, radius)
        stops = [stop for stop in stops if stop["distance_m"] <= radius]
        stops = stops[:8]
        details = []
        for stop in stops[:max(0, min(int(enrich), 6))]:
            try:
                detail = self._stop_details(stop["stop_id"])
                details.append({**stop, **detail})
            except MapError:
                details.append({**stop, "lines": [], "live": []})
        for stop in stops[len(details):]:
            details.append({**stop, "lines": [], "live": []})
        items = []
        for stop in details:
            live_text = ", ".join(
                f"{bus['route_code']} {bus['eta_min']} dk" for bus in stop.get("live", [])[:3]
            )
            lines_text = ", ".join(line["code"] for line in stop.get("lines", [])[:8])
            detail = f"{stop['distance_m']} m"
            if live_text:
                detail += f" · canlı: {live_text}"
            elif lines_text:
                detail += f" · hatlar: {lines_text}"
            items.append({
                "name": f"{stop['stop_id']} · {stop['name']}",
                "lat": stop["lat"],
                "lon": stop["lon"],
                "kind": "stop",
                "category": "Durak",
                "stop_id": stop["stop_id"],
                "lines": [line["code"] for line in stop.get("lines", [])[:8]],
                "live": [
                    {"code": bus["route_code"], "eta_min": bus["eta_min"]}
                    for bus in stop.get("live", [])[:4] if bus.get("eta_min") is not None
                ],
                "detail": detail,
            })
        self._emit({
            "kind": "places",
            "title": f"{origin['name']} çevresindeki duraklar",
            "center": self._public_place(origin),
            "items": items,
            "zoom": 15,
        })
        return {"center": self._public_place(origin), "radius": radius, "stops": details}

    def format_bus_stops(self, result: dict) -> str:
        center = result["center"]
        stops = result["stops"]
        if not stops:
            return (
                f"❌ {center['name']} çevresinde resmî durak bulunamadı. "
                "Konumu veya yarıçapı değiştirip yeniden dene."
            )
        lines = [f"🚏 {center['name']} çevresindeki duraklar ({result['radius']} m):"]
        for stop in stops:
            text = f"{stop['stop_id']} · {stop['name']} — {stop['distance_m']} m"
            if stop.get("live"):
                text += " · canlı: " + ", ".join(
                    f"{bus['route_code']} {bus['eta_min']} dk" for bus in stop["live"][:3]
                )
            elif stop.get("lines"):
                text += " · hatlar: " + ", ".join(
                    line["code"] for line in stop["lines"][:8]
                )
            lines.append(text)
        lines.append(
            "ℹ️ Canlı süreler resmî e-komobil verisidir; durak numarasıyla ayrıntılı sorgu yapılabilir."
        )
        return "\n".join(lines)

    def _osm_bus_stops(self, origin: dict, radius: int) -> list[dict]:
        query = (
            "[out:json][timeout:25];\n(\n"
            f'  nwr["highway"="bus_stop"](around:{radius},{origin["lat"]},{origin["lon"]});\n'
            f'  nwr["public_transport"="platform"]["bus"="yes"](around:{radius},{origin["lat"]},{origin["lon"]});\n'
            ");\nout center tags 40;"
        )
        try:
            payload = self._overpass(query)
        except MapError:
            return []
        stops = []
        for element in payload.get("elements") or []:
            tags = element.get("tags") or {}
            point = element.get("center") or element
            try:
                lat, lon = float(point["lat"]), float(point["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(tags.get("name") or tags.get("ref") or "Durak").strip()
            stop_id = str(tags.get("ref") or element.get("id") or "").strip()
            stops.append({
                "stop_id": stop_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "distance_m": int(round(_distance_m(origin["lat"], origin["lon"], lat, lon))),
            })
        stops.sort(key=lambda item: item["distance_m"])
        return stops

    # --------------------------------------------------------------- hatlar
    def line_catalog(self) -> list[dict]:
        cached = self._cache_get("line_catalog", 86400)
        if cached:
            return list(cached)
        response = self._request("GET", KOCAELI_LINES_URL, headers={"User-Agent": USER_AGENT})
        catalog = []
        for code, label in re.findall(
            r'<option\s+value="([^"]+)"[^>]*>(.*?)</option>',
            response.text,
            re.S | re.I,
        ):
            code = unescape(code).strip()
            label = _clean(label)
            if code and label and not _fold(label).startswith("hat seç"):
                catalog.append({"code": code, "label": label})
        if catalog:
            self._cache_put("line_catalog", catalog)
        return catalog

    def _match_line(self, query: str):
        text = str(query or "").strip()
        if not text:
            return None, []
        try:
            catalog = self.line_catalog()
        except MapError:
            return {"code": text.upper(), "label": text}, []
        folded = _fold(text)
        for item in catalog:
            if _fold(item["code"]) == folded:
                return item, []
        matches = [item for item in catalog if folded in _fold(item["label"])]
        if len(matches) == 1:
            return matches[0], []
        if matches:
            return None, matches[:10]
        return None, []

    def bus_line(self, query: str, direction: int = 0) -> dict:
        line, candidates = self._match_line(query)
        if not line:
            if candidates:
                listing = "\n".join(f"{item['code']} · {item['label']}" for item in candidates)
                raise MapError(f"'{query}' için birden fazla hat bulundu:\n{listing}\nHat numarasını belirt.")
            raise MapError(f"'{query}' ile eşleşen Kocaeli otobüs hattı bulunamadı.")
        try:
            direction = 1 if int(direction) == 1 else 0
        except (TypeError, ValueError):
            direction = 0
        directions_response = self._ekomobil(
            "searchRouteDirections", {"route_code": line["code"]}, "POST"
        )
        try:
            direction_data = json.loads(directions_response.text)
        except ValueError:
            direction_data = {}
        schedule_response = self._ekomobil(
            "searchRouteSchedule",
            {"route_code": line["code"], "direction": direction},
            "POST",
        )
        stops_response = self._ekomobil(
            "searchRouteStops",
            {"route_code": line["code"], "direction": direction},
        )
        try:
            stops_html = json.loads(stops_response.text)
        except ValueError:
            stops_html = stops_response.text
        stops = self.parse_route_stops(stops_html)
        schedule = self.parse_schedule(schedule_response.text)
        schedule["direction"] = direction
        schedule["direction_name"] = str(
            direction_data.get(f"direct{direction}")
            or (line["label"] if direction == 0 else "")
        )
        if stops:
            schedule["from"] = stops[0]["name"]
            schedule["to"] = stops[-1]["name"]
        else:
            schedule["from"] = schedule["direction_name"]
            schedule["to"] = ""
        result = {
            "code": line["code"],
            "label": line["label"],
            "directions": [direction_data.get("direct0", ""), direction_data.get("direct1", "")],
            "schedule": schedule,
            "stops": stops,
        }
        self._emit({
            "kind": "bus_line",
            "line": {
                "code": line["code"],
                "label": line["label"],
                "from": schedule["from"],
                "to": schedule["to"],
            },
            "stops": stops,
            "center": (
                {
                    "name": schedule["from"],
                    "lat": stops[0]["lat"] if stops else self.home()["lat"],
                    "lon": stops[0]["lon"] if stops else self.home()["lon"],
                }
            ),
            "zoom": 12,
        })
        return result

    @staticmethod
    def parse_route_stops(html_text: str) -> list[dict]:
        stops = []
        for value in MapTools._input_values(html_text):
            parts = value.split("+", 1)
            if len(parts) != 2:
                continue
            stop_id, rest = parts
            try:
                name, lat, lon = rest.rsplit("+", 2)
                lat, lon = float(lat), float(lon)
            except ValueError:
                continue
            stops.append({
                "stop_id": stop_id.strip(),
                "name": _clean(name),
                "lat": lat,
                "lon": lon,
            })
        return stops

    @staticmethod
    def parse_schedule(html_text: str) -> dict:
        schedule = {"weekday": [], "saturday": [], "sunday": [], "notes": [], "updated": ""}
        headers = {"açıklama", "saat", "renk", "not", "gün", "sefer", "hafta içi"}
        for row in _parse_rows(html_text):
            if len(row) >= 4 and row[0].strip().isdigit():
                for key, index in (("weekday", 1), ("saturday", 2), ("sunday", 3)):
                    for value in re.findall(r"\b\d{1,2}[:.]\d{2}\b", row[index]):
                        normalized = value.replace(".", ":")
                        if normalized not in schedule[key]:
                            schedule[key].append(normalized)
            elif len(row) >= 2 and row[1]:
                note = row[1].strip()
                folded = _fold(note)
                if folded in headers or folded.startswith("güncelleme"):
                    continue
                schedule["notes"].append(note)
        update = re.search(r"GÜNCELLEME:\s*(?:</strong>)?\s*([0-9.\-: ]{8,20})", html_text, re.I)
        if update:
            schedule["updated"] = update.group(1).strip()
        schedule["notes"] = list(dict.fromkeys(schedule["notes"]))[:8]
        return schedule

    def format_bus_line(self, result: dict) -> str:
        schedule = result["schedule"]
        lines = [
            f"🚌 Kocaeli {result['code']} · {result['label']}",
            f"Yön: {schedule['from']} → {schedule['to']}",
        ]
        for key, label in (("weekday", "Hafta içi"), ("saturday", "Cumartesi"), ("sunday", "Pazar/tatil")):
            times = schedule.get(key) or []
            if times:
                lines.append(f"{label} ({len(times)}): " + ", ".join(times))
        if result["stops"]:
            names = [stop["name"] for stop in result["stops"]]
            lines.append(f"Duraklar ({len(names)}): " + " → ".join(names))
        if schedule.get("updated"):
            lines.append(f"Tarife güncellemesi: {schedule['updated']}")
        for note in schedule.get("notes", [])[:3]:
            lines.append(f"Not: {note}")
        lines.append(
            "ℹ️ Bu saatler hattın başlangıç peronundan resmî kalkış tarifesidir; canlı durak varışı için "
            "bus_stop ile durak numarasını sorgula. Uydurma canlı süre verilmez."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------ ajan
    def run(self, args: dict) -> str:
        action = str(args.get("action") or "").strip().lower()
        try:
            if action == "home":
                home = self.home()
                center = {**home, "label": f"{home['name']}, {home['region']}"}
                self._emit({"kind": "home", "home": home, "center": center, "zoom": 13})
                if self._home_is_set():
                    return (
                        f"🏠 Ev konumu: {home['name']} · {home['region']} · "
                        f"{home['lat']:.5f}, {home['lon']:.5f}. Rotalarda varsayılan başlangıç bu nokta."
                    )
                return (
                    f"ℹ️ Henüz özel ev konumu işaretlenmedi. Şu an varsayılan merkez: "
                    f"{home['name']} · {home['region']} ({home['lat']:.5f}, {home['lon']:.5f}). "
                    "Haritada EVİM YAP ile veya 'evim şurası: ...' diyerek kaydedebilirsin."
                )
            if action == "set_home":
                lat, lon = args.get("lat"), args.get("lon")
                if lat is not None and lon is not None:
                    try:
                        place = {
                            "name": str(args.get("name") or "Evim"),
                            "region": "Kocaeli",
                            "lat": float(lat),
                            "lon": float(lon),
                        }
                    except (TypeError, ValueError) as error:
                        raise MapError("Geçerli bir enlem ve boylam gerekli.") from error
                else:
                    place = self._resolve_place(args.get("query") or "evim")
                home = self._set_home(place, str(args.get("name") or ""))
                return (
                    f"✅ Ev konumu kaydedildi: {home['name']} · {home['region']} · "
                    f"{home['lat']:.5f}, {home['lon']:.5f}. Rotalar artık buradan başlar."
                )
            if action == "route":
                destination = args.get("destination") or args.get("query")
                if not destination:
                    raise MapError("Rota için hedef konum gerekli.")
                route = self.route(
                    args.get("origin") or "evim",
                    destination,
                    str(args.get("mode") or "driving"),
                )
                return self.format_route(route)
            if action == "nearby":
                query = args.get("query") or args.get("category")
                if query and self._is_mall_query(str(query)):
                    try:
                        return self.format_mall(self.mall(str(query)))
                    except MapError:
                        pass
                result = self.nearby(query, args.get("center") or "evim", args.get("radius", 1500))
                return self.format_nearby(result)
            if action == "mall":
                query = args.get("query") or args.get("mall") or args.get("name")
                if not query:
                    raise MapError("AVM/plaza adı gerekli.")
                return self.format_mall(self.mall(str(query), args.get("radius", 0)))
            if action == "bus_stops":
                result = self.bus_stops(args.get("center") or "evim", args.get("radius", 1800))
                return self.format_bus_stops(result)
            if action == "bus_stop":
                stop_id = args.get("stop_id") or args.get("query")
                if not stop_id:
                    raise MapError("Durak numarası gerekli.")
                result = self.stop_details(str(stop_id))
                return self.format_stop_details(result)
            if action == "bus_line":
                query = args.get("line") or args.get("query")
                if not query:
                    raise MapError("Hat numarası veya adı gerekli.")
                result = self.bus_line(query, args.get("direction", 0))
                return self.format_bus_line(result)
            if action == "geocode":
                query = str(args.get("query") or "").strip()
                if not query:
                    raise MapError("Aranacak yer adı gerekli.")
                places = self.geocode_all(query, 3)
                if self._is_mall_query(query) or self._is_mall_place(places[0]):
                    try:
                        return self.format_mall(self.mall(query, places=places))
                    except MapError:
                        pass
                self._emit({
                    "kind": "focus",
                    "center": {
                        "name": places[0]["name"],
                        "lat": places[0]["lat"],
                        "lon": places[0]["lon"],
                    },
                    "zoom": 14,
                })
                lines = [f"📍 '{query}' için bulunan konumlar:"]
                for index, place in enumerate(places, 1):
                    lines.append(
                        f"{index}. {place['name']} · {place['label']} · {place['lat']:.5f}, {place['lon']:.5f}"
                    )
                return "\n".join(lines)
            raise MapError(f"Desteklenmeyen harita işlemi: {action or 'boş'}")
        except MapError as error:
            return f"❌ {error}"
        except (ValueError, TypeError) as error:
            return f"❌ Harita işlemi tamamlanamadı: {error}"
