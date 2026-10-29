"""Ortadaki cam küre; arayüzün tek fütüristik öğesi."""

from __future__ import annotations

import math
import random
from typing import List, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.theme import ultron as U

STATE_ACCENTS = {
    "idle": ("#a06a78", "#c98d9b"),
    "listening": ("#2fd6c3", "#8ff7ec"),
    "thinking": ("#ffc32e", "#ffe08a"),
    "speaking": ("#ff6b5b", "#ffb0a6"),
}

LIGHT_STATE_ACCENTS = {
    "idle": ("#8f5a67", "#b87f8c"),
    "listening": ("#0e9d8f", "#3fc4b6"),
    "thinking": ("#c08a00", "#d9a520"),
    "speaking": ("#c94a3c", "#dd7568"),
}


def state_color(state: str, secondary: bool = False) -> str:
    palette = LIGHT_STATE_ACCENTS if U.IS_LIGHT else STATE_ACCENTS
    primary, second = palette.get(state, palette["idle"])
    return second if secondary else primary


def _alpha(color: QColor, alpha: int) -> QColor:
    copy = QColor(color)
    copy.setAlpha(max(0, min(255, int(alpha))))
    return copy


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


class OrbWidget(QWidget):
    """Yumuşak ışıklı cam küre: sade, sakin ve duruma göre nefes alan."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.state = "idle"
        self.status = "Hazır"
        self._phase = 0.0
        self._spin = 0.0
        self._burst = 0.0
        self._burst_color = QColor(state_color("idle"))
        self._input_level = 0.0
        self._animating = True
        generator = random.Random(29)
        self._sparkles: List[Tuple[float, float, float, float, float]] = [
            (
                generator.uniform(0, math.tau),
                generator.uniform(0.12, 0.88),
                0.6 + generator.uniform(0.0, 1.1),
                generator.uniform(0.25, 1.0),
                generator.uniform(0, math.tau),
            )
            for _ in range(14)
        ]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._interval())

    def _interval(self) -> int:
        # Boştaki küre 15 FPS yeterli; görev sırasında 30 FPS'e çıkar.
        return 33 if self.state in ("thinking", "listening", "speaking") else 66

    def set_animating(self, enabled: bool) -> None:
        self._animating = bool(enabled)
        if self._animating and self.isVisible():
            self._timer.start(self._interval())
        else:
            self._timer.stop()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._animating:
            self._timer.start(self._interval())

    def set_state(self, state: str) -> None:
        if state not in STATE_ACCENTS:
            return
        if state != self.state:
            self._burst = 1.0
            self._burst_color = QColor(state_color(state))
            if state != "listening":
                self._input_level = 0.0
            self.update()
        self.state = state
        if self._animating and self.isVisible():
            self._timer.start(self._interval())
        self.update()

    def set_input_level(self, level: float) -> None:
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        rate = 0.62 if value > self._input_level else 0.24
        self._input_level += (value - self._input_level) * rate
        self.update()

    def _accents(self) -> Tuple[QColor, QColor]:
        return QColor(state_color(self.state)), QColor(state_color(self.state, secondary=True))

    def _tick(self) -> None:
        speed = 0.007 if self.state == "idle" else 0.02
        if self.state == "thinking":
            speed = 0.028
        self._phase = (self._phase + speed) % math.tau
        spin_speed = 0.05 if self.state in ("thinking", "listening") else 0.028
        self._spin = (self._spin + spin_speed) % math.tau
        if self._burst > 0.0:
            self._burst = max(0.0, self._burst - (self._interval() / 640.0))
        self.update()

    def paintEvent(self, event) -> None:
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QPointF(width / 2, height / 2)
        base = min(width, height)
        breath = 0.5 + 0.5 * math.sin(self._phase * 1.5)
        level = self._input_level if self.state == "listening" else 0.0
        radius = base * 0.30
        if self.state in ("listening", "speaking"):
            radius *= 1.0 + 0.02 * breath
        else:
            radius *= 1.0 + 0.008 * breath
        radius *= 1.0 + 0.15 * level
        primary, secondary = self._accents()
        self._paint_glow(painter, center, radius, primary, breath)
        self._paint_orbit(painter, center, radius, primary, secondary)
        self._paint_level_ring(painter, center, radius, secondary)
        sphere = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        self._paint_sphere(painter, sphere, primary, secondary, breath)
        self._paint_burst(painter, center, radius)
        painter.end()

    def _paint_glow(
        self, painter: QPainter, center: QPointF, radius: float, accent: QColor, breath: float
    ) -> None:
        strength = {"idle": 1.0, "listening": 1.75, "thinking": 1.45, "speaking": 1.6}.get(
            self.state, 1.0
        )
        level = self._input_level if self.state == "listening" else 0.0
        strength *= 1.0 + 2.0 * level
        reach = radius * (1.55 + 0.1 * breath)
        halo = QRadialGradient(center, reach)
        base_alpha = 46 if not U.IS_LIGHT else 24
        halo.setColorAt(0.0, _alpha(accent, min(200, base_alpha * strength)))
        halo.setColorAt(0.5, _alpha(accent, min(120, base_alpha * 0.42 * strength)))
        halo.setColorAt(1.0, _alpha(accent, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, reach, reach)

    def _paint_orbit(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        secondary: QColor,
    ) -> None:
        orbit_radius = radius * 1.30
        tilt = math.radians(-18.0)
        squash = 0.34
        ring = QRectF(-orbit_radius, -orbit_radius, orbit_radius * 2, orbit_radius * 2)

        painter.save()
        painter.translate(center)
        painter.rotate(-18.0)
        painter.scale(1.0, squash)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        faint = _alpha(QColor(U.TEXT), 18 if not U.IS_LIGHT else 34)
        painter.setPen(QPen(faint, 1.0))
        painter.drawEllipse(ring)

        start = int((math.degrees(self._spin) % 360.0) * 16)
        level = self._input_level if self.state == "listening" else 0.0
        span = int((64 + 60 * level) * 16)
        soft = _alpha(accent, (58 + int(110 * level)) if not U.IS_LIGHT else (34 + 60 * level))
        painter.setPen(QPen(soft, 4.4 + 2.2 * level))
        painter.drawArc(ring, start, span)
        bright = _alpha(secondary, (190 + int(55 * level)) if not U.IS_LIGHT else (140 + 50 * level))
        painter.setPen(QPen(bright, 1.8 + 0.8 * level))
        painter.drawArc(ring, start, span)
        painter.restore()

        x = math.cos(self._spin) * orbit_radius
        y = math.sin(self._spin) * orbit_radius * squash
        point = QPointF(
            center.x() + x * math.cos(tilt) - y * math.sin(tilt),
            center.y() + x * math.sin(tilt) + y * math.cos(tilt),
        )
        dot_glow = QRadialGradient(point, max(7.0, radius * 0.09))
        dot_glow.setColorAt(0.0, _alpha(secondary, 120 if not U.IS_LIGHT else 80))
        dot_glow.setColorAt(1.0, _alpha(secondary, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot_glow)
        dot_size = max(7.0, radius * 0.09)
        painter.drawEllipse(point, dot_size, dot_size)
        painter.setBrush(_alpha(secondary, 235 if not U.IS_LIGHT else 200))
        dot_radius = max(1.6, radius * 0.018)
        painter.drawEllipse(point, dot_radius, dot_radius)

    def _paint_level_ring(
        self, painter: QPainter, center: QPointF, radius: float, accent: QColor
    ) -> None:
        level = self._input_level if self.state == "listening" else 0.0
        if level <= 0.015:
            return
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        ring_radius = radius * (1.12 + 0.14 * level)
        glow = _alpha(accent, int(70 + 90 * level))
        painter.setPen(QPen(glow, 2.0 + 2.4 * level))
        start = int((math.degrees(-self._spin * 1.6) % 360.0) * 16)
        painter.drawArc(
            QRectF(-ring_radius, -ring_radius, ring_radius * 2, ring_radius * 2),
            start,
            int(96 * 16),
        )
        painter.setPen(QPen(_alpha(accent, int(30 + 40 * level)), 1.0))
        painter.drawEllipse(center, ring_radius, ring_radius)
        painter.restore()

    def _paint_sphere(
        self,
        painter: QPainter,
        sphere: QRectF,
        primary: QColor,
        secondary: QColor,
        breath: float,
    ) -> None:
        path = QPainterPath()
        path.addEllipse(sphere)

        painter.save()
        painter.setClipPath(path)
        light = QPointF(
            sphere.center().x() - sphere.width() * 0.18,
            sphere.center().y() - sphere.height() * 0.22,
        )
        body = QRadialGradient(light, sphere.width() * 0.95)
        if U.IS_LIGHT:
            body.setColorAt(0.0, QColor(255, 255, 255, 255))
            body.setColorAt(0.55, QColor(U.PANEL_2))
            body.setColorAt(1.0, _mix(QColor(U.BACKGROUND_2), primary, 0.14))
        else:
            body.setColorAt(0.0, _mix(QColor(U.PANEL_2), primary, 0.34))
            body.setColorAt(0.46, QColor(U.PANEL_2))
            body.setColorAt(1.0, _mix(QColor(U.BACKGROUND), primary, 0.10))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(body)
        painter.drawEllipse(sphere)

        inner = QRadialGradient(
            QPointF(sphere.center().x(), sphere.center().y() + sphere.height() * 0.16),
            sphere.width() * 0.62,
        )
        level = self._input_level if self.state == "listening" else 0.0
        inner_alpha = (62 + 16 * breath) if self.state != "idle" else (26 + 8 * breath)
        inner_alpha = int(inner_alpha * (1.0 + 0.9 * level))
        inner.setColorAt(0.0, _alpha(secondary, min(200, inner_alpha)))
        inner.setColorAt(0.62, _alpha(secondary, inner_alpha * 0.32))
        inner.setColorAt(1.0, _alpha(secondary, 0))
        painter.setBrush(inner)
        painter.drawEllipse(sphere)

        highlight_center = QPointF(
            sphere.center().x() - sphere.width() * 0.26,
            sphere.center().y() - sphere.height() * 0.30,
        )
        highlight_radius = sphere.width() * 0.34
        highlight = QRadialGradient(highlight_center, highlight_radius)
        if U.IS_LIGHT:
            highlight.setColorAt(0.0, QColor(255, 255, 255, 235))
            highlight.setColorAt(0.5, QColor(255, 255, 255, 90))
        else:
            highlight.setColorAt(0.0, QColor(255, 255, 255, 112))
            highlight.setColorAt(0.5, QColor(255, 255, 255, 40))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(highlight)
        painter.drawEllipse(highlight_center, highlight_radius, highlight_radius)

        painter.setPen(Qt.PenStyle.NoPen)
        for angle, distance, size, brightness, offset in self._sparkles:
            sparkle_angle = angle + self._spin * (0.4 + distance * 0.5)
            reach = sphere.width() * 0.38 * distance
            sparkle = QPointF(
                sphere.center().x() + math.cos(sparkle_angle) * reach,
                sphere.center().y() + math.sin(sparkle_angle) * reach * 0.85,
            )
            twinkle = 0.5 + 0.5 * math.sin(self._phase * 2.0 + offset)
            alpha = (16 + 44 * brightness) * (0.4 + 0.6 * twinkle)
            if U.IS_LIGHT:
                painter.setBrush(_alpha(primary, alpha * 0.65))
            else:
                painter.setBrush(_alpha(_mix(primary, QColor(255, 255, 255), 0.30), alpha))
            painter.drawEllipse(sparkle, size, size)
        painter.restore()

        rim = _alpha(primary, (92 + int(80 * level)) if not U.IS_LIGHT else (58 + 40 * level))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(rim, 1.4 + 0.8 * level))
        painter.drawEllipse(sphere)

        glint = sphere.adjusted(
            sphere.width() * 0.07, sphere.height() * 0.07,
            -sphere.width() * 0.07, -sphere.height() * 0.07,
        )
        painter.setPen(QPen(QColor(255, 255, 255, 66 if not U.IS_LIGHT else 130), 1.5))
        painter.drawArc(glint, int(96 * 16), int(74 * 16))

    def _paint_burst(self, painter: QPainter, center: QPointF, radius: float) -> None:
        if self._burst <= 0.02:
            return
        travel = 1.0 - self._burst
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for factor, weight in ((0.0, 1.0), (0.14, 0.55)):
            phase = max(0.0, min(1.0, travel - factor))
            ring = _alpha(self._burst_color, int(132 * self._burst * (1.0 - phase * 0.7) * weight))
            painter.setPen(QPen(ring, 1.5 + 0.9 * self._burst))
            radius_now = radius * (1.02 + 0.30 * phase)
            painter.drawEllipse(center, radius_now, radius_now)
