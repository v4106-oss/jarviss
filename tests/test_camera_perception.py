import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PySide6.QtCore import QObject, QRectF, QSize, Signal, Slot
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from app.core.hand_tracker import hand_payload
from app.ui import camera_window as camera_window_module
from app.ui.camera_hub import CameraPreview
from app.ui.camera_window import (
    CHAT_MAX_WIDTH,
    HUD_TRACK_HEIGHT,
    HUD_TRACK_WIDTH,
    SCENE_MAX_WIDTH,
    CameraFullscreenWindow,
    CameraWindow,
    estimate_frame_shift,
    parse_scene_payload,
    prepare_image,
)
from app.ui.main_window import MainWindow, camera_voice_intent
from tests.hand_test_utils import make_hand

APP = QApplication.instance() or QApplication([])

SCENE_REPLY = (
    '{"scene":"Masada bir kitap ve arkada raflar var.",'
    '"objects":[{"label":"yüz","kind":"face","box_2d":[100,300,500,700]},'
    '{"label":"kitap","kind":"object","box_2d":[550,200,900,750]}]}'
)


class FakeHub(QObject):
    frame_ready = Signal(QImage)
    active_changed = Signal(bool)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.active = False
        self.latest = None
        self.starts = 0
        self.stops = 0
        self.preview_active = []

    def start(self, index=0):
        self.starts += 1
        self.active = True
        self.active_changed.emit(True)
        return True

    def stop(self):
        self.stops += 1
        self.active = False
        self.latest = None
        self.active_changed.emit(False)

    def is_active(self):
        return self.active

    def latest_image(self):
        return self.latest.copy() if self.latest is not None else None

    def set_preview_active(self, active):
        self.preview_active.append(bool(active))


class FakeAI:
    def __init__(self):
        self.paths = []
        self.cancelled = False
        self.quota_blocked = False

    def ask(self, prompt, system_prompt=None, record_history=True, image_paths=None, **kwargs):
        self.paths.append(list(image_paths or []))
        return SCENE_REPLY

    def cancel(self):
        self.cancelled = True


class FakeTracker(QObject):
    ready = Signal()
    failed = Signal(str)
    hands_ready = Signal(list)
    status = Signal(int, int)
    gesture_triggered = Signal(str)

    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = 0
        self.frames = 0

    @Slot()
    def start(self):
        self.started = True
        self.ready.emit()

    @Slot(QImage)
    def process_frame(self, image):
        self.frames += 1

    @Slot()
    def stop(self):
        self.stopped += 1


class FakeFaceTracker(QObject):
    ready = Signal()
    failed = Signal(str)
    faces_ready = Signal(list)

    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = 0
        self.frames = 0

    @Slot()
    def start(self):
        self.started = True
        self.ready.emit()

    @Slot(QImage)
    def process_frame(self, image):
        self.frames += 1

    @Slot()
    def stop(self):
        self.stopped += 1
        self.faces_ready.emit([])


class FakeObjectTracker(QObject):
    ready = Signal()
    failed = Signal(str)
    objects_ready = Signal(list)

    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = 0
        self.frames = 0

    @Slot()
    def start(self):
        self.started = True
        self.ready.emit()

    @Slot(QImage)
    def process_frame(self, image):
        self.frames += 1

    @Slot()
    def stop(self):
        self.stopped += 1
        self.objects_ready.emit([])


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    APP.processEvents()
    return bool(predicate())


def test_frame(size=(64, 48)):
    image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    image.fill(QColor(120, 48, 48))
    return image


class ImagePrepareTests(unittest.TestCase):
    def test_large_frames_are_downscaled_before_upload(self):
        large = QImage(2000, 1000, QImage.Format.Format_RGB32)
        large.fill(QColor(10, 20, 30))

        scaled = prepare_image(large, SCENE_MAX_WIDTH)

        self.assertEqual(scaled.width(), SCENE_MAX_WIDTH)
        self.assertEqual(scaled.height(), round(1000 * SCENE_MAX_WIDTH / 2000))
        self.assertLessEqual(scaled.width(), CHAT_MAX_WIDTH)

    def test_small_and_empty_images_are_left_untouched(self):
        small = test_frame((64, 48))
        self.assertIs(prepare_image(small, SCENE_MAX_WIDTH), small)
        self.assertIs(prepare_image(small, 0), small)
        self.assertTrue(prepare_image(QImage(), SCENE_MAX_WIDTH).isNull())


class CameraWindowTests(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.parent.resize(1600, 960)
        self.parent.show()
        self.hub = FakeHub()
        self.ai = FakeAI()
        self.trackers = []
        self.face_trackers = []
        self.object_trackers = []
        self.window = CameraWindow(
            self.parent,
            self.hub,
            self.ai,
            hand_factory=self._tracker_factory,
            face_factory=self._face_tracker_factory,
            object_factory=self._object_tracker_factory,
        )
        APP.processEvents()

    def _tracker_factory(self):
        tracker = FakeTracker()
        self.trackers.append(tracker)
        return tracker

    def _face_tracker_factory(self):
        tracker = FakeFaceTracker()
        self.face_trackers.append(tracker)
        return tracker

    def _object_tracker_factory(self):
        tracker = FakeObjectTracker()
        self.object_trackers.append(tracker)
        return tracker

    def tearDown(self):
        try:
            self.window.stop_perception()
        except RuntimeError:
            pass
        self.window.close()
        self.parent.close()
        APP.processEvents()

    def test_perception_keeps_running_when_window_hidden(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)

        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.hub.starts, 1)
        self.assertTrue(self.window._timer.isActive())
        self.assertTrue(self.window.preview._scanning)
        self.assertTrue(self.window.preview._timer.isActive())

        self.window.hide()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window._perception)
        self.assertTrue(self.window._timer.isActive())
        self.assertFalse(self.window.preview._timer.isActive())

        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.ensure_visible()
        APP.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.preview._timer.isActive())

    def test_first_camera_frame_starts_automatic_analysis(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.assertFalse(self.window.is_analyzing())
        self.hub.frame_ready.emit(test_frame())
        self.assertTrue(wait_until(lambda: bool(self.window.last_scene)))
        self.assertEqual(self.window.last_scene, "Masada bir kitap ve arkada raflar var.")
        self.assertTrue(self.ai.paths)
        self.assertTrue(self.window._timer.isActive())
        self.assertTrue(self.window._timer.isSingleShot())
        self.assertGreaterEqual(self.window._timer.interval(), camera_window_module.SCAN_MIN_GAP_MS)
        self.assertLessEqual(self.window._timer.interval(), camera_window_module.SCAN_ACTIVE_MS)
        self.assertTrue(self.window.preview._scanning)
        self.assertFalse(self.window.preview._analyzing)
        self.assertEqual(len(self.window.preview._hud_boxes), 2)
        self.assertEqual([box[1] for box in self.window.preview._hud_boxes], ["face", "object"])
        self.assertEqual(self.window.last_box_count, 2)

    def test_hidden_camera_does_not_spend_cloud_requests(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=False)
        self.hub.frame_ready.emit(test_frame())
        for _ in range(20):
            APP.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.ai.paths, [])
        self.assertFalse(self.window._initial_analysis_started)
        self.assertTrue(self.window._timer.isActive())

    def test_quota_block_stops_scans_even_when_visible(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.ai.quota_blocked = True
        self.hub.frame_ready.emit(test_frame())
        for _ in range(20):
            APP.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.ai.paths, [])
        self.window._periodic_analysis()
        self.assertEqual(self.ai.paths, [])

    def test_motion_switches_scan_to_active_interval(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=False)
        dark = QImage(64, 48, QImage.Format.Format_RGB32)
        dark.fill(QColor(0, 0, 0))
        bright = QImage(64, 48, QImage.Format.Format_RGB32)
        bright.fill(QColor(255, 255, 255))
        self.hub.frame_ready.emit(dark)
        self.assertTrue(wait_until(lambda: not self.window.is_analyzing()))
        self.window._last_motion_sample = 0.0
        self.hub.frame_ready.emit(bright)
        self.assertTrue(wait_until(lambda: self.window._last_motion_at > 0))
        self.assertEqual(self.window._next_scan_interval(), camera_window_module.SCAN_ACTIVE_MS)
        self.assertEqual(self.window._timer.interval(), camera_window_module.SCAN_ACTIVE_MS)

    def test_motion_nudges_hud_boxes_locally(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.window._apply_hud_boxes([("kitap", "object", QRectF(0.4, 0.4, 0.2, 0.2))])
        frame = test_frame()
        with (
            patch.object(
                camera_window_module,
                "_frame_signature",
                side_effect=[b"a" * 4000, b"b" * 4000],
            ),
            patch.object(camera_window_module, "_signature_difference", return_value=5.0),
            patch.object(camera_window_module, "estimate_frame_shift", return_value=(2, 1)),
        ):
            self.window._last_motion_sample = 0.0
            self.window._track_motion(frame)
            self.window._last_motion_sample = 0.0
            self.window._track_motion(frame)

        self.assertGreater(self.window.preview._hud_shift.x(), 0)
        self.assertGreater(self.window.preview._hud_shift.y(), 0)

    def test_forced_analyze_while_busy_is_queued_and_timeout_recovers(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.window._busy = True
        self.assertFalse(self.window.analyze_now(force=True))
        self.assertTrue(self.window._queued_analyze)

        self.window._on_scan_timeout()

        self.assertFalse(self.window._busy)
        self.assertTrue(self.window.analyze_button.isEnabled())
        self.assertIn("ZAMAN AŞIMI", self.window.status_label.text())

    def test_scene_analysis_uses_temporary_frame_and_deletes_it(self):
        self.hub.latest = test_frame()
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.window.analyze_now(force=True)

        self.assertTrue(wait_until(lambda: bool(self.window.last_scene)))
        self.assertFalse(hasattr(self.window, "scene_edit"))
        self.assertEqual(self.window.last_scene, "Masada bir kitap ve arkada raflar var.")
        self.assertTrue(self.ai.paths)
        paths = self.ai.paths[-1]
        self.assertEqual(len(paths), 1)
        self.assertFalse(Path(paths[0]).exists())

    def test_fullscreen_hud_shows_boxes_and_restores_panel(self):
        self.hub.latest = test_frame()
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
            self.window.analyze_now(force=True)
        self.assertTrue(wait_until(lambda: bool(self.window.last_scene)))

        self.window.show_fullscreen()
        APP.processEvents()
        fullscreen = self.window._fullscreen_window
        self.assertIsInstance(fullscreen, CameraFullscreenWindow)
        self.assertTrue(fullscreen.isVisible())
        self.assertFalse(self.window.isVisible())
        self.assertEqual(len(fullscreen.preview._hud_boxes), 2)
        self.assertIn("HEDEF", fullscreen.state_label.text())

        fullscreen.close()
        self.assertTrue(wait_until(lambda: self.window.isVisible()))
        self.assertTrue(self.window._perception)

    def test_stop_perception_hides_window_and_cancels_ai(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.window.stop_perception()

        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window._perception)
        self.assertFalse(self.window._timer.isActive())
        self.assertTrue(self.ai.cancelled)
        self.assertEqual(self.hub.stops, 0)
        self.assertFalse(self.window.preview._scanning)
        self.assertEqual(self.window.preview._hud_boxes, [])
        self.assertEqual(self.window.last_box_count, 0)

    def test_scanning_preview_paints(self):
        self.window.preview.set_frame(test_frame())
        self.window.preview.set_scanning(True)
        APP.processEvents()
        self.assertFalse(self.window.preview.grab().isNull())

    def test_hand_control_tracks_frames_and_forwards_gesture(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        gestures = []
        self.window.gesture_action.connect(gestures.append)

        self.assertTrue(self.window.set_hand_control(True))
        self.assertTrue(wait_until(lambda: bool(self.trackers) and self.trackers[0].started))
        tracker = self.trackers[0]

        self.hub.frame_ready.emit(test_frame())
        self.assertTrue(wait_until(lambda: tracker.frames >= 1))

        tracker.hands_ready.emit([hand_payload(make_hand("open"))])
        tracker.status.emit(14, 1)
        self.assertTrue(wait_until(lambda: "1 EL" in self.window.hand_label.text()))
        self.assertEqual(self.window.preview._hand_count, 1)
        self.assertTrue(self.window.preview._hand_enabled)

        tracker.gesture_triggered.emit("palette")
        self.assertTrue(wait_until(lambda: gestures == ["palette"]))

        self.assertTrue(self.window.set_hand_control(False))
        self.assertTrue(wait_until(lambda: tracker.stopped >= 1))
        self.assertFalse(self.window.hand_control_enabled())
        self.assertFalse(self.window.preview._hand_enabled)
        self.assertFalse(self.window.hand_button.isChecked())

    def test_missing_mediapipe_disables_hand_control(self):
        self.window._hand_factory = None
        failures = []
        self.window.hand_failed.connect(failures.append)
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=False)
        with patch.object(camera_window_module, "mediapipe_installed", return_value=False):
            self.assertFalse(self.window.set_hand_control(True))

        self.assertTrue(failures)
        self.assertIn("MediaPipe", failures[0])
        self.assertIn("MEDIAPIPE", self.window.hand_label.text())
        self.assertFalse(self.window.preview._hand_enabled)

    def test_stop_perception_stops_hand_tracker(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.assertTrue(self.window.set_hand_control(True))
        self.assertTrue(wait_until(lambda: bool(self.trackers) and self.trackers[0].started))
        tracker = self.trackers[0]

        self.window.stop_perception()

        self.assertTrue(wait_until(lambda: tracker.stopped >= 1))
        self.assertFalse(self.window.hand_control_enabled())
        self.assertFalse(self.window.preview._hand_enabled)

    def test_fullscreen_hand_toggle_controls_tracker(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
            self.window.show_fullscreen()
        fullscreen = self.window._fullscreen_window
        self.assertTrue(fullscreen.isVisible())

        fullscreen.hand_button.setChecked(True)
        self.assertTrue(wait_until(lambda: bool(self.trackers) and self.trackers[0].started))
        self.assertTrue(self.window.hand_control_enabled())
        self.assertTrue(fullscreen.hand_button.isChecked())
        self.assertIn("ELLER", fullscreen.hand_label.text())

        fullscreen.hand_button.setChecked(False)
        self.assertTrue(wait_until(lambda: self.trackers[0].stopped >= 1))
        self.assertFalse(self.window.hand_control_enabled())
        self.assertFalse(fullscreen.hand_button.isChecked())

    def test_face_hud_starts_with_perception_and_feeds_panel_and_fullscreen(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.assertTrue(wait_until(lambda: bool(self.face_trackers) and self.face_trackers[0].started))
        tracker = self.face_trackers[0]
        self.assertTrue(self.window.face_tracking_enabled())

        self.hub.frame_ready.emit(test_frame())
        self.assertTrue(wait_until(lambda: tracker.frames >= 1))

        tracker.faces_ready.emit(
            [
                {"box": (0.2, 0.15, 0.3, 0.4), "confidence": 0.94, "focus": True},
                {"box": (0.62, 0.2, 0.2, 0.3), "confidence": 0.71, "focus": False},
            ]
        )
        self.assertTrue(wait_until(lambda: len(self.window.preview._face_boxes) == 2))
        self.assertTrue(self.window.preview._face_enabled)
        self.assertTrue(self.window.preview._face_boxes[0]["focus"])
        self.assertFalse(self.window.preview._face_boxes[1]["focus"])

        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.show_fullscreen()
        APP.processEvents()
        fullscreen = self.window._fullscreen_window
        self.assertEqual(len(fullscreen.preview._face_boxes), 2)
        self.assertTrue(fullscreen.preview._face_enabled)

    def test_face_hud_failure_does_not_stop_camera_perception(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.assertTrue(wait_until(lambda: bool(self.face_trackers) and self.face_trackers[0].started))
        tracker = self.face_trackers[0]

        tracker.failed.emit("Yüz modeli indirilemedi.")

        self.assertTrue(wait_until(lambda: not self.window.face_tracking_enabled()))
        self.assertTrue(self.window._perception)
        self.assertFalse(self.window.preview._face_enabled)
        self.assertTrue(wait_until(lambda: tracker.stopped >= 1))

    def test_stop_perception_stops_face_tracker_and_clears_hud(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.assertTrue(wait_until(lambda: bool(self.face_trackers) and self.face_trackers[0].started))
        tracker = self.face_trackers[0]
        tracker.faces_ready.emit(
            [{"box": (0.2, 0.2, 0.3, 0.3), "confidence": 0.9, "focus": True}]
        )
        self.assertTrue(wait_until(lambda: len(self.window.preview._face_boxes) == 1))

        self.window.stop_perception()

        self.assertTrue(wait_until(lambda: tracker.stopped >= 1))
        self.assertFalse(self.window.face_tracking_enabled())
        self.assertFalse(self.window.preview._face_enabled)
        self.assertEqual(self.window.preview._face_boxes, [])

    def test_local_objects_reach_hud_without_waiting_for_cloud(self):
        with patch.object(camera_window_module.QTimer, "singleShot"):
            self.window.start_perception(show=True)
        self.assertTrue(
            wait_until(lambda: bool(self.object_trackers) and self.object_trackers[0].started)
        )
        tracker = self.object_trackers[0]

        tracker.objects_ready.emit(
            [
                {"box": (0.1, 0.2, 0.3, 0.4), "label": "BARDAK", "score": 0.8},
                {"box": (0.5, 0.3, 0.25, 0.3), "label": "KİTAP", "score": 0.6},
            ]
        )
        self.assertTrue(wait_until(lambda: len(self.window.preview._local_boxes) == 2))
        self.assertEqual(self.window.preview._local_boxes[0][0], "BARDAK")
        self.assertEqual(self.window.preview._local_boxes[0][1], "object")
        self.assertFalse(self.window.last_scene)

        self.window._apply_hud_boxes([("kitap", "object", QRectF(0.5, 0.3, 0.25, 0.3))])
        self.assertTrue(self.window.preview._local_duplicate(QRectF(0.5, 0.3, 0.25, 0.3)))
        self.assertFalse(self.window.preview._local_duplicate(QRectF(0.8, 0.7, 0.1, 0.1)))

        self.window.stop_perception()
        self.assertTrue(wait_until(lambda: tracker.stopped >= 1))
        self.assertEqual(self.window.preview._local_boxes, [])


class ScenePayloadTests(unittest.TestCase):
    def test_parses_fenced_json_and_normalizes_boxes(self):
        payload = (
            '```json\n{"scene":"Bir yüz ve kitap.", "objects":['
            '{"label":"yüz","kind":"face","box_2d":[100,200,400,500]},'
            '{"label":"kitap","kind":"object","box":[600,100,900,300]}]}\n```'
        )
        scene, boxes = parse_scene_payload(payload)
        self.assertEqual(scene, "Bir yüz ve kitap.")
        self.assertEqual([box[0] for box in boxes], ["yüz", "kitap"])
        self.assertEqual([box[1] for box in boxes], ["face", "object"])
        first = boxes[0][2]
        self.assertAlmostEqual(first.left(), 0.2)
        self.assertAlmostEqual(first.top(), 0.1)
        self.assertAlmostEqual(first.width(), 0.3)
        self.assertAlmostEqual(first.height(), 0.3)
        self.assertAlmostEqual(boxes[1][2].top(), 0.6)

    def test_unit_and_turkish_kind_coordinates_are_accepted(self):
        scene, boxes = parse_scene_payload(
            '{"scene":"Ekran var.","objects":[{"label":"ekran","kind":"ekran",'
            '"box":[0.1,0.2,0.5,0.8]}]}'
        )
        self.assertEqual(scene, "Ekran var.")
        self.assertEqual(boxes[0][1], "screen")
        self.assertAlmostEqual(boxes[0][2].left(), 0.2)
        self.assertAlmostEqual(boxes[0][2].top(), 0.1)

    def test_invalid_payload_never_fabricates_boxes(self):
        scene, boxes = parse_scene_payload("Masada bir kitap var.")
        self.assertEqual(scene, "Masada bir kitap var.")
        self.assertEqual(boxes, [])
        scene, boxes = parse_scene_payload(
            '{"scene":"x","objects":[{"label":"yüz","box_2d":[1,2,3]},'
            '{"label":"","box_2d":[0,0,500,500]}]}'
        )
        self.assertEqual(scene, "x")
        self.assertEqual(boxes, [])

    def test_missing_scene_falls_back_to_target_labels(self):
        scene, boxes = parse_scene_payload(
            '{"objects":[{"label":"yüz","kind":"face","box_2d":[100,100,900,900]}]}'
        )
        self.assertEqual(scene, "Görülen hedefler: yüz")
        self.assertEqual(len(boxes), 1)


class CameraPreviewHudTests(unittest.TestCase):
    def test_face_targets_interpolate_instead_of_jumping(self):
        preview = CameraPreview()
        preview.resize(360, 260)
        preview.set_face_enabled(True)
        preview.set_faces([{"box": (0.2, 0.2, 0.2, 0.2), "confidence": 0.9, "focus": True}])
        start_x = preview._face_render[0]["box"].x()

        preview.set_faces([{"box": (0.42, 0.2, 0.2, 0.2), "confidence": 0.9, "focus": True}])
        self.assertAlmostEqual(preview._face_render[0]["box"].x(), start_x)

        preview._last_tick_at = time.monotonic() - 0.016
        preview._tick()
        interpolated = preview._face_render[0]["box"].x()
        self.assertGreater(interpolated, start_x)
        self.assertLess(interpolated, 0.42)

    def test_scan_probe_wanders_with_bounded_trail(self):
        preview = CameraPreview()
        preview.set_scanning(True)
        for _ in range(120):
            preview._advance_scan()
        self.assertEqual(len(preview._scan_trail), CameraPreview.SCAN_TRAIL_LEN)
        self.assertGreater(preview._scan_time, 0.0)
        start = preview._scan_trail[0]
        end = preview._scan_trail[-1]
        self.assertGreater(abs(start.x() - end.x()) + abs(start.y() - end.y()), 0.001)
        for point in preview._scan_trail:
            self.assertGreaterEqual(point.x(), 0.1)
            self.assertLessEqual(point.x(), 0.9)
            self.assertGreaterEqual(point.y(), 0.1)
            self.assertLessEqual(point.y(), 0.9)

    def test_scan_ping_spawns_and_resets(self):
        preview = CameraPreview()
        preview.set_scanning(True)
        preview._next_ping = 0.0
        preview._advance_scan()
        self.assertEqual(len(preview._scan_pings), 1)
        preview.set_scanning(False)
        self.assertEqual(preview._scan_trail, [])
        self.assertEqual(preview._scan_pings, [])
        self.assertEqual(preview._scan_time, 0.0)

    def test_hud_boxes_paint_and_clear(self):
        preview = CameraPreview()
        preview.resize(360, 260)
        preview.set_frame(test_frame((360, 260)))
        preview.set_scanning(True)
        preview.set_analyzing(True)
        preview.set_hud_boxes([
            ("yüz", "face", QRectF(0.12, 0.1, 0.3, 0.42)),
            ("kitap", "object", QRectF(0.55, 0.4, 0.28, 0.36)),
        ])
        APP.processEvents()
        self.assertEqual(len(preview._hud_boxes), 2)
        self.assertFalse(preview.grab().isNull())
        preview.clear_hud_boxes()
        self.assertEqual(preview._hud_boxes, [])

    def test_grid_does_not_fill_hud_boxes(self):
        preview = CameraPreview()
        preview.resize(360, 260)
        preview.set_frame(test_frame((360, 260)))
        preview.set_scanning(False)
        preview.set_cinematic(True)
        preview.set_hud_boxes([("kişi", "person", QRectF(0.25, 0.25, 0.5, 0.5))])
        for item in preview._hud_render:
            item["alpha"] = 1.0
        APP.processEvents()

        image = preview.grab().toImage()
        target = preview._target_rect()
        rect = preview._hud_render[0]["box"]
        left = int(target.left() + rect.left() * target.width()) + 6
        top = int(target.top() + rect.top() * target.height()) + 6
        right = int(target.left() + rect.right() * target.width()) - 6
        bottom = int(target.top() + rect.bottom() * target.height()) - 6
        matches = total = 0
        for y in range(top, bottom, 3):
            for x in range(left, right, 3):
                color = image.pixelColor(x, y)
                total += 1
                if (color.red(), color.green(), color.blue()) == (120, 48, 48):
                    matches += 1

        self.assertGreater(total, 50)
        # Izgara çizgileri birkaç pikseli boyar; dolgu hatasında oran ~0'a düşer.
        self.assertGreater(matches / total, 0.6)

    def test_hand_effects_and_status_paint_in_preview(self):
        preview = CameraPreview()
        preview.resize(360, 260)
        preview.set_frame(test_frame((360, 260)))
        preview.set_scanning(True)
        preview.set_effects_enabled(True)
        preview.set_hands([hand_payload(make_hand("open"))])
        preview.set_hand_status(12, 1)
        APP.processEvents()

        self.assertTrue(preview._hand_enabled)
        self.assertEqual(preview._hand_count, 1)
        self.assertFalse(preview.grab().isNull())

        preview.set_effects_enabled(False)
        self.assertFalse(preview._hand_enabled)
        self.assertEqual(preview._hand_count, 0)

    def test_local_face_hud_paints_nudges_and_clears(self):
        preview = CameraPreview()
        preview.resize(360, 260)
        preview.set_frame(test_frame((360, 260)))
        preview.set_scanning(True)
        preview.set_face_enabled(True)
        preview.set_faces(
            [
                {"box": (0.18, 0.12, 0.28, 0.42), "confidence": 0.93, "focus": True},
                {"box": (0.62, 0.2, 0.2, 0.3), "confidence": 0.68, "focus": False},
            ]
        )
        APP.processEvents()

        self.assertTrue(preview._face_enabled)
        self.assertEqual(len(preview._face_boxes), 2)
        self.assertTrue(preview._face_boxes[0]["focus"])
        self.assertFalse(preview.grab().isNull())

        preview.nudge_face_boxes(0.5, -0.5)
        self.assertAlmostEqual(preview._face_shift.x(), CameraPreview.FACE_SHIFT_LIMIT)
        self.assertAlmostEqual(preview._face_shift.y(), -CameraPreview.FACE_SHIFT_LIMIT)
        preview.nudge_face_boxes(0.01, 0.01)
        self.assertAlmostEqual(preview._face_shift.x(), CameraPreview.FACE_SHIFT_LIMIT)

        preview.clear_faces()
        self.assertEqual(preview._face_boxes, [])
        self.assertTrue(preview._face_enabled)
        preview.set_face_enabled(False)
        self.assertFalse(preview._face_enabled)


class FrameShiftTests(unittest.TestCase):
    @staticmethod
    def _textured_frame(shift_right: int = 0, shift_down: int = 0) -> bytes:
        data = bytearray(HUD_TRACK_WIDTH * HUD_TRACK_HEIGHT)
        for y in range(HUD_TRACK_HEIGHT):
            for x in range(HUD_TRACK_WIDTH):
                source_x = x - shift_right
                source_y = y - shift_down
                if 0 <= source_x < HUD_TRACK_WIDTH and 0 <= source_y < HUD_TRACK_HEIGHT:
                    data[y * HUD_TRACK_WIDTH + x] = (source_x * 7 + source_y * 13) % 256
        return bytes(data)

    def test_shift_estimate_follows_content(self):
        previous = self._textured_frame()
        current = self._textured_frame(2, 1)
        self.assertEqual(estimate_frame_shift(previous, current), (2, 1))

    def test_uniform_brightness_change_reports_no_shift(self):
        previous = bytes([10]) * (HUD_TRACK_WIDTH * HUD_TRACK_HEIGHT)
        current = bytes([180]) * (HUD_TRACK_WIDTH * HUD_TRACK_HEIGHT)
        self.assertEqual(estimate_frame_shift(previous, current), (0, 0))

    def test_hud_boxes_nudge_and_clamp(self):
        preview = CameraPreview()
        preview.set_hud_boxes([("yüz", "face", QRectF(0.2, 0.2, 0.3, 0.3))])
        preview.nudge_hud_boxes(0.5, -0.5)
        self.assertAlmostEqual(preview._hud_shift.x(), CameraPreview.HUD_SHIFT_LIMIT)
        self.assertAlmostEqual(preview._hud_shift.y(), -CameraPreview.HUD_SHIFT_LIMIT)
        preview.nudge_hud_boxes(0.01, 0.01)
        self.assertAlmostEqual(preview._hud_shift.x(), CameraPreview.HUD_SHIFT_LIMIT)

    def test_hud_targets_glide_instead_of_jumping(self):
        preview = CameraPreview()
        preview.resize(360, 260)
        preview.set_hud_boxes([("yüz", "face", QRectF(0.2, 0.2, 0.2, 0.2))])
        start_x = preview._hud_render[0]["box"].x()

        preview.set_hud_boxes([("yüz", "face", QRectF(0.42, 0.2, 0.2, 0.2))])
        self.assertAlmostEqual(preview._hud_render[0]["box"].x(), start_x)

        preview._last_tick_at = time.monotonic() - 0.033
        preview._tick()
        moved = preview._hud_render[0]["box"].x()
        self.assertGreater(moved, start_x)
        self.assertLess(moved, 0.42)


class CameraFormatTests(unittest.TestCase):
    class _Format:
        def __init__(self, width, height, fps):
            self._size = QSize(width, height)
            self._fps = fps

        def resolution(self):
            return self._size

        def maxFrameRate(self):
            return self._fps

    class _Device:
        def __init__(self, formats):
            self._formats = formats

        def videoFormats(self):
            return self._formats

    def test_prefers_720p_30fps_over_640x480(self):
        from app.ui.camera_hub import CameraHub

        device = self._Device(
            [
                self._Format(1280, 720, 30),
                self._Format(640, 480, 30),
                self._Format(640, 480, 15),
                self._Format(1920, 1080, 30),
            ]
        )

        chosen = CameraHub._preferred_format(device)

        self.assertEqual((chosen.resolution().width(), chosen.resolution().height()), (1280, 720))
        self.assertEqual(chosen.maxFrameRate(), 30)

    def test_falls_back_to_next_best_16_9_below_720p(self):
        from app.ui.camera_hub import CameraHub

        device = self._Device([self._Format(960, 540, 30), self._Format(800, 600, 30)])

        chosen = CameraHub._preferred_format(device)

        self.assertEqual((chosen.resolution().width(), chosen.resolution().height()), (960, 540))


class CameraVoiceIntentTests(unittest.TestCase):
    def test_camera_commands_are_classified(self):
        cases = {
            "kamerayı aç": "show",
            "Kamerayı açar mısın?": "show",
            "kamera başlat": "show",
            "kamerayı kapat": "off",
            "kamerayı kapatır mısın": "off",
            "kamera algısını durdur": "off",
            "kamerayı gizle": "hide",
            "kamera penceresini kapat": "hide",
            "kamera panelini gizle": "hide",
            "kamerayı analiz et": "analyze",
            "etrafı analiz et": "analyze",
            "ne görüyorsun?": "analyze",
            "beni tara": "analyze",
            "beni düzenli olarak tara": "analyze",
            "etrafı tara": "analyze",
            "kamerayı tara": "analyze",
            "kamerayı tam ekran yap": "fullscreen",
            "kamera tam ekran": "fullscreen",
            "kamerayı büyüt": "fullscreen",
            "kamera ekranını büyüt": "fullscreen",
            "kamera ekranını çıkar": "hide",
            "kamera ekranını küçült": "hide",
            "kamerayı büyük ekranda göster": "fullscreen",
            "el kontrolünü aç": "hands_on",
            "elleri kullan": "hands_on",
            "jestleri başlat": "hands_on",
            "el hareketiyle kontrolü aktif et": "hands_on",
            "el kontrolünü kapat": "hands_off",
            "elleri kapat": "hands_off",
            "jestleri durdur": "hands_off",
            "el kontrolünü devre dışı bırak": "hands_off",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(camera_voice_intent(text), expected)

    def test_questions_and_unrelated_text_are_not_commands(self):
        for text in (
            "kamera açık mı?", "kamera kapalı mı?", "merhaba", "kamera",
            "kamera kitabı tara", "el kontrolü nedir?", "ellerim yoruldu",
        ):
            with self.subTest(text=text):
                self.assertIsNone(camera_voice_intent(text))

    def test_negative_imperatives_are_not_commands(self):
        for text in ("kamerayı açma", "kamerayı kapatma", "el kontrolünü açma", "elleri kapatma"):
            with self.subTest(text=text):
                self.assertIsNone(camera_voice_intent(text))

    def test_multi_command_hand_text_is_deferred_to_ai(self):
        self.assertIsNone(camera_voice_intent("elleri aç ve kamerayı kapat"))


class _Chat:
    def __init__(self):
        self.items = []

    def add_message(self, text, user):
        self.items.append((text, bool(user)))


class _Speech:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class _Progress:
    def __init__(self):
        self.text = ""
        self.visible = False

    def setText(self, text):
        self.text = text

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class _CameraWindow:
    def __init__(self):
        self.hidden = 0

    def hide(self):
        self.hidden += 1


class _HandlerStub:
    def __init__(self):
        self.chat = _Chat()
        self.speech = _Speech()
        self.progress_label = _Progress()
        self.camera_window = _CameraWindow()
        self._camera_perception = True
        self._camera_manual_off = False
        self._voice_scene_pending = False
        self._voice_scene_tries = 0
        self.states = []
        self.disabled = []
        self.enabled = []
        self.triggered = 0
        self.fullscreen_ok = True
        self.fullscreen_calls = 0
        self.hand_enabled = False
        self.hand_open_ok = True
        self.hand_open_calls = 0
        self.hand_close_calls = 0

    def _set_state(self, state):
        self.states.append(state)

    def open_camera_fullscreen(self):
        self.fullscreen_calls += 1
        return self.fullscreen_ok

    def _trigger_voice_scene_analysis(self):
        self.triggered += 1

    def disable_camera_perception(self, quiet=False):
        self.disabled.append(quiet)
        self._camera_perception = False

    def enable_camera_perception(self, show=True, quiet=False):
        self.enabled.append((show, quiet))
        self._camera_perception = True

    def open_hand_control(self):
        self.hand_open_calls += 1
        if self.hand_open_ok:
            self.hand_enabled = True
        return self.hand_open_ok

    def close_hand_control(self):
        self.hand_close_calls += 1
        was_enabled = self.hand_enabled
        self.hand_enabled = False
        return was_enabled


class CameraVoiceHandlerTests(unittest.TestCase):
    def test_off_disables_and_speaks(self):
        stub = _HandlerStub()
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "kamerayı kapat"))
        self.assertEqual(stub.disabled, [True])
        self.assertTrue(stub._camera_manual_off)
        self.assertEqual(stub.states[-1], "speaking")
        self.assertTrue(stub.speech.spoken)
        self.assertEqual(stub.chat.items[0], ("kamerayı kapat", True))
        self.assertFalse(stub.chat.items[-1][1])

    def test_hide_keeps_perception_running(self):
        stub = _HandlerStub()
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "kamerayı gizle"))
        self.assertEqual(stub.camera_window.hidden, 1)
        self.assertTrue(stub._camera_perception)
        self.assertEqual(stub.disabled, [])

    def test_open_enables_and_shows(self):
        stub = _HandlerStub()
        stub._camera_perception = False
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "kamerayı aç"))
        self.assertEqual(stub.enabled, [(True, True)])
        self.assertTrue(stub.speech.spoken)

    def test_analyze_triggers_scene_after_voice_command(self):
        stub = _HandlerStub()
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "kamerayı analiz et"))
        self.assertTrue(stub._voice_scene_pending)
        self.assertEqual(stub.triggered, 1)
        self.assertTrue(stub.progress_label.visible)
        self.assertEqual(stub.speech.spoken, [])

    def test_fullscreen_command_opens_hud_and_speaks(self):
        stub = _HandlerStub()
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "kamerayı tam ekran yap"))
        self.assertEqual(stub.fullscreen_calls, 1)
        self.assertEqual(stub.states[-1], "speaking")
        self.assertIn("tam ekran", stub.speech.spoken[-1].lower())
        self.assertIn("Esc", stub.chat.items[-1][0])

    def test_fullscreen_command_reports_failure(self):
        stub = _HandlerStub()
        stub.fullscreen_ok = False
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "kamerayı büyüt", speak=False))
        self.assertEqual(stub.fullscreen_calls, 1)
        self.assertEqual(stub.states[-1], "idle")
        self.assertEqual(stub.speech.spoken, [])
        self.assertIn("açılamadı", stub.chat.items[-1][0])

    def test_unrelated_voice_text_is_ignored(self):
        stub = _HandlerStub()
        self.assertFalse(MainWindow._handle_camera_voice_command(stub, "merhaba"))
        self.assertEqual(stub.chat.items, [])

    def test_hands_on_opens_control_and_speaks_gestures(self):
        stub = _HandlerStub()
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "el kontrolünü aç"))
        self.assertEqual(stub.hand_open_calls, 1)
        self.assertTrue(stub.hand_enabled)
        self.assertEqual(stub.states[-1], "speaking")
        self.assertIn("Başparmak", stub.speech.spoken[-1])

    def test_hands_on_failure_explains_requirement(self):
        stub = _HandlerStub()
        stub.hand_open_ok = False
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "elleri kullan", speak=False))
        self.assertEqual(stub.states[-1], "idle")
        self.assertIn("açılamadı", stub.chat.items[-1][0])
        self.assertIn("MediaPipe", stub.chat.items[-1][0])

    def test_hands_off_closes_control(self):
        stub = _HandlerStub()
        stub.hand_enabled = True
        self.assertTrue(MainWindow._handle_camera_voice_command(stub, "el kontrolünü kapat"))
        self.assertEqual(stub.hand_close_calls, 1)
        self.assertFalse(stub.hand_enabled)
        self.assertIn("kapattım", stub.chat.items[-1][0])

        again = _HandlerStub()
        MainWindow._handle_camera_voice_command(again, "elleri kapat", speak=False)
        self.assertIn("zaten kapalıydı", again.chat.items[-1][0])


class _GestureStub:
    def __init__(self):
        self.toasts = []
        self.palette_calls = 0
        self.fullscreen_calls = 0
        self.disabled = []
        self._camera_manual_off = False

    def _toast(self, text, milliseconds=3600):
        self.toasts.append(text)

    def open_command_palette(self):
        self.palette_calls += 1

    def open_camera_fullscreen(self):
        self.fullscreen_calls += 1
        return True

    def disable_camera_perception(self, quiet=False):
        self.disabled.append(quiet)


class GestureActionTests(unittest.TestCase):
    def test_thumb_up_opens_palette(self):
        stub = _GestureStub()
        MainWindow._on_camera_gesture(stub, "palette")
        self.assertEqual(stub.palette_calls, 1)
        self.assertTrue(stub.toasts)

    def test_victory_opens_fullscreen(self):
        stub = _GestureStub()
        MainWindow._on_camera_gesture(stub, "fullscreen")
        self.assertEqual(stub.fullscreen_calls, 1)

    def test_fist_disables_perception(self):
        stub = _GestureStub()
        MainWindow._on_camera_gesture(stub, "stop")
        self.assertEqual(stub.disabled, [True])
        self.assertTrue(stub._camera_manual_off)

    def test_unknown_gesture_is_ignored(self):
        stub = _GestureStub()
        MainWindow._on_camera_gesture(stub, "wave")
        self.assertEqual(stub.toasts, [])
        self.assertEqual(stub.palette_calls, 0)

    def test_hand_failure_is_reported_as_toast(self):
        stub = _GestureStub()
        MainWindow._on_camera_hand_failed(stub, "model indirilemedi")
        self.assertIn("model indirilemedi", stub.toasts[-1])


class MainWindowCameraHookTests(unittest.TestCase):
    def test_auto_start_runs_silently_and_respects_manual_off(self):
        class Stub:
            _camera_perception = False
            _camera_manual_off = False

            def __init__(self):
                self.started = []

            def enable_camera_perception(self, show=True, quiet=False):
                self.started.append((show, quiet))

        stub = Stub()
        with patch("app.ui.main_window.CameraHub.available", return_value=True), patch(
            "app.ui.main_window.CameraHub.devices", return_value=["Kamera"]
        ):
            MainWindow._auto_start_camera_perception(stub)
        self.assertEqual(stub.started, [(False, True)])

        stub._camera_manual_off = True
        with patch("app.ui.main_window.CameraHub.available", return_value=True), patch(
            "app.ui.main_window.CameraHub.devices", return_value=["Kamera"]
        ):
            MainWindow._auto_start_camera_perception(stub)
        self.assertEqual(stub.started, [(False, True)])

        other = Stub()
        with patch("app.ui.main_window.CameraHub.available", return_value=True), patch(
            "app.ui.main_window.CameraHub.devices", return_value=[]
        ):
            MainWindow._auto_start_camera_perception(other)
        self.assertEqual(other.started, [])

    def test_chat_context_takes_snapshot_without_opening_camera_window(self):
        class Hub:
            def __init__(self):
                self.start_calls = 0

            def is_active(self):
                return False

            def start(self):
                self.start_calls += 1
                return True

        class Window:
            last_scene = "Masada kitap var."

            def __init__(self):
                self.snapshots = 0

            def ensure_visible(self):
                raise AssertionError("Yazılı istekte kamera paneli açılmamalı.")

            def snapshot_for_request(self):
                self.snapshots += 1
                return "C:/tmp/frame.jpg"

        class Stub:
            _camera_perception = True

            def __init__(self):
                self.camera_hub = Hub()
                self._window = Window()

            def _ensure_camera_window(self):
                return self._window

        stub = Stub()
        image, scene = MainWindow._prepare_camera_context(stub)
        self.assertEqual(image, "C:/tmp/frame.jpg")
        self.assertEqual(scene, "Masada kitap var.")
        self.assertEqual(stub._window.snapshots, 1)
        self.assertEqual(stub.camera_hub.start_calls, 1)

        stub._camera_perception = False
        self.assertEqual(MainWindow._prepare_camera_context(stub), (None, ""))


if __name__ == "__main__":
    unittest.main()
