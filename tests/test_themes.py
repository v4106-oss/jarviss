import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.settings import Settings
from app.theme.themes import (
    BLACK,
    EMBER,
    FOREST,
    MIDNIGHT,
    THEMES,
    WHITE,
    normalize_theme,
    palette,
    rgba_css,
)


class ThemePaletteTests(unittest.TestCase):
    def test_normalize_theme(self):
        self.assertEqual(normalize_theme("white"), "white")
        self.assertEqual(normalize_theme(" WHITE "), "white")
        self.assertEqual(normalize_theme("black"), "black")
        self.assertEqual(normalize_theme("mavi"), "midnight")
        self.assertEqual(normalize_theme("zümrüt"), "forest")
        self.assertEqual(normalize_theme("mor"), "black")
        self.assertEqual(normalize_theme(None), "black")

    def test_white_palette_is_light_and_black_is_dark(self):
        self.assertTrue(WHITE["IS_LIGHT"])
        self.assertFalse(BLACK["IS_LIGHT"])
        self.assertEqual(set(THEMES), {"black", "midnight", "ember", "forest", "white"})
        self.assertEqual(palette("white")["BACKGROUND"], WHITE["BACKGROUND"])

    def test_all_palettes_share_the_same_keys(self):
        expected = set(BLACK)
        for name, theme in THEMES.items():
            with self.subTest(theme=name):
                self.assertEqual(set(theme), expected)
                self.assertTrue(theme["NAME"])
                self.assertTrue(theme["LABEL"])
        self.assertFalse(MIDNIGHT["IS_LIGHT"])
        self.assertFalse(EMBER["IS_LIGHT"])
        self.assertFalse(FOREST["IS_LIGHT"])

    def test_rgba_css_supports_hex_and_invalid_values(self):
        self.assertEqual(rgba_css("#ff0000", 0.5), "rgba(255, 0, 0, 0.5)")
        self.assertEqual(rgba_css("bozuk", 0.5), "rgba(232, 57, 79, 0.5)")


class SettingsThemeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.local = Path(self._tmp.name)
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_theme_roundtrip_and_malformed_value(self):
        settings = Settings()
        self.assertEqual(settings.theme, "black")

        settings.set_theme("white")
        self.assertEqual(settings.theme, "white")
        self.assertEqual(Settings().theme, "white")

        data = json.loads((self.local / "Jarvis" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "white")

        settings.set_theme("mor")
        self.assertEqual(settings.theme, "black")

    def test_saved_theme_name_reads_file_and_env_override(self):
        from app.theme.themes import saved_theme_name

        settings = Settings()
        settings.set_theme("white")
        self.assertEqual(saved_theme_name(), "white")
        with patch.dict(os.environ, {"JARVIS_THEME": "black"}):
            self.assertEqual(saved_theme_name(), "black")


if __name__ == "__main__":
    unittest.main()
