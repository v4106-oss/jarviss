"""Uygulama içi tarayıcı; harita ile aynı tek QWebEngineProfile'ı kullanır."""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit

from app.theme import ultron as U
from .browser_service import BROWSER_AVAILABLE, browser_service
from .hud_modal import HudModal
from .hud_widgets import HudButton, hud_font, hud_label

SEARCH_URL = "https://duckduckgo.com/?q={}"
HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


class BrowserModal(HudModal):
    map_requested = Signal()

    def __init__(self, parent, width: int = 1180, height: int = 760) -> None:
        super().__init__(
            parent,
            "Tarayıcı",
            "JARVIS İÇ TARAYICI · TEK MOTOR",
            width=width,
            height=height,
            fade=False,
            scrollable=False,
            persistent=True,
        )
        self.view = None

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.back_button = HudButton("←", small=True)
        self.back_button.setToolTip("Geri")
        self.back_button.clicked.connect(lambda: self.view and self.view.back())
        self.forward_button = HudButton("→", small=True)
        self.forward_button.setToolTip("İleri")
        self.forward_button.clicked.connect(lambda: self.view and self.view.forward())
        self.reload_button = HudButton("YENİLE", small=True)
        self.reload_button.clicked.connect(lambda: self.view and self.view.reload())
        self.map_button = HudButton("HARİTA", small=True)
        self.map_button.setToolTip("Jarvis haritasını aç")
        self.map_button.clicked.connect(self.map_requested.emit)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Adres veya arama yaz…")
        self.address.setFont(hud_font(9, spacing=0.6))
        self.address.setMinimumHeight(30)
        self.address.setStyleSheet(
            f"""
            QLineEdit {{
                background:{U.PANEL};
                color:{U.TEXT};
                border:1px solid {U.BORDER};
                border-radius:6px;
                padding:4px 10px;
            }}
            QLineEdit:focus {{ border-color:{U.RED_SOFT}; }}
            """
        )
        self.address.returnPressed.connect(self._navigate)
        self.go_button = HudButton("GİT", accent=True, small=True)
        self.go_button.clicked.connect(self._navigate)
        toolbar.addWidget(self.back_button)
        toolbar.addWidget(self.forward_button)
        toolbar.addWidget(self.reload_button)
        toolbar.addWidget(self.map_button)
        toolbar.addWidget(self.address, 1)
        toolbar.addWidget(self.go_button)
        self.content.addLayout(toolbar)

        self.status_label = hud_label("TARAYICI HAZIR", 7, U.MUTED, spacing=1.4)
        self.content.addWidget(self.status_label)

        if not BROWSER_AVAILABLE:
            self.status_label.setText("QT WEBENGINE BULUNAMADI")
            self.address.setEnabled(False)
            self.go_button.setEnabled(False)
            return
        try:
            self.view = browser_service().create_view(self.body)
        except Exception as error:
            self.status_label.setText(f"TARAYICI BAŞLATILAMADI: {error}")
            return
        self.view.setMinimumHeight(430)
        self.view.titleChanged.connect(self._on_title)
        self.view.urlChanged.connect(self._on_url)
        self.view.loadStarted.connect(lambda: self.status_label.setText("YÜKLENİYOR…"))
        self.view.loadFinished.connect(self._on_load_finished)
        self.content.addWidget(self.view, 1)
        self._update_nav()

    def _navigate(self) -> None:
        self.open_url(self.address.text())

    @staticmethod
    def normalize_url(raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return ""
        host = value.split("/", 1)[0]
        looks_like_host = "." in host or ":" in host or host.casefold() == "localhost"
        if " " in value or not looks_like_host:
            return SEARCH_URL.format(quote_plus(value))
        if not HTTP_RE.match(value):
            prefix = "http://" if host.casefold().startswith(("localhost", "127.0.0.1")) else "https://"
            value = prefix + value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return value

    def open_url(self, raw: str) -> None:
        target = self.normalize_url(raw)
        if not target:
            self.status_label.setText("GEÇERSİZ ADRES")
            return
        if self.view is None:
            return
        self.address.setText(target)
        self.view.setUrl(QUrl(target))

    def focus_url(self) -> None:
        self.address.setFocus()
        self.address.selectAll()

    def _on_title(self, title: str) -> None:
        text = str(title or "").strip()
        if text:
            self.set_subtitle(text[:90])

    def _on_url(self, url: QUrl) -> None:
        value = url.toString()
        if value and value != "about:blank":
            self.address.setText(value)
        self._update_nav()

    def _on_load_finished(self, ok: bool) -> None:
        self.status_label.setText("SAYFA HAZIR" if ok else "SAYFA YÜKLENEMEDİ")
        self._update_nav()

    def _update_nav(self) -> None:
        if self.view is None:
            return
        try:
            history = self.view.page().history()
            self.back_button.setEnabled(history.canGoBack())
            self.forward_button.setEnabled(history.canGoForward())
        except Exception:
            pass

    def reopen(self) -> None:
        self._closed = False
