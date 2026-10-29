"""Tek kamera örneğini paylaşan motor ve ortak kamera önizlemesi."""

from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from app.theme import ultron as U
from .hand_effects import HandEffects
from .hud_widgets import hud_font

try:
    from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink

    CAMERA_AVAILABLE = True
except Exception:  # noqa: BLE001 - QtMultimedia bazı kurulumlarda yok
    CAMERA_AVAILABLE = False


class CameraHub(QObject):
    """Kamera cihazını tek noktadan yönetir; kitap penceresi ve canlı algı paylaşır."""

    frame_ready = Signal(QImage)
    active_changed = Signal(bool)
    error = Signal(str)

    LIVE_INTERVAL_S = 0.0
    IDLE_INTERVAL_S = 0.5
    TARGET_WIDTH = 1280
    TARGET_HEIGHT = 720
    MAX_WIDTH = 1280
    MAX_HEIGHT = 720
    MIN_WIDTH = 640
    MIN_HEIGHT = 360
    MIN_FPS = 24

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._camera = None
        self._session = None
        self._sink = None
        self._latest: Optional[QImage] = None
        self._device_index = 0
        self._starting = False
        self._frame_interval = self.LIVE_INTERVAL_S
        self._last_frame_at = 0.0
        self._received = 0
        self._delivered = 0

    @staticmethod
    def available() -> bool:
        return CAMERA_AVAILABLE

    @staticmethod
    def devices() -> List[str]:
        if not CAMERA_AVAILABLE:
            return []
        try:
            return [device.description() or "Kamera" for device in QMediaDevices.videoInputs()]
        except Exception:
            return []

    def is_active(self) -> bool:
        try:
            return bool(self._camera is not None and self._camera.isActive())
        except Exception:
            return False

    def latest_image(self) -> Optional[QImage]:
        return self._latest.copy() if self._latest is not None and not self._latest.isNull() else None

    def set_preview_active(self, active: bool) -> None:
        """Panel görünürken kameranın kendi hızında, gizliyken seyrek kare üretir."""
        self._frame_interval = self.LIVE_INTERVAL_S if active else self.IDLE_INTERVAL_S

    def set_device_index(self, index: int) -> None:
        try:
            self._device_index = max(0, int(index))
        except (TypeError, ValueError):
            self._device_index = 0

    @classmethod
    def _preferred_format(cls, device):
        """1280×720/30 fps'i hedefler; yoksa en yakın 16:9 formata düşer."""
        try:
            formats = list(device.videoFormats())
        except Exception:
            return None
        best = None
        for video_format in formats:
            try:
                size = video_format.resolution()
                fps = float(video_format.maxFrameRate())
            except Exception:
                continue
            width, height = size.width(), size.height()
            if width < cls.MIN_WIDTH or height < cls.MIN_HEIGHT or fps < cls.MIN_FPS:
                continue
            if width > cls.MAX_WIDTH or height > cls.MAX_HEIGHT:
                continue
            exact = width == cls.TARGET_WIDTH and height == cls.TARGET_HEIGHT
            sixteen_nine = abs(width / max(1, height) - 16 / 9) < 0.02
            near = -abs(width - cls.TARGET_WIDTH) - abs(height - cls.TARGET_HEIGHT)
            key = (exact, sixteen_nine, min(fps, 30.0), near)
            if best is None or key > best[0]:
                best = (key, video_format)
        return best[1] if best else None

    def start(self, index: int = -1) -> bool:
        if not CAMERA_AVAILABLE:
            self.error.emit("QtMultimedia bulunamadı; kamera kullanılamıyor.")
            return False
        if index >= 0:
            self._device_index = int(index)
        if self.is_active():
            return True
        if self._starting:
            return False
        self._starting = True
        self._teardown()
        try:
            devices = list(QMediaDevices.videoInputs())
            if not devices:
                self.error.emit("Kamera bulunamadı.")
                return False
            self._device_index = max(0, min(self._device_index, len(devices) - 1))
            self._camera = QCamera(devices[self._device_index], self)
            preferred = self._preferred_format(devices[self._device_index])
            if preferred is not None:
                try:
                    self._camera.setCameraFormat(preferred)
                except Exception:
                    pass
            self._session = QMediaCaptureSession(self)
            self._sink = QVideoSink(self)
            self._session.setCamera(self._camera)
            self._session.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._on_frame)
            self._camera.errorOccurred.connect(self._on_error)
            self._camera.activeChanged.connect(self._on_active)
            self._received = 0
            self._delivered = 0
            self._last_frame_at = 0.0
            self._camera.start()
            return True
        except Exception as error:  # noqa: BLE001 - sürücü hatası kullanıcıya aktarılır
            self.error.emit(f"Kamera başlatılamadı: {error}")
            self._teardown()
            return False
        finally:
            self._starting = False

    def restart(self, index: int = 0) -> bool:
        self.stop()
        return self.start(index)

    def stop(self) -> None:
        was_active = self.is_active()
        try:
            if self._camera is not None:
                self._camera.stop()
        except Exception:
            pass
        self._teardown()
        self._latest = None
        if was_active:
            self.active_changed.emit(False)

    def _teardown(self) -> None:
        if self._sink is not None:
            try:
                self._sink.videoFrameChanged.disconnect(self._on_frame)
            except Exception:
                pass
        for obj in (self._session, self._sink, self._camera):
            if obj is not None:
                try:
                    obj.deleteLater()
                except Exception:
                    pass
        self._camera = None
        self._session = None
        self._sink = None

    def _on_frame(self, frame) -> None:
        self._received += 1
        now = time.monotonic()
        if now - self._last_frame_at < self._frame_interval:
            return
        self._last_frame_at = now
        try:
            if not frame.isValid():
                return
            image = frame.toImage()
        except Exception:
            return
        if image.isNull():
            return
        # QImage örtük paylaşımlıdır; kopyalamadan iletmek CPU'yu düşürür.
        self._latest = image
        self._delivered += 1
        self.frame_ready.emit(image)

    def stats(self) -> dict:
        return {"received": self._received, "delivered": self._delivered, "interval_ms": round(self._frame_interval * 1000)}

    def _on_active(self, active: bool) -> None:
        self.active_changed.emit(bool(active))

    def _on_error(self, *args) -> None:
        message = str(args[-1]) if args else "bilinmeyen kamera hatası"
        self.error.emit(message)


class CameraPreview(QWidget):
    """Kamera karesini orana sığdırır; yüz kutularını 60 Hz'de yumuşatarak çizer."""

    activated = Signal()
    HUD_SHIFT_LIMIT = 0.22
    FACE_SHIFT_LIMIT = 0.12
    # Kutular hedefe ~60-90 ms'de oturur; eski değerler kafa hareketini geç hissettiriyordu.
    FACE_EASE = 60.0
    HUD_EASE = 55.0
    HUD_FADE_PER_S = 18.0
    SCAN_TRAIL_LEN = 24

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self._image: Optional[QImage] = None
        self._scaled: Optional[QImage] = None
        self._scaled_key: tuple = ()
        self._message = "KAMERA HAZIRLANIYOR…"
        self._scanning = False
        self._analyzing = False
        self._scan_time = 0.0
        self._scan_trail: List[QPointF] = []
        self._scan_pings: List[dict] = []
        self._next_ping = 0.8
        self._hud_boxes: List[Tuple[str, str, QRectF]] = []
        self._hud_render: List[dict] = []
        self._local_boxes: List[Tuple[str, str, QRectF]] = []
        self._local_render: List[dict] = []
        self._hud_shift = QPointF(0.0, 0.0)
        self._hud_set_at = 0.0
        self._face_boxes: List[dict] = []
        self._face_render: List[dict] = []
        self._face_enabled = False
        self._face_message = ""
        self._face_shift = QPointF(0.0, 0.0)
        self._face_fps = 0
        self._face_latency_ms = 0
        self._cinematic = False
        self._effects = HandEffects()
        self._hand_enabled = False
        self._hand_fps = 0
        self._hand_count = 0
        self._hand_message = ""
        self._last_tick_at = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_frame(self, image: Optional[QImage]) -> None:
        self._image = image if image is not None and not image.isNull() else None
        if self._image is not None:
            self._message = ""
        self._scaled = None
        self._scaled_key = ()
        if self.isVisible():
            self.update()

    def resizeEvent(self, event) -> None:
        self._scaled = None
        self._scaled_key = ()
        super().resizeEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mouseDoubleClickEvent(event)

    def set_cinematic(self, enabled: bool) -> None:
        self._cinematic = bool(enabled)
        self.update()

    def set_message(self, text: str) -> None:
        self._message = str(text or "")
        self.update()

    def image(self) -> Optional[QImage]:
        return self._image

    def set_scanning(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled and self._scanning:
            return  # kesintisiz taramada probun izi sıfırlanmasın
        self._scanning = enabled
        self._scan_time = 0.0
        self._scan_trail = []
        self._scan_pings = []
        self._next_ping = 0.8
        self._sync_animation()
        self.update()

    def _scan_point(self, t: float) -> QPointF:
        """Lissajous izi: frekanslar oransız, aynı desen sürekli tekrarlanmaz."""
        x = 0.5 + 0.33 * math.sin(t * 0.71 + 0.4) * math.cos(t * 0.23 + 1.1)
        y = 0.5 + 0.29 * math.sin(t * 1.17 + 1.9)
        return QPointF(min(0.9, max(0.1, x)), min(0.9, max(0.1, y)))

    def _advance_scan(self) -> None:
        pace = 0.05 if self._analyzing else 0.034
        breathe = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._scan_time * 0.19 + 0.4))
        self._scan_time += pace * breathe
        point = self._scan_point(self._scan_time)
        self._scan_trail.append(point)
        if len(self._scan_trail) > self.SCAN_TRAIL_LEN:
            del self._scan_trail[:-self.SCAN_TRAIL_LEN]
        self._next_ping -= pace
        if self._next_ping <= 0.0:
            self._scan_pings.append({"pos": QPointF(point), "radius": 2.0, "life": 1.0})
            cycle = 0.5 + 0.5 * math.sin(self._scan_time * 0.7 + 1.3)
            self._next_ping = 1.0 + 1.0 * cycle
        for ping in self._scan_pings:
            ping["radius"] += pace * 85.0
            ping["life"] -= pace * 0.62
        self._scan_pings = [ping for ping in self._scan_pings if ping["life"] > 0.03]

    def set_analyzing(self, enabled: bool) -> None:
        self._analyzing = bool(enabled)
        self.update()

    def set_hud_boxes(self, boxes) -> None:
        cleaned: List[Tuple[str, str, QRectF]] = []
        for item in boxes or []:
            try:
                label, kind, rect = item
            except (TypeError, ValueError):
                continue
            if not isinstance(rect, QRectF):
                continue
            if rect.width() <= 0.01 or rect.height() <= 0.01:
                continue
            cleaned.append((str(label)[:24], str(kind), rect))
        self._hud_boxes = cleaned[:8]
        self._retarget_hud_boxes()
        self._hud_shift = QPointF(0.0, 0.0)
        self._hud_set_at = time.monotonic()
        self._sync_animation()
        self.update()

    def _retarget_hud_boxes(self) -> None:
        """Yeni tespitle eşleşen kutuları yerinden sıçratmadan yeni hedefe kaydırır."""
        previous = list(self._hud_render)
        used = set()
        render: List[dict] = []
        for label, kind, target in self._hud_boxes:
            best_index = None
            best_distance = 0.36
            center = target.center()
            for index, old in enumerate(previous):
                if index in used or old["kind"] != kind:
                    continue
                old_center = old["target"].center()
                distance = math.hypot(old_center.x() - center.x(), old_center.y() - center.y())
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index is None:
                current = QRectF(target)
                alpha = 0.0
            else:
                used.add(best_index)
                current = QRectF(previous[best_index]["box"])
                alpha = float(previous[best_index]["alpha"])
            render.append(
                {
                    "label": label,
                    "kind": kind,
                    "box": current,
                    "target": QRectF(target),
                    "alpha": alpha,
                }
            )
        self._hud_render = render

    def _hud_animating(self) -> bool:
        for item in self._hud_render:
            if item["alpha"] < 0.995:
                return True
            current: QRectF = item["box"]
            target: QRectF = item["target"]
            if (
                abs(current.x() - target.x()) > 0.0015
                or abs(current.y() - target.y()) > 0.0015
                or abs(current.width() - target.width()) > 0.0015
                or abs(current.height() - target.height()) > 0.0015
            ):
                return True
        return False

    def clear_hud_boxes(self) -> None:
        self._hud_shift = QPointF(0.0, 0.0)
        if not self._hud_boxes:
            self._hud_render = []
            return
        self._hud_boxes = []
        self._hud_render = []
        self._hud_set_at = 0.0
        self.update()

    def nudge_hud_boxes(self, dx: float, dy: float) -> None:
        """Yapay zekâ yenilemeleri arasında kutuları kare hareketiyle kaydırır."""
        if not self._hud_boxes:
            return
        limit = self.HUD_SHIFT_LIMIT
        x = min(limit, max(-limit, self._hud_shift.x() + float(dx)))
        y = min(limit, max(-limit, self._hud_shift.y() + float(dy)))
        self._hud_shift = QPointF(x, y)
        self.update()

    def set_local_boxes(self, boxes) -> None:
        """Yerel dedektör kutuları; bulut kaymasından bağımsız, kendi katmanında."""
        cleaned: List[Tuple[str, str, QRectF]] = []
        for item in boxes or []:
            try:
                label, kind, rect = item
            except (TypeError, ValueError):
                continue
            if not isinstance(rect, QRectF):
                continue
            if rect.width() <= 0.01 or rect.height() <= 0.01:
                continue
            cleaned.append((str(label)[:20], str(kind), rect))
        self._local_boxes = cleaned[:6]
        self._retarget_local_boxes()
        self._sync_animation()
        self.update()

    def _retarget_local_boxes(self) -> None:
        previous = list(self._local_render)
        used = set()
        render: List[dict] = []
        for label, kind, target in self._local_boxes:
            best_index = None
            best_distance = 0.3
            center = target.center()
            for index, old in enumerate(previous):
                if index in used:
                    continue
                old_center = old["target"].center()
                distance = math.hypot(old_center.x() - center.x(), old_center.y() - center.y())
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index is None:
                current = QRectF(target)
                alpha = 0.0
            else:
                used.add(best_index)
                current = QRectF(previous[best_index]["box"])
                alpha = float(previous[best_index]["alpha"])
            render.append(
                {
                    "label": label,
                    "kind": kind,
                    "box": current,
                    "target": QRectF(target),
                    "alpha": alpha,
                }
            )
        self._local_render = render
        self._last_tick_at = time.monotonic()

    def clear_local_boxes(self) -> None:
        if not self._local_boxes and not self._local_render:
            return
        self._local_boxes = []
        self._local_render = []
        self.update()

    def _local_moving(self) -> bool:
        for item in self._local_render:
            if item["alpha"] < 0.995:
                return True
            current: QRectF = item["box"]
            target: QRectF = item["target"]
            if (
                abs(current.x() - target.x()) > 0.0015
                or abs(current.y() - target.y()) > 0.0015
                or abs(current.width() - target.width()) > 0.0015
                or abs(current.height() - target.height()) > 0.0015
            ):
                return True
        return False

    def set_effects_enabled(self, enabled: bool) -> None:
        self._hand_enabled = bool(enabled)
        self._effects.set_enabled(self._hand_enabled)
        if not self._hand_enabled:
            self._hand_count = 0
            self._hand_fps = 0
            self._hand_message = ""
        self._sync_animation()
        self.update()

    def set_hands(self, hands) -> None:
        if not self._hand_enabled:
            return
        self._effects.update_hands(hands)
        self._hand_count = len(self._effects.hands)
        self._hand_message = ""
        self._sync_animation()
        self.update()

    def set_hand_status(self, fps: int, count: int) -> None:
        if not self._hand_enabled:
            return
        self._hand_fps = max(0, int(fps))
        self._hand_count = max(0, int(count))
        self.update()

    def set_hand_message(self, text: str) -> None:
        self._hand_message = str(text or "")
        self.update()

    def hand_effects_active(self) -> bool:
        return self._hand_enabled and self._effects.is_active()

    def set_face_enabled(self, enabled: bool, message: str = "") -> None:
        self._face_enabled = bool(enabled)
        self._face_message = str(message or "") if enabled else ""
        if not enabled:
            self._face_boxes = []
            self._face_render = []
            self._face_shift = QPointF(0.0, 0.0)
            self._face_fps = 0
            self._face_latency_ms = 0
        self._sync_animation()
        self.update()

    def set_faces(self, faces) -> None:
        cleaned: List[dict] = []
        focus_seen = False
        for item in faces or []:
            if not isinstance(item, dict):
                continue
            box = item.get("box")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                left, top, width, height = (float(value) for value in box)
                confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
            except (TypeError, ValueError):
                continue
            left = min(max(left, 0.0), 1.0)
            top = min(max(top, 0.0), 1.0)
            width = min(max(width, 0.0), 1.0 - left)
            height = min(max(height, 0.0), 1.0 - top)
            if width < 0.02 or height < 0.02:
                continue
            focus = bool(item.get("focus")) and not focus_seen
            focus_seen = focus_seen or focus
            cleaned.append(
                {
                    "box": QRectF(left, top, width, height),
                    "confidence": confidence,
                    "focus": focus,
                }
            )
        if cleaned and not focus_seen:
            cleaned[0]["focus"] = True
        self._face_boxes = cleaned[:4]
        self._face_enabled = True
        if cleaned:
            self._face_message = ""
        self._retarget_faces()
        self._sync_animation()
        self.update()

    def _retarget_faces(self) -> None:
        """Yeni tespiti mevcut çizim kutularıyla eşler; görünür kutu sıçramaz."""
        previous = list(self._face_render)
        used = set()
        render: List[dict] = []
        for target in self._face_boxes:
            target_box: QRectF = target["box"]
            best_index = None
            best_distance = 0.4
            target_center = target_box.center()
            for index, old in enumerate(previous):
                if index in used:
                    continue
                old_center = old["box"].center()
                distance = math.hypot(
                    old_center.x() - target_center.x(), old_center.y() - target_center.y()
                )
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index is None:
                current = QRectF(target_box)
            else:
                used.add(best_index)
                current = QRectF(previous[best_index]["box"])
            render.append(
                {
                    "box": current,
                    "target": QRectF(target_box),
                    "confidence": target["confidence"],
                    "focus": target["focus"],
                }
            )
        self._face_render = render
        self._face_shift = QPointF(0.0, 0.0)
        self._last_tick_at = time.monotonic()

    def clear_faces(self) -> None:
        self._face_shift = QPointF(0.0, 0.0)
        self._face_fps = 0
        self._face_latency_ms = 0
        if not self._face_boxes and not self._face_render:
            return
        self._face_boxes = []
        self._face_render = []
        self.update()

    def nudge_face_boxes(self, dx: float, dy: float) -> None:
        """Geriye dönük uyumluluk: yumuşatma kapalıyken kutuları kaydırır."""
        if not self._face_boxes:
            return
        limit = self.FACE_SHIFT_LIMIT
        x = min(limit, max(-limit, self._face_shift.x() + float(dx)))
        y = min(limit, max(-limit, self._face_shift.y() + float(dy)))
        self._face_shift = QPointF(x, y)
        self.update()

    def set_face_status(self, fps: int, latency_ms: int) -> None:
        self._face_fps = max(0, int(fps))
        self._face_latency_ms = max(0, int(latency_ms))
        self.update()

    def _face_moving(self) -> bool:
        for item in self._face_render:
            current: QRectF = item["box"]
            target: QRectF = item["target"]
            if (
                abs(current.x() - target.x()) > 0.0005
                or abs(current.y() - target.y()) > 0.0005
                or abs(current.width() - target.width()) > 0.0005
                or abs(current.height() - target.height()) > 0.0005
            ):
                return True
        return False

    def _sync_animation(self) -> None:
        active = (
            self._scanning
            or self._cinematic
            or self._effects.is_active()
            or bool(self._face_render)
            or bool(self._local_render)
            or self._hud_animating()
        )
        if active and self.isVisible():
            if self._face_render or self._local_render:
                interval = 16
            elif self._scanning or self._effects.is_active():
                interval = 33
            else:
                interval = 50
            self._timer.setInterval(interval)
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, max(0.001, now - self._last_tick_at))
        self._last_tick_at = now
        if self._face_render:
            alpha = min(0.72, max(0.18, 1.0 - math.exp(-dt * self.FACE_EASE)))
            for item in self._face_render:
                current: QRectF = item["box"]
                target: QRectF = item["target"]
                item["box"] = QRectF(
                    current.x() + (target.x() - current.x()) * alpha,
                    current.y() + (target.y() - current.y()) * alpha,
                    current.width() + (target.width() - current.width()) * alpha,
                    current.height() + (target.height() - current.height()) * alpha,
                )
        if self._hud_render:
            hud_alpha = min(0.82, max(0.24, 1.0 - math.exp(-dt * self.HUD_EASE)))
            for item in self._hud_render:
                current = item["box"]
                target = item["target"]
                item["box"] = QRectF(
                    current.x() + (target.x() - current.x()) * hud_alpha,
                    current.y() + (target.y() - current.y()) * hud_alpha,
                    current.width() + (target.width() - current.width()) * hud_alpha,
                    current.height() + (target.height() - current.height()) * hud_alpha,
                )
                if item["alpha"] < 1.0:
                    item["alpha"] = min(1.0, item["alpha"] + dt * self.HUD_FADE_PER_S)
        if self._local_render:
            local_alpha = min(0.8, max(0.22, 1.0 - math.exp(-dt * self.FACE_EASE)))
            for item in self._local_render:
                current = item["box"]
                target = item["target"]
                item["box"] = QRectF(
                    current.x() + (target.x() - current.x()) * local_alpha,
                    current.y() + (target.y() - current.y()) * local_alpha,
                    current.width() + (target.width() - current.width()) * local_alpha,
                    current.height() + (target.height() - current.height()) * local_alpha,
                )
                if item["alpha"] < 1.0:
                    item["alpha"] = min(1.0, item["alpha"] + dt * self.HUD_FADE_PER_S)
        if self._scanning:
            self._advance_scan()
        self.update()
        if (
            not self._scanning
            and not self._cinematic
            and not self._effects.is_active()
            and not self._face_moving()
            and not self._hud_animating()
            and not self._local_moving()
        ):
            self._timer.stop()

    def _target_rect(self) -> QRectF:
        rect = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        if self._image is None or self._image.isNull():
            return rect
        source = self._image.size()
        if source.width() <= 0 or source.height() <= 0:
            return rect
        scale = min(rect.width() / source.width(), rect.height() / source.height())
        width = source.width() * scale
        height = source.height() * scale
        return QRectF(
            rect.center().x() - width / 2,
            rect.center().y() - height / 2,
            width,
            height,
        )

    def _scaled_frame(self, target: QRectF) -> Optional[QImage]:
        """Aynı kareyi her boyamada yeniden ölçeklemek yerine bir kez önbelleğe alır."""
        if self._image is None or self._image.isNull():
            return None
        size = target.size().toSize()
        if size.width() <= 0 or size.height() <= 0:
            return None
        key = (self._image.cacheKey(), size.width(), size.height())
        if self._scaled is not None and self._scaled_key == key:
            return self._scaled
        self._scaled = self._image.scaled(
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._scaled_key = key
        return self._scaled

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(U.INPUT))
        target = self._target_rect()

        if self._image is not None and not self._image.isNull():
            scaled = self._scaled_frame(target)
            painter.save()
            painter.setClipRect(target)
            if scaled is not None:
                painter.drawImage(target, scaled)
            painter.restore()
        else:
            painter.setPen(QColor(U.DIM))
            painter.setFont(hud_font(8, QFont.Weight.Medium, 1.4))
            painter.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                self._message or "KAMERA KAPALI",
            )

        painter.setPen(QPen(QColor(U.BORDER), 1.0))
        painter.drawRoundedRect(target.adjusted(-4, -4, 4, 4), 6, 6)

        painter.setPen(QPen(QColor(U.RED_SOFT), 1.0))
        corner = 20.0
        for x, y, dx, dy in (
            (target.left(), target.top(), 1, 1),
            (target.right(), target.top(), -1, 1),
            (target.left(), target.bottom(), 1, -1),
            (target.right(), target.bottom(), -1, -1),
        ):
            painter.drawLine(QPoint(int(x), int(y)), QPoint(int(x + corner * dx), int(y)))
            painter.drawLine(QPoint(int(x), int(y)), QPoint(int(x), int(y + corner * dy)))

        painter.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
        for index in range(1, 3):
            x = target.left() + target.width() * index / 3
            y = target.top() + target.height() * index / 3
            painter.drawLine(QPoint(int(x), int(target.top())), QPoint(int(x), int(target.bottom())))
            painter.drawLine(QPoint(int(target.left()), int(y)), QPoint(int(target.right()), int(y)))

        if self._scanning:
            self._paint_probe_scan(painter, target)

        if self._cinematic:
            self._paint_depth_grid(painter, target)

        if self._hud_render:
            for index, item in enumerate(self._hud_render, start=1):
                if item["kind"] == "face" and self._face_boxes:
                    continue  # yerel yüz kilidi varken bulut kutusunu iki kez çizme
                if item["kind"] == "object" and self._local_duplicate(item["box"]):
                    continue  # yerel dedektör aynı nesneyi daha taze işaretliyor
                self._paint_hud_box(
                    painter,
                    target,
                    item["box"],
                    item["label"],
                    item["kind"],
                    index,
                    item["alpha"],
                )

        if self._local_render:
            for index, item in enumerate(self._local_render, start=1):
                self._paint_hud_box(
                    painter,
                    target,
                    item["box"],
                    item["label"],
                    item["kind"],
                    index,
                    item["alpha"],
                    shift=QPointF(0.0, 0.0),
                )

        if self._face_enabled:
            self._paint_face_hud(painter, target)

        if self._hand_enabled:
            self._effects.paint(painter, target)
            self._paint_hand_chip(painter, target)

        painter.end()

    def _local_duplicate(self, box: QRectF) -> bool:
        """Bulut kutusu yerel dedektörün tazelediği bir alanı mı işaretliyor?"""
        area = box.width() * box.height()
        if area <= 0:
            return False
        for _, _, local in self._local_boxes:
            inter = box.intersected(local)
            if inter.isEmpty():
                continue
            overlap = inter.width() * inter.height()
            smaller = min(area, local.width() * local.height())
            if smaller > 0 and overlap / smaller > 0.45:
                return True
        return False

    def _paint_probe_scan(self, painter: QPainter, target: QRectF) -> None:
        """Gezinen odak: süpürme çizgisi yok; solan iz, sonar halkaları ve ince artı."""
        painter.save()
        painter.setClipRect(target)
        accent = QColor(U.RED_BRIGHT)

        def tone(alpha: float) -> QColor:
            color = QColor(accent)
            color.setAlpha(max(0, min(255, int(alpha))))
            return color

        unit = max(1.0, min(target.width(), target.height()) / 420.0)
        probe = self._scan_point(self._scan_time)
        center = QPointF(
            target.left() + probe.x() * target.width(),
            target.top() + probe.y() * target.height(),
        )
        pulse = 0.5 + 0.5 * math.sin(self._scan_time * 2.2)

        for ping in self._scan_pings:
            life = max(0.0, min(1.0, float(ping["life"])))
            pos: QPointF = ping["pos"]
            ring_center = QPointF(
                target.left() + pos.x() * target.width(),
                target.top() + pos.y() * target.height(),
            )
            radius = (1.0 - life) * 105.0 * unit
            if radius < 2.0:
                continue
            ring = tone((84 if self._analyzing else 62) * life)
            painter.setPen(QPen(ring, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(ring_center, radius, radius)

        trail = self._scan_trail[-10:]
        if len(trail) >= 2:
            painter.setPen(Qt.PenStyle.NoPen)
            for index, point in enumerate(trail):
                ratio = (index + 1) / len(trail)
                dot_center = QPointF(
                    target.left() + point.x() * target.width(),
                    target.top() + point.y() * target.height(),
                )
                painter.setBrush(tone(66 * ratio))
                painter.drawEllipse(dot_center, 1.7 * unit * ratio, 1.7 * unit * ratio)

        glow_radius = (20.0 + 5.0 * pulse) * unit
        glow = QRadialGradient(center, glow_radius)
        glow.setColorAt(0.0, tone(78 if self._analyzing else 60))
        glow.setColorAt(0.55, tone(22))
        glow.setColorAt(1.0, tone(0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, glow_radius, glow_radius)

        spin_radius = 17.0 * unit
        spin_rect = QRectF(
            center.x() - spin_radius,
            center.y() - spin_radius,
            spin_radius * 2,
            spin_radius * 2,
        )
        start = (self._scan_time * 80.0) % 360.0
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(tone(168 if self._analyzing else 126), 1.2))
        painter.drawArc(spin_rect, int(start * 16), int(62 * 16))
        painter.setPen(QPen(tone(64 if self._analyzing else 44), 1.0))
        painter.drawEllipse(center, 6.0 * unit, 6.0 * unit)

        arm = 11.0 * unit
        gap = 4.5 * unit
        painter.setPen(QPen(tone(206 if self._analyzing else 168), 1.0))
        painter.drawLine(QPointF(center.x() - gap - arm, center.y()), QPointF(center.x() - gap, center.y()))
        painter.drawLine(QPointF(center.x() + gap, center.y()), QPointF(center.x() + gap + arm, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - gap - arm), QPointF(center.x(), center.y() - gap))
        painter.drawLine(QPointF(center.x(), center.y() + gap), QPointF(center.x(), center.y() + gap + arm))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 251, 246, 232))
        painter.drawEllipse(center, 1.8 * unit, 1.8 * unit)

        text = "Analiz ediliyor…" if self._analyzing else "Taranıyor…"
        painter.setFont(hud_font(7, QFont.Weight.Medium, 0.2))
        metrics = painter.fontMetrics()
        chip_width = min(target.width() - 16, metrics.horizontalAdvance(text) + 30)
        chip = QRectF(
            target.center().x() - chip_width / 2,
            target.bottom() - 34,
            chip_width,
            22,
        )
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.setBrush(QColor(10, 10, 12, 158))
        painter.drawRoundedRect(chip, 11, 11)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tone(120 + 90 * pulse))
        painter.drawEllipse(QPointF(chip.left() + 11, chip.center().y()), 2.0, 2.0)
        painter.setPen(QColor(245, 243, 238, 232))
        painter.drawText(
            chip.adjusted(19, 0, -5, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()

    def _paint_face_hud(self, painter: QPainter, target: QRectF) -> None:
        faces = self._face_render or self._face_boxes
        for face in faces:
            self._paint_face_box(painter, target, face)
        self._paint_face_chip(painter, target)

    def _paint_face_box(self, painter: QPainter, target: QRectF, face: dict) -> None:
        box: QRectF = face["box"]
        focus = bool(face.get("focus"))
        confidence = max(0.0, min(1.0, float(face.get("confidence", 0.0))))
        rect = QRectF(
            target.left() + (box.left() + self._face_shift.x()) * target.width(),
            target.top() + (box.top() + self._face_shift.y()) * target.height(),
            box.width() * target.width(),
            box.height() * target.height(),
        ).intersected(target)
        if rect.width() < 8 or rect.height() < 8:
            return

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        base = QColor(U.RED_BRIGHT if focus else U.TEXT_SOFT)
        if not focus:
            base.setAlpha(196)
        faint = QColor(base)
        faint.setAlphaF(base.alphaF() * 0.32)
        painter.setPen(QPen(faint, 0.9, Qt.PenStyle.DashLine))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        painter.setPen(QPen(base, 2.0 if focus else 1.2))
        corner = min(24.0 if focus else 15.0, rect.width() * 0.3, rect.height() * 0.3)
        x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
        for sx, sy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            painter.drawLine(QPointF(sx, sy), QPointF(sx + corner * dx, sy))
            painter.drawLine(QPointF(sx, sy), QPointF(sx, sy + corner * dy))

        center = rect.center()
        if focus:
            halo = QColor(base)
            halo.setAlpha(54)
            painter.setPen(QPen(halo, 1.0))
            radius = min(rect.width(), rect.height()) * 0.36
            painter.drawEllipse(center, radius, radius)
            painter.setPen(QPen(base, 1.1))
            gap = min(7.0, rect.width() * 0.18)
            arm = min(14.0, rect.width() * 0.36)
            painter.drawLine(QPointF(center.x() - arm, center.y()), QPointF(center.x() - gap, center.y()))
            painter.drawLine(QPointF(center.x() + gap, center.y()), QPointF(center.x() + arm, center.y()))
            painter.drawLine(QPointF(center.x(), center.y() - arm), QPointF(center.x(), center.y() - gap))
            painter.drawLine(QPointF(center.x(), center.y() + gap), QPointF(center.x(), center.y() + arm))
            label = f"KİŞİ · ODAK · %{int(round(confidence * 100))}"
        else:
            label = "KİŞİ · DİĞER"

        painter.setFont(hud_font(7, QFont.Weight.DemiBold, 1.2))
        metrics = painter.fontMetrics()
        chip_width = min(target.width() - 8, metrics.horizontalAdvance(label) + 12)
        chip_height = 16.0
        chip_x = min(max(target.left() + 4, rect.left()), target.right() - chip_width - 4)
        chip_y = rect.bottom() + 3
        if chip_y > target.bottom() - chip_height - 4:
            chip_y = max(target.top() + 4, rect.top() - chip_height - 3)
        chip = QRectF(chip_x, chip_y, chip_width, chip_height)
        painter.fillRect(chip, QColor(8, 8, 12, 204))
        painter.setPen(QPen(base, 1.0))
        painter.drawRect(chip)
        painter.setPen(QColor(255, 255, 255, 232))
        painter.drawText(
            chip.adjusted(5, 0, -3, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            label,
        )
        painter.restore()

    def _paint_face_chip(self, painter: QPainter, target: QRectF) -> None:
        painter.save()
        if self._face_message:
            text = self._face_message.upper()
            color = QColor(U.GOLD)
        elif self._face_boxes:
            count = len(self._face_boxes)
            text = f"YÜZ HUD · {count} KİŞİ · YEREL"
            if self._face_fps:
                text += f" · {self._face_fps} FPS"
            if self._face_latency_ms:
                text += f" · {self._face_latency_ms} MS"
            color = QColor(U.RED_BRIGHT)
        else:
            text = "YÜZ HUD · ARANIYOR"
            color = QColor(U.MUTED)
        painter.setFont(hud_font(6.8, QFont.Weight.DemiBold, 1.1))
        metrics = painter.fontMetrics()
        width = min(target.width() - 16, metrics.horizontalAdvance(text) + 20)
        chip = QRectF(target.right() - width - 8, target.top() + 8, width, 18)
        painter.fillRect(chip, QColor(7, 7, 10, 190))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(U.RED_SOFT), 0.9))
        painter.drawRect(chip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(chip.left() + 8, chip.center().y()), 2.0, 2.0)
        painter.setPen(QColor(U.TEXT_SOFT))
        painter.drawText(
            chip.adjusted(15, 0, -4, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()

    def _paint_hand_chip(self, painter: QPainter, target: QRectF) -> None:
        painter.save()
        if self._hand_message:
            text = self._hand_message.upper()
        elif self._hand_count:
            text = f"YEREL ELLER · {self._hand_count} EL · {self._hand_fps} FPS"
        else:
            text = "YEREL ELLER · EL ARANIYOR"
        painter.setFont(hud_font(6.8, QFont.Weight.DemiBold, 1.1))
        metrics = painter.fontMetrics()
        width = min(target.width() - 16, metrics.horizontalAdvance(text) + 20)
        chip = QRectF(target.left() + 8, target.top() + 8, width, 18)
        painter.fillRect(chip, QColor(7, 7, 10, 190))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(U.RED_SOFT), 0.9))
        painter.drawRect(chip)
        dot = QColor(U.RED_BRIGHT if self._hand_count else U.DIM)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QPointF(chip.left() + 8, chip.center().y()), 2.0, 2.0)
        painter.setPen(QColor(U.TEXT_SOFT))
        painter.drawText(
            chip.adjusted(15, 0, -4, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()

    def _paint_depth_grid(self, painter: QPainter, target: QRectF) -> None:
        """Tam ekranda nesnelerin konumunu okumak için perspektif ızgarası."""
        painter.save()
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 1.7)
        ray_alpha = int(52 + 22 * pulse)
        ring_alpha = int(40 + 16 * pulse)
        horizon = target.top() + target.height() * 0.46
        vanish = QPointF(target.center().x(), horizon)
        painter.setPen(QPen(QColor(255, 255, 255, ray_alpha), 1.0))
        for index in range(-6, 7):
            x = target.center().x() + index * target.width() * 0.13
            painter.drawLine(
                QPointF(x, target.bottom()),
                QPointF(vanish.x() + index * 6, horizon),
            )
        rows = 8
        painter.setPen(QPen(QColor(255, 255, 255, ring_alpha), 1.0))
        row_ys = []
        for index in range(1, rows + 1):
            ratio = index / rows
            y = horizon + (target.bottom() - horizon) * (ratio * ratio)
            row_ys.append(y)
            painter.drawLine(QPointF(target.left(), y), QPointF(target.right(), y))
        painter.setPen(QPen(QColor(U.RED_BRIGHT), 1.2))
        painter.setOpacity(0.55)
        painter.drawLine(QPointF(target.left(), horizon), QPointF(target.right(), horizon))
        painter.setOpacity(1.0)

        painter.setFont(hud_font(6.6, QFont.Weight.DemiBold, 1.6))
        painter.setPen(QColor(255, 255, 255, 132))
        for label, row in (("YAKIN · 1M", 2), ("ORTA · 3M", 5), ("UZAK · 8M", 8)):
            if 0 < row <= len(row_ys):
                painter.drawText(
                    QRectF(target.left() + 10, row_ys[row - 1] - 14, 96, 12),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )

        chip = QRectF(target.left() + 10, target.bottom() - 28, 148, 18)
        painter.fillRect(chip, QColor(7, 7, 10, 168))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(U.RED_SOFT), 0.9))
        painter.drawRect(chip)
        painter.setFont(hud_font(6.8, QFont.Weight.DemiBold, 1.2))
        painter.setPen(QColor(U.RED_BRIGHT))
        painter.drawText(
            chip.adjusted(14, 0, -4, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "3B DERİNLİK · AKTİF",
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(U.RED_BRIGHT))
        painter.drawEllipse(QPointF(chip.left() + 7, chip.center().y()), 2.0, 2.0)
        painter.restore()

    def _paint_hud_box(
        self,
        painter: QPainter,
        target: QRectF,
        box: QRectF,
        label: str,
        kind: str,
        index: int,
        alpha: float,
        shift: Optional[QPointF] = None,
    ) -> None:
        if alpha <= 0.02:
            return
        offset = self._hud_shift if shift is None else shift
        shifted_left = box.left() + offset.x()
        shifted_top = box.top() + offset.y()
        rect = QRectF(
            target.left() + shifted_left * target.width(),
            target.top() + shifted_top * target.height(),
            box.width() * target.width(),
            box.height() * target.height(),
        ).intersected(target)
        if rect.width() < 8 or rect.height() < 8:
            return

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind in ("face", "person"):
            base = QColor(U.RED_BRIGHT)
        elif kind in ("text", "screen"):
            base = QColor(U.GOLD)
        else:
            base = QColor(U.RED_GLOW)
        base.setAlphaF(min(1.0, max(0.0, alpha)))

        faint = QColor(base)
        faint.setAlphaF(base.alphaF() * 0.34)
        field_pen = QPen(faint, 0.9, Qt.PenStyle.DashLine)
        painter.setPen(field_pen)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        bracket = QPen(base, 1.6)
        bracket.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(bracket)
        corner = min(18.0, rect.width() * 0.28, rect.height() * 0.28)
        x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
        for sx, sy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            painter.drawLine(QPointF(sx, sy), QPointF(sx + corner * dx, sy))
            painter.drawLine(QPointF(sx, sy), QPointF(sx, sy + corner * dy))

        center = rect.center()
        if kind == "face":
            gap = min(7.0, rect.width() * 0.18)
            arm = min(16.0, rect.width() * 0.4)
            painter.setPen(QPen(base, 1.1))
            painter.drawLine(QPointF(center.x() - arm, center.y()), QPointF(center.x() - gap, center.y()))
            painter.drawLine(QPointF(center.x() + gap, center.y()), QPointF(center.x() + arm, center.y()))
            painter.drawLine(QPointF(center.x(), center.y() - arm), QPointF(center.x(), center.y() - gap))
            painter.drawLine(QPointF(center.x(), center.y() + gap), QPointF(center.x(), center.y() + arm))
            halo = QPen(QColor(base.red(), base.green(), base.blue(), int(58 * alpha)), 1.0)
            painter.setPen(halo)
            radius = min(rect.width(), rect.height()) * 0.34
            painter.drawEllipse(center, radius, radius)

        text = f"{index:02d} · {str(label)[:18].upper()}"
        painter.setFont(hud_font(7, QFont.Weight.DemiBold, 1.2))
        metrics = painter.fontMetrics()
        chip_width = min(target.width() - 8, metrics.horizontalAdvance(text) + 12)
        chip_height = 16.0
        chip_x = min(max(target.left() + 4, rect.left()), target.right() - chip_width - 4)
        chip_y = rect.top() - chip_height - 3
        if chip_y < target.top() + 4:
            chip_y = min(rect.bottom() + 3, target.bottom() - chip_height - 4)
        chip = QRectF(chip_x, chip_y, chip_width, chip_height)
        painter.fillRect(chip, QColor(8, 8, 12, int(200 * alpha)))
        outline = QColor(base)
        outline.setAlphaF(min(1.0, alpha * 0.78))
        painter.setPen(QPen(outline, 1.0))
        painter.drawRect(chip)
        text_color = QColor(255, 255, 255)
        text_color.setAlphaF(min(1.0, alpha * 0.92))
        painter.setPen(text_color)
        painter.drawText(
            chip.adjusted(5, 0, -3, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()
