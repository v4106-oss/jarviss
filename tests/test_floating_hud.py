import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from app.core.settings import Settings
from app.ui.floating_hud import (
    MAX_SUBTITLE,
    STATE_INFO,
    FloatingHud,
)
from app.ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


class MemorySettings:
    def __init__(self, position=None):
        self.position = position
        self.saved = []

    @property
    def floating_hud_position(self):
        return self.position

    def save_floating_hud_position(self, x, y):
        self.position = (int(x), int(y))
        self.saved.append(self.position)


class FloatingHudTests(unittest.TestCase):
    def setUp(self):
        self.settings = MemorySettings()
        self.hud = FloatingHud(self.settings)

    def tearDown(self):
        self.hud.close()
        APP.processEvents()

    def test_state_mapping_and_fallback(self):
        for state in STATE_INFO:
            self.hud.set_state(state)
            self.assertEqual(self.hud.state, state)
        self.hud.set_state("bilinmeyen")
        self.assertEqual(self.hud.state, "idle")

    def test_subtitle_compaction_and_sender(self):
        self.hud.set_subtitle("  çok   boşluklu\nmetin  ", "sen")
        self.assertEqual(self.hud.subtitle, "çok boşluklu metin")
        self.assertEqual(self.hud._subtitle_sender, "SEN")

        self.hud.set_subtitle("x" * 400)
        self.assertLessEqual(len(self.hud.subtitle), MAX_SUBTITLE)
        self.assertTrue(self.hud.subtitle.endswith("..."))

    def test_camera_scene_suppresses_raw_json_and_clamps_count(self):
        self.hud.set_camera('{"scene":"masa","objects":[]}', 4, True)
        self.assertEqual(self.hud.camera_text, "")
        self.assertEqual(self.hud.camera_count, 4)
        self.assertTrue(self.hud.camera_active)

        self.hud.set_camera("Masada bir kitap var.", 3, True)
        self.assertEqual(self.hud.camera_text, "Masada bir kitap var.")
        self.assertEqual(self.hud.camera_count, 3)

        self.hud.set_camera("", -8, False)
        self.assertEqual(self.hud.camera_count, 0)
        self.assertFalse(self.hud.camera_active)

        self.hud.set_camera("", "bozuk", False)
        self.assertEqual(self.hud.camera_count, 0)

    def test_mic_flag_is_stored(self):
        self.assertFalse(self.hud._mic_enabled)
        self.hud.set_mic_enabled(True)
        self.assertTrue(self.hud._mic_enabled)
        self.hud.set_mic_enabled(False)
        self.assertFalse(self.hud._mic_enabled)

    def test_show_and_hide_for_main_window_state(self):
        self.hud.show_for_minimize()
        self.assertTrue(self.hud.isVisible())
        self.assertTrue(self.hud._timer.isActive())
        self.assertEqual(self.hud._timer.interval(), 83)

        self.hud.set_state("listening")
        self.assertEqual(self.hud._timer.interval(), 33)

        self.hud.hide_for_restore()
        self.assertFalse(self.hud.isVisible())
        self.assertFalse(self.hud._timer.isActive())

    def test_position_memory_and_reset(self):
        self.settings.position = (60, 70)
        self.hud.reposition()
        self.assertEqual((self.hud.x(), self.hud.y()), (60, 70))

        self.hud.move(15, 20)
        self.hud._save_position()
        self.assertEqual(self.settings.position, (15, 20))

        self.hud.reset_position()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.assertEqual(self.hud.x(), max(screen.left(), screen.right() - self.hud.width() - 24))
        self.assertEqual(self.hud.y(), max(screen.top(), screen.top() + 24))
        self.assertIn((self.hud.x(), self.hud.y()), self.settings.saved)

    def test_offscreen_stored_position_falls_back_to_default(self):
        self.settings.position = (100000, 100000)
        self.hud.reposition()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.assertLess(self.hud.x(), screen.right())
        self.assertLess(self.hud.y(), screen.bottom())

    def test_snap_to_screen_edges(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.hud.show_for_minimize()
        self.hud.move(screen.left() + 3, screen.top() + 4)
        self.hud._snap_to_screen()
        self.assertEqual(self.hud.x(), screen.left())
        self.assertEqual(self.hud.y(), screen.top())

        self.hud.move(screen.right() - self.hud.width() - 2, screen.bottom() - self.hud.height() - 1)
        self.hud._snap_to_screen()
        self.assertEqual(self.hud.x(), screen.right() - self.hud.width())
        self.assertEqual(self.hud.y(), screen.bottom() - self.hud.height())

    def test_paint_all_states_produces_card(self):
        self.hud.show_for_minimize()
        image = QImage(self.hud.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        for state in STATE_INFO:
            self.hud.set_state(state)
            self.hud.set_subtitle("Durum çizimi testi", "JARVIS")
            image.fill(0)
            self.hud.render(image)
            self.assertFalse(image.isNull())
            self.assertGreater(image.pixelColor(180, 80).alpha(), 0)

    def test_single_click_waits_but_double_click_restores(self):
        self.hud.show_for_minimize()
        restored = []
        self.hud.restore_requested.connect(lambda: restored.append(True))
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(80, 80),
            QPointF(200, 200),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(80, 80),
            QPointF(200, 200),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.hud.mousePressEvent(press)
        self.hud.mouseReleaseEvent(release)
        self.assertEqual(restored, [])

        double = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(80, 80),
            QPointF(200, 200),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.hud.mouseDoubleClickEvent(double)
        self.assertEqual(restored, [True])

    def test_main_window_sync_helper_shows_camera_and_restores(self):
        stub = SimpleNamespace(
            floating_hud=self.hud,
            _state="thinking",
            mic_enabled=True,
            _camera_perception=True,
            _restore_maximized=False,
            camera_window=SimpleNamespace(last_scene="Masada kitap var.", last_box_count=2),
        )
        MainWindow._sync_floating_hud_for_state(stub, True, True)
        self.assertTrue(self.hud.isVisible())
        self.assertEqual(self.hud.state, "thinking")
        self.assertTrue(self.hud.camera_active)
        self.assertEqual(self.hud.camera_count, 2)
        self.assertEqual(self.hud.camera_text, "Masada kitap var.")
        self.assertTrue(stub._restore_maximized)

        MainWindow._sync_floating_hud_for_state(stub, False)
        self.assertFalse(self.hud.isVisible())

    def test_camera_scene_signal_updates_hud(self):
        self.hud.show_for_minimize()
        stub = SimpleNamespace(
            floating_hud=self.hud,
            _camera_perception=True,
            camera_window=SimpleNamespace(last_box_count=3),
        )
        stub._update_floating_hud_camera = lambda text="", count=0, active=None: (
            MainWindow._update_floating_hud_camera(stub, text, count, active)
        )
        MainWindow._on_camera_scene_hud(stub, "Bir yüz ve iki kitap.")
        self.assertEqual(self.hud.camera_text, "Bir yüz ve iki kitap.")
        self.assertEqual(self.hud.camera_count, 3)
        self.assertTrue(self.hud.camera_active)

        MainWindow._on_camera_scene_failed_hud(stub, "bağlantı hatası")
        self.assertEqual(self.hud.camera_text, "")
        self.assertEqual(self.hud.camera_count, 0)


class OrbAndMeterTests(unittest.TestCase):
    def test_orb_state_colors_are_vivid(self):
        from app.ui.orb import STATE_ACCENTS, state_color

        for state in ("idle", "listening", "thinking", "speaking"):
            color = QColor(state_color(state))
            self.assertTrue(color.isValid())
            self.assertEqual(color.name(), STATE_ACCENTS[state][0].lower())

        thinking = QColor(state_color("thinking"))
        self.assertGreater(thinking.red(), 200)
        self.assertGreater(thinking.green(), 130)
        self.assertLess(thinking.blue(), 120)
        listening = QColor(state_color("listening"))
        self.assertGreater(listening.green(), 180)
        self.assertGreater(listening.blue(), 150)

    def test_orb_input_level_clamps_and_resets_on_state_change(self):
        from app.ui.orb import OrbWidget, state_color

        orb = OrbWidget()
        orb.set_input_level(5)
        self.assertGreater(orb._input_level, 0.5)
        orb.set_input_level("bozuk")
        orb.set_state("listening")
        orb.set_input_level(1.0)
        self.assertGreater(orb._input_level, 0.5)
        self.assertEqual(orb._accents()[0].name(), QColor(state_color("listening")).name())
        orb.set_state("thinking")
        self.assertEqual(orb._input_level, 0.0)

    def test_mic_meter_level_and_pulse(self):
        from app.ui.shell_parts import MicLevelMeter

        meter = MicLevelMeter()
        meter.set_enabled(True)
        meter.set_level(0.8)
        self.assertAlmostEqual(meter._level, 0.8)
        meter.pulse()
        self.assertEqual(meter._heard, 1.0)
        image = QImage(meter.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        meter.render(image)
        self.assertGreater(image.pixelColor(3, 10).alpha(), 0)
        meter.set_enabled(False)
        meter.set_level(0.7)
        self.assertEqual(meter._level, 0.0)

    def test_voice_level_mapping_is_monotonic_and_bounded(self):
        from app.core.voice import ConversationWorker

        silent = ConversationWorker._level_from_rms(10)
        quiet = ConversationWorker._level_from_rms(200)
        loud = ConversationWorker._level_from_rms(4000)
        self.assertEqual(silent, 0.0)
        self.assertGreater(quiet, 0.0)
        self.assertLess(quiet, loud)
        self.assertLessEqual(loud, 1.0)


class FloatingHudSettingsTests(unittest.TestCase):
    def test_position_roundtrip_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            settings = Settings()
            self.assertIsNone(settings.floating_hud_position)

            settings.save_floating_hud_position(140, -25)
            self.assertEqual(settings.floating_hud_position, (140, -25))
            self.assertEqual(Settings().floating_hud_position, (140, -25))

            raw = json.loads(settings.path.read_text(encoding="utf-8"))
            self.assertEqual(raw["floating_hud_position"], {"x": 140, "y": -25})

            settings.clear_floating_hud_position()
            self.assertIsNone(Settings().floating_hud_position)

    def test_malformed_position_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            settings = Settings()
            settings._data["floating_hud_position"] = {"x": "abc", "y": 3}
            self.assertIsNone(settings.floating_hud_position)

            settings._data["floating_hud_position"] = "bozuk"
            self.assertIsNone(settings.floating_hud_position)


if __name__ == "__main__":
    unittest.main()
