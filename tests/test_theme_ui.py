import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.settings import Settings
from app.theme.themes import BLACK, THEMES, WHITE
from app.ui.hud_theme import build_qss
from app.ui.settings_dialog import SettingsDialog

APP = QApplication.instance() or QApplication([])


class ThemeCssTests(unittest.TestCase):
    def test_all_palettes_build_complete_qss(self):
        dark = build_qss(BLACK)
        light = build_qss(WHITE)
        self.assertIn(BLACK["BACKGROUND"], dark)
        self.assertIn(WHITE["BACKGROUND"], light)
        self.assertIn("QTabBar::tab:selected", dark)
        self.assertIn("QTabBar::tab:selected", light)
        self.assertNotEqual(dark, light)

        for name, theme in THEMES.items():
            with self.subTest(theme=name):
                qss = build_qss(theme)
                self.assertIn(theme["BACKGROUND"], qss)
                self.assertIn(theme["RED"], qss)
                self.assertIn("QTabBar::tab:selected", qss)


class ThemeTabTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": self._tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.settings = Settings()

    def test_dialog_has_skill_tab_and_five_theme_cards(self):
        dialog = SettingsDialog(self.settings)
        self.addCleanup(dialog.deleteLater)

        labels = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
        self.assertEqual(
            labels, ["Bağlantı", "Ses & Kamera", "Güvenlik", "Eşlikçi", "Temalar", "Yetenekler"]
        )
        self.assertGreaterEqual(dialog.skill_list.count(), 4)
        self.assertEqual(
            set(dialog.theme_previews),
            {"black", "midnight", "ember", "forest", "white"},
        )
        self.assertEqual(dialog.selected_theme, "black")

    def test_saving_midnight_theme_persists_and_flags_restart(self):
        dialog = SettingsDialog(self.settings)
        self.addCleanup(dialog.deleteLater)
        dialog.api_key.setText("test-anahtar")

        dialog._select_theme("midnight")
        self.assertEqual(dialog.selected_theme, "midnight")
        self.assertIn("Gece", dialog.theme_readout.text())
        dialog.save()

        self.assertTrue(dialog.theme_changed)
        self.assertEqual(dialog.theme_name, "midnight")
        self.assertEqual(Settings().theme, "midnight")

    def test_saving_same_theme_does_not_request_restart(self):
        self.settings.set_theme("white")
        dialog = SettingsDialog(self.settings)
        self.addCleanup(dialog.deleteLater)
        dialog.api_key.setText("test-anahtar")

        dialog.save()

        self.assertFalse(dialog.theme_changed)


if __name__ == "__main__":
    unittest.main()
