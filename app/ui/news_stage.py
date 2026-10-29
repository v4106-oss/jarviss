"""Haber anlatımı sırasında açılan genişleyen fotoğraf slaytı.

Görseller yalnızca anlık gösterim içindir; diske yazılmaz. Her slaytta
haber kaynağı ve yayın zamanı görünür kalır.
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, List, Optional

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from app.core.news_tools import add_news_listener, download_image, fetch_article_image
from app.theme import ultron as U

MAX_SLIDES = 6
SLIDE_MS = 5200
TRANSITION_MS = 640
HIDE_GRACE_MS = 6500
MAX_VISIBLE_MS = 75000
PANEL_RADIUS = 22
IMAGE_RADIUS = 15


def _tint(color: QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(max(0, min(255, int(alpha))))
    return tinted


def _ease(value: float) -> float:
    t = max(0.0, min(1.0, float(value)))
    return t * t * (3.0 - 2.0 * t)


def _relative_time(value: str) -> str:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - moment).total_seconds()
    if seconds < 60:
        return "az önce"
    if seconds < 3600:
        return f"{int(seconds // 60)} dk önce"
    if seconds < 86400:
        return f"{int(seconds // 3600)} sa önce"
    return f"{int(seconds // 86400)} gün önce"


class NewsStageBridge(QObject):
    """Ajan iş parçacığından gelen haberleri arayüz iş parçacığına taşır."""

    items_ready = Signal(list)


_bridge: Optional[NewsStageBridge] = None
_bridge_lock = threading.Lock()


def news_stage_bridge() -> NewsStageBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            bridge = NewsStageBridge()
            add_news_listener(bridge.items_ready.emit)
            _bridge = bridge
        return _bridge


class _ImageLoader(QObject):
    image_ready = Signal(int, int, object)
    finished = Signal(int)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.active_token = 0

    def start(self, slides: List[dict], token: int) -> None:
        self.active_token = int(token)
        thread = threading.Thread(
            target=self._run,
            args=(list(slides), int(token)),
            name="jarvis-news-images",
            daemon=True,
        )
        thread.start()

    def cancel(self) -> None:
        self.active_token = -1

    def _run(self, slides: List[dict], token: int) -> None:
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs = {
                    pool.submit(self._load_one, slide): index
                    for index, slide in enumerate(slides)
                }
                for future in as_completed(jobs):
                    if token != self.active_token:
                        return
                    index = jobs[future]
                    try:
                        data = future.result()
                    except Exception:
                        data = None
                    if data and token == self.active_token:
                        self.image_ready.emit(index, token, data)
        finally:
            if token == self.active_token:
                self.finished.emit(token)

    @staticmethod
    def _load_one(slide: dict) -> Optional[bytes]:
        data = download_image(str(slide.get("image") or ""))
        if data is None:
            preview = fetch_article_image(str(slide.get("url") or ""))
            if preview:
                data = download_image(preview)
        return data


class NewsStage(QWidget):
    """Sağ üstteki küçük rozetten büyüyen, geçişli haber slaytı."""

    def __init__(self, parent: QWidget, is_busy: Optional[Callable[[], bool]] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._is_busy = is_busy
        self._slides: List[dict] = []
        self._pixmaps: List[Optional[QPixmap]] = []
        self._pixmap_start: List[float] = []
        self._index = 0
        self._previous = -1
        self._transition = 0.0
        self._transition_start = 0.0
        self._slide_started = time.monotonic()
        self._shown_at = 0.0
        self._last_busy_check = 0.0
        self._expanded = False
        self._geometry_hiding = False
        self._token = 0
        self._geometry_serial = 0
        self._geometry_animation: Optional[QPropertyAnimation] = None
        self._close_rect = QRect()

        self._font_head = QFont(U.FONT, 8)
        self._font_head.setWeight(QFont.Weight.DemiBold)
        self._font_head.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.4)
        self._font_title = QFont(U.FONT, 11)
        self._font_title.setWeight(QFont.Weight.DemiBold)
        self._font_meta = QFont(U.MONO, 7)
        self._font_meta.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        self._font_category = QFont(U.FONT, 34)
        self._font_category.setWeight(QFont.Weight.DemiBold)
        self._font_category.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 8.0)
        self._font_pill = QFont(U.MONO, 7)
        self._font_pill.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)

        self._loader = _ImageLoader(self)
        self._loader.image_ready.connect(self._on_image_ready)

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

        if parent is not None:
            parent.installEventFilter(self)
        self.hide()

    # ---------- yerleşim ----------

    def _expanded_rect(self) -> QRect:
        parent = self.parentWidget()
        if parent is None:
            return QRect(24, 100, 780, 500)
        width = max(1, parent.width())
        height = max(1, parent.height())
        panel_w = min(860, max(560, int(width * 0.55)))
        panel_h = min(560, max(360, int(height * 0.58)))
        panel_w = min(panel_w, width - 60)
        panel_h = min(panel_h, height - 210)
        top = max(84, min(int(height * 0.16), height - panel_h - 118))
        return QRect(width - 26 - panel_w, top, panel_w, panel_h)

    def _collapsed_rect(self) -> QRect:
        expanded = self._expanded_rect()
        return QRect(expanded.right() - 258, expanded.top(), 258, 92)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            if self.isVisible() and self._expanded:
                running = (
                    self._geometry_animation is not None
                    and self._geometry_animation.state() == QAbstractAnimation.State.Running
                )
                if not running:
                    self.setGeometry(self._expanded_rect())
        return False

    def _animate_geometry(self, target: QRect, duration: int, hiding: bool = False) -> None:
        self._geometry_serial += 1
        serial = self._geometry_serial
        self._geometry_hiding = hiding
        if self._geometry_animation is not None:
            try:
                self._geometry_animation.stop()
            except RuntimeError:
                pass
        animation = QPropertyAnimation(self, b"geometry", self)
        animation.setDuration(int(duration))
        animation.setStartValue(self.geometry())
        animation.setEndValue(target)
        animation.setEasingCurve(
            QEasingCurve.Type.InOutCubic if hiding else QEasingCurve.Type.OutCubic
        )

        def _done() -> None:
            if serial != self._geometry_serial:
                return
            self._geometry_hiding = False
            if hiding:
                self.hide()

        animation.finished.connect(_done)
        self._geometry_animation = animation
        animation.start()

    # ---------- akış ----------

    def show_items(self, items: List[dict]) -> None:
        slides = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title") or "").split())[:180]
            if not title:
                continue
            slides.append({
                "title": title,
                "source": str(item.get("source") or "").strip()[:60],
                "category": str(item.get("category") or "").strip().lower(),
                "published_at": str(item.get("published_at") or ""),
                "url": str(item.get("url") or ""),
                "image": str(item.get("image") or ""),
            })
            if len(slides) >= MAX_SLIDES:
                break
        if not slides:
            return

        self._slides = slides
        self._pixmaps = [None] * len(slides)
        self._pixmap_start = [0.0] * len(slides)
        self._index = 0
        self._previous = -1
        self._transition = 0.0
        now = time.monotonic()
        self._slide_started = now
        self._shown_at = now
        self._last_busy_check = now
        self._token += 1
        if not self.isVisible():
            self._expanded = True
            self.setGeometry(self._collapsed_rect())
            self.show()
            self.raise_()
            self._animate_geometry(self._expanded_rect(), 430)
        elif self._geometry_hiding or not self._expanded:
            self._expanded = True
            self.raise_()
            self._animate_geometry(self._expanded_rect(), 430)
        self._loader.start(self._slides, self._token)
        self._timer.start()
        self.update()

    def next_slide(self) -> None:
        if len(self._slides) < 2 or self._previous >= 0:
            return
        self._previous = self._index
        self._index = (self._index + 1) % len(self._slides)
        self._transition = 0.0
        self._transition_start = time.monotonic()
        self._slide_started = self._transition_start
        self.update()

    def collapse(self) -> None:
        if not self.isVisible():
            return
        self._expanded = False
        self._loader.cancel()
        self._geometry_serial += 1
        self._animate_geometry(self._collapsed_rect(), 360, hiding=True)
        self.update()

    def _tick(self) -> None:
        if not self.isVisible():
            self._timer.stop()
            return
        now = time.monotonic()
        if self._previous >= 0:
            self._transition = min(1.0, (now - self._transition_start) / (TRANSITION_MS / 1000.0))
            if self._transition >= 1.0:
                self._previous = -1
        elif now - self._slide_started >= SLIDE_MS / 1000.0:
            self.next_slide()
        if self._is_busy is not None and now - self._shown_at >= HIDE_GRACE_MS / 1000.0:
            if now - self._last_busy_check >= 0.6:
                self._last_busy_check = now
                try:
                    busy = bool(self._is_busy())
                except Exception:
                    busy = False
                if not busy and now - self._slide_started >= 2.4:
                    self.collapse()
                    return
        if now - self._shown_at > MAX_VISIBLE_MS / 1000.0:
            self.collapse()
            return
        self.update()

    def _on_image_ready(self, index: int, token: int, data) -> None:
        if token != self._token or index < 0 or index >= len(self._pixmaps):
            return
        pixmap = QPixmap()
        try:
            ok = pixmap.loadFromData(data)
        except Exception:
            ok = False
        if not ok or pixmap.isNull():
            return
        self._pixmaps[index] = pixmap
        self._pixmap_start[index] = time.monotonic()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._close_rect.contains(event.position().toPoint()):
            self.collapse()
            return
        self.next_slide()

    def dismiss(self) -> None:
        """Pencere gizlenirken sunumu tamamen sıfırlar."""
        self._slides = []
        self._pixmaps = []
        self._pixmap_start = []
        self._previous = -1
        self._transition = 0.0
        self._token += 1
        self._loader.cancel()
        self._timer.stop()
        self._geometry_serial += 1
        self._expanded = False
        self._geometry_hiding = False
        if self._geometry_animation is not None:
            try:
                self._geometry_animation.stop()
            except RuntimeError:
                pass
        self.hide()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        self._loader.cancel()
        self._geometry_serial += 1
        self._geometry_hiding = False
        super().hideEvent(event)

    # ---------- çizim ----------

    @staticmethod
    def _rounded(rect: QRectF, radius: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def paintEvent(self, event) -> None:
        width = self.width()
        height = self.height()
        if width < 60 or height < 40:
            return
        now = time.monotonic()
        accent = QColor(U.RED_BRIGHT if not U.IS_LIGHT else U.RED)
        text_main = QColor(U.TEXT)
        text_soft = QColor(U.TEXT_SOFT)
        muted = QColor(U.MUTED)
        panel = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawPath(self._rounded(panel.translated(0.0, 6.0), PANEL_RADIUS))

        body = QLinearGradient(panel.topLeft(), panel.bottomRight())
        body.setColorAt(0.0, QColor(U.PANEL_2))
        body.setColorAt(1.0, QColor(U.BACKGROUND_2))
        painter.setBrush(QBrush(body))
        painter.drawPath(self._rounded(panel, PANEL_RADIUS))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(U.BORDER), 1.0))
        painter.drawPath(self._rounded(panel, PANEL_RADIUS))

        if width < 430 or height < 300:
            self._paint_pill(painter, panel, accent, text_soft, now)
            painter.end()
            return

        painter.save()
        painter.setClipPath(self._rounded(panel, PANEL_RADIUS))
        glow = QRadialGradient(QPointF(panel.right() - 60.0, panel.top() + 20.0), panel.width() * 0.5)
        glow.setColorAt(0.0, _tint(accent, 20))
        glow.setColorAt(1.0, _tint(accent, 0))
        painter.fillRect(panel, QBrush(glow))
        painter.restore()

        margin = 22.0
        header_baseline = 32.0
        pulse = 0.5 + 0.5 * math.sin(now * 2.6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_tint(accent, int(36 + 30 * pulse)))
        painter.drawEllipse(QPointF(margin + 4.0, header_baseline - 4.0), 7.5, 7.5)
        painter.setBrush(_tint(accent, 220))
        painter.drawEllipse(QPointF(margin + 4.0, header_baseline - 4.0), 2.6, 2.6)
        painter.setFont(self._font_head)
        painter.setPen(_tint(text_soft, 215))
        painter.drawText(QPointF(margin + 18.0, header_baseline), "HABER SUNUMU")

        counter = f"{self._index + 1:02d} / {len(self._slides):02d}"
        painter.setFont(self._font_meta)
        painter.setPen(_tint(muted, 190))
        counter_width = painter.fontMetrics().horizontalAdvance(counter)
        painter.drawText(
            QPointF(panel.right() - margin - 38.0 - counter_width, header_baseline), counter
        )

        self._close_rect = QRect(int(panel.right() - margin - 24.0), 13, 24, 24)
        painter.setPen(QPen(_tint(muted, 130), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self._close_rect), 8.0, 8.0)
        cx = self._close_rect.center().x()
        cy = self._close_rect.center().y()
        painter.setPen(QPen(_tint(text_soft, 200), 1.3))
        painter.drawLine(QPointF(cx - 3.6, cy - 3.6), QPointF(cx + 3.6, cy + 3.6))
        painter.drawLine(QPointF(cx - 3.6, cy + 3.6), QPointF(cx + 3.6, cy - 3.6))

        footer_height = 116.0
        image = QRectF(
            margin,
            52.0,
            panel.width() - 2.0 * margin,
            panel.height() - 52.0 - footer_height,
        )
        image_path = self._rounded(image, IMAGE_RADIUS)
        painter.save()
        painter.setClipPath(image_path)
        painter.fillRect(image, QColor(U.BACKGROUND))
        if self._previous >= 0:
            t = _ease(self._transition)
            self._paint_slide(painter, image, self._previous, 1.0 - t, -14.0 * t, 1.055, now)
            self._paint_slide(painter, image, self._index, t, 30.0 * (1.0 - t), 1.02, now)
        else:
            self._paint_slide(painter, image, self._index, 1.0, 0.0, 1.02, now)
        shade = QLinearGradient(image.bottomLeft(), image.topLeft())
        shade.setColorAt(0.0, QColor(4, 6, 8, 165))
        shade.setColorAt(0.42, QColor(4, 6, 8, 45))
        shade.setColorAt(1.0, QColor(4, 6, 8, 0))
        painter.fillRect(image, QBrush(shade))
        if self._current_pixmap() is None:
            self._paint_loading(painter, image, accent, muted, now)
        painter.restore()
        painter.setPen(QPen(QColor(U.BORDER), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(image_path)

        title_alpha = 1.0 if self._previous < 0 else 0.45 + 0.55 * _ease(self._transition)
        meta_y = image.bottom() + 18.0
        slide = self._slides[self._index]
        meta_parts = [part for part in (slide["source"], slide["category"].upper(), _relative_time(slide["published_at"])) if part]
        painter.setFont(self._font_meta)
        painter.setPen(_tint(muted, int(205 * title_alpha)))
        painter.drawText(
            QRectF(margin, meta_y, panel.width() - 2.0 * margin, 16.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "  ·  ".join(meta_parts),
        )

        title_rect = QRectF(margin, meta_y + 20.0, panel.width() - 2.0 * margin, 48.0)
        painter.setFont(self._font_title)
        painter.setPen(_tint(text_main, int(245 * title_alpha)))
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            slide["title"],
        )

        if len(self._slides) > 1:
            track = QRectF(margin, panel.bottom() - 8.0, panel.width() - 2.0 * margin, 2.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(U.BORDER))
            painter.drawRoundedRect(track, 1.0, 1.0)
            if self._previous >= 0:
                progress = 0.0
            else:
                progress = min(1.0, (now - self._slide_started) / (SLIDE_MS / 1000.0))
            if progress > 0.0:
                painter.setBrush(_tint(accent, 185))
                painter.drawRoundedRect(
                    QRectF(track.left(), track.top(), max(2.0, track.width() * progress), track.height()),
                    1.0,
                    1.0,
                )
        painter.end()

    def _current_pixmap(self) -> Optional[QPixmap]:
        if 0 <= self._index < len(self._pixmaps):
            return self._pixmaps[self._index]
        return None

    def _paint_pill(self, painter: QPainter, panel: QRectF, accent: QColor, text_soft: QColor, now: float) -> None:
        margin = 16.0
        pulse = 0.5 + 0.5 * math.sin(now * 2.6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_tint(accent, int(34 + 26 * pulse)))
        painter.drawEllipse(QPointF(margin + 8.0, panel.center().y()), 8.0, 8.0)
        painter.setBrush(_tint(accent, 225))
        painter.drawEllipse(QPointF(margin + 8.0, panel.center().y()), 2.8, 2.8)
        painter.setFont(self._font_pill)
        painter.setPen(_tint(text_soft, 220))
        painter.drawText(
            QPointF(margin + 26.0, panel.center().y() - 3.0),
            "HABER SUNUMU",
        )
        dots = "·" * (int(now * 4.0) % 3 + 1)
        painter.setPen(_tint(QColor(U.MUTED), 180))
        painter.drawText(
            QPointF(margin + 26.0, panel.center().y() + 15.0),
            f"GÖRSELLER HAZIRLANIYOR {dots}",
        )

    def _paint_slide(
        self,
        painter: QPainter,
        rect: QRectF,
        index: int,
        alpha: float,
        offset: float,
        base_zoom: float,
        now: float,
    ) -> None:
        if index < 0 or index >= len(self._slides) or alpha <= 0.01:
            return
        slide = self._slides[index]
        pixmap = self._pixmaps[index]
        if pixmap is None or pixmap.isNull():
            if alpha >= 0.45:
                self._paint_text_slide(painter, rect, slide, alpha)
            return
        since = now - self._pixmap_start[index]
        fade = min(1.0, max(0.0, since / 0.36))
        target = rect.translated(offset, 0.0)
        source_w = max(1, pixmap.width())
        source_h = max(1, pixmap.height())
        elapsed = max(0.0, now - self._slide_started)
        zoom = base_zoom + 0.05 * min(1.0, elapsed / 6.0)
        scale = max(target.width() / source_w, target.height() / source_h) * zoom
        draw_w = source_w * scale
        draw_h = source_h * scale
        x = target.center().x() - draw_w / 2.0
        y = target.center().y() - draw_h / 2.0
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, alpha * fade)))
        painter.drawPixmap(
            QRectF(x, y, draw_w, draw_h),
            pixmap,
            QRectF(0.0, 0.0, float(source_w), float(source_h)),
        )
        painter.restore()

    def _paint_text_slide(self, painter: QPainter, rect: QRectF, slide: dict, alpha: float) -> None:
        accent = QColor(U.RED_BRIGHT if not U.IS_LIGHT else U.RED)
        painter.save()
        painter.setOpacity(alpha)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, _tint(accent, 46))
        gradient.setColorAt(0.55, QColor(U.PANEL_2))
        gradient.setColorAt(1.0, QColor(U.BACKGROUND))
        painter.fillRect(rect, QBrush(gradient))
        center = rect.center()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for radius in (rect.height() * 0.42, rect.height() * 0.55):
            painter.setPen(QPen(_tint(accent, 34), 1.0))
            painter.drawEllipse(center, radius, radius)
        label = (slide.get("category") or "haber").upper()
        painter.setFont(self._font_category)
        painter.setPen(_tint(accent, 88))
        metrics = painter.fontMetrics()
        label_width = metrics.horizontalAdvance(label)
        painter.drawText(
            QPointF(center.x() - label_width / 2.0, center.y() + metrics.height() / 3.0),
            label,
        )
        painter.setFont(self._font_meta)
        painter.setPen(_tint(QColor(U.MUTED), 160))
        source = slide.get("source") or "JARVIS HABER"
        source_width = painter.fontMetrics().horizontalAdvance(source.upper())
        painter.drawText(
            QPointF(center.x() - source_width / 2.0, center.y() + metrics.height() + 26.0),
            source.upper(),
        )
        painter.restore()

    def _paint_loading(self, painter: QPainter, rect: QRectF, accent: QColor, muted: QColor, now: float) -> None:
        phase = (now * 0.55) % 1.0
        sweep = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        sweep.setColorAt(max(0.0, phase - 0.22), _tint(accent, 0))
        sweep.setColorAt(min(1.0, max(0.0, phase)), _tint(accent, 16))
        sweep.setColorAt(min(1.0, phase + 0.22), _tint(accent, 0))
        painter.fillRect(rect, QBrush(sweep))
        painter.setFont(self._font_meta)
        painter.setPen(_tint(muted, 175))
        dots = "·" * (int(now * 3.0) % 3 + 1)
        text = f"GÖRSELLER HAZIRLANIYOR {dots}"
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(text)
        painter.drawText(
            QPointF(rect.center().x() - text_width / 2.0, rect.center().y() + 4.0),
            text,
        )
