"""Jarvis genel stil sayfası; siyah ve beyaz paletler için yeniden üretilebilir."""

from types import SimpleNamespace

from app.theme import ultron as U
from app.theme.themes import rgba_css


def build_qss(palette: dict = None) -> str:
    p = SimpleNamespace(**palette) if palette else U
    accent_wash = rgba_css(p.RED, 0.14 if not p.IS_LIGHT else 0.08)
    accent_soft = rgba_css(p.RED, 0.10 if not p.IS_LIGHT else 0.06)
    return f"""
QWidget {{
    background: transparent;
    color: {p.TEXT};
    font-family: "{p.FONT}";
    font-size: 12px;
}}
QMainWindow, QDialog {{
    background: {p.BACKGROUND};
}}
QToolTip {{
    background: {p.PANEL_2};
    color: {p.TEXT};
    border: 1px solid {p.BORDER};
    border-radius: 8px;
    padding: 6px 10px;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox {{
    background: {p.INPUT};
    border: 1px solid {p.BORDER};
    border-radius: 10px;
    padding: 8px 12px;
    color: {p.TEXT};
    selection-background-color: {p.RED_SOFT};
    selection-color: {"white" if not p.IS_LIGHT else "white"};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus, QComboBox:focus {{
    border-color: {p.RED};
    background: {p.PANEL_2};
}}
QLineEdit::placeholder {{
    color: {p.DIM};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {p.PANEL_2};
    color: {p.TEXT};
    border: 1px solid {p.BORDER};
    selection-background-color: {p.RED_DIM};
    outline: none;
}}
QPushButton {{
    background: transparent;
    color: {p.TEXT_SOFT};
    border: 1px solid {p.BORDER};
    border-radius: 10px;
    padding: 8px 15px;
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {p.RED_SOFT};
    color: {p.RED_BRIGHT};
    background: {accent_soft};
}}
QPushButton:pressed {{
    background: {accent_wash};
}}
QPushButton:disabled {{
    color: {p.FAINT};
    border-color: {p.BORDER};
}}
QCheckBox, QRadioButton {{
    color: {p.TEXT_SOFT};
    spacing: 8px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {p.BORDER};
    border-radius: 3px;
    background: {p.INPUT};
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.RED_SOFT};
    border-color: {p.RED};
}}
QTabWidget::pane {{
    border: 1px solid {p.BORDER};
    border-radius: 12px;
    background: {p.PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {p.MUTED};
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    padding: 9px 18px;
    margin-right: 2px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {p.TEXT};
    background: {accent_soft};
}}
QTabBar::tab:selected {{
    color: {p.TEXT};
    background: {p.PANEL};
    border-color: {p.BORDER};
    border-bottom: 2px solid {p.RED};
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}}
QGroupBox {{
    border: 1px solid {p.BORDER};
    border-radius: 12px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    color: {p.TEXT_SOFT};
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {p.RED_BRIGHT};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.FAINT};
    min-height: 24px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.RED_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {p.FAINT};
    min-width: 24px;
    border-radius: 4px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QListWidget {{
    background: transparent;
    border: none;
    color: {p.TEXT_SOFT};
    outline: none;
}}
QListWidget::item {{
    padding: 8px 6px;
    border-bottom: 1px solid {p.BORDER};
}}
QListWidget::item:selected {{
    background: {accent_soft};
    color: {p.TEXT};
}}
QMessageBox {{
    background: {p.BACKGROUND};
}}
QMessageBox QLabel {{
    color: {p.TEXT};
}}
QCalendarWidget QWidget {{
    background: {p.PANEL_2};
    color: {p.TEXT};
}}
QCalendarWidget QAbstractItemView:enabled {{
    background: {p.PANEL};
    color: {p.TEXT_SOFT};
    selection-background-color: {p.RED_DIM};
    selection-color: {"white" if not p.IS_LIGHT else "white"};
}}
QDialogButtonBox QPushButton {{
    min-width: 86px;
    border-color: {p.BORDER_RED};
}}
QScrollArea > QWidget > QWidget, QScrollArea QWidget#qt_scrollarea_viewport {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 1px 0;
}}
QScrollBar::handle:vertical {{
    background: {rgba_css(p.FAINT, 0.85)};
    min-height: 30px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: {rgba_css(p.RED_SOFT, 0.9)};
}}
QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 2px 8px;
}}
QPushButton:focus, QCheckBox:focus, QRadioButton:focus {{
    outline: none;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {rgba_css(p.RED, 0.75)};
}}
"""


GLOBAL_QSS = build_qss()
