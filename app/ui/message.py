from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QSizePolicy,
)


class Message(QWidget):
    def __init__(self, text, is_user=False):
        super().__init__()

        self.is_user = is_user

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)

        self.bubble = QFrame()

        if is_user:
            self.bubble.setStyleSheet("""
            QFrame{
                background:#5865F2;
                border-radius:14px;
            }

            QLabel{
                color:white;
                font-size:14px;
            }
            """)
        else:
            self.bubble.setStyleSheet("""
            QFrame{
                background:#313338;
                border-radius:14px;
            }

            QLabel{
                color:white;
                font-size:14px;
            }
            """)

        self.bubble.setSizePolicy(
            QSizePolicy.Maximum,
            QSizePolicy.Preferred
        )

        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 10)

        self.label = QLabel(text)
        self.label.setWordWrap(True)

        bubble_layout.addWidget(self.label)

        if is_user:
            outer.addStretch()
            outer.addWidget(self.bubble)
        else:
            outer.addWidget(self.bubble)
            outer.addStretch()

    def set_text(self, text: str):
        self.label.setText(text)