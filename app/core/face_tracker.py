"""Yerel yüz HUD'u; kareler cihazdan çıkmaz, yalnızca kutu koordinatları yayınlanır."""

from __future__ import annotations

import math
import os
import shutil
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage

from app.core.hand_tracker import mediapipe_available

FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
FACE_MODEL_FILENAME = "blaze_face_short_range.tflite"
FACE_MIN_MODEL_BYTES = 100_000
# BlazeFace ~4-16 ms; kameranın her karesini işlemek gecikmeyi kamera hızına indirir.
FACE_INTERVAL_S = 0.015
FACE_PULL_MS = 6
FACE_MAX_FACES = 4
FACE_MIN_BOX = 0.04
FACE_DETECT_WIDTH = 384
# Kutu izleme: yüksek faktör gecikmeyi kısaltır, önizleme zaten yumuşatıyor.
FACE_SMOOTHING = 0.94
FACE_TRACK_TTL_S = 0.15
FACE_MATCH_DISTANCE = 0.32


def face_model_path(data_dir: Path) -> Path:
    return Path(data_dir) / "models" / FACE_MODEL_FILENAME


def ensure_face_model(data_dir: Path, timeout: float = 40.0) -> Path:
    path = face_model_path(data_dir)
    try:
        if path.is_file() and path.stat().st_size >= FACE_MIN_MODEL_BYTES:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    request = urllib.request.Request(FACE_MODEL_URL, headers={"User-Agent": "Jarvis/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if temp.stat().st_size < FACE_MIN_MODEL_BYTES:
            raise OSError("Yüz modeli eksik indi.")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def face_payloads(detections, width: int, height: int, limit: int = FACE_MAX_FACES) -> List[dict]:
    """Algılamaları normalize kutuya çevirir; en büyük yüz odak olarak işaretlenir."""
    if width <= 0 or height <= 0:
        return []
    faces: List[dict] = []
    for detection in detections or []:
        bbox = getattr(detection, "bounding_box", None)
        if bbox is None:
            continue
        try:
            x = float(bbox.origin_x)
            y = float(bbox.origin_y)
            w = float(bbox.width)
            h = float(bbox.height)
        except (AttributeError, TypeError, ValueError):
            continue
        left = min(max(x / width, 0.0), 1.0)
        top = min(max(y / height, 0.0), 1.0)
        right = min(max((x + w) / width, 0.0), 1.0)
        bottom = min(max((y + h) / height, 0.0), 1.0)
        box_width, box_height = right - left, bottom - top
        if box_width < FACE_MIN_BOX or box_height < FACE_MIN_BOX:
            continue
        confidence = 0.0
        categories = getattr(detection, "categories", None) or []
        if categories:
            try:
                confidence = float(getattr(categories[0], "score", 0.0))
            except (AttributeError, TypeError, ValueError):
                confidence = 0.0
        faces.append(
            {
                "box": (
                    round(left, 4),
                    round(top, 4),
                    round(box_width, 4),
                    round(box_height, 4),
                ),
                "confidence": round(min(max(confidence, 0.0), 1.0), 3),
            }
        )
    faces.sort(key=lambda item: item["box"][2] * item["box"][3], reverse=True)
    faces = faces[: max(1, int(limit))]
    for index, face in enumerate(faces):
        face["focus"] = index == 0
    return faces


def _image_to_array(image: QImage, max_width: int = FACE_DETECT_WIDTH):
    """QImage'i modelin beklediği RGB dizisine çevirir; iş birimi GUI dışındadır."""
    if image is None or image.isNull() or max_width <= 0:
        return None
    try:
        import numpy as np
    except Exception:  # noqa: BLE001 - numpy mediapipe ile birlikte gelir
        return None
    width = min(max_width, image.width())
    if width <= 0:
        return None
    # Hızlı (nearest) küçültme yakın plandaki eli tamamen kaybettiriyor; yumuşak ölçek şart.
    scaled = image.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    rgb = scaled.convertToFormat(QImage.Format.Format_RGB888)
    if rgb.isNull() or rgb.width() <= 0 or rgb.height() <= 0:
        return None
    rows = np.frombuffer(rgb.constBits(), dtype=np.uint8).reshape((rgb.height(), rgb.bytesPerLine()))
    return rows[:, : rgb.width() * 3].reshape((rgb.height(), rgb.width(), 3)).copy()


def _center(box) -> tuple[float, float]:
    left, top, width, height = box
    return left + width / 2.0, top + height / 2.0


def _blend_box(previous, detected, factor: float) -> tuple[float, float, float, float]:
    return tuple(
        round(float(old) + (float(new) - float(old)) * factor, 4)
        for old, new in zip(previous, detected)
    )


class FaceTracker(QObject):
    """BlazeFace ile 22 Hz yüz kutuları üretir; kutular izler arasında yumuşatılır."""

    ready = Signal()
    failed = Signal(str)
    faces_ready = Signal(list)
    status = Signal(int, int)

    def __init__(self, data_dir: Optional[Path] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        if data_dir is None:
            data_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis"
        self._data_dir = Path(data_dir)
        self._detector = None
        self._mp = None
        self._ready = False
        self._last_at = 0.0
        self._last_timestamp = 0
        self._tracks: List[dict] = []
        self._fps_at = time.monotonic()
        self._fps_frames = 0
        self._fps = 0
        self._last_status_at = 0.0
        self._mail_lock = threading.Lock()
        self._mail_image: Optional[QImage] = None
        self._pump: Optional[QTimer] = None

    @property
    def is_ready(self) -> bool:
        return bool(self._ready)

    def submit_frame(self, image: QImage) -> None:
        """En güncel kareyi bırakır; iş parçacığı yavaşsa eski kareler kaybolur."""
        if image is None or image.isNull():
            return
        with self._mail_lock:
            self._mail_image = image

    def _take_mail(self) -> Optional[QImage]:
        with self._mail_lock:
            image = self._mail_image
            self._mail_image = None
        return image

    def _pull_latest(self) -> None:
        image = self._take_mail()
        if image is not None:
            self._process(image)

    @Slot()
    def start(self) -> None:
        if not mediapipe_available():
            self.failed.emit("Yüz HUD'u için MediaPipe kurulu değil. 'pip install mediapipe' gerekir.")
            return
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core.base_options import BaseOptions

            model = ensure_face_model(self._data_dir)
            options = vision.FaceDetectorOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.VIDEO,
                min_detection_confidence=0.4,
                min_suppression_threshold=0.3,
            )
            self._mp = mp
            self._detector = vision.FaceDetector.create_from_options(options)
            self._ready = True
            self._last_at = 0.0
            self._last_timestamp = 0
            self._tracks = []
            self._fps_at = time.monotonic()
            self._fps_frames = 0
            if self._pump is None:
                self._pump = QTimer(self)
                self._pump.setInterval(FACE_PULL_MS)
                self._pump.timeout.connect(self._pull_latest)
            self._pump.start()
            self.ready.emit()
        except Exception as error:  # noqa: BLE001 - model/indirme hatası kullanıcıya aktarılır
            self._ready = False
            self.failed.emit(f"Yüz HUD'u başlatılamadı: {error}")

    @Slot(QImage)
    def process_frame(self, image: QImage) -> None:
        self._process(image)

    def _process(self, image: QImage) -> None:
        if not self._ready or self._detector is None:
            return
        now = time.monotonic()
        if now - self._last_at < FACE_INTERVAL_S:
            return
        self._last_at = now
        started = time.perf_counter()
        array = _image_to_array(image)
        if array is None:
            return
        timestamp = max(self._last_timestamp + 1, int(now * 1000))
        self._last_timestamp = timestamp
        try:
            mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=array)
            result = self._detector.detect_for_video(mp_image, timestamp)
        except Exception as error:  # noqa: BLE001 - algılama hatası oturumu düşürmemeli
            self.failed.emit(f"Yüz algılama hatası: {error}")
            return
        detected = face_payloads(result.detections, array.shape[1], array.shape[0])
        self.faces_ready.emit(self._smoothed(detected, now))
        self._publish_status(now, int(round((time.perf_counter() - started) * 1000)))

    def _smoothed(self, detected: List[dict], now: float) -> List[dict]:
        """Tespitleri mevcut izlerle eşler, EMA uygular ve kısa süre kaybolanı korur."""
        unmatched = list(range(len(detected)))
        updated: List[dict] = []
        for track in self._tracks:
            best_index = None
            best_distance = FACE_MATCH_DISTANCE
            track_center = _center(track["box"])
            for index in unmatched:
                detected_center = _center(detected[index]["box"])
                distance = math.dist(track_center, detected_center)
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index is None:
                if now - track["seen_at"] <= FACE_TRACK_TTL_S:
                    updated.append(track)
                continue
            match = detected[best_index]
            unmatched.remove(best_index)
            updated.append(
                {
                    "box": _blend_box(track["box"], match["box"], FACE_SMOOTHING),
                    "confidence": round(
                        track["confidence"] + (match["confidence"] - track["confidence"]) * 0.5, 3
                    ),
                    "seen_at": now,
                }
            )
        for index in unmatched:
            match = detected[index]
            updated.append(
                {
                    "box": tuple(float(value) for value in match["box"]),
                    "confidence": float(match["confidence"]),
                    "seen_at": now,
                }
            )
        updated.sort(key=lambda item: item["box"][2] * item["box"][3], reverse=True)
        updated = updated[:FACE_MAX_FACES]
        self._tracks = updated
        return [
            {
                "box": tuple(round(float(value), 4) for value in track["box"]),
                "confidence": round(min(max(track["confidence"], 0.0), 1.0), 3),
                "focus": index == 0,
            }
            for index, track in enumerate(updated)
        ]

    def _publish_status(self, now: float, latency_ms: int) -> None:
        self._fps_frames += 1
        elapsed = now - self._fps_at
        if elapsed >= 1.0:
            self._fps = int(round(self._fps_frames / elapsed))
            self._fps_frames = 0
            self._fps_at = now
        if now - self._last_status_at >= 0.5:
            self._last_status_at = now
            self.status.emit(self._fps, max(0, int(latency_ms)))

    @Slot()
    def stop(self) -> None:
        self._ready = False
        self._tracks = []
        self._last_timestamp = 0
        if self._pump is not None and QThread.currentThread() is self.thread():
            self._pump.stop()
        with self._mail_lock:
            self._mail_image = None
        if self._detector is not None:
            try:
                self._detector.close()
            except Exception:
                pass
            self._detector = None
        self.faces_ready.emit([])
