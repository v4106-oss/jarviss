"""Jarvis komuta güvertesi için ortak kabuk bileşenleri."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.theme import ultron as U
from app.theme.themes import rgba_css
from .animations import animate_float
from .hud_widgets import hud_font, hud_label


# --------------------------------------------------------------------------- #
# Vektörel ikonlar
# --------------------------------------------------------------------------- #

def _arc_path(rect: QRectF, start_deg: float, span_deg: float) -> QPainterPath:
    path = QPainterPath()
    path.arcMoveTo(rect, start_deg)
    path.arcTo(rect, start_deg, span_deg)
    return path


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def glyph_path(key: str) -> QPainterPath:
    """24x24 kutu içinde normalleştirilmiş ikon yolu."""
    path = QPainterPath()
    key = str(key or "").strip().lower()
    if key == "chat":
        path.addPath(_rounded(QRectF(3, 4, 18, 13.5), 4))
        path.moveTo(8.5, 17.5)
        path.lineTo(7.2, 21.2)
        path.lineTo(12.6, 17.5)
    elif key == "spark":
        path.moveTo(12, 2.2)
        path.lineTo(14.3, 9.7)
        path.lineTo(21.8, 12)
        path.lineTo(14.3, 14.3)
        path.lineTo(12, 21.8)
        path.lineTo(9.7, 14.3)
        path.lineTo(2.2, 12)
        path.lineTo(9.7, 9.7)
        path.closeSubpath()
    elif key == "search":
        path.addEllipse(QPointF(10.5, 10.5), 5.6, 5.6)
        path.moveTo(14.7, 14.7)
        path.lineTo(20.5, 20.5)
    elif key == "mic":
        path.addPath(_rounded(QRectF(9, 2.4, 6, 11.2), 3))
        path.moveTo(5.4, 11.2)
        path.cubicTo(5.4, 15, 8.2, 17.4, 12, 17.4)
        path.cubicTo(15.8, 17.4, 18.6, 15, 18.6, 11.2)
        path.moveTo(12, 17.4)
        path.lineTo(12, 21.2)
        path.moveTo(8.4, 21.2)
        path.lineTo(15.6, 21.2)
    elif key == "camera":
        path.addPath(_rounded(QRectF(2.8, 6.8, 18.4, 12.4), 3.2))
        path.moveTo(8.2, 6.8)
        path.lineTo(9.8, 4.2)
        path.lineTo(14.2, 4.2)
        path.lineTo(15.8, 6.8)
        path.addEllipse(QPointF(12, 13), 3.8, 3.8)
    elif key == "map":
        path.moveTo(12, 2.4)
        path.cubicTo(7.5, 2.4, 4, 5.9, 4, 10.3)
        path.cubicTo(4, 16, 12, 21.8, 12, 21.8)
        path.cubicTo(12, 21.8, 20, 16, 20, 10.3)
        path.cubicTo(20, 5.9, 16.5, 2.4, 12, 2.4)
        path.closeSubpath()
        path.addEllipse(QPointF(12, 10), 2.6, 2.6)
    elif key == "book":
        path.moveTo(4, 4.6)
        path.cubicTo(7.2, 3.4, 10.2, 3.8, 12, 5.8)
        path.cubicTo(13.8, 3.8, 16.8, 3.4, 20, 4.6)
        path.lineTo(20, 19)
        path.cubicTo(16.8, 18, 13.8, 18.2, 12, 20.1)
        path.cubicTo(10.2, 18.2, 7.2, 18, 4, 19)
        path.closeSubpath()
        path.moveTo(12, 5.8)
        path.lineTo(12, 20.1)
    elif key == "clock":
        path.addEllipse(QPointF(12, 12), 9, 9)
        path.moveTo(12, 6.6)
        path.lineTo(12, 12)
        path.lineTo(16.2, 14.2)
    elif key == "screen":
        path.addPath(_rounded(QRectF(2.8, 4, 18.4, 12.4), 2.6))
        path.moveTo(9.2, 20.2)
        path.lineTo(14.8, 20.2)
        path.moveTo(12, 16.4)
        path.lineTo(12, 20.2)
    elif key == "settings":
        path.addEllipse(QPointF(12, 12), 3.1, 3.1)
        for index in range(8):
            angle = math.radians(index * 45)
            path.moveTo(12 + math.cos(angle) * 7.2, 12 + math.sin(angle) * 7.2)
            path.lineTo(12 + math.cos(angle) * 9.4, 12 + math.sin(angle) * 9.4)
    elif key == "bell":
        path.moveTo(6.2, 16.4)
        path.cubicTo(7.4, 14.4, 7.2, 12.2, 7.2, 10.2)
        path.cubicTo(7.2, 6.6, 9.4, 4.2, 12, 4.2)
        path.cubicTo(14.6, 4.2, 16.8, 6.6, 16.8, 10.2)
        path.cubicTo(16.8, 12.2, 16.6, 14.4, 17.8, 16.4)
        path.closeSubpath()
        path.moveTo(10.1, 19.2)
        path.cubicTo(10.6, 20.6, 13.4, 20.6, 13.9, 19.2)
    elif key == "globe":
        path.addEllipse(QPointF(12, 12), 9, 9)
        path.addEllipse(QPointF(12, 12), 4, 9)
        path.moveTo(3.2, 12)
        path.lineTo(20.8, 12)
        path.moveTo(4.8, 7.2)
        path.lineTo(19.2, 7.2)
        path.moveTo(4.8, 16.8)
        path.lineTo(19.2, 16.8)
    elif key == "browser":
        path.addPath(_rounded(QRectF(2.8, 3.4, 18.4, 17.2), 3.0))
        path.moveTo(2.8, 8.4)
        path.lineTo(21.2, 8.4)
        path.addEllipse(QPointF(12, 14.6), 3.6, 3.6)
        path.addEllipse(QPointF(12, 14.6), 1.4, 3.6)
        path.moveTo(8.4, 14.6)
        path.lineTo(15.6, 14.6)
    elif key == "plus":
        path.moveTo(12, 4.4)
        path.lineTo(12, 19.6)
        path.moveTo(4.4, 12)
        path.lineTo(19.6, 12)
    elif key == "send":
        path.moveTo(3.2, 11.2)
        path.lineTo(20.8, 3.4)
        path.lineTo(14.2, 20.8)
        path.lineTo(11.1, 13.2)
        path.closeSubpath()
        path.moveTo(11.1, 13.2)
        path.lineTo(20.8, 3.4)
    elif key == "stop":
        path.addPath(_rounded(QRectF(7, 7, 10, 10), 2.2))
    elif key == "pause":
        path.addPath(_rounded(QRectF(7.4, 6, 3.4, 12), 1.4))
        path.addPath(_rounded(QRectF(13.2, 6, 3.4, 12), 1.4))
    elif key == "play":
        path.moveTo(8.4, 5.8)
        path.lineTo(18.4, 12)
        path.lineTo(8.4, 18.2)
        path.closeSubpath()
    elif key == "power":
        path.addPath(_arc_path(QRectF(3.6, 3.6, 16.8, 16.8), 118, 304))
        path.moveTo(12, 2.4)
        path.lineTo(12, 11.2)
    elif key == "expand":
        path.moveTo(4.2, 9.2)
        path.lineTo(4.2, 4.2)
        path.lineTo(9.2, 4.2)
        path.moveTo(14.8, 4.2)
        path.lineTo(19.8, 4.2)
        path.lineTo(19.8, 9.2)
        path.moveTo(4.2, 14.8)
        path.lineTo(4.2, 19.8)
        path.lineTo(9.2, 19.8)
        path.moveTo(14.8, 19.8)
        path.lineTo(19.8, 19.8)
        path.lineTo(19.8, 14.8)
    elif key == "collapse":
        path.moveTo(9.2, 4.2)
        path.lineTo(9.2, 9.2)
        path.lineTo(4.2, 9.2)
        path.moveTo(14.8, 4.2)
        path.lineTo(14.8, 9.2)
        path.lineTo(19.8, 9.2)
        path.moveTo(9.2, 19.8)
        path.lineTo(9.2, 14.8)
        path.lineTo(4.2, 14.8)
        path.moveTo(14.8, 19.8)
        path.lineTo(14.8, 14.8)
        path.lineTo(19.8, 14.8)
    elif key == "copy":
        path.addPath(_rounded(QRectF(8.4, 8.4, 11.4, 11.4), 2.4))
        path.moveTo(15.6, 8.4)
        path.lineTo(15.6, 6.6)
        path.cubicTo(15.6, 5.4, 14.8, 4.6, 13.6, 4.6)
        path.lineTo(6.4, 4.6)
        path.cubicTo(5.2, 4.6, 4.4, 5.4, 4.4, 6.6)
        path.lineTo(4.4, 13.8)
        path.cubicTo(4.4, 15, 5.2, 15.8, 6.4, 15.8)
        path.lineTo(8.4, 15.8)
    elif key == "check":
        path.moveTo(4.2, 12.6)
        path.lineTo(9.4, 17.8)
        path.lineTo(19.8, 6.4)
    elif key == "grid":
        for x, y in ((4, 4), (13.4, 4), (4, 13.4), (13.4, 13.4)):
            path.addPath(_rounded(QRectF(x, y, 6.6, 6.6), 1.8))
    elif key == "layers":
        path.moveTo(12, 3.2)
        path.lineTo(21, 7.8)
        path.lineTo(12, 12.4)
        path.lineTo(3, 7.8)
        path.closeSubpath()
        path.moveTo(3.8, 12.2)
        path.lineTo(12, 16.5)
        path.lineTo(20.2, 12.2)
        path.moveTo(3.8, 16.2)
        path.lineTo(12, 20.5)
        path.lineTo(20.2, 16.2)
    elif key == "user":
        path.addEllipse(QPointF(12, 8.2), 3.6, 3.6)
        path.moveTo(4.6, 20.2)
        path.cubicTo(5.4, 15.8, 8.2, 13.6, 12, 13.6)
        path.cubicTo(15.8, 13.6, 18.6, 15.8, 19.4, 20.2)
    elif key == "menu":
        path.moveTo(4, 6.4)
        path.lineTo(20, 6.4)
        path.moveTo(4, 12)
        path.lineTo(20, 12)
        path.moveTo(4, 17.6)
        path.lineTo(20, 17.6)
    elif key == "arrow":
        path.moveTo(4.2, 12)
        path.lineTo(19.2, 12)
        path.moveTo(13, 5.8)
        path.lineTo(19.2, 12)
        path.lineTo(13, 18.2)
    else:
        path.addEllipse(QPointF(12, 12), 7.4, 7.4)
    return path


def draw_glyph(
    painter: QPainter,
    key: str,
    rect: QRectF,
    color: QColor,
    width: float = 1.6,
) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = min(rect.width(), rect.height()) / 24.0
    painter.translate(rect.x(), rect.y())
    painter.scale(scale, scale)
    pen = QPen(color, max(0.8, width / max(scale, 0.001)))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(glyph_path(key))
    painter.restore()


class GlyphIcon(QWidget):
    def __init__(self, key: str = "spark", size: int = 18, color: str = U.TEXT_SOFT, parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._color = QColor(color)
        self.setFixedSize(QSize(size, size))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_glyph(self, key: str) -> None:
        self._key = key
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        draw_glyph(painter, self._key, QRectF(1.4, 1.4, self.width() - 2.8, self.height() - 2.8), self._color, 1.7)
        painter.end()


class _MotionMixin:
    """0..1 arası değerleri yumuşakça kaydıran küçük yardımcı."""

    def _animate_value(self, name: str, target: float, duration: int = 150) -> None:
        animate_float(self, name, target, duration)


def _mix(base: QColor, target: QColor, amount: float) -> QColor:
    t = max(0.0, min(1.0, float(amount)))
    return QColor(
        int(base.red() + (target.red() - base.red()) * t),
        int(base.green() + (target.green() - base.green()) * t),
        int(base.blue() + (target.blue() - base.blue()) * t),
        int(base.alpha() + (target.alpha() - base.alpha()) * t),
    )


class GlyphButton(QAbstractButton, _MotionMixin):
    """Dairesel/kare HUD ikon düğmesi; durum rengi ve nabız noktası taşır."""

    def __init__(
        self,
        key: str,
        tooltip: str = "",
        size: int = 34,
        checkable: bool = False,
        accent: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._accent = accent
        self._hover = False
        self._active = False
        self._hover_value = 0.0
        self._active_value = 0.0
        self._pulse_value = 0.0
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(size, size))
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip or key)
        self._size = size

    def set_glyph(self, key: str) -> None:
        self._key = key
        self.update()

    def glyph(self) -> str:
        return self._key

    def set_active(self, active: bool) -> None:
        if self.isCheckable():
            self.setChecked(bool(active))
        self._active = bool(active)
        if self.isVisible():
            animate_float(self, "_active_value", 1.0 if self._active else 0.0, 180)
        else:
            self._active_value = 1.0 if self._active else 0.0
            self.update()

    def is_active(self) -> bool:
        return bool(self._active or (self.isCheckable() and self.isChecked()))

    def set_accent(self, accent: bool) -> None:
        self._accent = bool(accent)
        self.update()

    def pulse(self) -> None:
        self._pulse_value = 0.0
        self._animate_value("_pulse_value", 1.0, 420)

    def enterEvent(self, event):
        self._hover = True
        self._animate_value("_hover_value", 1.0, 130)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._animate_value("_hover_value", 0.0, 190)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hover = max(0.0, min(1.0, self._hover_value))
        active = max(0.0, min(1.0, self._active_value))
        enabled = self.isEnabled()
        transparent = QColor(0, 0, 0, 0)
        red = QColor(U.RED)
        red_bright = QColor(U.RED_BRIGHT if not U.IS_LIGHT else U.RED)

        if self._accent:
            fill = QColor(red)
            fill.setAlpha(int((26 if not U.IS_LIGHT else 16) + 12 * hover + 8 * active))
            border = QColor(red)
            border.setAlpha(int(80 + 20 * hover + 25 * active))
        else:
            ghost = QColor(U.TEXT)
            ghost.setAlpha(14 if not U.IS_LIGHT else 18)
            hover_fill = _mix(transparent, ghost, hover)
            active_fill = QColor(red)
            active_fill.setAlpha(34 if not U.IS_LIGHT else 20)
            fill = _mix(hover_fill, active_fill, active)
            ghost_border = QColor(U.TEXT)
            ghost_border.setAlpha(38)
            active_border = QColor(red)
            active_border.setAlpha(105)
            border = _mix(_mix(transparent, ghost_border, hover), active_border, active)

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        radius = min(rect.width(), rect.height()) / 2
        painter.drawRoundedRect(rect, radius, radius)

        pulse = max(0.0, min(1.0, self._pulse_value))
        if pulse > 0.01:
            ring = QColor(red_bright)
            ring.setAlpha(int(150 * (1.0 - pulse)))
            painter.setPen(QPen(ring, 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring_radius = radius * (0.42 + 0.5 * pulse)
            painter.drawEllipse(rect.center(), ring_radius, ring_radius)

        pad = self._size * 0.28
        if not enabled:
            color = QColor(U.FAINT)
        elif self._accent:
            color = QColor(red_bright)
        else:
            color = _mix(QColor(U.MUTED), QColor(U.TEXT_SOFT), hover)
            color = _mix(color, red_bright, active)
        draw_glyph(
            painter,
            self._key,
            QRectF(pad, pad, self._size - pad * 2, self._size - pad * 2),
            color,
            1.65,
        )
        if active > 0.02 and self._key in ("mic", "camera", "screen"):
            dot = QColor(red_bright)
            dot.setAlpha(int(255 * active))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(rect.right() - 4.5, rect.top() + 4.5), 2.2, 2.2)
        painter.end()


class NavButton(QAbstractButton, _MotionMixin):
    """Dikey gezinme rayının ikon + kısa etiket düğmesi."""

    def __init__(self, key: str, label: str, tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._label = str(label or "")
        self._hover = False
        self._active = False
        self._hover_value = 0.0
        self._active_value = 0.0
        self._press_value = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(62, 52))
        self.setToolTip(tooltip or label)
        self.setAccessibleName(tooltip or label)
        self.setCheckable(True)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if self.isCheckable():
            self.setChecked(self._active)
        if self.isVisible():
            animate_float(self, "_active_value", 1.0 if self._active else 0.0, 190)
        else:
            self._active_value = 1.0 if self._active else 0.0
            self.update()

    def is_active(self) -> bool:
        return self._active

    def enterEvent(self, event):
        self._hover = True
        self._animate_value("_hover_value", 1.0, 130)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._animate_value("_hover_value", 0.0, 190)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._animate_value("_press_value", 1.0, 90)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._animate_value("_press_value", 0.0, 220)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(3, 2, -3, -2)
        hover = max(0.0, min(1.0, self._hover_value))
        active = max(0.0, min(1.0, self._active_value))
        press = max(0.0, min(1.0, self._press_value))
        transparent = QColor(0, 0, 0, 0)
        red = QColor(U.RED)
        red_bright = QColor(U.RED_BRIGHT if not U.IS_LIGHT else U.RED)
        ghost = QColor(U.TEXT)
        ghost.setAlpha(10 if not U.IS_LIGHT else 14)
        active_fill = QColor(red)
        active_fill.setAlpha(18 if not U.IS_LIGHT else 12)
        press_fill = QColor(red_bright)
        press_fill.setAlpha(14 if not U.IS_LIGHT else 9)
        fill = _mix(_mix(transparent, ghost, hover), active_fill, active)
        fill = _mix(fill, press_fill, press)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        squeeze = 1.5 * press
        painter.drawRoundedRect(rect.adjusted(squeeze, squeeze, -squeeze, -squeeze), 12, 12)
        if not self.isEnabled():
            color = QColor(U.FAINT)
        else:
            color = _mix(QColor(U.MUTED), QColor(U.TEXT_SOFT), hover)
            color = _mix(color, red_bright, active)
            color = _mix(color, QColor(U.TEXT), 0.35 * press)
        icon_rect = QRectF(rect.center().x() - 9, rect.top() + 7, 18, 18)
        icon_rect = icon_rect.adjusted(press * 0.8, press * 0.8, -press * 0.8, -press * 0.8)
        draw_glyph(painter, self._key, icon_rect, color, 1.55)
        font = hud_font(6, QFont.Weight.Medium, 0.2)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            QRectF(rect.left(), rect.top() + 29, rect.width(), 14),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._label,
        )
        painter.end()


class BrandMark(QWidget):
    """Sade marka işareti: ince halka ve tek vurgu noktası."""

    def __init__(self, size: int = 38, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(size, size))

    def set_animating(self, enabled: bool) -> None:
        return

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        border = QColor(U.TEXT)
        border.setAlpha(20)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(QColor(U.PANEL_2))
        painter.drawRoundedRect(rect, 11, 11)

        center = rect.center()
        ring = QRectF(0, 0, rect.width() * 0.42, rect.height() * 0.42)
        ring.moveCenter(center)
        accent = QColor(U.RED_BRIGHT if not U.IS_LIGHT else U.RED)
        accent.setAlpha(190)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 1.5))
        painter.drawEllipse(ring)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(U.RED))
        painter.drawEllipse(center, 2.5, 2.5)
        painter.end()


class PulseDot(QWidget):
    """Nefes alan durum noktası."""

    def __init__(self, color: str = U.RED, size: int = 12, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._phase = 0.0
        self._active = True
        self._burst = 0.0
        self.setFixedSize(QSize(size, size))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_animating(self, enabled: bool) -> None:
        if enabled and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.08) % math.tau
        if self._burst > 0.0:
            self._burst = max(0.0, self._burst - 0.09)
        self.update()

    def set_color(self, color: str) -> None:
        fresh = QColor(color)
        if fresh != self._color:
            self._burst = 1.0
        self._color = fresh
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QPointF(self.width() / 2, self.height() / 2)
        base = min(self.width(), self.height())
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        if self._burst > 0.01:
            ring = QColor(self._color)
            ring.setAlpha(int(95 * self._burst))
            painter.setPen(QPen(ring, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = base * (0.30 + 0.20 * (1.0 - self._burst))
            painter.drawEllipse(center, radius, radius)
        halo = QColor(self._color)
        halo.setAlpha(int((46 + 46 * pulse + 60 * self._burst) if self._active else 24))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, base * 0.48, base * 0.48)
        core = QColor(self._color)
        core.setAlpha(240 if self._active else 120)
        painter.setBrush(core)
        painter.drawEllipse(center, base * 0.20, base * 0.20)
        painter.end()


# --------------------------------------------------------------------------- #
# Başlık güvertesi
# --------------------------------------------------------------------------- #

class TextChip(QPushButton):
    """Kapsül biçimli durum çipi; aktif rengi QSS özelliğiyle değiştirir."""

    def __init__(self, text: str = "", tooltip: str = "", dot: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._label = str(text or "")
        self._dot = dot
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(hud_font(7, QFont.Weight.Medium, 0.2))
        self.setToolTip(tooltip)
        self.setMinimumHeight(26)
        self.setStyleSheet(self._qss())
        self.set_active(False)
        self._render()

    def _qss(self) -> str:
        return f"""
        QPushButton {{
            background:transparent;
            color:{U.MUTED};
            border:1px solid {U.BORDER};
            border-radius:13px;
            padding:4px 11px;
        }}
        QPushButton:hover {{
            color:{U.TEXT_SOFT};
            border-color:{rgba_css(U.TEXT, 0.16)};
            background:{rgba_css(U.TEXT, 0.035)};
        }}
        QPushButton[active="true"] {{
            color:{U.RED_BRIGHT if not U.IS_LIGHT else U.TEXT};
            border-color:{rgba_css(U.RED, 0.34)};
            background:{rgba_css(U.RED, 0.10 if not U.IS_LIGHT else 0.07)};
        }}
        """

    def _render(self) -> None:
        self.setText(("●  " if self._dot else "") + self._label)

    def set_label(self, text: str) -> None:
        self._label = str(text or "")
        self._render()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    def is_active(self) -> bool:
        return self.property("active") == "true"


class MicLevelMeter(QWidget):
    """Mikrofona ulaşan sesin canlı seviyesi; söz algılanınca kısa bir parlama yapar."""

    BARS = 9

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._shown = 0.0
        self._heard = 0.0
        self._enabled = False
        self._accent = QColor("#0e9d8f" if U.IS_LIGHT else "#2fd6c3")
        self.setFixedSize(58, 22)
        self.setToolTip("Mikrofona ulaşan ses seviyesi; konuşunca çubuklar oynar.")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._level = 0.0
        self._ensure_timer()
        self.update()

    def set_level(self, level: float) -> None:
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        self._level = value if self._enabled else 0.0
        self._ensure_timer()
        self.update()

    def pulse(self) -> None:
        self._heard = 1.0
        self._ensure_timer()
        self.update()

    def _ensure_timer(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        self._shown += (self._level - self._shown) * 0.5
        if self._heard > 0.0:
            self._heard = max(0.0, self._heard - 0.07)
        self.update()
        if self._shown < 0.012 and self._heard <= 0.0:
            self._shown = 0.0
            self._timer.stop()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.width(), self.height()
        bar_width = 3.4
        gap = (width - self.BARS * bar_width) / (self.BARS - 1)
        base = QColor(U.FAINT)
        base.setAlpha(110 if U.IS_LIGHT else 80)
        for index in range(self.BARS):
            envelope = math.sin(math.pi * (index + 0.5) / self.BARS) ** 0.6
            lit = self._shown >= (index + 0.5) / self.BARS * 0.92
            bar_height = 3.0 + (height - 4.0) * 0.86 * envelope * (0.22 + 0.78 * self._shown)
            if self._heard > 0.0 and lit:
                bar_height = max(bar_height, (height - 4.0) * envelope)
            color = QColor(self._accent) if (self._enabled and lit) else QColor(base)
            if self._heard > 0.0:
                boost = int(150 * self._heard)
                color = QColor(
                    min(255, color.red() + boost),
                    min(255, color.green() + boost),
                    min(255, color.blue() + boost),
                )
            x = index * (bar_width + gap)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(x, (height - bar_height) / 2.0, bar_width, bar_height),
                bar_width / 2.0,
                bar_width / 2.0,
            )
        painter.end()


class CommandPill(QFrame):
    """Ortadaki komut arama kapsülü; tıklanınca komut paletini açar."""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover = False
        self._hover_value = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setMinimumWidth(240)
        self.setMaximumWidth(560)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 0, 9, 0)
        layout.setSpacing(9)
        self.icon = GlyphIcon("search", 16, U.MUTED)
        layout.addWidget(self.icon)
        self.hint = hud_label("Bir komut yaz veya ara…", 9, U.DIM, QFont.Weight.Medium, 0.2)
        layout.addWidget(self.hint, 1)
        badge = QFrame()
        badge.setStyleSheet(
            f"background:{rgba_css(U.TEXT, 0.04)};border:1px solid {U.BORDER};border-radius:7px;"
        )
        badge_layout = QHBoxLayout(badge)
        badge_layout.setContentsMargins(7, 3, 7, 3)
        badge_layout.addWidget(hud_label("Ctrl K", 7, U.DIM, QFont.Weight.Medium, 0.1))
        layout.addWidget(badge)

    def set_hint(self, text: str) -> None:
        self.hint.setText(str(text or ""))

    def enterEvent(self, event):
        self._hover = True
        animate_float(self, "_hover_value", 1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        animate_float(self, "_hover_value", 0.0, 200)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = max(0.0, min(1.0, self._hover_value))
        base = _mix(QColor(U.PANEL_2), QColor(U.PANEL_HOVER), hover)
        painter.setBrush(base)
        border = _mix(QColor(U.TEXT), QColor(U.TEXT), 0.0)
        border.setAlpha(int(26 + 28 * hover))
        painter.setPen(QPen(border, 1.0))
        painter.drawRoundedRect(rect, 12, 12)
        painter.end()


class ComposerFrame(QFrame):
    """Yazma alanı çerçevesi; odaklanınca vurgu halkası belirir."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._focus_value = 0.0

    def track(self, widget: QWidget) -> None:
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.FocusIn:
            animate_float(self, "_focus_value", 1.0, 200)
        elif event.type() == QEvent.Type.FocusOut:
            animate_float(self, "_focus_value", 0.0, 240)
        return super().eventFilter(obj, event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._focus_value <= 0.01:
            return
        color = min(1.0, max(0.0, self._focus_value))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.2, 1.2, -1.2, -1.2)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        glow = QColor(U.RED)
        glow.setAlpha(int(22 * color))
        painter.setPen(QPen(glow, 4.0))
        painter.drawRoundedRect(rect.adjusted(0.4, 0.4, -0.4, -0.4), 15, 15)
        accent = QColor(U.RED)
        accent.setAlpha(int(150 * color))
        painter.setPen(QPen(accent, 1.1))
        painter.drawRoundedRect(rect, 15, 15)
        painter.end()


# --------------------------------------------------------------------------- #
# Segment sekmeler ve kartlar
# --------------------------------------------------------------------------- #

class SegmentedControl(QFrame):
    currentChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"""
            QFrame#segmentBar {{
                background:{rgba_css(U.TEXT, 0.025)};
                border:1px solid {U.BORDER};
                border-radius:9px;
            }}
            """
        )
        self.setObjectName("segmentBar")
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 3, 3, 3)
        self._row.setSpacing(2)
        self._buttons = []
        self._current = -1

    def add_segment(self, label: str) -> int:
        index = len(self._buttons)
        button = QPushButton(str(label))
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFont(hud_font(7, QFont.Weight.Medium, 0.2))
        button.setStyleSheet(
            f"""
            QPushButton {{
                background:transparent;
                color:{U.MUTED};
                border:1px solid transparent;
                border-radius:7px;
                padding:5px 9px;
            }}
            QPushButton:hover {{ color:{U.TEXT_SOFT}; background:{rgba_css(U.TEXT, 0.04)}; }}
            QPushButton:checked {{
                color:{U.TEXT};
                background:{rgba_css(U.RED, 0.10 if not U.IS_LIGHT else 0.07)};
                border-color:{rgba_css(U.RED, 0.22)};
            }}
            """
        )
        button.clicked.connect(lambda _checked=False, i=index: self.set_current(i))
        self._row.addWidget(button)
        self._buttons.append(button)
        if self._current < 0:
            self.set_current(0)
        return index

    def set_current(self, index: int) -> None:
        if not self._buttons:
            return
        index = max(0, min(len(self._buttons) - 1, int(index)))
        self._current = index
        for position, button in enumerate(self._buttons):
            button.setChecked(position == index)
        self.currentChanged.emit(index)

    def current(self) -> int:
        return self._current


class SegmentedStack(QWidget):
    """Üstte segment şeridi, altında sayfa yığını."""

    currentChanged = Signal(int)

    def __init__(self, labels=(), parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.control = SegmentedControl()
        self.stack = QStackedWidget()
        layout.addWidget(self.control)
        layout.addWidget(self.stack, 1)
        self.control.currentChanged.connect(self._on_changed)
        for label in labels:
            self.add_page(QWidget(), label)

    def add_page(self, widget: QWidget, label: str) -> int:
        index = self.control.add_segment(label)
        self.stack.addWidget(widget)
        return index

    def _on_changed(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
        self.currentChanged.emit(index)

    def set_current(self, index: int) -> None:
        self.control.set_current(index)


class HudCard(QFrame):
    """Yeni yüzey kartı: başlık, isteğe bağlı ikon ve gövde düzeni."""

    clicked = Signal()

    def __init__(
        self,
        title: str = "",
        trailing: str = "",
        glyph: str = "",
        padding: int = 13,
        clickable: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._clickable = bool(clickable)
        self.setObjectName("hudCard")
        self.setStyleSheet(self._qss())
        if self._clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding - 1, padding, padding)
        outer.setSpacing(8)

        self.header = QHBoxLayout()
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setSpacing(7)
        self.icon = None
        if glyph:
            self.icon = GlyphIcon(glyph, 15, U.RED_SOFT)
            self.header.addWidget(self.icon)
        self.title = hud_label(title, 8, U.TEXT_SOFT, QFont.Weight.Medium, 0.2)
        # Uzun başlık dar sütunda kartı şişirmek yerine kısalsın.
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        self.trailing = hud_label(trailing, 7, U.DIM, QFont.Weight.Medium, 0.2)
        self.header.addWidget(self.trailing)
        if title or trailing or glyph:
            outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        outer.addLayout(self.body)

    def _qss(self) -> str:
        hover = (
            f"QFrame#hudCard:hover {{ border-color:{rgba_css(U.TEXT, 0.14)}; "
            f"background:{rgba_css(U.PANEL_HOVER, 0.72)}; }}"
            if self._clickable
            else ""
        )
        return f"""
        QFrame#hudCard {{
            background:{rgba_css(U.PANEL, 0.50)};
            border:1px solid {rgba_css(U.TEXT, 0.06)};
            border-radius:16px;
        }}
        {hover}
        """

    def set_trailing(self, text: str) -> None:
        self.trailing.setText(str(text))

    def set_glyph(self, key: str) -> None:
        if self.icon is not None:
            self.icon.set_glyph(key)

    def mouseReleaseEvent(self, event):
        if (
            self._clickable
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StatusRow(QWidget):
    """Küçük etiket + değer satırı; kartların içinde kullanılır."""

    def __init__(self, label: str, value: str = "—", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.label = hud_label(label, 7, U.DIM, QFont.Weight.Medium, 0.2)
        self.value = hud_label(value, 7, U.TEXT_SOFT, QFont.Weight.Medium, 0.2)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.label)
        layout.addStretch(1)
        layout.addWidget(self.value)

    def set_value(self, text: str) -> None:
        self.value.setText(str(text))


class SectionLabel(QWidget):
    """Panel içi bölüm etiketi; ince çizgi ve sayaç taşır."""

    def __init__(self, title: str, trailing: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 0, 1, 0)
        layout.setSpacing(8)
        self.title = hud_label(title, 7, U.MUTED, QFont.Weight.Medium, 0.3)
        self.trailing = hud_label(trailing, 6, U.FAINT, QFont.Weight.Medium, 0.2)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{rgba_css(U.TEXT, 0.05)};border:none;")
        layout.addWidget(self.title)
        layout.addWidget(line, 1)
        layout.addWidget(self.trailing)
