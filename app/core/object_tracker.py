"""Yerel nesne/kişi kutuları; kareler cihazdan çıkmaz, yalnızca kutular yayınlanır."""

from __future__ import annotations

import os
import shutil
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage

from app.core.face_tracker import _image_to_array

OBJECT_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/object_detector/"
    "efficientdet_lite0/int8/1/efficientdet_lite0.tflite"
)
OBJECT_MODEL_FILENAME = "efficientdet_lite0.tflite"
OBJECT_MIN_MODEL_BYTES = 4_000_000
# EfficientDet-Lite0 384 px'te ~80 ms; 180 ms taban ~5,5 Hz verir.
# Bulut sahne taraması saniyeler sürerken nesne kutuları anında görünür.
OBJECT_INTERVAL_S = 0.18
OBJECT_PULL_MS = 10
OBJECT_MAX_RESULTS = 6
OBJECT_SCORE_THRESHOLD = 0.35
OBJECT_DETECT_WIDTH = 384
OBJECT_MIN_BOX = 0.03

# Yalnızca sık görülen COCO sınıfları; kalan etiketler İngilizce kalır.
_LABELS_TR = {
    "person": "KİŞİ",
    "bicycle": "BİSİKLET",
    "car": "ARABA",
    "motorcycle": "MOTOSİKLET",
    "airplane": "UÇAK",
    "bus": "OTOBÜS",
    "train": "TREN",
    "truck": "KAMYON",
    "boat": "TEKNE",
    "bench": "BANK",
    "cat": "KEDİ",
    "dog": "KÖPEK",
    "backpack": "SIRT ÇANTASI",
    "umbrella": "ŞEMSİYE",
    "handbag": "ÇANTA",
    "suitcase": "BAVAL",
    "bottle": "ŞİŞE",
    "wine glass": "KADEH",
    "cup": "BARDAK",
    "fork": "ÇATAL",
    "knife": "BIÇAK",
    "spoon": "KAŞIK",
    "bowl": "KASE",
    "banana": "MUZ",
    "apple": "ELMA",
    "sandwich": "SANDVİÇ",
    "orange": "PORTAKAL",
    "pizza": "PİZZA",
    "cake": "PASTA",
    "chair": "SANDALYE",
    "couch": "KANEPE",
    "potted plant": "SAKSI BİTKİSİ",
    "bed": "YATAK",
    "dining table": "MASA",
    "tv": "TELEVİZYON",
    "laptop": "DİZÜSTÜ",
    "mouse": "FARE",
    "remote": "KUMANDA",
    "keyboard": "KLAVYE",
    "cell phone": "TELEFON",
    "microwave": "MİKRODALGA",
    "oven": "FIRIN",
    "sink": "LAVABO",
    "refrigerator": "BUZDOLABI",
    "book": "KİTAP",
    "clock": "SAAT",
    "vase": "VAZO",
    "scissors": "MAKAS",
    "teddy bear": "OYUNCAK AYI",
    "toothbrush": "DİŞ FIRÇASI",
}

# Yüz HUD'u zaten kişiyi işaretliyor; çift kutu çizmemek için atlanır.
_SKIP_LABELS = {"person"}


def object_model_path(data_dir: Path) -> Path:
    return Path(data_dir) / "models" / OBJECT_MODEL_FILENAME


def ensure_object_model(data_dir: Path, timeout: float = 60.0) -> Path:
    path = object_model_path(data_dir)
    try:
        if path.is_file() and path.stat().st_size >= OBJECT_MIN_MODEL_BYTES:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    request = urllib.request.Request(OBJECT_MODEL_URL, headers={"User-Agent": "Jarvis/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if temp.stat().st_size < OBJECT_MIN_MODEL_BYTES:
            raise OSError("Nesne modeli eksik indi.")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def turkish_label(name: str) -> str:
    key = str(name or "").strip()
    if not key:
        return "NESNE"
    return _LABELS_TR.get(key, key.upper())


def object_payloads(
    detections, width: int, height: int, limit: int = OBJECT_MAX_RESULTS
) -> List[dict]:
    """Algılamaları normalize kutuya ve okunur etikete çevirir."""
    if width <= 0 or height <= 0:
        return []
    objects: List[dict] = []
    for detection in detections or []:
        bbox = getattr(detection, "bounding_box", None)
        if bbox is None:
            continue
        categories = getattr(detection, "categories", None) or []
        if not categories:
            continue
        try:
            name = str(getattr(categories[0], "category_name", "") or "").strip()
            score = float(getattr(categories[0], "score", 0.0))
            x = float(bbox.origin_x)
            y = float(bbox.origin_y)
            w = float(bbox.width)
            h = float(bbox.height)
        except (AttributeError, TypeError, ValueError):
            continue
        if name.casefold() in _SKIP_LABELS or score < OBJECT_SCORE_THRESHOLD:
            continue
        left = min(max(x / width, 0.0), 1.0)
        top = min(max(y / height, 0.0), 1.0)
        right = min(max((x + w) / width, 0.0), 1.0)
        bottom = min(max((y + h) / height, 0.0), 1.0)
        box_width, box_height = right - left, bottom - top
        if box_width < OBJECT_MIN_BOX or box_height < OBJECT_MIN_BOX:
            continue
        objects.append(
            {
                "box": (
                    round(left, 4),
                    round(top, 4),
                    round(box_width, 4),
                    round(box_height, 4),
                ),
                "label": turkish_label(name),
                "score": round(min(max(score, 0.0), 1.0), 3),
            }
        )
    objects.sort(key=lambda item: item["box"][2] * item["box"][3], reverse=True)
    return objects[: max(1, int(limit))]


class ObjectTracker(QObject):
    """Kamera karelerinden yerel nesne kutuları üretir; görüntü diske/cloud'a gitmez."""

    ready = Signal()
    failed = Signal(str)
    objects_ready = Signal(list)
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
        self._interval = OBJECT_INTERVAL_S
        self._last_timestamp = 0
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

    def set_interval(self, seconds: float) -> None:
        """El/yüz önceliği için tarama sıklığını ayarlar."""
        try:
            self._interval = max(0.12, min(1.2, float(seconds)))
        except (TypeError, ValueError):
            self._interval = OBJECT_INTERVAL_S

    def submit_frame(self, image: QImage) -> None:
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
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core.base_options import BaseOptions

            model = ensure_object_model(self._data_dir)
            options = vision.ObjectDetectorOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.VIDEO,
                max_results=OBJECT_MAX_RESULTS,
                score_threshold=OBJECT_SCORE_THRESHOLD,
            )
            self._mp = mp
            self._detector = vision.ObjectDetector.create_from_options(options)
            self._ready = True
            self._fps_at = time.monotonic()
            self._fps_frames = 0
            if self._pump is None:
                self._pump = QTimer(self)
                self._pump.setInterval(OBJECT_PULL_MS)
                self._pump.timeout.connect(self._pull_latest)
            self._pump.start()
            self.ready.emit()
        except Exception as error:  # noqa: BLE001 - model/indirme hatası sessizce HUD'ı kapatır
            self._ready = False
            self.failed.emit(f"Nesne HUD'u başlatılamadı: {error}")

    @Slot(QImage)
    def process_frame(self, image: QImage) -> None:
        self._process(image)

    def _process(self, image: QImage) -> None:
        if not self._ready or self._detector is None:
            return
        now = time.monotonic()
        if now - self._last_at < self._interval:
            return
        self._last_at = now
        started = time.perf_counter()
        array = _image_to_array(image, OBJECT_DETECT_WIDTH)
        if array is None:
            return
        timestamp = max(self._last_timestamp + 1, int(now * 1000))
        self._last_timestamp = timestamp
        try:
            mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=array)
            result = self._detector.detect_for_video(mp_image, timestamp)
        except Exception as error:  # noqa: BLE001 - algılama hatası oturumu düşürmemeli
            self.failed.emit(f"Nesne algılama hatası: {error}")
            return
        objects = object_payloads(result.detections, array.shape[1], array.shape[0])
        self.objects_ready.emit(objects)
        self._publish_status(now, len(objects), int(round((time.perf_counter() - started) * 1000)))

    def _publish_status(self, now: float, count: int, latency_ms: int) -> None:
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
        self.objects_ready.emit([])
