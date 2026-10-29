"""Kamera HUD üzerine çizilen 3B el efektleri; tamamen yerel QPainter."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient

from app.theme import ultron as U
from .hud_widgets import hud_font

# "Dünya" efekti kullanıcı isteğiyle şimdilik kapalı.
PLANETS = (
    {"name": "Satürn", "color": (232, 206, 156), "ring": True},
    {"name": "Mars", "color": (222, 96, 64), "ring": False},
    {"name": "Jüpiter", "color": (226, 186, 148), "ring": False},
    {"name": "Ay", "color": (198, 204, 216), "ring": False},
    {"name": "Güneş", "color": (255, 178, 62), "ring": False},
)

_SKELETON = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

_GESTURE_LABELS = {
    "open": "AÇIK EL",
    "fist": "YUMRUK",
    "pinch": "ÇİMDİK",
    "point": "İŞARET",
    "victory": "ZAFFER",
    "thumb_up": "BAŞPARMAK",
}

# Elde kilitlenince HUD'un turuncu vurgusu; temadan bağımsız, video üstünde okunur.
HAND_ACCENT = (255, 174, 92)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _shade(color: tuple[int, int, int], factor: float) -> QColor:
    return QColor(
        int(_clamp(color[0] * factor, 0, 255)),
        int(_clamp(color[1] * factor, 0, 255)),
        int(_clamp(color[2] * factor, 0, 255)),
    )


def _round_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


class HandEffects:
    """El iskeleti, ateş, mühür, gezegen, şimşek ve patlama efektlerini yönetir."""

    PLANET_INTERVAL = 0.07
    PINCH_CONFIRM_S = 0.2
    PLANET_FLOAT_S = 3.5
    PLANET_FADE_S = 1.0

    def __init__(self) -> None:
        self.enabled = False
        self.show_skeleton = True
        self.hands: list[dict] = []
        self.planet: Optional[dict] = None
        self.bursts: list[dict] = []
        self.trails: dict[int, deque] = {}
        self._previous_gestures: dict[int, str] = {}
        self._pinch_latched = False
        self._pinch_since: Optional[float] = None
        self._planet_cycle = 0
        self._last_frame = 0.0
        self._last_hands_at = 0.0
        self._motion: dict[int, dict] = {}
        self._painted_at = 0.0

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.clear()

    def clear(self) -> None:
        self.hands = []
        self.planet = None
        self.bursts = []
        self.trails = {}
        self._previous_gestures = {}
        self._pinch_latched = False
        self._pinch_since = None
        self._last_frame = 0.0
        self._motion = {}
        self._painted_at = 0.0

    def set_skeleton(self, visible: bool) -> None:
        self.show_skeleton = bool(visible)

    def is_active(self, now: Optional[float] = None) -> bool:
        if not self.enabled:
            return False
        if self.hands or self.planet or self.bursts:
            return True
        now = time.monotonic() if now is None else now
        return any(trail and now - trail[-1][2] < 1.4 for trail in self.trails.values())

    @staticmethod
    def _palm(hand: dict) -> tuple[float, float]:
        palm = hand.get("palm")
        if isinstance(palm, (list, tuple)) and len(palm) >= 2:
            return float(palm[0]), float(palm[1])
        landmarks = hand.get("landmarks") or []
        if len(landmarks) >= 10:
            return (
                (float(landmarks[0][0]) + float(landmarks[9][0])) / 2,
                (float(landmarks[0][1]) + float(landmarks[9][1])) / 2,
            )
        return 0.5, 0.5

    def update_hands(self, hands, now: Optional[float] = None) -> None:
        if not self.enabled:
            return
        now = time.monotonic() if now is None else now
        cleaned = []
        for hand in hands or []:
            if isinstance(hand, dict) and len(hand.get("landmarks") or []) >= 21:
                cleaned.append(hand)
        self.hands = cleaned[:2]
        if self.hands:
            self._last_hands_at = now
        self._track_motion(now)

        for key in list(self.trails):
            trail = self.trails[key]
            while trail and now - trail[0][2] > 1.4:
                trail.popleft()
            if not trail:
                del self.trails[key]

        for index, hand in enumerate(self.hands):
            gesture = str(hand.get("gesture") or "none")
            if gesture == "point":
                tip = hand["landmarks"][8]
                trail = self.trails.setdefault(index, deque(maxlen=22))
                if not trail or now - trail[-1][2] > 0.035:
                    trail.append((float(tip[0]), float(tip[1]), now))
            previous = self._previous_gestures.get(index)
            if previous == "fist" and gesture == "open":
                x, y = self._palm(hand)
                planet_color = PLANETS[self.planet["index"]]["color"] if self.planet else (255, 184, 88)
                self.bursts.append({"x": x, "y": y, "t": now, "seed": now * 3.1 + index, "color": planet_color})
            self._previous_gestures[index] = gesture

        for index in list(self._previous_gestures):
            if index >= len(self.hands):
                self._previous_gestures.pop(index, None)

        self._update_planet(now)

    def _track_motion(self, now: float) -> None:
        """İskelet hızını tespitler arasında taşır; çizim 60 Hz'de gecikmesiz görünür."""
        for index in list(self._motion):
            if index >= len(self.hands):
                del self._motion[index]
        for index, hand in enumerate(self.hands):
            points = [tuple(float(value) for value in item[:3]) for item in hand["landmarks"]]
            state = self._motion.get(index)
            if state is None or now - state["at"] > 0.5:
                self._motion[index] = {
                    "points": points,
                    "vel": [(0.0, 0.0, 0.0)] * 21,
                    "shown": points,
                    "at": now,
                }
                continue
            elapsed = max(1e-3, now - state["at"])
            velocity = []
            for point, previous, old_velocity in zip(points, state["points"], state["vel"]):
                vx = _clamp((point[0] - previous[0]) / elapsed, -4.0, 4.0)
                vy = _clamp((point[1] - previous[1]) / elapsed, -4.0, 4.0)
                velocity.append(
                    (
                        old_velocity[0] * 0.45 + vx * 0.55,
                        old_velocity[1] * 0.45 + vy * 0.55,
                        0.0,
                    )
                )
            state["points"] = points
            state["vel"] = velocity
            state["at"] = now

    def _display_hands(self, now: float) -> list[dict]:
        """Son tespiti kısa bir öngörüyle ileri taşır; 20 Hz iskelet 60 Hz'de akıcı kalır."""
        frame_dt = min(0.05, max(0.0, now - self._painted_at)) if self._painted_at else 0.0
        self._painted_at = now
        shown_hands = []
        for index, hand in enumerate(self.hands):
            state = self._motion.get(index)
            if state is None:
                shown_hands.append(hand)
                continue
            horizon = min(0.06, max(0.0, now - state["at"])) * 0.7
            ease = 1.0 - math.exp(-55.0 * frame_dt) if frame_dt else 1.0
            shown = []
            for point, velocity, current in zip(state["points"], state["vel"], state["shown"]):
                target_x = point[0] + velocity[0] * horizon
                target_y = point[1] + velocity[1] * horizon
                shown.append(
                    (
                        current[0] + (target_x - current[0]) * ease,
                        current[1] + (target_y - current[1]) * ease,
                        point[2],
                    )
                )
            state["shown"] = shown
            display = dict(hand)
            display["landmarks"] = shown
            display["palm"] = ((shown[0][0] + shown[9][0]) / 2, (shown[0][1] + shown[9][1]) / 2)
            shown_hands.append(display)
        return shown_hands

    def _update_planet(self, now: float) -> None:
        pinches = [hand for hand in self.hands if hand.get("gesture") == "pinch"]
        if not pinches:
            self._pinch_latched = False
            self._pinch_since = None
            if self.planet is not None and self.planet["held"]:
                self.planet["held"] = False
                self.planet["released_at"] = now
                self.planet["moved_at"] = now
            return

        if self._pinch_since is None:
            self._pinch_since = now
        if now - self._pinch_since < self.PINCH_CONFIRM_S:
            return

        targets = []
        for hand in pinches[:2]:
            landmarks = hand["landmarks"]
            targets.append(
                (
                    (float(landmarks[4][0]) + float(landmarks[8][0])) / 2,
                    (float(landmarks[4][1]) + float(landmarks[8][1])) / 2,
                )
            )
        if len(targets) >= 2:
            target_x = (targets[0][0] + targets[1][0]) / 2
            target_y = (targets[0][1] + targets[1][1]) / 2
            span = math.hypot(targets[0][0] - targets[1][0], targets[0][1] - targets[1][1])
            target_radius = _clamp(span * 0.32, 0.05, 0.17)
            smoothing = 0.28
        else:
            target_x, target_y = targets[0]
            target_radius = self.PLANET_INTERVAL
            smoothing = 0.36

        if not self._pinch_latched:
            self._pinch_latched = True
            index = self._planet_cycle % len(PLANETS)
            self._planet_cycle += 1
            self.planet = {
                "index": index,
                "name": PLANETS[index]["name"],
                "x": target_x,
                "y": target_y,
                "vx": 0.0,
                "vy": 0.0,
                "radius": target_radius,
                "held": True,
                "moved_at": now,
                "released_at": None,
                "born": now,
            }
        if self.planet is None:
            return

        planet = self.planet
        previous_x, previous_y = planet["x"], planet["y"]
        planet["x"] += (target_x - previous_x) * smoothing
        planet["y"] += (target_y - previous_y) * smoothing
        planet["radius"] += (target_radius - planet["radius"]) * 0.3
        elapsed = max(1e-4, now - planet.get("moved_at", now))
        planet["vx"] = _clamp((planet["x"] - previous_x) / elapsed, -1.8, 1.8)
        planet["vy"] = _clamp((planet["y"] - previous_y) / elapsed, -1.8, 1.8)
        planet["held"] = True
        planet["moved_at"] = now
        planet["released_at"] = None

    def _advance(self, now: float) -> None:
        elapsed = min(0.05, max(0.0, now - self._last_frame)) if self._last_frame else 0.0
        self._last_frame = now
        planet = self.planet
        if planet is None:
            return
        if planet["held"]:
            return
        planet["x"] += planet["vx"] * elapsed
        planet["y"] += planet["vy"] * elapsed
        planet["vx"] *= 0.986
        planet["vy"] = planet["vy"] * 0.986 + 0.025 * elapsed
        margin = max(0.03, planet["radius"] * 0.65)
        if planet["x"] < margin:
            planet["x"] = margin
            planet["vx"] = abs(planet["vx"]) * 0.55
        elif planet["x"] > 1.0 - margin:
            planet["x"] = 1.0 - margin
            planet["vx"] = -abs(planet["vx"]) * 0.55
        if planet["y"] < margin:
            planet["y"] = margin
            planet["vy"] = abs(planet["vy"]) * 0.55
        elif planet["y"] > 1.0 - margin:
            planet["y"] = 1.0 - margin
            planet["vy"] = -abs(planet["vy"]) * 0.55
        released_at = planet.get("released_at")
        if released_at is not None and now - released_at > self.PLANET_FLOAT_S + self.PLANET_FADE_S:
            self.planet = None

    def paint(self, painter: QPainter, target: QRectF, now: Optional[float] = None) -> None:
        if not self.enabled or target.width() <= 0 or target.height() <= 0:
            return
        now = time.monotonic() if now is None else now
        self._advance(now)
        hands = self._display_hands(now)
        self._paint_trails(painter, target, now)
        open_hands = [hand for hand in hands if hand.get("gesture") == "open"]
        if len(open_hands) >= 2:
            for hand in open_hands:
                x, y = self._palm(hand)
                center = self._to_point(target, x, y)
                radius = target.height() * (0.045 + 0.006 * math.sin(now * 1.7))
                self._paint_seal(painter, center, radius, now)
        for hand in hands:
            self._paint_hand_frame(painter, target, hand)
            self._paint_tips(painter, target, hand, now)
        if self.show_skeleton:
            for hand in hands:
                self._paint_skeleton(painter, target, hand)
        for hand in open_hands:
            self._paint_fire(painter, target, hand, now)
        if self.planet is not None:
            self._paint_planet(painter, target, now)
        self._paint_lightning(painter, target, now)
        self._paint_bursts(painter, target, now)

    @staticmethod
    def _to_point(target: QRectF, x: float, y: float) -> QPointF:
        return QPointF(target.left() + x * target.width(), target.top() + y * target.height())

    def _paint_trails(self, painter: QPainter, target: QRectF, now: float) -> None:
        for trail in self.trails.values():
            points = list(trail)
            if len(points) < 2:
                continue
            glow = QColor(U.RED_BRIGHT)
            for index in range(1, len(points)):
                x1, y1, t1 = points[index - 1]
                x2, y2, t2 = points[index]
                age = max(0.0, now - max(t1, t2))
                alpha = int(150 * max(0.0, 1.0 - age / 1.3))
                if alpha <= 0:
                    continue
                glow.setAlpha(alpha)
                width = 0.8 + 2.6 * (index / len(points)) * (alpha / 150)
                painter.setPen(_round_pen(glow, width))
                painter.drawLine(self._to_point(target, x1, y1), self._to_point(target, x2, y2))
            tip_x, tip_y, _ = points[-1]
            center = self._to_point(target, tip_x, tip_y)
            self._glow(painter, center, target.height() * 0.022, QColor(U.RED_GLOW), 150)

    @staticmethod
    def _glow(painter: QPainter, center: QPointF, radius: float, color: QColor, alpha: int) -> None:
        if radius <= 0:
            return
        gradient = QRadialGradient(center, radius)
        inner = QColor(color)
        inner.setAlpha(alpha)
        outer = QColor(color)
        outer.setAlpha(0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(0.45, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.45)))
        gradient.setColorAt(1.0, outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

    @staticmethod
    def _hand_box(target: QRectF, hand: dict) -> Optional[QRectF]:
        landmarks = hand.get("landmarks") or []
        if len(landmarks) < 21:
            return None
        xs = [float(item[0]) for item in landmarks]
        ys = [float(item[1]) for item in landmarks]
        pad = target.height() * 0.022
        rect = QRectF(
            target.left() + min(xs) * target.width() - pad,
            target.top() + min(ys) * target.height() - pad,
            (max(xs) - min(xs)) * target.width() + pad * 2,
            (max(ys) - min(ys)) * target.height() + pad * 2,
        ).intersected(target)
        if rect.width() < 6 or rect.height() < 6:
            return None
        return rect

    def _paint_hand_frame(self, painter: QPainter, target: QRectF, hand: dict) -> None:
        """Ele kilitlenince turuncu köşe çerçevesi ve jest etiketi."""
        rect = self._hand_box(target, hand)
        if rect is None:
            return
        accent = QColor(*HAND_ACCENT)
        x, y = self._palm(hand)
        self._glow(painter, self._to_point(target, x, y), target.height() * 0.075, accent, 26)

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        edge = QColor(accent)
        edge.setAlpha(46)
        painter.setPen(QPen(edge, 1.0))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        pen = QPen(accent, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(pen)
        corner = min(18.0, rect.width() * 0.24, rect.height() * 0.24)
        x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
        for sx, sy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            painter.drawLine(QPointF(sx, sy), QPointF(sx + corner * dx, sy))
            painter.drawLine(QPointF(sx, sy), QPointF(sx, sy + corner * dy))

        label = _GESTURE_LABELS.get(str(hand.get("gesture") or ""), "EL")
        text = f"EL · {label}" if label != "EL" else "EL"
        painter.setFont(hud_font(6.8, QFont.Weight.DemiBold, 0.5))
        metrics = painter.fontMetrics()
        width = min(target.width() - 8, metrics.horizontalAdvance(text) + 24)
        chip = QRectF(rect.left(), rect.top() - 22, width, 17)
        if chip.top() < target.top() + 4:
            chip.moveTop(min(rect.bottom() + 4, target.bottom() - 21))
        chip.moveLeft(min(max(target.left() + 4, chip.left()), target.right() - width - 4))
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 168), 1.0))
        painter.setBrush(QColor(14, 10, 7, 196))
        painter.drawRoundedRect(chip, 7, 7)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(chip.left() + 9, chip.center().y()), 2.0, 2.0)
        painter.setPen(QColor(255, 248, 240, 236))
        painter.drawText(
            chip.adjusted(17, 0, -4, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()

    def _paint_tips(self, painter: QPainter, target: QRectF, hand: dict, now: float) -> None:
        """Her jestte parmak uçlarını turuncu ışıtır; el okunduğu anlaşılsın."""
        landmarks = hand.get("landmarks") or []
        if len(landmarks) < 21:
            return
        open_hand = str(hand.get("gesture") or "") == "open"
        for tip in (4, 8, 12, 16, 20):
            x, y = float(landmarks[tip][0]), float(landmarks[tip][1])
            center = self._to_point(target, x, y)
            flicker = 0.85 + 0.15 * math.sin(now * 4.2 + tip)
            radius = target.height() * 0.014 * flicker
            self._glow(painter, center, radius * 2.0, QColor(*HAND_ACCENT), 82 if open_hand else 52)

    def _paint_skeleton(self, painter: QPainter, target: QRectF, hand: dict) -> None:
        landmarks = hand.get("landmarks") or []
        if len(landmarks) < 21:
            return
        points = [self._to_point(target, float(item[0]), float(item[1])) for item in landmarks]
        line = QColor(*HAND_ACCENT)
        line.setAlpha(138)
        painter.setPen(_round_pen(line, 1.25))
        for start, end in _SKELETON:
            painter.drawLine(points[start], points[end])
        joint = QColor(*HAND_ACCENT)
        joint.setAlpha(150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(joint)
        for index in range(21):
            radius = 2.6 if index in (4, 8, 12, 16, 20) else 1.4
            painter.drawEllipse(points[index], radius, radius)

    def _paint_fire(self, painter: QPainter, target: QRectF, hand: dict, now: float) -> None:
        landmarks = hand.get("landmarks") or []
        if len(landmarks) < 21:
            return
        for tip in (4, 8, 12, 16, 20):
            x, y = float(landmarks[tip][0]), float(landmarks[tip][1])
            center = self._to_point(target, x, y)
            for layer in range(2):
                flicker = 0.85 + 0.2 * math.sin(now * (6.0 + layer * 2.2) + tip)
                radius = target.height() * (0.014 + layer * 0.009) * flicker
                gradient = QRadialGradient(center, radius * 2.2)
                gradient.setColorAt(0.0, QColor(255, 244, 196, 190 - layer * 40))
                gradient.setColorAt(0.32, QColor(255, 170, 64, 130 - layer * 30))
                gradient.setColorAt(0.7, QColor(235, 76, 34, 58))
                gradient.setColorAt(1.0, QColor(160, 20, 10, 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(gradient)
                painter.drawEllipse(center, radius, radius * 1.45)

    def _paint_seal(self, painter: QPainter, center: QPointF, radius: float, now: float) -> None:
        painter.save()
        painter.translate(center)
        painter.rotate((now * 36.0) % 360)
        for index, ratio in enumerate((1.0, 0.78, 0.56)):
            color = QColor(U.GOLD)
            color.setAlpha(78 - index * 14)
            pen = QPen(color, 1.0 + index * 0.25, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), radius * ratio, radius * ratio * 0.38)
        tick = QColor(U.RED_BRIGHT)
        tick.setAlpha(126)
        painter.setPen(QPen(tick, 1.3))
        for index in range(12):
            angle = math.radians(index * 30)
            inner = radius * 0.88
            outer = radius * 1.06
            painter.drawLine(
                QPointF(math.cos(angle) * inner, math.sin(angle) * inner * 0.38),
                QPointF(math.cos(angle) * outer, math.sin(angle) * outer * 0.38),
            )
        glow = QColor(U.RED_GLOW)
        glow.setAlpha(36)
        painter.setPen(QPen(glow, 4.0))
        painter.drawEllipse(QPointF(0, 0), radius * 1.04, radius * 0.4)
        painter.restore()

    def _paint_planet(self, painter: QPainter, target: QRectF, now: float) -> None:
        planet = self.planet
        if planet is None:
            return
        definition = PLANETS[planet["index"] % len(PLANETS)]
        color = definition["color"]
        center = self._to_point(target, planet["x"], planet["y"])
        radius = max(8.0, planet["radius"] * target.height())
        released_at = planet.get("released_at")
        alpha = 1.0
        if released_at is not None:
            alpha = _clamp(
                1.0 - (now - released_at - self.PLANET_FLOAT_S) / self.PLANET_FADE_S,
                0.0,
                1.0,
            )
        if alpha <= 0.01:
            return

        glow_color = _shade(color, 1.15)
        self._glow(painter, center, radius * 1.75, glow_color, int(80 * alpha))
        gradient = QRadialGradient(
            QPointF(center.x() - radius * 0.35, center.y() - radius * 0.4),
            radius * 1.35,
        )
        highlight = _shade(color, 1.32)
        highlight.setAlpha(int(255 * alpha))
        base = QColor(color)
        base.setAlpha(int(255 * alpha))
        shadow = _shade(color, 0.28)
        shadow.setAlpha(int(255 * alpha))
        gradient.setColorAt(0.0, highlight)
        gradient.setColorAt(0.55, base)
        gradient.setColorAt(1.0, shadow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

        if definition["ring"]:
            painter.save()
            painter.translate(center)
            painter.rotate(-18.0 + math.sin(now * 0.8) * 4.0)
            ring_color = _shade(color, 1.08)
            ring_color.setAlpha(int(210 * alpha))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ring_color, max(2.0, radius * 0.16)))
            painter.drawEllipse(QPointF(0, 0), radius * 1.55, radius * 0.48)
            inner = QColor(20, 16, 12, int(120 * alpha))
            painter.setPen(QPen(inner, max(1.0, radius * 0.07)))
            painter.drawEllipse(QPointF(0, 0), radius * 1.42, radius * 0.4)
            painter.restore()
        elif definition["name"] == "Güneş":
            ray = QColor(255, 206, 120)
            ray.setAlpha(int(120 * alpha))
            painter.setPen(QPen(ray, 1.2))
            for index in range(12):
                angle = now * 0.5 + index * math.pi / 6
                painter.drawLine(
                    QPointF(center.x() + math.cos(angle) * radius * 1.15, center.y() + math.sin(angle) * radius * 1.15),
                    QPointF(center.x() + math.cos(angle) * radius * 1.4, center.y() + math.sin(angle) * radius * 1.4),
                )
        else:
            crater = QColor(0, 0, 0, int(46 * alpha))
            painter.setBrush(crater)
            painter.setPen(Qt.PenStyle.NoPen)
            for dx, dy, size in ((-0.3, -0.2, 0.16), (0.22, 0.1, 0.11), (0.05, 0.42, 0.09)):
                painter.drawEllipse(
                    QPointF(center.x() + dx * radius, center.y() + dy * radius),
                    radius * size,
                    radius * size,
                )

        for index in range(5):
            angle = now * 0.9 + index * math.tau / 5
            orbit = radius * 1.5
            point = QPointF(center.x() + math.cos(angle) * orbit, center.y() + math.sin(angle) * orbit * 0.42)
            dot = QColor(255, 232, 176)
            dot.setAlpha(int(150 * alpha))
            painter.setBrush(dot)
            painter.drawEllipse(point, max(1.2, radius * 0.055), max(1.2, radius * 0.055))

        if definition["name"]:
            painter.setFont(hud_font(6.8, QFont.Weight.DemiBold, 1.1))
            metrics = painter.fontMetrics()
            label = definition["name"].upper()
            width = metrics.horizontalAdvance(label) + 14
            chip = QRectF(center.x() - width / 2, center.y() + radius * 1.32, width, 16)
            painter.fillRect(chip, QColor(7, 7, 10, int(190 * alpha)))
            line = QColor(_shade(color, 1.1))
            line.setAlpha(int(200 * alpha))
            painter.setPen(QPen(line, 0.9))
            painter.drawRect(chip)
            painter.setPen(QColor(245, 240, 230, int(235 * alpha)))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_lightning(self, painter: QPainter, target: QRectF, now: float) -> None:
        points_hands = [hand for hand in self.hands if hand.get("gesture") == "point"]
        if len(points_hands) < 2:
            return
        first = points_hands[0]["landmarks"][8]
        second = points_hands[1]["landmarks"][8]
        start = self._to_point(target, float(first[0]), float(first[1]))
        end = self._to_point(target, float(second[0]), float(second[1]))
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        length = math.hypot(dx, dy)
        if length < 6:
            return
        normal = QPointF(-dy / length, dx / length)
        segments = 9
        points = [start]
        for index in range(1, segments):
            ratio = index / segments
            jitter = math.sin(now * 34.0 + index * 2.7) * length * 0.025
            points.append(
                QPointF(
                    start.x() + dx * ratio + normal.x() * jitter,
                    start.y() + dy * ratio + normal.y() * jitter,
                )
            )
        points.append(end)
        glow = QColor(160, 220, 255)
        glow.setAlpha(54)
        painter.setPen(_round_pen(glow, 6.0))
        for index in range(1, len(points)):
            painter.drawLine(points[index - 1], points[index])
        bright = QColor(210, 240, 255)
        painter.setPen(_round_pen(bright, 1.6))
        for index in range(1, len(points)):
            painter.drawLine(points[index - 1], points[index])
        self._glow(painter, start, target.height() * 0.02, QColor(170, 225, 255), 130)
        self._glow(painter, end, target.height() * 0.02, QColor(170, 225, 255), 130)

    def _paint_bursts(self, painter: QPainter, target: QRectF, now: float) -> None:
        alive = []
        for burst in self.bursts:
            age = now - burst["t"]
            if age < 0 or age > 1.15:
                continue
            alive.append(burst)
            progress = age / 1.15
            center = self._to_point(target, burst["x"], burst["y"])
            color = burst.get("color") or (255, 184, 88)
            for index in range(18):
                angle = burst["seed"] + index * math.tau / 18
                speed = 0.05 + 0.20 * ((index * 7) % 11) / 11
                travel = speed * age * target.height()
                fade = int(230 * (1.0 - progress))
                particle = QColor(color[0], color[1], color[2], fade)
                painter.setBrush(particle)
                painter.setPen(Qt.PenStyle.NoPen)
                size = max(0.8, 3.4 * (1.0 - progress))
                painter.drawEllipse(
                    QPointF(center.x() + math.cos(angle) * travel, center.y() + math.sin(angle) * travel),
                    size,
                    size,
                )
            ring = QColor(255, 220, 160)
            ring.setAlpha(int(150 * (1.0 - progress)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ring, 1.4))
            radius = target.height() * 0.04 + target.height() * 0.16 * progress
            painter.drawEllipse(center, radius, radius)
        self.bursts = alive
