"""ULTRON HUD için ortak çizim ve yerleşim bileşenleri."""

from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QSize, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QBrush,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.theme import ultron as U
from app.theme.themes import rgba_css


def round_pen(color, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def hud_font(
    size: int = 10,
    weight: QFont.Weight = QFont.Weight.Medium,
    spacing: float = 1.4,
    family: str = U.FONT,
    italic: bool = False,
) -> QFont:
    font = QFont(family, size)
    font.setWeight(weight)
    # Sade tipografi: eski HUD izleme aralıklarını yumuşak ve okunur tut.
    spacing = max(0.0, min(1.4, float(spacing) * 0.45))
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    font.setItalic(italic)
    return font


def hud_label(
    text: str = "",
    size: int = 10,
    color: str = U.MUTED,
    weight: QFont.Weight = QFont.Weight.Medium,
    spacing: float = 1.4,
    upper: bool = False,
    family: str = U.FONT,
) -> QLabel:
    label = QLabel(text.upper() if upper else text)
    label.setFont(hud_font(size, weight, spacing, family))
    label.setStyleSheet(f"color:{color};background:transparent;border:none;")
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def set_label_color(label: QLabel, color: str) -> None:
    label.setStyleSheet(f"color:{color};background:transparent;border:none;")


class Divider(QFrame):
    def __init__(self, color: str = U.BORDER, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f"background:{color};border:none;")


class ScanLine(QWidget):
    """Başlık altında aralıklı süpürme: her turda yön ve hız değişir, arada dinlenir."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(3)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._progress = 0.0
        self._direction = 1
        self._speed = 0.012
        self._active = False
        self._wait = random.uniform(1.0, 2.6)
        self._ghost_x = -1.0
        self._ghost_life = 0.0
        self._animating = True
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def set_animating(self, enabled: bool) -> None:
        self._animating = bool(enabled)
        if self._animating and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self._animating:
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _head_position(self, progress: float) -> float:
        if self._direction >= 0:
            return progress * self.width()
        return (1.0 - progress) * self.width()

    def _advance(self) -> None:
        if self.window() is not None and self.window().isMinimized():
            return
        step = 0.04
        if not self._active:
            self._wait -= step
            if self._ghost_life > 0.0:
                self._ghost_life = max(0.0, self._ghost_life - step * 0.22)
            if self._wait <= 0.0:
                self._active = True
                self._progress = 0.0
                self._direction = 1 if random.random() < 0.5 else -1
                self._speed = random.uniform(0.010, 0.022)
            self.update()
            return
        self._progress += self._speed
        if self._progress >= 1.0:
            self._progress = 1.0
            self._active = False
            self._ghost_x = self._head_position(1.0)
            self._ghost_life = 1.0
            self._wait = random.uniform(2.6, 6.0)
        self.update()

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() / 2
        painter.setPen(QPen(QColor(U.BORDER), 1))
        painter.drawLine(0, int(mid), self.width(), int(mid))

        if self._ghost_life > 0.0 and self._ghost_x >= 0.0:
            ghost = QColor(U.RED_BRIGHT)
            ghost.setAlpha(int(58 * self._ghost_life))
            painter.setPen(QPen(ghost, 1.4))
            painter.drawPoint(QPointF(min(max(self._ghost_x, 1.0), self.width() - 1.0), mid))

        if not self._active:
            painter.end()
            return

        strength = min(
            1.0,
            self._progress * 12.0,
            (1.0 - self._progress) * 12.0,
        )
        if strength <= 0.01:
            painter.end()
            return

        head = self._head_position(self._progress)
        span = max(70.0, self.width() * 0.14)
        gradient = QLinearGradient(head - span, 0, head + span, 0)
        start = QColor(U.RED_BRIGHT)
        start.setAlpha(0)
        glow = QColor(U.RED_BRIGHT)
        glow.setAlpha(int(214 * strength))
        fade = QColor(U.RED_BRIGHT)
        fade.setAlpha(0)
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(0.5, glow)
        gradient.setColorAt(1.0, fade)
        painter.setPen(QPen(QBrush(gradient), 1.6))
        painter.drawLine(QPointF(head - span, mid), QPointF(head + span, mid))

        dot = QColor(U.RED_BRIGHT)
        dot.setAlpha(int(232 * strength))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QPointF(head, mid), 1.6, 1.6)
        painter.end()


class HudButton(QPushButton):
    """İnce çerçeveli, kırmızı vurgulu HUD düğmesi."""

    def __init__(self, text: str, accent: bool = False, small: bool = False, parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(hud_font(9 if small else 10, QFont.Weight.DemiBold, 1.6))
        height = 26 if small else 32
        self.setMinimumHeight(height)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background:{rgba_css(U.RED, 0.14) if accent else 'transparent'};
                color:{U.RED_BRIGHT if accent else U.TEXT_SOFT};
                border:1px solid {U.RED_DIM if accent else U.BORDER};
                border-radius:5px;
                padding:{'5px 10px' if small else '7px 16px'};
            }}
            QPushButton:hover {{
                border-color:{U.RED};
                color:{U.RED_BRIGHT};
                background:{rgba_css(U.RED, 0.10)};
            }}
            QPushButton:checked {{
                color:{U.RED_BRIGHT};
                border-color:{U.RED};
                background:{rgba_css(U.RED, 0.24)};
            }}
            QPushButton:pressed {{
                background:{rgba_css(U.RED, 0.22)};
            }}
            QPushButton:disabled {{
                color:{U.FAINT};
                border-color:{U.BORDER};
                background:transparent;
            }}
            """
        )


class ClickableFrame(QFrame):
    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background:transparent;border:none;")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Panel(QFrame):
    """Yuvarlak köşeli koyu panel."""

    def __init__(self, parent: Optional[QWidget] = None, padding: int = 16) -> None:
        super().__init__(parent)
        self.setObjectName("hudPanel")
        self.setStyleSheet(
            f"""
            QFrame#hudPanel {{
                background:{U.PANEL};
                border:1px solid {U.BORDER};
                border-radius:16px;
            }}
            """
        )
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(10)


class SectionTitle(QWidget):
    def __init__(self, title: str, trailing: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title = hud_label(title, 9, U.TEXT_SOFT, QFont.Weight.Medium, 0.3)
        self.trailing = hud_label(trailing, 8, U.DIM, QFont.Weight.Medium, 0.2)
        for label in (self.title, self.trailing):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.title)
        layout.addStretch()
        layout.addWidget(self.trailing)


class RingGauge(QWidget):
    """Sade dairesel ölçüm: tek ince yay, tik ve süs yok."""

    def __init__(
        self,
        value: float = 0.0,
        caption: str = "",
        unit: str = "%",
        color: str = U.RED,
        diameter: int = 84,
        show_value: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._value = max(0.0, min(100.0, float(value)))
        self.caption = caption
        self.unit = unit
        self.color = QColor(color)
        self.show_value = show_value
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_value(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value)))
        if abs(value - self._value) > 0.15:
            self._value = value
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        margin = 6.0
        rect = QRectF(margin, margin, side - margin * 2, side - margin * 2)
        center = rect.center()

        arc_rect = rect.adjusted(3, 3, -3, -3)
        track = QColor(U.TEXT)
        track.setAlpha(26)
        painter.setPen(round_pen(track, 2.4))
        painter.drawArc(arc_rect, 225 * 16, -270 * 16)

        if self._value > 0.4:
            glow = QColor(self.color)
            glow.setAlpha(40)
            painter.setPen(round_pen(glow, 5.5))
            painter.drawArc(arc_rect, 225 * 16, int(-270 * 16 * self._value / 100.0))
            painter.setPen(round_pen(self.color, 2.4))
            painter.drawArc(arc_rect, 225 * 16, int(-270 * 16 * self._value / 100.0))

        if self.show_value:
            painter.setPen(QColor(U.TEXT))
            font = QFont(U.FONT, 13)
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            value_rect = QRectF(0, center.y() - 15, self.width(), 20)
            painter.drawText(value_rect, Qt.AlignmentFlag.AlignHCenter, f"%{self._value:.0f}" if self.unit == "%" else f"{self._value:.0f}")
            painter.setPen(QColor(U.DIM))
            painter.setFont(QFont(U.FONT, 7))
            painter.drawText(
                QRectF(0, center.y() + 5, self.width(), 14),
                Qt.AlignmentFlag.AlignHCenter,
                self.caption.upper(),
            )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parent()
            while parent is not None:
                if isinstance(parent, ClickableFrame):
                    parent.clicked.emit()
                    break
                parent = parent.parent()
        super().mouseReleaseEvent(event)


class ClockDisc(QWidget):
    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(220, 220)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._time = "00:00"
        self._seconds = 0
        self._day_percent = 0.0

    def set_time(self, hour: int, minute: int, second: int = 0) -> None:
        text = f"{hour:02d}:{minute:02d}"
        if text != self._time:
            self._time = text
            self.update()
        self._seconds = second
        self._day_percent = (hour * 3600 + minute * 60 + second) / 86400.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        scale = min(self.width(), self.height()) / 220.0
        radius = 94.0 * scale

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 8))
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        painter.drawEllipse(center, radius, radius)

        for index in range(72):
            angle = math.radians(index * 5 - 90)
            if index % 6 == 0:
                inner, color = radius - 8, QColor(255, 255, 255, 75)
            else:
                inner, color = radius - 4, QColor(255, 255, 255, 28)
            painter.setPen(QPen(color, 1.0))
            painter.drawLine(
                QPointF(center.x() + math.cos(angle) * inner, center.y() + math.sin(angle) * inner),
                QPointF(center.x() + math.cos(angle) * radius, center.y() + math.sin(angle) * radius),
            )

        day_rect = QRectF(center.x() - radius + 10, center.y() - radius + 10, (radius - 10) * 2, (radius - 10) * 2)
        painter.setPen(round_pen(QColor(201, 164, 104, 55), 1.6))
        painter.drawArc(day_rect, 90 * 16, -int(360 * 16 * self._day_percent))

        glow = QColor(U.RED)
        glow.setAlpha(55)
        painter.setPen(round_pen(glow, 5.0))
        painter.drawArc(day_rect, 90 * 16, -int(360 * 16 * self._day_percent))
        painter.setPen(round_pen(QColor(U.RED), 1.4))
        painter.drawArc(day_rect, 90 * 16, -int(360 * 16 * self._day_percent))

        painter.setPen(QColor(U.TEXT))
        font = QFont(U.FONT, max(15, round(26 * scale)))
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5 * scale)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, center.y() - 28 * scale, self.width(), 44 * scale),
            Qt.AlignmentFlag.AlignHCenter,
            self._time,
        )
        painter.setFont(hud_font(max(5, round(6 * scale)), QFont.Weight.DemiBold, 2.4 * scale))
        painter.setPen(QColor(U.DIM))
        painter.drawText(
            QRectF(0, center.y() + 14 * scale, self.width(), 14 * scale),
            Qt.AlignmentFlag.AlignHCenter,
            "YEREL SAAT",
        )
        painter.setFont(hud_font(max(5, round(6 * scale)), QFont.Weight.Medium, 1.4 * scale))
        painter.setPen(QColor(U.FAINT))
        painter.drawText(
            QRectF(0, center.y() + 30 * scale, self.width(), 14 * scale),
            Qt.AlignmentFlag.AlignHCenter,
            f"GÜNÜN %{self._day_percent * 100:.0f}'İ",
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StatusEmblem(QWidget):
    """Durum amblemi: nefes, radar ve ping modlarını sırayla gösterir; döngü tekdüze değil."""

    MODE_SECONDS = 4.6

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(84, 84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status = "HAZIR"
        self._elapsed = 0.0
        self._last_mode = -1
        self._blip_angle = 40.0
        self._timer = None
        self._animating = True

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.update()

    def start(self) -> None:
        from PySide6.QtCore import QTimer

        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
        if self._animating:
            self._timer.start(60)

    def set_animating(self, enabled: bool) -> None:
        self._animating = bool(enabled)
        if self._timer is None:
            return
        if self._animating:
            self._timer.start(60)
        else:
            self._timer.stop()

    def _tick(self) -> None:
        self._elapsed = (self._elapsed + 0.06) % (self.MODE_SECONDS * 3)
        mode = int(self._elapsed / self.MODE_SECONDS) % 3
        if mode != self._last_mode:
            self._last_mode = mode
            if mode == 1:
                self._blip_angle = random.uniform(14.0, 346.0)
        self.update()

    def _paint_mode(self, painter, center, radius: float, mode: int, local: float, fade: float) -> None:
        if mode == 0:
            breath = 0.5 + 0.5 * math.sin(local * math.pi * 2.4)
            ring_radius = radius - 5.0 + 3.5 * breath
            ring = QColor(U.RED_BRIGHT)
            ring.setAlpha(int((22 + 58 * breath) * fade))
            painter.setPen(round_pen(ring, 1.2 + 0.9 * breath))
            rect = QRectF(
                center.x() - ring_radius,
                center.y() - ring_radius,
                ring_radius * 2,
                ring_radius * 2,
            )
            painter.drawArc(rect, int((40 + local * 180) * 16) % 5760, int(96 * 16))
            second_radius = ring_radius + 4.0
            second = QColor(U.GOLD_DIM)
            second.setAlpha(int(66 * fade))
            painter.setPen(round_pen(second, 1.0))
            painter.drawArc(
                QRectF(
                    center.x() - second_radius,
                    center.y() - second_radius,
                    second_radius * 2,
                    second_radius * 2,
                ),
                int((230 - local * 200) * 16) % 5760,
                int(48 * 16),
            )
            return

        if mode == 1:
            sweep = local * 360.0 * 1.35
            ring_radius = radius - 2.0
            rect = QRectF(
                center.x() - ring_radius,
                center.y() - ring_radius,
                ring_radius * 2,
                ring_radius * 2,
            )
            for index in range(12):
                alpha = int((96 - index * 7.5) * fade)
                if alpha <= 0:
                    continue
                streak = QColor(U.RED_BRIGHT)
                streak.setAlpha(alpha)
                painter.setPen(QPen(streak, 2.0 if index < 2 else 1.1))
                painter.drawArc(rect, int(((sweep - index * 6.0) % 360) * 16), int(6 * 16))

            needle = QColor(U.RED_BRIGHT)
            needle.setAlpha(int(90 * fade))
            painter.setPen(QPen(needle, 1.0))
            angle = math.radians(sweep)
            painter.drawLine(
                QPointF(center.x() + math.cos(angle) * 9, center.y() + math.sin(angle) * 9),
                QPointF(
                    center.x() + math.cos(angle) * (ring_radius - 1),
                    center.y() + math.sin(angle) * (ring_radius - 1),
                ),
            )

            cycle = int(self._elapsed / self.MODE_SECONDS)
            blip_angle = self._blip_angle
            delta = abs(((sweep - blip_angle) + 180) % 360 - 180)
            if delta < 100:
                blip = QColor(255, 222, 222)
                blip.setAlpha(int((1.0 - delta / 100) * 198 * fade))
                blip_distance = 15 + (cycle * 37) % 11
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(blip)
                painter.drawEllipse(
                    QPointF(
                        center.x() + math.cos(math.radians(blip_angle)) * blip_distance,
                        center.y() + math.sin(math.radians(blip_angle)) * blip_distance,
                    ),
                    2.0,
                    2.0,
                )
                painter.setBrush(Qt.BrushStyle.NoBrush)
            return

        for offset in (0.0, 0.34, 0.68):
            frac = (local + offset) % 1.0
            alpha = int((1.0 - frac) * 100 * fade)
            if alpha <= 0:
                continue
            ring = QColor(U.RED_BRIGHT)
            ring.setAlpha(alpha)
            painter.setPen(QPen(ring, 1.3 - 0.5 * frac))
            ring_radius = 7.0 + frac * (radius - 6.0)
            painter.drawEllipse(
                QRectF(
                    center.x() - ring_radius,
                    center.y() - ring_radius,
                    ring_radius * 2,
                    ring_radius * 2,
                )
            )
        pulse = 0.5 + 0.5 * math.sin(local * math.pi * 2)
        core = QColor(U.RED)
        core.setAlpha(int((48 + 68 * pulse) * fade))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(center, 2.6 + 1.6 * pulse, 2.6 + 1.6 * pulse)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = 31.0
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)

        painter.setPen(QPen(QColor(U.GOLD_DIM), 1.0, Qt.PenStyle.DashLine))
        painter.drawEllipse(center, radius + 6, radius + 6)

        for index in range(24):
            angle = math.radians(index * 15)
            inner = radius + 3
            outer = radius + (6 if index % 6 else 9)
            painter.setPen(QPen(QColor(U.FAINT), 1.0))
            painter.drawLine(
                QPointF(center.x() + math.cos(angle) * inner, center.y() + math.sin(angle) * inner),
                QPointF(center.x() + math.cos(angle) * outer, center.y() + math.sin(angle) * outer),
            )

        mode = int(self._elapsed / self.MODE_SECONDS) % 3
        local = (self._elapsed % self.MODE_SECONDS) / self.MODE_SECONDS
        fade = max(0.0, min(1.0, local / 0.14, (1.0 - local) / 0.14))
        if fade > 0.01:
            painter.save()
            self._paint_mode(painter, center, radius, mode, local, fade)
            painter.restore()

        painter.setPen(QPen(QColor(U.TEXT_SOFT), 1.2))
        for offset in (-7, 0, 7):
            painter.drawLine(
                QPointF(center.x() - 8, center.y() + offset),
                QPointF(center.x() + 8, center.y() + offset),
            )

        painter.setFont(hud_font(7, QFont.Weight.DemiBold, 1.6))
        painter.setPen(QColor(U.MUTED))
        painter.drawText(
            QRectF(0, self.height() - 16, self.width(), 14),
            Qt.AlignmentFlag.AlignHCenter,
            self.status,
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SparkBars(QWidget):
    def __init__(self, color: str = U.GOLD, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(12)
        self.color = QColor(color)
        self._values: List[float] = [0.18] * 28

    def push(self, value: float) -> None:
        self._values.append(max(0.06, min(1.0, float(value) / 100.0)))
        self._values = self._values[-28:]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        count = len(self._values)
        gap = 2.0
        bar_width = max(1.0, (width - gap * (count - 1)) / count)
        for index, value in enumerate(self._values):
            height = max(1.5, self.height() * value)
            color = QColor(self.color)
            color.setAlpha(50 + int(160 * index / count))
            painter.fillRect(
                QRectF(index * (bar_width + gap), self.height() - height, bar_width, height),
                color,
            )


class WeatherIcon(QWidget):
    def __init__(self, kind: str = "cloud", color: str = U.GOLD, size: int = 36, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.color = QColor(color)
        self.setFixedSize(size, size)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        scale = side / 36.0
        painter.scale(scale, scale)
        pen = round_pen(self.color, 1.4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.kind in ("clear", "partly"):
            painter.drawEllipse(QPointF(18, 15), 6.2, 6.2)
            for index in range(8):
                angle = math.radians(index * 45)
                painter.drawLine(
                    QPointF(18 + math.cos(angle) * 9.5, 15 + math.sin(angle) * 9.5),
                    QPointF(18 + math.cos(angle) * 12.5, 15 + math.sin(angle) * 12.5),
                )
            if self.kind == "clear":
                return

        if self.kind in ("cloud", "partly", "fog", "rain", "snow", "storm"):
            path = QPainterPath()
            path.moveTo(8, 24)
            path.cubicTo(4, 24, 3, 19, 7, 17)
            path.cubicTo(8, 11, 17, 10, 19, 15)
            path.cubicTo(25, 12, 30, 16, 28, 21)
            path.cubicTo(31, 22, 31, 26, 27, 26)
            path.lineTo(9, 26)
            path.cubicTo(7, 26, 6, 25, 8, 24)
            painter.drawPath(path)

        if self.kind in ("rain", "storm"):
            painter.setPen(QPen(QColor(U.RED_SOFT if self.kind == "storm" else self.color), 1.3))
            for x in (11, 17, 23):
                painter.drawLine(QPointF(x, 29), QPointF(x - 2, 33))
        if self.kind == "storm":
            painter.setPen(QPen(QColor(U.RED_BRIGHT), 1.6))
            path = QPainterPath()
            path.moveTo(19, 24)
            path.lineTo(15, 30)
            path.lineTo(19, 30)
            path.lineTo(16, 35)
            painter.drawPath(path)
        if self.kind == "snow":
            painter.setPen(QPen(self.color, 1.1))
            for x in (11, 18, 25):
                painter.drawLine(QPointF(x - 2, 31), QPointF(x + 2, 31))
                painter.drawLine(QPointF(x, 29), QPointF(x, 33))
        if self.kind == "fog":
            painter.setPen(QPen(QColor(U.MUTED), 1.1))
            for y in (29, 32, 35):
                painter.drawLine(QPointF(8 + (y - 29) * 2, y), QPointF(28, y))


class TempRangeBar(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(14)
        self.low = 13.0
        self.high = 18.0
        self.current = 16.0

    def set_range(self, low: float, high: float, current: float) -> None:
        self.low, self.high, self.current = float(low), float(high), float(current)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        painter.setPen(QPen(QColor(U.FAINT), 1.0))
        painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        span = max(1.0, self.high - self.low)
        pos = max(0.0, min(1.0, (self.current - self.low) / span))
        x = 4 + pos * (self.width() - 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(U.GOLD))
        painter.drawEllipse(QPointF(x, y), 3.4, 3.4)
        glow = QColor(U.GOLD)
        glow.setAlpha(45)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(x, y), 7.0, 7.0)


class TempCurve(QWidget):
    """Saatlik sıcaklık eğrisi; yağış olasılığı alt çubuklarla."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.hours: List[dict] = []

    def set_hours(self, hours: Sequence[dict]) -> None:
        self.hours = list(hours or [])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.hours:
            painter.setPen(QColor(U.DIM))
            painter.setFont(hud_font(9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Saatlik veri bekleniyor…")
            return

        left, right = 26.0, 26.0
        top, bottom = 30.0, 54.0
        width = max(10.0, self.width() - left - right)
        height = max(10.0, self.height() - top - bottom)
        temperatures = [float(hour.get("temp", 0)) for hour in self.hours]
        low = min(temperatures) - 2
        high = max(temperatures) + 2
        span = max(1.0, high - low)

        painter.setPen(QPen(QColor(255, 255, 255, 12), 1, Qt.PenStyle.DashLine))
        for fraction in (0.0, 0.5, 1.0):
            y = top + height * fraction
            painter.drawLine(QPointF(left, y), QPointF(left + width, y))

        points = []
        step = width / max(1, len(self.hours) - 1)
        for index, temperature in enumerate(temperatures):
            x = left + index * step
            y = top + height * (1 - (temperature - low) / span)
            points.append(QPointF(x, y))

        fill = QPainterPath()
        fill.moveTo(points[0].x(), top + height)
        for point in points:
            fill.lineTo(point)
        fill.lineTo(points[-1].x(), top + height)
        fill.closeSubpath()
        gradient = QLinearGradient(0, top, 0, top + height)
        gradient.setColorAt(0.0, QColor(232, 57, 79, 55))
        gradient.setColorAt(1.0, QColor(232, 57, 79, 0))
        painter.fillPath(fill, QBrush(gradient))

        line = QPainterPath()
        line.moveTo(points[0])
        for index in range(1, len(points)):
            previous = points[index - 1]
            current = points[index]
            mid = (previous.x() + current.x()) / 2
            line.cubicTo(QPointF(mid, previous.y()), QPointF(mid, current.y()), current)

        glow_pen = round_pen(QColor(232, 57, 79, 55), 5.0)
        painter.setPen(glow_pen)
        painter.drawPath(line)
        painter.setPen(round_pen(QColor(U.RED), 1.6))
        painter.drawPath(line)

        for index, point in enumerate(points):
            hour = self.hours[index]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(U.BACKGROUND))
            painter.drawEllipse(point, 3.6, 3.6)
            painter.setBrush(QColor(U.TEXT))
            painter.drawEllipse(point, 2.1, 2.1)

            painter.setFont(hud_font(7, QFont.Weight.DemiBold, 0.8))
            painter.setPen(QColor(U.TEXT_SOFT))
            painter.drawText(
                QRectF(point.x() - 28, point.y() - 22, 56, 14),
                Qt.AlignmentFlag.AlignHCenter,
                f"{int(hour.get('temp', 0))}°",
            )
            precip = int(hour.get("precip", 0) or 0)
            if precip:
                bar_height = max(2.0, (precip / 100.0) * 16.0)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(U.GOLD_DIM))
                painter.drawRect(QRectF(point.x() - 5, top + height + 25 - bar_height, 10, bar_height))
                painter.setPen(QColor(U.GOLD))
                painter.setFont(hud_font(6))
                painter.drawText(
                    QRectF(point.x() - 24, top + height + 6, 48, 12),
                    Qt.AlignmentFlag.AlignHCenter,
                    f"%{precip}",
                )
            painter.setPen(QColor(U.DIM))
            painter.setFont(hud_font(6, QFont.Weight.Medium, 0.6))
            painter.drawText(
                QRectF(point.x() - 30, self.height() - 20, 60, 14),
                Qt.AlignmentFlag.AlignHCenter,
                str(hour.get("label", "")),
            )


class MiniMap(QWidget):
    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._phase = 0.0
        self._home = {"name": "Derince", "region": "Kocaeli", "lat": 40.757, "lon": 29.815}
        self._animating = True
        from PySide6.QtCore import QTimer

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(220)

    def set_animating(self, enabled: bool) -> None:
        self._animating = bool(enabled)
        if self._animating and self.isVisible():
            self._timer.start(220)
        else:
            self._timer.stop()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._animating:
            self._timer.start(220)

    def set_home(self, home: dict) -> None:
        try:
            self._home = {
                "name": str(home.get("name") or "Evim"),
                "region": str(home.get("region") or "Kocaeli"),
                "lat": float(home["lat"]),
                "lon": float(home["lon"]),
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            return
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.04) % 1.0
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        if U.IS_LIGHT:
            gradient.setColorAt(0, QColor("#f7f7f9"))
            gradient.setColorAt(1, QColor("#ececef"))
            water_fill = QColor("#e2e2e8")
        else:
            gradient.setColorAt(0, QColor("#0d0507"))
            gradient.setColorAt(1, QColor("#12070a"))
            water_fill = QColor("#050507")
        painter.fillRect(rect, gradient)

        contour = QColor(U.RED)
        contour.setAlpha(30)
        grid = QColor(U.RED)
        grid.setAlpha(22)
        water_edge = QColor(U.RED)
        water_edge.setAlpha(55)
        gold = QColor(U.GOLD)
        gold.setAlpha(55)
        frame = QColor(U.BORDER) if U.IS_LIGHT else QColor(255, 255, 255, 22)

        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QPen(contour, 1.0))
        for index in range(7):
            path = QPainterPath()
            y_base = rect.height() * (0.18 + index * 0.11)
            path.moveTo(0, y_base)
            for step in range(0, 11):
                x = rect.width() * step / 10
                y = y_base + math.sin(step * 1.2 + index) * 8
                path.lineTo(x, y)
            painter.drawPath(path)

        painter.setPen(QPen(grid, 1.0, Qt.PenStyle.DashLine))
        for index in range(5):
            x = rect.width() * (0.15 + index * 0.18)
            painter.drawLine(QPointF(x, 0), QPointF(x + 30, rect.height()))

        water = QPainterPath()
        water.moveTo(rect.width() * 0.66, 0)
        water.cubicTo(rect.width() * 0.58, rect.height() * 0.35, rect.width() * 0.83, rect.height() * 0.6, rect.width() * 0.75, rect.height())
        water.lineTo(rect.width(), rect.height())
        water.lineTo(rect.width(), 0)
        water.closeSubpath()
        painter.fillPath(water, water_fill)
        painter.setPen(QPen(water_edge, 1.2))
        painter.drawPath(water)

        painter.setPen(QPen(gold, 1.0))
        painter.drawLine(QPointF(0, rect.height() * 0.62), QPointF(rect.width(), rect.height() * 0.5))
        painter.drawLine(QPointF(rect.width() * 0.2, 0), QPointF(rect.width() * 0.35, rect.height()))

        point = QPointF(rect.width() * 0.68, rect.height() * 0.44)
        pulse = 0.5 + 0.5 * math.sin(self._phase * math.pi * 2)
        pulse_color = QColor(U.RED_BRIGHT)
        pulse_color.setAlpha(int(70 + 80 * pulse))
        painter.setPen(QPen(pulse_color, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, 7 + pulse * 6, 7 + pulse * 6)
        painter.setPen(QPen(QColor(U.RED_BRIGHT), 1.4))
        painter.drawEllipse(point, 3.2, 3.2)
        painter.setPen(QColor(U.TEXT))
        painter.setFont(hud_font(7, QFont.Weight.DemiBold, 1.2))
        home_name = str(self._home.get("name") or "").upper()[:18]
        painter.drawText(
            QRectF(point.x() + 8, point.y() - 10, rect.width() - point.x() - 12, 14),
            Qt.AlignmentFlag.AlignLeft,
            home_name,
        )
        painter.setPen(QColor(U.MUTED))
        painter.setFont(hud_font(6, QFont.Weight.Medium, 1.0))
        painter.drawText(
            QRectF(8, rect.height() - 18, 140, 12),
            Qt.AlignmentFlag.AlignLeft,
            str(self._home.get("region") or "").upper()[:16],
        )
        painter.drawText(
            QRectF(rect.width() - 150, rect.height() - 18, 142, 12),
            Qt.AlignmentFlag.AlignRight,
            f"{float(self._home['lat']):.3f}° · {float(self._home['lon']):.3f}°",
        )

        painter.setPen(QPen(frame, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.restore()


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "—", note: str = "", meter: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setStyleSheet(
            f"""
            QFrame#metricCard {{
                background:{U.PANEL_2};
                border:1px solid {U.BORDER};
                border-radius:8px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        self.title = hud_label(title, 7, U.DIM, QFont.Weight.DemiBold, 1.8, upper=True)
        self.value = hud_label(value, 15, U.TEXT, QFont.Weight.DemiBold, 0.8)
        self.note = hud_label(note, 7, U.MUTED, QFont.Weight.Medium, 0.8)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        if meter:
            self.meter = QFrame()
            self.meter.setFixedHeight(2)
            self.meter.setStyleSheet(f"background:{U.RED_DIM};border:none;")
            layout.addWidget(self.meter)
        else:
            self.meter = None
        layout.addWidget(self.note)
        layout.addStretch()

    def set(self, value: str, note: str = "", percent: Optional[float] = None) -> None:
        self.value.setText(value)
        self.note.setText(note)
        if self.meter is not None and percent is not None:
            width = max(2, int(self.width() * max(0.0, min(100.0, percent)) / 100.0))
            self.meter.setFixedWidth(width)


class MeterBar(QProgressBar):
    def __init__(self, value: int = 0, height: int = 3, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(int(max(0, min(100, value))))
        self.setTextVisible(False)
        self.setFixedHeight(height)
        self.setStyleSheet(
            f"""
            QProgressBar {{
                background:{U.FAINT};
                border:none;
                border-radius:{height // 2}px;
            }}
            QProgressBar::chunk {{
                background:{U.RED};
                border-radius:{height // 2}px;
            }}
            """
        )

    def set_value(self, value: float) -> None:
        self.setValue(int(max(0, min(100, float(value)))))


class EmptyState(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setFont(hud_font(9, QFont.Weight.Medium, 0.6))
        self.setStyleSheet(f"color:{U.DIM};background:transparent;border:none;")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)
