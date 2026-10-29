from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton


class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.parent_window = parent
        self.drag_pos = QPoint()

        self.setFixedHeight(45)

        self.setStyleSheet("""
        QWidget{
            background:#25262b;
            border-bottom:1px solid #3b3d44;
        }

        QLabel{
            color:white;
            font-size:15px;
            font-weight:bold;
        }

        QPushButton{
            background:transparent;
            color:white;
            border:none;
            min-width:40px;
            min-height:35px;
            font-size:15px;
        }

        QPushButton:hover{
            background:#3b3d44;
            border-radius:8px;
        }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("🤖 Jarvis")

        self.min_btn = QPushButton("—")
        self.max_btn = QPushButton("□")
        self.close_btn = QPushButton("✕")

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        self.min_btn.clicked.connect(self.minimize_window)
        self.max_btn.clicked.connect(self.maximize_restore)
        self.close_btn.clicked.connect(self.close_window)

    def minimize_window(self):
        if self.parent_window:
            self.parent_window.showMinimized()

    def maximize_restore(self):
        if not self.parent_window:
            return

        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
        else:
            self.parent_window.showMaximized()

    def close_window(self):
        if self.parent_window:
            self.parent_window.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.parent_window and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self.drag_pos
            self.parent_window.move(self.parent_window.pos() + delta)
            self.drag_pos = event.globalPosition().toPoint()