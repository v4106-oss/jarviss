"""Vikipedi özet aracı.

Türkçe sayfa yoksa İngilizceye düşer. Yalnızca özet, konum ve kaynak
bağlantısı döndürür; fotoğraf indirmez. Sabit Vikipedi alan adları kullanılır.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import quote

import requests

TIMEOUT = (5, 10)
USER_AGENT = "JarvisDesktop/1.0 (local desktop assistant)"
CACHE_SECONDS = 3_600
MAX_DISK_ITEMS = 200
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis" / "data"
WIKI_CACHE = DATA_DIR / "wiki.json"
SUMMARY_URL = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
SEARCH_URL = "https://{lang}.wikipedia.org/w/api.php"
MAX_EXTRACT = 1_800


class WikiUnavailable(RuntimeError):
    pass


def _get_json(url: str, params: Optional[dict] = None) -> Tuple[int, Optional[dict]]:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        return 404, None
    response.raise_for_status()
    return response.status_code, response.json()


class WikiLookup:
    def __init__(
        self,
        fetcher: Optional[Callable[..., Tuple[int, Optional[dict]]]] = None,
        cache_path: Optional[Path] = None,
    ) -> None:
        self._fetch = fetcher or _get_json
        self.cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[float, Optional[dict]]] = {}
        self._search_cache: Dict[str, Tuple[float, list]] = {}
        self._last_error: Optional[str] = None
        self._stale = False
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, dict):
            return
        loaded: Dict[str, Tuple[float, Optional[dict]]] = {}
        for key, row in items.items():
            if not isinstance(row, dict) or not isinstance(row.get("at"), (int, float)):
                continue
            data = row.get("data")
            if data is not None and not isinstance(data, dict):
                continue
            loaded[str(key)] = (float(row["at"]), data)
        if len(loaded) > MAX_DISK_ITEMS:
            loaded = dict(sorted(loaded.items(), key=lambda item: item[1][0], reverse=True)[:MAX_DISK_ITEMS])
        with self._lock:
            self._cache = loaded

    def _save_disk_cache(self) -> None:
        if self.cache_path is None:
            return
        with self._lock:
            items = {
                key: {"at": row[0], "data": row[1]}
                for key, row in sorted(
                    self._cache.items(), key=lambda item: item[1][0], reverse=True
                )[:MAX_DISK_ITEMS]
            }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            temp.write_text(
                json.dumps({"version": 1, "items": items}, ensure_ascii=False),
                encoding="utf-8",
            )
            temp.replace(self.cache_path)
        except OSError:
            pass

    def _summary(self, lang: str, title: str) -> Optional[dict]:
        try:
            status, payload = self._fetch(SUMMARY_URL.format(lang=lang, title=quote(title, safe="")), None)
            if status == 404 or not isinstance(payload, dict):
                return None
            if payload.get("type") == "disambiguation":
                search_status, search = self._fetch(
                    SEARCH_URL.format(lang=lang),
                    {
                        "action": "query", "list": "search", "srsearch": title,
                        "srlimit": 1, "format": "json",
                    },
                )
                hits = ((search or {}).get("query") or {}).get("search") or []
                if search_status != 200 or not hits:
                    return None
                candidate = str(hits[0].get("title") or "").strip()
                if not candidate or candidate == title:
                    return None
                status, payload = self._fetch(
                    SUMMARY_URL.format(lang=lang, title=quote(candidate, safe="")), None
                )
                if status == 404 or not isinstance(payload, dict):
                    return None
            return payload
        except Exception as error:
            raise WikiUnavailable(str(error)) from error

    def _search_once(self, lang: str, query: str, limit: int) -> list:
        status, payload = self._fetch(
            SEARCH_URL.format(lang=lang),
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "format": "json",
            },
        )
        if status != 200 or not isinstance(payload, dict):
            return []
        hits = ((payload.get("query") or {}).get("search") or [])
        rows = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title") or "").strip()
            if not title:
                continue
            snippet = html.unescape(re.sub(r"<[^>]+>", " ", str(hit.get("snippet") or "")))
            rows.append({
                "lang": lang,
                "title": title,
                "snippet": " ".join(snippet.split())[:240],
                "url": f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}",
            })
        return rows[:limit]

    def search(self, query: str, limit: int = 5) -> list:
        query = re.sub(r"\s+", " ", str(query or "")).strip()
        if not query or len(query) > 200:
            return []
        try:
            limit = max(1, min(int(limit), 10))
        except (TypeError, ValueError):
            limit = 5
        cache_key = f"{query.casefold()}:{limit}"
        self._last_error = None
        with self._lock:
            cached = self._search_cache.get(cache_key)
            if cached and time.time() - cached[0] < CACHE_SECONDS:
                return list(cached[1])
        errors = []
        for lang in ("tr", "en"):
            try:
                rows = self._search_once(lang, query, limit)
            except Exception as error:
                errors.append(str(error))
                continue
            if not rows:
                continue
            with self._lock:
                if len(self._search_cache) >= 64:
                    oldest = min(self._search_cache.items(), key=lambda item: item[1][0])[0]
                    self._search_cache.pop(oldest, None)
                self._search_cache[cache_key] = (time.time(), rows)
                self._last_error = None
            return rows
        if errors:
            self._last_error = errors[-1]
        return []

    @staticmethod
    def _result(payload: dict, lang: str, fallback_title: str) -> Optional[dict]:
        extract = str(payload.get("extract") or "").strip()
        if not extract:
            return None
        coordinates = payload.get("coordinates") or {}
        content_urls = payload.get("content_urls") or {}
        return {
            "lang": lang,
            "title": str(payload.get("title") or fallback_title),
            "description": str(payload.get("description") or "").strip(),
            "extract": extract[:MAX_EXTRACT],
            "url": str(((content_urls.get("desktop") or {}).get("page")) or ""),
            "lat": coordinates.get("lat"),
            "lon": coordinates.get("lon"),
        }

    def lookup(self, title: str) -> Optional[dict]:
        query = re.sub(r"\s+", " ", str(title or "")).strip()
        if not query or len(query) > 200:
            return None
        self._stale = False
        self._last_error = None
        with self._lock:
            cached = self._cache.get(query.casefold())
            if cached and time.time() - cached[0] < CACHE_SECONDS:
                self._last_error = None
                return cached[1]
        result = None
        errors = []
        for lang in ("tr", "en"):
            try:
                payload = self._summary(lang, query)
            except WikiUnavailable as error:
                errors.append(str(error))
                continue
            if not payload:
                continue
            result = self._result(payload, lang, query)
            if result:
                break
        if result is None and not errors:
            suggestions = self.search(query, 1)
            if suggestions:
                candidate = suggestions[0]
                try:
                    payload = self._summary(candidate["lang"], candidate["title"])
                except WikiUnavailable as error:
                    errors.append(str(error))
                else:
                    if payload:
                        result = self._result(payload, candidate["lang"], candidate["title"])
                    if result is None:
                        errors.append("Arama sonucu özeti okunamadı.")
            elif self._last_error:
                errors.append(self._last_error)
        saved = False
        with self._lock:
            # Ağ hatasını "bulunamadı" diye önbelleğe alma; kısa süre sonra tekrar denenebilsin.
            if result is not None or not errors:
                self._cache[query.casefold()] = (time.time(), result)
                saved = True
            self._last_error = None if result is not None or not errors else errors[-1]
        if saved:
            self._save_disk_cache()
        if result is None and errors and cached and cached[1] is not None:
            self._stale = True
            return cached[1]
        return result

    def get(self, args: Optional[dict] = None) -> str:
        args = args or {}
        action = str(args.get("action") or "lookup").strip().casefold()
        if action not in {"lookup", "search"}:
            return "❌ wiki için action: lookup veya search olmalı."
        if action == "search":
            query = str(
                args.get("query") or args.get("title") or args.get("baslik") or ""
            ).strip()
            if not query:
                return "❌ Vikipedi araması için sorgu gerekli."
            rows = self.search(query, args.get("limit", 5))
            if not rows:
                if self._last_error:
                    return (
                        "❌ Vikipedi aramasına şu anda ulaşılamadı "
                        f"({self._last_error[:160]}). Sonuç uydurma; bağlantı sorununu söyle."
                    )
                return f"ℹ️ Vikipedi'de '{query}' için arama sonucu bulunamadı."
            lines = [f"✅ Vikipedi araması · {query} · {len(rows)} sonuç"]
            for row in rows:
                language = "TR" if row["lang"] == "tr" else "EN"
                lines.append(f"- [{language}] {row['title']}")
                if row["snippet"]:
                    lines.append(f"  {row['snippet']}")
                if row["url"]:
                    lines.append(f"  {row['url']}")
            lines.append("ℹ️ Ayrıntılı özet için action=lookup ve title kullan.")
            return "\n".join(lines)
        title = str(args.get("title") or args.get("baslik") or "").strip()
        if not title:
            return "❌ Vikipedi araması için başlık gerekli."
        result = self.lookup(title)
        if result is None:
            if self._last_error:
                return (
                    "❌ Vikipedi'ye şu anda ulaşılamadı "
                    f"({self._last_error[:160]}). Bilgi uydurma; bağlantı sorununu söyle."
                )
            return f"ℹ️ Vikipedi'de '{title}' bulunamadı. Bilgi uydurma; bulunamadığını söyle."
        language = "Türkçe" if result["lang"] == "tr" else "İngilizce"
        lines = [
            f"✅ Vikipedi · {language} · {result['title']}"
            + (" · ÖNBELLEK" if self._stale else "")
        ]
        if result["description"]:
            lines.append(result["description"])
        lines.append("")
        lines.append(result["extract"])
        if result["lat"] is not None and result["lon"] is not None:
            lines.append("")
            lines.append(f"Konum: {result['lat']}, {result['lon']}")
        if result["url"]:
            lines.append(f"Kaynak: {result['url']}")
        lines.append("")
        if self._stale:
            lines.append("⚠️ Canlı veri alınamadı; diskteki son önbellek gösteriliyor.")
        lines.append("⚠️ Özet kaynaktan alınmıştır; sayısal ayrıntı ve güncellik için kaynağa bak.")
        return "\n".join(lines)


_default_service: Optional[WikiLookup] = None
_default_lock = threading.Lock()


def shared_wiki_service() -> WikiLookup:
    """Aynı süreçteki tüm ajan turlarının önbelleği paylaşmasını sağlar."""
    global _default_service
    if _default_service is None:
        with _default_lock:
            if _default_service is None:
                _default_service = WikiLookup(cache_path=WIKI_CACHE)
    return _default_service
