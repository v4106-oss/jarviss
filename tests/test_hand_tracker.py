import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HAS_NUMPY = importlib.util.find_spec("numpy") is not None

from PySide6.QtGui import QColor, QImage

from app.core.hand_tracker import (
    HAND_MAX_WIDTH,
    HandTracker,
    _image_to_array,
    classify_hand,
    hand_model_path,
    hand_payload,
    hand_plausible,
)
from tests.hand_test_utils import make_hand


class GestureClassificationTests(unittest.TestCase):
    def test_all_supported_gestures_are_recognized(self):
        cases = {
            "open": "open",
            "fist": "fist",
            "thumb_up": "thumb_up",
            "victory": "victory",
            "point": "point",
            "pinch": "pinch",
        }
        for gesture, expected in cases.items():
            with self.subTest(gesture=gesture):
                self.assertEqual(classify_hand(make_hand(gesture)), expected)

    def test_payload_has_twenty_one_rounded_points_and_palm(self):
        payload = hand_payload(make_hand("open"), handedness="Right")

        self.assertEqual(len(payload["landmarks"]), 21)
        self.assertEqual(payload["gesture"], "open")
        self.assertEqual(payload["handedness"], "Right")
        self.assertLess(payload["pinch"], 3.0)
        palm_x, palm_y = payload["palm"]
        self.assertAlmostEqual(palm_x, 0.5, places=2)
        self.assertAlmostEqual(palm_y, 0.69, places=2)
        for point in payload["landmarks"]:
            self.assertEqual(len(point), 3)
            self.assertTrue(all(isinstance(value, float) for value in point))

    def test_heart_like_pose_is_not_thumb_up(self):
        points = [list(point) for point in make_hand("thumb_up")]
        points[8] = [0.40, 0.55, 0.0]
        points[4] = [0.49, 0.60, 0.0]

        self.assertEqual(classify_hand(points), "none")

    def test_empty_or_broken_input_does_not_crash(self):
        with self.assertRaises((IndexError, TypeError)):
            classify_hand([])


class HandModelPathTests(unittest.TestCase):
    def test_model_lives_under_jarvis_models(self):
        path = hand_model_path(Path("C:/JarvisData"))
        self.assertEqual(path, Path("C:/JarvisData") / "models" / "hand_landmarker.task")


class ImageConversionTests(unittest.TestCase):
    @unittest.skipUnless(HAS_NUMPY, "numpy/mediapipe kurulu değil")
    def test_qimage_is_scaled_and_converted_to_rgb_array(self):
        image = QImage(640, 480, QImage.Format.Format_RGB32)
        image.fill(QColor(30, 60, 90))

        array = _image_to_array(image)

        self.assertIsNotNone(array)
        self.assertEqual(array.shape, (240, HAND_MAX_WIDTH, 3))
        self.assertEqual(tuple(array[0, 0]), (30, 60, 90))

    def test_invalid_image_returns_none(self):
        self.assertIsNone(_image_to_array(None))
        self.assertIsNone(_image_to_array(QImage()))


class HandPlausibilityTests(unittest.TestCase):
    def test_real_gestures_pass(self):
        for gesture in ("open", "fist", "pinch", "point"):
            with self.subTest(gesture=gesture):
                self.assertTrue(hand_plausible(make_hand(gesture), 0.9))

    def test_low_score_is_rejected(self):
        self.assertFalse(hand_plausible(make_hand("open"), 0.3))

    def test_tiny_or_collapsed_landmarks_are_rejected(self):
        tiny = [
            (0.5 + (x - 0.5) * 0.05, 0.8 + (y - 0.8) * 0.05, z)
            for x, y, z in make_hand("open")
        ]
        self.assertFalse(hand_plausible(tiny, 0.9))
        self.assertFalse(hand_plausible([(0.5, 0.8, 0.0)] * 21, 0.9))

    def test_outlier_reach_ratio_is_rejected(self):
        stretched = list(make_hand("open"))
        stretched[8] = (2.5, -1.5, 0.0)
        self.assertFalse(hand_plausible(stretched, 0.9))


class HandConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tracker = HandTracker(data_dir=Path(tempfile.gettempdir()) / "jarvis_hand_tests")
        self.hand = hand_payload(make_hand("open"), "Right", 0.9)

    def tearDown(self):
        self.tracker.stop()

    def test_first_frame_is_held_back_and_second_confirms(self):
        self.assertEqual(self.tracker._confirmed([self.hand], 10.0), [])
        self.assertEqual(self.tracker._confirmed([self.hand], 10.05), [self.hand])

    def test_short_gap_holds_last_hands_then_clears(self):
        self.tracker._confirmed([self.hand], 10.0)
        self.tracker._confirmed([self.hand], 10.05)
        self.assertEqual(self.tracker._confirmed([], 10.12), [self.hand])
        self.assertEqual(self.tracker._confirmed([], 10.25), [])

    def test_single_frame_false_positive_never_reaches_hud(self):
        self.assertEqual(self.tracker._confirmed([self.hand], 10.0), [])
        self.tracker._confirmed([], 10.05)
        self.assertEqual(self.tracker._confirmed([self.hand], 10.5), [])

    def test_tracked_hand_is_not_reconfirmed_after_short_gap(self):
        self.assertEqual(self.tracker._confirmed([self.hand], 10.0), [])
        self.assertEqual(self.tracker._confirmed([self.hand], 10.05), [self.hand])
        self.assertEqual(self.tracker._confirmed([], 10.2), [self.hand])
        self.assertEqual(self.tracker._confirmed([self.hand], 10.4), [self.hand])


class HandHoldTests(unittest.TestCase):
    def setUp(self):
        self.tracker = HandTracker(data_dir=Path(tempfile.gettempdir()) / "jarvis_hand_tests")
        self.actions = []
        self.tracker.gesture_triggered.connect(self.actions.append)

    def tearDown(self):
        self.tracker.stop()

    def test_thumb_up_hold_fires_once_after_one_second(self):
        self.tracker._update_hold("thumb_up", 10.0)
        self.tracker._update_hold("thumb_up", 10.5)
        self.assertEqual(self.actions, [])
        self.tracker._update_hold("thumb_up", 11.01)
        self.assertEqual(self.actions, ["palette"])
        self.tracker._update_hold("thumb_up", 12.0)
        self.assertEqual(self.actions, ["palette"])

    def test_gesture_change_resets_hold_timer(self):
        self.tracker._update_hold("victory", 20.0)
        self.tracker._update_hold("fist", 20.4)
        self.tracker._update_hold("victory", 20.6)
        self.tracker._update_hold("victory", 21.4)
        self.assertEqual(self.actions, [])
        self.tracker._update_hold("victory", 21.51)
        self.assertEqual(self.actions, ["fullscreen"])

    def test_fist_requires_longer_hold(self):
        self.tracker._update_hold("fist", 30.0)
        self.tracker._update_hold("fist", 31.7)
        self.assertEqual(self.actions, [])
        self.tracker._update_hold("fist", 31.81)
        self.assertEqual(self.actions, ["stop"])

    def test_unknown_gesture_and_empty_hand_reset(self):
        self.tracker._update_hold("fist", 40.0)
        self.tracker._update_hold("none", 41.5)
        self.tracker._update_hold("fist", 41.6)
        self.tracker._update_hold("fist", 43.2)
        self.assertEqual(self.actions, [])


class MediaPipeAvailabilityTests(unittest.TestCase):
    def test_missing_mediapipe_emits_failed_without_thread(self):
        tracker = HandTracker(data_dir=Path(tempfile.gettempdir()) / "jarvis_hand_tests")
        errors = []
        tracker.failed.connect(errors.append)

        with patch("app.core.hand_tracker.mediapipe_available", return_value=False):
            tracker.start()

        self.assertTrue(errors)
        self.assertIn("MediaPipe", errors[0])
        tracker.stop()


if __name__ == "__main__":
    unittest.main()
