"""Yerel tanılama ve gizlilik raporunu gösteren HUD penceresi."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPlainTextEdit

from app.theme import ultron as U
from app.theme.themes import rgba_css
from .hud_modal import HudModal
from .hud_widgets import HudButton, hud_font, hud_label


class DiagnosticsModal(HudModal):
    def __init__(
        self,
        parent,
        text: str,
        refresh: Optional[Callable[[], str]] = None,
        data_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            parent,
            "Tanılama ve Gizlilik",
            "YEREL RAPOR · API ANAHTARI VE TOKEN YAZILMAZ",
            width=880,
            height=720,
        )
        self._refresh = refresh
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlainText(text)
        self.editor.setFont(hud_font(9, QFont.Weight.Normal, 0.2))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.editor.setMinimumHeight(430)
        self.editor.setStyleSheet(
            f"""
            QPlainTextEdit {{
                background:{U.PANEL_2};
                color:{U.TEXT_SOFT};
                border:1px solid {U.BORDER};
                border-radius:8px;
                padding:10px;
                selection-background-color:{rgba_css(U.RED, 0.35)};
            }}
            """
        )
        self.content.addWidget(self.editor, 1)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.notice = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.6)
        self.notice.setWordWrap(True)
        copy = HudButton("KOPYALA", small=True, accent=True)
        copy.clicked.connect(self._copy)
        refresh = HudButton("YENİLE", small=True)
        refresh.clicked.connect(self._reload)
        refresh.setEnabled(self._refresh is not None)
        folder = HudButton("VERİ KLASÖRÜ", small=True)
        folder.clicked.connect(self._open_folder)
        folder.setEnabled(self._data_dir is not None)
        actions.addWidget(self.notice, 1)
        actions.addWidget(folder)
        actions.addWidget(refresh)
        actions.addWidget(copy)
        self.content.addLayout(actions)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())
        self.notice.setText("Rapor panoya kopyalandı; paylaşmadan önce gözden geçir.")

    def _reload(self) -> None:
        if self._refresh is None:
            return
        try:
            self.editor.setPlainText(self._refresh())
            self.notice.setText("Rapor yenilendi.")
        except Exception as error:  # noqa: BLE001 - modal içinde kısa bildirim
            self.notice.setText(f"Rapor yenilenemedi: {error}")

    def _open_folder(self) -> None:
        if self._data_dir is None:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._data_dir)))
