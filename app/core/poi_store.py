"""Yerel, kotasız mekân indeksi: Overture Maps Places -> SQLite.

Overture aylık açık veri olarak yayımlanır; API anahtarı ve istek kotası yoktur.
Uygulama yalnızca ihtiyaç duyduğu bölgeyi arka planda indirip SQLite'a yazar,
sonraki sorgular yerelden anında yanıtlanır. Açılış saatleri ve ek etiketler
için OpenStreetMap canlı verisi ayrıca üstüne bindirilir.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

OVERTURE_RELEASE = "2026-10-21.0"
OVERTURE_PLACES_URL = (
    "s3://overturemaps-us-west-2/release/"
    f"{OVERTURE_RELEASE}/theme=places/type=place/*"
)
OVERTURE_ATTRIBUTION = "Overture Maps"
COVERAGE_TTL_SECONDS = 45 * 86400
MAX_IMPORT_SPAN_LAT = 2.0
MAX_IMPORT_SPAN_LON = 3.0

_FOLD_MAP = str.maketrans({
    "İ": "i", "I": "i", "ı": "i",
    "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c",
    "Â": "a", "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u",
})


def fold_tr(text) -> str:
    """Türkçe büyük/küçük harf ve aksan farklarını arama için düzleştirir."""
    return str(text or "").translate(_FOLD_MAP).lower().strip()


_EXACT_KINDS = {
    "shopping_mall": "mall",
    "department_store": "mall",
    "grocery_store": "supermarket",
    "supermarket": "supermarket",
    "convenience_store": "convenience",
    "clothing_store": "clothes",
    "womens_clothing_store": "clothes",
    "mens_clothing_store": "clothes",
    "childrens_clothing_store": "clothes",
    "shoe_store": "shoes",
    "jewelry_store": "jewelry",
    "electronics_store": "electronics",
    "mobile_phone_store": "mobile_phone",
    "bookstore": "books",
    "book_store": "books",
    "toy_store": "toys",
    "furniture_store": "furniture",
    "hardware_store": "hardware",
    "building_supply_store": "hardware",
    "home_goods_store": "houseware",
    "sporting_goods_store": "sports",
    "beauty_salon": "beauty",
    "beauty_supply_store": "beauty",
    "spa": "beauty",
    "hair_salon": "hairdresser",
    "barber": "barber",
    "optical_store": "optician",
    "pet_store": "pet",
    "flowers_and_gifts_store": "florist",
    "gift_shop": "gift",
    "florist": "florist",
    "stationery_store": "stationery",
    "bakery": "bakery",
    "butcher": "butcher",
    "greengrocer": "greengrocer",
    "deli": "deli",
    "pharmacy": "pharmacy",
    "drugstore": "pharmacy",
    "hospital": "hospital",
    "dental_clinic": "dentist",
    "dentist": "dentist",
    "doctors_office": "doctors",
    "doctor": "doctors",
    "veterinarian": "veterinary",
    "cafe": "cafe",
    "coffee_shop": "cafe",
    "tea_room": "cafe",
    "restaurant": "restaurant",
    "fast_food_restaurant": "fast_food",
    "food_court": "food_court",
    "dessert_shop": "ice_cream",
    "ice_cream_shop": "ice_cream",
    "bar": "bar",
    "pub": "pub",
    "steakhouse": "restaurant",
    "pizza_restaurant": "restaurant",
    "doner_kebab_restaurant": "restaurant",
    "burger_restaurant": "restaurant",
    "seafood_restaurant": "restaurant",
    "movie_theater": "cinema",
    "cinema": "cinema",
    "theater": "theatre",
    "bank": "bank",
    "bank_or_credit_union": "bank",
    "atm": "atm",
    "gas_station": "fuel",
    "parking": "parking",
    "post_office": "post_office",
    "school": "school",
    "elementary_school": "school",
    "high_school": "school",
    "preschool": "school",
    "college_university": "university",
    "university": "university",
    "college": "college",
    "government_office": "government",
    "hotel": "hotel",
    "guest_house": "guest_house",
    "gym": "fitness_centre",
    "fitness_center": "fitness_centre",
    "sports_center": "sports_centre",
    "park": "park",
    "laundry": "laundry",
    "dry_cleaner": "dry_cleaning",
    "auto_dealer": "car",
    "car_dealer": "car",
    "auto_repair": "car_repair",
    "automotive_repair": "car_repair",
    "bicycle_store": "bicycle",
    "travel_agency": "travel_agency",
    "real_estate_agent": "real_estate",
    "insurance_agency": "insurance",
    "tattoo_parlor": "tattoo",
    "museum": "museum",
}

_CONTAINS_KINDS = (
    ("restaurant", "restaurant"),
    ("kebab", "restaurant"),
    ("pizzeria", "restaurant"),
    ("cafe", "cafe"),
    ("coffee", "cafe"),
    ("bakery", "bakery"),
    ("pharmacy", "pharmacy"),
    ("hospital", "hospital"),
    ("clinic", "clinic"),
    ("hotel", "hotel"),
    ("motel", "hotel"),
    ("gas_station", "fuel"),
    ("parking", "parking"),
    ("supermarket", "supermarket"),
    ("grocery", "supermarket"),
    ("market", "supermarket"),
    ("clothing", "clothes"),
    ("shoe", "shoes"),
    ("jewelry", "jewelry"),
    ("electronics", "electronics"),
    ("phone", "mobile_phone"),
    ("book", "books"),
    ("toy", "toys"),
    ("furniture", "furniture"),
    ("hardware", "hardware"),
    ("flower", "florist"),
    ("barber", "barber"),
    ("hair", "hairdresser"),
    ("beauty", "beauty"),
    ("optic", "optician"),
    ("pet", "pet"),
    ("gift", "gift"),
    ("stationery", "stationery"),
    ("laundry", "laundry"),
    ("cleaner", "dry_cleaning"),
    ("bank", "bank"),
    ("fuel", "fuel"),
    ("school", "school"),
    ("university", "university"),
    ("government", "government"),
    ("gym", "fitness_centre"),
    ("fitness", "fitness_centre"),
    ("sport", "sports"),
    ("park", "park"),
    ("convenience", "convenience"),
    ("mall", "mall"),
)

CATEGORY_LABELS = {
    "mall": "AVM · Alışveriş merkezi",
    "supermarket": "Market · Süpermarket",
    "convenience": "Market · Bakkal",
    "clothes": "Giyim · Mağaza",
    "shoes": "Ayakkabı",
    "jewelry": "Kuyumcu · Takı",
    "electronics": "Elektronik",
    "mobile_phone": "Cep telefonu",
    "books": "Kitapçı",
    "toys": "Oyuncak",
    "furniture": "Mobilya",
    "hardware": "Hırdavat · Nalbur",
    "houseware": "Ev ürünleri",
    "sports": "Spor mağazası",
    "beauty": "Güzellik salonu",
    "hairdresser": "Kuaför",
    "barber": "Berber",
    "optician": "Optik",
    "pet": "Pet shop",
    "gift": "Hediyelik",
    "stationery": "Kırtasiye",
    "pharmacy": "Eczane",
    "hospital": "Hastane",
    "clinic": "Klinik",
    "doctors": "Doktor",
    "dentist": "Diş kliniği",
    "veterinary": "Veteriner",
    "restaurant": "Restoran · Yemek",
    "fast_food": "Fast food · Yemek",
    "cafe": "Kafe · Kahve",
    "food_court": "Yeme-içme alanı",
    "ice_cream": "Dondurma",
    "bar": "Bar",
    "pub": "Pub",
    "cinema": "Sinema",
    "theatre": "Tiyatro",
    "bank": "Banka",
    "atm": "ATM",
    "fuel": "Benzin istasyonu",
    "parking": "Otopark",
    "post_office": "PTT",
    "school": "Okul",
    "university": "Üniversite",
    "college": "Kolej",
    "government": "Kamu dairesi",
    "hotel": "Otel",
    "guest_house": "Pansiyon",
    "fitness_centre": "Spor salonu",
    "sports_centre": "Spor merkezi",
    "park": "Park",
    "laundry": "Çamaşırhane",
    "dry_cleaning": "Kuru temizleme",
    "car": "Oto galeri",
    "car_repair": "Oto tamir",
    "bicycle": "Bisikletçi",
    "florist": "Çiçekçi",
    "travel_agency": "Seyahat acentesi",
    "real_estate": "Emlakçı",
    "insurance": "Sigorta",
    "tattoo": "Dövme stüdyosu",
    "museum": "Müze",
    "place": "Mekân",
}


def ui_kind(raw) -> str:
    key = fold_tr(raw).replace(" ", "_")
    if not key:
        return ""
    exact = _EXACT_KINDS.get(key)
    if exact:
        return exact
    for needle, kind in _CONTAINS_KINDS:
        if needle in key:
            return kind
    return ""


def category_label(raw) -> str:
    return CATEGORY_LABELS.get(ui_kind(raw) or "place", "Mekân")


_NAME_CATEGORY_RULES = (
    (r"mezarl", "place", "Mezarlık"),
    (r"\b(cami|mosque|mescit)\b", "place", "İbadet yeri"),
    (r"(yap[ıi] market|h[ıi]rdavat|nalbur)", "hardware", "Hırdavat · Nalbur"),
    (r"(eczane|pharmacy)", "pharmacy", "Eczane"),
    (r"\b(otel|hotel|pansiyon)\b", "hotel", "Otel"),
    (r"(restoran|restaurant|lokanta|kebap|d[oö]ner|pide|pizza|burger|steakhouse)",
     "restaurant", "Restoran · Yemek"),
    (r"(kafe|cafe|kahve|coffee|bistro|espresso)", "cafe", "Kafe · Kahve"),
    (r"(f[ıi]r[ıi]n|bakery)", "bakery", "Fırın"),
    (r"(kasap|butcher)", "butcher", "Kasap"),
    (r"(kuaf[oö]r|hair salon)", "hairdresser", "Kuaför"),
    (r"\bberber\b", "barber", "Berber"),
    (r"(g[üu]zellik|beauty)", "beauty", "Güzellik salonu"),
    (r"(a101|\bbim\b|\bmigros\b|carrefour|\b[şs]ok\b|s[üu]permarket|gros|market|bakkal)",
     "supermarket", "Market · Süpermarket"),
    (r"(\bavm\b|al[ıi][şs]veri[şs] merkezi|shopping (center|centre)|\bmall\b|outlet|pasaj)",
     "mall", "AVM · Alışveriş merkezi"),
    (r"(\bptt\b|postane|post office)", "post_office", "PTT"),
    (r"(belediye|municipality)", "government", "Kamu dairesi"),
    (r"(hastane|hospital)", "hospital", "Hastane"),
    (r"(di[şs] klini|dentist|a[ğg][ıi]z sa[ğg]l[ıi][ğg][ıi])", "dentist", "Diş kliniği"),
    (r"(klinik|clinic)", "clinic", "Klinik"),
    (r"(veteriner|veterinary)", "veterinary", "Veteriner"),
    (r"(sinema|cinema|movie theater)", "cinema", "Sinema"),
    (r"(\bokul\b|school)", "school", "Okul"),
    (r"([üu]niversite|university)", "university", "Üniversite"),
    (r"(k[ıi]rtasiye|stationery)", "stationery", "Kırtasiye"),
    (r"(kitap[çc][ıi]|book ?store)", "books", "Kitapçı"),
    (r"([çc]i[çc]ek[çc]i|florist)", "florist", "Çiçekçi"),
    (r"(petshop|pet shop)", "pet", "Pet shop"),
)


def infer_name_category(name) -> Optional[tuple[str, str]]:
    """İsimdeki güçlü ipucu, Overture'ın hatalı taksonomisini düzeltir."""
    folded = fold_tr(name)
    if not folded:
        return None
    for pattern, kind, label in _NAME_CATEGORY_RULES:
        if re.search(pattern, folded):
            return kind, label
    return None


def _first_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
    return ""


def _join_address(addresses) -> str:
    if not addresses:
        return ""
    first = addresses[0] if isinstance(addresses, (list, tuple)) else addresses
    if not isinstance(first, dict):
        return str(first or "").strip()[:240]
    parts = []
    freeform = str(first.get("freeform") or "").strip()
    locality = str(first.get("locality") or "").strip()
    region = str(first.get("region") or "").strip()
    if freeform:
        parts.append(freeform)
    if locality and fold_tr(locality) not in fold_tr(freeform):
        parts.append(locality)
    if region and fold_tr(region) not in fold_tr(" ".join(parts)):
        parts.append(region)
    return ", ".join(dict.fromkeys(part for part in parts if part))[:240]


def _latest_update(sources) -> str:
    if not sources:
        return ""
    values = []
    for source in sources:
        if isinstance(source, dict):
            value = str(source.get("update_time") or "").strip()
            if value:
                values.append(value)
    return max(values) if values else ""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _normalize_bbox(min_lat, max_lat, min_lon, max_lon):
    south = _clamp(min_lat, -90.0, 90.0)
    north = _clamp(max_lat, -90.0, 90.0)
    west = _clamp(min_lon, -180.0, 180.0)
    east = _clamp(max_lon, -180.0, 180.0)
    if north < south:
        south, north = north, south
    if east < west:
        west, east = east, west
    return south, north, west, east


_IMPORT_THREADS: dict[str, threading.Thread] = {}
_IMPORT_LOCK = threading.Lock()


class PoiStore:
    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.last_error = ""
        self._unavailable = False
        self._ensure_schema()

    @classmethod
    def default(cls, settings=None) -> Optional["PoiStore"]:
        if settings is None:
            return None
        base = getattr(settings, "data_dir", None)
        if not base:
            return None
        try:
            return cls(Path(base) / "poi.sqlite3")
        except Exception:
            return None

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=8000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._db() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS places (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_fold TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    kind_raw TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    category_fold TEXT NOT NULL DEFAULT '',
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    address TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    website TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    source TEXT NOT NULL DEFAULT 'Overture',
                    release TEXT NOT NULL DEFAULT '',
                    updated TEXT NOT NULL DEFAULT '',
                    fetched_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_places_lat_lon ON places(lat, lon)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_places_name ON places(name_fold)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_places_category ON places(category_fold)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS coverage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    min_lat REAL NOT NULL,
                    max_lat REAL NOT NULL,
                    min_lon REAL NOT NULL,
                    max_lon REAL NOT NULL,
                    release TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    # ---------------------------------------------------------------- sorgu
    def coverage_contains(self, min_lat, max_lat, min_lon, max_lon) -> bool:
        south, north, west, east = _normalize_bbox(min_lat, max_lat, min_lon, max_lon)
        cutoff = time.time() - COVERAGE_TTL_SECONDS
        try:
            with self._db() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM coverage
                    WHERE release = ? AND updated_at >= ?
                      AND min_lat <= ? AND max_lat >= ?
                      AND min_lon <= ? AND max_lon >= ?
                    LIMIT 1
                    """,
                    (OVERTURE_RELEASE, cutoff, south, north, west, east),
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def search_near(
        self,
        lat: float,
        lon: float,
        radius_m: float,
        term: str = "",
        limit: int = 60,
        include_closed: bool = False,
    ) -> list[dict]:
        try:
            lat, lon = float(lat), float(lon)
            radius_m = max(30.0, min(float(radius_m), 20000.0))
        except (TypeError, ValueError):
            return []
        dlat = radius_m / 111_320.0
        dlon = radius_m / (111_320.0 * max(0.2, abs(math.cos(math.radians(lat)))))
        south, north = lat - dlat, lat + dlat
        west, east = lon - dlon, lon + dlon
        clauses = ["lat BETWEEN ? AND ?", "lon BETWEEN ? AND ?"]
        params: list = [south, north, west, east]
        if not include_closed:
            clauses.append("(status = '' OR status = 'open' OR status = 'temporarily_closed')")
        tokens = [token for token in re.split(r"\s+", fold_tr(term)) if len(token) >= 2][:4]
        if tokens:
            for token in tokens:
                clauses.append("(name_fold LIKE ? OR category_fold LIKE ?)")
                pattern = f"%{token}%"
                params.extend((pattern, pattern))
        params.extend((lat, lat, lon, lon))
        sql = (
            "SELECT * FROM places WHERE "
            + " AND ".join(clauses)
            + " ORDER BY ((lat - ?) * (lat - ?) + (lon - ?) * (lon - ?)) ASC LIMIT 4000"
        )
        try:
            with self._db() as connection:
                rows = connection.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
        results = []
        radius_earth = 6_371_000.0
        for row in rows:
            try:
                plat, plon = float(row["lat"]), float(row["lon"])
            except (TypeError, ValueError):
                continue
            p1, p2 = math.radians(lat), math.radians(plat)
            dp = math.radians(plat - lat)
            dl = math.radians(plon - lon)
            a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            distance = 2 * radius_earth * math.asin(math.sqrt(max(0.0, min(1.0, a))))
            if distance > radius_m + 30:
                continue
            results.append({
                "name": str(row["name"] or "").strip(),
                "lat": plat,
                "lon": plon,
                "distance_m": int(round(distance)),
                "kind": str(row["kind"] or "place"),
                "kind_raw": str(row["kind_raw"] or ""),
                "category": str(row["category"] or "Mekân"),
                "address": str(row["address"] or ""),
                "phone": str(row["phone"] or ""),
                "website": str(row["website"] or ""),
                "status": str(row["status"] or ""),
                "confidence": row["confidence"],
                "source": str(row["source"] or "Overture"),
                "release": str(row["release"] or ""),
                "updated": str(row["updated"] or ""),
            })
        results.sort(key=lambda item: item["distance_m"])
        return results[:max(1, min(int(limit), 200))]

    def stats(self) -> dict:
        try:
            with self._db() as connection:
                count = connection.execute("SELECT COUNT(*) FROM places").fetchone()[0]
                coverage = [
                    dict(row) for row in connection.execute(
                        "SELECT * FROM coverage ORDER BY updated_at DESC LIMIT 10"
                    ).fetchall()
                ]
        except sqlite3.Error:
            count, coverage = 0, []
        return {
            "path": str(self.path),
            "release": OVERTURE_RELEASE,
            "count": int(count),
            "coverage": coverage,
            "last_error": self.last_error,
        }

    # --------------------------------------------------------------- içe alma
    def upsert_records(self, records: Iterable[dict]) -> int:
        """Test ve içe alma için kayıtları SQLite'a yazar."""
        now = time.time()
        prepared = []
        for record in records:
            name = str(record.get("name") or "").strip()
            if not name:
                continue
            kind_raw = str(record.get("kind_raw") or "").strip()
            category = str(record.get("category") or "").strip()
            if category:
                kind = ui_kind(kind_raw) or "place"
            else:
                inferred = infer_name_category(name)
                if inferred:
                    kind, category = inferred
                else:
                    kind = ui_kind(kind_raw) or "place"
                    category = category_label(kind_raw)
            prepared.append((
                str(record.get("id") or f"local:{fold_tr(name)}:{record.get('lat')}:{record.get('lon')}"),
                name[:200],
                fold_tr(name)[:200],
                kind,
                kind_raw[:80],
                category[:80],
                fold_tr(category)[:160],
                float(record["lat"]),
                float(record["lon"]),
                str(record.get("address") or "")[:240],
                str(record.get("phone") or "")[:80],
                str(record.get("website") or "")[:320],
                fold_tr(record.get("status")),
                record.get("confidence"),
                str(record.get("source") or "Overture"),
                str(record.get("release") or OVERTURE_RELEASE),
                str(record.get("updated") or ""),
                now,
            ))
        if not prepared:
            return 0
        sql = """
            INSERT INTO places (
                id, name, name_fold, kind, kind_raw, category, category_fold,
                lat, lon, address, phone, website, status, confidence,
                source, release, updated, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                name_fold = excluded.name_fold,
                kind = excluded.kind,
                kind_raw = excluded.kind_raw,
                category = excluded.category,
                category_fold = excluded.category_fold,
                lat = excluded.lat,
                lon = excluded.lon,
                address = excluded.address,
                phone = excluded.phone,
                website = excluded.website,
                status = excluded.status,
                confidence = excluded.confidence,
                source = excluded.source,
                release = excluded.release,
                updated = excluded.updated,
                fetched_at = excluded.fetched_at
        """
        with self._db() as connection:
            connection.executemany(sql, prepared)
            connection.commit()
        return len(prepared)

    def reclassify_names(self) -> int:
        """Mevcut kayıtları güncel isim kurallarıyla yeniden sınıflandırır."""
        updated = 0
        with self._db() as connection:
            rows = connection.execute("SELECT id, name FROM places").fetchall()
            for row in rows:
                inferred = infer_name_category(row["name"])
                if not inferred:
                    continue
                kind, category = inferred
                label = category[:80]
                cursor = connection.execute(
                    """
                    UPDATE places
                       SET kind = ?, category = ?, category_fold = ?
                     WHERE id = ? AND (kind <> ? OR category <> ?)
                    """,
                    (kind, label, fold_tr(category)[:160], row["id"], kind, label),
                )
                updated += max(cursor.rowcount, 0)
            connection.commit()
        return updated

    def _record_coverage(self, bbox, count: int) -> None:
        south, north, west, east = bbox
        with self._db() as connection:
            connection.execute(
                """
                INSERT INTO coverage
                    (min_lat, max_lat, min_lon, max_lon, release, updated_at, count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (south, north, west, east, OVERTURE_RELEASE, time.time(), int(count)),
            )
            connection.commit()

    def import_bbox(self, min_lat, max_lat, min_lon, max_lon) -> int:
        if self._unavailable:
            return 0
        bbox = _normalize_bbox(min_lat, max_lat, min_lon, max_lon)
        south, north, west, east = bbox
        if north - south > MAX_IMPORT_SPAN_LAT or east - west > MAX_IMPORT_SPAN_LON:
            raise ValueError("İçe alma alanı çok geniş; daha küçük bir bölge seçilmeli.")
        try:
            import duckdb
        except ImportError as error:
            self._unavailable = True
            self.last_error = f"duckdb kurulu değil: {error}"
            raise
        connection = duckdb.connect()
        try:
            for extension in ("spatial", "httpfs"):
                try:
                    connection.execute(f"LOAD {extension}")
                except Exception:
                    connection.execute(f"INSTALL {extension}")
                    connection.execute(f"LOAD {extension}")
            connection.execute("SET s3_region='us-west-2'")
            connection.execute("SET threads=2")
            connection.execute("SET memory_limit='1GB'")
            query = f"""
                SELECT
                    id,
                    names.primary AS name,
                    COALESCE(taxonomy.primary, basic_category, '') AS kind_raw,
                    ST_Y(geometry) AS lat,
                    ST_X(geometry) AS lon,
                    addresses,
                    phones,
                    websites,
                    operating_status,
                    confidence,
                    sources
                FROM read_parquet('{OVERTURE_PLACES_URL}')
                WHERE names.primary IS NOT NULL AND names.primary <> ''
                  AND bbox IS NOT NULL AND geometry IS NOT NULL
                  AND bbox.xmin <= ? AND bbox.xmax >= ?
                  AND bbox.ymin <= ? AND bbox.ymax >= ?
            """
            cursor = connection.execute(query, [east, west, north, south])
            total = 0
            while True:
                rows = cursor.fetchmany(4000)
                if not rows:
                    break
                records = []
                for row in rows:
                    (
                        place_id, name, kind_raw, lat, lon, addresses,
                        phones, websites, status, confidence, sources,
                    ) = row
                    try:
                        lat, lon = float(lat), float(lon)
                    except (TypeError, ValueError):
                        continue
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        continue
                    name = str(name or "").strip()
                    if not name:
                        continue
                    kind_raw = str(kind_raw or "").strip()
                    records.append({
                        "id": str(place_id or ""),
                        "name": name,
                        "kind_raw": kind_raw,
                        "lat": lat,
                        "lon": lon,
                        "address": _join_address(addresses),
                        "phone": _first_text(phones),
                        "website": _first_text(websites),
                        "status": str(status or "").strip(),
                        "confidence": confidence,
                        "source": "Overture",
                        "release": OVERTURE_RELEASE,
                        "updated": _latest_update(sources),
                    })
                total += self.upsert_records(records)
            self._record_coverage(bbox, total)
            self.last_error = ""
            return total
        except Exception as error:
            self.last_error = str(error)[:300]
            raise
        finally:
            connection.close()

    def ensure_import_async(self, min_lat, max_lat, min_lon, max_lon) -> bool:
        """Bölge eksikse arka planda indirir; arayüzü asla bekletmez."""
        if self._unavailable:
            return False
        bbox = _normalize_bbox(min_lat, max_lat, min_lon, max_lon)
        south, north, west, east = bbox
        if north - south > MAX_IMPORT_SPAN_LAT or east - west > MAX_IMPORT_SPAN_LON:
            return False
        if self.coverage_contains(south, north, west, east):
            return False
        key = os.path.normcase(str(self.path.resolve()))
        with _IMPORT_LOCK:
            existing = _IMPORT_THREADS.get(key)
            if existing and existing.is_alive():
                return False
            thread = threading.Thread(
                target=self._warm,
                args=(bbox,),
                name="poi-overture-import",
                daemon=True,
            )
            _IMPORT_THREADS[key] = thread
        thread.start()
        return True

    def _warm(self, bbox) -> None:
        try:
            self.import_bbox(*bbox)
        except Exception:
            pass
