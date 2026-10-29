import os
import json
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication, QWidget

from app.core.book_tools import BookStore, extract_isbn
from app.core.ocr_tools import ScreenOCR
from app.ui.book_modal import BookModal

APP = QApplication.instance() or QApplication([])
ISBN13 = "9789750718533"


class Settings:
    provider = "gemini"
    model = "gemini-test"
    ollama_model = "qwen-test"


class FakeAI:
    def __init__(self, text="1) Kısa yönelim: deneme kitabı.\n2) Ana temalar: bellek, kimlik."):
        self.text = text
        self.calls = []
        self.cancelled = False

    def ask(self, prompt, system_prompt=None, record_history=True, **kwargs):
        self.calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "record_history": record_history,
        })
        return self.text

    def cancel(self):
        self.cancelled = True


class FakeService:
    def __init__(self, book):
        self.book = book
        self.queries = []
        self.cover_calls = []

    def lookup_isbn(self, isbn):
        self.queries.append(isbn)
        return dict(self.book)

    def search(self, query, limit=8):
        self.queries.append(("search", query))
        return [dict(self.book)]

    def prepare(self, book):
        return dict(book)

    def identify_cover(self, image_path, ocr_text="", ocr_isbn="", identified=None, limit=8):
        self.cover_calls.append({
            "ocr_text": ocr_text,
            "ocr_isbn": ocr_isbn,
            "identified": dict(identified or {}),
        })
        book = self.lookup_isbn(ocr_isbn) if ocr_isbn else dict(self.book)
        translators = list((identified or {}).get("translators") or [])
        if translators and not book.get("translators"):
            book["translators"] = translators
        return {
            "book": book,
            "candidates": [book],
            "match": {
                "similarity": 0.93,
                "metadata": 0.88,
                "score": 0.92,
                "accepted": True,
                "reason": "Test görsel eşleşmesi.",
            },
            "queries": [ocr_isbn or "test"],
            "identified": dict(identified or {}),
        }


def draw_text_page(path: Path, lines, size=(1600, 1100), font_size=48):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    y = 80
    for line in lines:
        draw.text((80, y), line, fill="black", font=font)
        y += int(font_size * 2)
    image.save(path)


def wait_until(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    APP.processEvents()
    return bool(predicate())


class BookModalTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name)
        self.store = BookStore(self.directory / "books.sqlite3")
        self.book = {
            "id": ISBN13,
            "isbn": ISBN13,
            "title": "1984",
            "authors": ["George Orwell"],
            "publisher": "Can Yayınları",
            "published": "2020",
            "page_count": 352,
            "categories": ["Distopya"],
            "description": "Distopik bir roman.",
            "toc": ["Birinci Bölüm · s. 1", "İkinci Bölüm · s. 37"],
            "source": "Open Library",
        }
        self.store.save_book(self.book)
        self.service = FakeService(self.book)
        self.ai = FakeAI()
        self.parent = QWidget()
        self.parent.resize(1600, 960)
        self.parent.show()
        self.modal = BookModal(
            self.parent,
            Settings(),
            storage=object(),
            auto_start=False,
            service=self.service,
            store=self.store,
            ai=self.ai,
        )
        self.modal.show()
        APP.processEvents()

    def tearDown(self):
        try:
            self.modal.close_modal()
        except RuntimeError:
            pass
        self.parent.close()
        APP.processEvents()

    def test_modal_starts_without_camera_and_shows_privacy_note(self):
        self.assertFalse(self.modal.camera.is_active())
        self.assertFalse(self.modal.profile_button.isEnabled())
        labels = " ".join(
            label.text() for label in self.modal.findChildren(type(self.modal.title_label))
        )
        self.assertIn("bellekte", labels.casefold())

    def test_page_upsert_and_clear_keep_text_only_in_memory(self):
        self.modal._upsert_page(4, "Dördüncü sayfanın geçici OCR metni.")
        self.modal._upsert_page(2, "İkinci sayfanın geçici OCR metni.")
        self.assertEqual([page["number"] for page in self.modal._pages], [2, 4])
        self.assertIn("TARANAN SAYFALAR", self.modal.pages_label.text())

        self.modal._clear_pages()
        self.assertEqual(self.modal._pages, [])
        self.assertEqual(self.store.stats()["profiles"], 0)

    def test_summary_prompt_uses_pages_and_persists_only_summary_and_refs(self):
        self.modal._apply_book(self.book)
        self.modal._upsert_page(1, "Bu sayfada anlatıcı geçmişini hatırlıyor ve Parti'yi sorguluyor.")
        self.modal._upsert_page(2, "İkinci sayfada Büyük Birader'in gözetimi anlatılıyor.")
        self.modal._generate_summary("pages")

        self.assertTrue(wait_until(lambda: bool(self.modal.summary_edit.toPlainText().strip())))
        self.assertTrue(wait_until(lambda: self.store.stats()["profiles"] == 1))

        call = self.ai.calls[0]
        self.assertFalse(call["record_history"])
        self.assertIn("Sayfa 1", call["prompt"])
        self.assertIn("Parti'yi sorguluyor", call["prompt"])
        self.assertIn("Tam metni kopyalama", call["prompt"])

        profile = self.store.latest_profile(ISBN13)
        self.assertEqual(profile["page_refs"], ["1", "2"])
        self.assertIn("Kısa yönelim", profile["summary"])

        with self.store._db() as connection:
            rows = connection.execute("SELECT * FROM profiles").fetchall()
        raw = " ".join(str(value) for row in rows for value in tuple(row))
        self.assertNotIn("Parti'yi sorguluyor", raw)
        self.assertNotIn("Büyük Birader'in gözetimi", raw)

    def test_closing_modal_clears_pages_and_summary(self):
        self.modal._apply_book(self.book)
        self.modal._upsert_page(1, "Kapatınca silinecek geçici sayfa metni.")
        self.modal.summary_edit.setPlainText("Geçici özet.")
        self.modal.close_modal()
        APP.processEvents()
        self.assertEqual(self.modal._pages, [])
        self.assertEqual(self.modal.summary_edit.toPlainText(), "")

    def test_library_lists_saved_profiles(self):
        self.store.save_profile(ISBN13, "profile", "Kayıtlı profil özeti.", page_refs=[])
        self.modal._load_library()
        self.assertGreater(self.modal.library_combo.count(), 1)
        self.assertIn("1984", self.modal.library_combo.itemText(1))
        self.modal.library_combo.setCurrentIndex(1)
        APP.processEvents()
        self.assertEqual(self.modal.summary_edit.toPlainText(), "Kayıtlı profil özeti.")
        self.assertEqual(self.modal.title_label.text(), "1984")

    def test_cover_recognition_uses_vision_metadata_and_visual_match(self):
        self.ai.text = json.dumps({
            "title": "Küçük Prens",
            "authors": ["Antoine de Saint-Exupéry"],
            "publisher": "Can Çocuk",
            "translators": ["Cemal Süreya", "Tomris Uyar"],
            "isbn": "",
            "confidence": 0.98,
            "bbox": [0.2, 0.1, 0.8, 0.9],
        }, ensure_ascii=False)
        image_path = self.directory / "cover.jpg"
        Image.new("RGB", (300, 420), (120, 70, 40)).save(image_path, "JPEG")

        self.modal._recognize_cover(image_path, "", "")

        self.assertTrue(wait_until(lambda: self.modal.book is not None, timeout=10))
        call = self.service.cover_calls[0]
        self.assertEqual(call["identified"]["title"], "Küçük Prens")
        self.assertEqual(call["identified"]["translators"], ["Cemal Süreya", "Tomris Uyar"])
        self.assertIn("görsel %93", self.modal.status_label.text())
        self.assertIn("çeviri:", self.modal.status_label.text())
        self.assertFalse(self.modal._cover_busy)
        self.assertTrue(self.modal.cover_button.isEnabled())
        self.assertTrue(image_path.is_file())


def _ocr_image(lines, path):
    draw_text_page(path, lines)
    return path


@unittest.skipUnless(ScreenOCR.available(), "Windows OCR motoru yok")
class BookOcrModalTests(BookModalTests):
    def test_cover_ocr_extracts_isbn_and_loads_metadata(self):
        image_path = _ocr_image(
            ["BIN DOKUZ YUZ SEKSEN DORT", "George Orwell", "ISBN 978-975-07-1853-3"],
            self.directory / "cover.png",
        )
        self.modal._start_ocr(image_path, "cover")

        self.assertTrue(wait_until(lambda: self.modal.book is not None, timeout=25))
        self.assertEqual(self.service.queries[0], ISBN13)
        self.assertEqual(self.service.cover_calls[0]["ocr_isbn"], ISBN13)
        self.assertIn("978-975-07-1853-3", self.service.cover_calls[0]["ocr_text"])
        self.assertEqual(self.modal.title_label.text(), "1984")
        self.assertEqual(self.modal.authors_label.text(), "George Orwell")
        self.assertTrue(image_path.is_file())
        self.assertFalse(self.modal._ocr_busy)

    def test_page_ocr_adds_page_and_next_number(self):
        image_path = _ocr_image(
            [
                "Birinci bolum",
                "Savas baristir, ozgurluk kolelestirir,",
                "cahillik mutluluk getirir.",
            ],
            self.directory / "page.png",
        )
        self.modal.page_spin.setValue(7)
        self.modal._start_ocr(image_path, "page")

        self.assertTrue(wait_until(lambda: bool(self.modal._pages), timeout=25))
        self.assertEqual(self.modal._pages[0]["number"], 7)
        self.assertIn("Savas", self.modal._pages[0]["text"])
        self.assertEqual(self.modal.page_spin.value(), 8)
        self.assertTrue(image_path.is_file())
        self.assertEqual(self.store.stats()["profiles"], 0)


if __name__ == "__main__":
    unittest.main()
