from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QPushButton,
    QLabel,
)


class Sidebar(QWidget):
    def __init__(self):
        super().__init__()

        self.setFixedWidth(220)

        self.setStyleSheet("""
        QWidget{
            background:#25262b;
            border-right:1px solid #3b3d44;
        }

        QLabel{
            color:white;
            font-size:20px;
            font-weight:bold;
            padding:15px 10px;
        }

        QPushButton{
            background:#5865F2;
            border:none;
            border-radius:10px;
            color:white;
            font-size:14px;
            font-weight:bold;
            padding:12px;
        }

        QPushButton:hover{
            background:#7289da;
        }

        QPushButton:pressed{
            background:#4752c4;
        }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.title = QLabel("🤖 Jarvis")

        self.new_chat = QPushButton("+ Yeni Sohbet")
        self.new_chat.setCursor(Qt.PointingHandCursor)

        self.settings = QPushButton("⚙ Yapay Zekâ Ayarları")
        self.settings.setCursor(Qt.PointingHandCursor)

        layout.addWidget(self.title)
        layout.addWidget(self.new_chat)
        layout.addWidget(self.settings)
        layout.addStretch()
