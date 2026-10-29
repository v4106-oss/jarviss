import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.face_tracker import (
    FACE_MAX_FACES,
    FaceTracker,
    face_model_path,
    face_payloads,
)


def detection(origin_x, origin_y, width, height, score=0.9):
    return SimpleNamespace(
        bounding_box=SimpleNamespace(
            origin_x=origin_x,
            origin_y=origin_y,
            width=width,
            height=height,
        ),
        categories=[SimpleNamespace(score=score)],
    )


class FacePayloadTests(unittest.TestCase):
    def test_largest_face_is_focus_and_boxes_are_normalized(self):
        faces = face_payloads(
            [
                detection(20, 30, 100, 100, 0.72),
                detection(200, 40, 160, 180, 0.95),
            ],
            400,
            400,
        )

        self.assertEqual(len(faces), 2)
        self.assertTrue(faces[0]["focus"])
        self.assertFalse(faces[1]["focus"])
        self.assertEqual(faces[0]["confidence"], 0.95)
        self.assertAlmostEqual(faces[0]["box"][0], 0.5)
        self.assertAlmostEqual(faces[0]["box"][1], 0.1)
        self.assertAlmostEqual(faces[0]["box"][2], 0.4)
        self.assertAlmostEqual(faces[0]["box"][3], 0.45)
        self.assertEqual(set(faces[0]), {"box", "confidence", "focus"})

    def test_boxes_are_clamped_and_tiny_or_invalid_boxes_are_dropped(self):
        faces = face_payloads(
            [
                detection(370, 370, 80, 80, 0.8),
                detection(10, 10, 4, 4, 0.9),
                detection(-20, -20, 60, 60, 0.7),
                SimpleNamespace(bounding_box=None, categories=[]),
            ],
            400,
            400,
        )

        self.assertEqual(len(faces), 2)
        for face in faces:
            left, top, width, height = face["box"]
            self.assertGreaterEqual(left, 0.0)
            self.assertGreaterEqual(top, 0.0)
            self.assertLessEqual(left + width, 1.0)
            self.assertLessEqual(top + height, 1.0)

    def test_face_count_is_limited_and_first_face_is_always_focus(self):
        faces = face_payloads(
            [detection(index * 10, 0, 100, 100, 0.8) for index in range(8)],
            1000,
            400,
        )

        self.assertEqual(len(faces), FACE_MAX_FACES)
        self.assertEqual(sum(1 for face in faces if face["focus"]), 1)
        self.assertTrue(faces[0]["focus"])

    def test_invalid_image_size_returns_no_faces(self):
        self.assertEqual(face_payloads([detection(0, 0, 100, 100)], 0, 400), [])
        self.assertEqual(face_payloads([], 400, 0), [])


class FaceModelPathTests(unittest.TestCase):
    def test_model_lives_under_jarvis_models(self):
        path = face_model_path(Path("C:/JarvisData"))
        self.assertEqual(path, Path("C:/JarvisData") / "models" / "blaze_face_short_range.tflite")


class FaceTrackerAvailabilityTests(unittest.TestCase):
    def test_missing_mediapipe_emits_failed(self):
        tracker = FaceTracker(data_dir=Path(tempfile.gettempdir()) / "jarvis_face_tests")
        errors = []
        tracker.failed.connect(errors.append)

        with patch("app.core.face_tracker.mediapipe_available", return_value=False):
            tracker.start()

        self.assertTrue(errors)
        self.assertIn("MediaPipe", errors[0])
        tracker.stop()

    def test_stop_publishes_empty_face_list(self):
        tracker = FaceTracker(data_dir=Path(tempfile.gettempdir()) / "jarvis_face_tests")
        faces = []
        tracker.faces_ready.connect(faces.append)
        tracker.stop()
        self.assertEqual(faces, [[]])


class FaceTrackingSmoothingTests(unittest.TestCase):
    def test_nearby_detection_moves_box_smoothly_not_instantly(self):
        tracker = FaceTracker(data_dir=Path(tempfile.gettempdir()) / "jarvis_face_tests")
        tracker._smoothed([{"box": (0.2, 0.2, 0.2, 0.2), "confidence": 0.8}], 100.0)

        smoothed = tracker._smoothed(
            [{"box": (0.3, 0.2, 0.2, 0.2), "confidence": 0.9}], 100.05
        )

        self.assertEqual(len(smoothed), 1)
        self.assertGreater(smoothed[0]["box"][0], 0.2)
        self.assertLess(smoothed[0]["box"][0], 0.3)
        self.assertTrue(smoothed[0]["focus"])

    def test_missing_detection_is_held_briefly_then_dropped(self):
        tracker = FaceTracker(data_dir=Path(tempfile.gettempdir()) / "jarvis_face_tests")
        tracker._smoothed([{"box": (0.2, 0.2, 0.2, 0.2), "confidence": 0.9}], 100.0)

        held = tracker._smoothed([], 100.1)
        gone = tracker._smoothed([], 100.4)

        self.assertEqual(len(held), 1)
        self.assertEqual(gone, [])


if __name__ == "__main__":
    unittest.main()
