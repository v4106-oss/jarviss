"""Zaman, sistem, zamanlayıcı, notlar, takvim ve model pencereleri."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDateTime, QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core import speech
from app.core.hud_data import (
    MONTHS,
    WEEKDAYS,
    HudStore,
    SystemSampler,
    TimerEngine,
    format_remaining,
    moon_phase,
    turkish_date,
)
from app.theme import ultron as U
from .hud_modal import HudModal, card, stat_card
from .hud_widgets import (
    Divider,
    EmptyState,
    HudButton,
    MeterBar,
    clear_layout,
    hud_font,
    hud_label,
)

WORLD_ZONES = [
    ("İstanbul", "Europe/Istanbul"),
    ("Londra", "Europe/London"),
    ("Berlin", "Europe/Berlin"),
    ("Dubai", "Asia/Dubai"),
    ("Tokyo", "Asia/Tokyo"),
    ("Sidney", "Australia/Sydney"),
    ("New York", "America/New_York"),
    ("Los Angeles", "America/Los_Angeles"),
]


class DayProgress(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(18)
        self.percent = 0.0

    def set_percent(self, percent: float) -> None:
        self.percent = max(0.0, min(100.0, float(percent)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        painter.setPen(QPen(QColor(U.FAINT), 1))
        painter.drawLine(2, int(y), self.width() - 2, int(y))
        x = 2 + (self.width() - 4) * self.percent / 100
        painter.setPen(QPen(QColor(U.RED), 1.6))
        painter.drawLine(2, int(y), int(x), int(y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(U.RED_BRIGHT))
        painter.drawEllipse(int(x) - 2, int(y) - 2, 4, 4)


class TimeModal(HudModal):
    def __init__(self, parent, weather: Optional[dict] = None) -> None:
        super().__init__(parent, "Zaman", "", width=900)
        self.weather = weather or {}
        self.now = datetime.now()
        year, week, _ = self.now.isocalendar()
        day_of_year = self.now.timetuple().tm_yday

        self.time_label = hud_label("00:00:00", 44, U.TEXT, QFont.Weight.DemiBold, 2.0)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.date_label = hud_label("", 9, U.TEXT_SOFT, QFont.Weight.Medium, 0.3)
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.detail_label = hud_label("", 7, U.MUTED, QFont.Weight.Normal, 0.2)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.day_progress = DayProgress()
        self.day_progress.setMinimumWidth(640)

        self.content.addSpacing(4)
        self.content.addWidget(self.time_label)
        self.content.addWidget(self.date_label)
        self.content.addSpacing(6)
        self.content.addWidget(self.day_progress)
        self.content.addWidget(self.detail_label)
        self.content.addSpacing(12)
        self.content.addWidget(hud_label("Dünya Saatleri", 8, U.MUTED, QFont.Weight.Medium, 0.3))

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.city_time_labels: List[QLabel] = []
        self.city_offset_labels: List[QLabel] = []
        for index, (city, zone_name) in enumerate(WORLD_ZONES):
            frame = card(height=66, padding=10)
            layout = frame.layout()
            layout.addWidget(hud_label(city, 7, U.MUTED, QFont.Weight.Medium, 0.2))
            time_label = hud_label("--:--", 16, U.TEXT, QFont.Weight.DemiBold, 0.6)
            offset_label = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.8)
            layout.addWidget(time_label)
            layout.addWidget(offset_label)
            self.city_time_labels.append(time_label)
            self.city_offset_labels.append(offset_label)
            grid.addWidget(frame, index // 4, index % 4)
        self.content.addLayout(grid)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 4, 0, 0)
        footer_layout.addStretch(1)
        self.sunrise_label = hud_label("Gün doğumu —", 8, U.MUTED, QFont.Weight.Medium, 0.2)
        self.sunset_label = hud_label("Gün batımı —", 8, U.MUTED, QFont.Weight.Medium, 0.2)
        self.moon_label = hud_label(moon_phase(), 8, U.GOLD, QFont.Weight.DemiBold, 1.6)
        for label in (self.sunrise_label, self.sunset_label, self.moon_label):
            footer_layout.addWidget(label)
            if label is not self.moon_label:
                separator = hud_label("·", 8, U.DIM)
                footer_layout.addWidget(separator)
        footer_layout.addStretch(1)
        self.content.addWidget(footer)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_clock)
        self._timer.start(1000)
        self._update_clock()

    def set_weather(self, weather: Optional[dict]) -> None:
        self.weather = weather or {}
        self._update_clock()

    def _update_clock(self) -> None:
        now = datetime.now()
        self.time_label.setText(now.strftime("%H:%M:%S"))
        self.date_label.setText(f"{turkish_date(now)} {now.year}")
        year, week, _ = now.isocalendar()
        seconds = now.hour * 3600 + now.minute * 60 + now.second
        percent = seconds / 86400.0 * 100
        self.day_progress.set_percent(percent)
        self.detail_label.setText(
            f"Günün %{percent:.0f}'i geçti · Yılın {now.timetuple().tm_yday}. günü · {week}. hafta"
        )

        local_offset = now.astimezone().utcoffset() or timedelta()
        for index, (_, zone_name) in enumerate(WORLD_ZONES):
            try:
                zone_now = now.astimezone(ZoneInfo(zone_name))
            except Exception:
                continue
            self.city_time_labels[index].setText(zone_now.strftime("%H:%M"))
            zone_offset = zone_now.utcoffset() or timedelta()
            difference = round((zone_offset.total_seconds() - local_offset.total_seconds()) / 3600)
            if difference == 0:
                text = "aynı saat"
            else:
                text = f"{difference:+d} sa"
            self.city_offset_labels[index].setText(text)

        current = (self.weather or {}).get("current") or {}
        self.sunrise_label.setText(f"Gün doğumu {current.get('sunrise', '—')}")
        self.sunset_label.setText(f"Gün batımı {current.get('sunset', '—')}")
        self.moon_label.setText(current.get("moon", moon_phase(now)))

    def close_modal(self) -> None:
        self._timer.stop()
        super().close_modal()


class SystemModal(HudModal):
    def __init__(self, parent, controls=None) -> None:
        super().__init__(parent, "Sistem", "", width=920)
        self.controls = controls
        self.content.addWidget(hud_label("Canlı Ölçümler", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.cpu_card = stat_card("İşlemci", "%—", "", 0)
        self.memory_card = stat_card("Bellek", "%—", "", 0)
        self.disk_card = stat_card("Disk", "%—", "", 0)
        self.battery_card = stat_card("Pil", "—", "", None)
        self.network_card = stat_card("Ağ", "—", "Toplam ↑ — · ↓ —", None)
        self.uptime_card = stat_card("Açık Kalma", "—", "", None)
        for column, widget in enumerate(
            (self.cpu_card, self.memory_card, self.disk_card, self.battery_card, self.network_card, self.uptime_card)
        ):
            grid.addWidget(widget, column // 3, column % 3)
        self.content.addLayout(grid)
        self.content.addSpacing(8)
        self.content.addWidget(hud_label("Süreçler", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        processes = QHBoxLayout()
        processes.setSpacing(12)
        self.cpu_process_panel, self.cpu_process_rows = self._process_panel("En çok CPU")
        self.memory_process_panel, self.memory_process_rows = self._process_panel("En çok bellek")
        processes.addWidget(self.cpu_process_panel)
        processes.addWidget(self.memory_process_panel)
        self.content.addLayout(processes)
        self.content.addSpacing(8)
        self.content.addWidget(hud_label("Hızlı Kontroller", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        controls_row = QHBoxLayout()
        controls_row.setSpacing(7)
        for label, action_name in (
            ("SES −", "volume_down"),
            ("SES +", "volume_up"),
            ("SESSİZ", "mute"),
            ("OYNAT / DURAKLAT", "media_play_pause"),
            ("ÖNCEKİ", "media_previous"),
            ("SONRAKİ", "media_next"),
            ("KİLİTLE", "lock"),
        ):
            button = HudButton(label, small=True)
            button.clicked.connect(lambda _=False, name=action_name: self._control(name))
            controls_row.addWidget(button)
        controls_row.addStretch(1)
        self.content.addLayout(controls_row)
        self.control_status = hud_label(
            "Ses, medya ve ekran kilidi işlemleri buradan yapılır.",
            7,
            U.DIM,
            QFont.Weight.Medium,
            0.6,
        )
        self.content.addWidget(self.control_status)

    def _control(self, action_name: str) -> None:
        if self.controls is None:
            self.control_status.setText("Sistem kontrolleri kullanılamıyor.")
            return
        try:
            self.control_status.setText(self.controls.action(action_name))
        except Exception as error:  # noqa: BLE001 - kullanıcıya durum satırında göster
            self.control_status.setText(f"İşlem başarısız: {error}")

    def _process_panel(self, title: str):
        frame = QFrame()
        frame.setObjectName("innerCard")
        frame.setStyleSheet(
            f"QFrame#innerCard{{background:{U.PANEL_2};border:1px solid {U.BORDER};border-radius:8px;}}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        layout.addWidget(hud_label(title, 7, U.MUTED, QFont.Weight.Medium, 0.3))
        rows = []
        for _ in range(7):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            name = hud_label("—", 8, U.TEXT_SOFT, QFont.Weight.Medium, 0.3)
            value = hud_label("—", 8, U.MUTED, QFont.Weight.Medium, 0.3)
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            row_layout.addWidget(name, 1)
            row_layout.addWidget(value)
            layout.addWidget(row)
            rows.append((name, value))
        return frame, rows

    def update_data(self, snapshot: dict) -> None:
        if not snapshot or not snapshot.get("available"):
            self.set_subtitle("Sistem bilgisi alınamıyor")
            return
        self.set_subtitle(f"{snapshot.get('host', '')} · {snapshot.get('cpu_name', '')} · {snapshot.get('os', '')}")
        cpu = float(snapshot.get("cpu", 0))
        memory = float(snapshot.get("memory_percent", 0))
        disk = float(snapshot.get("disk_percent", 0))
        self.cpu_card.value_label.setText(f"%{cpu:.0f}")
        self.cpu_card.note_label.setText(f"{snapshot.get('cores', '—')} ÇEKİRDEK · {snapshot.get('threads', '—')} İŞ PARÇACIĞI")
        self.cpu_card.meter.set_value(cpu)
        self.memory_card.value_label.setText(f"%{memory:.0f}")
        self.memory_card.note_label.setText(
            f"{SystemSampler.bytes_text(snapshot.get('memory_used', 0))} / "
            f"{SystemSampler.bytes_text(snapshot.get('memory_total', 0))} · "
            f"TAKAS {SystemSampler.bytes_text(max(0, (snapshot.get('memory_total', 0) - snapshot.get('memory_used', 0))))}"
        )
        self.memory_card.meter.set_value(memory)
        self.disk_card.value_label.setText(f"%{disk:.0f}")
        self.disk_card.note_label.setText(
            f"{SystemSampler.bytes_text(snapshot.get('disk_used', 0))} / "
            f"{SystemSampler.bytes_text(snapshot.get('disk_total', 0))} DOLU"
        )
        self.disk_card.meter.set_value(disk)

        battery = snapshot.get("battery_percent")
        if battery is None:
            self.battery_card.value_label.setText("PİL YOK")
            self.battery_card.note_label.setText("MASAÜSTÜ BESLEME")
        else:
            self.battery_card.value_label.setText(f"%{battery:.0f}")
            self.battery_card.note_label.setText("ŞARJDA" if snapshot.get("battery_plugged") else "PİLDE")
        network = snapshot.get("network") or {}
        self.network_card.value_label.setText(f"{SystemSampler.bytes_text(network.get('down_rate', 0))}/sn ↓")
        self.network_card.note_label.setText(
            f"↑ {SystemSampler.bytes_text(network.get('up_rate', 0))}/sn · "
            f"TOPLAM ↑ {SystemSampler.bytes_text(network.get('total_up', 0))} · "
            f"↓ {SystemSampler.bytes_text(network.get('total_down', 0))}"
        )
        self.uptime_card.value_label.setText(SystemSampler.uptime_text(snapshot.get("uptime", 0)))
        self.uptime_card.note_label.setText("SİSTEM AÇIK")

        for rows, key in ((self.cpu_process_rows, "top_cpu"), (self.memory_process_rows, "top_memory")):
            for index, (name_label, value_label) in enumerate(rows):
                items = snapshot.get(key) or []
                if index < len(items):
                    item = items[index]
                    name = str(item.get("name", "—"))
                    name_label.setText(name[:26])
                    if key == "top_cpu":
                        value_label.setText(f"%{item.get('cpu', 0):.0f}")
                    else:
                        value_label.setText(SystemSampler.bytes_text(item.get("memory", 0)))
                else:
                    name_label.setText("—")
                    value_label.setText("—")


class TimerModal(HudModal):
    def __init__(self, parent, engine: TimerEngine) -> None:
        super().__init__(parent, "Zamanlayıcı", "", width=820)
        self.engine = engine
        self.content.addWidget(hud_label("Yeni Zamanlayıcı", 8, U.MUTED, QFont.Weight.Medium, 0.3))

        presets = QHBoxLayout()
        presets.setSpacing(7)
        for minutes in (1, 3, 5, 10, 15, 25, 45, 60):
            button = HudButton(f"{minutes} DK", small=True)
            button.clicked.connect(lambda _=False, value=minutes: self._add_preset(value))
            presets.addWidget(button)
        presets.addStretch(1)
        self.content.addLayout(presets)

        custom = QHBoxLayout()
        custom.setSpacing(8)
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 600)
        self.minutes.setValue(10)
        self.minutes.setSuffix(" dk")
        self.minutes.setFixedWidth(100)
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("Ad (isteğe bağlı): çay, mola, toplantı…")
        self.label_input.returnPressed.connect(self._add_custom)
        add_button = HudButton("KUR", accent=True)
        add_button.clicked.connect(self._add_custom)
        custom.addWidget(self.minutes)
        custom.addWidget(self.label_input, 1)
        custom.addWidget(add_button)
        self.content.addLayout(custom)

        self.active_title = hud_label("Çalışan zamanlayıcı yok", 8, U.MUTED, QFont.Weight.Medium, 0.3)
        self.content.addWidget(self.active_title)
        self.active_container = QWidget()
        self.active_layout = QVBoxLayout(self.active_container)
        self.active_layout.setContentsMargins(0, 0, 0, 0)
        self.active_layout.setSpacing(7)
        self.content.addWidget(self.active_container)

        self.content.addSpacing(10)
        self.content.addWidget(Divider())
        self.content.addWidget(hud_label("Kronometre", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        self.stopwatch_label = hud_label("00:00.0", 30, U.TEXT, QFont.Weight.DemiBold, 1.4)
        self.content.addWidget(self.stopwatch_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_button = HudButton("BAŞLAT")
        self.start_button.clicked.connect(self._toggle_stopwatch)
        reset_button = HudButton("SIFIRLA")
        reset_button.clicked.connect(self._reset_stopwatch)
        buttons.addWidget(self.start_button)
        buttons.addWidget(reset_button)
        buttons.addStretch(1)
        self.content.addLayout(buttons)

        self._elapsed = QElapsedTimer()
        self._accumulated = 0
        self._running = False
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(100)
        self.engine.changed.connect(self._refresh_timers)
        self._refresh_timers()

    def _add_preset(self, minutes: int) -> None:
        self.engine.add(minutes)
        self._refresh_timers()

    def _add_custom(self) -> None:
        self.engine.add(self.minutes.value(), self.label_input.text())
        self.label_input.clear()
        self._refresh_timers()

    def _refresh_timers(self) -> None:
        clear_layout(self.active_layout)
        items = self.engine.items
        self.active_title.setText(f"Çalışan Zamanlayıcı · {len(items)}" if items else "Çalışan Zamanlayıcı Yok")
        if not items:
            self.active_layout.addWidget(EmptyState("Henüz kurulmuş bir zamanlayıcı yok. Yukarıdaki sürelerden birini seç."))
            return
        for item in items:
            row = QFrame()
            row.setObjectName("timerRow")
            row.setStyleSheet(
                f"QFrame#timerRow{{background:{U.PANEL_2};border:1px solid {U.BORDER};border-radius:7px;}}"
            )
            layout = QHBoxLayout(row)
            layout.setContentsMargins(12, 8, 10, 8)
            layout.setSpacing(10)
            label = hud_label(str(item.get("label", "—")), 9, U.TEXT_SOFT, QFont.Weight.DemiBold, 1.0)
            remaining = hud_label(format_remaining(self.engine.remaining(item)), 12, U.GOLD, QFont.Weight.DemiBold, 1.4)
            cancel = HudButton("İPTAL", small=True)
            cancel.clicked.connect(lambda _=False, timer_id=item.get("id"): self._remove(timer_id))
            layout.addWidget(label, 1)
            layout.addWidget(remaining)
            layout.addWidget(cancel)
            self.active_layout.addWidget(row)
            item["_row_label"] = remaining

    def _remove(self, timer_id: int) -> None:
        self.engine.remove(int(timer_id))
        self._refresh_timers()

    def _tick(self) -> None:
        for item in self.engine.items:
            label = item.get("_row_label")
            if label is not None:
                try:
                    label.setText(format_remaining(self.engine.remaining(item)))
                except RuntimeError:
                    pass
        if self._running:
            milliseconds = self._accumulated + self._elapsed.elapsed()
            seconds, tenths = divmod(milliseconds // 100, 10)
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            text = f"{hours:02d}:{minutes:02d}:{seconds:02d}.{tenths}" if hours else f"{minutes:02d}:{seconds:02d}.{tenths}"
            self.stopwatch_label.setText(text)

    def _toggle_stopwatch(self) -> None:
        if self._running:
            self._accumulated += self._elapsed.elapsed()
            self._running = False
            self.start_button.setText("DEVAM ET")
        else:
            self._elapsed.start()
            self._running = True
            self.start_button.setText("DURDUR")

    def _reset_stopwatch(self) -> None:
        self._running = False
        self._accumulated = 0
        self.stopwatch_label.setText("00:00.0")
        self.start_button.setText("BAŞLAT")

    def close_modal(self) -> None:
        self._tick_timer.stop()
        super().close_modal()


class NotesModal(HudModal):
    changed = Signal()

    def __init__(self, parent, store: HudStore) -> None:
        super().__init__(parent, "Notlar", "", width=720)
        self.store = store
        self.editing_id: Optional[str] = None
        self.content.addWidget(hud_label("Yeni Not", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Yeni not yaz, Enter ile ekle…")
        self.input.returnPressed.connect(self._submit)
        self.submit_button = HudButton("EKLE", accent=True)
        self.submit_button.clicked.connect(self._submit)
        self.cancel_edit = HudButton("VAZGEÇ", small=True)
        self.cancel_edit.clicked.connect(self._cancel_edit)
        self.cancel_edit.hide()
        row.addWidget(self.input, 1)
        row.addWidget(self.submit_button)
        row.addWidget(self.cancel_edit)
        self.content.addLayout(row)

        self.content.addWidget(hud_label("Kayıtlı Notlar", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.content.addWidget(self.list_container)
        self._refresh()

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            self.input.setFocus()
            return
        if self.editing_id:
            self.store.update_note(self.editing_id, text)
        else:
            self.store.add_note(text)
        self.input.clear()
        self._cancel_edit()
        self._refresh()
        self.changed.emit()

    def _cancel_edit(self) -> None:
        self.editing_id = None
        self.submit_button.setText("EKLE")
        self.cancel_edit.hide()

    def _edit(self, note_id: str, text: str) -> None:
        self.editing_id = note_id
        self.input.setText(text)
        self.input.setFocus()
        self.submit_button.setText("GÜNCELLE")
        self.cancel_edit.show()

    def _delete(self, note_id: str) -> None:
        self.store.delete_note(note_id)
        if self.editing_id == note_id:
            self.input.clear()
            self._cancel_edit()
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        clear_layout(self.list_layout)
        notes = list(reversed(self.store.notes()))
        self.set_subtitle(f"{len(notes)} NOT · YEREL DEPO")
        if not notes:
            self.list_layout.addWidget(EmptyState("Henüz not yok. Yukarıya yazıp ekle ya da ana ekrandaki not alanını kullan."))
            return
        for note in notes:
            frame = card(padding=11)
            layout = frame.layout()
            text = hud_label(str(note.get("text", "")), 9, U.TEXT_SOFT, QFont.Weight.Medium, 0.4)
            text.setWordWrap(True)
            layout.addWidget(text)
            row = QHBoxLayout()
            row.setSpacing(6)
            created = str(note.get("created", ""))[:16].replace("T", " ")
            date_label = hud_label(created, 7, U.DIM, QFont.Weight.Medium, 0.8)
            edit = HudButton("DÜZENLE", small=True)
            edit.clicked.connect(lambda _=False, item=note: self._edit(str(item.get("id")), str(item.get("text", ""))))
            delete = HudButton("SİL", small=True)
            delete.clicked.connect(lambda _=False, note_id=str(note.get("id")): self._delete(note_id))
            row.addWidget(date_label, 1)
            row.addWidget(edit)
            row.addWidget(delete)
            layout.addLayout(row)
            self.list_layout.addWidget(frame)


class CalendarModal(HudModal):
    changed = Signal()

    def __init__(self, parent, store: HudStore) -> None:
        super().__init__(parent, "Takvim", "", width=820)
        self.store = store
        self.content.addWidget(hud_label("Yeni Etkinlik", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Etkinlik adı…")
        self.title_input.returnPressed.connect(self._add_event)
        self.when_input = QDateTimeEdit()
        self.when_input.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.when_input.setCalendarPopup(True)
        self.when_input.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self.when_input.setFixedWidth(160)
        add = HudButton("EKLE", accent=True)
        add.clicked.connect(self._add_event)
        row.addWidget(self.title_input, 1)
        row.addWidget(self.when_input)
        row.addWidget(add)
        self.content.addLayout(row)
        self.content.addSpacing(4)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.content.addWidget(self.list_container)
        self.weather_modal = None
        self._refresh()

    def _add_event(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            self.title_input.setFocus()
            return
        when = self.when_input.dateTime().toPython().replace(second=0, microsecond=0).isoformat(timespec="minutes")
        self.store.add_event(title, when)
        self.title_input.clear()
        self.when_input.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self._refresh()
        self.changed.emit()

    def _delete(self, event_id: str) -> None:
        self.store.delete_event(event_id)
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        clear_layout(self.list_layout)
        now = datetime.now()
        horizon = now + timedelta(days=14)
        events = []
        for event in self.store.events():
            try:
                moment = datetime.fromisoformat(str(event.get("when")))
            except (TypeError, ValueError):
                continue
            if now - timedelta(minutes=1) <= moment <= horizon:
                events.append((moment, event))
        events.sort(key=lambda pair: pair[0])
        self.set_subtitle(f"ÖNÜMÜZDEKİ 14 GÜN · {len(events)} ETKİNLİK")
        if not events:
            self.list_layout.addWidget(EmptyState("Önümüzdeki 14 günde takviminde etkinlik yok."))
            return
        grouped: Dict[str, List[tuple]] = defaultdict(list)
        for moment, event in events:
            grouped[moment.strftime("%Y-%m-%d")].append((moment, event))
        for date_key, items in grouped.items():
            moment = datetime.fromisoformat(date_key)
            heading = f"{turkish_date(moment).upper()}" + (" · BUGÜN" if moment.date() == now.date() else "")
            self.list_layout.addWidget(hud_label(heading, 7, U.GOLD, QFont.Weight.DemiBold, 2.0))
            for event_moment, event in items:
                frame = card(padding=10)
                outer = frame.layout()
                layout = QHBoxLayout()
                layout.setContentsMargins(2, 0, 0, 0)
                layout.setSpacing(10)
                time_label = hud_label(event_moment.strftime("%H:%M"), 12, U.RED_BRIGHT, QFont.Weight.DemiBold, 1.0)
                title = hud_label(str(event.get("title", "—")), 9, U.TEXT_SOFT, QFont.Weight.Medium, 0.5)
                delete = HudButton("SİL", small=True)
                delete.clicked.connect(lambda _=False, event_id=str(event.get("id")): self._delete(event_id))
                layout.addWidget(time_label)
                layout.addWidget(title, 1)
                layout.addWidget(delete)
                outer.addLayout(layout)
                self.list_layout.addWidget(frame)


class ModelsModal(HudModal):
    def __init__(self, parent, settings) -> None:
        super().__init__(parent, "AI Modelleri", "", width=720)
        self.settings = settings
        self.content.addWidget(hud_label("Aktif Yapılandırma", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        grid = QGridLayout()
        grid.setSpacing(10)
        engine_name = {
            "gemini": "Google Gemini API",
            "ollama": "Ollama (yerel)",
            "tokenharbor": "Token Harbor (ücretsiz API)",
        }.get(settings.provider, settings.provider)
        model = {
            "gemini": settings.model,
            "ollama": settings.ollama_model,
            "tokenharbor": settings.tokenharbor_model or "otomatik (:free)",
        }.get(settings.provider, "")
        active_key = (
            settings.tokenharbor_api_key
            if settings.provider == "tokenharbor"
            else settings.api_key
        )
        key_state = "TANIMLI" if active_key else "TANIMSIZ"
        full_access = settings.access_mode == "full"
        access = (
            "TAM ERİŞİM" if full_access
            else "ADIM ADIM" if settings.access_mode == "step"
            else "ONAYLI"
        )
        seconds_limit = settings.agent_seconds_limit
        step_limit = settings.agent_step_limit
        loop_limit = settings.agent_loop_limit
        budget = f"{seconds_limit} SN" if seconds_limit else "SINIRSIZ"
        budget_note = (
            f"{step_limit or 'SINIRSIZ'} ADIM · "
            f"DÖNGÜ {loop_limit or 'KAPALI'} · "
            f"{'GERİ ALMA AKTİF' if full_access else 'ONAY KATMANI'}"
        )
        home = settings.home_location
        remote = settings.provider in ("gemini", "tokenharbor")
        key_source = "TOKEN HARBOR" if settings.provider == "tokenharbor" else "GEMINI"
        if settings.provider == "ollama":
            key_source = "GEREKMEZ"
        cards = [
            stat_card("Motor", engine_name, "İNTERNET ÜZERİNDEN" if remote else "YEREL SUNUCU"),
            stat_card("Model", model, "AKTİF"),
            stat_card("API Anahtarı", key_state, key_source),
            stat_card("Erişim Modu", access, "GÜVENLİK KATMANI AÇIK"),
            stat_card("Ses", "NÖRAL SES", speech.style_label(settings.voice_style)),
            stat_card("Ajan Bütçesi", budget, budget_note),
            stat_card("Ev Konumu", str(home.get("name", "—")), f"{home.get('lat', '—')}, {home.get('lon', '—')}"),
            stat_card("Araçlar", "40+ AJAN ARACI", "HARİTA · KOD · SİSTEM · KİŞİSEL"),
            stat_card("Kısayollar", "CTRL+K", "GEÇMİŞ · PANO · BRİFİNG · HATIRLATICI"),
        ]
        for index, widget in enumerate(cards):
            grid.addWidget(widget, index // 3, index % 3)
        self.content.addLayout(grid)
        self.content.addSpacing(6)
        info = hud_label(
            "Model ve anahtar değişiklikleri Ayarlar penceresinden yapılır. Jarvis, seçili model hesapta "
            "bulunmazsa çalışan uygun Gemini modeline kendisi geçer. Onaylı modda aynı adım üst üste "
            "değişmeyen sonuç verirse uyarı gösterilip döngü kesilir; tam erişimde tekrar engeli yoktur, "
            "hatalı dosya değişiklikleri geri alınır. Yasak listesi, denetim günlüğü ve yedekler her iki "
            "modda da açıktır.",
            8,
            U.MUTED,
            QFont.Weight.Medium,
            0.4,
        )
        info.setWordWrap(True)
        self.content.addWidget(info)
        actions = QHBoxLayout()
        actions.addStretch(1)
        close = HudButton("KAPAT")
        close.clicked.connect(self.close_modal)
        open_settings = HudButton("AYARLARI AÇ", accent=True)
        open_settings.clicked.connect(self._open_settings)
        actions.addWidget(close)
        actions.addWidget(open_settings)
        self.content.addLayout(actions)

    def _open_settings(self) -> None:
        self.open_settings_requested.emit()
        self.close_modal()
