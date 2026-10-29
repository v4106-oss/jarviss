"""Jarvis açılış, yükleme ve geçiş animasyonları."""

from __future__ import annotations

import math
from typing import Callable, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
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
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from app.theme import ultron as U
from app.theme.themes import rgba_css


def _tinted(color: QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(max(0, min(255, int(alpha))))
    return tinted


def _stop_window_fade(widget: QWidget) -> None:
    animation = getattr(widget, "_window_fade", None)
    if animation is not None:
        try:
            animation.stop()
        except RuntimeError:
            pass


def fade_window_in(widget: QWidget, duration: int = 180) -> Optional[QPropertyAnimation]:
    """Ayrı pencereyi karartmadan yumuşakça görünür yapar."""
    try:
        _stop_window_fade(widget)
        widget.setWindowOpacity(0.0)
        animation = QPropertyAnimation(widget, b"windowOpacity", widget)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        widget._window_fade = animation
        animation.start()
        return animation
    except RuntimeError:
        return None


def fade_window_out(
    widget: QWidget,
    duration: int = 160,
    on_finished: Optional[Callable[[], None]] = None,
) -> Optional[QPropertyAnimation]:
    try:
        _stop_window_fade(widget)
        animation = QPropertyAnimation(widget, b"windowOpacity", widget)
        animation.setDuration(duration)
        animation.setStartValue(float(widget.windowOpacity()))
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.InCubic)
        if on_finished is not None:
            animation.finished.connect(on_finished)
        widget._window_fade = animation
        animation.start()
        return animation
    except RuntimeError:
        if on_finished is not None:
            on_finished()
        return None


def animate_float(owner, name: str, target: float, duration: int = 150) -> None:
    """Sahibindeki 0..1 değerini yumuşakça hedefe kaydırır, her adımda yeniden çizer."""
    key = "_anim_" + name
    animation = getattr(owner, key, None)
    if animation is not None:
        try:
            animation.stop()
            animation.deleteLater()
        except RuntimeError:
            pass
        setattr(owner, key, None)
    start = float(getattr(owner, name, 0.0))
    target = float(target)
    if abs(start - target) < 0.005:
        setattr(owner, name, target)
        try:
            owner.update()
        except RuntimeError:
            pass
        return
    animation = QVariantAnimation(owner)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(target)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.valueChanged.connect(lambda value, o=owner, n=name: _set_float(o, n, value))
    animation.finished.connect(lambda o=owner, n=name, a=animation: _release_float(o, n, a))
    setattr(owner, key, animation)
    animation.start()


def _set_float(owner, name: str, value) -> None:
    try:
        setattr(owner, name, float(value))
        owner.update()
    except RuntimeError:
        pass


def _release_float(owner, name: str, animation) -> None:
    try:
        if getattr(owner, "_anim_" + name, None) is animation:
            setattr(owner, "_anim_" + name, None)
    except RuntimeError:
        return
    animation.deleteLater()


def pop_in(widget: QWidget, duration: int = 220, rise: int = 9) -> None:
    """Sohbet balonunu belirginleştirip hafifçe yukarı kaydırarak yerleştirir."""
    try:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
    except RuntimeError:
        return

    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    def release_effect() -> None:
        try:
            widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    fade.finished.connect(release_effect)
    widget._intro_fade = fade
    fade.start()

    layout = widget.layout()
    if layout is None or rise <= 0:
        return
    base = layout.contentsMargins()

    def settle(value) -> None:
        try:
            layout.setContentsMargins(
                base.left(),
                base.top() + int(rise * (1.0 - float(value))),
                base.right(),
                base.bottom(),
            )
        except RuntimeError:
            pass

    growth = QVariantAnimation(widget)
    growth.setDuration(duration)
    growth.setStartValue(0.0)
    growth.setEndValue(1.0)
    growth.setEasingCurve(QEasingCurve.Type.OutCubic)
    growth.valueChanged.connect(settle)
    growth.finished.connect(
        lambda: layout.setContentsMargins(base.left(), base.top(), base.right(), base.bottom())
    )
    widget._intro_settle = growth
    growth.start()


class MiniOrb(QWidget):
    """Splash ve yükleme rozetlerinde kullanılan küçük, dönen çekirdek."""

    def __init__(
        self,
        size: int = 96,
        parent: Optional[QWidget] = None,
        accent: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._phase = 0.0
        self._accent = QColor(accent or U.RED_BRIGHT)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.058) % math.tau
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.start()

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        center = rect.center()
        radius = max(6.0, min(rect.width(), rect.height()) / 2.0)
        phase = self._phase
        accent = self._accent

        glow = QRadialGradient(center, radius)
        glow.setColorAt(0.0, _tinted(accent, 92))
        glow.setColorAt(0.55, _tinted(accent, 30))
        glow.setColorAt(1.0, _tinted(accent, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius, radius)

        painter.save()
        painter.translate(center)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        dash = QPen(_tinted(accent, 58), max(0.8, radius * 0.011))
        dash.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(dash)
        painter.drawEllipse(QPointF(0.0, 0.0), radius * 0.9, radius * 0.9)

        arcs = (
            (0.92, 96.0, 1.35, _tinted(accent, 205), 0.0),
            (0.92, 52.0, -0.9, _tinted(QColor(U.GOLD), 160), 150.0),
            (0.76, 74.0, -1.6, _tinted(accent, 120), 265.0),
        )
        for scale, span, speed, color, offset in arcs:
            painter.save()
            painter.rotate(math.degrees(phase * speed) + offset)
            pen = QPen(color, max(1.0, radius * 0.022))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            arc_rect = QRectF(-radius * scale, -radius * scale, radius * scale * 2, radius * scale * 2)
            painter.drawArc(arc_rect, 0, int(span * 16))
            painter.restore()

        painter.save()
        painter.rotate(math.degrees(phase * 0.22))
        ticks = 40
        for index in range(ticks):
            painter.save()
            painter.rotate(index * 360.0 / ticks)
            strong = index % 5 == 0
            pen = QPen(
                _tinted(accent, 108 if strong else 44),
                max(0.8, radius * (0.019 if strong else 0.012)),
            )
            painter.setPen(pen)
            inner = radius * 0.69
            outer = radius * (0.78 if strong else 0.745)
            painter.drawLine(QPointF(0.0, -outer), QPointF(0.0, -inner))
            painter.restore()
        painter.restore()

        painter.save()
        painter.rotate(math.degrees(phase * 0.65))
        painter.setPen(QPen(_tinted(accent, 62), max(0.8, radius * 0.013)))
        painter.drawEllipse(QRectF(-radius * 0.86, -radius * 0.3, radius * 1.72, radius * 0.6))
        painter.rotate(74.0)
        painter.drawEllipse(QRectF(-radius * 0.78, -radius * 0.22, radius * 1.56, radius * 0.44))
        painter.restore()

        core_radius = radius * 0.3
        core = QRadialGradient(
            QPointF(-core_radius * 0.3, -core_radius * 0.35),
            core_radius * 1.45,
        )
        core.setColorAt(0.0, QColor(255, 255, 255, 238))
        core.setColorAt(0.18, _tinted(accent.lighter(150), 235))
        core.setColorAt(0.62, _tinted(accent, 225))
        core.setColorAt(1.0, _tinted(QColor(U.RED_DIM), 200))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(QPointF(0.0, 0.0), core_radius, core_radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_tinted(QColor(U.TEXT), 70), 0.8))
        painter.drawEllipse(QPointF(0.0, 0.0), core_radius, core_radius)
        painter.restore()

        painter.save()
        painter.translate(center)
        painter.rotate(-22.0)
        particles = ((0.84, 1.5, 0.05, accent), (0.6, -2.1, 0.032, QColor(U.GOLD)))
        for index, (distance, speed, size, color) in enumerate(particles):
            angle = phase * speed + index * 2.3
            point = QPointF(
                math.cos(angle) * radius * distance,
                math.sin(angle) * radius * distance * 0.44,
            )
            dot = max(1.4, radius * size)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_tinted(color, 48))
            painter.drawEllipse(point, dot * 2.4, dot * 2.4)
            painter.setBrush(_tinted(color, 230))
            painter.drawEllipse(point, dot, dot)
        painter.restore()
        painter.end()


def _smoothstep(value: float) -> float:
    t = max(0.0, min(1.0, float(value)))
    return t * t * (3.0 - 2.0 * t)


class LaunchOverlay(QWidget):
    """Ana pencerenin üzerinde bir kez oynayan ateşleme efekti."""

    finished = Signal()

    FRAME_MS = 16
    HOLD_MAX_MS = 1900
    EXIT_MS = 240

    def __init__(self, parent: QWidget, duration: int = 1750) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._duration = max(600, int(duration))
        self._elapsed = 0.0
        self._progress = 0.0
        self._holding = False
        self._hold_elapsed = 0.0
        self._exit = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    def eventFilter(self, watched, event):
        parent = self.parentWidget()
        if watched is parent and event.type() == QEvent.Type.Resize and parent is not None:
            self.setGeometry(parent.rect())
        return False

    def start(self) -> None:
        self.show()
        self.raise_()
        self._timer.start()

    def hold(self) -> None:
        """Harita gibi ağır görünümler hazır olana kadar son kareyi tutar."""
        self._holding = True

    def release(self) -> None:
        self._holding = False

    def _tick(self) -> None:
        self._elapsed += self.FRAME_MS
        self._progress = min(1.0, self._elapsed / float(self._duration))
        if self._progress >= 1.0:
            if self._holding and self._hold_elapsed < self.HOLD_MAX_MS:
                self._hold_elapsed += self.FRAME_MS
                self.update()
                return
            self._exit += self.FRAME_MS
            if self._exit < self.EXIT_MS:
                self.update()
                return
            self._timer.stop()
            self.hide()
            self.finished.emit()
            self.deleteLater()
            return
        self.update()

    def paintEvent(self, event) -> None:
        width = self.width()
        height = self.height()
        if width < 80 or height < 80:
            return
        progress = self._progress
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        accent = QColor(U.RED_BRIGHT if not U.IS_LIGHT else U.RED)
        text_soft = QColor(U.TEXT_SOFT)
        center = QPointF(width / 2.0, height * 0.44)
        base = min(width, height) * 0.15

        scrim = 1.0 - _smoothstep(self._exit / float(self.EXIT_MS))
        painter.fillRect(self.rect(), QColor(2, 2, 3, int(250 * scrim)))

        appear = _smoothstep(progress / 0.16)
        content = scrim
        seconds = self._elapsed / 1000.0
        breathe = 0.5 + 0.5 * math.sin(seconds * 3.4)
        alive = appear * content

        halo = QRadialGradient(center, base * 3.3)
        halo.setColorAt(0.0, _tinted(accent, int(44 * alive)))
        halo.setColorAt(0.5, _tinted(accent, int(12 * alive)))
        halo.setColorAt(1.0, _tinted(accent, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(center, base * 3.3, base * 3.3)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        ring = base * 1.04
        painter.setPen(QPen(_tinted(text_soft, int(28 * alive)), 1.0))
        painter.drawEllipse(center, ring, ring)
        painter.setPen(QPen(_tinted(text_soft, int(14 * alive)), 1.0))
        outer = base * 1.32
        painter.drawEllipse(center, outer, outer)

        spin = seconds * 245.0
        arc_rect = QRectF(center.x() - ring, center.y() - ring, ring * 2.0, ring * 2.0)
        arc_pen = QPen(_tinted(accent, int(215 * alive)), 1.9)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(arc_rect, int(-spin * 16), int(104 * 16))
        lead_angle = math.radians(-spin + 104.0)
        lead = QPointF(
            center.x() + ring * math.cos(lead_angle),
            center.y() - ring * math.sin(lead_angle),
        )
        lead_glow = QRadialGradient(lead, 9.0)
        lead_glow.setColorAt(0.0, _tinted(accent, int(150 * alive)))
        lead_glow.setColorAt(1.0, _tinted(accent, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(lead_glow))
        painter.drawEllipse(lead, 9.0, 9.0)
        painter.setBrush(QColor(255, 255, 255, int(235 * alive)))
        painter.drawEllipse(lead, 2.3, 2.3)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        core_radius = base * (0.115 + 0.012 * breathe)
        core = QRadialGradient(center, core_radius * 2.8)
        core.setColorAt(0.0, QColor(255, 255, 255, int(245 * content)))
        core.setColorAt(0.3, _tinted(accent.lighter(130), int(230 * content)))
        core.setColorAt(0.62, _tinted(accent, int(190 * content)))
        core.setColorAt(1.0, _tinted(accent, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(core))
        painter.drawEllipse(center, core_radius * 2.8, core_radius * 2.8)
        painter.setBrush(_tinted(accent, int(235 * content)))
        painter.drawEllipse(center, core_radius * 0.52, core_radius * 0.52)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        title_alpha = _smoothstep((progress - 0.05) / 0.2) * content
        title_y = center.y() + base * 1.92
        if title_alpha > 0.01:
            title_font = QFont(U.FONT, int(21 + 5 * progress))
            title_font.setWeight(QFont.Weight.DemiBold)
            title_font.setLetterSpacing(
                QFont.SpacingType.AbsoluteSpacing, 16.0 - 6.0 * progress
            )
            painter.setFont(title_font)
            painter.setPen(_tinted(QColor(U.TEXT), int(246 * title_alpha)))
            metrics = painter.fontMetrics()
            title = "JARVIS"
            title_width = metrics.horizontalAdvance(title)
            painter.drawText(QPointF(center.x() - title_width / 2.0, title_y), title)

            caption_font = QFont(U.MONO, 7)
            caption_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 5.0)
            painter.setFont(caption_font)
            painter.setPen(_tinted(text_soft, int(150 * title_alpha)))
            caption = "KİŞİSEL ASİSTAN"
            caption_width = painter.fontMetrics().horizontalAdvance(caption)
            painter.drawText(
                QPointF(center.x() - caption_width / 2.0, title_y + 22.0), caption
            )

        status_alpha = _smoothstep((progress - 0.14) / 0.18) * scrim
        if status_alpha > 0.01:
            if self._holding and progress >= 0.8:
                status = "HARİTA ÖN YÜKLENİYOR"
            elif progress < 0.26:
                status = "ÇEKİRDEK BAŞLATILIYOR"
            elif progress < 0.54:
                status = "SERVİSLER BAĞLANIYOR"
            elif progress < 0.8:
                status = "HARİTA VE ARAYÜZ HAZIRLANIYOR"
            else:
                status = "SİSTEM HAZIR"
            dots = "·" * (int(self._elapsed / 180.0) % 3 + 1)
            status_font = QFont(U.MONO, 8)
            status_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.8)
            painter.setFont(status_font)
            painter.setPen(_tinted(text_soft, int(212 * status_alpha)))
            metrics = painter.fontMetrics()
            text = f"{status} {dots}"
            text_width = metrics.horizontalAdvance(text)
            painter.drawText(
                QPointF(center.x() - text_width / 2.0, title_y + 46.0), text
            )

            track_width = min(width * 0.22, base * 2.5)
            track_y = title_y + 60.0
            track = QRectF(center.x() - track_width / 2.0, track_y, track_width, 2.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(30 * status_alpha)))
            painter.drawRoundedRect(track, 1.0, 1.0)
            travel = _smoothstep(min(1.0, progress / 0.94))
            fill = QRectF(track.left(), track.top(), track.width() * travel, 2.0)
            painter.setBrush(_tinted(accent, int(232 * status_alpha)))
            painter.drawRoundedRect(fill, 1.0, 1.0)
            if 0.02 < travel < 0.999:
                tip = QPointF(fill.right(), track.center().y())
                tip_glow = QRadialGradient(tip, 8.0)
                tip_glow.setColorAt(0.0, _tinted(accent, int(140 * status_alpha)))
                tip_glow.setColorAt(1.0, _tinted(accent, 0))
                painter.setBrush(QBrush(tip_glow))
                painter.drawEllipse(tip, 8.0, 8.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.end()


def play_launch_effect(window: QWidget, duration: int = 1750) -> Optional[LaunchOverlay]:
    try:
        overlay = LaunchOverlay(window, duration)
        window._launch_overlay = overlay

        def _release() -> None:
            try:
                if getattr(window, "_launch_overlay", None) is overlay:
                    window._launch_overlay = None
            except RuntimeError:
                pass

        overlay.finished.connect(_release)
        overlay.start()
        return overlay
    except RuntimeError:
        return None


class BusyPill(QFrame):
    """Uzun işlemlerde ana pencerenin üstünde beliren yükleme rozeti."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("busyPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            f"""
            QFrame#busyPill {{
                background:{rgba_css(U.PANEL_2, 0.97)};
                border:1px solid {rgba_css(U.RED, 0.5)};
                border-radius:15px;
            }}
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 2, 14, 2)
        layout.setSpacing(7)
        self.orb = MiniOrb(22, self)
        self.label = QLabel("")
        label_font = QFont(U.FONT, 7)
        label_font.setWeight(QFont.Weight.DemiBold)
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        self.label.setFont(label_font)
        self.label.setStyleSheet(f"color:{U.TEXT_SOFT};background:transparent;border:none;")
        self.label.setMaximumWidth(420)
        layout.addWidget(self.orb)
        layout.addWidget(self.label)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade: Optional[QPropertyAnimation] = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_busy)
        self.hide()

    def _animate(self, target: float, duration: int, on_finished=None) -> None:
        if self._fade is not None:
            try:
                self._fade.stop()
            except RuntimeError:
                pass
        animation = QPropertyAnimation(self._effect, b"opacity", self)
        animation.setDuration(duration)
        animation.setStartValue(float(self._effect.opacity()))
        animation.setEndValue(float(target))
        animation.setEasingCurve(
            QEasingCurve.Type.OutCubic if target > self._effect.opacity() else QEasingCurve.Type.InCubic
        )
        if on_finished is not None:
            animation.finished.connect(on_finished)
        self._fade = animation
        animation.start()

    def show_busy(self, text: str = "") -> None:
        self._hide_timer.stop()
        message = str(text or "").strip().upper()
        if message:
            self.label.setText(message[:82])
        self.adjustSize()
        if not self.isVisible():
            self.show()
            self._animate(1.0, 170)
        elif self._effect.opacity() < 1.0:
            self._animate(1.0, 140)
        self.orb.start()

    def set_text(self, text: str) -> None:
        message = str(text or "").strip().upper()
        if not message:
            return
        self.label.setText(message[:82])
        self.adjustSize()
        if self.isVisible():
            self._animate(1.0, 120)

    def hide_busy(self, delay_ms: int = 0) -> None:
        if delay_ms > 0:
            self._hide_timer.start(delay_ms)
            return
        self._hide_timer.stop()
        if not self.isVisible():
            return

        def finish_hide() -> None:
            self.hide()
            self.orb.stop()

        self._animate(0.0, 150, finish_hide)

    def hideEvent(self, event):
        self.orb.stop()
        super().hideEvent(event)
