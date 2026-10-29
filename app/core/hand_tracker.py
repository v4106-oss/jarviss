"""Yerel el iskeleti takibi ve jest sınıflandırması; kareler cihazdan çıkmaz."""

from __future__ import annotations

import importlib.util
import os
import shutil
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MODEL_FILENAME = "hand_landmarker.task"
# Ölçüm: model ~26-32 ms. Gelen en güncel kare işlenir; eski kare kuyruğu oluşmaz.
HAND_INTERVAL_S = 0.015
HAND_PULL_MS = 6
HAND_MAX_WIDTH = 320
HAND_MIN_MODEL_BYTES = 1_000_000
# Yüz/arka plan parçaları el sanılabiliyor; skor, avuç boyu ve erişim oranıyla elenir.
HAND_MIN_SCORE = 0.5
# Tek karelik yanlış tespitler HUD'a ulaşmasın; ikinci kare eli onaylar.
HAND_CONFIRM_FRAMES = 2
# El bir kez oturduysa tek kare yeter; HUD her karede yeniden onay bekleyip gecikmesin.
HAND_RECONFIRM_S = 0.6
# El kaybolunca iskelet 0.3 sn asılı kalıyordu; kısa tutulur ki HUD hemen temizlensin.
HAND_HOLD_S = 0.15
HAND_MIN_PALM = 0.035
HAND_MIN_REACH_RATIO = 0.4
HAND_MAX_REACH_RATIO = 3.2

_MEDIAPIPE: object = None

_FINGER_TIPS = (4, 8, 12, 16, 20)
_FINGER_PIPS = (2, 6, 10, 14, 18)


def mediapipe_installed() -> bool:
    """Paket kurulu mu? Sadece find_spec; ağır mediapipe import'u GUI'yi kilitlemesin."""
    try:
        return importlib.util.find_spec("mediapipe") is not None
    except (ImportError, ValueError):
        return False


def mediapipe_available() -> bool:
    global _MEDIAPIPE
    if _MEDIAPIPE is None:
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import vision

            _MEDIAPIPE = (mp, vision)
        except Exception:  # noqa: BLE001 - isteğe bağlı görüntü bağımlılığı
            _MEDIAPIPE = False
    return bool(_MEDIAPIPE)


def hand_model_path(data_dir: Path) -> Path:
    return Path(data_dir) / "models" / MODEL_FILENAME


def ensure_hand_model(data_dir: Path, timeout: float = 40.0) -> Path:
    path = hand_model_path(data_dir)
    try:
        if path.is_file() and path.stat().st_size >= HAND_MIN_MODEL_BYTES:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Jarvis/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if temp.stat().st_size < HAND_MIN_MODEL_BYTES:
            raise OSError("El modeli eksik indi.")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def _point(landmarks: Sequence, index: int) -> tuple[float, float, float]:
    landmark = landmarks[index]
    if hasattr(landmark, "x"):
        return float(landmark.x), float(landmark.y), float(getattr(landmark, "z", 0.0))
    values = list(landmark)
    return float(values[0]), float(values[1]), float(values[2]) if len(values) > 2 else 0.0


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def palm_point(landmarks: Sequence) -> tuple[float, float]:
    wrist = _point(landmarks, 0)
    middle = _point(landmarks, 9)
    return (wrist[0] + middle[0]) / 2, (wrist[1] + middle[1]) / 2


def classify_hand(landmarks: Sequence, handedness: str = "") -> str:
    """21 noktadan açık el, yumruk, çimdik, işaret, zafer veya başparmak seçer."""
    points = [_point(landmarks, index) for index in range(21)]
    wrist = points[0]
    palm_size = max(_distance(wrist, points[9]), 1e-5)
    extended: list[bool] = []
    for tip, pip in zip(_FINGER_TIPS, _FINGER_PIPS):
        tip_distance = _distance(wrist, points[tip])
        pip_distance = _distance(wrist, points[pip])
        threshold = 1.05 if tip == 4 else 1.12
        extended.append(tip_distance > pip_distance * threshold)

    pinch_ratio = _distance(points[4], points[8]) / palm_size
    if pinch_ratio < 0.3:
        return "pinch"
    index, middle, ring, pinky = extended[1:]
    if index and middle and ring and pinky:
        return "open"
    if not any((index, middle, ring, pinky)):
        # Kalp işaretinde başparmak ucu işaret ucuna değer; gerçek thumbs-up'ta uzaktır.
        tips_close = _distance(points[4], points[8]) < palm_size * 0.55
        thumb_up = (
            extended[0]
            and not tips_close
            and points[4][1] < wrist[1] - palm_size * 0.35
        )
        if thumb_up:
            return "thumb_up"
        # Uçları birleşik açık başparmak yumruk sayılıp durdurma komutunu tetiklemesin.
        return "none" if tips_close and extended[0] else "fist"
    if index and middle and not ring and not pinky:
        return "victory"
    if index and not middle and not ring and not pinky:
        return "point"
    return "none"


def hand_plausible(landmarks: Sequence, score: float = 1.0) -> bool:
    """Düşük güvenli veya avuç geometrisi bozuk tespitleri eler."""
    try:
        points = [_point(landmarks, index) for index in range(21)]
        score = float(score)
    except (IndexError, TypeError, ValueError):
        return False
    if score < HAND_MIN_SCORE:
        return False
    palm = max(_distance(points[0], points[9]), 1e-5)
    if palm < HAND_MIN_PALM:
        return False
    reach = max(_distance(points[0], point) for point in points)
    ratio = reach / palm
    return HAND_MIN_REACH_RATIO <= ratio <= HAND_MAX_REACH_RATIO


def hand_payload(landmarks: Sequence, handedness: str = "", score: float = 1.0) -> dict:
    points = [tuple(round(value, 5) for value in _point(landmarks, index)) for index in range(21)]
    pinch_ratio = _distance(points[4], points[8]) / max(_distance(points[0], points[9]), 1e-5)
    return {
        "landmarks": points,
        "gesture": classify_hand(points, handedness),
        "pinch": round(min(1.0, pinch_ratio), 4),
        "palm": palm_point(points),
        "handedness": str(handedness or ""),
        "score": round(min(max(float(score), 0.0), 1.0), 3),
    }


def _image_to_array(image: QImage):
    if image is None or image.isNull():
        return None
    try:
        import numpy as np
    except Exception:  # noqa: BLE001 - numpy mediapipe ile birlikte gelir
        return None
    width = min(HAND_MAX_WIDTH, image.width())
    if width <= 0:
        return None
    # Hızlı (nearest) küçültme yakın plandaki eli tamamen kaybettiriyor; yumuşak ölçek şart.
    scaled = image.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    rgb = scaled.convertToFormat(QImage.Format.Format_RGB888)
    if rgb.isNull() or rgb.width() <= 0 or rgb.height() <= 0:
        return None
    pointer = rgb.constBits()
    rows = np.frombuffer(pointer, dtype=np.uint8).reshape((rgb.height(), rgb.bytesPerLine()))
    return rows[:, : rgb.width() * 3].reshape((rgb.height(), rgb.width(), 3)).copy()


class HandTracker(QObject):
    """Kamera karelerini arka planda işler; yalnızca iskelet koordinatları yayınlar."""

    ready = Signal()
    failed = Signal(str)
    hands_ready = Signal(list)
    status = Signal(int, int)
    gesture_triggered = Signal(str)

    HOLD_GESTURES = {
        "thumb_up": ("palette", 1.0),
        "victory": ("fullscreen", 0.9),
        "fist": ("stop", 1.8),
    }

    def __init__(self, data_dir: Optional[Path] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        if data_dir is None:
            data_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis"
        self._data_dir = Path(data_dir)
        self._landmarker = None
        self._mp = None
        self._ready = False
        self._last_at = 0.0
        self._last_timestamp = 0
        self._fps_at = time.monotonic()
        self._fps_frames = 0
        self._fps = 0
        self._last_status_at = 0.0
        self._hold_action: Optional[str] = None
        self._hold_started = 0.0
        self._hold_fired = False
        self._mail_lock = threading.Lock()
        self._mail_image: Optional[QImage] = None
        self._pump: Optional[QTimer] = None
        self._hand_streak = 0
        self._last_good_hands: list[dict] = []
        self._last_good_at = 0.0
        self._confirmed_at = 0.0

    @property
    def is_ready(self) -> bool:
        return bool(self._ready)

    def submit_frame(self, image: QImage) -> None:
        """GUI iş parçacığından en güncel kareyi bırakır; eski kareler birikmez."""
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
            self.failed.emit("El kontrolü için MediaPipe kurulu değil. 'pip install mediapipe' gerekir.")
            return
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core.base_options import BaseOptions

            model = ensure_hand_model(self._data_dir)
            options = vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=2,
                min_hand_detection_confidence=0.55,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._mp = mp
            self._landmarker = vision.HandLandmarker.create_from_options(options)
            self._ready = True
            self._fps_at = time.monotonic()
            self._fps_frames = 0
            self._hand_streak = 0
            self._last_good_hands = []
            self._last_good_at = 0.0
            self._confirmed_at = 0.0
            if self._pump is None:
                self._pump = QTimer(self)
                self._pump.setInterval(HAND_PULL_MS)
                self._pump.timeout.connect(self._pull_latest)
            self._pump.start()
            self.ready.emit()
        except Exception as error:  # noqa: BLE001 - model/indirme hatası kullanıcıya aktarılır
            self._ready = False
            self.failed.emit(f"El kontrolü başlatılamadı: {error}")

    @Slot(QImage)
    def process_frame(self, image: QImage) -> None:
        self._process(image)

    def _process(self, image: QImage) -> None:
        if not self._ready or self._landmarker is None:
            return
        now = time.monotonic()
        if now - self._last_at < HAND_INTERVAL_S:
            return
        self._last_at = now
        array = _image_to_array(image)
        if array is None:
            return
        timestamp = max(self._last_timestamp + 1, int(now * 1000))
        self._last_timestamp = timestamp
        try:
            mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=array)
            result = self._landmarker.detect_for_video(mp_image, timestamp)
        except Exception as error:  # noqa: BLE001 - takip hatası oturumu düşürmemeli
            self.failed.emit(f"El takibi hatası: {error}")
            return

        hands: list[dict] = []
        for index, landmarks in enumerate(result.hand_landmarks or []):
            handedness = ""
            score = 1.0
            try:
                category = result.handedness[index][0]
                handedness = str(category.category_name or "")
                score = float(getattr(category, "score", 1.0))
            except (AttributeError, IndexError, TypeError, ValueError):
                pass
            if not hand_plausible(landmarks, score):
                continue
            hands.append(hand_payload(landmarks, handedness, score))
        hands = self._confirmed(hands, now)
        self.hands_ready.emit(hands)
        self._update_hold(hands[0]["gesture"] if hands else None, now)
        self._publish_status(now, len(hands))

    def _confirmed(self, hands: list[dict], now: float) -> list[dict]:
        """Soğukta iki kare onay ister; el oturmuşsa tek kare HUD'a hemen yansır."""
        if hands:
            self._hand_streak += 1
            self._last_good_hands = hands
            self._last_good_at = now
            if self._hand_streak >= HAND_CONFIRM_FRAMES or now - self._confirmed_at <= HAND_RECONFIRM_S:
                self._confirmed_at = now
                return hands
            return []
        self._hand_streak = 0
        if self._last_good_hands and now - self._last_good_at <= HAND_HOLD_S:
            return self._last_good_hands
        self._last_good_hands = []
        return []

    def _publish_status(self, now: float, count: int) -> None:
        self._fps_frames += 1
        elapsed = now - self._fps_at
        if elapsed >= 1.0:
            self._fps = int(round(self._fps_frames / elapsed))
            self._fps_frames = 0
            self._fps_at = now
        if now - self._last_status_at >= 0.25:
            self._last_status_at = now
            self.status.emit(self._fps, count)

    def _update_hold(self, gesture: Optional[str], now: float) -> None:
        mapping = self.HOLD_GESTURES.get(gesture or "")
        if mapping is None:
            self._hold_action = None
            self._hold_fired = False
            return
        action, seconds = mapping
        if action != self._hold_action:
            self._hold_action = action
            self._hold_started = now
            self._hold_fired = False
            return
        if not self._hold_fired and now - self._hold_started >= seconds:
            self._hold_fired = True
            self.gesture_triggered.emit(action)

    @Slot()
    def stop(self) -> None:
        self._ready = False
        self._hold_action = None
        self._hold_fired = False
        self._hand_streak = 0
        self._last_good_hands = []
        self._last_good_at = 0.0
        self._confirmed_at = 0.0
        if self._pump is not None and QThread.currentThread() is self.thread():
            self._pump.stop()
        with self._mail_lock:
            self._mail_image = None
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
            self._landmarker = None
        self.hands_ready.emit([])
