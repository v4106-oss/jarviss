import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.ocr_tools import ScreenOCR, _normalize
from app.core.safety import ActionForbidden

LINES = [
    {
        "text": "Açıklama Tartışmalar",
        "words": [
            {"text": "Açıklama", "x": 478.0, "y": 317.0, "width": 61.0, "height": 14.0},
            {"text": "Tartışmalar", "x": 556.0, "y": 317.0, "width": 76.0, "height": 14.0},
        ],
    },
    {
        "text": "Abone Ol",
        "words": [
            {"text": "Abone", "x": 1000.0, "y": 860.0, "width": 50.0, "height": 14.0},
            {"text": "Ol", "x": 1055.0, "y": 860.0, "width": 20.0, "height": 14.0},
        ],
    },
]


class NormalizationTests(unittest.TestCase):
    def test_turkish_case_and_diacritics_are_normalized(self):
        self.assertEqual(_normalize("Tartışmalar"), "tartismalar")
        self.assertEqual(_normalize("AÇIKLAMA"), "aciklama")
        self.assertEqual(_normalize("  Abone   Ol "), "abone ol")


class MatchTests(unittest.TestCase):
    def test_word_match_returns_word_box_not_whole_line(self):
        matches = ScreenOCR._match(LINES, "Tartışmalar")
        self.assertTrue(matches)
        self.assertEqual(matches[0]["text"], "Tartışmalar")
        self.assertEqual(matches[0]["box"], [556, 317, 76, 14])
        self.assertEqual(matches[0]["score"], 1.0)

    def test_multi_word_match_unions_word_boxes(self):
        matches = ScreenOCR._match(LINES, "Abone Ol")
        self.assertTrue(matches)
        self.assertEqual(matches[0]["box"], [1000, 860, 75, 14])

    def test_missing_text_returns_no_match(self):
        self.assertEqual(ScreenOCR._match(LINES, "Kaydet"), [])


class FindCoordinateTests(unittest.TestCase):
    def test_ocr_image_coordinates_are_mapped_to_screen(self):
        async def fake_recognize(self, path, language):
            return LINES, "tr-TR"

        with patch.object(ScreenOCR, "_recognize", new=fake_recognize):
            result = ScreenOCR().find(Path("ekran.png"), "Tartışmalar", origin=(100, 50))
        self.assertEqual(result["language"], "tr-TR")
        self.assertEqual(result["best"]["center_x"], 694)
        self.assertEqual(result["best"]["center_y"], 374)
        self.assertIn("gerçek ekran", result["note"])

    def test_empty_query_is_refused(self):
        with self.assertRaises(ActionForbidden):
            ScreenOCR().find(Path("ekran.png"), "  ")


class EngineCacheTests(unittest.TestCase):
    def setUp(self):
        import app.core.ocr_tools as module

        self.module = module
        patcher = patch.dict(module._ENGINE_CACHE, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_engine_is_created_once_per_language(self):
        created = []

        def fake_create(language=""):
            created.append(language)
            return object(), "tr-TR"

        with patch.object(ScreenOCR, "_create_engine", staticmethod(fake_create)):
            first = ScreenOCR._engine("tr")
            second = ScreenOCR._engine("Türkçe")
            third = ScreenOCR._engine("en")

        self.assertIs(first[0], second[0])
        self.assertIsNot(first[0], third[0])
        self.assertEqual(created, ["tr", "en"])
        self.assertEqual(len(self.module._ENGINE_CACHE), 2)

    def test_refresh_replaces_cached_engine(self):
        engines = [object(), object()]
        created = []

        def fake_create(language=""):
            created.append(language)
            return engines[len(created) - 1], "tr-TR"

        with patch.object(ScreenOCR, "_create_engine", staticmethod(fake_create)):
            first = ScreenOCR._engine("tr")
            second = ScreenOCR._engine("tr", refresh=True)

        self.assertIs(first[0], engines[0])
        self.assertIs(second[0], engines[1])
        self.assertEqual(len(created), 2)
        self.assertIs(self.module._ENGINE_CACHE["tr-TR"][0], engines[1])


if __name__ == "__main__":
    unittest.main()
