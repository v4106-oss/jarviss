import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image

from app.core.ai import AI, TOOL_DECLARATIONS
from app.core.book_tools import (
    MAX_TOTAL_PAGE_CHARS,
    BookError,
    BookService,
    BookStore,
    BookTools,
    build_summary_prompt,
    clean_ocr_text,
    extract_isbn,
    extract_isbns,
    isbn10_valid,
    isbn10_to_13,
    isbn13_valid,
    normalize_isbn,
    prepare_for_ocr,
)
from app.core.safety import ActionForbidden, PathPolicy, Risk, classify_action

ISBN13 = "9789750718533"
ISBN10 = "043942089X"


class FakeResponse:
    def __init__(self, payload=None, status=200, content=b""):
        self._payload = payload
        self.status_code = status
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            raise ValueError("json yok")
        return self._payload


class FakeSession:
    def __init__(self, router):
        self.router = router
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.router(url, kwargs)
        if isinstance(result, Exception):
            raise result
        return result


class StubSettings:
    def __init__(self, data_dir):
        self.data_dir = str(data_dir)


def jpeg_bytes(size=(60, 90), color=(120, 30, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


def openlibrary_payload(isbn=ISBN13, cover_url="https://covers.openlibrary.org/b/id/42-L.jpg"):
    return {
        f"ISBN:{isbn}": {
            "title": "Bin Dokuz Yüz Seksen Dört",
            "authors": [{"name": "George Orwell"}],
            "publishers": [{"name": "Can Yayınları"}],
            "publish_date": "2020",
            "number_of_pages": 352,
            "cover": {"large": cover_url} if cover_url else {},
            "details": {
                "works": [{"key": "/works/OL123W"}],
                "table_of_contents": [
                    {"title": "Birinci Bölüm", "pagenum": "1"},
                    {"title": "İkinci Bölüm", "pagenum": "37"},
                ],
                "languages": [{"key": "/languages/tur"}],
            },
            "url": "https://openlibrary.org/books/OL1M/Bin_Dokuz_Yuz_SekSen_Dort",
        }
    }


def service_with(router, directory, store=None):
    session = FakeSession(router)
    store = store or BookStore(Path(directory) / "books.sqlite3")
    return (
        BookService(
            settings=StubSettings(directory),
            store=store,
            session=session,
            cover_dir=Path(directory) / "covers",
            sleep=lambda _: None,
        ),
        session,
        store,
    )


class IsbnTests(unittest.TestCase):
    def test_valid_isbn13_and_isbn10(self):
        self.assertTrue(isbn13_valid(ISBN13))
        self.assertTrue(isbn10_valid(ISBN10))
        self.assertTrue(isbn10_valid("0-8044-2957-X"))
        self.assertFalse(isbn13_valid("9789750718534"))
        self.assertFalse(isbn10_valid("0439420890"))

    def test_isbn10_converts_to_canonical_isbn13(self):
        self.assertEqual(isbn10_to_13(ISBN10), "9780439420891")
        self.assertEqual(normalize_isbn("0-439-42089-X"), "9780439420891")
        self.assertEqual(normalize_isbn("978 975 07 1853 3"), ISBN13)
        self.assertEqual(normalize_isbn("9789750718534"), "")

    def test_extracts_isbn_from_ocr_text_and_corrects_confusions(self):
        self.assertEqual(extract_isbn("ISBN: 978-975-07-1853-3"), ISBN13)
        self.assertEqual(extract_isbn("ISBN 978-975-O7-1853-3"), ISBN13)
        self.assertEqual(extract_isbn("978-O14-O32872-I"), "9780140328721")
        self.assertEqual(extract_isbns("9789750718533 ve 9780140328721"),
                         [ISBN13, "9780140328721"])

    def test_phone_numbers_and_random_digits_are_not_isbns(self):
        self.assertEqual(extract_isbns("Telefon: 0555 123 45 67"), [])
        self.assertEqual(extract_isbns("sipariş no 1234567890123"), [])


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = self._temp.name

    def router(self, url, kwargs):
        if "openlibrary.org/api/books" in url:
            return FakeResponse(openlibrary_payload())
        if "openlibrary.org/works/" in url:
            return FakeResponse({
                "description": {"value": "Büyük Birader gözetimindedir."},
                "subjects": ["Distopya", "Siyaset"],
            })
        if "covers.openlibrary.org" in url:
            return FakeResponse(status=200, content=jpeg_bytes())
        if "googleapis.com" in url:
            return FakeResponse({"items": []})
        raise AssertionError(f"beklenmeyen URL: {url}")

    def test_openlibrary_lookup_parses_metadata_toc_and_caches_cover(self):
        service, session, store = service_with(self.router, self.directory)

        book = service.lookup_isbn("978-975-07-1853-3")

        self.assertEqual(book["title"], "Bin Dokuz Yüz Seksen Dört")
        self.assertEqual(book["authors"], ["George Orwell"])
        self.assertEqual(book["publisher"], "Can Yayınları")
        self.assertEqual(book["page_count"], 352)
        self.assertEqual(book["language"], "tur")
        self.assertEqual(book["toc"], ["Birinci Bölüm · s. 1", "İkinci Bölüm · s. 37"])
        self.assertIn("Büyük Birader", book["description"])
        self.assertIn("Distopya", book["categories"])
        self.assertEqual(book["source"], "Open Library")
        self.assertTrue(Path(book["cover_path"]).is_file())
        stored = store.find_by_isbn(ISBN13)
        self.assertEqual(stored["title"], book["title"])
        self.assertEqual(stored["toc"], book["toc"])
        self.assertTrue(any("openlibrary.org/api/books" in url for url, _ in session.calls))

    def test_google_books_is_used_when_openlibrary_has_no_record(self):
        def router(url, kwargs):
            if "openlibrary.org/api/books" in url:
                return FakeResponse({})
            if "googleapis.com" in url:
                return FakeResponse({"items": [{"volumeInfo": {
                    "title": "Fantastic Mr. Fox",
                    "authors": ["Roald Dahl"],
                    "publisher": "Puffin",
                    "publishedDate": "1970",
                    "pageCount": 96,
                    "language": "en",
                    "industryIdentifiers": [{"type": "ISBN_13", "identifier": ISBN13}],
                }}]})
            raise AssertionError(f"beklenmeyen URL: {url}")

        service, _, _ = service_with(router, self.directory)
        book = service.lookup_isbn(ISBN13)

        self.assertEqual(book["title"], "Fantastic Mr. Fox")
        self.assertEqual(book["source"], "Google Books")
        self.assertEqual(book["page_count"], 96)

    def test_cached_metadata_is_returned_when_services_are_down(self):
        store = BookStore(Path(self.directory) / "books.sqlite3")
        store.save_book({
            "id": ISBN13,
            "isbn": ISBN13,
            "title": "Önbellekteki Kitap",
            "authors": ["Yazar"],
            "source": "Open Library",
        })

        def router(url, kwargs):
            raise requests.ConnectionError("ağ yok")

        service, _, _ = service_with(router, self.directory, store=store)
        book = service.lookup_isbn(ISBN13)

        self.assertEqual(book["title"], "Önbellekteki Kitap")
        self.assertIn("önbellek", book["source"].casefold())

    def test_search_merges_and_deduplicates_providers(self):
        def router(url, kwargs):
            if "openlibrary.org/search.json" in url:
                return FakeResponse({"docs": [{
                    "key": "/works/OL9W",
                    "title": "Kitap",
                    "author_name": ["A"],
                    "isbn": ["9789750718533"],
                    "first_publish_year": 2000,
                    "subject": ["Deneme"],
                }]})
            if "googleapis.com" in url:
                return FakeResponse({"items": [{"volumeInfo": {
                    "title": "Kitap",
                    "authors": ["A"],
                    "industryIdentifiers": [{"identifier": "9789750718533"}],
                }}]})
            raise AssertionError(f"beklenmeyen URL: {url}")

        service, _, _ = service_with(router, self.directory)
        results = service.search("Kitap", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Kitap")
        self.assertEqual(results[0]["isbn"], ISBN13)


class StorePrivacyTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = BookStore(Path(self._temp.name) / "books.sqlite3")

    def columns(self, table):
        with self.store._db() as connection:
            return [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]

    def test_schema_has_no_ocr_or_full_text_columns(self):
        book_columns = set(self.columns("books"))
        profile_columns = set(self.columns("profiles"))
        forbidden = {"ocr", "ocr_text", "full_text", "page_text", "text_content", "content"}
        self.assertFalse(book_columns & forbidden)
        self.assertFalse(profile_columns & forbidden)
        self.assertIn("summary", profile_columns)
        self.assertIn("page_refs", profile_columns)

    def test_book_and_profile_round_trip_without_page_text(self):
        saved = self.store.save_book({
            "id": "b1",
            "isbn": ISBN13,
            "title": "Deneme",
            "authors": ["Yazar"],
            "toc": ["Giriş · s. 1"],
            "source": "Open Library",
        })
        self.assertEqual(saved["id"], "b1")
        profile_id = self.store.save_profile(
            "b1", "pages", "Kişisel özet metni.", page_refs=[12, 13], model="test"
        )
        self.assertGreater(profile_id, 0)

        profile = self.store.latest_profile("b1")
        self.assertEqual(profile["summary"], "Kişisel özet metni.")
        self.assertEqual(profile["page_refs"], ["12", "13"])
        self.assertEqual(profile["book_title"], "Deneme")

        with self.store._db() as connection:
            raw = connection.execute("SELECT * FROM profiles").fetchone()
        values = " ".join(str(value) for value in tuple(raw))
        self.assertNotIn("Taranan sayfanın tam OCR metni", values)
        self.assertEqual(self.store.stats()["books"], 1)


class SummaryPromptTests(unittest.TestCase):
    def book(self):
        return {
            "title": "Kitap",
            "authors": ["Yazar"],
            "publisher": "Yayınevi",
            "published": "2020",
            "isbn": ISBN13,
            "page_count": 100,
            "categories": ["Deneme"],
            "toc": [f"Bölüm {index}" for index in range(1, 30)],
            "description": "Kısa açıklama.",
        }

    def test_prompt_contains_page_references_and_no_copy_instruction(self):
        prompt = build_summary_prompt(
            self.book(),
            [{"number": 12, "text": "Sayfa on iki metni."}, {"number": 13, "text": "Sayfa on üç."}],
        )
        self.assertIn("--- Sayfa 12 ---", prompt)
        self.assertIn("[s. N]", prompt)
        self.assertIn("Tam metni kopyalama", prompt)
        self.assertIn("uydurma", prompt.casefold())
        self.assertIn("Sayfa 13", prompt)

    def test_prompt_caps_pages_and_toc(self):
        pages = [{"number": index, "text": "kısa metin"} for index in range(1, 26)]
        prompt = build_summary_prompt(self.book(), pages)

        self.assertIn("--- Sayfa 20 ---", prompt)
        self.assertNotIn("--- Sayfa 21 ---", prompt)
        self.assertEqual(prompt.count("Bölüm "), 20)

    def test_prompt_limits_total_page_characters(self):
        pages = [{"number": index, "text": "x" * 6000} for index in range(1, 21)]
        prompt = build_summary_prompt(self.book(), pages)
        scanned = prompt.split("Taranan sayfalar", 1)[1]
        self.assertLessEqual(scanned.count("x"), MAX_TOTAL_PAGE_CHARS + 2000)
        self.assertIn("--- Sayfa 1 ---", prompt)
        self.assertNotIn("--- Sayfa 20 ---", prompt)

    def test_profile_mode_and_clean_ocr_text(self):
        prompt = build_summary_prompt(self.book(), [], mode="profile")
        self.assertIn("kitap profili istiyor", prompt)
        self.assertIn("Taranan sayfa yok.", prompt)
        self.assertEqual(
            clean_ocr_text("  satır  bir \n\nsatır  bir\nsatır iki "),
            "satır bir\nsatır iki",
        )

    def test_prepare_for_ocr_produces_contrasted_png(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "page.jpg"
            Image.new("RGB", (300, 420), (180, 180, 180)).save(source, "JPEG")
            target = Path(directory) / "page.png"

            result = prepare_for_ocr(source, target)

            self.assertEqual(result, target)
            with Image.open(target) as image:
                self.assertEqual(image.mode, "L")
                self.assertGreaterEqual(image.width, 1400)


class BookToolsTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = BookStore(Path(self._temp.name) / "books.sqlite3")
        self.book = {
            "id": ISBN13,
            "isbn": ISBN13,
            "title": "Bin Dokuz Yüz Seksen Dört",
            "authors": ["George Orwell"],
            "publisher": "Can Yayınları",
            "published": "2020",
            "page_count": 352,
            "categories": ["Distopya"],
            "toc": ["Birinci Bölüm · s. 1"],
            "description": "Distopik bir roman.",
            "source": "Open Library",
        }
        self.store.save_book(self.book)

    def fake_service(self):
        book = dict(self.book)

        class Service:
            store = self.store

            def lookup_isbn(self, isbn):
                return dict(book)

            def search(self, query, limit=6):
                return [dict(book)]

        return Service()

    def tools(self, listener=None):
        return BookTools(service=self.fake_service(), store=self.store, listener=listener)

    def test_open_camera_notifies_ui(self):
        events = []
        result = self.tools(listener=events.append).run({"action": "open_camera"})
        self.assertEqual(events, [{"kind": "camera"}])
        self.assertIn("Kamera", result)
        self.assertIn("Tam metin kalıcı kaydedilmez", result)

    def test_lookup_by_isbn_formats_metadata_and_latest_profile(self):
        self.store.save_profile(ISBN13, "profile", "Kısa profil.", page_refs=[])
        result = self.tools().run({"action": "lookup", "query": ISBN13})

        self.assertIn("Bin Dokuz Yüz Seksen Dört", result)
        self.assertIn("George Orwell", result)
        self.assertIn("Can Yayınları", result)
        self.assertIn("Birinci Bölüm", result)
        self.assertIn("Kısa profil.", result)

    def test_search_lists_candidates(self):
        result = self.tools().run({"action": "lookup", "query": "Orwell"})
        self.assertIn("kitap adayı", result)
        self.assertIn("Bin Dokuz Yüz Seksen Dört", result)

    def test_library_and_profile_use_local_store(self):
        tools = self.tools()
        self.assertIn("Kayıtlı kitap özeti yok", tools.run({"action": "library"}))
        self.store.save_profile(ISBN13, "pages", "Sayfa özeti.", page_refs=[5])

        library = tools.run({"action": "library"})
        self.assertIn("sayfa özeti", library)
        self.assertIn("Bin Dokuz Yüz Seksen Dört", library)

        profile = tools.run({"action": "profile", "isbn": ISBN13})
        self.assertIn("Sayfa özeti.", profile)
        self.assertIn("Sayfa referansları: 5", profile)

    def test_invalid_action_is_refused(self):
        with self.assertRaises(ActionForbidden):
            self.tools().run({"action": "download_full_text"})
        with self.assertRaises(BookError):
            self.tools().run({"action": "lookup", "query": "  "})

    def test_book_risk_classification(self):
        policy = PathPolicy([Path(self._temp.name)])
        self.assertEqual(classify_action("book", {"action": "open_camera"}, policy), Risk.READ)
        self.assertEqual(classify_action("book", {"action": "lookup", "query": ISBN13}, policy), Risk.READ)
        with self.assertRaises(ActionForbidden):
            classify_action("book", {"action": "delete_library"}, policy)


class ThemeSmokeTests(unittest.TestCase):
    def test_book_tool_is_declared_for_both_providers(self):
        declarations = {
            item["name"]: item
            for group in TOOL_DECLARATIONS
            for item in group["functionDeclarations"]
        }
        book = declarations["book"]
        self.assertEqual(
            book["parameters"]["properties"]["action"]["enum"],
            ["open_camera", "lookup", "library", "profile"],
        )
        self.assertIn("tam metin", book["description"].casefold())


class AIHistoryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)

    class Storage:
        def __init__(self):
            self.messages = []

        def add_message(self, role, content, source=None):
            self.messages.append((role, content))

        def get_all_memory(self):
            return []

        def get_conversation_history(self, limit=16):
            return []

    class Settings:
        api_key = "AQ.test"
        model = "gemini-1.5-flash"
        provider = "gemini"
        ollama_url = "http://localhost:11434"
        ollama_model = "qwen2.5-coder:3b"
        access_mode = "approval"

        def set_model(self, model):
            self.model = model

    def test_record_history_false_keeps_ocr_prompt_out_of_storage(self):
        storage = self.Storage()
        ai = AI(storage=storage, settings=self.Settings())
        response = type("R", (), {
            "ok": True,
            "status_code": 200,
            "text": "",
            "json": lambda self: {"candidates": [{"content": {"parts": [{"text": "Özet"}]}}]},
        })()
        with patch("app.core.ai.SESSION.post", return_value=response) as post:
            result = ai.ask("GİZLİ OCR METNİ", record_history=False)

        self.assertEqual(result, "Özet")
        self.assertEqual(storage.messages, [])
        sent = json.dumps(post.call_args.kwargs["json"], ensure_ascii=False)
        self.assertIn("GİZLİ OCR METNİ", sent)


if __name__ == "__main__":
    unittest.main()
