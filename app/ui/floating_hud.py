"""Masaüstünde kalan kompakt Jarvis HUD'ı."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from app.theme import ultron as U
from app.theme.themes import rgba_css
from .animations import fade_window_in
from .orb import state_color

CARD_WIDTH = 384
CARD_HEIGHT = 186
EDGE_MARGIN = 24
SNAP_DISTANCE = 22
ACTIVE_INTERVAL_MS = 33
IDLE_INTERVAL_MS = 83
MAX_SUBTITLE = 240

SURFACE_INSET = 5.0
SURFACE_RADIUS = 20.0
CONTENT_LEFT = 22.0
ORB_X = 41.0
ORB_Y = 45.0
TEXT_X = 80.0
SUB_Y = 76.0
DIVIDER_Y = 102.0
CHIP_Y = 110.0
CHIP_HEIGHT = 23.0
WAVE_Y = 153.0

STATE_INFO = {
    "idle": ("HAZIR", state_color("idle")),
    "listening": ("DİNLİYOR", state_color("listening")),
    "thinking": ("DÜŞÜNÜYOR", state_color("thinking")),
    "speaking": ("KONUŞUYOR", state_color("speaking")),
    "sleeping": ("UYKU MODU", U.DIM),
    "error": ("HATA", state_color("speaking")),
}

STATE_HINTS = {
    "idle": "SİSTEM HAZIR · KOMUT BEKLİYOR",
    "listening": "SENİ DİNLİYORUM…",
    "thinking": "GÖREV ADIM ADIM YÜRÜTÜLÜYOR…",
    "speaking": "SESLİ YANIT VERİYORUM…",
    "sleeping": "UYKU MODUNDA · SESSİZCE BEKLİYORUM",
    "error": "BİR HATA OLUŞTU · AYRINTI ANA PENCEREDE",
}


def _compact_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: max(1, limit - 3)].rstrip() + "..."
    return text


class FloatingHud(QWidget):
    restore_requested = Signal()

    def __init__(self, settings=None) -> None:
        super().__init__(None)
        self._settings = settings
        self._state = "idle"
        self._subtitle = ""
        self._subtitle_sender = "JARVIS"
        self._camera_text = ""
        self._camera_count = 0
        self._camera_active = False
        self._mic_enabled = False
        self._mic_level = 0.0
        self._phase = 0.0
        self._pulse = 0.0
        self._spin = 0.0
        self._hover = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._press_pos = QPoint()
        self._moved = False
        self._animating = True
        self._position_checked = False

        self.setWindowTitle("Jarvis HUD")
        self.setAccessibleName("Jarvis yüzen HUD")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setWindowOpacity(0.985)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Çift tıkla ana pencereye dön · sürükleyerek taşı")

        self._close_button = QPushButton("×", self)
        self._close_button.setFixedSize(22, 22)
        self._close_button.move(CARD_WIDTH - 40, 14)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.setToolTip("HUD'ı gizle")
        self._close_button.setAccessibleName("HUD'ı gizle")
        self._close_button.setStyleSheet(
            f"""
            QPushButton {{
                background:{rgba_css(U.TEXT, 0.035)};
                color:{U.MUTED};
                border:1px solid {rgba_css(U.TEXT, 0.08)};
                border-radius:11px;
                padding:0;
                font-family:"{U.FONT}";
                font-size:13px;
            }}
            QPushButton:hover {{
                color:{U.RED_BRIGHT};
                border-color:{U.RED_SOFT};
                background:{rgba_css(U.RED, 0.15)};
            }}
            """
        )
        self._close_button.clicked.connect(self.dismiss)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    @property
    def state(self) -> str:
        return self._state

    @property
    def subtitle(self) -> str:
        return self._subtitle

    @property
    def camera_text(self) -> str:
        return self._camera_text

    @property
    def camera_count(self) -> int:
        return self._camera_count

    @property
    def camera_active(self) -> bool:
        return self._camera_active

    def set_state(self, state: str) -> None:
        self._state = state if state in STATE_INFO else "idle"
        self._sync_timer()
        self.update()

    def set_subtitle(self, text: str, sender: str = "JARVIS") -> None:
        compact = _compact_text(text, MAX_SUBTITLE)
        self._subtitle = compact
        self._subtitle_sender = "SEN" if str(sender).upper() == "SEN" else "JARVIS"
        if self.isVisible():
            self.update()

    def set_camera(self, text: str = "", count: int = 0, active: bool = False) -> None:
        scene = _compact_text(text, 160)
        if scene.startswith(("{", "[")) or '"objects"' in scene[:200].casefold():
            scene = ""
        self._camera_text = scene
        try:
            self._camera_count = max(0, int(count))
        except (TypeError, ValueError):
            self._camera_count = 0
        self._camera_active = bool(active)
        if self.isVisible():
            self.update()

    def set_mic_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._mic_enabled == enabled:
            return
        self._mic_enabled = enabled
        if not enabled:
            self._mic_level = 0.0
        if self.isVisible():
            self.update()

    def set_mic_level(self, level: float) -> None:
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        if abs(value - self._mic_level) < 0.015:
            return
        self._mic_level = value
        if self.isVisible():
            self.update()

    def show_for_minimize(self) -> None:
        if not self._position_checked:
            self.reposition()
        else:
            self._ensure_on_screen()
        appeared = not self.isVisible()
        if appeared:
            self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        if appeared:
            fade_window_in(self, 200)

    def hide_for_restore(self) -> None:
        self.hide()

    def dismiss(self) -> None:
        self.hide()

    def reposition(self) -> None:
        stored = self._load_position()
        if stored is not None and self._position_visible(*stored):
            self.move(*stored)
        else:
            self._move_to_default()
            self._save_position()
        self._position_checked = True

    def reset_position(self) -> None:
        self._move_to_default()
        self._save_position()

    def _move_to_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.right() - self.width() - EDGE_MARGIN
        y = area.top() + EDGE_MARGIN
        self.move(max(area.left(), x), max(area.top(), y))

    def _ensure_on_screen(self) -> None:
        if not self._position_visible(self.x(), self.y()):
            self._move_to_default()
            self._save_position()

    @staticmethod
    def _position_visible(x: int, y: int) -> bool:
        point = QPoint(int(x) + CARD_WIDTH // 2, int(y) + CARD_HEIGHT // 2)
        screen = QGuiApplication.screenAt(point)
        if screen is None:
            return False
        visible = screen.availableGeometry().intersected(
            QRect(int(x), int(y), CARD_WIDTH, CARD_HEIGHT)
        )
        return visible.width() >= 120 and visible.height() >= 60

    def _load_position(self):
        getter = getattr(self._settings, "floating_hud_position", None)
        try:
            position = getter() if callable(getter) else getter
        except Exception:
            return None
        if not isinstance(position, (tuple, list)) or len(position) != 2:
            return None
        try:
            return int(position[0]), int(position[1])
        except (TypeError, ValueError):
            return None

    def _save_position(self) -> None:
        saver = getattr(self._settings, "save_floating_hud_position", None)
        if not callable(saver):
            return
        try:
            saver(self.x(), self.y())
        except OSError:
            pass

    def _snap_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x, y = self.x(), self.y()
        if abs(x - area.left()) <= SNAP_DISTANCE:
            x = area.left()
        elif abs(x + self.width() - area.right()) <= SNAP_DISTANCE:
            x = area.right() - self.width()
        if abs(y - area.top()) <= SNAP_DISTANCE:
            y = area.top()
        elif abs(y + self.height() - area.bottom()) <= SNAP_DISTANCE:
            y = area.bottom() - self.height()
        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)

    def _interval(self) -> int:
        return (
            ACTIVE_INTERVAL_MS
            if self._state in ("listening", "thinking", "speaking", "error")
            else IDLE_INTERVAL_MS
        )

    def _sync_timer(self) -> None:
        if self._animating and self.isVisible():
            self._timer.start(self._interval())
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._position_checked:
            self.reposition()
        self._sync_timer()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _tick(self) -> None:
        step = 0.026 if self._state in ("listening", "speaking") else 0.018
        if self._state == "thinking":
            step = 0.032
        self._phase = (self._phase + step) % math.tau
        self._spin = (self._spin + (0.52 if self._state in ("thinking", "listening") else 0.18)) % math.tau
        self._pulse = 0.5 + 0.5 * math.sin(self._phase * 2.1)
        self.update()

    @staticmethod
    def _surface(width: int, height: int) -> QRectF:
        return QRectF(
            SURFACE_INSET + 0.5,
            SURFACE_INSET + 0.5,
            width - SURFACE_INSET * 2 - 1.0,
            height - SURFACE_INSET * 2 - 1.0,
        )

    def paintEvent(self, event) -> None:
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        color = QColor(STATE_INFO[self._state][1])
        card = self._surface(width, height)
        self._paint_surface(painter, card, color)

        clip = QPainterPath()
        clip.addRoundedRect(card, SURFACE_RADIUS, SURFACE_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        self._paint_sweep(painter, card, color)
        self._paint_corner_ticks(painter, card, color)
        self._paint_orb(painter, card, color)
        self._paint_header(painter, width, card, color)
        self._paint_subtitle(painter, width, color)
        self._paint_divider(painter, width, color)
        self._paint_status_chips(painter)
        self._paint_waveform(painter, width, color)
        self._paint_footer(painter, width, height)
        painter.restore()
        painter.end()

    def _paint_surface(self, painter: QPainter, card: QRectF, color: QColor) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for growth, alpha in ((5.0, 14), (3.2, 22), (1.6, 30)):
            halo = card.adjusted(-growth, -growth + 1.0, growth, growth + 1.0)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(
                halo, SURFACE_RADIUS + growth, SURFACE_RADIUS + growth
            )

        background = QLinearGradient(card.topLeft(), card.bottomRight())
        background.setColorAt(0.0, QColor(23, 19, 26, 253))
        background.setColorAt(0.46, QColor(11, 11, 16, 253))
        background.setColorAt(1.0, QColor(5, 5, 8, 254))
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(card, SURFACE_RADIUS, SURFACE_RADIUS)

        sheen = QLinearGradient(card.left(), card.top(), card.left(), card.top() + 78)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 16))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.save()
        sheen_clip = QPainterPath()
        sheen_clip.addRoundedRect(card, SURFACE_RADIUS, SURFACE_RADIUS)
        painter.setClipPath(sheen_clip)
        painter.setBrush(QBrush(sheen))
        painter.drawRect(QRectF(card.left(), card.top(), card.width(), 78))
        painter.restore()

        glow_center = QPointF(card.left() + ORB_X, card.top() + ORB_Y - 4.0)
        glow = QRadialGradient(glow_center, 168.0)
        active = self._state in ("listening", "thinking", "speaking", "error")
        center_alpha = 44 + int(16 * self._pulse) if active else 30
        glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), center_alpha))
        glow.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), 11))
        glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setBrush(QBrush(glow))
        painter.drawRoundedRect(card, SURFACE_RADIUS, SURFACE_RADIUS)

        border = QLinearGradient(card.topLeft(), card.bottomRight())
        base_alpha = (124 + int(40 * self._pulse)) if active else 92
        if self._hover:
            base_alpha = min(255, base_alpha + 22)
        border.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), base_alpha))
        border.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(), 42))
        border.setColorAt(1.0, QColor(255, 255, 255, 18))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QBrush(border), 1.1))
        painter.drawRoundedRect(card, SURFACE_RADIUS, SURFACE_RADIUS)

        painter.setPen(QPen(QColor(255, 255, 255, 10), 1.0))
        painter.drawRoundedRect(
            card.adjusted(1.4, 1.4, -1.4, -1.4),
            SURFACE_RADIUS - 1.4,
            SURFACE_RADIUS - 1.4,
        )
        painter.restore()

    def _paint_sweep(self, painter: QPainter, card: QRectF, color: QColor) -> None:
        span = 88.0
        travel = (self._phase / math.tau) % 1.0
        start = card.left() + 14.0 - span + travel * (card.width() - 28.0 + span * 2.0)
        left = max(card.left() + 14.0, start)
        right = min(card.right() - 14.0, start + span)
        if right - left < 8.0:
            return
        gradient = QLinearGradient(start, 0, start + span, 0)
        alpha = 130 if self._state in ("listening", "thinking", "speaking") else 40
        if self._hover:
            alpha += 24
        gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 0))
        gradient.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), alpha))
        gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.save()
        painter.setPen(
            QPen(QBrush(gradient), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        painter.drawLine(QPointF(left, card.top() + 0.9), QPointF(right, card.top() + 0.9))
        painter.restore()

    def _paint_corner_ticks(self, painter: QPainter, card: QRectF, color: QColor) -> None:
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 58), 1.0))
        length = 9.0
        inset = 9.0
        x1, y1 = card.left() + inset, card.top() + inset
        x2, y2 = card.left() + inset, card.bottom() - inset
        painter.drawLine(QPointF(x1, y1), QPointF(x1 + length, y1))
        painter.drawLine(QPointF(x1, y1), QPointF(x1, y1 + length))
        painter.drawLine(QPointF(x2 - length, y2), QPointF(x2, y2))
        painter.drawLine(QPointF(x2, y2 - length), QPointF(x2, y2))
        painter.restore()

    def _paint_orb(self, painter: QPainter, card: QRectF, color: QColor) -> None:
        painter.save()
        center = QPointF(card.left() + ORB_X, card.top() + ORB_Y)
        active = self._state in ("listening", "thinking", "speaking", "error")
        breathing = 1.0 + (0.13 if self._state in ("listening", "speaking") else 0.05) * self._pulse

        halo = QRadialGradient(center, 46.0)
        halo.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 34))
        halo.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(), 8))
        halo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(center, 46.0, 46.0)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 34), 1.0))
        painter.drawEllipse(center, 27.0, 27.0)
        painter.setPen(
            QPen(
                QColor(color.red(), color.green(), color.blue(), 172 if active else 122),
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawArc(
            QRectF(center.x() - 27.0, center.y() - 27.0, 54.0, 54.0),
            int((-self._spin * 180 / math.pi - 58) * 16),
            74 * 16,
        )
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 28), 1.0))
        painter.drawEllipse(center, 21.0, 21.0)
        painter.setPen(
            QPen(
                QColor(color.red(), color.green(), color.blue(), 128),
                1.2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawArc(
            QRectF(center.x() - 21.0, center.y() - 21.0, 42.0, 42.0),
            int(self._spin * 180 / math.pi) * 16,
            128 * 16,
        )

        tick_pen = QPen(QColor(color.red(), color.green(), color.blue(), 62), 1.0)
        painter.setPen(tick_pen)
        for dx, dy in ((0.0, -1.0), (0.0, 1.0), (-1.0, 0.0), (1.0, 0.0)):
            start = QPointF(center.x() + dx * 29.5, center.y() + dy * 29.5)
            end = QPointF(center.x() + dx * 33.0, center.y() + dy * 33.0)
            painter.drawLine(start, end)

        radius = 10.2 * breathing
        core = QRadialGradient(center, radius + 1.5)
        core.setColorAt(0.0, QColor(255, 242, 245, 250))
        core.setColorAt(0.32, QColor(color.red(), color.green(), color.blue(), 240))
        core.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 24))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(core))
        painter.drawEllipse(center, radius, radius)

        speck = QRadialGradient(center, radius * 0.5)
        speck.setColorAt(0.0, QColor(255, 255, 255, 190))
        speck.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(speck))
        painter.drawEllipse(center, radius * 0.62, radius * 0.62)
        painter.restore()

    def _paint_header(
        self, painter: QPainter, width: int, card: QRectF, color: QColor
    ) -> None:
        painter.save()
        title_font = QFont(U.FONT)
        title_font.setPointSizeF(9.3)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.4)
        painter.setFont(title_font)
        painter.setPen(QColor(U.TEXT))
        painter.drawText(
            QRectF(TEXT_X, card.top() + 10.0, width - TEXT_X - 72.0, 18.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "JARVIS",
        )

        state_font = QFont(U.FONT)
        state_font.setPointSizeF(6.5)
        state_font.setWeight(QFont.Weight.DemiBold)
        state_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        painter.setFont(state_font)
        label = STATE_INFO[self._state][0]
        metrics = QFontMetrics(state_font)
        chip = QRectF(
            TEXT_X,
            card.top() + 31.0,
            metrics.horizontalAdvance(label) + 30.0,
            17.0,
        )
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 62), 1.0))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 18))
        painter.drawRoundedRect(chip, 8.5, 8.5)

        dot = QPointF(chip.left() + 10.0, chip.center().y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 46))
        painter.drawEllipse(dot, 4.0, 4.0)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 234))
        painter.drawEllipse(dot, 2.2, 2.2)

        painter.setPen(QColor(color.red(), color.green(), color.blue(), 234))
        painter.drawText(
            QRectF(chip.left() + 17.0, chip.top(), chip.width() - 18.0, chip.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            label,
        )

        clock_font = QFont(U.MONO)
        clock_font.setPointSizeF(7.0)
        clock_font.setWeight(QFont.Weight.Medium)
        painter.setFont(clock_font)
        painter.setPen(QColor(U.MUTED if self._hover else U.DIM))
        painter.drawText(
            QRectF(width - 114.0, card.top() + 12.0, 62.0, 15.0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            time.strftime("%H:%M"),
        )
        painter.restore()

    def _paint_subtitle(
        self, painter: QPainter, width: int, color: QColor
    ) -> None:
        painter.save()
        if self._subtitle:
            sender = self._subtitle_sender
            text = self._subtitle
            tone = QColor(U.GOLD) if sender == "SEN" else QColor(color)
            muted = False
        elif self._camera_active and self._camera_text:
            sender = "KAMERA"
            text = self._camera_text
            tone = QColor(U.RED_BRIGHT)
            muted = False
        else:
            sender = "DURUM"
            text = STATE_HINTS[self._state]
            tone = QColor(U.MUTED)
            muted = True

        tick = QRectF(CONTENT_LEFT, SUB_Y + 3.0, 2.4, 12.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tone)
        painter.drawRoundedRect(tick, 1.2, 1.2)

        sender_font = QFont(U.FONT)
        sender_font.setPointSizeF(6.4)
        sender_font.setWeight(QFont.Weight.DemiBold)
        sender_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.3)
        painter.setFont(sender_font)
        sender_color = QColor(tone)
        sender_color.setAlpha(228 if not muted else 170)
        painter.setPen(sender_color)
        sender_width = QFontMetrics(sender_font).horizontalAdvance(sender)
        painter.drawText(
            QRectF(CONTENT_LEFT + 9.0, SUB_Y, sender_width + 1.0, 17.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            sender,
        )

        body_font = QFont(U.FONT)
        body_font.setPointSizeF(8.2)
        body_font.setWeight(QFont.Weight.Medium)
        painter.setFont(body_font)
        metrics = QFontMetrics(body_font)
        body_x = CONTENT_LEFT + 9.0 + sender_width + 10.0
        available = max(24.0, width - CONTENT_LEFT - body_x)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(available))
        painter.setPen(QColor(U.MUTED if muted else U.TEXT_SOFT))
        painter.drawText(
            QRectF(body_x, SUB_Y, available, 17.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided,
        )
        painter.restore()

    def _paint_divider(self, painter: QPainter, width: int, color: QColor) -> None:
        painter.save()
        left = CONTENT_LEFT
        right = width - CONTENT_LEFT
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 140), 1.0))
        painter.drawLine(QPointF(left, DIVIDER_Y), QPointF(left + 14.0, DIVIDER_Y))
        gradient = QLinearGradient(left + 14.0, 0.0, right, 0.0)
        gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 52))
        gradient.setColorAt(0.6, QColor(255, 255, 255, 14))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(QPen(QBrush(gradient), 1.0))
        painter.drawLine(QPointF(left + 14.0, DIVIDER_Y), QPointF(right, DIVIDER_Y))
        painter.restore()

    def _paint_status_chips(self, painter: QPainter) -> None:
        font = QFont(U.FONT)
        font.setPointSizeF(6.4)
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
        metrics = QFontMetrics(font)

        accent = QColor(U.RED_BRIGHT)
        mic_on = self._mic_enabled
        mic_text = "MİKROFON AÇIK" if mic_on else "MİKROFON KAPALI"
        mic_width = 11.0 + 12.0 + 7.0 + metrics.horizontalAdvance(mic_text) + 13.0
        mic_rect = QRectF(CONTENT_LEFT, CHIP_Y, mic_width, CHIP_HEIGHT)
        if mic_on:
            mic_tone = QColor(accent)
            mic_outline = QColor(accent.red(), accent.green(), accent.blue(), 74)
            mic_fill = QColor(accent.red(), accent.green(), accent.blue(), 20)
        else:
            mic_tone = QColor(U.DIM)
            mic_outline = QColor(255, 255, 255, 24)
            mic_fill = QColor(255, 255, 255, 6)
        self._draw_chip(
            painter, mic_rect, font, mic_text, self._draw_mic_glyph,
            mic_tone, mic_outline, mic_fill,
        )

        cam_on = self._camera_active
        if cam_on:
            cam_text = (
                f"KAMERA · {self._camera_count} HEDEF"
                if self._camera_count
                else "KAMERA · CANLI"
            )
            cam_tone = QColor(U.GOLD)
            cam_outline = QColor(cam_tone.red(), cam_tone.green(), cam_tone.blue(), 82)
            cam_fill = QColor(cam_tone.red(), cam_tone.green(), cam_tone.blue(), 20)
        else:
            cam_text = "KAMERA KAPALI"
            cam_tone = QColor(U.DIM)
            cam_outline = QColor(255, 255, 255, 24)
            cam_fill = QColor(255, 255, 255, 6)
        cam_width = 11.0 + 12.0 + 7.0 + metrics.horizontalAdvance(cam_text) + 13.0
        cam_rect = QRectF(mic_rect.right() + 8.0, CHIP_Y, cam_width, CHIP_HEIGHT)
        self._draw_chip(
            painter, cam_rect, font, cam_text, self._draw_camera_glyph,
            cam_tone, cam_outline, cam_fill,
        )

    @staticmethod
    def _draw_chip(
        painter: QPainter,
        rect: QRectF,
        font: QFont,
        text: str,
        glyph,
        tone: QColor,
        outline: QColor,
        fill: QColor,
    ) -> None:
        painter.save()
        painter.setPen(QPen(outline, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        glyph(painter, QPointF(rect.left() + 11.0, rect.center().y() - 6.0), tone)
        painter.setFont(font)
        painter.setPen(tone)
        painter.drawText(
            QRectF(rect.left() + 30.0, rect.top(), rect.width() - 38.0, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()

    @staticmethod
    def _draw_mic_glyph(painter: QPainter, origin: QPointF, color: QColor) -> None:
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                color,
                1.25,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawRoundedRect(
            QRectF(origin.x() + 4.0, origin.y() + 1.2, 4.0, 6.6), 2.0, 2.0
        )
        path = QPainterPath()
        path.moveTo(origin.x() + 2.4, origin.y() + 6.0)
        path.arcTo(QRectF(origin.x() + 2.4, origin.y() + 3.2, 7.2, 7.2), 180.0, -180.0)
        painter.drawPath(path)
        painter.drawLine(
            QPointF(origin.x() + 6.0, origin.y() + 10.4),
            QPointF(origin.x() + 6.0, origin.y() + 12.0),
        )
        painter.restore()

    @staticmethod
    def _draw_camera_glyph(painter: QPainter, origin: QPointF, color: QColor) -> None:
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                color,
                1.25,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawRoundedRect(
            QRectF(origin.x() + 1.4, origin.y() + 3.4, 9.2, 6.8), 1.8, 1.8
        )
        painter.drawRoundedRect(
            QRectF(origin.x() + 4.2, origin.y() + 1.6, 4.0, 2.2), 0.9, 0.9
        )
        painter.drawEllipse(QPointF(origin.x() + 6.0, origin.y() + 6.8), 2.0, 2.0)
        painter.restore()

    def _paint_waveform(self, painter: QPainter, width: int, color: QColor) -> None:
        painter.save()
        left = CONTENT_LEFT
        right = width - CONTENT_LEFT
        bars = 56
        gap = 2.2
        bar_width = max(1.4, (right - left - (bars - 1) * gap) / bars)
        max_height = 13.0
        state = self._state

        painter.setPen(QPen(QColor(255, 255, 255, 9), 1.0))
        painter.drawLine(QPointF(left, WAVE_Y), QPointF(right, WAVE_Y))

        for index in range(bars):
            envelope = math.sin(math.pi * (index + 1) / (bars + 1)) ** 0.55
            if state == "listening":
                wobble = 0.5 + 0.5 * math.sin(self._phase * 4.2 + index * 0.9)
                if self._mic_level > 0.02:
                    level = 0.05 + 0.95 * self._mic_level * (0.45 + 0.55 * wobble)
                else:
                    level = 0.02 + 0.02 * wobble
            elif state == "speaking":
                level = 0.12 + 0.8 * abs(math.sin(self._phase * 3.2 + index * 0.68))
            elif state == "thinking":
                level = 0.1 + 0.44 * (0.5 + 0.5 * math.sin(self._phase * 2.8 - index * 0.36))
            elif state == "error":
                level = 0.14 + 0.26 * abs(math.sin(self._phase * 5.0 + index * 0.48))
            else:
                level = 0.05 + 0.09 * (0.5 + 0.5 * math.sin(self._phase * 1.2 + index * 0.3))

            bar_height = max(1.2, max_height * level * envelope)
            alpha = 46 + int(150 * level)
            if state in ("listening", "speaking"):
                alpha = min(232, alpha + 24)
            if self._hover:
                alpha = min(255, alpha + 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), alpha))
            x = left + index * (bar_width + gap)
            painter.drawRoundedRect(
                QRectF(x, WAVE_Y - bar_height / 2.0, bar_width, bar_height),
                bar_width / 2.0,
                bar_width / 2.0,
            )
        painter.restore()

    def _paint_footer(self, painter: QPainter, width: int, height: int) -> None:
        painter.save()
        font = QFont(U.FONT)
        font.setPointSizeF(5.7)
        font.setWeight(QFont.Weight.Medium)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        painter.setFont(font)
        painter.setPen(QColor(U.MUTED if self._hover else U.FAINT))
        painter.drawText(
            QRectF(CONTENT_LEFT, height - 19.0, width - CONTENT_LEFT * 2.0, 11.0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "ÇİFT TIKLA · ANA PENCERE",
        )
        painter.drawText(
            QRectF(CONTENT_LEFT, height - 19.0, width - CONTENT_LEFT * 2.0, 11.0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "SÜRÜKLE · KÖŞEYE YAPIŞ",
        )
        painter.restore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._moved = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.globalPosition().toPoint() - self._press_pos).manhattanLength() > 4:
                self._moved = True
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if self._moved:
                self._snap_to_screen()
                self._save_position()
                self._position_checked = True
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.restore_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background:{U.PANEL_2};
                color:{U.TEXT};
                border:1px solid {U.BORDER};
                padding:4px;
            }}
            QMenu::item {{ padding:6px 18px; }}
            QMenu::item:selected {{ background:{U.RED_DIM}; color:{U.TEXT}; }}
            """
        )
        menu.addAction("Ana pencereye dön", self.restore_requested.emit)
        menu.addAction("Konumu sıfırla", self.reset_position)
        menu.addSeparator()
        menu.addAction("HUD'ı gizle", self.dismiss)
        menu.exec(event.globalPos())
        event.accept()
