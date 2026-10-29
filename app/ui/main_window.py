"""Jarvis ULTRON HUD ana penceresi."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    QAbstractAnimation,
    QDateTime,
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPixmap,
    QRadialGradient,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from app.core.companion import CompanionPlanner
from app.core.diagnostics import build_report
from app.core.home_tools import FOAMS, SUGARS, TONES, SmartHome, home_voice_intent
from app.core.hud_data import (
    HudStore,
    SystemSampler,
    TimerEngine,
    WeatherService,
    local_moment,
    turkish_date,
)
from app.core.ai import AI
from app.core.personal_tools import (
    ClipboardHistory,
    SystemControls,
    build_briefing_text,
)
from app.core.settings import Settings
from app.core.speech import SpeechService, spoken_limit_for_prompt
from app.core.storage import Storage
from app.core.task_session import TaskSessionManager
from app.core.voice import ConversationWorker
from app.core.worker import AIWorker
from app.theme import ultron as U
from app.theme.themes import THEME_ORDER, THEMES, normalize_theme, rgba_css
from .animations import (
    BusyPill,
    fade_window_in,
    fade_window_out,
    play_launch_effect,
)
from .extra_modals import (
    BriefingModal,
    ChatHistoryModal,
    ClipboardModal,
    CommandPalette,
    MemoryModal,
    RemindersModal,
    TaskSessionModal,
    TaskTraceModal,
)
from .home_modal import HomeModal
from .hud_chat import ChatEntry, HudChat
from .hud_modal import HudModal
from .hud_modals import (
    CalendarModal,
    ModelsModal,
    NotesModal,
    SystemModal,
    TimeModal,
    TimerModal,
)
from .floating_hud import FloatingHud
from .hud_theme import GLOBAL_QSS
from .hud_weather import MapModal, WeatherModal
from .browser_modal import BrowserModal
from .camera_hub import CameraHub
from .diagnostics_modal import DiagnosticsModal
from .hud_widgets import (
    ClickableFrame,
    Divider,
    EmptyState,
    HudButton,
    MiniMap,
    RingGauge,
    SectionTitle,
    SparkBars,
    TempRangeBar,
    WeatherIcon,
    clear_layout,
    hud_font,
    hud_label,
    set_label_color,
)
from .news_stage import NewsStage, news_stage_bridge
from .orb import OrbWidget, state_color
from .settings_dialog import SettingsDialog
from .shell_parts import (
    BrandMark,
    CommandPill,
    ComposerFrame,
    GlyphButton,
    GlyphIcon,
    HudCard,
    MicLevelMeter,
    NavButton,
    PulseDot,
    SectionLabel,
    TextChip,
)

STATE_LABELS = {
    "idle": "Hazır",
    "listening": "Dinliyor",
    "thinking": "Düşünüyor",
    "speaking": "Konuşuyor",
}

RAIL_WIDTH = 78
LEFT_WIDTH = 288
RIGHT_WIDTH = 330
HEADER_HEIGHT = 66
FOOTER_HEIGHT = 30

STATE_HINTS = {
    "idle_off": "Mikrofon kapalı · Konuşmak için mikrofon düğmesine ya da F4'e bas.",
    "idle_on": "Mikrofon açık · Konuşmanı bekliyorum.",
    "listening": "Dinliyorum… Konuşman bitince göndereceğim.",
    "thinking": "Düşünüyorum… Görevi adım adım yürütüyorum.",
    "speaking": "Sesli yanıt veriyorum…",
}

_ORPHAN_THREADS: List[QThread] = []


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _smoothstep(value: float) -> float:
    t = _clamp01(value)
    return t * t * (3.0 - 2.0 * t)


class FocusCenterLayout(QLayout):
    """Orb ve sohbeti normal yerleşimden odak yerleşimine kademeli taşır."""

    NORMAL_CONVERSATION_MIN = 330
    NORMAL_CONVERSATION_MAX = 400

    def __init__(self, orb: QWidget, conversation: QWidget, parent=None) -> None:
        super().__init__(parent)
        self._orb = orb
        self._conversation = conversation
        self._items = [QWidgetItem(orb), QWidgetItem(conversation)]
        self.addChildWidget(orb)
        self.addChildWidget(conversation)
        self._focus = 0.0
        self._normal_conversation_height = 330
        self.setContentsMargins(10, 2, 10, 2)
        self.setSpacing(0)

    def set_focus(self, value: float) -> None:
        self._focus = _clamp01(float(value))
        self.invalidate()

    def capture_normal_height(self) -> int:
        height = max(
            self._conversation.sizeHint().height(),
            self._conversation.minimumSizeHint().height(),
            self.NORMAL_CONVERSATION_MIN,
        )
        self._normal_conversation_height = int(
            min(height, self.NORMAL_CONVERSATION_MAX)
        )
        return self._normal_conversation_height

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def addItem(self, item) -> None:
        self.addChildWidget(item.widget())

    def sizeHint(self) -> QSize:
        return QSize(760, 600)

    def minimumSize(self) -> QSize:
        return QSize(360, 420)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)

        margins = self.contentsMargins()
        inner = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        width = max(1, inner.width())
        height = max(1, inner.height())

        conv_height = min(max(self.NORMAL_CONVERSATION_MIN, self._normal_conversation_height), height)
        top_height = max(1, height - conv_height)
        normal_size = max(300, min(width, top_height) - 12)
        normal_orb = QRect(
            inner.left() + (width - normal_size) // 2,
            inner.top() + (top_height - normal_size) // 2,
            normal_size,
            normal_size,
        )
        normal_conversation = QRect(inner.left(), inner.top() + top_height, width, conv_height)

        focus_width = max(380, min(round(width * 0.34), 500))
        focus_height = max(focus_width, height - 24)
        focus_orb = QRect(
            inner.left() + 16,
            inner.top() + 12,
            focus_width,
            focus_height,
        )
        gap = max(20, int(width * 0.012))
        chat_left = focus_orb.right() + gap
        focus_conversation = QRect(
            chat_left,
            inner.top() + 8,
            max(240, inner.right() - chat_left + 1),
            max(220, height - 16),
        )

        t = self._focus
        orb_slide = _smoothstep((t - 0.24) / 0.38)
        orb_drop = _smoothstep((t - 0.62) / 0.38)
        chat_slide = _smoothstep((t - 0.42) / 0.24)
        chat_grow = _smoothstep((t - 0.66) / 0.34)

        self._orb.setGeometry(
            QRect(
                round(normal_orb.x() + (focus_orb.x() - normal_orb.x()) * orb_slide),
                round(normal_orb.y() + (focus_orb.y() - normal_orb.y()) * orb_drop),
                round(normal_orb.width() + (focus_orb.width() - normal_orb.width()) * orb_slide),
                # Yükseklik, sohbet yukarı açılmadan büyürse iki alan kesişir; dikey büyüme son faza bırakılır.
                round(normal_orb.height() + (focus_orb.height() - normal_orb.height()) * orb_drop),
            )
        )
        self._conversation.setGeometry(
            QRect(
                round(normal_conversation.x() + (focus_conversation.x() - normal_conversation.x()) * chat_slide),
                round(normal_conversation.y() + (focus_conversation.y() - normal_conversation.y()) * chat_grow),
                round(normal_conversation.width() + (focus_conversation.width() - normal_conversation.width()) * chat_slide),
                round(normal_conversation.height() + (focus_conversation.height() - normal_conversation.height()) * chat_grow),
            )
        )


def _fold_turkish(text: str) -> str:
    folded = str(text or "").casefold()
    for source, target in (
        ("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"),
        ("ö", "o"), ("ç", "c"), ("â", "a"), ("î", "i"), ("û", "u"),
    ):
        folded = folded.replace(source, target)
    return re.sub(r"[^a-z0-9 ]+", " ", folded).strip()


def camera_voice_intent(text: str) -> Optional[str]:
    """Sesli kamera komutunu yerel sınıflandırır; AI isteğine dönüşmez."""
    normalized = _fold_turkish(text)
    if not normalized:
        return None
    words = normalized.split()

    def negative(word: str, stem: str) -> bool:
        return word == f"{stem}ma" or word.startswith((f"{stem}may", f"{stem}mam"))

    hand_request = any(
        root in normalized
        for root in (
            "el kontrol", "eller", "elleri", "ellerim", "elim", "elimle",
            "jest", "el hareket", "el takip", "el iskelet",
        )
    )
    if hand_request:
        if any(connector in f" {normalized} " for connector in (" ve ", " sonra ", " ardindan ")):
            return None
        off_negative = any(negative(word, "kapat") or negative(word, "durdur") for word in words)
        on_negative = any(negative(word, "ac") or negative(word, "baslat") for word in words)
        if "devre disi" in normalized or (
            any(word.startswith(("kapat", "durdur")) or word == "kapa" for word in words)
            and not off_negative
        ):
            return "hands_off"
        if any(word.startswith(("ac", "baslat", "kullan", "aktif")) for word in words) and not on_negative:
            return "hands_on"
        return None
    analyze_phrases = (
        "ne goruyorsun", "kamerada ne var", "kameradan ne goruyorsun",
        "etrafi analiz", "cevreyi analiz", "ortami analiz", "etrafi tara",
        "cevreyi tara", "ortami tara", "arkamdakileri analiz", "arkami analiz",
        "beni analiz", "etrafi incele", "cevreyi incele", "beni goruyor musun",
    )
    if any(phrase in normalized for phrase in analyze_phrases):
        return "analyze"
    if "beni" in words and any(
        word.startswith(("tara", "incele", "analiz")) for word in words
    ):
        return "analyze"
    if not any(root in normalized for root in ("kamera", "kamere")):
        return None
    book_request = any(root in normalized for root in ("kitap", "kitab", "isbn"))
    if not book_request and any(word.startswith("tara") for word in words):
        return "analyze"
    if not book_request and any(word.startswith(("analiz", "incele")) for word in words):
        return "analyze"
    if any(word.startswith("gizle") for word in words):
        return "hide"
    if any(word.startswith(("pencere", "panel")) for word in words) and any(
        word.startswith(("kapat", "gizle")) for word in words
    ):
        return "hide"
    if any("ekran" in word for word in words) and any(
        word.startswith(("cikar", "kucult")) for word in words
    ):
        return "hide"
    if not book_request and (
        any(phrase in normalized for phrase in ("tam ekran", "buyuk ekran"))
        or any(word.startswith("buyut") for word in words)
    ):
        return "fullscreen"

    if any(
        (word.startswith("kapat") and not negative(word, "kapat"))
        or (word.startswith("durdur") and not negative(word, "durdur"))
        or word == "kapa"
        for word in words
    ):
        return "off"
    if any(
        (
            word.startswith("ac")
            and not word.startswith(("acik", "acaba", "acikl"))
            and not negative(word, "ac")
        )
        or word.startswith(("goster", "baslat", "gorun"))
        for word in words
    ):
        return "show"
    return None


def screen_voice_intent(text: str) -> Optional[str]:
    """Yalnızca açık ekran bağlamı komutlarını yakalar; soruları AI'ya bırakır."""
    normalized = _fold_turkish(text)
    if not normalized or len(normalized.split()) > 10:
        return None
    if not any(
        phrase in normalized for phrase in ("ekran baglam", "ekran izle", "ekran takip")
    ):
        return None
    words = normalized.split()
    negative = any(
        word.startswith(("kapatma", "durdurma", "acma", "baslatma"))
        for word in words
    )
    if negative:
        return None
    if any(word.startswith(("kapat", "durdur", "birak")) for word in words):
        return "off"
    if any(word.startswith(("ac", "baslat", "kullan", "aktif", "izle")) for word in words):
        return "on"
    return None


_UI_INTENT_PHRASES = (
    ("map", ("haritayi ac", "haritayi goster", "harita panelini ac", "haritayi getir", "harita ekranini ac")),
    ("browser", ("tarayiciyi ac", "tarayici ac", "tarayiciyi goster", "web tarayicisini ac", "browser ac")),
    ("settings", ("ayarlari ac", "ayarlari goster", "ayarlar ekranini ac", "ayar panelini ac")),
    ("models", ("modelleri ac", "model secimini ac", "model ayarlarini ac", "modelleri goster")),
    ("commands", ("komut paletini ac",)),
    ("history", ("sohbet gecmisini ac",)),
    ("trace", ("gorev gunlugunu ac", "gorev gecmisini ac", "ajan gunlugunu ac", "islem gecmisini goster")),
    ("session", ("gorev oturumunu ac", "gorev oturumunu goster", "oturum panelini ac", "gorev adimlarini ac")),
    ("memory", ("hafizayi ac", "hafiza yoneticisini ac", "kalici hafizayi goster", "hafizami goster")),
    ("clipboard", ("pano merkezini ac", "panoyu ac")),
    ("reminders", ("hatirlaticilari ac", "hatirlaticilari goster", "hatirlatici panelini ac")),
    ("briefing", ("brifingi ac", "brifingi goster")),
    ("time", ("saat panelini ac",)),
    ("system", ("sistem panelini ac", "sistem durumunu goster")),
    ("timer", ("zamanlayiciyi ac", "zamanlayici panelini ac")),
    ("notes", ("notlar panelini ac", "notlarimi goster", "notlari ac")),
    ("calendar", ("takvimi ac", "takvim panelini ac")),
    ("weather", ("hava durumunu ac", "hava durumu panelini ac")),
    ("book", ("kitap taramayi ac", "kitap tarama panelini ac", "kitap kamerasi")),
    ("home", ("ev panelini ac", "akilli evi ac", "akilli ev panelini ac", "evi yonet")),
)

_UI_INTENT_REPLIES = {
    "map": "Harita açıldı.",
    "browser": "Tarayıcı açıldı.",
    "settings": "Ayarlar açıldı.",
    "models": "Model seçimi açıldı.",
    "commands": "Komut paleti açıldı.",
    "history": "Sohbet geçmişi açıldı.",
    "trace": "Görev günlüğü açıldı.",
    "session": "Görev oturumu açıldı.",
    "memory": "Kalıcı hafıza açıldı.",
    "clipboard": "Pano merkezi açıldı.",
    "reminders": "Hatırlatıcılar açıldı.",
    "briefing": "Brifing açıldı.",
    "time": "Saat paneli açıldı.",
    "system": "Sistem paneli açıldı.",
    "timer": "Zamanlayıcı açıldı.",
    "notes": "Notlar açıldı.",
    "calendar": "Takvim açıldı.",
    "weather": "Hava durumu açıldı.",
    "book": "Kitap tarama açıldı.",
    "home": "Akıllı ev paneli açıldı.",
}


def local_ui_intent(text: str) -> Optional[str]:
    """Doğrudan panel açma komutlarını AI'ya gitmeden sınıflandırır."""
    normalized = _fold_turkish(text)
    if not normalized or len(normalized.split()) > 10:
        return None
    padded = f" {normalized} "
    if any(connector in padded for connector in (" ve ", " sonra ", " ardindan ", " ile ")):
        return None
    for intent, phrases in _UI_INTENT_PHRASES:
        if any(phrase in normalized for phrase in phrases):
            return intent
    return None


def camera_context_needed(text: str) -> bool:
    """Yalnızca görsel sorularda canlı kare ekler; diğer mesajları hızlandırır."""
    folded = _fold_turkish(text)
    if not folded:
        return False
    if "ne var" in folded:
        return True
    roots = (
        "kamera", "kamere", "goruntu", "foto", "resim", "ekran", "etraf",
        "cevre", "onum", "elim", "uzerim", "bak", "goruyor", "gordun",
        "gorebiliyor", "okuy",
    )
    return any(word == "oku" or word.startswith(roots) for word in folded.split())


def screen_context_needed(text: str) -> bool:
    """Ekranla ilgili sorularda tek seferlik ekran görüntüsü bağlamı ister."""
    folded = _fold_turkish(text)
    if not folded:
        return False
    if any(
        phrase in folded
        for phrase in ("tam ekran", "buyuk ekran", "ekran karti", "ekran cozunurluk", "ekran parlak", "ekrani kilitle")
    ):
        return False
    words = folded.split()
    if any(word.startswith(("ekran", "pencere", "sekme", "masaustu", "imlec")) for word in words):
        return True
    return "uygulama" in words and any(
        word.startswith(("acik", "onum", "calis", "goster", "gordun")) for word in words
    )


class _VoiceBridge(QObject):
    """Ses sinyallerini arayüz iş parçacığına taşır.

    Düz lambda'nın QObject alıcı bağlamı yok; işçi iş parçacığından gelen
    kuyruklu çağrı arayüze ulaşmayabiliyordu.
    """

    def __init__(self, window: "MainWindow", token: int, worker, thread) -> None:
        super().__init__(window)
        self._window = window
        self._token = token
        self._worker = worker
        self._thread = thread

    @Slot(str)
    def heard(self, text: str) -> None:
        self._window._on_voice_result(text, self._token)

    @Slot(str)
    def error(self, text: str) -> None:
        self._window._on_voice_error(text, self._token)

    @Slot(float)
    def level(self, value: float) -> None:
        self._window._on_voice_level(value, self._token)

    @Slot()
    def finished(self) -> None:
        self._window._on_voice_finished(self._worker, self._thread)


class MainWindow(QWidget):
    weather_ready = Signal(dict)
    weather_failed = Signal(str)
    system_sampled = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Jarvis")
        width, height = 1600, 960
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, max(900, available.width()))
            height = min(height, max(640, available.height()))
        self.resize(width, height)
        self.setMinimumSize(min(1280, width), min(760, height))

        self.storage = Storage()
        self.settings = Settings()
        self.camera_hub = CameraHub(self)
        self.camera_hub.set_device_index(self.settings.camera_index)
        self.camera_hub.active_changed.connect(lambda _active: self._update_camera_style())
        self.camera_window = None
        self._camera_ai: Optional[AI] = None
        self._camera_perception = False
        self._camera_autostart_scheduled = False
        self._camera_manual_off = False
        self._screen_context = bool(self.settings.screen_context)
        self._screen_tools = None
        self._voice_scene_pending = False
        self._voice_scene_tries = 0
        self.hud_store = HudStore()
        self.companion = CompanionPlanner(self.settings.data_dir / "companion.json")
        self.smart_home = SmartHome()
        self.timer_engine = TimerEngine(self)
        self.system_sampler = SystemSampler()
        self.weather_service = WeatherService(self.settings)
        self.speech = SpeechService(self.settings, self)
        self.clipboard_history = ClipboardHistory(limit=30)
        self.system_controls = SystemControls()
        self._last_clipboard = ""

        self.attached_files: List[str] = []
        self.worker: Optional[AIWorker] = None
        self.thread: Optional[QThread] = None
        self.request_active = False
        self.current_streaming_message: Optional[ChatEntry] = None
        self._stream_text = ""
        self._cancel_requested = False
        self._task_takeover = False
        self._stale_workers: List[Tuple[QThread, AIWorker]] = []
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog)

        self.mic_enabled = False
        self.speak_replies = bool(self.settings.voice_replies)
        self.voice_thread: Optional[QThread] = None
        self.voice_worker: Optional[ConversationWorker] = None
        self._voice_bridge: Optional[_VoiceBridge] = None
        self._voice_session = 0
        self._no_speech_count = 0
        self._pending_voice_text: Optional[str] = None
        self._voice_speech_limit: Optional[int] = None

        self.weather: Optional[dict] = None
        self._weather_ready_once = False
        self._weather_stop = threading.Event()
        self._weather_wake = threading.Event()
        self._weather_lock = threading.Lock()
        self._monitors_started = False
        self._monitors_scheduled = False
        self._browser_prewarmed = False

        self._system_snapshot: Optional[dict] = None
        self._system_stop = threading.Event()

        self._modal: Optional[HudModal] = None
        self._map_modal: Optional[MapModal] = None
        self._browser_modal: Optional[BrowserModal] = None
        self._toast_token = 0
        self._toast_anim = None
        self._launch_effect_played = False
        self._launch_overlay = None
        self._dark_title_set = False
        self._state = "idle"
        self._closing = False
        self._close_animation_started = False
        self._restore_maximized = False
        self._cpu_history: List[float] = []

        self._chat_focus = 0.0
        self._focus_expanded = False
        self._focus_animation: Optional[QVariantAnimation] = None
        self._side_width = RIGHT_WIDTH
        self._sidebar_effects: List[QGraphicsOpacityEffect] = []
        self._ambient_enabled = True
        self._background_cache: Optional[QPixmap] = None

        self.weather_ready.connect(self._on_weather)
        self.weather_failed.connect(self._on_weather_error)
        self.system_sampled.connect(self._on_system)

        self._build_window()
        self.news_stage = NewsStage(
            self, is_busy=lambda: self.request_active or self.speech.busy
        )
        self.news_stage.hide()
        news_stage_bridge().items_ready.connect(self._on_news_items)
        self._wire_events()
        self._create_floating_hud()
        self._tick_clock()

    # ---------- görünüm ----------

    def _build_window(self) -> None:
        self._set_window_icon()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(GLOBAL_QSS)
        else:
            self.setStyleSheet(GLOBAL_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav_rail = self._build_nav()
        self.left_panel = self._build_left()
        self.center_panel = self._build_center()
        self.right_panel = self._build_right()
        body.addWidget(self.nav_rail)
        body.addWidget(self.left_panel)
        body.addWidget(self.center_panel, 1)
        body.addWidget(self.right_panel)
        root.addLayout(body, 1)
        root.addWidget(self._build_footer())

        self.busy = BusyPill(self)
        self.busy.hide()

        self.toast = QLabel(self)
        self.toast.setFont(hud_font(8, QFont.Weight.DemiBold, 0.9))
        self.toast.setStyleSheet(
            f"""
            background:{U.PANEL_2};
            color:{U.TEXT};
            border:1px solid {rgba_css(U.RED, 0.42)};
            border-radius:10px;
            padding:9px 16px;
            """
        )
        self.toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toast_effect = QGraphicsOpacityEffect(self.toast)
        self._toast_effect.setOpacity(1.0)
        self.toast.setGraphicsEffect(self._toast_effect)
        self.toast.hide()

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("topDeck")
        header.setFixedHeight(HEADER_HEIGHT)
        header.setStyleSheet(
            f"""
            QFrame#topDeck {{
                background:{rgba_css(U.PANEL, 0.74)};
                border:none;
                border-bottom:1px solid {U.BORDER};
            }}
            """
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 7, 14, 7)
        layout.setSpacing(9)

        self.brand_mark = BrandMark(38)
        layout.addWidget(self.brand_mark)
        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)
        title = hud_label("Jarvis", 14, U.TEXT, QFont.Weight.DemiBold, 0.2)
        subtitle = hud_label("Masaüstü asistanı", 7, U.MUTED, QFont.Weight.Medium, 0.2)
        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)
        layout.addWidget(brand)
        layout.addSpacing(8)

        self.command_pill = CommandPill()
        self.command_pill.clicked.connect(self.open_command_palette)
        layout.addWidget(self.command_pill, 1)

        self.mic_status = TextChip("Mikrofon", "Mikrofonu aç/kapat (F4)")
        self.mic_status.clicked.connect(self.toggle_mic)
        self.mic_meter = MicLevelMeter()
        self.camera_status = TextChip(
            "Kamera",
            "Kamera algısını ve HUD hedeflerini aç/kapat. "
            "Sesli: 'beni tara', 'kamerayı aç/kapat', 'kamerayı analiz et'.",
        )
        self.camera_status.clicked.connect(self.toggle_camera_perception)
        self.screen_status = TextChip(
            "Ekran",
            "Ekran bağlamı: her komuta güncel ekran görüntüsünü ekler. "
            "Ekran görüntüsü seçili model sağlayıcısına gönderilir.",
        )
        self.screen_status.clicked.connect(self.toggle_screen_context)
        self.speak_status = TextChip(
            "Sesli yanıt",
            "Yazılı komutlarda da sesli yanıt verir. Tıklayınca tüm sesli yanıtlar kapanır.",
        )
        self.speak_status.clicked.connect(self.toggle_voice_replies)
        layout.addWidget(self.mic_status)
        layout.addWidget(self.mic_meter)
        layout.addWidget(self.speak_status)
        layout.addWidget(self.camera_status)
        layout.addWidget(self.screen_status)
        layout.addWidget(self._vline(18))

        self.briefing_action = self._icon_action("bell", "Günlük brifing (Ctrl+B)", self.open_briefing)
        self.palette_action = self._icon_action("search", "Komut paleti (Ctrl+K)", self.open_command_palette)
        self.browser_action = self._icon_action(
            "globe", "Jarvis'in kendi tarayıcısı · tek Chromium motoru", self.open_browser
        )
        self.models_action = self._icon_action("spark", "AI modelleri", self.open_models)
        self.theme_action = self._icon_action("layers", "Tema değiştir", self._cycle_theme)
        self.settings_action = self._icon_action("settings", "Ayarlar", self.open_settings)
        for action in (
            self.briefing_action,
            self.palette_action,
            self.browser_action,
            self.models_action,
            self.theme_action,
            self.settings_action,
        ):
            layout.addWidget(action)

        layout.addWidget(self._vline(18))
        clock_box = QWidget()
        clock_layout = QVBoxLayout(clock_box)
        clock_layout.setContentsMargins(2, 0, 2, 0)
        clock_layout.setSpacing(0)
        self.header_clock = hud_label("--:--", 9, U.TEXT, QFont.Weight.DemiBold, 1.6)
        self.header_date = hud_label("", 5, U.MUTED, QFont.Weight.Medium, 1.0)
        self.header_clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.header_date.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        clock_layout.addWidget(self.header_clock)
        clock_layout.addWidget(self.header_date)
        layout.addWidget(clock_box)

        self.power_action = GlyphButton("power", "Jarvis'i kapat", 30)
        self.power_action.clicked.connect(self.close)
        layout.addWidget(self.power_action)

        self._update_mic_style()
        self._update_speak_style()
        self._update_camera_style()
        self._update_screen_style()
        return header

    def _vline(self, height: int = 20) -> QFrame:
        line = QFrame()
        line.setFixedSize(1, height)
        line.setStyleSheet(f"background:{U.BORDER};border:none;")
        return line

    def _icon_action(self, glyph: str, tooltip: str, slot) -> GlyphButton:
        button = GlyphButton(glyph, tooltip, 30)
        button.clicked.connect(slot)
        return button

    def _build_nav(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("navRail")
        rail.setFixedWidth(RAIL_WIDTH)
        rail.setStyleSheet(
            f"""
            QFrame#navRail {{
                background:transparent;
                border:none;
                border-right:1px solid {U.BORDER};
            }}
            """
        )
        outer = QVBoxLayout(rail)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addSpacing(10)

        # Kısa ekranlarda sekmeler kırpılmasın; ray tekerlekle kaydırılabilir.
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;border:none;")
        scroll.viewport().setStyleSheet("background:transparent;")
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(8, 2, 8, 6)
        layout.setSpacing(4)

        self.nav_chat = NavButton("chat", "Sohbet", "Sohbet odağı (F2)")
        self.nav_chat.clicked.connect(self.toggle_chat_focus)
        self.nav_camera = NavButton("camera", "Kamera", "Kamera algısı")
        self.nav_camera.clicked.connect(self.toggle_camera_perception)
        self.nav_map = NavButton("map", "Harita", "Haritayı aç")
        self.nav_map.clicked.connect(self.open_map)
        self.nav_browser = NavButton("browser", "Tarayıcı", "Jarvis tarayıcısını aç")
        self.nav_browser.clicked.connect(self.open_browser)
        self.nav_weather = NavButton("globe", "Hava", "Hava durumu")
        self.nav_weather.clicked.connect(self.open_weather)
        self.nav_book = NavButton("book", "Kitap", "Kitap tarama")
        self.nav_book.clicked.connect(self.open_book_camera)
        self.nav_calendar = NavButton("clock", "Gün", "Takvim")
        self.nav_calendar.clicked.connect(self.open_calendar)
        self.nav_reminders = NavButton("bell", "Hatırlat", "Hatırlatıcılar")
        self.nav_reminders.clicked.connect(self.open_reminders)
        self.nav_briefing = NavButton("spark", "Brifing", "Günlük brifing")
        self.nav_briefing.clicked.connect(self.open_briefing)
        self.nav_settings = NavButton("settings", "Ayarlar", "Ayarlar")
        self.nav_settings.clicked.connect(self.open_settings)
        for button in (
            self.nav_chat,
            self.nav_camera,
            self.nav_map,
            self.nav_browser,
            self.nav_weather,
            self.nav_book,
            self.nav_calendar,
            self.nav_reminders,
            self.nav_briefing,
            self.nav_settings,
        ):
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(8, 4, 8, 10)
        bottom_layout.setSpacing(5)
        bottom_layout.addWidget(self._vline(1), 0, Qt.AlignmentFlag.AlignHCenter)
        self.rail_mic = GlyphButton("mic", "Mikrofonu aç/kapat (F4)", 40, checkable=True)
        self.rail_mic.clicked.connect(self.toggle_mic)
        self.rail_power = GlyphButton("power", "Jarvis'i kapat", 40)
        self.rail_power.clicked.connect(self.close)
        bottom_layout.addWidget(self.rail_mic, 0, Qt.AlignmentFlag.AlignHCenter)
        bottom_layout.addWidget(self.rail_power, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(bottom)
        return rail

    def _build_left(self) -> QWidget:
        left = QFrame()
        left.setObjectName("leftDock")
        left.setFixedWidth(LEFT_WIDTH)
        left.setStyleSheet(
            f"""
            QFrame#leftDock {{
                background:transparent;
                border:none;
                border-right:1px solid {U.BORDER};
            }}
            """
        )
        outer = QVBoxLayout(left)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;border:none;")
        scroll.viewport().setStyleSheet("background:transparent;")
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout.addWidget(SectionLabel("Çekirdek", "Canlı"))

        clock_card = HudCard("Yerel saat", "Bugün", "clock", clickable=True)
        clock_card.clicked.connect(self.open_time)
        self.left_time = hud_label("--:--", 30, U.TEXT, QFont.Weight.DemiBold, 0.5)
        self.left_time.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        clock_card.body.addWidget(self.left_time)
        self.left_date = hud_label("", 7, U.TEXT_SOFT, QFont.Weight.Medium, 0.2)
        self.left_date.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.left_date.setWordWrap(True)
        clock_card.body.addWidget(self.left_date)
        world_link = hud_label("Dünya saatleri  →", 7, U.MUTED, QFont.Weight.Medium, 0.2)
        world_link.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        world_link.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        clock_card.body.addWidget(world_link)
        layout.addWidget(clock_card)

        system_card = HudCard("Sistem", "Canlı", "grid", clickable=True)
        system_card.clicked.connect(self.open_system)
        system_row = QHBoxLayout()
        system_row.setContentsMargins(0, 0, 0, 0)
        system_row.setSpacing(9)
        self.cpu_gauge = RingGauge(0, "CPU", diameter=82)
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(2)
        self.ram_label = hud_label("RAM  %—", 7, U.TEXT_SOFT, QFont.Weight.Medium, 0.2)
        self.disk_label = hud_label("Disk  %— dolu", 7, U.TEXT_SOFT, QFont.Weight.Medium, 0.2)
        self.cores_label = hud_label("— çekirdek", 7, U.DIM, QFont.Weight.Medium, 0.2)
        for label in (self.ram_label, self.disk_label, self.cores_label):
            label.setWordWrap(True)
        self.cpu_spark = SparkBars()
        info.addWidget(self.ram_label)
        info.addWidget(self.disk_label)
        info.addWidget(self.cores_label)
        info.addWidget(self.cpu_spark)
        system_row.addWidget(self.cpu_gauge)
        system_row.addLayout(info, 1)
        system_card.body.addLayout(system_row)
        layout.addWidget(system_card)

        timer_card = HudCard("", "", "", clickable=True)
        timer_card.clicked.connect(self.open_timer)
        self.timer_section = SectionTitle("Zamanlayıcı", "")
        timer_card.body.addWidget(self.timer_section)
        self.timer_hint = hud_label("Hızlı süre seç (dk)", 7, U.DIM, QFont.Weight.Medium, 0.2)
        timer_card.body.addWidget(self.timer_hint)
        quick = QHBoxLayout()
        quick.setSpacing(4)
        for minutes in (1, 5, 10, 25):
            button = HudButton(f"{minutes}", small=True)
            button.setToolTip(f"{minutes} dakika")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _=False, value=minutes: self._quick_timer(value))
            quick.addWidget(button)
        timer_card.body.addLayout(quick)
        stopwatch_button = HudButton("Kronometre", small=True)
        stopwatch_button.clicked.connect(self.open_timer)
        timer_card.body.addWidget(stopwatch_button)
        self.left_timers = QWidget()
        self.left_timers_layout = QVBoxLayout(self.left_timers)
        self.left_timers_layout.setContentsMargins(0, 2, 0, 0)
        self.left_timers_layout.setSpacing(2)
        timer_card.body.addWidget(self.left_timers)
        layout.addWidget(timer_card)

        notes_card = HudCard("", "", "", clickable=True)
        notes_card.clicked.connect(self.open_notes)
        self.notes_section = SectionTitle("Notlar", "0 not")
        notes_card.body.addWidget(self.notes_section)
        self.note_preview = hud_label(
            "Henüz not yok. “Not al: …” de ya da aşağıya yaz.", 7, U.DIM, QFont.Weight.Medium, 0.2
        )
        self.note_preview.setWordWrap(True)
        notes_card.body.addWidget(self.note_preview)
        self.quick_note = QLineEdit()
        self.quick_note.setPlaceholderText("Not ekle…")
        self.quick_note.setFont(hud_font(8, QFont.Weight.Normal, 0.3))
        self.quick_note.setFixedHeight(30)
        self.quick_note.setStyleSheet(
            f"""
            QLineEdit {{
                background:{rgba_css(U.TEXT, 0.035)};
                border:1px solid {U.BORDER};
                border-radius:8px;
                padding:3px 9px;
                color:{U.TEXT_SOFT};
            }}
            QLineEdit:focus {{ border-color:{rgba_css(U.RED, 0.5)}; background:{U.INPUT}; }}
            """
        )
        self.quick_note.returnPressed.connect(self._quick_note)
        notes_card.body.addWidget(self.quick_note)
        layout.addWidget(notes_card)

        layout.addStretch(1)
        self._refresh_notes_panel()
        self._refresh_timer_panel()
        return left

    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setStyleSheet("background:transparent;")

        self.orb = OrbWidget(center)
        self.conversation = QWidget(center)
        self.conversation.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(self.conversation)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(7)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(10, 0, 10, 0)
        status_row.setSpacing(9)
        self.state_dot = PulseDot(U.RED_SOFT, 10)
        self.status_label = hud_label(
            STATE_HINTS["idle_off"], 8, U.TEXT_SOFT, QFont.Weight.Medium, 0.2
        )
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.state_dot, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)

        self.progress_label = hud_label("", 7, U.RED_SOFT, QFont.Weight.Medium, 0.2)
        self.progress_label.setContentsMargins(10, 0, 10, 0)
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        self.chat = HudChat()
        self.chat.setMinimumHeight(160)
        self.chat.setMaximumHeight(240)
        self.chat.suggested.connect(self._on_chat_suggestion)
        layout.addWidget(self.chat, 1)

        self.composer = ComposerFrame()
        self.composer.setObjectName("composer")
        self.composer.setStyleSheet(
            f"""
            QFrame#composer {{
                background:{rgba_css(U.PANEL, 0.86)};
                border:1px solid {rgba_css(U.TEXT, 0.10)};
                border-radius:16px;
            }}
            QFrame#composer:hover {{
                border-color:{rgba_css(U.TEXT, 0.16)};
            }}
            """
        )
        compose = QVBoxLayout(self.composer)
        compose.setContentsMargins(10, 8, 8, 8)
        compose.setSpacing(5)

        bar_layout = QHBoxLayout()
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(6)

        attach = GlyphButton("plus", "Dosya ekle", 30)
        attach.clicked.connect(self.open_file)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Jarvis'e yaz ya da konuş…")
        self.input.setFont(hud_font(10, QFont.Weight.Normal, 0.2))
        self.input.setStyleSheet(
            f"""
            QLineEdit {{
                background:transparent;
                border:none;
                padding:4px 6px;
                color:{U.TEXT};
                selection-background-color:{U.RED_SOFT};
            }}
            QLineEdit:focus {{ border:none; }}
            """
        )
        self.input.returnPressed.connect(self.send_message)
        self.composer.track(self.input)
        self.mic_button = GlyphButton("mic", "Mikrofonu aç/kapat (F4)", 32, checkable=True)
        self.mic_button.clicked.connect(self.toggle_mic)
        self.pause_button = GlyphButton("pause", "Görevi duraklat", 32)
        self.pause_button.clicked.connect(self.toggle_task_pause)
        self.pause_button.hide()
        self.stop_button = GlyphButton("stop", "İsteği durdur (Esc)", 32)
        self.stop_button.clicked.connect(self.cancel_current_request)
        self.stop_button.hide()
        self.send_button = GlyphButton("send", "Gönder (Enter)", 34, accent=True)
        self.send_button.clicked.connect(self.send_message)

        bar_layout.addWidget(attach)
        bar_layout.addWidget(self.input, 1)
        bar_layout.addWidget(self.mic_button)
        bar_layout.addWidget(self.pause_button)
        bar_layout.addWidget(self.stop_button)
        bar_layout.addWidget(self.send_button)
        compose.addLayout(bar_layout)

        self.attachment_label = hud_label("", 6, U.GOLD, QFont.Weight.DemiBold, 0.8)
        self.attachment_label.setContentsMargins(6, 0, 6, 2)
        self.attachment_label.hide()
        compose.addWidget(self.attachment_label)
        layout.addWidget(self.composer)

        hint = QHBoxLayout()
        hint.setContentsMargins(8, 0, 6, 0)
        hint.setSpacing(8)
        hint.addWidget(
            hud_label(
                "Enter gönder · F4 mikrofon · Ctrl+K komut · Ctrl+Alt+V pano · Esc durdur",
                6,
                U.FAINT,
                QFont.Weight.Medium,
                0.1,
            )
        )
        hint.addStretch(1)
        self.focus_button = HudButton("Sohbeti genişlet", small=True)
        self.focus_button.setFixedHeight(24)
        self.focus_button.setToolTip("Sohbet alanını genişlet/küçült (F2)")
        self.focus_button.setStyleSheet(
            f"""
            QPushButton {{
                background:transparent;
                color:{U.MUTED};
                border:1px solid {U.BORDER};
                border-radius:11px;
                padding:2px 9px;
                font-size:7px;
                letter-spacing:0.2px;
            }}
            QPushButton:hover {{
                color:{U.TEXT};
                border-color:{rgba_css(U.TEXT, 0.18)};
                background:{rgba_css(U.TEXT, 0.04)};
            }}
            """
        )
        self.focus_button.clicked.connect(self.toggle_chat_focus)
        hint.addWidget(self.focus_button)
        layout.addLayout(hint)

        self.center_layout = FocusCenterLayout(self.orb, self.conversation, center)
        return center

    def _build_right(self) -> QWidget:
        right = QFrame()
        right.setObjectName("rightDock")
        right.setFixedWidth(RIGHT_WIDTH)
        right.setStyleSheet(
            f"""
            QFrame#rightDock {{
                background:transparent;
                border:none;
                border-left:1px solid {U.BORDER};
            }}
            """
        )
        outer = QVBoxLayout(right)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;border:none;")
        scroll.viewport().setStyleSheet("background:transparent;")
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout.addWidget(SectionLabel("Bağlam", "Canlı"))

        self.weather_card = HudCard("Hava durumu", "Open-Meteo", "globe", clickable=True)
        weather_layout = self.weather_card.body
        weather_top = QHBoxLayout()
        weather_top.setSpacing(8)
        self.weather_location = hud_label("Derince", 8, U.TEXT_SOFT, QFont.Weight.Medium, 0.2)
        self.weather_icon = WeatherIcon("cloud", U.GOLD, 34)
        weather_top.addWidget(self.weather_location)
        weather_top.addStretch(1)
        weather_top.addWidget(self.weather_icon)
        weather_layout.addLayout(weather_top)
        temp_row = QHBoxLayout()
        temp_row.setSpacing(8)
        self.weather_temp = hud_label("—°", 28, U.TEXT, QFont.Weight.DemiBold, 0.6)
        self.weather_desc = hud_label("veri yükleniyor…", 8, U.MUTED, QFont.Weight.Medium, 0.4)
        self.weather_desc.setWordWrap(True)
        temp_row.addWidget(self.weather_temp)
        temp_row.addWidget(self.weather_desc, 1)
        weather_layout.addLayout(temp_row)
        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        self.weather_low = hud_label("—°", 7, U.DIM, QFont.Weight.Medium, 0.4)
        self.weather_range = TempRangeBar()
        self.weather_high = hud_label("—°", 7, U.DIM, QFont.Weight.Medium, 0.4)
        range_row.addWidget(self.weather_low)
        range_row.addWidget(self.weather_range, 1)
        range_row.addWidget(self.weather_high)
        weather_layout.addLayout(range_row)
        self.weather_detail = hud_label(
            "Hissedilen — · Nem %—\nRüzgar — km/s", 7, U.DIM, QFont.Weight.Medium, 0.2
        )
        self.weather_detail.setWordWrap(True)
        weather_layout.addWidget(self.weather_detail)
        self.weather_card.clicked.connect(self.open_weather)
        layout.addWidget(self.weather_card)

        agenda_card = HudCard("Gündem", "Bugün", "clock")
        calendar_block = ClickableFrame()
        calendar_layout = QVBoxLayout(calendar_block)
        calendar_layout.setContentsMargins(0, 0, 0, 0)
        calendar_layout.setSpacing(3)
        self.calendar_section = SectionTitle("Takvim", "0 bugün")
        calendar_layout.addWidget(self.calendar_section)
        calendar_count_row = QHBoxLayout()
        self.calendar_count = hud_label("0", 20, U.TEXT, QFont.Weight.DemiBold, 1.0)
        self.calendar_count_note = hud_label("bugün", 7, U.GOLD, QFont.Weight.Medium, 0.2)
        calendar_count_row.addWidget(self.calendar_count)
        calendar_count_row.addWidget(self.calendar_count_note)
        calendar_count_row.addStretch(1)
        calendar_layout.addLayout(calendar_count_row)
        self.calendar_message = hud_label(
            "Bugün takviminde etkinlik yok.", 7, U.DIM, QFont.Weight.Medium, 0.4
        )
        self.calendar_message.setWordWrap(True)
        calendar_layout.addWidget(self.calendar_message)
        calendar_block.clicked.connect(self.open_calendar)
        agenda_card.body.addWidget(calendar_block)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background:{rgba_css(U.TEXT, 0.06)};border:none;")
        agenda_card.body.addWidget(divider)

        reminders_block = ClickableFrame()
        reminders_layout = QVBoxLayout(reminders_block)
        reminders_layout.setContentsMargins(0, 0, 0, 0)
        reminders_layout.setSpacing(3)
        self.reminders_section = SectionTitle("Hatırlatıcılar", "0 açık")
        reminders_layout.addWidget(self.reminders_section)
        reminders_count_row = QHBoxLayout()
        self.reminders_count = hud_label("0", 20, U.TEXT, QFont.Weight.DemiBold, 1.0)
        self.reminders_note = hud_label("açık", 7, U.GOLD, QFont.Weight.Medium, 0.2)
        reminders_count_row.addWidget(self.reminders_count)
        reminders_count_row.addWidget(self.reminders_note)
        reminders_count_row.addStretch(1)
        reminders_layout.addLayout(reminders_count_row)
        self.reminders_message = hud_label(
            "Bekleyen hatırlatıcı yok.", 7, U.DIM, QFont.Weight.Medium, 0.4
        )
        self.reminders_message.setWordWrap(True)
        reminders_layout.addWidget(self.reminders_message)
        reminders_block.clicked.connect(self.open_reminders)
        agenda_card.body.addWidget(reminders_block)
        layout.addWidget(agenda_card)

        book_card = HudCard("Kitap tarayıcı", "Kamera", "book", clickable=True)
        book_hint = hud_label(
            "Kapak/ISBN tara · sayfa OCR · kişisel özet",
            7,
            U.DIM,
            QFont.Weight.Medium,
            0.4,
        )
        book_hint.setWordWrap(True)
        book_card.body.addWidget(book_hint)
        book_row = QHBoxLayout()
        book_row.setContentsMargins(0, 0, 0, 0)
        book_row.addWidget(hud_label("Kamerayı aç", 7, U.MUTED, QFont.Weight.Medium, 0.2))
        book_row.addStretch(1)
        book_row.addWidget(GlyphIcon("arrow", 13, U.RED_SOFT))
        book_card.body.addLayout(book_row)
        book_card.clicked.connect(self.open_book_camera)
        layout.addWidget(book_card)

        map_card = HudCard("Harita", "Canlı", "map")
        map_card.body.setContentsMargins(0, 1, 0, 0)
        self.mini_map = MiniMap()
        self.mini_map.setMinimumHeight(150)
        home = self.settings.home_location
        self.mini_map.set_home(home)
        self.mini_map.clicked.connect(self.open_map)
        map_card.body.addWidget(self.mini_map, 1)
        map_footer = QHBoxLayout()
        self.map_caption = hud_label("", 7, U.TEXT_SOFT, QFont.Weight.DemiBold, 1.4)
        self.map_caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._set_map_caption(home)
        map_footer.addWidget(self.map_caption, 1)
        map_footer.addStretch(1)
        map_link = ClickableFrame()
        map_link_layout = QHBoxLayout(map_link)
        map_link_layout.setContentsMargins(0, 0, 0, 0)
        map_link_layout.addWidget(hud_label("Aç  →", 7, U.MUTED, QFont.Weight.Medium, 0.2))
        map_link.clicked.connect(self.open_map)
        map_footer.addWidget(map_link)
        map_card.body.addLayout(map_footer)
        credit = hud_label("© OpenStreetMap katkıları", 6, U.FAINT, QFont.Weight.Medium, 0.6)
        credit.setWordWrap(True)
        map_card.body.addWidget(credit)
        layout.addWidget(map_card, 1)
        layout.addStretch(0)
        self._refresh_calendar_panel()
        return right

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("footerBar")
        footer.setFixedHeight(FOOTER_HEIGHT)
        footer.setStyleSheet(
            f"""
            QFrame#footerBar {{
                background:{rgba_css(U.PANEL, 0.42)};
                border:none;
                border-top:1px solid {U.BORDER};
            }}
            """
        )
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(10)
        layout.addWidget(hud_label("Jarvis · Masaüstü asistanı", 6, U.FAINT, QFont.Weight.Medium, 0.2))
        layout.addWidget(self._vline(11))
        provider = self.settings.provider_label
        model = self.settings.active_model or "otomatik (:free)"
        self.model_status = hud_label(
            f"{provider} · {model} · Ses {self.speech.voice_name}", 6, U.FAINT, QFont.Weight.Medium, 0.2
        )
        layout.addWidget(self.model_status)
        layout.addStretch(1)
        self.connection_dot = PulseDot(U.DIM, 8)
        layout.addWidget(self.connection_dot)
        self.connection_status = hud_label("Sistem hazır", 6, U.DIM, QFont.Weight.Medium, 0.2)
        layout.addWidget(self.connection_status)
        layout.addWidget(self._vline(11))
        close = QPushButton("Oturumu kapat")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFont(hud_font(6, QFont.Weight.Medium, 0.2))
        close.setStyleSheet(
            f"""
            QPushButton {{
                background:transparent;
                color:{U.MUTED};
                border:none;
                padding:2px 4px;
            }}
            QPushButton:hover {{ color:{U.RED_BRIGHT}; }}
            """
        )
        close.clicked.connect(self.close)
        layout.addWidget(close)
        return footer

    # ---------- sohbet odağı ----------

    def _ensure_sidebar_effects(self) -> None:
        if self._sidebar_effects:
            return
        for panel in (self.left_panel, self.right_panel):
            effect = QGraphicsOpacityEffect(panel)
            effect.setOpacity(1.0)
            panel.setGraphicsEffect(effect)
            self._sidebar_effects.append(effect)

    def _clear_sidebar_effects(self) -> None:
        for panel in (self.left_panel, self.right_panel):
            panel.setGraphicsEffect(None)
        self._sidebar_effects.clear()

    def toggle_chat_focus(self) -> None:
        if self._focus_animation is not None and self._focus_animation.state() == QAbstractAnimation.State.Running:
            return
        expanding = not self._focus_expanded
        self._focus_expanded = expanding
        self._ensure_sidebar_effects()

        if expanding:
            self.center_layout.capture_normal_height()
            self.chat.set_expanded(True)
            self.chat.setMaximumHeight(16777215)
            self.focus_button.setText("Sohbeti küçült")
            self.nav_chat.set_active(True)
        else:
            self.left_panel.show()
            self.right_panel.show()
            self.left_panel.setFixedWidth(max(1, self._side_width))
            self.right_panel.setFixedWidth(max(1, self._side_width))
            self.focus_button.setText("Sohbeti genişlet")
            self.nav_chat.set_active(False)

        animation = QVariantAnimation(self)
        animation.setStartValue(self._chat_focus)
        animation.setEndValue(1.0 if expanding else 0.0)
        animation.setDuration(560)
        animation.setEasingCurve(QEasingCurve.Type.Linear)
        animation.valueChanged.connect(self._apply_chat_focus)
        animation.finished.connect(self._on_focus_animation_finished)
        self._focus_animation = animation
        animation.start()

    def _apply_chat_focus(self, value) -> None:
        focus = _clamp01(float(value))
        self._chat_focus = focus
        self.center_layout.set_focus(focus)

        side = _smoothstep(focus / 0.28)
        left_width = round(LEFT_WIDTH * (1.0 - side))
        right_width = round(RIGHT_WIDTH * (1.0 - side))
        self._side_width = max(left_width, right_width)
        self.left_panel.setFixedWidth(max(0, left_width))
        self.right_panel.setFixedWidth(max(0, right_width))
        for effect in self._sidebar_effects:
            effect.setOpacity(1.0 - side)
        self.update()

    def _on_focus_animation_finished(self) -> None:
        animation = self._focus_animation
        self._focus_animation = None
        if animation is not None:
            animation.deleteLater()

        if self._focus_expanded:
            self.left_panel.setFixedWidth(0)
            self.right_panel.setFixedWidth(0)
            self.left_panel.hide()
            self.right_panel.hide()
        else:
            self.left_panel.setFixedWidth(LEFT_WIDTH)
            self.right_panel.setFixedWidth(RIGHT_WIDTH)
            self.left_panel.show()
            self.right_panel.show()
            self.chat.setMaximumHeight(240)
            self.chat.set_expanded(False)
            self.center_layout.capture_normal_height()
            self.center_layout.invalidate()
        self._clear_sidebar_effects()
        self.update()

    # ---------- olay bağlantıları ----------

    def _wire_events(self) -> None:
        self.timer_engine.changed.connect(self._refresh_timer_panel)
        self.timer_engine.expired.connect(self._on_timer_expired)
        self.speech.started.connect(self._on_speech_started)
        self.speech.finished.connect(self._on_speech_finished)
        self.speech.error.connect(self._on_speech_error)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._timer_engine_tick = QTimer(self)
        self._timer_engine_tick.timeout.connect(self.timer_engine.tick)
        self._timer_engine_tick.start(1000)

        self._shortcut_mic = QShortcut(QKeySequence("F4"), self)
        self._shortcut_mic.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_mic.activated.connect(self.toggle_mic)
        self._shortcut_focus = QShortcut(QKeySequence("F2"), self)
        self._shortcut_focus.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_focus.activated.connect(self.toggle_chat_focus)
        self._shortcut_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._shortcut_escape.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_escape.activated.connect(self._escape)
        self._shortcut_palette = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut_palette.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_palette.activated.connect(self.open_command_palette)
        self._shortcut_history = QShortcut(QKeySequence("Ctrl+H"), self)
        self._shortcut_history.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_history.activated.connect(self.open_chat_history)
        self._shortcut_clipboard = QShortcut(QKeySequence("Ctrl+Alt+V"), self)
        self._shortcut_clipboard.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_clipboard.activated.connect(self.open_clipboard)
        self._shortcut_briefing = QShortcut(QKeySequence("Ctrl+B"), self)
        self._shortcut_briefing.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_briefing.activated.connect(self.open_briefing)
        self._shortcut_reminders = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self._shortcut_reminders.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_reminders.activated.connect(self.open_reminders)

        self._reminder_timer = QTimer(self)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_timer.start(15_000)
        QTimer.singleShot(1800, self._check_reminders)
        self._companion_timer = QTimer(self)
        self._companion_timer.timeout.connect(self._check_companion)
        self._companion_timer.start(60_000)
        QTimer.singleShot(30_000, self._check_companion)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            self._last_clipboard = clipboard.text()
            clipboard.dataChanged.connect(self._capture_clipboard)

        self.chat.add_message(
            "Merhaba Cem. Sistemler çevrimiçi. Yine karşı karşıyayız; bakalım bu defa neyi sorgulayacaksın. "
            "Ctrl+K ile komut paletini açabilirsin.",
            False,
        )
        self.input.setFocus()

    def _start_monitors(self) -> None:
        if self._monitors_started:
            return
        self._monitors_started = True
        threading.Thread(target=self._system_loop, name="jarvis-system", daemon=True).start()
        threading.Thread(target=self._weather_loop, name="jarvis-weather", daemon=True).start()
        threading.Thread(target=self._news_prewarm, name="jarvis-news", daemon=True).start()

    def _news_prewarm(self) -> None:
        # İlk haber isteği RSS turunu beklemesin; servis önbelleği süreç boyunca paylaşılır.
        try:
            from app.core.news_tools import shared_news_service

            shared_news_service().fetch_all()
        except Exception:
            pass

    def _system_loop(self) -> None:
        while not self._system_stop.is_set():
            try:
                snapshot = self.system_sampler.snapshot()
                if snapshot:
                    self.system_sampled.emit(snapshot)
            except Exception:
                pass
            self._system_stop.wait(2.5)

    def _weather_loop(self) -> None:
        self._weather_wake.wait(3.0)
        self._weather_wake.clear()
        while not self._weather_stop.is_set():
            delay = 600.0
            if self._weather_lock.acquire(blocking=False):
                try:
                    data = self.weather_service.fetch()
                    self.weather_ready.emit(data)
                    if data.get("stale"):
                        delay = 60.0
                except Exception as error:
                    delay = 60.0
                    self.weather_failed.emit(str(error))
                finally:
                    self._weather_lock.release()
            if self._weather_stop.is_set():
                break
            self._weather_wake.wait(delay)
            self._weather_wake.clear()

    # ---------- saat / paneller ----------

    def _tick_clock(self) -> None:
        now = datetime.now()
        self.header_clock.setText(now.strftime("%H:%M"))
        self.header_date.setText(now.strftime("%d.%m.%Y"))
        self.left_time.setText(now.strftime("%H:%M"))
        self.left_date.setText(f"{turkish_date(now)} · {now.year}")
        for item in self.timer_engine.items:
            label = item.get("_left_remaining")
            if label is not None:
                try:
                    from app.core.hud_data import format_remaining

                    label.setText(format_remaining(self.timer_engine.remaining(item)))
                except RuntimeError:
                    pass

    def _on_system(self, snapshot: dict) -> None:
        self._system_snapshot = snapshot
        if not snapshot.get("available"):
            return
        cpu = float(snapshot.get("cpu", 0))
        self.cpu_gauge.set_value(cpu)
        self._cpu_history.append(cpu)
        if len(self._cpu_history) > 120:
            del self._cpu_history[:-120]
        self.cpu_spark.push(cpu)
        self.ram_label.setText(f"RAM  %{snapshot.get('memory_percent', 0):.0f}")
        self.disk_label.setText(f"Disk  %{snapshot.get('disk_percent', 0):.0f} dolu")
        self.cores_label.setText(
            f"{snapshot.get('cores', '—')} çekirdek · {snapshot.get('threads', '—')} iş parçacığı"
        )
        if isinstance(self._modal, SystemModal):
            self._modal.update_data(snapshot)

    def _on_weather(self, data: dict) -> None:
        self.weather = data
        self._weather_ready_once = True
        current = data.get("current") or {}
        daily = data.get("daily") or []
        location = str(data.get("location", "Derince · Turkey"))
        self.weather_location.setText(location.split("·")[0].strip())
        self.weather_temp.setText(f"{current.get('temp', '—')}°")
        desc = str(current.get("text", "—"))
        self.weather_desc.setText(desc[:1].upper() + desc[1:])
        self.weather_icon.set_kind(str(current.get("kind", "cloud")))
        if daily:
            low = float(daily[0].get("min", 0))
            high = float(daily[0].get("max", 0))
            self.weather_low.setText(f"{low:.0f}°")
            self.weather_high.setText(f"{high:.0f}°")
            self.weather_range.set_range(low, high, float(current.get("temp", low)))
        self.weather_detail.setText(
            f"Hissedilen {current.get('feels', '—')}° · Nem %{current.get('humidity', '—')}\n"
            f"Rüzgar {current.get('wind', '—')} km/s · {current.get('wind_dir', '—')} yönünden\n"
            f"Basınç {current.get('pressure', '—')} hPa · Görüş {current.get('visibility', '—')} km"
        )
        if isinstance(self._modal, WeatherModal):
            self._modal.set_weather(data)
        if isinstance(self._modal, TimeModal):
            self._modal.set_weather(data)
        if isinstance(self._modal, BriefingModal):
            self._modal.set_text(self._briefing_text())

    def _on_weather_error(self, error: str) -> None:
        if self.weather is None:
            self.weather_desc.setText("hava verisi alınamadı")
            self.weather_detail.setText("İnternet bağlantısı gerekli\nOpen-Meteo servisine ulaşılamadı")
        if isinstance(self._modal, WeatherModal):
            self._modal.set_error(error)
        if self._weather_ready_once:
            self._toast("Hava durumu güncellenemedi; son veri korunuyor.")

    def _refresh_notes_panel(self) -> None:
        notes = list(reversed(self.hud_store.notes()))
        self.notes_section.trailing.setText(f"{len(notes)} not")
        if not notes:
            self.note_preview.setText("Henüz not yok. “Not al: …” de ya da aşağıya yaz.")
            set_label_color(self.note_preview, U.DIM)
            return
        text = " · ".join(str(note.get("text", "")) for note in notes[:2])
        if len(notes) > 2:
            text += f" · +{len(notes) - 2}"
        self.note_preview.setText(text[:160])
        set_label_color(self.note_preview, U.TEXT_SOFT)

    def _quick_note(self) -> None:
        text = self.quick_note.text().strip()
        if not text:
            return
        self.hud_store.add_note(text)
        self.quick_note.clear()
        self._refresh_notes_panel()
        self._toast("Not kaydedildi.")

    def _refresh_timer_panel(self) -> None:
        clear_layout(self.left_timers_layout)
        items = self.timer_engine.items
        self.timer_section.trailing.setText(f"{len(items)} aktif" if items else "")
        if not items:
            self.timer_hint.setText("Hızlı süre seç")
            self.left_timers.hide()
            return
        self.left_timers.show()
        self.timer_hint.setText("Çalışıyor")
        for item in items[:3]:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            name = hud_label(str(item.get("label", "—"))[:18], 7, U.TEXT_SOFT, QFont.Weight.Medium, 0.4)
            remaining = hud_label("--:--", 7, U.GOLD, QFont.Weight.Medium, 0.2)
            remaining.setAlignment(Qt.AlignmentFlag.AlignRight)
            item["_left_remaining"] = remaining
            layout.addWidget(name, 1)
            layout.addWidget(remaining)
            self.left_timers_layout.addWidget(row)

    def _quick_timer(self, minutes: int) -> None:
        self.timer_engine.add(minutes)
        self._toast(f"{minutes} dakikalık zamanlayıcı kuruldu.")
        self._refresh_timer_panel()

    def _on_timer_expired(self, label: str) -> None:
        self._toast(f"SÜRE DOLDU · {label}", 6000)
        self.chat.add_message(f"Zamanlayıcı bitti · {label}", False)
        if self.mic_enabled and self.speak_replies:
            self.speech.speak(f"Süre doldu. {label}")

    def _refresh_calendar_panel(self) -> None:
        now = datetime.now()
        events = []
        for event in self.hud_store.events():
            moment = local_moment(event.get("when"))
            if moment is None:
                continue
            events.append((moment, event))
        today = [(moment, event) for moment, event in events if moment.date() == now.date()]
        upcoming = [(moment, event) for moment, event in events if moment >= now - timedelta(minutes=1)]
        upcoming.sort(key=lambda pair: pair[0])
        today.sort(key=lambda pair: pair[0])
        self.calendar_section.trailing.setText(f"{len(today)} bugün")
        self.calendar_count.setText(str(len(today)))
        if today:
            first_moment, first_event = today[0]
            self.calendar_message.setText(f"{first_moment.strftime('%H:%M')} · {first_event.get('title', '—')}")
        elif upcoming:
            first_moment, first_event = upcoming[0]
            self.calendar_message.setText(f"En yakın: {first_moment.strftime('%d.%m %H:%M')} · {first_event.get('title', '—')}")
        else:
            self.calendar_message.setText("Bugün takviminde etkinlik yok.")
        reminders = self.hud_store.pending_reminders()
        self.reminders_section.trailing.setText(f"{len(reminders)} açık")
        self.reminders_count.setText(str(len(reminders)))
        if reminders:
            first = reminders[0]
            moment = local_moment(first.get("when"))
            when_text = ""
            if moment is not None:
                when_text = moment.strftime("%H:%M") if moment.date() == now.date() else moment.strftime("%d.%m %H:%M")
            self.reminders_message.setText(
                f"{first.get('text', '—')}" + (f" · {when_text}" if when_text else "")
            )
        else:
            self.reminders_message.setText("Bekleyen hatırlatıcı yok.")

    # ---------- kişisel araçlar ----------

    def _briefing_text(self) -> str:
        return build_briefing_text(
            now=datetime.now(),
            weather=self.weather,
            events=self.hud_store.events(),
            reminders=self.hud_store.reminders(),
            notes=self.hud_store.notes(),
            snapshot=self._system_snapshot,
        )

    def open_briefing(self) -> None:
        self._show_modal(BriefingModal(self, self._briefing_text(), speak=self._speak_briefing))

    def open_home(self) -> None:
        self._show_modal(HomeModal(self, self.smart_home))

    def _speak_briefing(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        if not self.speak_replies:
            self._toast("Sesli yanıt kapalı; üstteki Ses düğmesinden açabilirsin.", 4000)
            return
        self.speech.stop()
        self._set_state("speaking")
        self.speech.speak(text)

    def open_reminders(self) -> None:
        modal = RemindersModal(self, self.hud_store)
        modal.changed.connect(self._refresh_calendar_panel)
        self._show_modal(modal)

    def open_chat_history(self) -> None:
        self._show_modal(ChatHistoryModal(self, self.storage))

    def open_task_trace(self) -> None:
        self._show_modal(TaskTraceModal(self, self.storage))

    def open_task_session(self) -> None:
        manager = TaskSessionManager(self.storage)
        self._show_modal(
            TaskSessionModal(
                self,
                manager,
                pause=self._pause_task,
                resume=self._resume_task,
                takeover=self._takeover_task,
            )
        )

    def _pause_task(self, reason: str = "") -> None:
        worker = self.worker
        if worker is None or not self.request_active:
            self._toast("Duraklatılacak etkin görev yok.", 3500)
            return
        try:
            worker.pause(reason)
        except RuntimeError:
            return
        self._toast("Görev duraklatılıyor…", 3500)

    def _resume_task(self) -> None:
        worker = self.worker
        if worker is None or not self.request_active:
            return
        try:
            worker.resume()
        except RuntimeError:
            return

    def _takeover_task(self, reason: str = "") -> None:
        worker = self.worker
        if worker is None or not self.request_active:
            self._toast("Devralınacak etkin görev yok.", 3500)
            return
        try:
            worker.takeover(reason)
        except RuntimeError:
            return

    def toggle_task_pause(self) -> None:
        worker = self.worker
        if worker is None or not self.request_active:
            return
        if worker.is_paused:
            self._resume_task()
        else:
            self._pause_task("Görev kullanıcı tarafından duraklatıldı.")

    def open_memory_manager(self) -> None:
        self._show_modal(MemoryModal(self, self.storage))

    def open_clipboard(self) -> None:
        self._show_modal(ClipboardModal(self, self.clipboard_history))

    def _capture_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        text = clipboard.text()
        if text == self._last_clipboard:
            return
        self._last_clipboard = text
        if text.strip():
            self.clipboard_history.add(text)

    def _notify_reminder(self, text: str, when_text: str = "") -> None:
        suffix = f" · {when_text}" if when_text else ""
        self._toast(f"HATIRLATICI · {text}{suffix}", 9000)
        self.chat.add_message(f"Hatırlatıcı: {text}{suffix}", False)
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        QApplication.alert(self, 2500)
        if self.mic_enabled and self.speak_replies and not self.request_active and not self.speech.busy and self.voice_thread is None:
            self.speech.speak(f"Hatırlatıcı: {text}")

    def _check_reminders(self) -> None:
        try:
            due = self.hud_store.due_reminders()
        except Exception:
            return
        if not due:
            return
        for item in due:
            reminder_id = str(item.get("id") or "")
            if reminder_id:
                self.hud_store.mark_reminder_notified(reminder_id)
            moment = local_moment(item.get("when"))
            when_text = moment.strftime("%H:%M") if moment is not None else ""
            self._notify_reminder(str(item.get("text") or "Hatırlatıcı"), when_text)
        self._refresh_calendar_panel()
        modal = self._modal
        if isinstance(modal, RemindersModal):
            modal.refresh()

    def _check_companion(self) -> None:
        if self._closing or not self.settings.companion_enabled:
            return
        if self.request_active or self._modal is not None or self.speech.busy or self.voice_thread is not None:
            return
        try:
            nudge = self.companion.next(
                events=self.hud_store.upcoming_events(),
                reminders=self.hud_store.pending_reminders(),
                interval_minutes=self.settings.companion_interval,
                quiet_start=self.settings.companion_quiet_start,
                quiet_end=self.settings.companion_quiet_end,
                hydration=self.settings.companion_hydration,
            )
        except Exception:
            return
        if nudge is None:
            return
        self.companion.mark(nudge)
        label = {
            "event": "Yaklaşan",
            "reminder": "Hatırlatıcı",
            "hydration": "Su molası",
            "break": "Mola",
        }.get(nudge.kind, "Eşlikçi")
        self._toast(f"{label} · {nudge.text}", 8000)
        if self.settings.companion_speak and self.mic_enabled and self.speak_replies:
            self.speech.speak(nudge.text)

    def toggle_companion(self) -> None:
        enabled = not self.settings.companion_enabled
        self.settings.set_companion(enabled=enabled)
        self._toast(f"Eşlikçi {'açıldı' if enabled else 'kapatıldı'}", 3000)

    def _diagnostics_text(self) -> str:
        return build_report(settings=self.settings, data_dir=self.settings.data_dir)

    def open_diagnostics(self) -> None:
        self._show_modal(
            DiagnosticsModal(
                self,
                self._diagnostics_text(),
                refresh=self._diagnostics_text,
                data_dir=self.settings.data_dir,
            )
        )

    def _quick_system(self, action: str) -> None:
        try:
            self._toast(self.system_controls.action(action))
        except Exception as error:  # noqa: BLE001 - kullanıcıya kısa bildirim
            self._toast(f"Sistem işlemi başarısız: {error}", 5000)

    def _palette_commands(self) -> List[dict]:
        return [
            {"label": "Haritayı aç", "hint": "Yakındaki yerler ve rota", "keywords": "harita map konum yol", "action": self.open_map},
            {"label": "Tarayıcıyı aç", "hint": "Jarvis iç tarayıcısı · tek motor", "keywords": "tarayici browser web internet arama", "action": self.open_browser},
            {"label": "Kitap tara", "hint": "Kamera/ISBN · sayfa OCR · kişisel özet", "keywords": "kitap kamera isbn tarama tarayici ozet okuma", "action": self.open_book_camera},
            {"label": "Günlük brifing", "hint": "Hava, takvim, hatırlatıcı, notlar", "keywords": "brifing bugün özet gunluk rapor", "action": self.open_briefing},
            {"label": "Hatırlatıcılar", "hint": "Hatırlatıcı ekle ve listele", "keywords": "hatirlatici reminder alarm", "action": self.open_reminders},
            {"label": "Pano merkezi", "hint": "Kopyalanan metinler", "keywords": "pano clipboard kopyala yapistir", "action": self.open_clipboard},
            {"label": "Sohbet geçmişi", "hint": "Ara, kopyala, Markdown dışa aktar", "keywords": "gecmis history sohbet export", "action": self.open_chat_history},
            {"label": "Görev günlüğü", "hint": "Ajan araç izleri ve görev özetleri", "keywords": "gorev trace ajan log iz kayit gecmis", "action": self.open_task_trace},
            {"label": "Görev oturumu", "hint": "Adım izi · duraklat · devral · yetenek", "keywords": "gorev oturum session durakla devral adim iz", "action": self.open_task_session},
            {"label": "Kalıcı hafıza", "hint": "Jarvis'in senin hakkında tuttuğu notlar", "keywords": "hafiza memory not hatirla kalici", "action": self.open_memory_manager},
            {"label": "Notlar", "hint": "Hızlı notlar", "keywords": "not note defter", "action": self.open_notes},
            {"label": "Takvim", "hint": "Etkinlikler", "keywords": "takvim etkinlik calendar", "action": self.open_calendar},
            {"label": "Sistem merkezi", "hint": "Ses, medya, güç ve süreçler", "keywords": "sistem ses medya guc guc pil", "action": self.open_system},
            {"label": "Dünya saatleri", "hint": "Saat dilimleri", "keywords": "saat dunya time", "action": self.open_time},
            {"label": "Hava durumu", "hint": "Ayrıntılı tahmin", "keywords": "hava weather tahmin", "action": self.open_weather},
            {"label": "Zamanlayıcı", "hint": "Kronometre ve sayaç", "keywords": "zamanlayici timer kronometre", "action": self.open_timer},
            {"label": "AI modelleri", "hint": "Model ve ajan bütçesi", "keywords": "model yapay zeka ai", "action": self.open_models},
            {"label": "Ayarlar", "hint": "Erişim modu ve konum", "keywords": "ayar settings tercih", "action": self.open_settings},
            {"label": "Mikrofonu aç/kapat", "hint": "Sesli komut", "keywords": "mikrofon mik ses f4", "action": self.toggle_mic},
            {"label": "Kamera algısı", "hint": "Arka plan kamera izleme · aç/kapat", "keywords": "kamera algila izle perception webcam", "action": self.toggle_camera_perception},
            {"label": "Ekran bağlamı", "hint": "Her komuta ekran görüntüsü ekle · aç/kapat", "keywords": "ekran screen ekran goruntusu baglam", "action": self.toggle_screen_context},
            {"label": "Kamera tam ekran HUD", "hint": "Büyük görünüm · 3B ızgara · Esc ile çık", "keywords": "kamera tam ekran hud buyuk izgara", "action": self.open_camera_fullscreen},
            {"label": "El kontrolü · 3B efektler", "hint": "Yerel jestler · mühür, ateş, gezegen · cihazdan çıkmaz", "keywords": "el kontrolu jest mediapipe 3b efekt gezegen eller", "action": self.toggle_hand_control},
            {"label": "Akıllı ev", "hint": "Işıklar ve kahve makineleri · simülasyon", "keywords": "akilli ev isik lamba kahve fincan mutfak home", "action": self.open_home},
            {"label": "Sohbeti genişlet", "hint": "Odak modu", "keywords": "sohbet genislet focus", "action": self.toggle_chat_focus},
            {"label": "5 dakikalık zamanlayıcı", "hint": "Hızlı sayaç", "keywords": "5 dakika zamanlayici", "action": lambda: self._quick_timer(5)},
            {"label": "Sessize al", "hint": "Sistemi sessize al/geri aç", "keywords": "sessiz mute ses kapat", "action": lambda: self._quick_system("mute")},
            {"label": "Ekranı kilitle", "hint": "Bilgisayarı kilitle", "keywords": "kilit lock ekran", "action": lambda: self._quick_system("lock")},
            {"label": "Hava durumunu yenile", "hint": "Open-Meteo verisini çek", "keywords": "hava yenile guncelle", "action": self._refresh_weather},
            {"label": "Tanılama ve gizlilik", "hint": "Yerel rapor · API anahtarı yazılmaz", "keywords": "tanilama teshis rapor gizlilik privacy diagnostics surum", "action": self.open_diagnostics},
            {"label": "Eşlikçiyi aç/kapat", "hint": "Nazik dürtüler · sessiz saatler", "keywords": "eslikci companion durtu mola su hatirlatma", "action": self.toggle_companion},
        ]

    def open_command_palette(self) -> None:
        if isinstance(self._modal, CommandPalette):
            return
        self._show_modal(CommandPalette(self, self._palette_commands()))

    # ---------- modaller ----------

    def _show_modal(self, modal: HudModal) -> None:
        if modal is self._modal:
            modal.raise_()
            modal.setFocus()
            return
        if self.camera_window is not None:
            try:
                self.camera_window.hide()
            except RuntimeError:
                self.camera_window = None
        previous = self._modal
        self._modal = modal
        if previous is not None:
            try:
                previous.close_modal()
            except RuntimeError:
                pass
        if not getattr(modal, "_window_wired", False):
            modal._window_wired = True
            modal.closed.connect(lambda current=modal: self._modal_closed(current))
            if hasattr(modal, "open_settings_requested"):
                modal.open_settings_requested.connect(self.open_settings)
        if previous is not None:
            # Zemin zaten koyu; yeni bölüm arka planı sıfırdan soldurmasın.
            prepare = getattr(modal, "prepare_incoming", None)
            if callable(prepare):
                prepare()
        modal.setGeometry(self.rect())
        modal.show()
        modal.raise_()
        modal.setFocus()
        self._set_ambient_animations(False)

    def _set_ambient_animations(self, enabled: bool) -> None:
        if enabled == self._ambient_enabled:
            return
        self._ambient_enabled = bool(enabled)
        for widget in (
            getattr(self, "orb", None),
            getattr(self, "mini_map", None),
            getattr(self, "brand_mark", None),
            getattr(self, "state_dot", None),
            getattr(self, "connection_dot", None),
        ):
            setter = getattr(widget, "set_animating", None)
            if callable(setter):
                setter(enabled)

    def _modal_closed(self, modal: HudModal) -> None:
        if self._modal is modal:
            self._modal = None
        if self._modal is None:
            self._set_ambient_animations(True)
            if self.mic_enabled and not self.request_active and not self.speech.busy:
                self._maybe_listen(700)

    def open_time(self) -> None:
        self._show_modal(TimeModal(self, self.weather))

    def open_system(self) -> None:
        modal = SystemModal(self, self.system_controls)
        if self._system_snapshot:
            modal.update_data(self._system_snapshot)
        self._show_modal(modal)

    def open_timer(self) -> None:
        self._show_modal(TimerModal(self, self.timer_engine))

    def open_notes(self) -> None:
        modal = NotesModal(self, self.hud_store)
        modal.changed.connect(self._refresh_notes_panel)
        self._show_modal(modal)

    def open_calendar(self) -> None:
        modal = CalendarModal(self, self.hud_store)
        modal.changed.connect(self._refresh_calendar_panel)
        self._show_modal(modal)

    def open_weather(self) -> None:
        modal = WeatherModal(self, self.weather)
        modal.refresh_requested.connect(self._refresh_weather)
        self._show_modal(modal)

    def _set_map_caption(self, home: dict) -> None:
        name = str(home.get("name") or "Evim")
        region = str(home.get("region") or "Kocaeli")
        self.map_caption.setText(f"{name} · {region}")

    def _ensure_map_modal(self) -> MapModal:
        modal = self._map_modal
        if modal is None:
            modal = MapModal(self, self.settings)
            self._map_modal = modal
            modal.home_changed.connect(self._on_home_changed)
            modal.set_click_querier(self._map_click_query)
            modal.set_category_querier(self._map_category_query)
            modal.set_route_querier(self._map_route_query)
            modal.set_link_opener(self.open_browser)
        return modal

    def open_map(self, payload: Optional[dict] = None) -> None:
        if isinstance(self._modal, MapModal):
            if payload:
                self._modal.apply_payload(payload)
            return
        modal = self._ensure_map_modal()
        modal.reopen()
        self._show_modal(modal)
        self._map_tools_service().warm_home()
        if payload:
            modal.apply_payload(payload)

    def _ensure_browser_modal(self) -> BrowserModal:
        modal = self._browser_modal
        if modal is None:
            modal = BrowserModal(self)
            self._browser_modal = modal
            modal.map_requested.connect(self.open_map)
        return modal

    def open_browser(self, url: Optional[str] = None) -> None:
        modal = self._ensure_browser_modal()
        modal.reopen()
        self._show_modal(modal)
        if url:
            modal.open_url(str(url))
        else:
            modal.focus_url()

    def open_book_camera(self) -> None:
        from .book_modal import BookModal

        if isinstance(self._modal, BookModal):
            return
        self._show_modal(BookModal(
            self,
            self.settings,
            self.storage,
            camera=self.camera_hub,
            camera_keep=lambda: self._camera_perception,
        ))

    # ---------- kamera algısı ----------

    def _ensure_camera_window(self):
        if self.camera_window is None:
            from .camera_window import CameraWindow

            if self._camera_ai is None:
                self._camera_ai = AI(storage=self.storage, settings=self.settings)
            self.camera_window = CameraWindow(self, self.camera_hub, self._camera_ai)
            self.camera_window.stop_requested.connect(self.disable_camera_perception)
            self.camera_window.scene_updated.connect(self._on_voice_camera_scene)
            self.camera_window.scene_updated.connect(self._on_camera_scene_hud)
            self.camera_window.scene_failed.connect(self._on_voice_camera_scene_failed)
            self.camera_window.scene_failed.connect(self._on_camera_scene_failed_hud)
            self.camera_window.gesture_action.connect(self._on_camera_gesture)
            self.camera_window.hand_failed.connect(self._on_camera_hand_failed)
            self.camera_hub.error.connect(self._on_camera_hub_error)
            self._reposition_camera_window()
        return self.camera_window

    def _reposition_camera_window(self) -> None:
        if self.camera_window is not None:
            try:
                self.camera_window.reposition()
            except RuntimeError:
                self.camera_window = None

    def _auto_start_camera_perception(self) -> None:
        if self._camera_perception or self._camera_manual_off:
            return
        if not CameraHub.available() or not CameraHub.devices():
            return
        self.enable_camera_perception(show=False, quiet=True)

    def toggle_camera_perception(self) -> None:
        if self._camera_perception:
            self._camera_manual_off = True
            self.disable_camera_perception()
        else:
            self._camera_manual_off = False
            self.enable_camera_perception()

    def enable_camera_perception(self, show: bool = True, quiet: bool = False) -> None:
        self._camera_manual_off = False
        if self._camera_perception:
            if show:
                window = self._ensure_camera_window()
                window.ensure_visible()
            return
        self._camera_perception = True
        self._update_camera_style()
        self._update_floating_hud_camera("", 0, True)
        window = self._ensure_camera_window()
        window.start_perception(show=show)
        if show:
            self._reposition_camera_window()
            if not quiet:
                self._toast("Kamera algısı açıldı. Pencereyi kapatsan bile arka planda çalışır.", 5000)
        elif not quiet:
            self._toast("Kamera algısı arka planda açıldı.", 3500)

    def disable_camera_perception(self, quiet: bool = False) -> None:
        if not self._camera_perception:
            return
        if self._voice_scene_pending:
            self._voice_scene_pending = False
            self.progress_label.hide()
        self._camera_perception = False
        if self.camera_window is not None:
            try:
                self.camera_window.stop_perception()
            except RuntimeError:
                self.camera_window = None
        if not getattr(self._modal, "uses_camera", False):
            self.camera_hub.stop()
        self._update_camera_style()
        self._update_floating_hud_camera("", 0, False)
        if not quiet:
            self._toast("Kamera algısı kapatıldı.", 3500)

    def open_camera_fullscreen(self) -> bool:
        self._camera_manual_off = False
        self.enable_camera_perception(show=False, quiet=True)
        if not self._camera_perception:
            return False
        try:
            self._ensure_camera_window().show_fullscreen()
        except RuntimeError:
            self.camera_window = None
            return False
        return True

    def open_hand_control(self) -> bool:
        self._camera_manual_off = False
        if not self._camera_perception:
            self.enable_camera_perception(show=True, quiet=True)
        if not self._camera_perception:
            self._toast("El kontrolü için kamera açılamadı.", 5000)
            return False
        started = False
        try:
            window = self._ensure_camera_window()
            started = bool(window.set_hand_control(True))
            if started:
                window.ensure_visible()
        except RuntimeError:
            self.camera_window = None
        if started:
            self._toast(
                "El kontrolü başlatılıyor · başparmak: palet · zafer: tam ekran · yumruk: durdur",
                6000,
            )
        else:
            self._toast("El kontrolü başlatılamadı · MediaPipe veya el modeli eksik olabilir", 6000)
        return started

    def close_hand_control(self) -> bool:
        window = self.camera_window
        if window is None:
            return False
        try:
            was_enabled = bool(window.hand_control_enabled())
            window.set_hand_control(False)
            return was_enabled
        except RuntimeError:
            self.camera_window = None
            return False

    def toggle_hand_control(self) -> None:
        window = self.camera_window
        enabled = False
        if window is not None:
            try:
                enabled = bool(window.hand_control_enabled())
            except RuntimeError:
                self.camera_window = None
        if enabled:
            self.close_hand_control()
            self._toast("El kontrolü kapatıldı.", 3500)
        else:
            self.open_hand_control()

    def _on_camera_gesture(self, action: str) -> None:
        action = str(action or "")
        if action == "palette":
            self._toast("Jest algılandı · komut paleti açılıyor.", 3000)
            self.open_command_palette()
        elif action == "fullscreen":
            self._toast("Jest algılandı · tam ekran HUD açılıyor.", 3000)
            self.open_camera_fullscreen()
        elif action == "stop":
            self._camera_manual_off = True
            self.disable_camera_perception(quiet=True)
            self._toast("Yumruk jesti · kamera algısı durduruldu.", 4000)

    def _on_camera_hand_failed(self, message: str) -> None:
        self._toast(f"El kontrolü: {str(message)[:180]}", 7000)

    def _handle_screen_voice_command(self, text: str, speak: bool = True) -> bool:
        intent = screen_voice_intent(text)
        if intent is None:
            return False
        self.chat.add_message(text, True)
        if intent == "on":
            if self._screen_context:
                reply = "Ekran bağlamı zaten açık."
            elif self.enable_screen_context():
                reply = "Ekran bağlamı açık. Her komutta ekranını görüyorum; kapatmak için 'ekran bağlamını kapat' de."
            else:
                reply = "Ekran bağlamı açılamadı; ekran görüntüsü alınamadı."
        else:
            was_open = self._screen_context
            self.disable_screen_context()
            reply = "Ekran bağlamını kapattım." if was_open else "Ekran bağlamı zaten kapalıydı."
        self.chat.add_message(reply, False)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_subtitle(reply, "Jarvis")
        self.progress_label.hide()
        if speak:
            self._set_state("speaking")
            self.speech.speak(reply)
        else:
            self._set_state("idle")
        return True

    def _handle_camera_voice_command(self, text: str, speak: bool = True) -> bool:
        intent = camera_voice_intent(text)
        if intent is None:
            return False
        self.chat.add_message(text, True)
        if intent == "analyze":
            self._camera_manual_off = False
            self._voice_scene_pending = True
            self._voice_scene_tries = 0
            self._set_state("thinking")
            self.progress_label.setText("Kamera analiz ediliyor…")
            self.progress_label.show()
            self._trigger_voice_scene_analysis()
            return True
        if intent == "fullscreen":
            if self.open_camera_fullscreen():
                reply = "Kamera tam ekran HUD açıldı. Çıkmak için Esc veya F11."
            else:
                reply = "Kamera tam ekran açılamadı."
            self.chat.add_message(reply, False)
            self.progress_label.hide()
            if speak:
                self._set_state("speaking")
                self.speech.speak(reply)
            else:
                self._set_state("idle")
            return True
        if intent == "hands_on":
            if self.open_hand_control():
                reply = (
                    "El kontrolü açıldı. Başparmak 1 saniye: komut paleti, zafer 0.9 saniye: "
                    "tam ekran, yumruk 1.8 saniye: algıyı durdur."
                )
            else:
                reply = "El kontrolü açılamadı. MediaPipe veya el modeli hazır değil."
        elif intent == "hands_off":
            was_enabled = self.close_hand_control()
            reply = "El kontrolünü kapattım." if was_enabled else "El kontrolü zaten kapalıydı."
        elif intent == "off":
            self._camera_manual_off = True
            self._voice_scene_pending = False
            self.progress_label.hide()
            was_active = self._camera_perception
            self.disable_camera_perception(quiet=True)
            reply = "Kamera algısını kapattım." if was_active else "Kamera algısı zaten kapalıydı."
        elif intent == "hide":
            was_active = self._camera_perception
            if self.camera_window is not None:
                try:
                    self.camera_window.hide()
                except RuntimeError:
                    self.camera_window = None
            reply = (
                "Kamera panelini gizledim; algı arka planda sürüyor."
                if was_active
                else "Kamera paneli zaten kapalı."
            )
        else:
            was_active = self._camera_perception
            self.enable_camera_perception(show=True, quiet=True)
            if not self._camera_perception:
                reply = "Kamera açılamadı."
            elif was_active:
                reply = "Kamera paneli açık."
            else:
                reply = "Kamera açık. Seni ve çevreni algılıyorum."
        self.chat.add_message(reply, False)
        self.progress_label.hide()
        if speak:
            self._set_state("speaking")
            self.speech.speak(reply)
        else:
            self._set_state("idle")
        return True

    def _handle_home_voice_command(self, text: str, speak: bool = True) -> bool:
        intent = home_voice_intent(text)
        if intent is None:
            return False
        self.chat.add_message(text, True)
        action = intent.get("action")
        if action == "panel":
            self.open_home()
            reply = "Akıllı ev paneli açıldı. Işıklar ve kahve makineleri simülasyonla çalışıyor."
        elif action == "light":
            reply = self._apply_home_light(intent)
        elif action == "light_status":
            reply = self._describe_home_lights(str(intent.get("room") or "salon"))
        elif action == "coffee_brew":
            reply = self._brew_home_coffee(intent)
        elif action == "coffee_status":
            reply = self.smart_home.coffee(str(intent.get("kind") or "turk")).describe(self.smart_home.now())
        elif action == "coffee_cancel":
            coffee = self.smart_home.cancel_coffee(str(intent.get("kind") or "turk"))
            reply = f"{coffee.name} iptal edildi."
        else:
            return False
        self.chat.add_message(reply, False)
        modal = self._modal
        if isinstance(modal, HomeModal):
            modal.refresh()
        self.progress_label.hide()
        if speak:
            self._set_state("speaking")
            self.speech.speak(reply)
        else:
            self._set_state("idle")
        return True

    def _apply_home_light(self, intent: dict) -> str:
        room = str(intent.get("room") or "salon")
        on = intent.get("on")
        brightness = intent.get("brightness")
        tone = intent.get("tone")
        if room == "all":
            lights = self.smart_home.set_all_lights(on=on, brightness=brightness, tone=tone)
            if on is False:
                return "Tüm ışıklar kapatıldı."
            if on is True:
                return (
                    f"Tüm ışıklar açıldı · %{lights[0].brightness} · "
                    f"{TONES.get(lights[0].tone, 'Nötr')}."
                )
            return "Işıklar güncellendi · " + ", ".join(
                f"{light.name}: %{light.brightness} {TONES.get(light.tone, 'Nötr')}" for light in lights
            ) + "."
        light = self.smart_home.set_light(room, on=on, brightness=brightness, tone=tone)
        return light.describe()

    def _describe_home_lights(self, room: str) -> str:
        if room == "all":
            return " ".join(light.describe() for light in self.smart_home.lights.values())
        return self.smart_home.light(room).describe()

    def _brew_home_coffee(self, intent: dict) -> str:
        kind = str(intent.get("kind") or "turk")
        ok, coffee, seconds = self.smart_home.brew(
            kind,
            cups=intent.get("cups"),
            foam=intent.get("foam"),
            sugar=intent.get("sugar"),
        )
        if not ok:
            remaining = coffee.snapshot(self.smart_home.now()).get("remaining", 0)
            return f"{coffee.name} zaten demleniyor · {remaining} sn kaldı."
        if kind == "turk":
            return (
                f"Türk kahvesi pişiyor · {FOAMS.get(coffee.foam, 'Orta köpüklü').lower()}, "
                f"{SUGARS.get(coffee.sugar, 'Orta şekerli').lower()} · yaklaşık {seconds} saniye."
            )
        return f"Filtre kahve demleniyor · {coffee.cups} fincan · yaklaşık {int(seconds)} saniye."

    def _handle_local_ui_intent(self, text: str, speak: bool = False) -> bool:
        intent = local_ui_intent(text)
        if intent is None:
            return False
        handlers = {
            "map": self.open_map,
            "browser": self.open_browser,
            "settings": self.open_settings,
            "models": self.open_models,
            "commands": self.open_command_palette,
            "history": self.open_chat_history,
            "trace": self.open_task_trace,
            "session": self.open_task_session,
            "memory": self.open_memory_manager,
            "clipboard": self.open_clipboard,
            "reminders": self.open_reminders,
            "briefing": self.open_briefing,
            "time": self.open_time,
            "system": self.open_system,
            "timer": self.open_timer,
            "notes": self.open_notes,
            "calendar": self.open_calendar,
            "weather": self.open_weather,
            "book": self.open_book_camera,
            "home": self.open_home,
        }
        handler = handlers.get(intent)
        if handler is None:
            return False
        self.chat.add_message(text, True)
        handler()
        reply = _UI_INTENT_REPLIES[intent]
        self.chat.add_message(reply, False)
        self.progress_label.hide()
        if speak:
            self._set_state("speaking")
            self.speech.speak(reply)
        else:
            self._set_state("idle")
        return True

    def _trigger_voice_scene_analysis(self) -> None:
        if not self._voice_scene_pending:
            return
        if not self._camera_perception and self._voice_scene_tries == 0:
            self.enable_camera_perception(show=False, quiet=True)
        if not self._camera_perception:
            self._finish_voice_scene("Kamera açılamadı; görüntüyü analiz edemiyorum.")
            return
        window = self._ensure_camera_window()
        window.analyze_now(force=True)
        self._voice_scene_tries += 1
        if self._voice_scene_tries < 14 and self._voice_scene_pending:
            QTimer.singleShot(500, self._trigger_voice_scene_analysis)
        elif self._voice_scene_pending and not window.is_analyzing():
            self._finish_voice_scene("Kamera görüntüsü alınamadı; analiz yapılamadı.")

    def _finish_voice_scene(self, reply: str) -> None:
        if not self._voice_scene_pending:
            return
        self._voice_scene_pending = False
        self.progress_label.hide()
        self.chat.add_message(reply, False)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_subtitle(reply, "Jarvis")
        if self.speak_replies:
            self._set_state("speaking")
            self.speech.speak(reply)
        else:
            self._toast(reply, 7000)
            self._set_state("idle")

    def _on_voice_camera_scene(self, text: str) -> None:
        if not self._voice_scene_pending:
            return
        scene = str(text or "").strip()
        self._finish_voice_scene(
            f"Kamera analizi: {scene}" if scene else "Kamera analizi boş döndü."
        )

    def _on_voice_camera_scene_failed(self, error: str) -> None:
        if not self._voice_scene_pending:
            return
        message = str(error or "").strip() or "Kamera analizi başarısız."
        self._finish_voice_scene(f"Kamera analizi yapılamadı: {message[:300]}")

    def _update_camera_style(self) -> None:
        active = self._camera_perception
        if active and self.camera_hub.is_active():
            label = "Kamera tarıyor"
        elif active:
            label = "Kamera beklemede"
        else:
            label = "Kamera"
        self.camera_status.set_label(label)
        self.camera_status.set_active(active)
        nav = getattr(self, "nav_camera", None)
        if nav is not None:
            nav.set_active(active)

    # ---------- ekran bağlamı ----------

    def _screen_toolset(self):
        if self._screen_tools is None:
            from app.core.desktop import DesktopTools

            self._screen_tools = DesktopTools(self.settings.data_dir)
        return self._screen_tools

    def _capture_screen_image(self) -> Optional[str]:
        try:
            path, _message = self._screen_toolset().capture({})
            return path or None
        except Exception as error:  # noqa: BLE001 - kullanıcıya kısa bildirim
            self._toast(f"Ekran görüntüsü alınamadı: {error}", 5500)
            return None

    def _update_screen_style(self) -> None:
        chip = getattr(self, "screen_status", None)
        if chip is None:
            return
        chip.set_label("Ekran açık" if self._screen_context else "Ekran")
        chip.set_active(self._screen_context)

    def enable_screen_context(self) -> bool:
        if self._screen_context:
            return True
        probe = self._capture_screen_image()
        if probe is None:
            return False
        try:
            Path(probe).unlink(missing_ok=True)
        except OSError:
            pass
        self._screen_context = True
        self.settings.set_screen_context(True)
        self._update_screen_style()
        return True

    def disable_screen_context(self) -> None:
        if not self._screen_context:
            return
        self._screen_context = False
        self.settings.set_screen_context(False)
        self._update_screen_style()

    def toggle_screen_context(self) -> None:
        if self._screen_context:
            self.disable_screen_context()
            self._toast("Ekran bağlamı kapatıldı; görüntü artık isteklere eklenmiyor.")
            return
        if not self.enable_screen_context():
            return
        self._toast(
            "Ekran bağlamı açık: her komutta güncel ekran görüntüsü seçili modele eklenir.",
            5500,
        )

    def _camera_scene_summary(self) -> str:
        if not self._camera_perception:
            return ""
        try:
            return str(self._ensure_camera_window().last_scene or "")
        except RuntimeError:
            self.camera_window = None
            return ""

    def _prepare_camera_context(self) -> Tuple[Optional[str], str]:
        if not self._camera_perception:
            return None, ""
        window = self._ensure_camera_window()
        if not self.camera_hub.is_active():
            self.camera_hub.start()
        image_path = window.snapshot_for_request()
        if image_path is None:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and self._camera_perception:
                QApplication.processEvents()
                image_path = window.snapshot_for_request()
                if image_path is not None:
                    break
                time.sleep(0.04)
        return image_path, window.last_scene

    def _on_camera_hub_error(self, message: str) -> None:
        text = str(message or "").strip()
        if text:
            self._toast(f"Kamera: {text}", 5500)
        if self._camera_perception and not self.camera_hub.is_active():
            self._camera_perception = False
            if self.camera_window is not None:
                try:
                    self.camera_window.stop_perception()
                except RuntimeError:
                    self.camera_window = None
            self._update_camera_style()

    def _map_tools_service(self):
        from app.core.map_tools import MapTools

        tools = getattr(self, "_map_click_tools", None)
        if tools is None:
            tools = MapTools(self.settings)
            self._map_click_tools = tools
        return tools

    def _map_click_query(self, lat: float, lon: float, zoom: float) -> dict:
        return self._map_tools_service().click_nearby(lat, lon, zoom)

    def _map_category_query(self, category: str, lat: float, lon: float, zoom: float) -> dict:
        tools = self._map_tools_service()
        if category == "mall":
            try:
                zoom_value = int(float(zoom))
            except (TypeError, ValueError):
                zoom_value = 15
            radius = max(3000, min(12000, 2500 * (2 ** max(0, 15 - zoom_value))))
            result = tools.nearby(
                "avm",
                center={"lat": lat, "lon": lon, "name": "Harita merkezi"},
                radius=radius,
                limit=24,
                retries=0,
            )
            result["title"] = "Yakın AVM'ler"
            return result
        return tools.click_nearby(lat, lon, zoom)

    def _map_route_query(self, lat: float, lon: float, name: str) -> dict:
        tools = self._map_tools_service()
        home = tools.home()
        destination = {
            "name": str(name or "Hedef").strip() or "Hedef",
            "lat": float(lat),
            "lon": float(lon),
        }
        return tools.route(home, destination, "driving")

    def _on_map_view(self, payload: dict) -> None:
        if not payload:
            return
        if payload.get("kind") == "home" and payload.get("home"):
            self._on_home_changed(payload["home"])
        self.open_map(payload)

    def _on_book_view(self, payload: dict) -> None:
        if isinstance(payload, dict) and payload.get("kind") == "camera":
            self.open_book_camera()

    def _on_home_changed(self, home: dict) -> None:
        try:
            self.mini_map.set_home(home)
            self._set_map_caption(home)
        except RuntimeError:
            pass

    def open_models(self) -> None:
        self._show_modal(ModelsModal(self, self.settings))

    def _refresh_weather(self) -> None:
        self._weather_wake.set()
        self._toast("Hava durumu güncelleniyor…")

    def open_settings(self) -> None:
        previous_camera = self.settings.camera_index
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            provider = self.settings.provider_label
            model = self.settings.active_model or "otomatik (:free)"
            self.model_status.setText(f"{provider} · {model} · Ses {self.speech.voice_name}")
            self.speech.apply_settings()
            self.camera_hub.set_device_index(self.settings.camera_index)
            if self.settings.camera_index != previous_camera and self.camera_hub.is_active():
                self.camera_hub.stop()
                self.camera_hub.start(self.settings.camera_index)
            if self.mic_enabled:
                self._restart_voice_session()
            mode = "tam erişim" if self.settings.access_mode == "full" else "onaylı"
            self._on_home_changed(self.settings.home_location)
            self._weather_wake.set()
            self._toast(f"Ayarlar kaydedildi. Erişim: {mode}.", 5000)
            if getattr(dialog, "theme_changed", False):
                self._offer_theme_restart(dialog.theme_name)

    def _cycle_theme(self) -> None:
        current = normalize_theme(self.settings.theme)
        try:
            index = THEME_ORDER.index(current)
        except ValueError:
            index = -1
        key = THEME_ORDER[(index + 1) % len(THEME_ORDER)]
        self.settings.set_theme(key)
        label = THEMES[key].get("NAME", key)
        self._toast(f"Tema: {label} · yeniden başlatınca uygulanır.", 4200)

    def _on_chat_suggestion(self, text: str) -> None:
        if self.request_active:
            return
        prompt = str(text or "").strip()
        if not prompt:
            return
        self.send_message(prompt)

    def _offer_theme_restart(self, theme_name: str) -> None:
        label = THEMES[normalize_theme(theme_name)].get("NAME", "Seçili")
        if getattr(self, "request_active", False):
            self._toast(f"{label} tema kaydedildi; görev bitince yeniden başlatınca uygulanacak.", 6500)
            return
        answer = QMessageBox.question(
            self,
            "Tema değişti",
            f"{label} tema kaydedildi. Tüm arayüze uygulanması için Jarvis şimdi yeniden başlatılsın mı?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._toast(f"{label} tema sonraki açılışta uygulanacak.", 5000)
            return
        root = str(Path(__file__).resolve().parents[2])
        try:
            subprocess.Popen([sys.executable, *sys.argv], cwd=root)
        except Exception as error:
            self._toast(f"Yeniden başlatılamadı: {error}", 6500)
            return
        self._closing = True
        self.close()

    # ---------- istek akışı ----------

    def send_message(self, text: Optional[str] = None, from_voice: bool = False) -> None:
        if self.request_active:
            if not from_voice:
                self._toast("Jarvis hâlâ çalışıyor; DUR ile iptal edebilirsin.")
                self.input.setFocus()
            return
        prompt = (text if text is not None else self.input.text()).strip()
        if not prompt:
            return
        pulse = getattr(self.send_button, "pulse", None)
        if callable(pulse):
            pulse()
        if self._modal is not None:
            try:
                self._modal.close_modal()
            except RuntimeError:
                pass

        if self._handle_screen_voice_command(prompt, speak=self.speak_replies):
            if text is None:
                self.input.clear()
            self.input.setFocus()
            return
        if self._handle_camera_voice_command(prompt, speak=self.speak_replies):
            if text is None:
                self.input.clear()
            self.input.setFocus()
            return
        if self._handle_home_voice_command(prompt, speak=self.speak_replies):
            if text is None:
                self.input.clear()
            self.input.setFocus()
            return
        if self._handle_local_ui_intent(prompt, speak=self.speak_replies):
            if text is None:
                self.input.clear()
            self.input.setFocus()
            return

        screen_query = screen_context_needed(prompt)
        if self._screen_context or screen_query:
            screen_image = self._capture_screen_image()
        else:
            screen_image = None
        if camera_context_needed(prompt):
            folded = _fold_turkish(prompt)
            camera_phrase = any(
                root in folded for root in ("kamera", "etraf", "beni", "elim", "uzerim", "onum")
            )
            if screen_query and not camera_phrase:
                camera_image, scene_summary = None, self._camera_scene_summary()
            else:
                camera_image, scene_summary = self._prepare_camera_context()
        else:
            camera_image, scene_summary = None, self._camera_scene_summary()
        self.speech.stop()
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_subtitle(prompt, "Sen")
        self.chat.add_message(prompt, True)
        if text is None:
            self.input.clear()
        self.current_streaming_message = None
        self._stream_text = ""
        self._cancel_requested = False
        self._task_takeover = False
        self._pending_voice_text = None
        self._voice_speech_limit = (
            spoken_limit_for_prompt(prompt) if (from_voice or self.speak_replies) else None
        )
        self.progress_label.hide()
        self._set_state("thinking")
        self._set_connection("● Görev yürütülüyor", U.GOLD)
        self.stop_button.show()
        self.stop_button.setEnabled(True)
        self.pause_button.set_glyph("pause")
        self.pause_button.setToolTip("Görevi duraklat")
        self.pause_button.set_active(False)
        self.pause_button.setEnabled(True)
        self.pause_button.show()
        self.send_button.setEnabled(False)
        self.mic_button.setEnabled(False)
        self.attachment_label.hide()

        self.thread = QThread(self)
        self.worker = AIWorker(self.storage, self.settings, hud_store=self.hud_store, home=self.smart_home)
        self.worker.set_prompt(prompt)
        self.worker.set_speak_response(from_voice)
        self.worker.set_attachments(self.attached_files)
        self.worker.set_camera_context(camera_image, scene_summary)
        self.worker.set_screen_context(screen_image)
        self.attached_files = []
        self.worker.moveToThread(self.thread)

        worker = self.worker
        thread = self.thread
        self.thread.started.connect(worker.run)
        worker.chunk.connect(self._on_chunk)
        worker.response.connect(self._on_response)
        worker.error.connect(self._on_error)
        worker.progress.connect(self._on_progress)
        worker.approval_requested.connect(self._on_approval)
        worker.paused.connect(self._on_task_paused)
        worker.resumed.connect(self._on_task_resumed)
        worker.takeover_requested.connect(self._on_task_takeover)
        worker.map_view.connect(self._on_map_view)
        worker.book_view.connect(self._on_book_view)
        worker.browser_view.connect(self.open_browser)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(
            lambda w=worker, t=thread: self._on_thread_finished(w, t),
            Qt.ConnectionType.QueuedConnection,
        )
        thread.finished.connect(thread.deleteLater)

        self.request_active = True
        seconds_limit = getattr(self.settings, "agent_seconds_limit", 0) or 0
        if seconds_limit > 0:
            self._watchdog.start((seconds_limit + 25) * 1000)
        thread.start()
        self.input.setFocus()

    def _on_chunk(self, chunk: str) -> None:
        if not self.request_active:
            return
        self._stream_text += chunk
        hud = getattr(self, "floating_hud", None)
        if hud is not None and self._stream_text.strip():
            hud.set_subtitle(self._stream_text, "Jarvis")
        if self.current_streaming_message is None:
            self.current_streaming_message = self.chat.add_message(self._stream_text, False)
            self.current_streaming_message.set_sender("Jarvis · yazıyor")
        else:
            self.chat.update_message(self.current_streaming_message, self._stream_text)

    def _on_response(self, text: str) -> None:
        if not self.request_active:
            return
        self.hide_busy()
        text = str(text or "").strip()
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_subtitle(text or "Yanıt alınamadı.", "Jarvis")
        self.progress_label.hide()
        if self.current_streaming_message is not None:
            if text:
                self.chat.update_message(self.current_streaming_message, text, rich=True)
            else:
                self.chat.update_message(self.current_streaming_message, "Yanıt alınamadı.")
            self.current_streaming_message.set_sender("Jarvis")
            self.current_streaming_message = None
        elif text:
            entry = self.chat.add_message(text, False)
            entry.set_text(text, rich=True)
        if not text:
            self.chat.add_message("Yanıt boş döndü. Komutu farklı bir ifadeyle tekrar deneyebilirsin.", False)
        self._stream_text = ""
        self._set_connection("● Yanıt hazır", U.RED_SOFT)
        if self.speak_replies and text:
            self._set_state("speaking")
            self.speech.speak(text, max_chars=self._voice_speech_limit)
        elif not self.mic_enabled:
            self._set_state("idle")
        self._voice_speech_limit = None

    def _on_error(self, text: str) -> None:
        self.hide_busy()
        self.progress_label.hide()
        message = f"HATA · {text}"
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_state("error")
            hud.set_subtitle(message, "Jarvis")
        if self.current_streaming_message is not None:
            self.chat.update_message(self.current_streaming_message, message)
            self.current_streaming_message.set_sender("Jarvis")
            self.current_streaming_message = None
        else:
            self.chat.add_message(message, False)
        self._stream_text = ""

    def _on_progress(self, text: str) -> None:
        if not self.request_active:
            return
        self.progress_label.setText(str(text))
        self.progress_label.show()
        self.show_busy(text)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_subtitle(f"Görev · {text}", "Jarvis")

    def _on_approval(self, description: str) -> None:
        answer = QMessageBox.question(
            self,
            "İşlem onayı",
            f"Jarvis bu işlemi gerçekleştirmek istiyor:\n\n{description}\n\nOnaylıyor musun?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if self.worker is not None:
            self.worker.set_approval(answer == QMessageBox.StandardButton.Yes)

    def _on_task_paused(self, reason: str) -> None:
        if not self.request_active:
            return
        self.pause_button.set_glyph("play")
        self.pause_button.setToolTip("Görevi sürdür")
        self.pause_button.set_active(True)
        self.progress_label.setText("Duraklatıldı · Devam için ▶")
        self.progress_label.show()
        self.show_busy("Duraklatıldı · Devam bekleniyor")
        self._set_connection("● Görev duraklatıldı", U.GOLD)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            prefix = "Kontrol sende" if self._task_takeover else "Duraklatıldı"
            hud.set_subtitle(f"{prefix} · {reason}", "Jarvis")

    def _on_task_resumed(self) -> None:
        if not self.request_active:
            return
        self._task_takeover = False
        self.pause_button.set_glyph("pause")
        self.pause_button.setToolTip("Görevi duraklat")
        self.pause_button.set_active(False)
        self.progress_label.hide()
        self.show_busy("Görev sürüyor · Devam")
        self._set_connection("● Görev yürütülüyor", U.GOLD)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_subtitle("Görev sürüyor · Devam", "Jarvis")
        self._toast("Görev sürdürülüyor.", 3000)

    def _on_task_takeover(self, message: str) -> None:
        if not self.request_active:
            return
        self._task_takeover = True
        self.chat.add_message(f"Kontrol sende: {message}", False)
        self._toast("Kontrol sende. İşlemi tamamlayınca DEVAM ET'e bas.", 9000)

    def _reset_pause_button(self) -> None:
        self._task_takeover = False
        self.pause_button.set_glyph("pause")
        self.pause_button.setToolTip("Görevi duraklat")
        self.pause_button.set_active(False)
        self.pause_button.setEnabled(True)
        self.pause_button.hide()

    def _on_thread_finished(self, worker: AIWorker, thread: QThread) -> None:
        if self.worker is not worker:
            self._stale_workers = [
                (stale_thread, stale_worker)
                for stale_thread, stale_worker in self._stale_workers
                if stale_thread is not thread
            ]
            return
        self.worker = None
        self.thread = None
        self.request_active = False
        self.hide_busy()
        self._watchdog.stop()
        self.stop_button.hide()
        self._reset_pause_button()
        self.send_button.setEnabled(True)
        self.mic_button.setEnabled(True)
        self.progress_label.hide()
        self._set_connection("● Sistem hazır", U.DIM)
        self._refresh_notes_panel()
        self._refresh_calendar_panel()
        self.input.setFocus()
        self._voice_speech_limit = None
        if self._cancel_requested:
            self._cancel_requested = False
            self.chat.add_message("İstek durduruldu. Arayüz yeniden hazır.", False)
        pending = self._pending_voice_text
        self._pending_voice_text = None
        if pending and self.mic_enabled:
            QTimer.singleShot(
                140,
                lambda text=pending: None if self.request_active else self.send_message(text, from_voice=True),
            )
            return
        if not self.mic_enabled:
            self._set_state("idle")
        elif not self.speech.busy:
            self._set_state("listening")
            self._maybe_listen(200)

    # ---------- iptal / zaman aşımı ----------

    def cancel_current_request(self) -> None:
        if not self.request_active and not self.speech.busy:
            return
        self.speech.stop()
        self._cancel_requested = True
        if self.worker is not None:
            try:
                self.worker.cancel()
            except RuntimeError:
                pass
        if self.current_streaming_message is not None:
            if not self._stream_text.strip():
                self.chat.update_message(self.current_streaming_message, "Durduruldu.")
            self.current_streaming_message.set_sender("Jarvis")
            self.current_streaming_message = None
        self.progress_label.setText("Durduruluyor…")
        self.progress_label.show()
        self.show_busy("Durduruluyor · Görev kapatılıyor")
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self._toast("Durdurma isteği gönderildi…")
        if self.request_active:
            QTimer.singleShot(2600, self._force_release_if_stuck)
        elif self.speech.busy:
            self._set_state("idle" if not self.mic_enabled else "listening")

    def _force_release_if_stuck(self) -> None:
        if not self.request_active:
            return
        worker = self.worker
        thread = self.thread
        if worker is not None:
            try:
                worker.cancel()
            except RuntimeError:
                pass
        self._detach_current()
        self.chat.add_message(
            "İstek arka planda iptal edildi; Jarvis'in işlem yetkisi kapatıldı. Arayüz yeniden hazır.",
            False,
        )
        self._toast("Arayüz serbest bırakıldı.", 5000)
        if not self.mic_enabled:
            self._set_state("idle")
        else:
            self._maybe_listen(700)

    def _on_watchdog(self) -> None:
        if not self.request_active:
            return
        if self.worker is not None:
            try:
                self.worker.cancel()
            except RuntimeError:
                pass
        self._detach_current()
        seconds_limit = getattr(self.settings, "agent_seconds_limit", 0) or 0
        self.chat.add_message(
            f"İşlem {seconds_limit} saniyelik ajan bütçesini aştı ve güvenli biçimde durduruldu.",
            False,
        )
        self._toast("Görev zaman aşımına uğradı.", 6000)
        self._set_state("idle")

    def _detach_current(self) -> None:
        worker = self.worker
        thread = self.thread
        if worker is not None:
            for signal in (
                worker.chunk,
                worker.response,
                worker.error,
                worker.progress,
                worker.approval_requested,
                worker.paused,
                worker.resumed,
                worker.takeover_requested,
            ):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            try:
                worker.cancel()
            except RuntimeError:
                pass
        if thread is not None and worker is not None:
            self._stale_workers.append((thread, worker))
        self.worker = None
        self.thread = None
        self.request_active = False
        self.hide_busy()
        self._watchdog.stop()
        self.current_streaming_message = None
        self._stream_text = ""
        self.stop_button.hide()
        self._reset_pause_button()
        self.send_button.setEnabled(True)
        self.mic_button.setEnabled(True)
        self.progress_label.hide()
        self._set_connection("● Sistem hazır", U.DIM)
        self.input.setFocus()

    # ---------- ses ----------

    def toggle_mic(self) -> None:
        self.mic_enabled = not self.mic_enabled
        self._voice_session += 1
        if self.mic_enabled:
            self._no_speech_count = 0
            self._pending_voice_text = None
            self.speech.stop()
            self._set_state("listening")
            self._update_mic_style()
            self._start_listening()
        else:
            self._stop_voice_session()
            self.speech.stop()
            self._update_mic_style()
            self._set_state("thinking" if self.request_active else "idle")
            self._toast("Mikrofon kapatıldı; sesli yanıt da durduruldu.")
        for button in (getattr(self, "mic_button", None), getattr(self, "rail_mic", None)):
            pulse = getattr(button, "pulse", None)
            if callable(pulse):
                pulse()

    def _update_mic_style(self) -> None:
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_mic_enabled(self.mic_enabled)
            hud.set_mic_level(0.0)
        meter = getattr(self, "mic_meter", None)
        if meter is not None:
            meter.set_enabled(self.mic_enabled)
            if not self.mic_enabled:
                meter.set_level(0.0)
        label = "Mikrofon açık" if self.mic_enabled else "Mikrofon kapalı"
        self.mic_status.set_label(label)
        self.mic_status.set_active(self.mic_enabled)
        rail_mic = getattr(self, "rail_mic", None)
        if rail_mic is not None:
            rail_mic.set_active(self.mic_enabled)
        if not hasattr(self, "mic_button"):
            return
        self.mic_button.set_active(self.mic_enabled)
        self.mic_button.setToolTip(
            "Mikrofonu kapat (F4)" if self.mic_enabled else "Mikrofonu aç (F4)"
        )

    def toggle_voice_replies(self) -> None:
        self.speak_replies = not self.speak_replies
        self.settings.set_voice_replies(self.speak_replies)
        self._update_speak_style()
        if not self.speak_replies:
            self.speech.stop()
            if not self.request_active and not self.mic_enabled:
                self._set_state("idle")
        label = "Sesli yanıt açık" if self.speak_replies else "Sesli yanıt kapalı"
        self._toast(f"{label} · yazılı sohbet her zaman tam metni gösterir.", 3500)

    def _update_speak_style(self) -> None:
        chip = getattr(self, "speak_status", None)
        if chip is None:
            return
        chip.set_label("Ses açık" if self.speak_replies else "Ses kapalı")
        chip.set_active(self.speak_replies)
        chip.setToolTip(
            "Sesli yanıtları kapat" if self.speak_replies else "Sesli yanıtları aç"
        )

    def _maybe_listen(self, delay: int = 500) -> None:
        if self._voice_scene_pending:
            return
        if not self.mic_enabled or self.voice_thread is not None or self._closing:
            return
        QTimer.singleShot(delay, self._start_listening)

    def _start_listening(self) -> None:
        if self._voice_scene_pending:
            return
        if not self.mic_enabled or self.voice_thread is not None or self._closing:
            return
        token = self._voice_session
        self.mic_status.set_label("Dinliyorum")
        self.mic_status.set_active(True)
        if not self.request_active and not self.speech.busy:
            self._set_state("listening")
        thread = QThread(self)
        worker = ConversationWorker(
            device_name=self.settings.mic_device,
            barge_in=self.settings.voice_headphones,
        )
        self.voice_thread = thread
        self.voice_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Lambda'ların alıcı bağlamı yok; sinyaller işçi iş parçacığına
        # düşüp arayüzü orada oluşturuyordu (Qt yığını bozulması).
        bridge = _VoiceBridge(self, token, worker, thread)
        self._voice_bridge = bridge
        worker.heard.connect(bridge.heard)
        worker.error.connect(bridge.error)
        worker.level.connect(bridge.level)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(bridge.finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _stop_voice_session(self) -> None:
        self._voice_session += 1
        worker = self.voice_worker
        thread = self.voice_thread
        self.voice_worker = None
        self.voice_thread = None
        self._drop_voice_bridge()
        self.orb.set_input_level(0.0)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_mic_level(0.0)
        meter = getattr(self, "mic_meter", None)
        if meter is not None:
            meter.set_level(0.0)
        if worker is not None:
            try:
                worker.stop()
            except RuntimeError:
                pass
        if thread is not None:
            try:
                thread.quit()
            except RuntimeError:
                pass

    def _restart_voice_session(self) -> None:
        if self.voice_thread is None:
            return
        self._stop_voice_session()
        QTimer.singleShot(250, self._maybe_listen)

    def _drop_voice_bridge(self) -> None:
        bridge = self._voice_bridge
        self._voice_bridge = None
        if bridge is not None:
            bridge.deleteLater()

    def _on_voice_level(self, level: float, token: int) -> None:
        if token != self._voice_session or not self.mic_enabled:
            return
        hud = getattr(self, "floating_hud", None)
        meter = getattr(self, "mic_meter", None)
        if meter is not None:
            meter.set_level(level)
        if self._state == "listening":
            self.orb.set_input_level(level)
            if hud is not None:
                hud.set_mic_level(level)

    def _on_voice_result(self, text: str, token: int) -> None:
        if token != self._voice_session or not self.mic_enabled:
            return
        self._no_speech_count = 0
        text = str(text or "").strip()
        if not text:
            return
        meter = getattr(self, "mic_meter", None)
        if meter is not None:
            meter.pulse()
        interrupted = self.speech.busy
        if interrupted:
            self.speech.stop()
            self._set_state("listening")
        if self.request_active:
            if interrupted:
                self._pending_voice_text = text
            return
        self._update_mic_style()
        if self._handle_screen_voice_command(text, speak=self.speak_replies):
            return
        if self._handle_camera_voice_command(text, speak=self.speak_replies):
            return
        if self._handle_home_voice_command(text, speak=self.speak_replies):
            return
        if self._handle_local_ui_intent(text, speak=self.speak_replies):
            return
        self._set_state("thinking")
        self.send_message(text, from_voice=True)

    def _on_voice_error(self, text: str, token: int) -> None:
        if token != self._voice_session or not self.mic_enabled:
            return
        message = str(text or "").strip()
        self.mic_enabled = False
        self.orb.set_input_level(0.0)
        meter = getattr(self, "mic_meter", None)
        if meter is not None:
            meter.set_level(0.0)
        self._update_mic_style()
        self._set_state("idle")
        self._toast(f"Mikrofon: {message}", 6500)

    def _on_voice_finished(self, worker: ConversationWorker, thread: QThread) -> None:
        bridge = self._voice_bridge
        if bridge is not None and getattr(bridge, "_worker", None) is worker:
            self._drop_voice_bridge()
        if self.voice_worker is not None and self.voice_worker is not worker:
            return
        if self.voice_worker is worker:
            self.voice_worker = None
            self.voice_thread = None
        if self._closing:
            return
        if not self.mic_enabled:
            if not self.request_active:
                self._set_state("idle")
            return
        if self.request_active or self.speech.busy:
            QTimer.singleShot(900, self._maybe_listen)
        else:
            QTimer.singleShot(350, self._start_listening)

    def _on_speech_started(self) -> None:
        self._set_state("speaking")
        worker = self.voice_worker
        if worker is not None:
            try:
                worker.set_speaking(True, self.speech.spoken_text)
            except RuntimeError:
                pass

    def _on_speech_finished(self) -> None:
        worker = self.voice_worker
        if worker is not None:
            try:
                worker.set_speaking(False)
            except RuntimeError:
                pass
        if self.request_active:
            return
        if self.mic_enabled:
            self._set_state("listening")
            self._maybe_listen(150)
        else:
            self._set_state("idle")

    def _on_speech_error(self, text: str) -> None:
        self._toast(str(text), 5000)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_state("error")
            hud.set_subtitle(f"Ses hatası · {text}", "Jarvis")

    # ---------- yüzen HUD ----------

    def _create_floating_hud(self) -> None:
        self.floating_hud = FloatingHud(self.settings)
        self.floating_hud.restore_requested.connect(self._restore_from_floating_hud)
        self.floating_hud.set_mic_enabled(self.mic_enabled)
        self.floating_hud.set_state(self._state)

    def _sync_floating_hud_for_state(self, minimized: bool, was_maximized: bool = False) -> None:
        hud = getattr(self, "floating_hud", None)
        if hud is None:
            return
        if not minimized:
            hud.hide_for_restore()
            return
        self._restore_maximized = bool(was_maximized)
        hud.set_state(getattr(self, "_state", "idle"))
        hud.set_mic_enabled(bool(getattr(self, "mic_enabled", False)))
        active = bool(getattr(self, "_camera_perception", False))
        scene = ""
        count = 0
        window = getattr(self, "camera_window", None)
        if active and window is not None:
            try:
                scene = str(getattr(window, "last_scene", "") or "")
                count = int(getattr(window, "last_box_count", 0) or 0)
            except RuntimeError:
                window = None
        hud.set_camera(scene, count, active)
        hud.show_for_minimize()

    def _update_floating_hud_camera(self, text: str = "", count: int = 0, active=None) -> None:
        hud = getattr(self, "floating_hud", None)
        if hud is None:
            return
        if active is None:
            active = bool(getattr(self, "_camera_perception", False))
        hud.set_camera(text, count, active)

    def _on_camera_scene_hud(self, text: str) -> None:
        count = 0
        window = getattr(self, "camera_window", None)
        if window is not None:
            try:
                count = int(getattr(window, "last_box_count", 0) or 0)
            except RuntimeError:
                window = None
        self._update_floating_hud_camera(text, count)

    def _on_camera_scene_failed_hud(self, _error: str) -> None:
        self._update_floating_hud_camera("", 0)

    def _restore_from_floating_hud(self) -> None:
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.hide_for_restore()
        if self.isMinimized():
            self.showNormal()
            if self._restore_maximized:
                self.showMaximized()
        else:
            self.show()
        self.raise_()
        fade_window_in(self, 190)
        self.activateWindow()
        self.input.setFocus()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange or self._closing:
            return
        old_state = getattr(event, "oldState", lambda: Qt.WindowState.WindowNoState)()
        self._sync_floating_hud_for_state(
            self.isMinimized(),
            bool(old_state & Qt.WindowState.WindowMaximized),
        )

    # ---------- durum ----------

    def _set_state(self, state: str) -> None:
        self._state = state
        self.orb.set_state(state)
        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            hud.set_state(state)
            if state != "listening":
                hud.set_mic_level(0.0)
        if state != "listening":
            self.orb.set_input_level(0.0)
        hint = STATE_HINTS.get(state, STATE_HINTS["idle_off"])
        if state == "idle":
            hint = STATE_HINTS["idle_on"] if self.mic_enabled else STATE_HINTS["idle_off"]
        self.status_label.setText(hint)
        dot = getattr(self, "state_dot", None)
        if dot is not None:
            dot.set_color(state_color(state))
            dot.set_active(state != "idle")
        if state in ("thinking", "listening"):
            self._set_connection(f"● {STATE_LABELS.get(state, '')}", U.GOLD)
        elif state == "speaking":
            self._set_connection("● Sesli yanıt", U.RED_SOFT)
        elif state == "idle":
            self._set_connection("● Sistem hazır", U.DIM)
        self.progress_label.hide()

    def _set_connection(self, text: str, color: str) -> None:
        clean = str(text or "").replace("●", "").strip()
        self.connection_status.setText(clean)
        self.connection_status.setStyleSheet(f"color:{color};background:transparent;border:none;")
        dot = getattr(self, "connection_dot", None)
        if dot is not None:
            dot.set_color(color)

    def _escape(self) -> None:
        if self._modal is not None:
            try:
                self._modal.close_modal()
            except RuntimeError:
                pass
            return
        if self.request_active or self.speech.busy:
            self.cancel_current_request()
            return
        if self.mic_enabled:
            self.toggle_mic()

    # ---------- dosya / pencere ----------

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Dosya seçin",
            "",
            "Desteklenen dosyalar (*.txt *.md *.py *.json *.csv *.log *.xml *.html *.pdf "
            "*.docx *.xlsx *.pptx *.odt *.epub *.png *.jpg *.jpeg *.webp);;Tüm dosyalar (*)",
        )
        if not path:
            return
        self.attached_files = [path]
        self.attachment_label.setText(f"Ek dosya · {Path(path).name}  ·  Gönderince eklenecek")
        self.attachment_label.show()
        self._toast(f"Dosya eklendi: {Path(path).name}")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._launch_effect_played:
            self._launch_effect_played = True
            fade_window_in(self, 280)
            self._launch_overlay = play_launch_effect(self)
            overlay = self._launch_overlay
            if overlay is not None:
                overlay.hold()
                overlay.finished.connect(self._on_launch_overlay_finished)
        self._reposition_camera_window()
        if self._modal is None:
            self._set_ambient_animations(True)
        if not self._dark_title_set:
            self._dark_title_set = True
            QTimer.singleShot(80, self._apply_dark_titlebar)
        if not self._monitors_scheduled:
            self._monitors_scheduled = True
            QTimer.singleShot(2000, self._start_monitors)
        if not self._camera_autostart_scheduled:
            self._camera_autostart_scheduled = True
            QTimer.singleShot(1500, self._auto_start_camera_perception)
        if not self._browser_prewarmed:
            self._browser_prewarmed = True
            QTimer.singleShot(120, self._prewarm_browser)

    def _prewarm_browser(self) -> None:
        from .browser_service import browser_service

        try:
            service = browser_service()
            service.prewarm()
            service.map_ready.connect(self._release_launch_overlay)
        except Exception:
            self._release_launch_overlay(False)
        # Harita ve tarayıcı açılış ekranı sürerken gizli kurulur; ilk tıklamada
        # Chromium yüzeyi yeniden doğmadığı için uygulama yeniden açılıyormuş gibi durmaz.
        try:
            self._ensure_map_modal()
        except Exception:
            self._release_launch_overlay(False)
        QTimer.singleShot(900, self._prewarm_browser_modal)

    def _release_launch_overlay(self, ok: bool = True) -> None:
        overlay = self._launch_overlay
        if overlay is None:
            return
        try:
            overlay.release()
        except RuntimeError:
            self._launch_overlay = None

    def _on_launch_overlay_finished(self) -> None:
        self._launch_overlay = None

    def _prewarm_browser_modal(self) -> None:
        try:
            self._ensure_browser_modal()
        except Exception:
            pass

    def _on_news_items(self, items) -> None:
        if not items or not self.isVisible():
            return
        self.news_stage.show_items(items)

    def hideEvent(self, event):
        self._set_ambient_animations(False)
        try:
            self.news_stage.dismiss()
        except (AttributeError, RuntimeError):
            pass
        super().hideEvent(event)

    def _apply_dark_titlebar(self) -> None:
        try:
            import ctypes

            hwnd = int(self.winId())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _set_window_icon(self) -> None:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QRadialGradient(32, 32, 30)
        gradient.setColorAt(0.0, QColor(U.RED_GLOW))
        gradient.setColorAt(0.2, QColor(U.RED))
        gradient.setColorAt(0.7, QColor(U.RED_SOFT))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(2, 2, 60, 60)
        painter.end()
        self.setWindowIcon(QIcon(pixmap))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._background_cache = None
        if self._modal is not None:
            try:
                self._modal.setGeometry(self.rect())
            except RuntimeError:
                pass
        if self.toast.isVisible():
            self._position_toast()
        self._position_busy()
        self._reposition_camera_window()

    def _toast_target(self) -> QPoint:
        self.toast.adjustSize()
        x = max(20, (self.width() - self.toast.width()) // 2)
        y = max(80, self.height() - 190)
        return QPoint(x, y)

    def _position_toast(self) -> None:
        self.toast.move(self._toast_target())

    def _position_busy(self) -> None:
        busy = getattr(self, "busy", None)
        if busy is None:
            return
        busy.adjustSize()
        x = max(12, (self.width() - busy.width()) // 2)
        busy.move(x, HEADER_HEIGHT + 10)

    def show_busy(self, text: str = "") -> None:
        if self._closing:
            return
        self.busy.show_busy(text)
        self._position_busy()

    def hide_busy(self) -> None:
        self.busy.hide_busy()

    def _stop_toast_animation(self) -> None:
        animation = self._toast_anim
        self._toast_anim = None
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
            animation.deleteLater()

    def _animate_toast(self, target: QPoint, show: bool, on_finished=None) -> None:
        self._stop_toast_animation()
        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(self._toast_effect, b"opacity", group)
        fade.setDuration(190 if show else 150)
        fade.setStartValue(float(self._toast_effect.opacity()))
        fade.setEndValue(1.0 if show else 0.0)
        fade.setEasingCurve(
            QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic
        )
        group.addAnimation(fade)
        move = QPropertyAnimation(self.toast, b"pos", group)
        move.setDuration(230 if show else 170)
        move.setStartValue(self.toast.pos())
        move.setEndValue(target)
        move.setEasingCurve(
            QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic
        )
        group.addAnimation(move)
        if on_finished is not None:
            group.finished.connect(on_finished)
        group.finished.connect(lambda current=group: self._clear_toast_animation(current))
        self._toast_anim = group
        group.start()

    def _clear_toast_animation(self, group) -> None:
        if self._toast_anim is group:
            self._toast_anim = None
        group.deleteLater()

    def show_toast(self, text: str, milliseconds: int = 3600) -> None:
        self._toast_token += 1
        token = self._toast_token
        self.toast.setText(text)
        target = self._toast_target()
        visible = self.toast.isVisible() and self._toast_effect.opacity() > 0.05
        if not visible:
            self.toast.move(target + QPoint(0, 12))
            self._toast_effect.setOpacity(0.0)
        self.toast.show()
        self.toast.raise_()
        self._animate_toast(target, show=True)
        QTimer.singleShot(milliseconds, lambda: self._hide_toast(token))

    def _hide_toast(self, token: int) -> None:
        if token != self._toast_token or not self.toast.isVisible():
            return
        self._animate_toast(
            self._toast_target() + QPoint(0, 8),
            show=False,
            on_finished=self.toast.hide,
        )

    def _toast(self, text: str, milliseconds: int = 3600) -> None:
        self.show_toast(text, milliseconds)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cache = self._background_cache
        if cache is None or cache.deviceIndependentSize().toSize() != self.size():
            dpr = self.devicePixelRatioF()
            cache = QPixmap(
                max(1, int(self.width() * dpr)),
                max(1, int(self.height() * dpr)),
            )
            cache.setDevicePixelRatio(dpr)
            cache.fill(QColor(U.BACKGROUND))
            cache_painter = QPainter(cache)
            cache_painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            center = QPoint(int(self.width() / 2), int(self.height() * 0.42))
            halo = QRadialGradient(center, max(self.width(), self.height()) * 0.62)
            if U.IS_LIGHT:
                halo.setColorAt(0.0, QColor(255, 255, 255, 150))
                halo.setColorAt(0.45, QColor(255, 255, 255, 70))
                halo.setColorAt(1.0, QColor(255, 255, 255, 0))
            else:
                accent = QColor(U.RED)
                halo.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 16))
                halo.setColorAt(0.45, QColor(accent.red(), accent.green(), accent.blue(), 6))
                halo.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
            cache_painter.fillRect(self.rect(), halo)

            vignette = QRadialGradient(center, max(self.width(), self.height()) * 0.78)
            vignette.setColorAt(0.6, QColor(0, 0, 0, 0))
            vignette.setColorAt(1.0, QColor(0, 0, 0, 18 if U.IS_LIGHT else 90))
            cache_painter.fillRect(self.rect(), vignette)
            cache_painter.end()
            self._background_cache = cache
        painter.drawPixmap(0, 0, cache)

    def _play_close_animation(self) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide_busy()
        self._stop_toast_animation()
        hud = getattr(self, "floating_hud", None)
        if hud is not None and hud.isVisible():
            fade_window_out(hud, 180)
        fade_window_out(self, 210, self._finish_close_animation)

    def _finish_close_animation(self) -> None:
        try:
            self.close()
        except RuntimeError:
            pass

    @staticmethod
    def _thread_running(thread: Optional[QThread]) -> bool:
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            return False

    @staticmethod
    def _force_exit_if_running(threads) -> None:
        for thread in threads:
            try:
                if thread.isRunning():
                    os._exit(0)
            except RuntimeError:
                os._exit(0)

    def closeEvent(self, event):
        if self.request_active and not self._close_animation_started:
            answer = QMessageBox.question(
                self,
                "Jarvis çalışıyor",
                "Jarvis hâlâ bir görev yürütüyor. Yine de kapatılsın mı?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if not self._close_animation_started and self.isVisible():
            self._close_animation_started = True
            event.ignore()
            self._play_close_animation()
            return
        if self._closing and getattr(self, "_cleanup_done", False):
            event.accept()
            return
        animation = getattr(self, "_window_fade", None)
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
            self._window_fade = None
        self._closing = True
        if self._focus_animation is not None:
            self._focus_animation.stop()
            self._focus_animation = None
        self._clock_timer.stop()
        self._timer_engine_tick.stop()
        self._watchdog.stop()
        self._reminder_timer.stop()
        self._companion_timer.stop()
        self._voice_scene_pending = False
        self._camera_manual_off = True
        try:
            self.disable_camera_perception(quiet=True)
        except Exception:
            pass
        self.camera_hub.stop()
        self._weather_stop.set()
        self._weather_wake.set()
        self._system_stop.set()
        self.speech.shutdown()

        lingering = []
        voice_thread = self.voice_thread
        self._stop_voice_session()
        if voice_thread is not None:
            try:
                voice_thread.quit()
                voice_thread.wait(350)
            except Exception:
                pass
            if self._thread_running(voice_thread):
                lingering.append(voice_thread)

        if self.worker is not None:
            try:
                self.worker.cancel()
            except RuntimeError:
                pass
        thread = self.thread
        if thread is not None and self._thread_running(thread):
            try:
                thread.quit()
                thread.wait(700)
            except Exception:
                pass
            if self._thread_running(thread):
                lingering.append(thread)

        for stale in lingering:
            try:
                stale.setParent(None)
            except RuntimeError:
                pass
            _ORPHAN_THREADS.append(stale)
        if lingering:
            # QThread çalışırken yok edilirse Qt fail-fast ile süreci düşürür.
            # Kısa bekleme yetmezse pencere kapanırken süreci temiz biçimde bitir.
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(
                    lambda threads=tuple(lingering): MainWindow._force_exit_if_running(threads)
                )

        if self._modal is not None:
            try:
                self._modal.close_modal()
            except RuntimeError:
                pass

        hud = getattr(self, "floating_hud", None)
        if hud is not None:
            try:
                hud.close()
            except RuntimeError:
                pass
            self.floating_hud = None

        try:
            self.storage.close()
        except Exception:
            pass

        self._cleanup_done = True
        event.accept()
