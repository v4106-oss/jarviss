from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QScrollArea,
)
from PySide6.QtCore import Qt, QTimer

from .message import Message


class ChatWidget(QWidget):
    def __init__(self):
        super().__init__()

        self._thinking_message = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)

        self.container = QWidget()

        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(8)
        self.layout.addStretch()

        self.scroll.setWidget(self.container)

        main_layout.addWidget(self.scroll)

    def add_message(self, text, is_user=False):
        msg = Message(text, is_user)

        self.layout.insertWidget(self.layout.count() - 1, msg)

        if text == "🤖 Düşünüyorum...":
            self._thinking_message = msg

        self.scroll_to_bottom()
        return msg

    def update_message(self, msg, text):
        msg.set_text(text)
        if self._thinking_message is msg:
            self._thinking_message = None
        self.scroll_to_bottom()

    def remove_thinking(self):
        if not self._thinking_message:
            return

        self._thinking_message.hide()
        self.layout.removeWidget(self._thinking_message)
        self._thinking_message.setParent(None)
        self._thinking_message.deleteLater()
        self._thinking_message = None

        self.scroll_to_bottom()

    def clear(self):
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        self._thinking_message = None
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        QTimer.singleShot(
            10,
            lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            ),
        )