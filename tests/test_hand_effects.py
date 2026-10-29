import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.core.hand_tracker import hand_payload
from app.ui.hand_effects import PLANETS, HandEffects
from tests.hand_test_utils import make_hand, shift_hand

APP = QApplication.instance() or QApplication([])


def payload(gesture, dx=0.0, dy=0.0):
    return hand_payload(shift_hand(make_hand(gesture), dx, dy))


class PlanetEffectTests(unittest.TestCase):
    def setUp(self):
        self.effects = HandEffects()
        self.effects.set_enabled(True)

    def test_single_pinch_creates_and_releases_planet(self):
        self.effects.update_hands([payload("pinch")], now=10.0)
        self.assertIsNone(self.effects.planet)
        self.effects.update_hands([payload("pinch")], now=10.25)
        planet = self.effects.planet
        self.assertIsNotNone(planet)
        self.assertEqual(planet["name"], "Satürn")
        self.assertTrue(planet["held"])

        self.effects.update_hands([], now=10.4)
        self.assertFalse(planet["held"])
        self.assertEqual(planet["released_at"], 10.4)

    def test_brief_pinch_does_not_spawn_planet(self):
        self.effects.update_hands([payload("pinch")], now=5.0)
        self.effects.update_hands([payload("pinch")], now=5.15)
        self.assertIsNone(self.effects.planet)
        self.effects.update_hands([], now=5.2)
        self.assertIsNone(self.effects.planet)

    def test_world_effect_disabled(self):
        self.assertNotIn("Dünya", [planet["name"] for planet in PLANETS])

    def test_each_new_pinch_cycles_planet(self):
        self.effects.update_hands([payload("pinch")], now=20.0)
        self.effects.update_hands([payload("pinch")], now=20.25)
        self.assertEqual(self.effects.planet["name"], "Satürn")
        self.effects.update_hands([], now=20.4)
        self.effects.update_hands([payload("pinch")], now=20.5)
        self.effects.update_hands([payload("pinch")], now=20.75)
        self.assertEqual(self.effects.planet["name"], "Mars")
        self.effects.update_hands([], now=20.9)
        self.effects.update_hands([payload("pinch")], now=21.0)
        self.effects.update_hands([payload("pinch")], now=21.25)
        self.assertEqual(self.effects.planet["name"], "Jüpiter")

    def test_two_pinches_scale_planet_between_hands(self):
        hands = [payload("pinch", dx=-0.15), payload("pinch", dx=0.15)]
        self.effects.update_hands(hands, now=30.0)
        self.effects.update_hands(hands, now=30.25)
        planet = self.effects.planet
        self.assertGreater(planet["radius"], 0.09)
        self.assertAlmostEqual(planet["x"], 0.425, places=2)

    def test_released_planet_moves_and_stops_after_lifetime(self):
        self.effects.update_hands([payload("pinch")], now=40.0)
        self.effects.update_hands([payload("pinch")], now=40.25)
        self.effects.update_hands([], now=40.4)
        planet = self.effects.planet
        start_x = planet["x"]
        self.effects._advance(40.5)
        self.assertGreaterEqual(planet["x"], start_x)
        self.effects._advance(49.2)
        self.assertIsNone(self.effects.planet)


class BurstAndTrailTests(unittest.TestCase):
    def setUp(self):
        self.effects = HandEffects()
        self.effects.set_enabled(True)

    def test_fist_to_open_creates_particle_burst(self):
        self.effects.update_hands([payload("fist")], now=10.0)
        self.assertEqual(self.effects.bursts, [])
        self.effects.update_hands([payload("open")], now=10.1)
        self.assertEqual(len(self.effects.bursts), 1)
        self.effects.update_hands([payload("open")], now=10.2)
        self.assertEqual(len(self.effects.bursts), 1)

    def test_pointing_finger_leaves_trail(self):
        self.effects.update_hands([payload("point")], now=20.0)
        self.assertEqual(len(self.effects.trails[0]), 1)
        self.effects.update_hands([payload("point")], now=20.1)
        self.assertEqual(len(self.effects.trails[0]), 2)
        self.effects.update_hands([], now=22.0)
        self.assertFalse(self.effects.trails)

    def test_disable_clears_every_effect(self):
        self.effects.update_hands([payload("pinch")], now=30.0)
        self.effects.update_hands([payload("pinch")], now=30.25)
        self.effects.set_enabled(False)
        self.assertIsNone(self.effects.planet)
        self.assertEqual(self.effects.hands, [])
        self.assertFalse(self.effects.is_active())


class EffectsPaintTests(unittest.TestCase):
    def test_effects_render_without_crashing(self):
        effects = HandEffects()
        effects.set_enabled(True)
        effects.update_hands(
            [payload("open", dx=-0.12), payload("open", dx=0.12)],
            now=40.0,
        )
        image = QImage(480, 320, QImage.Format.Format_RGB32)
        image.fill(QColor(0, 0, 0))
        painter = QPainter(image)
        effects.paint(painter, QRectF(0, 0, 480, 320), now=40.1)
        painter.end()

        self.assertFalse(image.isNull())
        lit = any(
            image.pixelColor(x, y).red() > 12
            for y in range(0, image.height(), 20)
            for x in range(0, image.width(), 20)
        )
        self.assertTrue(lit)

    def test_disabled_effects_leave_frame_untouched(self):
        effects = HandEffects()
        effects.update_hands([payload("open")], now=50.0)
        image = QImage(64, 48, QImage.Format.Format_RGB32)
        image.fill(QColor(0, 0, 0))
        painter = QPainter(image)
        effects.paint(painter, QRectF(0, 0, 64, 48), now=50.1)
        painter.end()
        self.assertEqual(image.pixelColor(20, 20), QColor(0, 0, 0))

    def test_skeleton_leads_fast_motion_between_detections(self):
        effects = HandEffects()
        effects.set_enabled(True)
        first = hand_payload(make_hand("open"))
        moved = hand_payload(shift_hand(make_hand("open"), 0.06))
        effects.update_hands([first], now=10.0)
        effects.update_hands([moved], now=10.05)
        shown = effects._display_hands(10.09)
        self.assertGreater(shown[0]["palm"][0], moved["palm"][0])


if __name__ == "__main__":
    unittest.main()
