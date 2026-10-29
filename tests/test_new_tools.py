import json
import tempfile
import time
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import requests

from app.core.ai import TOOL_DECLARATIONS
from app.core.file_context import extract_file_context
from app.core.market_tools import MarketService, TODAY_URL, parse_bulletin, _previous_weekday
from app.core.media_tools import youtube_target
from app.core.memory_tools import MemoryTools
from app.core.news_tools import CATEGORIES, FEEDS, NewsService, _feed_items
from app.core.safety import ActionForbidden, PathPolicy, Risk, classify_action
from app.core.skill_library import SkillError, SkillLibrary, parse_skill
from app.core.storage import Storage
from app.core.weather_tools import format_weather
from app.core.wiki_tools import WikiLookup


class FakeResponse:
    def __init__(self, content=b"", status_code=200, payload=None, headers=None):
        self.content = content
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def rss(items):
    parts = []
    for title, summary, moment in items:
        stamp = format_datetime(moment)
        parts.append(
            f"<item><title>{title}</title><description>{summary}</description>"
            f"<link>https://example.test/{title[:8]}</link><pubDate>{stamp}</pubDate></item>"
        )
    return ("<?xml version='1.0'?><rss><channel>" + "".join(parts) + "</channel></rss>").encode()


def tcmb(day, usd, eur=44.0, gbp=52.0):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Tarih_Date Tarih="{day}">
  <Currency CurrencyCode="USD" Kod="USD"><Unit>1</Unit><Isim>ABD DOLARI</Isim><ForexSelling>{usd}</ForexSelling></Currency>
  <Currency CurrencyCode="EUR" Kod="EUR"><Unit>1</Unit><Isim>EURO</Isim><ForexSelling>{eur}</ForexSelling></Currency>
  <Currency CurrencyCode="GBP" Kod="GBP"><Unit>1</Unit><Isim>İNGİLİZ STERLİNİ</Isim><ForexSelling>{gbp}</ForexSelling></Currency>
</Tarih_Date>""".encode()


def wiki_payload(title="Ankara", extract="Ankara, Türkiye'nin başkentidir."):
    return {
        "type": "standard",
        "title": title,
        "description": "Türkiye'nin başkenti",
        "extract": extract,
        "coordinates": {"lat": 39.93, "lon": 32.86},
        "content_urls": {"desktop": {"page": "https://tr.wikipedia.org/wiki/Ankara"}},
    }


class NewsTests(unittest.TestCase):
    def test_feed_parsing_and_dedupe(self):
        now = datetime.now(timezone.utc)
        raw = rss([
            ("Merkez Bankası faiz kararını açıkladı", "Piyasalar kararı bekliyordu.", now),
            ("Merkez Bankası faiz kararını duyurdu", "Karar sonrası piyasa hareketlendi.", now - timedelta(minutes=5)),
        ])
        items = _feed_items(raw, "Kaynak", "ekonomi")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["source"], "Kaynak")
        self.assertEqual(items[0]["category"], "ekonomi")
        self.assertTrue(items[0]["title"].startswith("Merkez Bankası"))
        other = _feed_items(rss([("Merkez Bankası faiz kararını duyurdu", "Özet", now)]), "Diğer", "ekonomi")

        service = NewsService(feeds=[{"source": "A", "url": "http://a", "category": "ekonomi"}])
        with patch.object(service, "_get_feed", return_value=items + other):
            text = service.get({"limit": 10})
        payload = json.loads(text[text.index("{"):])
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["other_sources"], ["Diğer"])

    def test_filters_and_cache(self):
        now = datetime.now(timezone.utc)
        fresh = {"title": "Yeni teknoloji haberi", "summary": "", "source": "A", "category": "teknoloji",
                 "published_at": now.isoformat(), "url": ""}
        old = {"title": "Eski ekonomi haberi", "summary": "", "source": "B", "category": "ekonomi",
               "published_at": (now - timedelta(hours=100)).isoformat(), "url": ""}
        calls = []

        def fake_feed(feed):
            calls.append(feed["url"])
            return [fresh] if feed["category"] == "teknoloji" else [old]

        service = NewsService(feeds=[
            {"source": "A", "url": "http://a", "category": "teknoloji"},
            {"source": "B", "url": "http://b", "category": "ekonomi"},
        ])
        with patch.object(service, "_get_feed", side_effect=fake_feed):
            data = service.fetch_all()
            service.fetch_all()
            self.assertEqual(len(calls), 2)
            service.fetch_all(force=True)
            self.assertEqual(len(calls), 4)
        self.assertEqual(data["scanned"], 2)
        with patch.object(service, "_get_feed", side_effect=fake_feed):
            text = service.get({"categories": ["teknoloji"], "since_hours": 18})
        self.assertIn("Yeni teknoloji haberi", text)
        self.assertNotIn("Eski ekonomi haberi", text)
        with patch.object(service, "_get_feed", side_effect=fake_feed):
            text = service.get({"exclude_categories": ["teknoloji"], "since_hours": 168})
        self.assertIn("Eski ekonomi haberi", text)
        self.assertNotIn("Yeni teknoloji haberi", text)
        with patch.object(service, "_get_feed", side_effect=fake_feed):
            text = service.get({"categories": ["teknoloji", "kültür-sanat"], "since_hours": 18})
        self.assertIn("Yeni teknoloji haberi", text)
        self.assertNotIn("Eski ekonomi haberi", text)
        with patch.object(service, "_get_feed", side_effect=fake_feed):
            text = service.get({"sources": ["B"], "since_hours": 168})
        self.assertIn("Eski ekonomi haberi", text)
        self.assertNotIn("Yeni teknoloji haberi", text)

    def test_keyword_query_filters_title_and_summary(self):
        now = datetime.now(timezone.utc)
        items = [
            {"title": "Merkez Bankası FAİZ kararını açıkladı", "summary": "PİYASA kararı bekliyordu.",
             "source": "A", "category": "ekonomi", "published_at": now.isoformat(), "url": ""},
            {"title": "Yeni telefon tanıtıldı", "summary": "Kamera ve batarya iyileşti.",
             "source": "B", "category": "teknoloji", "published_at": now.isoformat(), "url": ""},
        ]
        service = NewsService(feeds=[
            {"source": "A", "url": "http://a", "category": "ekonomi"},
            {"source": "B", "url": "http://b", "category": "teknoloji"},
        ])
        with patch.object(service, "_get_feed", side_effect=lambda feed: [items[0] if feed["url"] == "http://a" else items[1]]):
            text = service.get({"query": "faiz"})
            only_news = json.loads(text[text.index("{"):])
            summary_text = service.get({"query": "kamera"})
            only_tech = json.loads(summary_text[summary_text.index("{"):])
        self.assertEqual(only_news["query"], "faiz")
        self.assertEqual(len(only_news["items"]), 1)
        self.assertIn("FAİZ", only_news["items"][0]["title"])
        self.assertEqual(len(only_tech["items"]), 1)
        self.assertIn("telefon", only_tech["items"][0]["title"])
        with patch.object(service, "_get_feed", side_effect=lambda feed: [items[0] if feed["url"] == "http://a" else items[1]]):
            empty = service.get({"query": "borsa"})
        self.assertIn("'borsa' aramasıyla", empty)

    def test_large_payload_stays_valid_json(self):
        now = datetime.now(timezone.utc)
        items = []
        for index in range(40):
            items.append({
                "title": " ".join(f"konu{index}x{part}" for part in range(12)),
                "summary": "özet " * 80,
                "source": "A",
                "category": "gundem",
                "published_at": (now - timedelta(minutes=index)).isoformat(),
                "url": "",
            })
        service = NewsService(feeds=[{"source": "A", "url": "http://a", "category": "gundem"}])
        with patch.object(service, "_get_feed", return_value=items):
            text = service.get({"limit": 40})
        payload = json.loads(text[text.index("{"):])
        self.assertTrue(payload.get("truncated"))
        self.assertLess(len(payload["items"]), 40)

    def test_every_category_has_a_feed(self):
        self.assertEqual({feed["category"] for feed in FEEDS}, CATEGORIES)

    def test_partial_feed_failure_is_reported(self):
        now = datetime.now(timezone.utc)
        item = {"title": "Çalışan kaynak haberi", "summary": "", "source": "A",
                "category": "gundem", "published_at": now.isoformat(), "url": ""}
        service = NewsService(feeds=[
            {"source": "A", "url": "http://a", "category": "gundem"},
            {"source": "B", "url": "http://b", "category": "gundem"},
        ])

        def fake_feed(feed):
            if feed["url"] == "http://b":
                raise requests.ConnectionError("down")
            return [item]

        with patch.object(service, "_get_feed", side_effect=fake_feed):
            data = service.fetch_all()
        self.assertEqual(data["feeds_failed"], 1)
        self.assertEqual(data["failed_sources"], ["B"])
        text = service.get({})
        self.assertIn("Bazı haber akışlarına ulaşılamadı", text)
        self.assertIn("Çalışan kaynak haberi", text)

    def test_all_sources_fail(self):
        service = NewsService(feeds=[{"source": "A", "url": "http://a", "category": "gundem"}])
        with patch("app.core.news_tools.requests.get", side_effect=requests.ConnectionError("down")):
            text = service.get({})
        self.assertIn("ulaşılamadı", text)

    def test_requests_path(self):
        now = datetime.now(timezone.utc)
        response = FakeResponse(content=rss([("Başlık haber", "özet", now)]))
        service = NewsService(feeds=[{"source": "A", "url": "http://a", "category": "gundem"}])
        with patch("app.core.news_tools.requests.get", return_value=response) as get:
            data = service.fetch_all()
        get.assert_called_once()
        self.assertEqual(len(data["items"]), 1)


class NewsCacheTests(unittest.TestCase):
    FEEDS = [{"source": "A", "url": "http://a", "category": "gundem"}]

    def _rss(self):
        return rss([("Önbellek haberi", "özet", datetime.now(timezone.utc))])

    def test_conditional_get_reuses_304_items(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "news.json"
            service = NewsService(feeds=self.FEEDS, cache_path=cache)
            first = FakeResponse(
                content=self._rss(),
                headers={"ETag": '"abc"', "Last-Modified": "Mon, 26 Oct 2026 10:00:00 GMT"},
            )
            with patch("app.core.news_tools.requests.get", return_value=first):
                initial = service.fetch_all()
            self.assertEqual(len(initial["items"]), 1)

            with patch(
                "app.core.news_tools.requests.get",
                return_value=FakeResponse(status_code=304),
            ) as get:
                again = service.fetch_all(force=True)
            self.assertEqual(len(again["items"]), 1)
            headers = get.call_args.kwargs["headers"]
            self.assertEqual(headers.get("If-None-Match"), '"abc"')
            self.assertEqual(headers.get("If-Modified-Since"), "Mon, 26 Oct 2026 10:00:00 GMT")

    def test_fresh_disk_cache_skips_network(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "news.json"
            with patch("app.core.news_tools.requests.get", return_value=FakeResponse(content=self._rss())):
                NewsService(feeds=self.FEEDS, cache_path=cache).fetch_all()

            with patch("app.core.news_tools.requests.get") as get:
                data = NewsService(feeds=self.FEEDS, cache_path=cache).fetch_all()
            get.assert_not_called()
            self.assertEqual(len(data["items"]), 1)

    def test_stale_disk_cache_is_fallback_when_network_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "news.json"
            with patch("app.core.news_tools.requests.get", return_value=FakeResponse(content=self._rss())):
                NewsService(feeds=self.FEEDS, cache_path=cache).fetch_all()

            payload = json.loads(cache.read_text(encoding="utf-8"))
            payload["cache"]["at"] = time.time() - 1200
            cache.write_text(json.dumps(payload), encoding="utf-8")

            service = NewsService(feeds=self.FEEDS, cache_path=cache)
            with patch("app.core.news_tools.requests.get", side_effect=requests.ConnectionError("down")):
                data = service.fetch_all()
            self.assertTrue(data["stale"])
            self.assertEqual(len(data["items"]), 1)

    def test_corrupt_disk_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "news.json"
            cache.write_text("{bozuk", encoding="utf-8")
            service = NewsService(feeds=self.FEEDS, cache_path=cache)
            self.assertIsNone(service._cache)
            with patch("app.core.news_tools.requests.get", return_value=FakeResponse(content=self._rss())):
                data = service.fetch_all()
            self.assertEqual(len(data["items"]), 1)


class MarketTests(unittest.TestCase):
    def test_parse_and_weekdays(self):
        raw = tcmb("23.10.2026", "41,2500")
        day, values = parse_bulletin(raw)
        self.assertEqual(day, date(2026, 10, 23))
        self.assertAlmostEqual(values["USD"]["selling"], 41.25)
        self.assertEqual(values["USD"]["name"], "ABD DOLARI")
        self.assertEqual(_previous_weekday(date(2026, 10, 26)), date(2026, 10, 23))
        self.assertEqual(_previous_weekday(date(2026, 10, 25)), date(2026, 10, 23))

    def test_get_with_change_and_no_broken_date(self):
        def fetcher(url):
            if url == TODAY_URL:
                return tcmb("23.10.2026", "41.0000")
            return tcmb("22.10.2026", "40.0000")

        service = MarketService(fetcher=fetcher)
        text = service.get()
        self.assertIn("41,0000", text)
        self.assertIn("+2,50", text)
        self.assertIn("23.10.2026", text)
        self.assertIn("22.10.2026", text)
        self.assertNotIn("23,10,2026", text)

    def test_stale_cache(self):
        state = {"fail": False}

        def fetcher(url):
            if state["fail"]:
                raise requests.ConnectionError("down")
            return tcmb("23.10.2026", "41.0000")

        service = MarketService(fetcher=fetcher)
        self.assertIn("41,0000", service.get())
        state["fail"] = True
        service._today["at"] = 0
        text = service.get()
        self.assertIn("ÖNBELLEK", text)
        self.assertIn("41,0000", text)

    def test_disk_cache_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "market.json"
            first = MarketService(
                fetcher=lambda url: tcmb("23.10.2026", "41.0000"),
                cache_path=cache,
            )
            self.assertIn("41,0000", first.get())
            self.assertTrue(cache.is_file())

            def failing(url):
                raise requests.ConnectionError("down")

            second = MarketService(fetcher=failing, cache_path=cache)
            text = second.get()
            self.assertIn("41,0000", text)
            self.assertNotIn("ÖNBELLEK", text)

    def test_stale_disk_cache_is_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "market.json"
            first = MarketService(
                fetcher=lambda url: tcmb("23.10.2026", "41.0000"),
                cache_path=cache,
            )
            first.get()
            payload = json.loads(cache.read_text(encoding="utf-8"))
            payload["today"]["at"] = time.time() - 7_200
            cache.write_text(json.dumps(payload), encoding="utf-8")

            def failing(url):
                raise requests.ConnectionError("down")

            second = MarketService(fetcher=failing, cache_path=cache)
            text = second.get()
            self.assertIn("ÖNBELLEK", text)
            self.assertIn("41,0000", text)


class WikiTests(unittest.TestCase):
    def test_turkish_hit_and_cache(self):
        calls = []

        def fetcher(url, params=None):
            calls.append(url)
            return 200, {
                "type": "standard",
                "title": "Ankara",
                "description": "Türkiye'nin başkenti",
                "extract": "Ankara, Türkiye'nin başkentidir.",
                "coordinates": {"lat": 39.93, "lon": 32.86},
                "content_urls": {"desktop": {"page": "https://tr.wikipedia.org/wiki/Ankara"}},
            }

        wiki = WikiLookup(fetcher=fetcher)
        text = wiki.get({"title": "Ankara"})
        self.assertIn("Türkçe", text)
        self.assertIn("başkentidir", text)
        self.assertIn("39.93", text)
        wiki.get({"title": "Ankara"})
        self.assertEqual(len(calls), 1)

    def test_english_fallback_and_disambiguation(self):
        def fetcher(url, params=None):
            if "tr.wikipedia" in url:
                return 404, None
            if params:
                return 200, {"query": {"search": [{"title": "Ada Lovelace"}]}}
            return 200, {"type": "standard", "title": "Ada Lovelace", "extract": "Mathematician."}

        wiki = WikiLookup(fetcher=fetcher)
        result = wiki.lookup("Ada Lovelace")
        self.assertEqual(result["lang"], "en")
        self.assertEqual(result["extract"], "Mathematician.")

        seen = []

        def disambiguation(url, params=None):
            seen.append(url)
            if params:
                return 200, {"query": {"search": [{"title": "Mercury (planet)"}]}}
            if "Mercury" in url and "%28planet%29" in url:
                return 200, {"type": "standard", "title": "Mercury (planet)", "extract": "Planet."}
            return 200, {"type": "disambiguation", "extract": ""}

        result = WikiLookup(fetcher=disambiguation).lookup("Mercury")
        self.assertEqual(result["title"], "Mercury (planet)")

    def test_search_lists_results_and_caches(self):
        calls = []

        def fetcher(url, params=None):
            calls.append((url, dict(params or {})))
            return 200, {"query": {"search": [
                {"title": "Ankara", "snippet": "<span class='searchmatch'>Ankara</span> Türkiye'nin başkentidir."},
                {"title": "Ankara (il)", "snippet": "İl hakkında."},
            ]}}

        wiki = WikiLookup(fetcher=fetcher)
        text = wiki.get({"action": "search", "query": "Ankara"})
        self.assertIn("[TR] Ankara", text)
        self.assertIn("başkentidir", text)
        self.assertIn("wikipedia.org/wiki/Ankara", text)
        wiki.get({"action": "search", "query": "Ankara"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["action"], "query")

    def test_lookup_uses_search_suggestion_when_exact_title_missing(self):
        def fetcher(url, params=None):
            if params:
                if "tr.wikipedia" in url:
                    return 200, {"query": {"search": [{"title": "Ankara"}]}}
                return 200, {"query": {"search": []}}
            if url.endswith("/Ankara"):
                return 200, {"type": "standard", "title": "Ankara", "extract": "Başkent."}
            return 404, None

        result = WikiLookup(fetcher=fetcher).lookup("başkent ankara")
        self.assertEqual(result["title"], "Ankara")
        self.assertEqual(result["lang"], "tr")

    def test_search_network_error_is_reported(self):
        def failing(url, params=None):
            raise requests.ConnectionError("down")

        text = WikiLookup(fetcher=failing).get({"action": "search", "query": "Ankara"})
        self.assertIn("ulaşılamadı", text)

    def test_missing(self):
        wiki = WikiLookup(fetcher=lambda url, params=None: (404, None))
        self.assertIsNone(wiki.lookup("Yok"))
        self.assertIn("bulunamadı", wiki.get({"title": "Yok"}))

    def test_network_error_is_not_cached_as_missing(self):
        state = {"fail": True}

        def fetcher(url, params=None):
            if state["fail"]:
                raise requests.ConnectionError("down")
            return 404, None

        wiki = WikiLookup(fetcher=fetcher)
        self.assertIn("ulaşılamadı", wiki.get({"title": "Deneme"}))
        state["fail"] = False
        self.assertIn("bulunamadı", wiki.get({"title": "Deneme"}))

    def test_disk_cache_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "wiki.json"
            first = WikiLookup(
                fetcher=lambda url, params=None: (200, wiki_payload()),
                cache_path=cache,
            )
            self.assertIsNotNone(first.lookup("Ankara"))
            self.assertTrue(cache.is_file())

            def failing(url, params=None):
                raise requests.ConnectionError("down")

            second = WikiLookup(fetcher=failing, cache_path=cache)
            result = second.lookup("Ankara")
            self.assertEqual(result["title"], "Ankara")
            self.assertFalse(second._stale)

    def test_stale_disk_cache_is_fallback_on_network_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "wiki.json"
            first = WikiLookup(
                fetcher=lambda url, params=None: (200, wiki_payload()),
                cache_path=cache,
            )
            first.lookup("Ankara")
            payload = json.loads(cache.read_text(encoding="utf-8"))
            for row in payload["items"].values():
                row["at"] = time.time() - 7_200
            cache.write_text(json.dumps(payload), encoding="utf-8")

            def failing(url, params=None):
                raise requests.ConnectionError("down")

            second = WikiLookup(fetcher=failing, cache_path=cache)
            text = second.get({"title": "Ankara"})
            self.assertIn("ÖNBELLEK", text)
            self.assertIn("başkentidir", text)


class MemorySkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_memory_crud_and_limits(self):
        with Storage(str(self.root / "jarvis.db")) as storage:
            tools = MemoryTools(storage)
            self.assertIn("not yok", tools.run({"action": "list"}))
            self.assertIn("kaydedildi", tools.run({"action": "add", "key": "kahve", "value": "Sade içer"}))
            self.assertIn("Sade içer", tools.run({"action": "list"}))
            tools.run({"action": "add", "key": "kahve", "value": "Orta şekerli"})
            self.assertIn("Orta şekerli", tools.run({"action": "list"}))
            self.assertEqual(len(storage.get_all_memory()), 1)
            self.assertIn("silindi", tools.run({"action": "delete", "key": "kahve"}))
            self.assertIn("bulunamadı", tools.run({"action": "delete", "key": "kahve"}))
            with self.assertRaises(ValueError):
                tools.run({"action": "add", "value": "x" * 2001})
            with self.assertRaises(ValueError):
                tools.run({"action": "add"})

    def test_skill_parse_create_read(self):
        skill = parse_skill("---\nname: test-skill\ndescription: Deneme\n---\n\nAdımlar.", self.root / "SKILL.md")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "test-skill")
        self.assertIsNone(parse_skill("name: test", self.root / "x"))
        self.assertIsNone(parse_skill("---\nname: BAD NAME\ndescription: x\n---\nbody", self.root / "x"))
        self.assertIsNone(parse_skill("---\nname: ok-name\ndescription: x\n---\n", self.root / "x"))

        library = SkillLibrary(root=self.root / "skills", seed=False)
        created = library.create("Günlük Plan", "Plan yapar", "1. Adım")
        self.assertEqual(created.name, "gunluk-plan")
        self.assertIn("gunluk-plan", [s.name for s in library.list_skills()])
        self.assertIn("1. Adım", library.run({"action": "read", "name": "gunluk plan"}))
        self.assertIn("gunluk-plan", library.index_text())
        with self.assertRaises(SkillError):
            library.create("gunluk-plan", "x", "y")
        with self.assertRaises(SkillError):
            library.run({"action": "create", "name": "gunluk-plan", "description": "x", "body": "y"})
        self.assertIn("bulunamadı", library.run({"action": "read", "name": "yok"}))
        self.assertIn("kaydedildi", library.run({
            "action": "create", "name": "../../evil", "description": "x", "body": "y", "overwrite": True,
        }))
        self.assertEqual(library.get("evil").path, (self.root / "skills" / "evil" / "SKILL.md"))
        with self.assertRaises(SkillError):
            library.create("!!!", "x", "y")
        library.create("yeni-cache", "Önbellek denemesi", "Adım")
        self.assertIn("yeni-cache", library.index_text())

    def test_skill_seed(self):
        library = SkillLibrary(root=self.root / "skills", seed=True)
        names = {skill.name for skill in library.list_skills()}
        self.assertIn("belge-ozetle", names)
        self.assertIn("hata-ayikla", names)
        self.assertIn("gunluk-plan", names)
        self.assertIn("arastirma-notu", names)
        self.assertIn("YETENEK", library.run({"action": "read", "name": "belge-ozetle"}))


class MediaWeatherTests(unittest.TestCase):
    def test_youtube_target(self):
        self.assertEqual(youtube_target(query="kedi videoları"), "https://www.youtube.com/results?search_query=kedi+videolar%C4%B1")
        self.assertEqual(youtube_target(url="http://youtube.com/watch?v=1"), "https://youtube.com/watch?v=1")
        self.assertEqual(youtube_target(url="youtu.be/abc"), "https://youtu.be/abc")
        with self.assertRaises(ValueError):
            youtube_target(url="https://youtube.com.evil.test/watch")
        with self.assertRaises(ValueError):
            youtube_target()

    def test_weather_text(self):
        data = {
            "location": "Evim · Kocaeli",
            "current": {"temp": 18, "text": "Parçalı bulutlu", "feels": 17, "humidity": 60,
                        "wind": 12, "wind_dir": "K", "pressure": 1012, "uv": 6,
                        "sunrise": "07:10", "sunset": "18:05"},
            "daily": [{"min": 12, "max": 20, "text": "Parçalı bulutlu"},
                      {"min": 11, "max": 19, "text": "Yağmurlu"},
                      {"min": 10, "max": 17, "text": "Güneşli"}],
            "hourly": [{"precip": 20}, {"precip": 65}],
            "updated": "09:30",
            "stale": True,
        }
        text = format_weather(data)
        self.assertIn("Parçalı bulutlu", text)
        self.assertIn("Yarın", text)
        self.assertIn("Öbür gün", text)
        self.assertIn("Güneşli", text)
        self.assertIn("UV 6", text)
        self.assertIn("Gün doğumu 07:10", text)
        self.assertIn("yağış olasılığı", text)
        self.assertIn("ÖNBELLEK", text)
        with self.assertRaises(ValueError):
            format_weather({})


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_xlsx(self):
        path = self.root / "tablo.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="Sayfa1"/></sheets></workbook>')
            archive.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Merhaba</t></si><si><r><t>Dün</t></r><r><t>ya</t></r></si></sst>')
            archive.writestr("xl/worksheets/sheet1.xml", (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                '<row r="2"><c r="A2"><v>42</v></c><c r="B2" t="inlineStr"><is><t>Satır</t></is></c></row>'
                '</sheetData></worksheet>'
            ))
        text = extract_file_context(str(path))
        self.assertIn("Sayfa1", text)
        self.assertIn("Merhaba", text)
        self.assertIn("Dünya", text)
        self.assertIn("42", text)

    def test_pptx_and_odt(self):
        pptx = self.root / "sunum.pptx"
        with zipfile.ZipFile(pptx, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Merhaba</a:t><a:t>Dünya</a:t></p:sld>')
        self.assertIn("Merhaba\nDünya", extract_file_context(str(pptx)))

        odt = self.root / "metin.odt"
        with zipfile.ZipFile(odt, "w") as archive:
            archive.writestr("content.xml", '<office:document-content xmlns:office="o" xmlns:text="t"><text:h>Başlık</text:h><text:p>Paragraf <text:span>metni</text:span></text:p></office:document-content>')
        text = extract_file_context(str(odt))
        self.assertIn("Başlık", text)
        self.assertIn("Paragraf metni", text)

    def test_epub_uses_spine_order_and_skips_nav(self):
        epub = self.root / "kitap.epub"
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            '</manifest><spine><itemref idref="ch2"/><itemref idref="ch1"/></spine></package>'
        )
        font_encryption = (
            '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<EncryptedData><CipherData><CipherReference URI="fonts/kitap.otf"/></CipherData>'
            '</EncryptedData></encryption>'
        )
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("META-INF/encryption.xml", font_encryption)
            archive.writestr("OEBPS/content.opf", opf)
            archive.writestr("OEBPS/ch1.xhtml", "<html><body><p>Birinci bölüm metni.</p></body></html>")
            archive.writestr("OEBPS/ch2.xhtml", "<html><body><h1>İkinci bölüm</h1><p>Devam metni.</p></body></html>")
            archive.writestr("OEBPS/nav.xhtml", "<html><body><p>İçindekiler</p></body></html>")

        text = extract_file_context(str(epub))
        self.assertIn("Birinci bölüm metni", text)
        self.assertIn("İkinci bölüm", text)
        self.assertLess(text.index("İkinci bölüm"), text.index("Birinci bölüm metni"))
        self.assertNotIn("İçindekiler", text)

    def test_encrypted_epub_is_rejected(self):
        epub = self.root / "drm.epub"
        encryption = (
            '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<EncryptedData><CipherData><CipherReference URI="OEBPS/ch1.xhtml"/></CipherData>'
            '</EncryptedData></encryption>'
        )
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("META-INF/encryption.xml", encryption)
            archive.writestr("OEBPS/ch1.xhtml", "<html><body><p>Gizli metin.</p></body></html>")
        text = extract_file_context(str(epub))
        self.assertIn("DRM/şifreli EPUB", text)
        self.assertNotIn("Gizli metin", text)

    def test_text_context_cache_hit_and_invalidation(self):
        from app.core import file_context

        path = self.root / "not.txt"
        path.write_text("Birinci içerik", encoding="utf-8")
        first = extract_file_context(str(path))
        self.assertIn("Birinci", first)

        key = str(path.resolve())
        with file_context._CACHE_LOCK:
            signature, _ = file_context._CONTEXT_CACHE[key]
            file_context._CONTEXT_CACHE[key] = (signature, "ÖNBELLEK")
        self.assertEqual(extract_file_context(str(path)), "ÖNBELLEK")

        path.write_text("İkinci içerik daha uzun", encoding="utf-8")
        second = extract_file_context(str(path))
        self.assertIn("İkinci", second)
        self.assertNotEqual(first, second)

    def test_missing_file_is_not_cached(self):
        from app.core import file_context

        path = self.root / "yok.txt"
        self.assertEqual(extract_file_context(str(path)), "Dosya bulunamadı.")
        self.assertNotIn(str(path.resolve()), file_context._CONTEXT_CACHE)


class SharedServiceTests(unittest.TestCase):
    def test_service_singletons_survive_between_calls(self):
        from app.core.github_tools import shared_github_tools
        from app.core.market_tools import shared_market_service
        from app.core.news_tools import shared_news_service
        from app.core.wiki_tools import shared_wiki_service

        self.assertIs(shared_news_service(), shared_news_service())
        self.assertIs(shared_market_service(), shared_market_service())
        self.assertIs(shared_wiki_service(), shared_wiki_service())
        self.assertIs(shared_github_tools(), shared_github_tools())


class WiringTests(unittest.TestCase):
    def test_classify_new_actions(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        policy = PathPolicy([root], [], base=root)
        for action in ("news", "markets", "wiki", "weather", "github"):
            self.assertEqual(classify_action(action, {}, policy), Risk.READ)
        self.assertEqual(classify_action("youtube", {"query": "kedi"}, policy), Risk.CREATE)
        self.assertEqual(classify_action("youtube", {"url": "https://youtu.be/x"}, policy), Risk.CREATE)
        with self.assertRaises(ActionForbidden):
            classify_action("youtube", {"url": "https://evil.test/x"}, policy)
        with self.assertRaises(ActionForbidden):
            classify_action("youtube", {}, policy)
        self.assertEqual(classify_action("memory", {"action": "list"}, policy), Risk.READ)
        self.assertEqual(classify_action("memory", {"action": "add"}, policy), Risk.CREATE)
        self.assertEqual(classify_action("memory", {"action": "delete"}, policy), Risk.MODIFY)
        self.assertEqual(classify_action("skill", {"action": "read"}, policy), Risk.READ)
        self.assertEqual(classify_action("skill", {"action": "create"}, policy), Risk.MODIFY)
        self.assertEqual(classify_action("skill", {"action": "create", "overwrite": True}, policy), Risk.MODIFY)

    def test_tool_declarations(self):
        declarations = [item for group in TOOL_DECLARATIONS for item in group.get("functionDeclarations", [])]
        names = {item["name"] for item in declarations}
        for name in ("news", "markets", "wiki", "weather", "youtube", "memory", "skill", "github"):
            self.assertIn(name, names)
        news = next(item for item in declarations if item["name"] == "news")
        self.assertEqual(news["parameters"]["properties"]["categories"]["type"], "ARRAY")
        self.assertIn("items", news["parameters"]["properties"]["categories"])
        self.assertIn("query", news["parameters"]["properties"])
        self.assertIn("sources", news["parameters"]["properties"])
        weather = next(item for item in declarations if item["name"] == "weather")
        self.assertIn("city", weather["parameters"]["properties"])
        screen = next(item for item in declarations if item["name"] == "screen_map")
        self.assertIn("fast", screen["parameters"]["properties"])
        wait = next(item for item in declarations if item["name"] == "screen_wait")
        self.assertIn("text", wait["parameters"]["properties"])
        self.assertIn("window_title", wait["parameters"]["properties"])
        github = next(item for item in declarations if item["name"] == "github")
        self.assertIn("releases", github["parameters"]["properties"]["action"]["enum"])
        self.assertIn("commits", github["parameters"]["properties"]["action"]["enum"])
        self.assertIn("commit", github["parameters"]["properties"]["action"]["enum"])
        self.assertIn("compare", github["parameters"]["properties"]["action"]["enum"])
        self.assertIn("issues", github["parameters"]["properties"]["action"]["enum"])
        self.assertIn("pulls", github["parameters"]["properties"]["action"]["enum"])
        self.assertIn("state", github["parameters"]["properties"])
        self.assertIn("labels", github["parameters"]["properties"])
        wiki = next(item for item in declarations if item["name"] == "wiki")
        self.assertIn("search", wiki["parameters"]["properties"]["action"]["enum"])
        self.assertIn("limit", wiki["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
