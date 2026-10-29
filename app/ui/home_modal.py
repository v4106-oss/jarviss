"""Akıllı ev paneli: ışık ve kahve simülasyonu."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSlider, QVBoxLayout, QWidget

from app.core.home_tools import (
    COFFEE_NAMES,
    FILTER_SECONDS_PER_CUP,
    TURK_BREW_SECONDS,
    TONES,
    SmartHome,
)
from app.theme import ultron as U
from app.theme.themes import rgba_css
from .hud_modal import HudModal, card
from .hud_widgets import Divider, HudButton, hud_font, hud_label


def _combo(options: list[tuple[str, str]]) -> QComboBox:
    combo = QComboBox()
    combo.setFont(hud_font(9, QFont.Weight.Medium, 0.4))
    combo.setStyleSheet(
        f"""
        QComboBox {{
            background:{U.PANEL};
            color:{U.TEXT_SOFT};
            border:1px solid {U.BORDER};
            border-radius:5px;
            padding:5px 9px;
        }}
        QComboBox:hover {{ border-color:{U.RED_SOFT}; }}
        QComboBox QAbstractItemView {{
            background:{U.PANEL};
            color:{U.TEXT_SOFT};
            border:1px solid {U.BORDER};
            selection-background-color:{rgba_css(U.RED, 0.18)};
        }}
        """
    )
    for label, value in options:
        combo.addItem(label, value)
    return combo


def _tone_button(text: str) -> HudButton:
    button = HudButton(text, small=True)
    button.setCheckable(True)
    button.setStyleSheet(
        button.styleSheet()
        + f"""
        QPushButton:checked {{
            background:{rgba_css(U.RED, 0.18)};
            color:{U.RED_BRIGHT};
            border-color:{U.RED};
        }}
        """
    )
    return button


class HomeModal(HudModal):
    """Dört simüle cihazı tek panelde yönetir."""

    def __init__(self, parent: QWidget, home: SmartHome) -> None:
        super().__init__(
            parent,
            "Akıllı Ev",
            "SİMÜLASYON · CİHAZLAR BİLGİSAYARDA CANLANDIRILIR · AĞ KULLANILMAZ",
            width=880,
            height=660,
        )
        self.home = home
        self._refreshing = False
        self.light_widgets: dict[str, dict] = {}
        self.coffee_widgets: dict[str, dict] = {}

        self.content.addWidget(hud_label("Işıklar", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        light_row = QHBoxLayout()
        light_row.setSpacing(12)
        for key in ("salon", "calisma"):
            light_row.addWidget(self._build_light_card(key), 1)
        self.content.addLayout(light_row)

        all_off = HudButton("TÜM IŞIKLARI KAPAT", small=True)
        all_off.clicked.connect(self._all_lights_off)
        self.content.addWidget(all_off, alignment=Qt.AlignmentFlag.AlignRight)

        self.content.addWidget(Divider())
        self.content.addWidget(hud_label("Kahve makineleri", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        coffee_row = QHBoxLayout()
        coffee_row.setSpacing(12)
        coffee_row.addWidget(self._build_turk_card(), 1)
        coffee_row.addWidget(self._build_filter_card(), 1)
        self.content.addLayout(coffee_row)
        note = hud_label(
            "Türk kahvesi ~75 sn, filtre kahve fincan başına ~40 sn demlenir. "
            "Süreler gerçek zamanlı ilerler; panel kapanınca da durum korunur.",
            7,
            U.FAINT,
            QFont.Weight.Medium,
            0.6,
        )
        note.setWordWrap(True)
        self.content.addWidget(note)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self.closed.connect(self._timer.stop)
        self._timer.start()
        self._refresh()

    def _build_light_card(self, key: str) -> QWidget:
        frame = card(padding=14)
        body = frame.layout()
        light = self.home.light(key)
        body.addWidget(hud_label(light.name, 10, U.TEXT, QFont.Weight.DemiBold, 1.2, upper=True))
        state = hud_label("KAPALI", 8, U.DIM, QFont.Weight.Medium, 1.0)
        body.addWidget(state)
        body.addSpacing(4)
        brightness = hud_label("%70", 7, U.MUTED, QFont.Weight.Medium, 0.6)
        body.addWidget(brightness)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(light.brightness)
        slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                height:4px;background:{U.PANEL};border:1px solid {U.BORDER};border-radius:2px;
            }}
            QSlider::sub-page:horizontal {{
                background:{U.RED};border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                width:13px;margin:-6px 0;border-radius:6px;
                background:{U.RED_BRIGHT};border:1px solid {U.RED_DIM};
            }}
            """
        )
        slider.valueChanged.connect(lambda value, room=key: self._on_brightness(room, value))
        body.addWidget(slider)
        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        toggle = HudButton("AÇ / KAPAT", small=True, accent=True)
        toggle.clicked.connect(lambda _checked=False, room=key: self._toggle_light(room))
        buttons.addWidget(toggle)
        body.addLayout(buttons)
        tones = QHBoxLayout()
        tones.setSpacing(5)
        tone_buttons = {}
        for tone, label in TONES.items():
            button = _tone_button(label.upper())
            button.clicked.connect(lambda _checked=False, room=key, value=tone: self._set_tone(room, value))
            tone_buttons[tone] = button
            tones.addWidget(button)
        body.addLayout(tones)
        self.light_widgets[key] = {
            "state": state,
            "brightness": brightness,
            "slider": slider,
            "tones": tone_buttons,
        }
        return frame

    def _build_turk_card(self) -> QWidget:
        frame = card(padding=14)
        body = frame.layout()
        body.addWidget(hud_label(COFFEE_NAMES["turk"], 10, U.TEXT, QFont.Weight.DemiBold, 1.2, upper=True))
        state = hud_label("MAKİNE BOŞ", 8, U.MUTED, QFont.Weight.Medium, 0.8)
        body.addWidget(state)
        body.addSpacing(4)
        body.addWidget(hud_label("Köpük", 7, U.DIM, QFont.Weight.Medium, 1.0))
        foam = _combo([("Az köpüklü", "az"), ("Orta köpüklü", "orta"), ("Bol köpüklü", "bol")])
        foam.setCurrentIndex(1)
        body.addWidget(foam)
        body.addWidget(hud_label("Şeker", 7, U.DIM, QFont.Weight.Medium, 1.0))
        sugar = _combo([("Sade", "sade"), ("Az şekerli", "az"), ("Orta şekerli", "orta"), ("Çok şekerli", "cok")])
        sugar.setCurrentIndex(2)
        body.addWidget(sugar)
        brew = HudButton("PİŞİR", small=True, accent=True)
        brew.clicked.connect(lambda: self._brew("turk"))
        body.addWidget(brew, alignment=Qt.AlignmentFlag.AlignLeft)
        self.coffee_widgets["turk"] = {
            "state": state,
            "brew": brew,
            "foam": foam,
            "sugar": sugar,
        }
        return frame

    def _build_filter_card(self) -> QWidget:
        frame = card(padding=14)
        body = frame.layout()
        body.addWidget(hud_label(COFFEE_NAMES["filtre"], 10, U.TEXT, QFont.Weight.DemiBold, 1.2, upper=True))
        state = hud_label("MAKİNE BOŞ", 8, U.MUTED, QFont.Weight.Medium, 1.0)
        state.setWordWrap(True)
        body.addWidget(state)
        body.addSpacing(4)
        body.addWidget(hud_label("Fincan", 7, U.DIM, QFont.Weight.Medium, 1.0))
        cups = _combo([("1 fincan", "1"), ("2 fincan", "2"), ("3 fincan", "3"), ("4 fincan", "4")])
        cups.setCurrentIndex(1)
        body.addWidget(cups)
        keep_warm = hud_label("Hazır olunca sıcak tutulur.", 7, U.FAINT, QFont.Weight.Medium, 0.6)
        body.addWidget(keep_warm)
        brew = HudButton("DEMLE", small=True, accent=True)
        brew.clicked.connect(lambda: self._brew("filtre"))
        body.addWidget(brew, alignment=Qt.AlignmentFlag.AlignLeft)
        self.coffee_widgets["filtre"] = {
            "state": state,
            "brew": brew,
            "cups": cups,
        }
        return frame

    def _on_brightness(self, key: str, value: int) -> None:
        if self._refreshing:
            return
        self.home.set_light(key, on=value > 0, brightness=value)
        self._refresh()

    def _toggle_light(self, key: str) -> None:
        light = self.home.light(key)
        self.home.set_light(key, on=not light.on)
        self._refresh()

    def _set_tone(self, key: str, tone: str) -> None:
        self.home.set_light(key, tone=tone)
        self._refresh()

    def _all_lights_off(self) -> None:
        self.home.set_all_lights(on=False)
        self._refresh()

    def _brew(self, kind: str) -> None:
        entry = self.coffee_widgets[kind]
        if kind == "turk":
            foam = str(entry["foam"].currentData())
            sugar = str(entry["sugar"].currentData())
            self.home.brew(kind, foam=foam, sugar=sugar)
        else:
            cups = int(str(entry["cups"].currentData()))
            self.home.brew(kind, cups=cups)
        self._refresh()

    def _refresh(self) -> None:
        self._refreshing = True
        try:
            for key, widgets in self.light_widgets.items():
                light = self.home.light(key)
                widgets["state"].setText(
                    f"AÇIK · %{light.brightness} · {TONES.get(light.tone, 'Nötr').upper()}"
                    if light.on
                    else "KAPALI"
                )
                widgets["brightness"].setText(f"%{light.brightness}")
                widgets["slider"].blockSignals(True)
                widgets["slider"].setValue(light.brightness)
                widgets["slider"].blockSignals(False)
                for tone, button in widgets["tones"].items():
                    button.blockSignals(True)
                    button.setChecked(light.tone == tone)
                    button.blockSignals(False)
            for kind, widgets in self.coffee_widgets.items():
                coffee = self.home.coffee(kind)
                widgets["state"].setText(coffee.describe(self.home.now()).upper())
                widgets["brew"].setEnabled(coffee.started_at is None or coffee.ready)
        finally:
            self._refreshing = False

    def refresh(self) -> None:
        self._refresh()
