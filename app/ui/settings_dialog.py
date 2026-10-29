from PySide6.QtCore import QPointF, QRectF, QTime, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.safety import AccessMode
from app.core.settings import Settings
from app.core.skill_library import SkillLibrary
from app.core.speech import VOICE_STYLES
from app.core.voice import microphone_devices
from app.theme import ultron as U
from app.theme.themes import THEME_ORDER, THEMES, normalize_theme
from .animations import animate_float, fade_window_in, fade_window_out
from .camera_hub import CameraHub

try:
    from PySide6.QtMultimedia import QMediaDevices
except Exception:  # noqa: BLE001 - QtMultimedia bazı kurulumlarda yok
    QMediaDevices = None

FULL_ACCESS_WARNING = (
    "Tam erişim modunda Jarvis, dosya değiştirme ve silme dahil işlemleri sormadan yapar.\n\n"
    "Yasaklı işlemler (sistem klasörleri, anahtar dosyaları, Jarvis'in kendi kodu), işlem günlüğü, "
    "değişiklik yedekleri ve Durdur düğmesi bu modda da çalışır.\n\n"
    "Devam edilsin mi?"
)


def _blend_colors(base: QColor, target: QColor, amount: float) -> QColor:
    t = max(0.0, min(1.0, float(amount)))
    return QColor(
        int(base.red() + (target.red() - base.red()) * t),
        int(base.green() + (target.green() - base.green()) * t),
        int(base.blue() + (target.blue() - base.blue()) * t),
        int(base.alpha() + (target.alpha() - base.alpha()) * t),
    )


class ThemePreview(QFrame):
    """Seçilebilir tema kartı; mini arayüzü doğrudan çizer."""

    clicked = Signal(str)

    def __init__(self, key: str, palette: dict, parent=None):
        super().__init__(parent)
        self.key = key
        self.palette = palette
        self._selected = False
        self._hover = False
        self._hover_value = 0.0
        self._select_value = 0.0
        self.setFixedSize(176, 142)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"{palette.get('LABEL', key)} teması")
        self.setToolTip(f"{palette.get('LABEL', key)} temasını seç")

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        if self.isVisible():
            animate_float(self, "_select_value", 1.0 if self._selected else 0.0, 220)
        else:
            self._select_value = 1.0 if self._selected else 0.0
            self.update()

    def enterEvent(self, event):
        self._hover = True
        animate_float(self, "_hover_value", 1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        animate_float(self, "_hover_value", 0.0, 200)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette
        hover = max(0.0, min(1.0, self._hover_value))
        selected = max(0.0, min(1.0, self._select_value))
        lift = 2.0 * hover * (1.0 - 0.4 * selected)
        outer = QRectF(self.rect()).adjusted(1.5, 1.5 - lift, -1.5, -1.5 - lift)

        if selected > 0.01:
            glow = QColor(pal["RED"])
            glow.setAlpha(int(38 * selected))
            painter.setPen(QPen(glow, 4.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(outer.adjusted(-1, -1, 1, 1), 13, 13)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(pal["BACKGROUND"]))
        painter.drawRoundedRect(outer, 12, 12)

        header = QRectF(outer.left() + 10, outer.top() + 10, outer.width() - 20, 24)
        painter.setBrush(QColor(pal["PANEL_2"]))
        painter.drawRoundedRect(header, 6, 6)

        painter.setBrush(QColor(pal["RED"]))
        painter.drawEllipse(QPointF(header.left() + 12, header.center().y()), 4, 4)
        title_font = QFont(pal["FONT"], 8, QFont.Weight.DemiBold)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        painter.setFont(title_font)
        painter.setPen(QColor(pal["TEXT"]))
        painter.drawText(
            QRectF(header.left() + 22, header.top(), 64, header.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "JARVIS",
        )
        painter.setFont(QFont(pal["FONT"], 6, QFont.Weight.Medium))
        painter.setPen(QColor(pal["RED_BRIGHT"]))
        painter.drawText(
            QRectF(header.right() - 68, header.top(), 58, header.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            "PROTOKOL",
        )

        rail = QRectF(outer.left() + 10, outer.top() + 42, 46, 74)
        body = QRectF(outer.left() + 62, outer.top() + 42, outer.width() - 72, 74)
        painter.setBrush(QColor(pal["PANEL"]))
        painter.drawRoundedRect(rail, 6, 6)
        painter.setBrush(QColor(pal["PANEL"]))
        painter.drawRoundedRect(body, 6, 6)

        painter.setBrush(QColor(pal["RED"]))
        painter.drawEllipse(QPointF(rail.center().x(), rail.top() + 18), 6, 6)
        painter.setBrush(QColor(pal["FAINT"]))
        for index in range(3):
            painter.drawRoundedRect(
                QRectF(rail.left() + 10, rail.top() + 34 + index * 12, 26, 4), 2, 2
            )

        painter.setBrush(QColor(pal["PANEL_2"]))
        painter.drawRoundedRect(
            QRectF(body.left() + 8, body.top() + 10, body.width() - 16, 22), 4, 4
        )
        painter.setBrush(QColor(pal["RED_SOFT"]))
        painter.drawRoundedRect(
            QRectF(body.left() + 8, body.top() + 40, body.width() * 0.52, 6), 3, 3
        )
        painter.setBrush(QColor(pal["FAINT"]))
        painter.drawRoundedRect(
            QRectF(body.left() + 8, body.top() + 52, body.width() - 16, 4), 2, 2
        )
        painter.drawRoundedRect(
            QRectF(body.left() + 8, body.top() + 62, body.width() * 0.72, 4), 2, 2
        )

        label_font = QFont(pal["FONT"], 8, QFont.Weight.DemiBold)
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        painter.setFont(label_font)
        painter.setPen(_blend_colors(QColor(pal["TEXT_SOFT"]), QColor(pal["TEXT"]), selected))
        painter.drawText(
            QRectF(outer.left() + 12, outer.bottom() - 26, outer.width() - 24, 18),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            pal.get("LABEL", self.key),
        )

        if selected > 0.01:
            check_rect = QRectF(outer.right() - 28, outer.bottom() - 27, 16, 16)
            check_fill = QColor(pal["RED"])
            check_fill.setAlpha(int(255 * selected))
            painter.setBrush(check_fill)
            painter.setPen(Qt.PenStyle.NoPen)
            radius = 8.0 * selected
            painter.drawEllipse(check_rect.center(), radius, radius)
            if selected > 0.35:
                check_ink = QColor(pal["BACKGROUND"])
                check_ink.setAlpha(int(255 * min(1.0, (selected - 0.35) / 0.65)))
                check_pen = QPen(check_ink, 1.8)
                check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(check_pen)
                painter.drawLine(
                    QPointF(check_rect.left() + 4, check_rect.center().y()),
                    QPointF(check_rect.left() + 7, check_rect.bottom() - 5),
                )
                painter.drawLine(
                    QPointF(check_rect.left() + 7, check_rect.bottom() - 5),
                    QPointF(check_rect.right() - 4, check_rect.top() + 5),
                )

        border = _blend_colors(QColor(pal["BORDER"]), QColor(pal["RED_SOFT"]), hover)
        border = _blend_colors(border, QColor(pal["RED"]), selected)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.0 + 1.0 * selected))
        painter.drawRoundedRect(outer, 12, 12)
        painter.end()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.theme_changed = False
        self.theme_name = settings.theme
        self.selected_theme = normalize_theme(settings.theme)
        self._closing = False
        self.setWindowTitle("Jarvis Ayarları")
        self.setMinimumSize(620, 540)
        self.resize(720, 600)
        self.setMaximumWidth(780)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 14)
        outer.setSpacing(12)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        connection = QWidget()
        connection_form = QFormLayout(connection)
        connection_form.setContentsMargins(18, 18, 18, 18)
        connection_form.setSpacing(10)
        self.provider = QComboBox()
        self.provider.addItem("Google Gemini API", "gemini")
        self.provider.addItem("Ollama (yerel, ücretsiz)", "ollama")
        self.provider.addItem("Token Harbor (ücretsiz API)", "tokenharbor")
        provider_index = self.provider.findData(settings.provider)
        self.provider.setCurrentIndex(max(0, provider_index))
        connection_form.addRow("Yapay zekâ motoru:", self.provider)

        self.api_key_label = QLabel("Gemini API anahtarı:")
        self.api_key = QLineEdit(settings.api_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Google AI Studio API anahtarın")
        self.model_label = QLabel("Gemini modeli:")
        self.model = QLineEdit(settings.model)
        self.model.setPlaceholderText("Örn. gemini-3.8-flash")
        connection_form.addRow(self.api_key_label, self.api_key)
        connection_form.addRow(self.model_label, self.model)
        self.gemini_note = QLabel("Model hesabında yoksa Jarvis uygun Gemini modelini kendisi seçer.")
        self.gemini_note.setWordWrap(True)
        self.gemini_note.setStyleSheet(f"color:{U.MUTED};")
        connection_form.addRow(self.gemini_note)

        self.ollama_url_label = QLabel("Ollama adresi:")
        self.ollama_url = QLineEdit(settings.ollama_url)
        self.ollama_url.setPlaceholderText("http://localhost:11434")
        self.ollama_model_label = QLabel("Ollama modeli:")
        self.ollama_model = QLineEdit(settings.ollama_model)
        self.ollama_model.setPlaceholderText("Örn. qwen2.5-coder:3b")
        connection_form.addRow(self.ollama_url_label, self.ollama_url)
        connection_form.addRow(self.ollama_model_label, self.ollama_model)
        self.ollama_note = QLabel("Önce Ollama'yı kurup modeli indir. API anahtarı ve kredi gerekmez.")
        self.ollama_note.setWordWrap(True)
        self.ollama_note.setStyleSheet(f"color:{U.MUTED};")
        connection_form.addRow(self.ollama_note)

        self.tokenharbor_key_label = QLabel("Token Harbor API anahtarı:")
        self.tokenharbor_key = QLineEdit(settings.tokenharbor_api_key)
        self.tokenharbor_key.setEchoMode(QLineEdit.Password)
        self.tokenharbor_key.setPlaceholderText("tokenharbor.ai panosundaki anahtar")
        self.tokenharbor_model_label = QLabel("Token Harbor modeli:")
        self.tokenharbor_model = QLineEdit(settings.tokenharbor_model)
        self.tokenharbor_model.setPlaceholderText("Boş bırak = ücretsiz modeli kendi seçer")
        connection_form.addRow(self.tokenharbor_key_label, self.tokenharbor_key)
        connection_form.addRow(self.tokenharbor_model_label, self.tokenharbor_model)
        self.tokenharbor_note = QLabel(
            "tokenharbor.ai/dashboard/api-keys adresinden ücretsiz anahtarını al. "
            "Model adı boşsa Jarvis :free ile biten ilk modeli otomatik seçer; "
            "görsel/ekran özellikleri modele göre değişebilir."
        )
        self.tokenharbor_note.setWordWrap(True)
        self.tokenharbor_note.setStyleSheet(f"color:{U.MUTED};")
        connection_form.addRow(self.tokenharbor_note)
        self.provider.currentIndexChanged.connect(self._update_provider_fields)
        self._update_provider_fields()
        self.tabs.addTab(connection, "Bağlantı")

        audio = QWidget()
        audio_form = QFormLayout(audio)
        audio_form.setContentsMargins(18, 18, 18, 18)
        audio_form.setSpacing(10)
        audio_note = QLabel(
            "Mikrofon açıkken Jarvis sürekli dinler ve yanıtından sonra kendiliğinden tekrar dinlemeye geçer. "
            "Kulaklık modu açıksa Jarvis konuşurken sözünü kesebilirsin; hoparlör kullanıyorsan kapat ki "
            "Jarvis kendi sesini duyup sözünü kesmesin."
        )
        audio_note.setWordWrap(True)
        audio_note.setStyleSheet(f"color:{U.MUTED};")
        audio_form.addRow(audio_note)

        self.mic_combo = QComboBox()
        try:
            mic_devices, default_mic = microphone_devices()
        except Exception:
            mic_devices, default_mic = [], ""
        self.mic_combo.addItem(
            "Sistem varsayılanı" + (f" · {default_mic}" if default_mic else ""),
            "",
        )
        for _index, name in mic_devices:
            self.mic_combo.addItem(name, name)
        if settings.mic_device and self.mic_combo.findData(settings.mic_device) < 0:
            self.mic_combo.addItem(f"{settings.mic_device} · şu an yok", settings.mic_device)
        if settings.mic_device:
            self.mic_combo.setCurrentIndex(max(0, self.mic_combo.findData(settings.mic_device)))
        audio_form.addRow("Mikrofon:", self.mic_combo)

        self.speaker_combo = QComboBox()
        default_speaker = ""
        if QMediaDevices is not None:
            try:
                default_speaker = QMediaDevices.defaultAudioOutput().description()
            except Exception:
                default_speaker = ""
        self.speaker_combo.addItem(
            "Sistem varsayılanı" + (f" · {default_speaker}" if default_speaker else ""),
            "",
        )
        if QMediaDevices is not None:
            try:
                for device in QMediaDevices.audioOutputs():
                    description = device.description()
                    self.speaker_combo.addItem(description, description)
            except Exception:
                pass
        if settings.speaker_device and self.speaker_combo.findData(settings.speaker_device) < 0:
            self.speaker_combo.addItem(f"{settings.speaker_device} · şu an yok", settings.speaker_device)
        if settings.speaker_device:
            self.speaker_combo.setCurrentIndex(
                max(0, self.speaker_combo.findData(settings.speaker_device))
            )
        audio_form.addRow("Hoparlör / kulaklık:", self.speaker_combo)

        volume_row = QWidget()
        volume_layout = QHBoxLayout(volume_row)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(8)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(settings.voice_volume)
        self.volume_value = QLabel(f"%{settings.voice_volume}")
        self.volume_value.setMinimumWidth(38)
        self.volume_value.setStyleSheet(f"color:{U.TEXT_SOFT};")
        self.volume.valueChanged.connect(lambda value: self.volume_value.setText(f"%{value}"))
        volume_layout.addWidget(self.volume, 1)
        volume_layout.addWidget(self.volume_value)
        audio_form.addRow("Ses düzeyi:", volume_row)

        self.voice_style = QComboBox()
        for key, (_voice, _pitch, label) in VOICE_STYLES.items():
            self.voice_style.addItem(label, key)
        self.voice_style.setCurrentIndex(max(0, self.voice_style.findData(settings.voice_style)))
        audio_form.addRow("Ses karakteri:", self.voice_style)
        voice_note = QLabel(
            "Jarvis artık daha canlı, derin ve hızlı çok dilli bir nöral ses kullanıyor. "
            "Doğal Türkçe (Emel), sıcak kadın (Ava) ve derin erkek (William) alternatifleri var; "
            "çok dilli seslerde Türkçe vurgusu hafif aksanlı olabilir."
        )
        voice_note.setWordWrap(True)
        voice_note.setStyleSheet(f"color:{U.MUTED};")
        audio_form.addRow(voice_note)

        self.headphones = QCheckBox(
            "Kulaklık kullanıyorum; Jarvis konuşurken sözünü kesebileyim"
        )
        self.headphones.setChecked(settings.voice_headphones)
        audio_form.addRow(self.headphones)

        self.camera_combo = QComboBox()
        try:
            camera_devices = CameraHub.devices()
        except Exception:
            camera_devices = []
        if camera_devices:
            for index, name in enumerate(camera_devices):
                self.camera_combo.addItem(f"{index + 1}. {name}", index)
            self.camera_combo.setCurrentIndex(
                min(settings.camera_index, self.camera_combo.count() - 1)
            )
        else:
            self.camera_combo.addItem("Kamera bulunamadı", 0)
        for combo in (self.mic_combo, self.speaker_combo, self.camera_combo):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(24)
        audio_form.addRow("Kamera:", self.camera_combo)

        camera_note = QLabel(
            "Kamera seçimi bir sonraki kamera açılışında uygulanır; açık kameraysa ayar kaydedilince yenilenir."
        )
        camera_note.setWordWrap(True)
        camera_note.setStyleSheet(f"color:{U.MUTED};")
        audio_form.addRow(camera_note)
        self.tabs.addTab(audio, "Ses & Kamera")

        security = QWidget()
        security_form = QFormLayout(security)
        security_form.setContentsMargins(18, 18, 18, 18)
        security_form.setSpacing(10)
        self.mode = QComboBox()
        self.mode.addItem("Onaylı (önerilen)", AccessMode.APPROVAL)
        self.mode.addItem("Adım adım (her işlemde sor)", AccessMode.STEP)
        self.mode.addItem("Tam erişim (sormadan yapar)", AccessMode.FULL)
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.access_mode)))
        security_form.addRow("Erişim modu:", self.mode)
        mode_note = QLabel(
            "Onaylı: dosya değiştirme ve silme önce sorulur. "
            "Adım adım: uygulama açma, sayfa açma ve her oluşturma/değiştirme adımı da sorulur. "
            "Tam erişim: sormadan yapar; yasaklar, işlem günlüğü ve geri alma yine açıktır."
        )
        mode_note.setWordWrap(True)
        mode_note.setStyleSheet(f"color:{U.MUTED};")
        security_form.addRow(mode_note)
        self.strict_web = QCheckBox("İnternetten okuduktan sonra riskli işlemlerde yine onay iste (önerilir)")
        self.strict_web.setChecked(settings.strict_after_web)
        security_form.addRow(self.strict_web)

        limits_note = QLabel("Ajan sınırları — 0 = sınırsız/kapalı:")
        limits_note.setStyleSheet(f"color:{U.TEXT_SOFT};font-weight:600;")
        security_form.addRow(limits_note)
        self.step_limit = QSpinBox()
        self.step_limit.setRange(0, 10000)
        self.step_limit.setValue(settings.agent_step_limit)
        self.step_limit.setSpecialValueText("Sınırsız")
        security_form.addRow("Adım sınırı:", self.step_limit)
        self.seconds_limit = QSpinBox()
        self.seconds_limit.setRange(0, 86400)
        self.seconds_limit.setValue(settings.agent_seconds_limit)
        self.seconds_limit.setSpecialValueText("Sınırsız")
        self.seconds_limit.setSuffix(" sn")
        security_form.addRow("Süre sınırı:", self.seconds_limit)
        self.loop_limit = QSpinBox()
        self.loop_limit.setRange(0, 1000)
        self.loop_limit.setValue(settings.agent_loop_limit)
        self.loop_limit.setSpecialValueText("Kapalı")
        security_form.addRow("Döngü koruması:", self.loop_limit)

        self.roots = QListWidget()
        self.roots.addItems(settings.extra_roots)
        self.roots.setMaximumHeight(100)
        security_form.addRow("Ek proje klasörleri:", self.roots)
        root_buttons = QWidget()
        root_layout = QHBoxLayout(root_buttons)
        root_layout.setContentsMargins(0, 0, 0, 0)
        add_root = QPushButton("Klasör ekle")
        remove_root = QPushButton("Seçileni kaldır")
        add_root.clicked.connect(self.add_root)
        remove_root.clicked.connect(self.remove_root)
        root_layout.addWidget(add_root)
        root_layout.addWidget(remove_root)
        security_form.addRow(root_buttons)
        self.tabs.addTab(security, "Güvenlik")

        companion = QWidget()
        companion_form = QFormLayout(companion)
        companion_form.setContentsMargins(18, 18, 18, 18)
        companion_form.setSpacing(10)
        companion_note = QLabel(
            "Eşlikçi yalnızca Jarvis boştayken kısa bir bildirim gösterir: yaklaşan etkinlik, "
            "hatırlatıcı ön uyarısı, su molası ve uzun oturum arası. Sessiz saatlerde susar; "
            "sesli fısıldama yalnızca mikrofon açıksa çalışır."
        )
        companion_note.setWordWrap(True)
        companion_note.setStyleSheet(f"color:{U.MUTED};")
        companion_form.addRow(companion_note)
        self.companion_enabled = QCheckBox("Proaktif eşlikçi açık")
        self.companion_enabled.setChecked(settings.companion_enabled)
        companion_form.addRow(self.companion_enabled)
        self.companion_interval = QSpinBox()
        self.companion_interval.setRange(15, 480)
        self.companion_interval.setSingleStep(15)
        self.companion_interval.setValue(settings.companion_interval)
        self.companion_interval.setSuffix(" dk")
        companion_form.addRow("Mola aralığı:", self.companion_interval)
        self.companion_hydration = QCheckBox("Su molası hatırlat")
        self.companion_hydration.setChecked(settings.companion_hydration)
        companion_form.addRow(self.companion_hydration)
        self.companion_speak = QCheckBox("Mikrofon açıksa sesli söyle")
        self.companion_speak.setChecked(settings.companion_speak)
        companion_form.addRow(self.companion_speak)
        self.companion_quiet_start = QTimeEdit()
        self.companion_quiet_start.setDisplayFormat("HH:mm")
        self.companion_quiet_start.setTime(QTime.fromString(settings.companion_quiet_start, "HH:mm"))
        self.companion_quiet_end = QTimeEdit()
        self.companion_quiet_end.setDisplayFormat("HH:mm")
        self.companion_quiet_end.setTime(QTime.fromString(settings.companion_quiet_end, "HH:mm"))
        quiet_row = QWidget()
        quiet_layout = QHBoxLayout(quiet_row)
        quiet_layout.setContentsMargins(0, 0, 0, 0)
        quiet_layout.addWidget(self.companion_quiet_start)
        quiet_layout.addWidget(QLabel("—"))
        quiet_layout.addWidget(self.companion_quiet_end)
        quiet_layout.addStretch(1)
        companion_form.addRow("Sessiz saatler:", quiet_row)
        self.tabs.addTab(companion, "Eşlikçi")

        themes = QWidget()
        theme_layout = QVBoxLayout(themes)
        theme_layout.setContentsMargins(18, 18, 18, 18)
        theme_layout.setSpacing(10)
        theme_title = QLabel("GÖRÜNÜM PROTOKOLÜ")
        theme_title.setStyleSheet(f"color:{U.RED_BRIGHT};font-weight:700;font-size:10px;")
        theme_layout.addWidget(theme_title)
        theme_hint = QLabel(
            "Beş farklı palet: ULTRON kırmızısı, mavi HUD, amber kor, zümrüt orman ve aydınlık. "
            "Kartlara tıklayarak seç; değişiklik yeniden başlatınca uygulanır."
        )
        theme_hint.setWordWrap(True)
        theme_hint.setStyleSheet(f"color:{U.MUTED};")
        theme_layout.addWidget(theme_hint)

        cards = QGridLayout()
        cards.setHorizontalSpacing(8)
        cards.setVerticalSpacing(8)
        self.theme_previews = {}
        for index, key in enumerate(THEME_ORDER):
            card = ThemePreview(key, THEMES[key], themes)
            card.clicked.connect(self._select_theme)
            cards.addWidget(card, index // 3, index % 3)
            self.theme_previews[key] = card
        theme_layout.addLayout(cards)

        self.theme_readout = QLabel("")
        self.theme_readout.setStyleSheet(f"color:{U.TEXT_SOFT};font-weight:600;")
        theme_layout.addWidget(self.theme_readout)
        theme_layout.addStretch(1)
        self.tabs.addTab(themes, "Temalar")
        self._select_theme(self.selected_theme)

        self.skill_library = SkillLibrary()
        skills = QWidget()
        skill_layout = QVBoxLayout(skills)
        skill_layout.setContentsMargins(18, 18, 18, 18)
        skill_layout.setSpacing(10)
        skill_note = QLabel(
            "Yerel yetenekler SKILL.md dosyalarından yüklenir ve görev sırasında Jarvis tarafından okunur. "
            "Yetenek talimatları güvenlik kurallarını, kullanıcı onayını veya araç sınırlarını değiştirmez."
        )
        skill_note.setWordWrap(True)
        skill_layout.addWidget(skill_note)
        self.skill_list = QListWidget()
        for skill in self.skill_library.list_skills():
            item = QListWidgetItem(f"{skill.name} · {skill.description}")
            item.setToolTip(skill.description)
            self.skill_list.addItem(item)
        if self.skill_list.count() == 0:
            self.skill_list.addItem("Henüz yetenek yok.")
        skill_layout.addWidget(self.skill_list, 1)
        skill_folder = QLabel(f"Klasör: {self.skill_library.root}")
        skill_folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        skill_folder.setWordWrap(True)
        skill_layout.addWidget(skill_folder)
        open_skills = QPushButton("Yetenek klasörünü aç")
        open_skills.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.skill_library.root)))
        )
        skill_layout.addWidget(open_skills, 0, Qt.AlignLeft)
        self.tabs.addTab(skills, "Yetenekler")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        fade_window_in(self, 170)

    def done(self, result):
        if self._closing:
            QDialog.done(self, result)
            return
        self._closing = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        animation = fade_window_out(self, 140)
        if animation is None:
            QDialog.done(self, result)
            return
        animation.finished.connect(lambda: QDialog.done(self, result))

    def _select_theme(self, key: str) -> None:
        self.selected_theme = normalize_theme(key)
        for name, card in self.theme_previews.items():
            card.set_selected(name == self.selected_theme)
        self.theme_readout.setText(
            f"Seçili tema: {THEMES[self.selected_theme].get('LABEL', self.selected_theme)}"
        )

    def _update_provider_fields(self):
        selected = self.provider.currentData()
        gemini = selected == "gemini"
        ollama = selected == "ollama"
        for widget in (self.api_key_label, self.api_key, self.model_label, self.model, self.gemini_note):
            widget.setVisible(gemini)
        for widget in (
            self.ollama_url_label, self.ollama_url, self.ollama_model_label,
            self.ollama_model, self.ollama_note,
        ):
            widget.setVisible(ollama)
        for widget in (
            self.tokenharbor_key_label, self.tokenharbor_key,
            self.tokenharbor_model_label, self.tokenharbor_model, self.tokenharbor_note,
        ):
            widget.setVisible(selected == "tokenharbor")

    def add_root(self):
        path = QFileDialog.getExistingDirectory(self, "Jarvis'in kullanabileceği proje klasörünü seç")
        if path and not self.roots.findItems(path, Qt.MatchExactly):
            self.roots.addItem(path)

    def remove_root(self):
        for item in self.roots.selectedItems():
            self.roots.takeItem(self.roots.row(item))

    def save(self):
        if self.provider.currentData() == "gemini" and not self.api_key.text().strip():
            self.api_key.setFocus()
            return
        if self.provider.currentData() == "ollama" and not (
            self.ollama_url.text().strip() and self.ollama_model.text().strip()
        ):
            self.ollama_model.setFocus()
            return
        if self.provider.currentData() == "tokenharbor" and not self.tokenharbor_key.text().strip():
            self.tokenharbor_key.setFocus()
            return
        mode = self.mode.currentData()
        if mode == AccessMode.FULL and self.settings.access_mode != AccessMode.FULL:
            answer = QMessageBox.warning(
                self, "Tam erişim", FULL_ACCESS_WARNING, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                self.mode.setCurrentIndex(0)
                return
        previous_theme = self.settings.theme
        self.settings.save(
            self.api_key.text(),
            self.model.text(),
            self.provider.currentData(),
            self.ollama_url.text(),
            self.ollama_model.text(),
            tokenharbor_api_key=self.tokenharbor_key.text(),
            tokenharbor_model=self.tokenharbor_model.text(),
            tokenharbor_url=self.settings.tokenharbor_url,
        )
        self.settings.set_access(mode, self.strict_web.isChecked())
        self.settings.set_agent_limits(
            self.step_limit.value(), self.seconds_limit.value(), self.loop_limit.value()
        )
        self.settings.set_companion(
            enabled=self.companion_enabled.isChecked(),
            interval=self.companion_interval.value(),
            quiet_start=self.companion_quiet_start.time().toString("HH:mm"),
            quiet_end=self.companion_quiet_end.time().toString("HH:mm"),
            hydration=self.companion_hydration.isChecked(),
            speak=self.companion_speak.isChecked(),
        )
        self.settings.set_audio(
            mic_device=str(self.mic_combo.currentData() or ""),
            speaker_device=str(self.speaker_combo.currentData() or ""),
            camera_index=int(self.camera_combo.currentData() or 0),
            voice_style=str(self.voice_style.currentData() or "jarvis"),
            voice_rate=self.settings.voice_rate,
            voice_volume=self.volume.value(),
            headphones=self.headphones.isChecked(),
        )
        self.settings.set_roots([self.roots.item(index).text() for index in range(self.roots.count())])
        self.settings.set_theme(self.selected_theme)
        self.theme_name = self.selected_theme
        self.theme_changed = self.selected_theme != previous_theme
        self.accept()
