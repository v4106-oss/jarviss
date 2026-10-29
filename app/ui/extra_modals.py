"""Hatırlatıcı, pano, sohbet geçmişi, brifing ve komut paleti pencereleri."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from PySide6.QtCore import QDateTime, QEvent, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDateTimeEdit,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.core.hud_data import HudStore, local_moment
from app.core.memory_tools import MAX_KEY, MAX_NOTES, MAX_VALUE
from app.core.personal_tools import (
    ClipboardHistory,
    matches_query,
    messages_to_markdown,
)
from app.core.storage import Storage
from app.core.task_session import TaskSessionError, TaskSessionManager
from app.theme import ultron as U
from app.theme.themes import rgba_css
from .hud_modal import HudModal, card
from .hud_widgets import Divider, EmptyState, HudButton, clear_layout, hud_font, hud_label


def _role_time(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = local_moment(value)
    if moment is None:
        return ""
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%d.%m.%Y %H:%M")


class RemindersModal(HudModal):
    changed = Signal()

    def __init__(self, parent, store: HudStore) -> None:
        super().__init__(parent, "Hatırlatıcılar", "", width=780, height=640)
        self.store = store
        self.content.addWidget(hud_label("Yeni hatırlatıcı", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Ne hatırlatılsın? Örn. ilaç, toplantı, çay…")
        self.text_input.returnPressed.connect(self._add)
        self.content.addWidget(self.text_input)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.when_input = QDateTimeEdit()
        self.when_input.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.when_input.setCalendarPopup(True)
        self.when_input.setMinimumDateTime(QDateTime.currentDateTime())
        self.when_input.setDateTime(QDateTime.currentDateTime().addSecs(1800))
        self.when_input.setFixedWidth(170)
        add_button = HudButton("HATIRLAT", accent=True)
        add_button.clicked.connect(self._add)
        self.clear_button = HudButton("TAMAMLANANLARI TEMİZLE", small=True)
        self.clear_button.clicked.connect(self._clear_completed)
        row.addWidget(self.when_input)
        row.addWidget(add_button)
        row.addStretch(1)
        row.addWidget(self.clear_button)
        self.content.addLayout(row)
        self.content.addSpacing(4)
        self.content.addWidget(Divider())
        self.list_title = hud_label("Bekleyen hatırlatıcılar", 8, U.MUTED, QFont.Weight.Medium, 0.3)
        self.content.addWidget(self.list_title)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.content.addWidget(self.list_container)
        self._refresh()

    def _add(self) -> None:
        text = self.text_input.text().strip()
        if not text:
            self.text_input.setFocus()
            return
        when = self.when_input.dateTime().toPython().replace(second=0, microsecond=0).isoformat(timespec="minutes")
        try:
            self.store.add_reminder(text, when)
        except ValueError:
            self.set_subtitle("Geçerli bir tarih ve saat seç.")
            self.when_input.setFocus()
            return
        self.text_input.clear()
        self.when_input.setDateTime(QDateTime.currentDateTime().addSecs(1800))
        self._refresh()
        self.changed.emit()

    def _complete(self, reminder_id: str) -> None:
        self.store.complete_reminder(reminder_id)
        self._refresh()
        self.changed.emit()

    def _delete(self, reminder_id: str) -> None:
        self.store.delete_reminder(reminder_id)
        self._refresh()
        self.changed.emit()

    def _clear_completed(self) -> None:
        removed = self.store.clear_completed_reminders()
        self._refresh()
        if removed:
            self.changed.emit()

    def refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        clear_layout(self.list_layout)
        items = self.store.reminders()
        pending = [item for item in items if not item.get("done")]
        pending.sort(key=lambda item: (
            local_moment(item.get("when")) is None,
            local_moment(item.get("when")) or datetime.max,
        ))
        completed = [item for item in items if item.get("done")]
        self.clear_button.setEnabled(bool(completed))
        self.set_subtitle(f"{len(pending)} BEKLEYEN · {len(completed)} TAMAMLANAN · YEREL DEPO")
        self.list_title.setText(f"Bekleyen Hatırlatıcılar · {len(pending)}")
        if not pending:
            self.list_layout.addWidget(
                EmptyState("Bekleyen hatırlatıcı yok. Yukarıdan metin ve zaman seçip HATIRLAT'a bas.")
            )
        for item in pending:
            moment = local_moment(item.get("when"))
            overdue = moment is not None and moment < datetime.now()
            frame = card(padding=11)
            layout = frame.layout()
            outer = QHBoxLayout()
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(10)
            when_text = moment.strftime("%d.%m %H:%M") if moment else str(item.get("when", "—"))
            when_label = hud_label(when_text, 11, U.RED_BRIGHT if overdue else U.GOLD, QFont.Weight.DemiBold, 0.8)
            when_label.setFixedWidth(100)
            body = QVBoxLayout()
            body.setSpacing(3)
            text_label = hud_label(str(item.get("text", "—")), 9, U.TEXT_SOFT, QFont.Weight.Medium, 0.4)
            text_label.setWordWrap(True)
            state = hud_label("SÜRESİ GEÇTİ · BİLDİRİM BEKLİYOR" if overdue and not item.get("notified") else "BEKLİYOR",
                              7, U.DIM, QFont.Weight.Medium, 1.0)
            body.addWidget(text_label)
            body.addWidget(state)
            done = HudButton("TAMAM", small=True)
            done.clicked.connect(lambda _=False, rid=str(item.get("id")): self._complete(rid))
            remove = HudButton("SİL", small=True)
            remove.clicked.connect(lambda _=False, rid=str(item.get("id")): self._delete(rid))
            outer.addWidget(when_label)
            outer.addLayout(body, 1)
            outer.addWidget(done)
            outer.addWidget(remove)
            layout.addLayout(outer)
            self.list_layout.addWidget(frame)
        if completed:
            self.list_layout.addSpacing(6)
            self.list_layout.addWidget(hud_label(f"Tamamlananlar · {len(completed)}", 7, U.DIM, QFont.Weight.DemiBold, 1.8))
            for item in completed[-8:]:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(2, 2, 2, 2)
                row_layout.setSpacing(8)
                text_label = hud_label(str(item.get("text", "—")), 8, U.MUTED, QFont.Weight.Medium, 0.3)
                text_label.setWordWrap(True)
                remove = HudButton("SİL", small=True)
                remove.clicked.connect(lambda _=False, rid=str(item.get("id")): self._delete(rid))
                row_layout.addWidget(text_label, 1)
                row_layout.addWidget(remove)
                self.list_layout.addWidget(row)


class ClipboardModal(HudModal):
    def __init__(self, parent, history: ClipboardHistory) -> None:
        super().__init__(parent, "Pano Merkezi", "OTURUM BOYUNCA BELLEKTE · UYGULAMA KAPANINCA SİLİNİR", width=780, height=620)
        self.history = history
        self.search = QLineEdit()
        self.search.setPlaceholderText("Pano geçmişinde ara…")
        self.search.textChanged.connect(self._refresh)
        self.content.addWidget(self.search)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.content.addWidget(self.list_container)
        footer = QHBoxLayout()
        self.notice = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.8)
        clear = HudButton("GEÇMİŞİ TEMİZLE", small=True)
        clear.clicked.connect(self._clear)
        footer.addWidget(self.notice, 1)
        footer.addWidget(clear)
        self.content.addLayout(footer)
        self._refresh()

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.notice.setText("Panoya kopyalandı.")

    def _remove(self, text: str) -> None:
        self.history.remove(text)
        self._refresh()

    def _clear(self) -> None:
        self.history.clear()
        self._refresh()
        self.notice.setText("Pano geçmişi temizlendi.")

    def _refresh(self) -> None:
        clear_layout(self.list_layout)
        query = self.search.text()
        items = [item for item in self.history.items() if matches_query(query, item)]
        self.set_subtitle(
            f"{len(self.history)} KAYIT · {len(items)} GÖSTERİLİYOR · OTURUM BOYUNCA BELLEKTE"
        )
        if not items:
            text = "Pano geçmişinde eşleşen kayıt yok." if query else (
                "Henüz pano kaydı yok. Bir metni kopyaladığında burada görünür."
            )
            self.list_layout.addWidget(EmptyState(text))
            return
        for index, text in enumerate(items, start=1):
            frame = card(padding=10)
            layout = frame.layout()
            preview = text.replace("\r", " ").replace("\n", " ⏎ ")
            if len(preview) > 260:
                preview = preview[:260] + "…"
            label = hud_label(preview, 9, U.TEXT_SOFT, QFont.Weight.Medium, 0.3)
            label.setWordWrap(True)
            layout.addWidget(label)
            row = QHBoxLayout()
            row.setSpacing(7)
            info = hud_label(f"#{index} · {len(text)} KARAKTER", 7, U.DIM, QFont.Weight.Medium, 1.0)
            copy = HudButton("KOPYALA", small=True)
            copy.clicked.connect(lambda _=False, value=text: self._copy(value))
            remove = HudButton("SİL", small=True)
            remove.clicked.connect(lambda _=False, value=text: self._remove(value))
            row.addWidget(info, 1)
            row.addWidget(copy)
            row.addWidget(remove)
            layout.addLayout(row)
            self.list_layout.addWidget(frame)


class ChatHistoryModal(HudModal):
    def __init__(self, parent, storage: Storage) -> None:
        super().__init__(parent, "Sohbet Geçmişi", "ARAMA · KOPYALAMA · MARKDOWN DIŞA AKTARMA", width=880, height=700)
        self.storage = storage
        self.messages: List[Any] = []
        self.search = QLineEdit()
        self.search.setPlaceholderText("Sohbet geçmişinde ara… (boş bırakırsan son mesajlar)")
        self.search.textChanged.connect(self._schedule)
        self.content.addWidget(self.search)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._reload)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.content.addWidget(self.list_container)
        footer = QHBoxLayout()
        self.notice = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.8)
        self.notice.setWordWrap(True)
        copy_all = HudButton("TÜMÜNÜ KOPYALA", small=True)
        copy_all.clicked.connect(self._copy_all)
        export = HudButton("MARKDOWN DIŞA AKTAR", small=True, accent=True)
        export.clicked.connect(self._export)
        footer.addWidget(self.notice, 1)
        footer.addWidget(copy_all)
        footer.addWidget(export)
        self.content.addLayout(footer)
        self._reload()

    def _schedule(self) -> None:
        self._search_timer.start(250)

    def _reload(self) -> None:
        query = self.search.text().strip()
        try:
            if query:
                self.messages = self.storage.search_messages(query, 120)
            else:
                self.messages = self.storage.get_recent_messages(120)
        except Exception as error:
            self.messages = []
            self.notice.setText(f"Geçmiş okunamadı: {error}")
        self._render()
        mode = f"ARAMA · {query}" if query else "SON MESAJLAR"
        self.set_subtitle(f"{len(self.messages)} KAYIT · {mode}")

    def _render(self) -> None:
        clear_layout(self.list_layout)
        if not self.messages:
            text = "Bu aramada mesaj bulunamadı." if self.search.text().strip() else "Henüz kayıtlı sohbet yok."
            self.list_layout.addWidget(EmptyState(text))
            return
        for message in self.messages:
            role = str(getattr(message, "role", "assistant"))
            content = str(getattr(message, "content", "") or "")
            stamp = _role_time(getattr(message, "created_at", None))
            frame = card(padding=10)
            layout = frame.layout()
            head = QHBoxLayout()
            head.setSpacing(8)
            name = "SEN" if role == "user" else "JARVIS"
            name_label = hud_label(name, 7, U.RED_BRIGHT if role == "user" else U.GOLD, QFont.Weight.DemiBold, 2.0)
            time_label = hud_label(stamp, 7, U.FAINT, QFont.Weight.Medium, 0.8)
            copy = HudButton("KOPYALA", small=True)
            copy.clicked.connect(lambda _=False, value=content: self._copy(value))
            head.addWidget(name_label)
            head.addWidget(time_label)
            head.addStretch(1)
            head.addWidget(copy)
            layout.addLayout(head)
            preview = content if len(content) <= 1500 else content[:1500] + "\n… (kısaltıldı, tam metin kopyalanır)"
            label = hud_label(preview or "(boş yanıt)", 9, U.TEXT_SOFT, QFont.Weight.Normal, 0.2)
            label.setWordWrap(True)
            label.setTextInteractionFlags(label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
            self.list_layout.addWidget(frame)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.notice.setText("Mesaj panoya kopyalandı.")

    def _copy_all(self) -> None:
        if not self.messages:
            return
        QApplication.clipboard().setText(messages_to_markdown(self.messages))
        self.notice.setText(f"{len(self.messages)} mesaj Markdown olarak panoya kopyalandı.")

    def _export(self) -> None:
        if not self.messages:
            self.notice.setText("Dışa aktarılacak mesaj yok.")
            return
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        base = Path(documents) if documents else Path.home()
        default = base / f"Jarvis_sohbet_{datetime.now():%Y%m%d_%H%M}.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "Sohbeti dışa aktar", str(default), "Markdown (*.md);;Metin (*.txt)"
        )
        if not path:
            return
        try:
            Path(path).write_text(messages_to_markdown(self.messages), encoding="utf-8")
            self.notice.setText(f"Dışa aktarıldı: {path}")
        except OSError as error:
            self.notice.setText(f"Yazılamadı: {error}")


class TaskTraceModal(HudModal):
    """Ajan çalışmalarının araç bazlı izini ve özetini gösterir."""

    STATUS_STYLES = {
        "completed": ("TAMAMLANDI", U.GOLD),
        "running": ("SÜRÜYOR", U.RED_SOFT),
        "cancelled": ("DURDURULDU", U.MUTED),
        "error": ("HATA", U.RED_BRIGHT),
    }

    def __init__(self, parent, storage: Storage) -> None:
        super().__init__(parent, "Görev Günlüğü", "AJAN ÇALIŞMALARI · ARAÇ İZLERİ · SON 15", width=920, height=720)
        self.storage = storage
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.content.addWidget(self.list_container)
        footer = QHBoxLayout()
        self.notice = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.8)
        self.notice.setWordWrap(True)
        refresh = HudButton("YENİLE", small=True, accent=True)
        refresh.clicked.connect(self._reload)
        footer.addWidget(self.notice, 1)
        footer.addWidget(refresh)
        self.content.addLayout(footer)
        self._reload()

    def _reload(self) -> None:
        try:
            runs = self.storage.get_agent_runs(15)
        except Exception as error:  # noqa: BLE001 - modal içinde kısa bildirim
            runs = []
            self.notice.setText(f"Görev kayıtları okunamadı: {error}")
        clear_layout(self.list_layout)
        if not runs:
            self.list_layout.addWidget(EmptyState("Henüz kayıtlı ajan görevi yok."))
            self.set_subtitle("0 GÖREV")
            return
        total_tools = 0
        for run in runs:
            try:
                events = self.storage.get_agent_events(int(run.get("id") or 0), 60)
            except Exception:
                events = []
            total_tools += sum(1 for event in events if event.get("event_type") == "tool_result")
            self.list_layout.addWidget(self._run_card(run, events))
        self.set_subtitle(f"{len(runs)} GÖREV · {total_tools} ARAÇ İZİ")

    def _run_card(self, run: dict, events: List[dict]) -> QWidget:
        frame = card(padding=11)
        layout = frame.layout()
        status = str(run.get("status") or "completed")
        label, color = self.STATUS_STYLES.get(status, self.STATUS_STYLES["completed"])
        head = QHBoxLayout()
        head.setSpacing(7)
        head.addWidget(hud_label("●", 7, color, QFont.Weight.DemiBold, 0.0))
        head.addWidget(hud_label(label, 6, color, QFont.Weight.DemiBold, 1.6))
        head.addWidget(hud_label(f"· GÖREV #{run.get('id')}", 6, U.FAINT, QFont.Weight.Medium, 1.2))
        head.addStretch(1)
        stamp = hud_label(_role_time(run.get("created_at")), 6, U.FAINT, QFont.Weight.Medium, 0.6)
        head.addWidget(stamp)
        copy = HudButton("KOPYALA", small=True)
        copy.clicked.connect(lambda _=False, r=run, e=events: self._copy(r, e))
        head.addWidget(copy)
        layout.addLayout(head)

        prompt = str(run.get("prompt") or "").strip() or "(boş görev)"
        prompt_label = hud_label(prompt, 9, U.TEXT, QFont.Weight.DemiBold, 0.2)
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        tool_events = [event for event in events if event.get("event_type") == "tool_result"]
        if tool_events:
            for event in tool_events[-10:]:
                payload = self._payload(event)
                tool = str(payload.get("tool") or "araç")
                result = re.sub(r"\s+", " ", str(payload.get("result") or "")).strip()
                layout.addWidget(hud_label(f"› {tool} · {result[:240]}", 7, U.TEXT_SOFT, QFont.Weight.Normal, 0.2))
            if len(tool_events) > 10:
                layout.addWidget(hud_label(f"… +{len(tool_events) - 10} araç izi daha", 6, U.FAINT, QFont.Weight.Medium, 0.6))
        else:
            layout.addWidget(hud_label("Araç izi kaydedilmedi.", 7, U.FAINT, QFont.Weight.Medium, 0.4))

        summary = str(run.get("summary") or "").strip()
        if summary:
            preview = re.sub(r"\s+", " ", summary)[:420]
            layout.addWidget(hud_label(f"Özet: {preview}", 7, U.MUTED, QFont.Weight.Normal, 0.2))
        return frame

    @staticmethod
    def _payload(event: dict) -> dict:
        raw = event.get("payload")
        if isinstance(raw, dict):
            return raw
        try:
            data = json.loads(raw or "{}")
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}

    def _copy(self, run: dict, events: List[dict]) -> None:
        lines = [
            f"# Görev #{run.get('id')}",
            f"Durum: {run.get('status')}",
            f"Komut: {run.get('prompt')}",
            f"Başlangıç: {_role_time(run.get('created_at'))}",
        ]
        if run.get("finished_at"):
            lines.append(f"Bitiş: {_role_time(run.get('finished_at'))}")
        if run.get("summary"):
            lines.append(f"Özet: {run.get('summary')}")
        if events:
            lines.append("")
            lines.append("## Araç izleri")
            for event in events:
                if event.get("event_type") != "tool_result":
                    continue
                payload = self._payload(event)
                lines.append(f"- {payload.get('tool')}: {str(payload.get('result') or '')[:600]}")
        QApplication.clipboard().setText("\n".join(lines))
        self.notice.setText("Görev izi panoya kopyalandı.")


class TaskSessionModal(HudModal):
    """Etkin görevin adım izini, notlarını ve insan devralma kontrollerini gösterir."""

    STATUS_STYLES = {
        "running": ("SÜRÜYOR", U.RED_SOFT),
        "paused": ("DURAKLATILDI", U.GOLD),
        "takeover": ("KULLANICIDA", U.GOLD),
        "completed": ("TAMAMLANDI", U.GOLD),
        "cancelled": ("DURDURULDU", U.MUTED),
        "error": ("HATA", U.RED_BRIGHT),
    }

    def __init__(
        self,
        parent,
        manager: TaskSessionManager,
        pause: Optional[Callable[[str], None]] = None,
        resume: Optional[Callable[[], None]] = None,
        takeover: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(
            parent, "Görev Oturumu", "ADIM İZİ · DURAKLAT · DEVRAL · YETENEK", width=940, height=780
        )
        self.manager = manager
        self._pause = pause
        self._resume = resume
        self._takeover = takeover
        self._session_id: Optional[int] = None
        self._status = ""

        self.summary_card = card(padding=12)
        summary_layout = self.summary_card.layout()
        self.goal_label = hud_label("", 10, U.TEXT, QFont.Weight.DemiBold, 0.4)
        self.goal_label.setWordWrap(True)
        summary_layout.addWidget(self.goal_label)
        self.meta_label = hud_label("", 7, U.MUTED, QFont.Weight.Medium, 0.8)
        self.meta_label.setWordWrap(True)
        summary_layout.addWidget(self.meta_label)
        self.pause_label = hud_label("", 7, U.GOLD, QFont.Weight.Medium, 0.6)
        self.pause_label.setWordWrap(True)
        self.pause_label.hide()
        summary_layout.addWidget(self.pause_label)
        self.content.addWidget(self.summary_card)

        controls = QHBoxLayout()
        controls.setSpacing(7)
        self.pause_button = HudButton("DURAKLAT", small=True, accent=True)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.takeover_button = HudButton("DEVRAL", small=True)
        self.takeover_button.clicked.connect(self._do_takeover)
        self.export_button = HudButton("DIŞA AKTAR", small=True)
        self.export_button.clicked.connect(self._export)
        self.learn_button = HudButton("YETENEK OLARAK KAYDET", small=True)
        self.learn_button.clicked.connect(self._learn_skill)
        for button in (self.pause_button, self.takeover_button, self.export_button, self.learn_button):
            controls.addWidget(button)
        controls.addStretch(1)
        self.notice = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.6)
        self.notice.setWordWrap(True)
        controls.addWidget(self.notice, 1)
        refresh = HudButton("YENİLE", small=True)
        refresh.clicked.connect(self._reload)
        controls.addWidget(refresh)
        self.content.addLayout(controls)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.content.addWidget(self.list_container)

        self._timer = QTimer(self)
        self._timer.setInterval(1400)
        self._timer.timeout.connect(self._reload)
        self._timer.start()
        self._reload()

    def close_modal(self) -> None:
        self._timer.stop()
        super().close_modal()

    def _capture(self):
        try:
            return self.manager.current() or self.manager.storage.get_latest_task_session()
        except Exception:
            return None

    def _reload(self) -> None:
        session = self._capture()
        clear_layout(self.list_layout)
        if not session:
            self._session_id = None
            self._status = ""
            self.goal_label.setText("Henüz görev oturumu yok.")
            self.meta_label.setText("Jarvis bir görev çalıştırdığında adımlar burada birikir.")
            self.pause_label.hide()
            for button in (self.pause_button, self.takeover_button, self.export_button, self.learn_button):
                button.setEnabled(False)
            self.list_layout.addWidget(EmptyState("Görev başlatınca araç adımları ve doğrulamalar kaydedilir."))
            self.set_subtitle("0 GÖREV")
            return

        self._session_id = int(session["id"])
        status = str(session.get("status") or "completed")
        self._status = status
        label, color = self.STATUS_STYLES.get(status, self.STATUS_STYLES["completed"])
        self.goal_label.setText(f"Görev #{self._session_id} · {session.get('goal')}")
        self.meta_label.setText(
            f"{label} · {session.get('created_at', '')[:19].replace('T', ' ')}"
            + (f" · Bitiş {str(session.get('finished_at'))[:19].replace('T', ' ')}" if session.get("finished_at") else "")
        )
        self.goal_label.setStyleSheet(f"color:{U.TEXT};")
        self.meta_label.setStyleSheet(f"color:{color};")
        reason = str(session.get("pause_reason") or "").strip()
        self.pause_label.setVisible(bool(reason))
        self.pause_label.setText(f"Bekleme: {reason}" if reason else "")

        active = status in {"running", "paused", "takeover"}
        self.pause_button.setEnabled(active and (self._pause is not None or self._resume is not None))
        self.pause_button.setText("DEVAM ET" if status in {"paused", "takeover"} else "DURAKLAT")
        self.takeover_button.setEnabled(active and self._takeover is not None)
        self.export_button.setEnabled(True)
        self.learn_button.setEnabled(status == "completed")

        try:
            steps = self.manager.storage.get_task_steps(self._session_id, 400)
        except Exception as error:  # noqa: BLE001 - modal içinde kısa bildirim
            steps = []
            self.notice.setText(f"Adımlar okunamadı: {error}")
        self.set_subtitle(f"GÖREV #{self._session_id} · {len(steps)} ADIM")
        if not steps:
            self.list_layout.addWidget(EmptyState("Bu görevde henüz araç adımı yok."))
            return
        for row in steps[-80:]:
            self.list_layout.addWidget(self._step_card(row))
        if len(steps) > 80:
            self.list_layout.addWidget(
                hud_label(f"… ilk {len(steps) - 80} adım dışa aktarılan izde saklı", 6, U.FAINT, QFont.Weight.Medium, 0.6)
            )

    def _step_card(self, row: dict) -> QWidget:
        frame = card(padding=8)
        layout = frame.layout()
        status = str(row.get("status") or "ok")
        color = {"error": U.RED_BRIGHT, "note": U.GOLD}.get(status, U.TEXT_SOFT)
        mark = {"ok": "✓", "error": "✗", "note": "•"}.get(status, "•")
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(hud_label(f"{mark} {row.get('seq')}", 7, color, QFont.Weight.DemiBold, 0.6))
        head.addWidget(hud_label(str(row.get("tool") or "araç"), 8, U.TEXT, QFont.Weight.DemiBold, 0.4))
        head.addStretch(1)
        head.addWidget(hud_label(str(row.get("created_at") or "")[11:19], 6, U.FAINT, QFont.Weight.Medium, 0.4))
        layout.addLayout(head)
        args = str(row.get("args_summary") or "").strip()
        if args:
            layout.addWidget(hud_label(args[:300], 6, U.MUTED, QFont.Weight.Normal, 0.2))
        result = re.sub(r"\s+", " ", str(row.get("result") or "")).strip()
        if result:
            layout.addWidget(hud_label(result[:600], 7, U.TEXT_SOFT, QFont.Weight.Normal, 0.2))
        if row.get("screenshot"):
            layout.addWidget(hud_label(f"GÖRÜNTÜ · {row.get('screenshot')}", 6, U.FAINT, QFont.Weight.Medium, 0.2))
        return frame

    def _toggle_pause(self) -> None:
        if self._status in {"paused", "takeover"}:
            if self._resume is not None:
                self._resume()
                self.notice.setText("Devam isteği gönderildi.")
        elif self._pause is not None:
            self._pause("Görev kullanıcı tarafından duraklatıldı.")
            self.notice.setText("Duraklatma isteği gönderildi.")
        QTimer.singleShot(350, self._reload)

    def _do_takeover(self) -> None:
        if self._takeover is None:
            return
        self._takeover("Kullanıcı kontrolü görev panelinden devraldı.")
        self.notice.setText("Kontrol sende. Jarvis, DEVAM ET'e basana kadar araç çalıştırmaz.")
        QTimer.singleShot(350, self._reload)

    def _export(self) -> None:
        if self._session_id is None:
            return
        try:
            message = self.manager.export(self._session_id)
        except (TaskSessionError, OSError) as error:
            self.notice.setText(f"Dışa aktarılamadı: {error}")
            return
        self.notice.setText(message.replace("\n", " · "))

    def _learn_skill(self) -> None:
        if self._session_id is None:
            return
        session = self.manager.storage.get_task_session(self._session_id) or {}
        default = str(session.get("goal") or f"gorev-{self._session_id}")[:60]
        name, ok = QInputDialog.getText(self, "Yetenek adı", "Küçük harf, sayı ve tire:", text=default)
        if not ok or not name.strip():
            return
        description, ok = QInputDialog.getText(
            self, "Yetenek açıklaması", "Bu yetenek ne zaman kullanılmalı?", text=default
        )
        if not ok or not description.strip():
            return
        try:
            message = self.manager.build_skill(name, description, self._session_id)
        except (TaskSessionError, ValueError, OSError) as error:
            self.notice.setText(f"Yetenek oluşturulamadı: {error}")
            return
        self.notice.setText(message.replace("\n", " · "))


class MemoryModal(HudModal):
    """Kalıcı hafızadaki notları görüntüler, ekler ve siler."""

    def __init__(self, parent, storage: Storage) -> None:
        super().__init__(parent, "Kalıcı Hafıza", "JARVIS'İN SENİN HAKKINDA TUTTUĞU NOTLAR", width=820, height=680)
        self.storage = storage
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText(f"Başlık (en fazla {MAX_KEY} karakter)")
        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Not metni · örn. 'Kısa ve net yanıtları severim'")
        self.value_input.returnPressed.connect(self._save)
        save = HudButton("KAYDET", small=True, accent=True)
        save.clicked.connect(self._save)
        form = QHBoxLayout()
        form.setSpacing(7)
        form.addWidget(self.key_input, 1)
        form.addWidget(self.value_input, 2)
        form.addWidget(save)
        self.content.addLayout(form)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.content.addWidget(self.list_container)
        self.notice = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.8)
        self.notice.setWordWrap(True)
        self.content.addWidget(self.notice)
        self._reload()

    def _reload(self) -> None:
        try:
            rows = self.storage.get_all_memory()
        except Exception as error:  # noqa: BLE001 - modal içinde kısa bildirim
            rows = []
            self.notice.setText(f"Hafıza okunamadı: {error}")
        clear_layout(self.list_layout)
        if not rows:
            self.list_layout.addWidget(EmptyState("Hafıza boş. Jarvis'e 'hatırla: ...' diyerek not ekleyebilirsin."))
            self.set_subtitle("0 NOT")
            return
        for row in rows:
            self.list_layout.addWidget(self._memory_card(row))
        self.set_subtitle(f"{len(rows)} KALICI NOT · {MAX_NOTES} SINIR")

    def _memory_card(self, row: dict) -> QWidget:
        frame = card(padding=10)
        layout = frame.layout()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(hud_label(str(row.get("key") or "not"), 8, U.GOLD, QFont.Weight.DemiBold, 1.4))
        head.addWidget(hud_label(_role_time(row.get("updated_at")), 6, U.FAINT, QFont.Weight.Medium, 0.6))
        head.addStretch(1)
        delete = HudButton("SİL", small=True)
        delete.clicked.connect(lambda _=False, key=str(row.get("key") or ""): self._delete(key))
        head.addWidget(delete)
        layout.addLayout(head)
        value = hud_label(str(row.get("value") or ""), 9, U.TEXT_SOFT, QFont.Weight.Normal, 0.2)
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(value)
        return frame

    def _save(self) -> None:
        value = self.value_input.text().strip()
        if not value:
            self.notice.setText("Kaydedilecek not metni boş olamaz.")
            return
        if len(value) > MAX_VALUE:
            self.notice.setText(f"Not en fazla {MAX_VALUE} karakter olabilir.")
            return
        key = re.sub(r"\s+", " ", self.key_input.text()).strip()[:MAX_KEY]
        if not key:
            key = value[:50]
        try:
            existing = self.storage.get_all_memory()
            if len(existing) >= MAX_NOTES and self.storage.get_memory(key) is None:
                self.notice.setText(f"Hafıza sınırı dolu ({MAX_NOTES} not); önce bir not sil.")
                return
            self.storage.save_memory(key, value)
        except Exception as error:  # noqa: BLE001 - modal içinde kısa bildirim
            self.notice.setText(f"Not kaydedilemedi: {error}")
            return
        self.key_input.clear()
        self.value_input.clear()
        self.notice.setText(f"Kaydedildi: {key}")
        self._reload()

    def _delete(self, key: str) -> None:
        if not key:
            return
        try:
            self.storage.delete_memory(key)
        except Exception as error:  # noqa: BLE001 - modal içinde kısa bildirim
            self.notice.setText(f"Not silinemedi: {error}")
            return
        self.notice.setText(f"Silindi: {key}")
        self._reload()


class BriefingModal(HudModal):
    refresh_requested = Signal()

    def __init__(self, parent, text: str, speak: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent, "Günlük Brifing", "TAKVİM · HAVA · HATIRLATICI · NOTLAR", width=760, height=620)
        self._speak = speak
        self.body_label = hud_label(text, 11, U.TEXT_SOFT, QFont.Weight.Normal, 0.6)
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.content.addWidget(self.body_label)
        self.content.addSpacing(6)
        actions = QHBoxLayout()
        refresh = HudButton("YENİLE", small=True)
        refresh.clicked.connect(self.refresh_requested.emit)
        copy = HudButton("KOPYALA", small=True)
        copy.clicked.connect(self._copy)
        speak = HudButton("SESLİ OKU", small=True, accent=True)
        speak.clicked.connect(self._read_aloud)
        self.notice = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.8)
        actions.addWidget(self.notice, 1)
        actions.addWidget(refresh)
        actions.addWidget(copy)
        actions.addWidget(speak)
        self.content.addLayout(actions)

    def set_text(self, text: str) -> None:
        self.body_label.setText(text)
        self.notice.setText(f"Güncellendi · {datetime.now():%H:%M}")

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.body_label.text())
        self.notice.setText("Brifing panoya kopyalandı.")

    def _read_aloud(self) -> None:
        if self._speak is not None:
            self._speak(self.body_label.text())
            self.notice.setText("Sesli okuma başlatıldı.")


class CommandPalette(HudModal):
    """Ctrl+K ile açılan, Türkçe katlamalı komut arama penceresi."""

    def __init__(self, parent, commands: Sequence[dict]) -> None:
        super().__init__(parent, "Komut Paleti", "YAZARAK ARA · ↑↓ GEZİN · ENTER ÇALIŞTIR", width=720, height=560)
        self.commands = list(commands)
        self._visible: List[dict] = []
        self.input = QLineEdit()
        self.input.setPlaceholderText("Komut ara… örn. harita, hatırlat, pano, brifing")
        self.input.setFont(hud_font(11, QFont.Weight.Normal, 0.3))
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._refresh)
        self.content.addWidget(self.input)
        self.list = QListWidget()
        self.list.setFont(hud_font(10, QFont.Weight.Medium, 0.4))
        self.list.setMinimumHeight(300)
        self.list.setStyleSheet(
            f"""
            QListWidget {{
                background:{U.PANEL_2};
                border:1px solid {U.BORDER};
                border-radius:8px;
                color:{U.TEXT_SOFT};
                outline:0;
                padding:5px;
            }}
            QListWidget::item {{
                padding:10px 12px;
                border-radius:5px;
            }}
            QListWidget::item:selected {{
                background:{rgba_css(U.RED, 0.16)};
                color:{U.TEXT};
            }}
            """
        )
        self.list.itemActivated.connect(lambda item: self._run(self.list.row(item)))
        self.content.addWidget(self.list, 1)
        self.empty = EmptyState("Eşleşen komut yok. Farklı bir sözcük dene.")
        self.content.addWidget(self.empty)
        self.content.addWidget(Divider())
        hint = hud_label("CTRL+K · HIZLI ERİŞİM · ESC KAPAT", 7, U.FAINT, QFont.Weight.Medium, 1.4)
        self.content.addWidget(hint)
        self._refresh()

    def _refresh(self) -> None:
        query = self.input.text()
        self._visible = [
            command for command in self.commands
            if matches_query(
                query,
                command.get("label", ""),
                command.get("hint", ""),
                command.get("keywords", ""),
            )
        ]
        self.list.clear()
        for command in self._visible:
            label = str(command.get("label", "—"))
            hint = str(command.get("hint", "")).strip()
            item = QListWidgetItem(f"{label}   ·   {hint}" if hint else label)
            self.list.addItem(item)
        has_items = bool(self._visible)
        self.list.setVisible(has_items)
        self.empty.setVisible(not has_items)
        if has_items:
            self.list.setCurrentRow(0)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self._visible:
                    row = self.list.currentRow()
                    step = 1 if key == Qt.Key.Key_Down else -1
                    self.list.setCurrentRow((row + step) % len(self._visible))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run(self.list.currentRow() if self.list.currentRow() >= 0 else 0)
                return True
        return super().eventFilter(watched, event)

    def _run(self, index: int) -> None:
        if not (0 <= index < len(self._visible)):
            return
        action = self._visible[index].get("action")
        self.close_modal()
        if callable(action):
            QTimer.singleShot(0, action)
