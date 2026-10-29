"""Yerel nesne HUD'u ve en-güncel-kare posta kutusu testleri."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage

from app.core.object_tracker import ObjectTracker, object_payloads, turkish_label


class _Box:
    def __init__(self, x, y, w, h):
        self.origin_x = x
        self.origin_y = y
        self.width = w
        self.height = h


class _Category:
    def __init__(self, name, score):
        self.category_name = name
        self.score = score


class _Detection:
    def __init__(self, name, score, box):
        self.categories = [_Category(name, score)]
        self.bounding_box = _Box(*box)


class ObjectPayloadTests(unittest.TestCase):
    def test_labels_are_turkish_and_boxes_normalized(self):
        detections = [
            _Detection("cup", 0.82, (100, 120, 80, 90)),
            _Detection("book", 0.61, (400, 300, 120, 60)),
        ]

        objects = object_payloads(detections, 1000, 500)

        self.assertEqual([item["label"] for item in objects], ["BARDAK", "KİTAP"])
        self.assertEqual(objects[0]["box"], (0.1, 0.24, 0.08, 0.18))
        self.assertGreater(objects[0]["score"], 0.8)

    def test_person_low_score_and_tiny_boxes_are_filtered(self):
        detections = [
            _Detection("person", 0.93, (0, 0, 300, 400)),
            _Detection("cup", 0.2, (10, 10, 50, 50)),
            _Detection("book", 0.7, (10, 10, 5, 5)),
        ]

        objects = object_payloads(detections, 640, 480)

        self.assertEqual(objects, [])

    def test_unknown_label_falls_back_to_uppercase(self):
        self.assertEqual(turkish_label("banana"), "MUZ")
        self.assertEqual(turkish_label("satellite"), "SATELLITE")
        self.assertEqual(turkish_label(""), "NESNE")


class MailboxTests(unittest.TestCase):
    def test_interval_is_clamped_for_hand_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            tracker = ObjectTracker(Path(folder))
            tracker.set_interval(0.36)
            self.assertAlmostEqual(tracker._interval, 0.36)
            tracker.set_interval(99.0)
            self.assertAlmostEqual(tracker._interval, 1.2)
            tracker.set_interval("bozuk")
            self.assertAlmostEqual(tracker._interval, 0.18)

    def test_only_latest_frame_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            tracker = ObjectTracker(Path(folder))
            first = QImage(64, 48, QImage.Format.Format_RGB32)
            first.fill(QColor(10, 20, 30))
            second = QImage(64, 48, QImage.Format.Format_RGB32)
            second.fill(QColor(200, 10, 10))

            tracker.submit_frame(first)
            tracker.submit_frame(second)

            taken = tracker._take_mail()
            self.assertIsNotNone(taken)
            self.assertEqual(taken.pixelColor(0, 0).red(), 200)
            self.assertIsNone(tracker._take_mail())

    def test_null_frames_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            tracker = ObjectTracker(Path(folder))
            tracker.submit_frame(QImage())
            self.assertIsNone(tracker._take_mail())


if __name__ == "__main__":
    unittest.main()
