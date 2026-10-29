"""Jarvis konuşma günlüğü: akış dostu baloncuklar ve hafif Markdown."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Dict, Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.theme import ultron as U
from app.theme.themes import rgba_css
from .animations import pop_in
from .hud_widgets import hud_font, hud_label
from .shell_parts import GlyphButton, GlyphIcon, draw_glyph

_STREAM_FLUSH_MS = 45
_RICH_TEXT_LIMIT = 24_000
_CODE_BLOCK_RE = re.compile(r"```[ \t]*([\w+#.-]*)[ \t]*\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def render_markdown(text: str) -> str:
    """AI yanıtını QLabel'in anlayacağı güvenli zengin metne çevirir."""
    raw = str(text or "")
    if not raw.strip():
        return ""
    if len(raw) > _RICH_TEXT_LIMIT:
        return html.escape(raw).replace("\n", "<br>")

    blocks: list[str] = []
    inlines: list[str] = []

    def stash_block(match: re.Match) -> str:
        language = (match.group(1) or "").strip()
        code = html.escape(match.group(2).strip("\n"))
        label = (
            f'<div style="color:{U.DIM};font-family:{U.MONO};font-size:8px;'
            f'letter-spacing:1px;margin-bottom:2px;">{html.escape(language.upper())}</div>'
            if language
            else ""
        )
        blocks.append(
            f'<div style="background-color:{rgba_css(U.TEXT, 0.055)};'
            f'border:1px solid {rgba_css(U.TEXT, 0.08)};border-radius:8px;'
            f'padding:7px 9px;margin:3px 0;">{label}'
            f'<span style="font-family:{U.MONO};font-size:9px;color:{U.TEXT_SOFT};">'
            f"{code}</span></div>"
        )
        return f"\x00B{len(blocks) - 1}\x00"

    working = _CODE_BLOCK_RE.sub(stash_block, raw)
    working = html.escape(working)

    def stash_inline(match: re.Match) -> str:
        inlines.append(
            f'<span style="background-color:{rgba_css(U.TEXT, 0.07)};'
            f'font-family:{U.MONO};color:{U.TEXT};padding:0 3px;border-radius:3px;">'
            f"{match.group(1)}</span>"
        )
        return f"\x00I{len(inlines) - 1}\x00"

    working = _INLINE_CODE_RE.sub(stash_inline, working)
    working = _LINK_RE.sub(
        lambda match: (
            f'<a href="{match.group(2)}" style="color:{U.RED_BRIGHT};'
            f'text-decoration:none;">{match.group(1)}</a>'
        ),
        working,
    )
    working = _BOLD_RE.sub(r"<b>\1</b>", working)
    working = _ITALIC_RE.sub(r"<i>\1</i>", working)

    lines = []
    for line in working.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            lines.append(
                f'<div style="color:{U.TEXT};font-weight:600;font-size:10px;'
                f'letter-spacing:0.3px;margin:2px 0;">{heading}</div>'
            )
            continue
        if stripped.startswith(("- ", "* ", "• ")):
            lines.append(
                f'<div style="margin-left:4px;"><span style="color:{U.RED_SOFT};">•</span> '
                f"{stripped[2:].strip()}</div>"
            )
            continue
        lines.append(line)
    working = "<br>".join(lines)

    for index, block in enumerate(blocks):
        working = working.replace(f"\x00B{index}\x00", block)
    for index, inline in enumerate(inlines):
        working = working.replace(f"\x00I{index}\x00", inline)
    return working


class _Avatar(QWidget):
    def __init__(self, is_user: bool, size: int = 30, parent=None) -> None:
        super().__init__(parent)
        self._is_user = is_user
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._size = size

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.6, 0.6, -0.6, -0.6)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        accent = QColor(U.RED if self._is_user else U.GOLD)
        deep = QColor(U.PANEL_2)
        gradient.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 62))
        gradient.setColorAt(1.0, deep)
        painter.setBrush(gradient)
        painter.setPen(QColor(accent.red(), accent.green(), accent.blue(), 90))
        painter.drawEllipse(rect)
        pad = self._size * 0.28
        draw_glyph(
            painter,
            "user" if self._is_user else "spark",
            QRectF(pad, pad, self._size - pad * 2, self._size - pad * 2),
            QColor(U.RED_BRIGHT if self._is_user else U.GOLD),
            1.5,
        )
        painter.end()


class ChatEntry(QWidget):
    """Tek bir mesaj baloncuğu; akış sırasında hafif, bitince zengin metin."""

    def __init__(self, text: str, is_user: bool, parent=None) -> None:
        super().__init__(parent)
        self.is_user = is_user
        self._raw = str(text or "")
        self._rich = False
        self._streaming = False
        self._caret_on = True
        self._expanded = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(8)

        self.avatar = _Avatar(is_user)
        self.bubble = QFrame()
        self.bubble.setObjectName("chatBubble")
        self.bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.bubble.setStyleSheet(self._bubble_qss())
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(12, 8, 12, 9)
        bubble_layout.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(7)
        accent = U.RED_BRIGHT if is_user else U.GOLD
        self.sender = hud_label("Sen" if is_user else "Jarvis", 7, accent, QFont.Weight.Medium, 0.2)
        dot = QLabel("●")
        dot.setFont(hud_font(6, QFont.Weight.DemiBold, 0.0))
        dot.setStyleSheet(f"color:{accent};background:transparent;border:none;")
        self.time_label = hud_label(datetime.now().strftime("%H:%M"), 7, U.FAINT, QFont.Weight.Medium, 0.6)
        self.copy_button = GlyphButton("copy", "Mesajı kopyala", 22)
        self.copy_button.clicked.connect(self._copy)
        self.copy_button.hide()
        top.addWidget(dot)
        top.addWidget(self.sender)
        top.addWidget(self.time_label)
        top.addStretch(1)
        top.addWidget(self.copy_button)
        bubble_layout.addLayout(top)

        self.label = QLabel("")
        self.label.setFont(hud_font(10, QFont.Weight.Normal, 0.15))
        self.label.setStyleSheet(f"color:{U.TEXT_SOFT};background:transparent;border:none;")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.label.setOpenExternalLinks(True)
        self.label.setMinimumWidth(150)
        bubble_layout.addWidget(self.label)

        if is_user:
            outer.addStretch(1)
            outer.addWidget(self.bubble)
            outer.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        else:
            outer.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
            outer.addWidget(self.bubble)
            outer.addStretch(1)

        self._caret_timer = QTimer(self)
        self._caret_timer.setInterval(620)
        self._caret_timer.timeout.connect(self._blink)
        self.set_text(text, rich=False)

    def _bubble_qss(self) -> str:
        if self.is_user:
            return f"""
            QFrame#chatBubble {{
                background:{rgba_css(U.RED, 0.07 if not U.IS_LIGHT else 0.05)};
                border:1px solid {rgba_css(U.RED, 0.14)};
                border-radius:14px;
            }}
            """
        return f"""
        QFrame#chatBubble {{
            background:{rgba_css(U.TEXT, 0.028)};
            border:1px solid {rgba_css(U.TEXT, 0.06)};
            border-radius:14px;
        }}
        """

    def _apply_width(self) -> None:
        self.bubble.setMaximumWidth(1180 if self._expanded else 800)

    def _display_text(self) -> str:
        if not self._raw:
            return "…"
        if self._rich:
            rendered = render_markdown(self._raw)
        else:
            rendered = self._raw
        if self._streaming and not self._rich:
            return rendered + ("  ▌" if self._caret_on else "")
        return rendered

    def _refresh_text(self) -> None:
        if self._rich and not self._streaming:
            self.label.setTextFormat(Qt.TextFormat.RichText)
            self.label.setText(render_markdown(self._raw) or "…")
        else:
            self.label.setTextFormat(Qt.TextFormat.PlainText)
            self.label.setText(self._display_text())

    def set_text(self, text: str, rich: bool = False) -> None:
        self._raw = str(text or "")
        self._rich = bool(rich)
        self._refresh_text()

    def set_sender(self, text: str) -> None:
        self.sender.setText(text)
        streaming = "YAZIYOR" in text.upper()
        if streaming != self._streaming:
            self.set_streaming(streaming)

    def set_streaming(self, streaming: bool) -> None:
        streaming = bool(streaming)
        if streaming == self._streaming:
            return
        self._streaming = streaming
        if streaming:
            self._caret_timer.start()
        else:
            self._caret_timer.stop()
            self._caret_on = True
        self._refresh_text()

    def _blink(self) -> None:
        if not self._streaming:
            self._caret_timer.stop()
            return
        self._caret_on = not self._caret_on
        self._refresh_text()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._apply_width()
        self.label.setFont(hud_font(11 if expanded else 10, QFont.Weight.Normal, 0.15))

    def enterEvent(self, event):
        self.copy_button.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.copy_button.hide()
        super().leaveEvent(event)

    def _copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._raw)
        self.copy_button.set_glyph("check")
        QTimer.singleShot(1300, lambda: self.copy_button.set_glyph("copy"))

    def is_streaming(self) -> bool:
        return self._streaming


class _DayDivider(QWidget):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(30, 4, 30, 4)
        layout.setSpacing(10)
        left = QFrame()
        left.setFixedHeight(1)
        left.setStyleSheet(f"background:{rgba_css(U.TEXT, 0.05)};border:none;")
        right = QFrame()
        right.setFixedHeight(1)
        right.setStyleSheet(left.styleSheet())
        label = hud_label(text, 6, U.FAINT, QFont.Weight.Medium, 0.3)
        layout.addWidget(left, 1)
        layout.addWidget(label)
        layout.addWidget(right, 1)


class _EmptyState(QWidget):
    suggested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.addStretch(1)
        panel = QWidget()
        panel.setMaximumWidth(560)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(9)

        mark = QHBoxLayout()
        mark.addStretch(1)
        mark.addWidget(GlyphIcon("spark", 26, U.RED_SOFT))
        mark.addStretch(1)
        layout.addLayout(mark)
        title = hud_label("Komuta hazır", 10, U.TEXT_SOFT, QFont.Weight.Medium, 0.3)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title)
        hint = hud_label(
            "Yaz, mikrofonla konuş ya da aşağıdaki önerilerden başla.\n"
            "Jarvis ekranı, kamerayı ve sistemi tek akışta kullanabilir.",
            8,
            U.DIM,
            QFont.Weight.Normal,
            0.2,
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addStretch(1)
        for text in ("Bugün neler var?", "Kamerayı aç ve beni tara", "Ekranımda ne var?", "Haritayı aç"):
            chip = QPushButton(text)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFont(hud_font(7, QFont.Weight.Medium, 0.2))
            chip.setStyleSheet(
                f"""
                QPushButton {{
                    background:{rgba_css(U.TEXT, 0.028)};
                    color:{U.TEXT_SOFT};
                    border:1px solid {U.BORDER};
                    border-radius:13px;
                    padding:5px 12px;
                }}
                QPushButton:hover {{
                    color:{U.RED_BRIGHT};
                    border-color:{rgba_css(U.RED, 0.35)};
                    background:{rgba_css(U.RED, 0.07)};
                }}
                """
            )
            chip.clicked.connect(lambda _=False, value=text: self.suggested.emit(value))
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.addLayout(chips)
        outer.addWidget(panel)
        outer.addStretch(1)


class HudChat(QWidget):
    suggested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background:transparent;border:none;")
        self.scroll.viewport().setStyleSheet("background:transparent;")
        self.container = QWidget()
        self.container.setStyleSheet("background:transparent;")
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(10, 6, 14, 6)
        self.layout.setSpacing(8)
        self._empty = _EmptyState()
        self._empty.suggested.connect(self.suggested.emit)
        self.layout.addWidget(self._empty)
        self.layout.addStretch(1)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

        self._expanded = False
        self._last_day: Optional[str] = None
        self._pending: Dict[ChatEntry, str] = {}
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(_STREAM_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_pending)

    def _insert_before_stretch(self, widget: QWidget) -> None:
        self.layout.insertWidget(max(0, self.layout.count() - 1), widget)

    def add_message(self, text: str, is_user: bool = False) -> ChatEntry:
        self._flush_pending()
        today = datetime.now().strftime("%d.%m.%Y")
        if self._last_day != today:
            label = "Bugün" if self._last_day is None else today
            self._insert_before_stretch(_DayDivider(label))
            self._last_day = today
        entry = ChatEntry(text, is_user)
        entry.set_expanded(self._expanded)
        self._insert_before_stretch(entry)
        pop_in(entry)
        self._empty.hide()
        self.scroll_to_bottom()
        return entry

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        if expanded:
            self.layout.setContentsMargins(22, 12, 24, 10)
        else:
            self.layout.setContentsMargins(10, 6, 14, 6)
        for index in range(self.layout.count()):
            widget = self.layout.itemAt(index).widget()
            if isinstance(widget, ChatEntry):
                widget.set_expanded(expanded)
        self.scroll_to_bottom()

    def update_message(self, entry: ChatEntry, text: str, rich: bool = False) -> None:
        try:
            entry.objectName()
        except RuntimeError:
            return
        if rich:
            self._pending.pop(entry, None)
            try:
                entry.set_text(text, rich=True)
            except RuntimeError:
                return
            self.scroll_to_bottom()
            return
        self._pending[entry] = str(text or "")
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for entry, text in pending.items():
            try:
                entry.set_text(text, rich=False)
            except RuntimeError:
                continue
        self.scroll_to_bottom()

    def clear(self) -> None:
        self._flush_timer.stop()
        self._pending.clear()
        for index in range(self.layout.count() - 1, -1, -1):
            item = self.layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None or widget is self._empty:
                continue
            self.layout.takeAt(index)
            widget.setParent(None)
            widget.deleteLater()
        self._last_day = None
        self._empty.show()
        self.scroll_to_bottom()

    def count(self) -> int:
        total = 0
        for index in range(self.layout.count()):
            widget = self.layout.itemAt(index).widget()
            if isinstance(widget, ChatEntry):
                total += 1
        return total

    def scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            16,
            lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            ),
        )
