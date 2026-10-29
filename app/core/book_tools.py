"""Kitap künyesi, ISBN, kapak ve kişisel özet altyapısı.

Taranan sayfaların OCR metni yalnızca bellekte tutulur; SQLite'a yalnızca
kitap künyesi, kapak yolu ve kullanıcı için üretilen özet/sayfa referansları
yazılır.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import sqlite3
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from app.core.safety import ActionForbidden

OPEN_LIBRARY_BOOKS = "https://openlibrary.org/api/books"
OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
OPEN_LIBRARY_WORK = "https://openlibrary.org{key}.json"
OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
OPEN_LIBRARY_ISBN_COVER = "https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
GOOGLE_BOOKS = "https://www.googleapis.com/books/v1/volumes"
GOOGLE_BOOKS_COVER = "https://books.google.com/books/content?id={volume_id}&printsec=frontcover&img=1&zoom=2"

USER_AGENT = "JarvisBookReader/1.0 (local desktop assistant)"

MAX_DESCRIPTION_CHARS = 1200
MAX_TOC_ITEMS = 20
MAX_TOC_ITEM_CHARS = 180
MAX_PAGES = 20
MAX_PAGE_CHARS = 5000
MAX_TOTAL_PAGE_CHARS = 30000
MAX_COVER_BYTES = 4 * 1024 * 1024
MAX_COVER_CANDIDATES = 48
MAX_COVER_DOWNLOADS = 26
MAX_COVER_EDITION_WORKS = 8
MAX_COVER_QUERIES = 9
COVER_WINDOW_BUDGET = 520

COVER_VISION_SYSTEM = (
    "Sen Jarvis'in kapak tanıma katmanısın. Görüntüde gerçekten görünen kitap kapağından "
    "emin olduğun bilgileri çıkar. Künye uydurma; emin olmadığın alanı boş bırak. "
    "Yanıtını yalnızca istenen JSON nesnesi olarak ver; JSON dışında açıklama yazma."
)
COVER_VISION_PROMPT = (
    "Görüntüdeki kitap kapağını incele ve yalnızca şu JSON şemasında yanıt ver:\n"
    '{"title":"","subtitle":"","authors":[],"publisher":"","translators":[],"isbn":"",'
    '"edition":"","language":"","confidence":0.0,"visible_text":"","bbox":[0,0,1,1]}\n'
    "bbox, kapak görselinin kare içindeki normalize [x0,y0,x1,y1] sınır kutusudur. "
    "Görüntüde kapak tek başına doluysa [0,0,1,1] kullan. Emin olmadığın alanları boş bırak."
)

SUMMARY_SYSTEM_PROMPT = (
    "Sen Jarvis'in kitap analiz modülüsün. Kullanıcının erişim hakkı olan bir kitabı anlamasına "
    "yardım ediyorsun. Yalnızca verilen künye, içindekiler ve kullanıcının taradığı sayfa "
    "notlarından yararlan. Kitabın tam metnini yeniden üretme; uzun birebir alıntılar verme, "
    "kendi cümlelerinle özetle. Verilmeyen bilgiyi uydurma; bilinmeyen alanlara 'veride yok' yaz. "
    "Sayfa referanslarını yalnızca taranan sayfalardan biliyorsan [s. N] biçiminde ver."
)


class BookError(Exception):
    """Kitap künyesi veya tarama işlemi başarısız olduğunda kullanıcıya gösterilir."""


# -------------------------------------------------------------------------- ISBN
def _isbn_clean(value: Any) -> str:
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def isbn10_valid(value: Any) -> bool:
    cleaned = _isbn_clean(value)
    if len(cleaned) != 10 or not cleaned[:9].isdigit() or not (cleaned[-1].isdigit() or cleaned[-1] == "X"):
        return False
    total = sum((10 - index) * int(char) for index, char in enumerate(cleaned[:9]))
    check = 10 if cleaned[-1] == "X" else int(cleaned[-1])
    return (total + check) % 11 == 0


def isbn13_valid(value: Any) -> bool:
    cleaned = _isbn_clean(value)
    if len(cleaned) != 13 or not cleaned.isdigit() or not cleaned.startswith(("978", "979")):
        return False
    total = sum((1 if index % 2 == 0 else 3) * int(char) for index, char in enumerate(cleaned[:12]))
    return (10 - total % 10) % 10 == int(cleaned[-1])


def isbn10_to_13(value: Any) -> str:
    cleaned = _isbn_clean(value)
    if not isbn10_valid(cleaned):
        return ""
    body = "978" + cleaned[:9]
    total = sum((1 if index % 2 == 0 else 3) * int(char) for index, char in enumerate(body))
    return body + str((10 - total % 10) % 10)


def normalize_isbn(value: Any) -> str:
    cleaned = _isbn_clean(value)
    if isbn13_valid(cleaned):
        return cleaned
    if isbn10_valid(cleaned):
        return isbn10_to_13(cleaned)
    return ""


def valid_isbn(value: Any) -> bool:
    return bool(normalize_isbn(value))


def _fix_ocr_isbn(candidate: str) -> str:
    fixed = re.sub(r"[Oo]", "0", candidate)
    fixed = re.sub(r"[Il|]", "1", fixed)
    fixed = re.sub(r"[Ss]", "5", fixed)
    fixed = re.sub(r"[Bb]", "8", fixed)
    return fixed


def extract_isbns(text: str) -> List[str]:
    """OCR metnindeki geçerli ISBN-10/13 değerlerini kanonik ISBN-13 olarak döndürür."""
    if not text:
        return []
    pattern = re.compile(
        r"(?i)(?<![0-9A-Za-z])(?:(?:97[89][\s-]?)?(?:[0-9OoIlSsBb|][\s-]?){9}"
        r"[0-9XxOoIlSsBb|])(?![0-9A-Za-z])"
    )
    found: List[str] = []
    for match in pattern.finditer(text):
        candidate = _fix_ocr_isbn(match.group(0))
        if sum(char.isdigit() for char in candidate) < 9:
            continue
        normalized = normalize_isbn(candidate)
        if normalized and normalized not in found:
            found.append(normalized)
    return found


def extract_isbn(text: str) -> Optional[str]:
    found = extract_isbns(text)
    return found[0] if found else None


# ----------------------------------------------------------------------- helpers
def _clean_text(value: Any, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = " ".join(text.split())
    return text[:limit].strip()


def _text_value(value: Any) -> str:
    if isinstance(value, dict):
        return _clean_text(value.get("value"))
    return _clean_text(value)


def _name_list(value: Any) -> List[str]:
    names: List[str] = []
    if not value:
        return names
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict):
        value = [value]
    for item in value:
        name = item
        if isinstance(item, dict):
            name = item.get("name") or ""
            author = item.get("author")
            if not name and isinstance(author, dict):
                name = author.get("name") or ""
        cleaned = _clean_text(name, 160)
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


def _contributor_lists(value: Any) -> Tuple[List[str], List[str]]:
    authors: List[str] = []
    translators: List[str] = []
    if not value:
        return authors, translators
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        role = ""
        if isinstance(item, dict):
            role = _ascii_fold(item.get("role") or "")
        names = _name_list(item)
        for name in names:
            target = translators if ("translat" in role or "cevir" in role) else authors
            if name not in target:
                target.append(name)
    return authors, translators


def _toc_items(value: Any) -> List[str]:
    items: List[str] = []
    if not value:
        return items
    if isinstance(value, str):
        return [_clean_text(value, MAX_TOC_ITEM_CHARS)] if value.strip() else []
    if isinstance(value, dict):
        value = [value]
    for item in value:
        if isinstance(item, dict):
            title = _clean_text(item.get("title") or item.get("label") or "", MAX_TOC_ITEM_CHARS)
            page = _clean_text(item.get("pagenum") or item.get("page") or "", 20)
            text = f"{title} · s. {page}" if title and page else title or page
        else:
            text = _clean_text(item, MAX_TOC_ITEM_CHARS)
        if text and text not in items:
            items.append(text)
        if len(items) >= MAX_TOC_ITEMS:
            break
    return items


def _synthetic_id(title: str, authors: Sequence[str] = ()) -> str:
    seed = "|".join([_clean_text(title, 240).casefold(), *[str(a).casefold() for a in authors]])
    return "book:" + hashlib.sha1(seed.encode("utf-8", "replace")).hexdigest()[:16]


# ------------------------------------------------------------- kapak tanıma
def _ascii_fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.replace("ı", "i").replace("İ", "i").casefold()


def _normalized_words(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", _ascii_fold(value)).split())


def _word_tokens(value: Any) -> List[str]:
    return [token for token in _normalized_words(value).split() if len(token) >= 2]


def _person_list(value: Any) -> List[str]:
    names: List[str] = []
    if not value:
        return names
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if isinstance(item, dict):
            item = item.get("name") or item.get("author") or ""
        for part in re.split(r"\s*(?:&|;|/|,|\bve\b)\s*", str(item or "")):
            cleaned = re.sub(
                r"^(?:çeviren|ceviren|translator|translated by|yazar|author)\s*[:\-]?\s*",
                "",
                part.strip(),
                flags=re.IGNORECASE,
            )
            cleaned = _clean_text(cleaned, 160)
            if cleaned and cleaned.casefold() not in {name.casefold() for name in names}:
                names.append(cleaned)
    return names


def _json_object(text: Any) -> dict:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    candidate = re.sub(r",\s*([}\]])", r"\1", raw[start:end + 1])
    try:
        data = json.loads(candidate)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _cover_bbox(value: Any) -> List[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    try:
        coords = [float(item) for item in value]
    except (TypeError, ValueError):
        return []
    if not all(0.0 <= item <= 1.0 for item in coords):
        return []
    x0, y0, x1, y1 = coords
    if x1 - x0 < 0.04 or y1 - y0 < 0.04:
        return []
    if (x1 - x0) * (y1 - y0) >= 0.985:
        return []
    return [round(item, 4) for item in coords]


def parse_cover_vision(text: Any) -> dict:
    """AI kapak yanıtındaki JSON'u güvenli, doğrulanmış alanlara çevirir."""
    data = _json_object(text)
    if not data:
        data = {}
        for key in ("title", "publisher", "isbn", "language"):
            match = re.search(rf'"{key}"\s*:\s*"([^"]*)"', str(text or ""), re.IGNORECASE)
            if match:
                data[key] = match.group(1)
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence > 1.0:
        confidence = confidence / 100.0 if confidence <= 100.0 else 0.0
    return {
        "title": _clean_text(data.get("title"), 240),
        "subtitle": _clean_text(data.get("subtitle"), 240),
        "authors": _person_list(data.get("authors")),
        "publisher": _clean_text(data.get("publisher"), 160),
        "translators": _person_list(data.get("translators")),
        "isbn": normalize_isbn(data.get("isbn")) or "",
        "edition": _clean_text(data.get("edition"), 80),
        "language": _clean_text(data.get("language"), 20),
        "confidence": max(0.0, min(1.0, confidence)),
        "visible_text": _clean_text(data.get("visible_text"), 800),
        "bbox": _cover_bbox(data.get("bbox")),
    }


_PUBLISHER_NOISE = {
    "yayinlari", "yayinevi", "yayin", "kitaplari", "kitap", "cocuk", "basim",
    "basimevi", "publishing", "publishers", "publisher", "press", "books", "book",
    "editions", "edition", "group", "inc", "ltd", "the", "and", "ve",
}


def _publisher_core(value: Any) -> str:
    tokens = [token for token in _word_tokens(value) if token not in _PUBLISHER_NOISE]
    return " ".join(tokens)


def _publisher_variants(value: Any) -> List[str]:
    raw = _clean_text(value, 140)
    variants: List[str] = []

    def add(item: str) -> None:
        cleaned = " ".join(str(item or "").split())
        if cleaned and len(cleaned) >= 3 and cleaned.casefold() not in {v.casefold() for v in variants}:
            variants.append(cleaned)

    add(raw)
    add(_publisher_core(raw))
    return variants


def cover_query_candidates(
    ocr_text: str = "",
    identified: Optional[dict] = None,
    ocr_isbn: str = "",
) -> List[str]:
    """Kapaktaki AI/OCR ipuçlarından çoklu arama sorguları üretir."""
    identified = identified or {}
    queries: List[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        text = " ".join(str(query or "").split())
        if 3 <= len(text) <= 120 and text.casefold() not in seen:
            seen.add(text.casefold())
            queries.append(text)

    isbn = normalize_isbn(ocr_isbn) or normalize_isbn(identified.get("isbn"))
    title = _clean_text(identified.get("title"), 140)
    authors = _person_list(identified.get("authors"))
    translators = [name for name in _person_list(identified.get("translators")) if len(name) >= 3]
    publisher_variants = _publisher_variants(identified.get("publisher"))
    publisher = publisher_variants[0] if publisher_variants else ""
    core_publisher = _publisher_core(publisher)

    lines = [
        _clean_text(line, 100)
        for line in str(ocr_text or "").splitlines()
        if _clean_text(line, 100)
    ]
    lines = [line for line in lines if "isbn" not in line.casefold() and 3 <= len(line) <= 90]
    translator_line = ""
    publisher_line = ""
    author_line = ""
    for line in lines:
        folded = line.casefold()
        if not translator_line and (
            "çevir" in folded
            or "cevir" in folded
            or ("&" in line and sum(word[:1].isupper() for word in line.split()) >= 2)
            or any(_ascii_fold(name.split()[-1]) in _ascii_fold(line) for name in translators if name.split())
        ):
            translator_line = line
        if not publisher_line and any(word in folded for word in ("yayın", "yayin", "kitap", "press", "publish")):
            publisher_line = line
        if not author_line and authors:
            surname = _ascii_fold(authors[0].split()[-1])
            if surname and surname in _ascii_fold(line):
                author_line = line

    if isbn:
        add(isbn)
    if title:
        if core_publisher:
            add(f"{title} {core_publisher}")
        if publisher and publisher.casefold() != core_publisher.casefold():
            add(f"{title} {publisher}")
        if translators:
            add(f"{title} {' '.join(translators[:2])}")
        if authors:
            add(f"{title} {authors[0]}")
            if core_publisher:
                add(f"{title} {authors[0]} {core_publisher}")
        add(title)
    if translators:
        add(" ".join(translators[:3]))

    if translator_line and translator_line.casefold() not in {q.casefold() for q in queries}:
        cleaned_translator = re.sub(r"(?i)\b(?:çevirisiyle|cevirisiyle|çeviren|ceviren)\b", " ", translator_line)
        cleaned_translator = " ".join(cleaned_translator.split())
        if title:
            add(f"{title} {cleaned_translator}")
        else:
            add(cleaned_translator)
    if publisher_line:
        if title:
            add(f"{title} {publisher_line}")
        else:
            add(publisher_line)
    if author_line:
        if title:
            add(f"{title} {author_line}")
        elif translator_line:
            add(f"{author_line} {translator_line}")

    if not title:
        scored: List[Tuple[float, str]] = []
        for line in lines:
            folded = line.casefold()
            if line == translator_line or line == publisher_line:
                continue
            words = line.split()
            score = 0.0
            if 2 <= len(words) <= 6:
                score += 2.0
            if sum(word[:1].isupper() for word in words) >= max(1, len(words) - 1):
                score += 1.0
            if 5 <= len(line) <= 48:
                score += 1.0
            if any(particle in folded.split() for particle in ("de", "van", "von", "bin")):
                score -= 1.5
            if sum(char.isdigit() for char in line) >= 2:
                score -= 2.0
            scored.append((score, line))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _, guess in scored[:2]:
            add(guess)
            if publisher_line:
                add(f"{guess} {publisher_line}")

    return queries[:MAX_COVER_QUERIES]


def _load_image(source: Any):
    from PIL import Image, ImageOps

    if isinstance(source, Image.Image):
        image = source.convert("RGB")
    elif isinstance(source, (bytes, bytearray)):
        image = Image.open(io.BytesIO(source)).convert("RGB")
    else:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    if image.width > 1200 or image.height > 1200:
        image.thumbnail((1200, 1200), Image.LANCZOS)
    return image


def _dhash(gray, width: int, height: int) -> int:
    pixels = gray.tobytes()
    bits = 0
    for row in range(height):
        offset = row * width
        for column in range(width - 1):
            bits = (bits << 1) | (
                1 if pixels[offset + column] > pixels[offset + column + 1] else 0
            )
    return bits


def _color_histogram(image, normalize: bool = False) -> Tuple[float, ...]:
    """Yumuşak kenarlı 3x16 renk histogramı; normalize ışık değişimini dengeler."""
    from PIL import Image

    small = image.resize((32, 32), Image.LANCZOS)
    raw = small.tobytes()
    mean = sum(raw) / max(1, len(raw))
    scale = 160.0 / mean if normalize and mean > 1 else 1.0
    bins = 16
    hist = [[0.0] * bins for _ in range(3)]
    for index in range(0, len(raw), 3):
        for channel, value in enumerate(raw[index:index + 3]):
            position = min(255.0, value * scale) * bins / 256.0
            low = min(bins - 1, int(position))
            high = min(bins - 1, low + 1)
            weight = position - low
            hist[channel][low] += 1.0 - weight
            hist[channel][high] += weight
    total = max(1, len(raw) // 3)
    return tuple(value / total for channel in hist for value in channel)


def image_signature(source: Any) -> dict:
    """Kapak için 64/256 bit dHash, ışığa dayanıklı renk histogramı ve oran üretir."""
    from PIL import Image

    image = _load_image(source)
    aspect = image.width / max(1, image.height)
    small = image.resize((32, 32), Image.LANCZOS)
    gray = small.convert("L")
    return {
        "hash": _dhash(gray.resize((9, 8), Image.LANCZOS), 9, 8),
        "hash256": _dhash(gray.resize((17, 16), Image.LANCZOS), 17, 16),
        "hist": _color_histogram(small),
        "hist_norm": _color_histogram(small, normalize=True),
        "aspect": aspect,
    }


def _histogram_intersection(left, right) -> float:
    try:
        return sum(min(a, b) for a, b in zip(left, right)) / 3.0
    except (TypeError, ValueError):
        return 0.0


def signature_similarity(left: dict, right: dict) -> float:
    try:
        hamming64 = bin(int(left["hash"]) ^ int(right["hash"])).count("1") / 64.0
        hamming256 = bin(int(left["hash256"]) ^ int(right["hash256"])).count("1") / 256.0
        histogram = max(
            _histogram_intersection(left["hist"], right["hist"]),
            _histogram_intersection(left["hist_norm"], right["hist_norm"]),
        )
        aspect_left, aspect_right = float(left["aspect"]), float(right["aspect"])
        aspect = min(aspect_left, aspect_right) / max(1e-6, max(aspect_left, aspect_right))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0.0
    score = (
        0.52 * (1.0 - hamming64)
        + 0.25 * (1.0 - hamming256)
        + 0.18 * histogram
        + 0.05 * aspect
    )
    return max(0.0, min(1.0, score))


def cover_capture_signatures(source: Any, bbox: Any = None) -> Tuple[List[dict], bool]:
    """Kamera karesinden tam kare, AI sınır kutusu ve merkez kırpma imzaları üretir."""
    image = _load_image(source)
    signatures = [image_signature(image)]
    used_bbox = False
    box = _cover_bbox(bbox)
    if box:
        # AI kutusu bazen taşırır; tam, genişletilmiş ve içe daraltılmış kırpmalar denenir.
        for margin_x, margin_y in ((0.0, 0.0), (0.02, 0.02), (-0.03, -0.03)):
            left = max(0, int((box[0] - margin_x) * image.width))
            top = max(0, int((box[1] - margin_y) * image.height))
            right = min(image.width, int((box[2] + margin_x) * image.width))
            bottom = min(image.height, int((box[3] + margin_y) * image.height))
            if right - left >= 40 and bottom - top >= 60:
                signatures.append(image_signature(image.crop((left, top, right, bottom))))
                used_bbox = True
    elif image.width > image.height * 1.05:
        for fraction in (0.82, 0.62):
            width = int(image.width * fraction)
            height = int(image.height * fraction)
            left = (image.width - width) // 2
            top = (image.height - height) // 2
            signatures.append(image_signature(image.crop((left, top, left + width, top + height))))
    return signatures, used_bbox


def _cover_window_signatures(source: Any, budget: int = COVER_WINDOW_BUDGET) -> List[dict]:
    """Kamera karesinde kapak çerçeve içinde küçükse kayan pencere imzaları üretir."""
    image = _load_image(source)
    if image.width / max(1, image.height) < 1.05:
        return []
    from PIL import Image

    width = 240
    height = max(1, round(image.height * width / image.width))
    work = image.resize((width, height), Image.LANCZOS)
    signatures: List[dict] = []
    seen: set[Tuple[int, int, int, int]] = set()
    for height_fraction in (0.95, 0.8, 0.65, 0.5):
        window_height = max(24, int(height * height_fraction))
        for aspect in (0.6, 0.68, 0.76):
            window_width = max(16, int(window_height * aspect))
            if window_width > width or window_height > height:
                continue
            step_x = max(8, window_width // 4)
            step_y = max(8, window_height // 4)
            for top in range(0, height - window_height + 1, step_y):
                for left in range(0, width - window_width + 1, step_x):
                    box = (left, top, window_width, window_height)
                    if box in seen:
                        continue
                    seen.add(box)
                    signatures.append(image_signature(work.crop(
                        (left, top, left + window_width, top + window_height)
                    )))
                    if len(signatures) >= budget:
                        return signatures
    return signatures


def _sequence_score(left: Any, right: Any) -> float:
    a, b = _normalized_words(left), _normalized_words(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _token_cover(left: Any, right: Any) -> float:
    a, b = set(_word_tokens(left)), set(_word_tokens(right))
    if not a or not b:
        return 0.0
    shorter = a if len(a) <= len(b) else b
    longer = b if shorter is a else a
    return len(shorter & longer) / max(1, len(shorter))


def _author_score(identified_authors: Sequence[str], book_authors: Sequence[str]) -> float:
    if not identified_authors:
        return 0.5
    if not book_authors:
        return 0.0
    return max(
        max(_sequence_score(left, right), _token_cover(left, right))
        for left in identified_authors
        for right in book_authors
    )


def _publisher_score(identified_publisher: str, book_publisher: str) -> float:
    if not identified_publisher:
        return 0.5
    if not book_publisher:
        return 0.0
    core_left = _publisher_core(identified_publisher)
    core_right = _publisher_core(book_publisher)
    if core_left and core_right and (core_left in core_right or core_right in core_left):
        return 1.0
    return max(
        _sequence_score(identified_publisher, book_publisher),
        _token_cover(identified_publisher, book_publisher),
    )


def metadata_cover_score(identified: dict, ocr_text: str, book: dict) -> float:
    identified = identified or {}
    title = _clean_text(identified.get("title"), 160)
    book_title = _clean_text(book.get("title"), 240)
    if title:
        title_score = max(_sequence_score(title, book_title), _token_cover(title, book_title))
    else:
        title_score = 0.0
        for line in str(ocr_text or "").splitlines():
            score = _sequence_score(line, book_title)
            if score >= 0.78:
                title_score = max(title_score, score)
    author_score = _author_score(
        _person_list(identified.get("authors")),
        [str(item) for item in (book.get("authors") or [])],
    )
    publisher_score = _publisher_score(
        _clean_text(identified.get("publisher"), 160),
        _clean_text(book.get("publisher"), 160),
    )
    score = 0.60 * title_score + 0.25 * author_score + 0.15 * publisher_score
    isbn = normalize_isbn(identified.get("isbn"))
    book_isbn = normalize_isbn(book.get("isbn"))
    if isbn and book_isbn and isbn == book_isbn:
        score += 0.18
    try:
        score += 0.04 * max(0.0, min(1.0, float(identified.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        pass
    return max(0.0, min(1.0, score))


def _source_label(book: dict) -> str:
    source = str(book.get("source") or "Open Library").strip()
    if "önbellek" in source.casefold():
        return source
    return f"{source} (açık künye)"


def _book_directory(settings) -> Optional[Path]:
    data_dir = getattr(settings, "data_dir", None)
    if not data_dir:
        return None
    return Path(data_dir) / "book_data"


# ------------------------------------------------------------------------- store
class BookStore:
    """Künye, kapak yolu ve kişisel özetleri tutar; OCR tam metni saklanmaz."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @classmethod
    def default(cls, settings) -> Optional["BookStore"]:
        directory = _book_directory(settings)
        if directory is None:
            return None
        return cls(directory / "books.sqlite3")

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS books (
                    id TEXT PRIMARY KEY,
                    isbn TEXT,
                    title TEXT NOT NULL,
                    subtitle TEXT,
                    authors TEXT,
                    translators TEXT,
                    publisher TEXT,
                    published TEXT,
                    page_count INTEGER,
                    language TEXT,
                    categories TEXT,
                    description TEXT,
                    toc TEXT,
                    cover_url TEXT,
                    cover_path TEXT,
                    source TEXT,
                    source_url TEXT,
                    fetched_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn);
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    page_refs TEXT,
                    model TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_profiles_book ON profiles(book_id, created_at);
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(books)").fetchall()
            }
            if "translators" not in columns:
                connection.execute("ALTER TABLE books ADD COLUMN translators TEXT")

    @staticmethod
    def _parse_json_list(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return [str(value)]
        return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]

    def save_book(self, book: dict) -> dict:
        isbn = normalize_isbn(book.get("isbn")) or str(book.get("isbn") or "").strip()
        authors = [str(item) for item in (book.get("authors") or []) if str(item).strip()]
        translators = [str(item) for item in (book.get("translators") or []) if str(item).strip()]
        title = _clean_text(book.get("title"), 240) or "Adsız kitap"
        book_id = str(book.get("id") or isbn or _synthetic_id(title, authors)).strip()
        categories = [str(item) for item in (book.get("categories") or []) if str(item).strip()]
        toc = [str(item) for item in (book.get("toc") or []) if str(item).strip()][:MAX_TOC_ITEMS]
        page_count = book.get("page_count")
        try:
            page_count = int(page_count or 0)
        except (TypeError, ValueError):
            page_count = 0
        now = time.time()
        with self._db() as connection:
            connection.execute(
                """
                INSERT INTO books (
                    id, isbn, title, subtitle, authors, translators, publisher, published,
                    page_count, language, categories, description, toc,
                    cover_url, cover_path, source, source_url, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    isbn = excluded.isbn,
                    title = excluded.title,
                    subtitle = excluded.subtitle,
                    authors = excluded.authors,
                    translators = excluded.translators,
                    publisher = excluded.publisher,
                    published = excluded.published,
                    page_count = excluded.page_count,
                    language = excluded.language,
                    categories = excluded.categories,
                    description = excluded.description,
                    toc = excluded.toc,
                    cover_url = excluded.cover_url,
                    cover_path = COALESCE(NULLIF(excluded.cover_path, ''), books.cover_path),
                    source = excluded.source,
                    source_url = excluded.source_url,
                    fetched_at = excluded.fetched_at
                """,
                (
                    book_id,
                    isbn[:32],
                    title,
                    _clean_text(book.get("subtitle"), 240),
                    json.dumps(authors, ensure_ascii=False),
                    json.dumps(translators, ensure_ascii=False),
                    _clean_text(book.get("publisher"), 240),
                    _clean_text(book.get("published"), 80),
                    page_count,
                    _clean_text(book.get("language"), 40),
                    json.dumps(categories, ensure_ascii=False),
                    _clean_text(book.get("description"), MAX_DESCRIPTION_CHARS),
                    json.dumps(toc, ensure_ascii=False),
                    str(book.get("cover_url") or "")[:600],
                    str(book.get("cover_path") or "")[:600],
                    _clean_text(book.get("source"), 80) or "Open Library",
                    str(book.get("source_url") or "")[:600],
                    float(book.get("fetched_at") or now),
                ),
            )
        saved = dict(book)
        saved.update({
            "id": book_id,
            "isbn": isbn,
            "title": title,
            "authors": authors,
            "translators": translators,
            "categories": categories,
            "toc": toc,
            "page_count": page_count,
            "source": _clean_text(book.get("source"), 80) or "Open Library",
            "fetched_at": float(book.get("fetched_at") or now),
        })
        return saved

    @staticmethod
    def _row_to_book(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "isbn": row["isbn"] or "",
            "title": row["title"] or "Adsız kitap",
            "subtitle": row["subtitle"] or "",
            "authors": BookStore._parse_json_list(row["authors"]),
            "translators": BookStore._parse_json_list(row["translators"]),
            "publisher": row["publisher"] or "",
            "published": row["published"] or "",
            "page_count": int(row["page_count"] or 0),
            "language": row["language"] or "",
            "categories": BookStore._parse_json_list(row["categories"]),
            "description": row["description"] or "",
            "toc": BookStore._parse_json_list(row["toc"]),
            "cover_url": row["cover_url"] or "",
            "cover_path": row["cover_path"] or "",
            "source": row["source"] or "",
            "source_url": row["source_url"] or "",
            "fetched_at": float(row["fetched_at"] or 0),
        }

    def get_book(self, book_id: str) -> Optional[dict]:
        if not book_id:
            return None
        with self._db() as connection:
            row = connection.execute("SELECT * FROM books WHERE id = ?", (str(book_id),)).fetchone()
        return self._row_to_book(row) if row else None

    def find_by_isbn(self, isbn: str) -> Optional[dict]:
        normalized = normalize_isbn(isbn) or _isbn_clean(isbn)
        if not normalized:
            return None
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM books WHERE isbn = ? OR id = ? ORDER BY fetched_at DESC LIMIT 1",
                (normalized, normalized),
            ).fetchone()
        return self._row_to_book(row) if row else None

    def find_books(self, query: str, limit: int = 10) -> List[dict]:
        text = f"%{str(query or '').strip()}%"
        if text == "%%":
            return []
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM books WHERE title LIKE ? OR authors LIKE ? OR translators LIKE ? "
                "OR isbn LIKE ? ORDER BY fetched_at DESC LIMIT ?",
                (text, text, text, text, max(1, min(int(limit), 30))),
            ).fetchall()
        return [self._row_to_book(row) for row in rows]

    def save_profile(
        self,
        book_id: str,
        kind: str,
        summary: str,
        page_refs: Sequence[int] = (),
        model: str = "",
    ) -> int:
        text = str(summary or "").strip()
        if not text:
            raise BookError("Boş özet kaydedilemez.")
        refs = [int(ref) for ref in page_refs if str(ref).strip()]
        with self._db() as connection:
            cursor = connection.execute(
                "INSERT INTO profiles (book_id, kind, summary, page_refs, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(book_id),
                    str(kind or "pages")[:40],
                    text[:60000],
                    json.dumps(refs),
                    str(model or "")[:120],
                    time.time(),
                ),
            )
            return int(cursor.lastrowid)

    def _row_to_profile(self, row: sqlite3.Row) -> dict:
        return {
            "id": int(row["id"]),
            "book_id": row["book_id"],
            "book_title": row["book_title"] if "book_title" in row.keys() else "",
            "isbn": row["isbn"] if "isbn" in row.keys() else "",
            "kind": row["kind"],
            "summary": row["summary"],
            "page_refs": self._parse_json_list(row["page_refs"]),
            "model": row["model"] or "",
            "created_at": float(row["created_at"] or 0),
        }

    def profiles(self, book_id: Optional[str] = None, limit: int = 30) -> List[dict]:
        limit = max(1, min(int(limit), 100))
        with self._db() as connection:
            if book_id:
                rows = connection.execute(
                    "SELECT p.*, b.title AS book_title, b.isbn AS isbn FROM profiles p "
                    "LEFT JOIN books b ON b.id = p.book_id WHERE p.book_id = ? "
                    "ORDER BY p.created_at DESC LIMIT ?",
                    (str(book_id), limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT p.*, b.title AS book_title, b.isbn AS isbn FROM profiles p "
                    "LEFT JOIN books b ON b.id = p.book_id "
                    "ORDER BY p.created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def latest_profile(self, book_id: str, kind: Optional[str] = None) -> Optional[dict]:
        with self._db() as connection:
            if kind:
                row = connection.execute(
                    "SELECT p.*, b.title AS book_title, b.isbn AS isbn FROM profiles p "
                    "LEFT JOIN books b ON b.id = p.book_id "
                    "WHERE p.book_id = ? AND p.kind = ? ORDER BY p.created_at DESC LIMIT 1",
                    (str(book_id), str(kind)),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT p.*, b.title AS book_title, b.isbn AS isbn FROM profiles p "
                    "LEFT JOIN books b ON b.id = p.book_id "
                    "WHERE p.book_id = ? ORDER BY p.created_at DESC LIMIT 1",
                    (str(book_id),),
                ).fetchone()
        return self._row_to_profile(row) if row else None

    def stats(self) -> dict:
        with self._db() as connection:
            books = int(connection.execute("SELECT COUNT(*) FROM books").fetchone()[0])
            profiles = int(connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0])
        return {"books": books, "profiles": profiles, "path": str(self.path)}


# ----------------------------------------------------------------------- service
class BookService:
    def __init__(
        self,
        settings=None,
        store: Optional[BookStore] = None,
        session=None,
        cover_dir: Optional[Path | str] = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self.store = store if store is not None else BookStore.default(settings)
        self.session = session or requests.Session()
        self._sleep = sleep
        directory = _book_directory(settings)
        if cover_dir is None and directory is not None:
            cover_dir = directory / "covers"
        self.cover_dir = Path(cover_dir) if cover_dir is not None else None
        headers = getattr(self.session, "headers", None)
        if headers is not None:
            try:
                headers.setdefault("User-Agent", USER_AGENT)
            except Exception:
                pass

    @classmethod
    def default(cls, settings) -> Optional["BookService"]:
        directory = _book_directory(settings)
        if directory is None:
            return None
        return cls(
            settings=settings,
            store=BookStore(directory / "books.sqlite3"),
            cover_dir=directory / "covers",
        )

    def _get_json(self, url: str, params: dict, attempts: int = 2) -> dict:
        last_error: Optional[Exception] = None
        for attempt in range(max(1, attempts)):
            try:
                response = self.session.get(url, params=params, timeout=12)
                if response.status_code == 429 or response.status_code >= 500:
                    raise BookError(f"Servis geçici olarak yanıt vermedi ({response.status_code}).")
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except BookError as error:
                last_error = error
            except requests.RequestException as error:
                last_error = error
            if attempt + 1 < attempts:
                self._sleep(0.6)
        raise BookError(str(last_error or "Kitap servisine ulaşılamadı."))

    @staticmethod
    def _language_tag(value: Any) -> str:
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                key = str(first.get("key") or "")
                return key.rsplit("/", 1)[-1] if key else ""
            return str(first)
        if isinstance(value, dict):
            return str(value.get("key") or "").rsplit("/", 1)[-1]
        return str(value or "")

    def _openlibrary_work(self, key: str) -> dict:
        if not key.startswith("/"):
            return {}
        try:
            return self._get_json(OPEN_LIBRARY_WORK.format(key=key), {}, attempts=1)
        except BookError:
            return {}

    def _openlibrary_isbn(self, isbn: str) -> dict:
        payload = self._get_json(
            OPEN_LIBRARY_BOOKS,
            {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "details"},
        )
        entry = next(iter(payload.values()), None)
        if not isinstance(entry, dict):
            return {}
        details = entry.get("details") or {}
        title = details.get("title") or entry.get("title") or ""
        if not title:
            return {}
        contributor_authors, translators = _contributor_lists(details.get("contributors"))
        authors = _name_list(entry.get("authors")) or contributor_authors
        publishers = entry.get("publishers") or details.get("publishers") or []
        publisher = ""
        if publishers:
            first = publishers[0]
            publisher = first.get("name") if isinstance(first, dict) else first
        description = _text_value(details.get("description"))
        subjects = []
        works = details.get("works") or entry.get("works") or []
        work_key = ""
        if works and isinstance(works[0], dict):
            work_key = str(works[0].get("key") or "")
        if work_key:
            work = self._openlibrary_work(work_key)
            if work:
                description = description or _text_value(work.get("description"))
                subjects = _name_list(work.get("subjects"))[:12]
        cover_url = ""
        cover = entry.get("cover") or {}
        if isinstance(cover, dict):
            cover_url = str(cover.get("large") or cover.get("medium") or cover.get("small") or "")
        covers = [
            cover for cover in (details.get("covers") or [])
            if isinstance(cover, int) and cover > 0
        ]
        if not cover_url and covers:
            cover_url = OPEN_LIBRARY_COVER.format(cover_id=covers[0])
        if not cover_url:
            cover_url = OPEN_LIBRARY_ISBN_COVER.format(isbn=isbn)
        identifiers = entry.get("identifiers") or details.get("identifiers") or {}
        isbns = identifiers.get("isbn_13") or identifiers.get("isbn_10") or []
        if not cover_url and isinstance(identifiers, dict):
            isbns = isbns or []
        page_count = entry.get("number_of_pages") or details.get("number_of_pages") or 0
        return {
            "id": isbn,
            "isbn": isbn,
            "title": _clean_text(title, 240),
            "subtitle": _clean_text(details.get("subtitle"), 240),
            "authors": authors,
            "translators": translators,
            "publisher": _clean_text(publisher, 240),
            "published": _clean_text(entry.get("publish_date") or details.get("publish_date"), 80),
            "page_count": page_count,
            "language": self._language_tag(details.get("languages")),
            "categories": subjects,
            "description": description,
            "toc": _toc_items(details.get("table_of_contents")),
            "cover_url": cover_url,
            "source": "Open Library",
            "source_url": str(entry.get("url") or f"https://openlibrary.org/isbn/{isbn}"),
            "fetched_at": time.time(),
        }

    def _google_isbn(self, isbn: str) -> dict:
        payload = self._get_json(GOOGLE_BOOKS, {"q": f"isbn:{isbn}", "maxResults": 5})
        items = payload.get("items") or []
        if not items:
            return {}
        item = items[0]
        info = item.get("volumeInfo") or {}
        title = info.get("title")
        if not title:
            return {}
        image = (info.get("imageLinks") or {})
        cover_url = str(image.get("thumbnail") or image.get("smallThumbnail") or "")
        if cover_url.startswith("http://"):
            cover_url = "https://" + cover_url[len("http://"):]
        return {
            "id": isbn,
            "isbn": isbn,
            "title": _clean_text(title, 240),
            "subtitle": _clean_text(info.get("subtitle"), 240),
            "authors": _name_list(info.get("authors")),
            "translators": [],
            "publisher": _clean_text(info.get("publisher"), 240),
            "published": _clean_text(info.get("publishedDate"), 80),
            "page_count": int(info.get("pageCount") or 0),
            "language": _clean_text(info.get("language"), 40),
            "categories": _name_list(info.get("categories")),
            "description": _text_value(info.get("description")),
            "toc": [],
            "cover_url": cover_url,
            "source": "Google Books",
            "source_url": str(info.get("infoLink") or item.get("selfLink") or ""),
            "fetched_at": time.time(),
        }

    def _download_cover(self, book: dict) -> dict:
        url = str(book.get("cover_url") or "").strip()
        if not url or self.cover_dir is None or not url.startswith(("http://", "https://")):
            return book
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code != 200:
                return book
            content = response.content or b""
            if not content or len(content) > MAX_COVER_BYTES:
                return book
            from PIL import Image

            with Image.open(io.BytesIO(content)) as image:
                image = image.convert("RGB")
                image.thumbnail((480, 720))
                self.cover_dir.mkdir(parents=True, exist_ok=True)
                safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(book.get("id") or "book"))[:80]
                path = self.cover_dir / f"{safe_id}.jpg"
                image.save(path, "JPEG", quality=88)
            book = dict(book)
            book["cover_path"] = str(path)
        except Exception:
            return book
        return book

    def _prepare(self, book: dict) -> dict:
        book = self.save(book)
        if not book:
            return {}
        book = self._download_cover(book)
        return self.save(book) or book

    def save(self, book: dict) -> Optional[dict]:
        if not book or not book.get("title"):
            return None
        if self.store is None:
            return dict(book)
        try:
            return self.store.save_book(book)
        except Exception:
            return dict(book)

    def lookup_isbn(self, isbn: str) -> dict:
        normalized = normalize_isbn(isbn)
        if not normalized:
            raise BookError("Geçerli bir ISBN-10 veya ISBN-13 gir.")
        errors: List[str] = []
        for provider in (self._openlibrary_isbn, self._google_isbn):
            try:
                book = provider(normalized)
            except BookError as error:
                errors.append(str(error))
                continue
            except Exception as error:
                errors.append(str(error))
                continue
            if book:
                return self._prepare(book)
        cached = self.store.find_by_isbn(normalized) if self.store else None
        if cached:
            cached = dict(cached)
            cached["source"] = f"{cached.get('source') or 'Kitap'} · önbellek"
            return cached
        detail = f" ({errors[-1]})" if errors else ""
        raise BookError(f"{normalized} için kitap künyesi bulunamadı{detail}. ISBN'i kontrol et.")

    def _openlibrary_search(self, query: str, limit: int) -> List[dict]:
        payload = self._get_json(
            OPEN_LIBRARY_SEARCH,
            {"q": query, "limit": max(1, min(limit * 2, 20)), "fields": (
                "key,title,subtitle,author_name,publisher,first_publish_year,"
                "isbn,cover_i,number_of_pages_median,subject,language,edition_key"
            )},
            attempts=1,
        )
        docs = payload.get("docs") or []
        results: List[dict] = []
        for doc in docs:
            title = _clean_text(doc.get("title"), 240)
            if not title:
                continue
            isbn = ""
            for candidate in doc.get("isbn") or []:
                isbn = normalize_isbn(candidate)
                if isbn:
                    break
            key = str(doc.get("key") or "")
            book_id = isbn or (f"ol:{key.rsplit('/', 1)[-1]}" if key else _synthetic_id(title, doc.get("author_name") or []))
            cover_id = doc.get("cover_i")
            results.append({
                "id": book_id,
                "isbn": isbn,
                "title": title,
                "subtitle": _clean_text(doc.get("subtitle"), 240),
                "authors": _name_list(doc.get("author_name")),
                "translators": [],
                "publisher": _clean_text((doc.get("publisher") or [""])[0], 240),
                "published": str(doc.get("first_publish_year") or ""),
                "page_count": int(doc.get("number_of_pages_median") or 0),
                "language": self._language_tag(doc.get("language")),
                "categories": _name_list(doc.get("subject"))[:10],
                "description": "",
                "toc": [],
                "cover_url": OPEN_LIBRARY_COVER.format(cover_id=cover_id) if cover_id else "",
                "source": "Open Library",
                "source_url": f"https://openlibrary.org{key}" if key else "",
                "fetched_at": time.time(),
            })
        return results

    def _google_search(self, query: str, limit: int) -> List[dict]:
        payload = self._get_json(
            GOOGLE_BOOKS,
            {"q": query, "maxResults": max(1, min(limit, 20)), "printType": "books"},
            attempts=1,
        )
        results: List[dict] = []
        for item in payload.get("items") or []:
            info = item.get("volumeInfo") or {}
            title = _clean_text(info.get("title"), 240)
            if not title:
                continue
            isbn = ""
            for identifier in info.get("industryIdentifiers") or []:
                isbn = normalize_isbn(identifier.get("identifier"))
                if isbn:
                    break
            image = info.get("imageLinks") or {}
            cover_url = str(image.get("thumbnail") or image.get("smallThumbnail") or "")
            if cover_url.startswith("http://"):
                cover_url = "https://" + cover_url[len("http://"):]
            results.append({
                "id": isbn or _synthetic_id(title, info.get("authors") or []),
                "isbn": isbn,
                "title": title,
                "subtitle": _clean_text(info.get("subtitle"), 240),
                "authors": _name_list(info.get("authors")),
                "translators": [],
                "publisher": _clean_text(info.get("publisher"), 240),
                "published": _clean_text(info.get("publishedDate"), 80),
                "page_count": int(info.get("pageCount") or 0),
                "language": _clean_text(info.get("language"), 40),
                "categories": _name_list(info.get("categories")),
                "description": _text_value(info.get("description")),
                "toc": [],
                "cover_url": cover_url,
                "source": "Google Books",
                "source_url": str(info.get("infoLink") or ""),
                "fetched_at": time.time(),
            })
        return results

    def search(self, query: str, limit: int = 6) -> List[dict]:
        text = str(query or "").strip()
        if not text:
            return []
        normalized = normalize_isbn(text)
        if normalized:
            try:
                return [self.lookup_isbn(normalized)]
            except BookError:
                return []
        found: List[dict] = []
        seen = set()
        for provider in (self._openlibrary_search, self._google_search):
            try:
                results = provider(text, limit)
            except (BookError, Exception):
                results = []
            for book in results:
                key = (book.get("id"), _clean_text(book.get("title"), 100).casefold())
                if key in seen:
                    continue
                seen.add(key)
                found.append(book)
                if len(found) >= limit:
                    return found
        return found[:limit]

    def _openlibrary_editions(
        self, work_key: str, base: Optional[dict] = None, limit: int = 40
    ) -> List[dict]:
        key = str(work_key or "").strip()
        if key.startswith("http"):
            match = re.search(r"(/works/OL\d+W)", key)
            key = match.group(1) if match else ""
        if not key.startswith("/works/"):
            return []
        try:
            payload = self._get_json(
                f"https://openlibrary.org{key}/editions.json",
                {"limit": max(10, min(int(limit), 100))},
                attempts=1,
            )
        except BookError:
            return []
        base = base or {}
        results: List[dict] = []
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            title = _clean_text(entry.get("title") or base.get("title"), 240)
            if not title:
                continue
            isbns: List[str] = []
            for candidate in (entry.get("isbn_13") or []) + (entry.get("isbn_10") or []):
                normalized = normalize_isbn(candidate)
                if normalized and normalized not in isbns:
                    isbns.append(normalized)
            edition_key = str(entry.get("key") or "")
            authors = _name_list(entry.get("authors")) or _name_list(base.get("authors"))
            contributor_authors, translators = _contributor_lists(entry.get("contributors"))
            if contributor_authors and not authors:
                authors = contributor_authors
            publishers = entry.get("publishers") or []
            publisher = publishers[0] if publishers else base.get("publisher", "")
            covers = [cover for cover in (entry.get("covers") or []) if isinstance(cover, int) and cover > 0]
            cover_url = OPEN_LIBRARY_COVER.format(cover_id=covers[0]) if covers else str(base.get("cover_url") or "")
            if not cover_url and isbns:
                cover_url = OPEN_LIBRARY_ISBN_COVER.format(isbn=isbns[0])
            results.append({
                "id": isbns[0] if isbns else (
                    f"ol:{edition_key.rsplit('/', 1)[-1]}" if edition_key
                    else _synthetic_id(title, authors)
                ),
                "isbn": isbns[0] if isbns else "",
                "title": title,
                "subtitle": _clean_text(entry.get("subtitle"), 240),
                "authors": authors,
                "translators": translators,
                "publisher": _clean_text(publisher, 240),
                "published": _clean_text(entry.get("publish_date"), 80),
                "page_count": int(entry.get("number_of_pages") or 0),
                "language": self._language_tag(entry.get("languages")),
                "categories": _name_list(entry.get("subjects"))[:10],
                "description": "",
                "toc": _toc_items(entry.get("table_of_contents")),
                "cover_url": cover_url,
                "source": "Open Library",
                "source_url": f"https://openlibrary.org{edition_key}" if edition_key else "",
                "fetched_at": time.time(),
                "_work_key": key,
            })
        return results

    def cover_search(
        self,
        queries: Sequence[str],
        limit: int = 8,
        identified: Optional[dict] = None,
        ocr_text: str = "",
    ) -> List[dict]:
        """Birden çok sorguyu Open Library/Google Books'ta tarar, edisyonları genişletir."""
        identified = identified or {}
        collected: List[dict] = []
        index_by_key: Dict[str, int] = {}
        work_paths: List[str] = []
        work_rank: Dict[str, int] = {}

        def add(book: dict, query_rank: int) -> None:
            if not book or not _clean_text(book.get("title"), 240):
                return
            isbn = normalize_isbn(book.get("isbn"))
            key = isbn or str(book.get("id") or "") or _normalized_words(book.get("title"))
            if key in index_by_key:
                existing = collected[index_by_key[key]]
                for field in (
                    "translators", "authors", "publisher", "published", "page_count",
                    "toc", "subtitle", "language", "cover_url", "cover_path", "source_url",
                ):
                    if not existing.get(field) and book.get(field):
                        existing[field] = book[field]
                existing["_query_rank"] = min(
                    int(existing.get("_query_rank") or query_rank), query_rank
                )
                return
            item = dict(book)
            item["_query_rank"] = query_rank
            index_by_key[key] = len(collected)
            collected.append(item)
            match = re.search(r"/works/(OL\d+W)", str(book.get("source_url") or ""))
            if match:
                path = f"/works/{match.group(1)}"
                if path not in work_rank:
                    work_paths.append(path)
                    work_rank[path] = query_rank

        for rank, query in enumerate(list(queries)[:MAX_COVER_QUERIES]):
            normalized = normalize_isbn(query)
            if normalized:
                try:
                    add(self.lookup_isbn(normalized), rank)
                except BookError:
                    pass
                continue
            for provider in (self._openlibrary_search, self._google_search):
                try:
                    results = provider(query, max(6, int(limit)))
                except Exception:
                    results = []
                for book in results:
                    add(book, rank)

        for path in work_paths[:MAX_COVER_EDITION_WORKS]:
            base = next(
                (book for book in collected
                 if path in str(book.get("source_url") or "")),
                {},
            )
            for edition in self._openlibrary_editions(path, base, limit=40):
                add(edition, int(base.get("_query_rank") or work_rank.get(path) or 0))

        for item in collected:
            item["_metadata_score"] = metadata_cover_score(identified, ocr_text, item)
            item["_specificity"] = 1.0 / (1.0 + max(0, int(item.get("_query_rank") or 0)))
        collected.sort(
            key=lambda book: (
                float(book.get("_metadata_score") or 0.0)
                + 0.08 * float(book.get("_specificity") or 0.0)
                + (0.02 if book.get("cover_url") else 0.0)
                + (0.01 if book.get("isbn") else 0.0)
            ),
            reverse=True,
        )
        return collected[:MAX_COVER_CANDIDATES]

    def _cover_signature_from_url(self, url: str, cache: Dict[str, Optional[dict]]) -> Optional[dict]:
        if not url:
            return None
        if url in cache:
            return cache[url]
        signature: Optional[dict] = None
        if url.startswith(("http://", "https://")):
            try:
                response = self.session.get(url, timeout=10)
                content = response.content or b""
                if response.status_code == 200 and 0 < len(content) <= MAX_COVER_BYTES:
                    from PIL import Image

                    with Image.open(io.BytesIO(content)) as image:
                        if image.width >= 80 and image.height >= 110:
                            signature = image_signature(image)
            except Exception:
                signature = None
        cache[url] = signature
        return signature

    def match_cover(
        self,
        image_path: Path | str,
        candidates: Sequence[dict],
        identified: Optional[dict] = None,
        ocr_text: str = "",
        limit: int = 8,
    ) -> dict:
        """Görsel imza + künye benzerliğiyle adayları puanlar ve güvenli eşleşmeyi seçer."""
        identified = identified or {}
        capture_signatures, used_bbox = cover_capture_signatures(
            image_path, identified.get("bbox")
        )
        cache: Dict[str, Optional[dict]] = {}
        prepared: List[dict] = []
        downloads = 0
        for item in candidates:
            metadata = float(item.get("_metadata_score") or 0.0)
            if not metadata:
                metadata = metadata_cover_score(identified, ocr_text, item)
            signature = None
            if downloads < MAX_COVER_DOWNLOADS and item.get("cover_url"):
                signature = self._cover_signature_from_url(str(item["cover_url"]), cache)
                if signature is not None:
                    downloads += 1
            prepared.append({
                "book": item,
                "signature": signature,
                "metadata": metadata,
                "visual": None,
            })
        for entry in prepared:
            if entry["signature"] is not None:
                entry["visual"] = max(
                    signature_similarity(entry["signature"], signature)
                    for signature in capture_signatures
                )
        best_visual = max(
            (entry["visual"] for entry in prepared if entry["visual"] is not None),
            default=0.0,
        )
        if best_visual < 0.78 and not used_bbox and any(
            entry["signature"] is not None for entry in prepared
        ):
            windows = _cover_window_signatures(image_path)
            if windows:
                for entry in prepared:
                    if entry["signature"] is None:
                        continue
                    entry["visual"] = max(
                        [entry["visual"]]
                        + [signature_similarity(entry["signature"], signature) for signature in windows]
                    )
        isbn = normalize_isbn(identified.get("isbn"))
        for entry in prepared:
            visual = entry["visual"]
            metadata = entry["metadata"]
            exact = bool(isbn and normalize_isbn(entry["book"].get("isbn")) == isbn)
            if visual is None:
                score = metadata * 0.55
            else:
                score = 0.72 * visual + 0.28 * metadata
            if exact:
                score = min(1.0, score + 0.04)
            accepted = False
            if visual is not None:
                if visual >= 0.80:
                    accepted = True
                elif visual >= 0.70 and metadata >= 0.55:
                    accepted = True
                elif visual >= 0.62 and metadata >= 0.75:
                    accepted = True
                elif exact and metadata >= 0.60 and visual >= 0.55:
                    accepted = True
            elif exact and metadata >= 0.85:
                accepted = True
            entry.update({"score": score, "exact": exact, "accepted": accepted})

        prepared.sort(
            key=lambda entry: (
                entry["accepted"],
                entry["score"],
                entry["visual"] if entry["visual"] is not None else -1.0,
            ),
            reverse=True,
        )
        best = prepared[0] if prepared else None
        chosen: dict = {}
        if best is not None and best["accepted"]:
            chosen = self._prepare(dict(best["book"]))
            chosen["match_similarity"] = (
                round(float(best["visual"]), 3) if best["visual"] is not None else None
            )
            chosen["match_metadata"] = round(float(best["metadata"]), 3)
            chosen["match_score"] = round(float(best["score"]), 3)
            chosen["match_source"] = "görsel + künye"
        ranked: List[dict] = []
        for entry in prepared[:max(1, int(limit))]:
            book = dict(entry["book"])
            for key in ("_metadata_score", "_query_rank", "_specificity", "_work_key"):
                book.pop(key, None)
            book["cover_similarity"] = (
                round(float(entry["visual"]), 3) if entry["visual"] is not None else None
            )
            book["metadata_score"] = round(float(entry["metadata"]), 3)
            book["match_score"] = round(float(entry["score"]), 3)
            book["match_accepted"] = bool(entry["accepted"])
            ranked.append(book)

        if best is None:
            reason = "Kapak adayı bulunamadı."
        elif best["accepted"]:
            if best["visual"] is not None and best["visual"] >= 0.80:
                reason = "Görsel kapak eşleşmesi güçlü."
            elif best["exact"]:
                reason = "ISBN ve künye doğrulandı."
            else:
                reason = "Görsel ve künye birlikte doğrulandı."
        else:
            reason = "Güvenli görsel/künye eşleşmesi yok; adaylar listelendi."
        return {
            "book": chosen,
            "candidates": ranked,
            "match": {
                "similarity": round(float(best["visual"]), 3) if best and best["visual"] is not None else None,
                "metadata": round(float(best["metadata"]), 3) if best else 0.0,
                "score": round(float(best["score"]), 3) if best else 0.0,
                "accepted": bool(best and best["accepted"]),
                "reason": reason,
            },
        }

    def identify_cover(
        self,
        image_path: Path | str,
        ocr_text: str = "",
        ocr_isbn: str = "",
        identified: Optional[dict] = None,
        limit: int = 8,
    ) -> dict:
        """AI künyesi/OCR + çoklu arama + edisyon genişletme + görsel eşleştirme."""
        identified = identified or {}
        queries = cover_query_candidates(ocr_text, identified, ocr_isbn)
        if not queries:
            raise BookError(
                "Kapaktan arama sorgusu çıkarılamadı. Daha iyi ışıkta yeniden tara "
                "veya ISBN gir."
            )
        candidates = self.cover_search(
            queries, limit=max(4, int(limit)), identified=identified, ocr_text=ocr_text
        )
        if not candidates:
            raise BookError(
                "Kapak için açık künye adayı bulunamadı. İnternet bağlantısını ve "
                "kitap servislerini kontrol et; ISBN ile de arayabilirsin."
            )
        result = self.match_cover(
            image_path, candidates, identified=identified, ocr_text=ocr_text, limit=limit
        )
        book = result.get("book") or {}
        if book:
            if not book.get("translators") and identified.get("translators"):
                book["translators"] = list(identified["translators"])
                saved = self.save(book)
                if saved:
                    book = saved
            if not book.get("publisher") and identified.get("publisher"):
                book["publisher"] = identified["publisher"]
            result["book"] = book
        result["queries"] = queries
        result["identified"] = identified
        return result

    def prepare(self, book: dict) -> dict:
        if not book or not book.get("title"):
            raise BookError("Kitap künyesi boş.")
        isbn = normalize_isbn(book.get("isbn"))
        if isbn and not book.get("description") and not book.get("toc"):
            try:
                return self.lookup_isbn(isbn)
            except BookError:
                pass
        return self._prepare(book)

    def cached(self, isbn: str) -> Optional[dict]:
        return self.store.find_by_isbn(isbn) if self.store else None


# ---------------------------------------------------------------- OCR / summary
def prepare_for_ocr(source: Path | str, target: Optional[Path | str] = None) -> Path:
    """Kamera/görsel karesini OCR için geçici, gri ve kontrastlı PNG'ye çevirir."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError:
        raise BookError("Görsel hazırlama için Pillow gerekli.")
    source_path = Path(source)
    try:
        with Image.open(source_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            if image.width > 2400:
                ratio = 2400 / image.width
                image = image.resize((2400, max(1, int(image.height * ratio))), Image.LANCZOS)
            elif image.width < 1400:
                ratio = 1400 / max(1, image.width)
                image = image.resize(
                    (1400, max(1, int(image.height * ratio))), Image.LANCZOS
                )
            gray = ImageOps.grayscale(image)
            gray = ImageOps.autocontrast(gray, cutoff=1)
            gray = ImageEnhance.Contrast(gray).enhance(1.45)
            gray = gray.filter(ImageFilter.SHARPEN)
            bordered = Image.new("L", (gray.width + 32, gray.height + 32), 255)
            bordered.paste(gray, (16, 16))
            if target is None:
                handle = tempfile.NamedTemporaryFile(
                    prefix="jarvis_book_ocr_", suffix=".png", delete=False
                )
                handle.close()
                target = Path(handle.name)
            target = Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            bordered.save(target, "PNG")
            return target
    except BookError:
        raise
    except Exception as error:
        raise BookError(f"Görsel OCR için hazırlanamadı: {error}")


def clean_ocr_text(text: str, limit: int = MAX_TOTAL_PAGE_CHARS) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        line = " ".join(raw.split()).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)[:limit].strip()


def build_summary_prompt(book: dict, pages: Sequence[dict], mode: str = "pages") -> str:
    title = _clean_text(book.get("title"), 240) or "Adsız kitap"
    authors = ", ".join(str(item) for item in (book.get("authors") or [])) or "veride yok"
    translators = ", ".join(str(item) for item in (book.get("translators") or [])) or "veride yok"
    publisher = _clean_text(book.get("publisher"), 240) or "veride yok"
    published = _clean_text(book.get("published"), 80) or "veride yok"
    isbn = normalize_isbn(book.get("isbn")) or "veride yok"
    page_count = book.get("page_count") or 0
    categories = ", ".join(str(item) for item in (book.get("categories") or [])[:10]) or "veride yok"
    toc = [str(item) for item in (book.get("toc") or [])[:MAX_TOC_ITEMS]]
    description = _clean_text(book.get("description"), MAX_DESCRIPTION_CHARS) or "veride yok"

    cleaned_pages: List[Tuple[int, str]] = []
    total_chars = 0
    for page in list(pages)[:MAX_PAGES]:
        number = page.get("number")
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue
        text = clean_ocr_text(page.get("text"), MAX_PAGE_CHARS)
        if not text:
            continue
        allowance = max(0, MAX_TOTAL_PAGE_CHARS - total_chars)
        if allowance <= 0:
            break
        text = text[:allowance]
        total_chars += len(text)
        cleaned_pages.append((number, text))

    toc_text = "\n".join(f"- {item}" for item in toc) if toc else "veride yok"
    if cleaned_pages:
        pages_text = "\n\n".join(
            f"--- Sayfa {number} ---\n{text}" for number, text in cleaned_pages
        )
    else:
        pages_text = "Taranan sayfa yok."

    mode_text = (
        "Kullanıcı yalnızca künye ve içindekilerden bir kitap profili istiyor."
        if mode == "profile"
        else "Kullanıcı taranan sayfalardan kişisel bir okuma özeti istiyor."
    )
    return (
        "Aşağıdaki kitap için Jarvis kitap analizi hazırla.\n\n"
        f"Künye:\n- Ad: {title}\n- Yazar: {authors}\n- Çeviren: {translators}\n- Yayınevi: {publisher}\n"
        f"- Yayın: {published}\n- ISBN: {isbn}\n- Sayfa: {page_count or 'veride yok'}\n"
        f"- Kategoriler: {categories}\n\n"
        f"Kaynak açıklaması (kısa künye):\n{description}\n\n"
        f"İçindekiler:\n{toc_text}\n\n"
        f"Taranan sayfalar (geçici OCR; kalıcı kaydedilmez):\n{pages_text}\n\n"
        f"Görev: {mode_text}\n"
        "Yanıtı Türkçe ve şu başlıklarla ver:\n"
        "1) Kısa yönelim (tür, dönem, kimler için)\n"
        "2) Ana temalar (5-8 madde)\n"
        "3) Yapı / bölüm haritası (yalnızca içindekiler veya taranan sayfalar varsa)\n"
        "4) Önemli fikirler ve sayfa referansları (yalnızca [s. N] ile bildiğin sayfalar)\n"
        "5) Okuma notları: 3 soru ve 3 anahtar kavram\n"
        "6) Veri sınırı: hangi bilgiler eksik, neyi uydurmadın\n\n"
        "Tam metni kopyalama; kendi cümlelerinle, kısa ve yoğun bir özet üret. "
        "Kitapta olmayan bir olay, alıntı veya sayfa numarası ekleme."
    )


def summary_title(book: dict, mode: str) -> str:
    name = _clean_text(book.get("title"), 120) or "Kitap"
    return f"{name} · Kitap profili" if mode == "profile" else f"{name} · Sayfa özeti"


# ------------------------------------------------------------------- agent tool
def format_book(book: dict) -> str:
    authors = ", ".join(book.get("authors") or []) or "bilinmiyor"
    isbn = book.get("isbn") or "yok"
    translators = ", ".join(book.get("translators") or []) or "veride yok"
    lines = [
        f"📖 {book.get('title') or 'Adsız kitap'}",
        f"Yazar: {authors}",
        f"Çeviren: {translators}",
        f"Yayınevi: {book.get('publisher') or 'veride yok'} · Yayın: {book.get('published') or 'veride yok'}",
        f"Sayfa: {book.get('page_count') or 'veride yok'} · ISBN: {isbn}",
    ]
    categories = ", ".join(book.get("categories") or [])
    if categories:
        lines.append(f"Kategoriler: {categories}")
    toc = book.get("toc") or []
    if toc:
        lines.append("İçindekiler: " + " | ".join(toc[:8]))
    if book.get("description"):
        lines.append(f"Kısa açıklama: {_clean_text(book.get('description'), 400)}")
    lines.append(f"Kaynak: {_source_label(book)}")
    return "\n".join(lines)


def format_profile(profile: dict) -> str:
    refs = ", ".join(str(ref) for ref in profile.get("page_refs") or [])
    title = profile.get("book_title") or "Kitap"
    lines = [
        f"📝 {title} · {'Kitap profili' if profile.get('kind') == 'profile' else 'Sayfa özeti'}",
        f"Tarih: {time.strftime('%d.%m.%Y %H:%M', time.localtime(profile.get('created_at') or time.time()))}",
    ]
    if refs:
        lines.append(f"Sayfa referansları: {refs}")
    if profile.get("model"):
        lines.append(f"Model: {profile['model']}")
    lines.extend(["", str(profile.get("summary") or "")[:6000]])
    return "\n".join(lines)


class BookTools:
    def __init__(self, settings=None, listener=None, service: Optional[BookService] = None, store: Optional[BookStore] = None):
        self.listener = listener
        self.service = service or BookService.default(settings)
        self.store = store or (self.service.store if self.service is not None else None)

    def _require_service(self) -> BookService:
        if self.service is None:
            raise BookError("Kitap servisi bu oturumda kullanılamıyor.")
        return self.service

    def _require_store(self) -> BookStore:
        if self.store is None:
            raise BookError("Kitap kitaplığı bu oturumda kullanılamıyor.")
        return self.store

    def run(self, args: dict) -> str:
        action = str(args.get("action") or "lookup").strip().casefold()
        if action == "open_camera":
            if not callable(self.listener):
                raise ActionForbidden("Kamera kitap tarayıcısı bu oturumda açılamıyor.")
            self.listener({"kind": "camera"})
            return (
                "✅ Kamera ve kitap tarayıcı açıldı. Kullanıcı kapağı/ISBN'i gösterebilir; "
                "tarama ve özetleme işlemi arayüzde yapılır. Tam metin kalıcı kaydedilmez."
            )
        if action == "lookup":
            query = str(args.get("query") or "").strip()
            if not query:
                raise BookError("Kitap adı veya ISBN gerekli.")
            service = self._require_service()
            isbn = normalize_isbn(query)
            if isbn:
                book = service.lookup_isbn(isbn)
                profile = self.store.latest_profile(book["id"]) if self.store else None
                result = format_book(book)
                if profile:
                    result += "\n\n" + format_profile(profile)
                return result
            results = service.search(query, limit=max(1, min(int(args.get("limit", 5)), 8)))
            if not results:
                return "❌ Bu arama için kitap künyesi bulunamadı. ISBN veya daha belirgin bir başlık dene."
            lines = [f"🔎 {len(results)} kitap adayı:"]
            for index, book in enumerate(results, start=1):
                authors = ", ".join(book.get("authors") or []) or "bilinmiyor"
                isbn_text = f" · ISBN {book['isbn']}" if book.get("isbn") else ""
                lines.append(f"{index}. {book.get('title') or 'Adsız'} — {authors}{isbn_text}")
            lines.append("Seçim için ISBN veya kitap kimliğiyle lookup kullan.")
            return "\n".join(lines)
        if action == "library":
            store = self._require_store()
            limit = max(1, min(int(args.get("limit", 10)), 30))
            profiles = store.profiles(limit=limit)
            if not profiles:
                return "📚 Kayıtlı kitap özeti yok. Kamera menüsünden kitap tarayıp özet oluştur."
            lines = [f"📚 Son {len(profiles)} kitap özeti:"]
            for profile in profiles:
                when = time.strftime("%d.%m.%Y", time.localtime(profile.get("created_at") or time.time()))
                lines.append(
                    f"- {profile.get('book_title') or 'Kitap'} · "
                    f"{'profil' if profile.get('kind') == 'profile' else 'sayfa özeti'} · {when}"
                )
            return "\n".join(lines)
        if action == "profile":
            store = self._require_store()
            book_id = str(args.get("book_id") or args.get("isbn") or "").strip()
            query = str(args.get("query") or "").strip()
            if book_id:
                normalized = normalize_isbn(book_id)
                book = store.get_book(book_id) or (store.find_by_isbn(normalized) if normalized else None)
                if book is None and query:
                    matches = store.find_books(query, limit=1)
                    book = matches[0] if matches else None
                if book is None:
                    return "❌ Bu kitap kitaplıkta bulunamadı."
                profile = store.latest_profile(book["id"])
            elif query:
                matches = store.find_books(query, limit=5)
                if not matches:
                    return "❌ Kitaplıkta eşleşen kitap yok."
                book = matches[0]
                profile = store.latest_profile(book["id"])
            else:
                raise BookError("Kitap kimliği veya adı gerekli.")
            if profile is None:
                return f"📖 {book.get('title') or 'Kitap'} için kayıtlı özet yok."
            return format_book(book) + "\n\n" + format_profile(profile)
        raise ActionForbidden("book için action: open_camera, lookup, library veya profile olmalı.")
