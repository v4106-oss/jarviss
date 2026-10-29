"""Ana HUD üzerine açılan koyu, ortalanmış HUD pencereleri."""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.theme import ultron as U
from app.theme.themes import rgba_css
from .hud_widgets import Divider, clear_layout, hud_font, hud_label


class _ModalVeil(QWidget):
    """Web görünümlü kartların üstünde yumuşak karartma; tıklamayı geçirir."""

    def __init__(self, modal: "HudModal") -> None:
        super().__init__(modal)
        self._modal = modal
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:
        alpha = int(238 * max(0.0, min(1.0, self._modal._veil_alpha)))
        if alpha <= 0:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(2, 2, 3, alpha))
        painter.end()


class HudModal(QWidget):
    """Ana pencerenin üstünü karartan, kartı ortada tutan taban sınıf."""

    closed = Signal()
    open_settings_requested = Signal()

    def __init__(
        self,
        parent: QWidget,
        title: str,
        subtitle: str = "",
        width: int = 820,
        height: Optional[int] = None,
        padding: int = 26,
        fade: bool = True,
        scrollable: bool = True,
        persistent: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._preferred_width = width
        self._preferred_height = height
        self._padding = padding
        self._closed = False
        self._persistent = bool(persistent)
        self._backdrop = 1.0
        self._backdrop_animation = None
        self._close_animation = None
        self._entrance_animation = None
        self._entrance_value = 0.0
        self._entrance_rise = 18
        self._veil = None
        self._veil_alpha = 0.0
        self._veil_animation = None
        self._skip_backdrop_fade = False

        outer = QVBoxLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(24, 20, 24, 20)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.card = QFrame()
        self.card.setObjectName("modalCard")
        self.card.setStyleSheet(
            f"""
            QFrame#modalCard {{
                background:{U.PANEL};
                border:1px solid {U.BORDER};
                border-radius:{U.WINDOW_RADIUS}px;
            }}
            """
        )
        row.addWidget(self.card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(self._padding, 22, self._padding, 0)
        card_layout.setSpacing(0)

        self.header = QWidget()
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(7)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        self.title_label = hud_label(title, 15, U.TEXT, QFont.Weight.DemiBold, 0.2)
        self.close_button = QPushButton("✕")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setStyleSheet(
            f"""
            QPushButton {{
                background:transparent;
                color:{U.TEXT_SOFT};
                border:1px solid {U.BORDER};
                border-radius:7px;
                font-size:12px;
                padding:0;
            }}
            QPushButton:hover {{
                color:{U.RED_BRIGHT};
                border-color:{U.RED_SOFT};
                background:{rgba_css(U.RED, 0.10)};
            }}
            """
        )
        self.close_button.clicked.connect(self.close_modal)
        top.addWidget(self.title_label)
        top.addStretch(1)
        top.addWidget(self.close_button)
        header_layout.addLayout(top)
        self.subtitle_label = hud_label(subtitle, 8, U.MUTED, QFont.Weight.Medium, 2.2, upper=True)
        self.subtitle_label.setWordWrap(True)
        header_layout.addWidget(self.subtitle_label)
        card_layout.addWidget(self.header)
        card_layout.addSpacing(16)

        self.scroll = None
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 0, 4, 26)
        self.content.setSpacing(14)
        if scrollable:
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}QScrollArea > QWidget > QWidget{background:transparent;}")
            self.scroll.viewport().setStyleSheet("background:transparent;")
            self.scroll.setWidget(self.body)
            card_layout.addWidget(self.scroll, 1)
        else:
            card_layout.addWidget(self.body, 1)

        self._effect = None
        self._animation = None
        if fade:
            self._effect = QGraphicsOpacityEffect(self.card)
            self._effect.setOpacity(0.0)
            self.card.setGraphicsEffect(self._effect)
            self._animation = QPropertyAnimation(self._effect, b"opacity", self)
            self._animation.setDuration(170)
            self._animation.setStartValue(0.0)
            self._animation.setEndValue(1.0)
            self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        else:
            # Web görünümü kart opaklığıyla solmaz; üstüne çizilen perde kullanılır.
            self._veil = _ModalVeil(self)
            self._veil.hide()

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text.upper())

    def add_actions(self, layout: QHBoxLayout) -> None:
        card_layout = self.card.layout()
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(self._padding, 12, self._padding, 16)
        row.setSpacing(8)
        row.addStretch(1)
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                row.addWidget(item.widget())
        card_layout.addWidget(holder)

    def _apply_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        available_width = max(360, parent.width() - 64)
        available_height = max(320, parent.height() - 64)
        self.card.setFixedWidth(min(self._preferred_width, available_width))
        if self._preferred_height:
            height = self._preferred_height
        else:
            height = self.header.sizeHint().height() + self.body.sizeHint().height() + 58
        self.card.setFixedHeight(max(280, min(int(height), available_height)))

    def _set_backdrop(self, value) -> None:
        self._backdrop = float(value)
        self.update()

    def _set_entrance_offset(self, value) -> None:
        self._entrance_value = float(value)
        offset = int(round(self._entrance_rise * self._entrance_value))
        self._outer_layout.setContentsMargins(24, 20 + offset, 24, 20 - offset)

    def _finish_entrance(self) -> None:
        animation = self._entrance_animation
        self._entrance_animation = None
        if animation is not None:
            animation.deleteLater()
        self._set_entrance_offset(0.0)

    def _start_entrance(self) -> None:
        if self._entrance_animation is not None:
            try:
                self._entrance_animation.stop()
            except RuntimeError:
                pass
            self._entrance_animation.deleteLater()
        animation = QVariantAnimation(self)
        animation.setDuration(240)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_entrance_offset)
        animation.finished.connect(self._finish_entrance)
        self._entrance_animation = animation
        animation.start()

    @staticmethod
    def _stop_animation(animation) -> None:
        if animation is None:
            return
        try:
            animation.stop()
        except RuntimeError:
            pass
        try:
            animation.deleteLater()
        except RuntimeError:
            pass

    def _cancel_close_animation(self) -> None:
        animation = self._close_animation
        self._close_animation = None
        self._stop_animation(animation)

    def _set_veil(self, value) -> None:
        self._veil_alpha = float(value)
        if self._veil is not None:
            self._veil.update()

    def _start_veil_enter(self) -> None:
        if self._veil is None:
            return
        self._stop_animation(self._veil_animation)
        self._veil.setGeometry(self.rect())
        self._veil.show()
        self._veil.raise_()
        self._set_veil(1.0)
        animation = QVariantAnimation(self)
        animation.setDuration(300)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_veil)
        self._veil_animation = animation
        animation.start()

    def _start_backdrop_enter(self) -> None:
        self._stop_animation(self._backdrop_animation)
        if self._skip_backdrop_fade:
            self._skip_backdrop_fade = False
            self._set_backdrop(1.0)
        else:
            self._set_backdrop(0.0)
            animation = QVariantAnimation(self)
            animation.setDuration(200)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(self._set_backdrop)
            self._backdrop_animation = animation
            animation.start()
        if self._animation is not None:
            self._animation.stop()
            self._animation.start()

    def prepare_incoming(self) -> None:
        """Başka bir bölümden gelirken zemin hazır kalsın; yalnız kart hareket eder."""
        self._skip_backdrop_fade = True

    def showEvent(self, event):
        super().showEvent(event)
        self._cancel_close_animation()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._opened_at = time.monotonic()
        self.setGeometry(self.parentWidget().rect())
        self._apply_geometry()
        self._start_entrance()
        self._start_backdrop_enter()
        self._start_veil_enter()
        self.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_geometry()
        if self._veil is not None:
            self._veil.setGeometry(self.rect())

    def paintEvent(self, event):
        painter = QPainter(self)
        alpha = int(216 * max(0.0, min(1.0, self._backdrop)))
        painter.fillRect(self.rect(), QColor(2, 2, 3, alpha))
        super().paintEvent(event)

    def mousePressEvent(self, event):
        # Açılış tıklaması veya hızlı çift tıklama modalı hemen kapatmasın.
        if time.monotonic() - getattr(self, "_opened_at", 0.0) < 0.35:
            return
        if not self.card.geometry().contains(event.position().toPoint()):
            self.close_modal()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close_modal()
            return
        super().keyPressEvent(event)

    def close_modal(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.closed.emit()
        if not self.isVisible():
            self.hide()
            if not self._persistent:
                self.deleteLater()
            return
        if self._close_animation is not None:
            return
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._stop_animation(self._backdrop_animation)
        self._stop_animation(self._entrance_animation)
        self._entrance_animation = None
        self._stop_animation(self._veil_animation)
        group = QParallelAnimationGroup(self)
        if self._effect is not None:
            fade = QPropertyAnimation(self._effect, b"opacity", group)
            fade.setDuration(170)
            fade.setStartValue(float(self._effect.opacity()))
            fade.setEndValue(0.0)
            fade.setEasingCurve(QEasingCurve.Type.InCubic)
            group.addAnimation(fade)
        backdrop = QVariantAnimation(group)
        backdrop.setDuration(210)
        backdrop.setStartValue(float(self._backdrop))
        backdrop.setEndValue(0.0)
        backdrop.setEasingCurve(QEasingCurve.Type.InCubic)
        backdrop.valueChanged.connect(self._set_backdrop)
        group.addAnimation(backdrop)
        settle = QVariantAnimation(group)
        settle.setDuration(210)
        settle.setStartValue(float(self._entrance_value))
        settle.setEndValue(min(1.0, float(self._entrance_value) + 0.6))
        settle.setEasingCurve(QEasingCurve.Type.InCubic)
        settle.valueChanged.connect(self._set_entrance_offset)
        group.addAnimation(settle)
        if self._veil is not None:
            veil = QVariantAnimation(group)
            veil.setDuration(170)
            veil.setStartValue(float(self._veil_alpha))
            veil.setEndValue(1.0)
            veil.setEasingCurve(QEasingCurve.Type.InCubic)
            veil.valueChanged.connect(self._set_veil)
            group.addAnimation(veil)
        group.finished.connect(self._finish_close_animation)
        self._close_animation = group
        group.start()

    def _finish_close_animation(self) -> None:
        group = self._close_animation
        self._close_animation = None
        if group is not None:
            group.deleteLater()
        if self._veil is not None:
            self._veil.hide()
        self.hide()
        if not self._persistent:
            self.deleteLater()

    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            self.closed.emit()
        super().closeEvent(event)


def card(width: Optional[int] = None, height: Optional[int] = None, padding: int = 12) -> QFrame:
    frame = QFrame()
    frame.setObjectName("innerCard")
    if width:
        frame.setFixedWidth(width)
    if height:
        frame.setFixedHeight(height)
    frame.setStyleSheet(
        f"""
        QFrame#innerCard {{
            background:{U.PANEL_2};
            border:1px solid {U.BORDER};
            border-radius:12px;
        }}
        """
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(5)
    return frame


def stat_card(title: str, value: str, note: str = "", percent: Optional[float] = None) -> QFrame:
    frame = card()
    frame.body = frame.layout()
    frame.body.addWidget(hud_label(title, 7, U.DIM, QFont.Weight.Medium, 0.2))
    value_label = hud_label(value, 19, U.TEXT, QFont.Weight.DemiBold, 0.4)
    frame.body.addWidget(value_label)
    meter = None
    if percent is not None:
        from .hud_widgets import MeterBar

        meter = MeterBar(percent, 3)
        frame.body.addWidget(meter)
    frame.value_label = value_label
    frame.note_label = hud_label(note, 7, U.MUTED, QFont.Weight.Medium, 0.8)
    frame.body.addWidget(frame.note_label)
    frame.meter = meter
    return frame
