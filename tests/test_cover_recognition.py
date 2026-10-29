import io
import json
import tempfile
import unittest
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from app.core.book_tools import (
    BookService,
    BookStore,
    cover_capture_signatures,
    cover_query_candidates,
    image_signature,
    parse_cover_vision,
    signature_similarity,
)

TARGET_ISBN = "9789750724435"
TARGET_URL = "https://covers.openlibrary.org/b/id/12273315-L.jpg"
OTHER_URL = "https://covers.openlibrary.org/b/id/999-L.jpg"
BBOX = [0.3, 0.15, 0.534375, 0.733333]


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


def target_cover():
    image = Image.new("RGB", (300, 420), (240, 232, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 300, 48), fill=(190, 35, 45))
    draw.rectangle((28, 80, 190, 96), fill=(25, 25, 30))
    draw.rectangle((28, 106, 150, 118), fill=(25, 25, 30))
    draw.ellipse((170, 170, 280, 280), fill=(226, 188, 90))
    draw.polygon([(60, 330), (120, 230), (180, 330)], fill=(40, 60, 80))
    draw.rectangle((0, 380, 300, 420), fill=(190, 35, 45))
    return image


def other_cover():
    image = Image.new("RGB", (300, 420), (18, 34, 66))
    draw = ImageDraw.Draw(image)
    for offset in range(-420, 300, 60):
        draw.line((offset, 0, offset + 420, 420), fill=(230, 230, 235), width=14)
    draw.rectangle((60, 120, 240, 300), fill=(230, 120, 30))
    return image


def image_bytes(image, fmt="PNG"):
    buffer = io.BytesIO()
    image.save(buffer, fmt, quality=90) if fmt == "JPEG" else image.save(buffer, fmt)
    return buffer.getvalue()


def camera_frame(path: Path) -> Path:
    frame = Image.new("RGB", (1280, 720), (78, 76, 72))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 520, 1280, 720), fill=(105, 88, 66))
    draw.ellipse((900, 80, 1120, 260), fill=(66, 92, 80))
    draw.rectangle((60, 420, 250, 520), fill=(54, 62, 82))
    frame.paste(target_cover(), (384, 108))
    frame.save(path, "JPEG", quality=92)
    return path


IDENTIFIED = {
    "title": "Küçük Prens",
    "authors": ["Antoine de Saint-Exupéry"],
    "publisher": "Can Çocuk",
    "translators": ["Cemal Süreya", "Tomris Uyar"],
    "confidence": 0.98,
    "bbox": BBOX,
}


def target_book():
    return {
        "id": "ol:OL35431855M",
        "isbn": TARGET_ISBN,
        "title": "Küçük Prens",
        "authors": ["Antoine de Saint-Exupéry"],
        "publisher": "Can Yayınları",
        "published": "Jan 03, 2017",
        "page_count": 112,
        "cover_url": TARGET_URL,
        "source": "Open Library",
    }


def other_book():
    return {
        "id": "ol:OTHER",
        "isbn": "9780140328721",
        "title": "Küçük Prens",
        "authors": ["Antoine de Saint-Exupéry"],
        "publisher": "Bilinmeyen Yayınevi",
        "cover_url": OTHER_URL,
        "source": "Open Library",
    }


def cover_router(url, kwargs):
    if url == TARGET_URL:
        return FakeResponse(status=200, content=image_bytes(target_cover()))
    if url == OTHER_URL:
        return FakeResponse(status=200, content=image_bytes(other_cover()))
    raise AssertionError(f"beklenmeyen URL: {url}")


def make_service(directory, router=cover_router):
    return BookService(
        settings=StubSettings(directory),
        store=BookStore(Path(directory) / "books.sqlite3"),
        session=FakeSession(router),
        cover_dir=Path(directory) / "covers",
        sleep=lambda _: None,
    )


class CoverVisionTests(unittest.TestCase):
    def test_parses_fenced_json_and_validates_fields(self):
        text = "```json\n" + json.dumps({
            "title": "Küçük Prens",
            "authors": ["Antoine de Saint-Exupéry"],
            "publisher": "Can Yayınları",
            "translators": "Cemal Süreya & Tomris Uyar",
            "isbn": "0-439-42089-X",
            "confidence": 98,
            "bbox": [0.05, 0.05, 0.95, 0.95],
        }, ensure_ascii=False) + "\n```"

        parsed = parse_cover_vision(text)

        self.assertEqual(parsed["title"], "Küçük Prens")
        self.assertEqual(parsed["translators"], ["Cemal Süreya", "Tomris Uyar"])
        self.assertEqual(parsed["isbn"], "9780439420891")
        self.assertAlmostEqual(parsed["confidence"], 0.98)
        self.assertEqual(parsed["bbox"], [0.05, 0.05, 0.95, 0.95])

    def test_full_or_tiny_bbox_is_rejected_and_trailing_comma_repaired(self):
        parsed = parse_cover_vision(
            '{"title":"Deneme","confidence":80,"bbox":[0,0,1,1],}'
        )
        self.assertEqual(parsed["title"], "Deneme")
        self.assertEqual(parsed["bbox"], [])
        self.assertAlmostEqual(parsed["confidence"], 0.8)

        tiny = parse_cover_vision('{"title":"X","bbox":[0.4,0.4,0.42,0.42]}')
        self.assertEqual(tiny["bbox"], [])


class CoverQueryTests(unittest.TestCase):
    def test_target_cover_produces_publisher_and_translator_queries(self):
        ocr = (
            "KÜÇÜK PRENS\nANTOINE DE SAINT-EXUPÉRY\n"
            "Cemal Süreya & Tomris Uyar\nCan Yayınları"
        )
        queries = cover_query_candidates(ocr, IDENTIFIED)

        self.assertEqual(queries[0], "Küçük Prens can")
        self.assertIn("Küçük Prens Can Çocuk", queries)
        self.assertIn("Küçük Prens Cemal Süreya Tomris Uyar", queries)
        self.assertIn("Küçük Prens Antoine de Saint-Exupéry", queries)
        self.assertIn("Cemal Süreya Tomris Uyar", queries)
        self.assertLessEqual(len(queries), 9)
        self.assertEqual(len(queries), len({query.casefold() for query in queries}))

    def test_isbn_is_searched_before_text_queries(self):
        queries = cover_query_candidates("", IDENTIFIED, ocr_isbn="975-07-2443-7")
        self.assertEqual(queries[0], TARGET_ISBN)


class SignatureTests(unittest.TestCase):
    def test_same_cover_scores_high_and_other_cover_scores_low(self):
        cover = target_cover()
        signature = image_signature(cover)
        recompressed = image_signature(image_bytes(cover, "JPEG"))

        self.assertGreater(signature_similarity(signature, recompressed), 0.95)
        self.assertLess(
            signature_similarity(signature, image_signature(other_cover())),
            0.75,
        )

    def test_bbox_crop_recovers_cover_from_cluttered_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = camera_frame(Path(directory) / "frame.jpg")
            signatures, used_bbox = cover_capture_signatures(frame, BBOX)

            self.assertTrue(used_bbox)
            self.assertEqual(len(signatures), 4)
            cover_signature = image_signature(target_cover())
            self.assertGreater(
                signature_similarity(cover_signature, signatures[1]), 0.95
            )
            self.assertLess(max(
                signature_similarity(cover_signature, signatures[0]),
                signature_similarity(image_signature(other_cover()), signatures[1]),
            ), 0.95)


class CoverMatchTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name)
        self.frame = camera_frame(self.directory / "frame.jpg")
        self.service = make_service(self.directory)

    def test_visual_match_chooses_correct_edition(self):
        result = self.service.match_cover(
            self.frame,
            [other_book(), target_book()],
            identified=IDENTIFIED,
        )

        self.assertTrue(result["match"]["accepted"])
        self.assertGreaterEqual(result["match"]["similarity"], 0.80)
        self.assertEqual(result["book"]["isbn"], TARGET_ISBN)
        self.assertEqual(result["candidates"][0]["isbn"], TARGET_ISBN)
        self.assertTrue(result["candidates"][0]["match_accepted"])
        self.assertFalse(result["candidates"][1]["match_accepted"])

    def test_identify_cover_uses_queries_attaches_translators_and_saves(self):
        seen = {}

        def fake_search(queries, limit=8, identified=None, ocr_text=""):
            seen["queries"] = list(queries)
            return [other_book(), target_book()]

        self.service.cover_search = fake_search
        result = self.service.identify_cover(self.frame, identified=IDENTIFIED)

        self.assertIn("Küçük Prens can", seen["queries"])
        self.assertIn("Küçük Prens Cemal Süreya Tomris Uyar", seen["queries"])
        self.assertTrue(result["match"]["accepted"])
        self.assertEqual(result["book"]["isbn"], TARGET_ISBN)
        self.assertEqual(
            result["book"]["translators"], ["Cemal Süreya", "Tomris Uyar"]
        )
        stored = self.service.store.find_by_isbn(TARGET_ISBN)
        self.assertEqual(stored["translators"], ["Cemal Süreya", "Tomris Uyar"])
        self.assertTrue(Path(stored["cover_path"]).is_file())


class CoverSearchTests(unittest.TestCase):
    def test_search_expands_editions_and_merges_translators(self):
        def router(url, kwargs):
            if "openlibrary.org/search.json" in url:
                self.assertEqual(kwargs["params"]["q"], "Küçük Prens can")
                return FakeResponse({"docs": [{
                    "key": "/works/OL26220771W",
                    "title": "Küçük Prens",
                    "author_name": ["Antoine de Saint-Exupéry"],
                    "isbn": [TARGET_ISBN],
                    "cover_i": 12273315,
                }]})
            if "openlibrary.org/works/OL26220771W/editions.json" in url:
                return FakeResponse({"entries": [{
                    "key": "/books/OL35431855M",
                    "title": "Küçük Prens",
                    "isbn_13": [TARGET_ISBN],
                    "publishers": ["Can Yayınları"],
                    "publish_date": "Jan 03, 2017",
                    "number_of_pages": 112,
                    "covers": [12273315],
                    "contributors": [
                        {"role": "Translator", "name": "Cemal Süreya"},
                        {"role": "Translator", "name": "Tomris Uyar"},
                    ],
                }]})
            if "googleapis.com" in url:
                return FakeResponse({"items": []})
            raise AssertionError(f"beklenmeyen URL: {url}")

        with tempfile.TemporaryDirectory() as directory:
            service = make_service(directory, router)
            results = service.cover_search(["Küçük Prens can"], identified=IDENTIFIED)

        self.assertEqual(len(results), 1)
        book = results[0]
        self.assertEqual(book["isbn"], TARGET_ISBN)
        self.assertEqual(book["translators"], ["Cemal Süreya", "Tomris Uyar"])
        self.assertEqual(book["publisher"], "Can Yayınları")
        self.assertEqual(book["page_count"], 112)
        self.assertTrue(book["cover_url"].endswith("12273315-L.jpg"))


class TranslatorStoreTests(unittest.TestCase):
    def test_translators_round_trip_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BookStore(Path(directory) / "books.sqlite3")
            store.save_book({
                "id": TARGET_ISBN,
                "isbn": TARGET_ISBN,
                "title": "Küçük Prens",
                "authors": ["Antoine de Saint-Exupéry"],
                "translators": ["Cemal Süreya", "Tomris Uyar"],
                "source": "Open Library",
            })
            stored = store.find_by_isbn(TARGET_ISBN)
            self.assertEqual(stored["translators"], ["Cemal Süreya", "Tomris Uyar"])
            self.assertEqual(store.find_books("Süreya")[0]["isbn"], TARGET_ISBN)


if __name__ == "__main__":
    unittest.main()
