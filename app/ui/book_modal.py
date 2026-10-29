"""Kamera ile kitap kapağı/ISBN tarama, sayfa OCR ve kişisel özet arayüzü."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.ai import AI
from app.core.book_tools import (
    COVER_VISION_PROMPT,
    COVER_VISION_SYSTEM,
    SUMMARY_SYSTEM_PROMPT,
    BookError,
    BookService,
    BookStore,
    build_summary_prompt,
    clean_ocr_text,
    extract_isbn,
    normalize_isbn,
    parse_cover_vision,
    prepare_for_ocr,
    summary_title,
)
from app.core.ocr_tools import ScreenOCR
from app.theme import ultron as U
from .camera_hub import CameraHub, CameraPreview
from .hud_modal import HudModal, card
from .hud_widgets import Divider, HudButton, hud_font, hud_label


def _combo_style() -> str:
    return f"""
    QComboBox {{
        background:{U.INPUT};
        color:{U.TEXT_SOFT};
        border:1px solid {U.BORDER};
        border-radius:6px;
        padding:5px 10px;
        font-size:11px;
    }}
    QComboBox:hover {{ border-color:{U.RED_DIM}; }}
    QComboBox::drop-down {{ border:none; width:18px; }}
    QComboBox QAbstractItemView {{
        background:{U.PANEL_2};
        color:{U.TEXT_SOFT};
        border:1px solid {U.BORDER};
        selection-background-color:{U.RED_DIM};
        selection-color:{U.TEXT};
        outline:none;
    }}
    """


def _edit_style() -> str:
    return f"""
    QLineEdit, QSpinBox {{
        background:{U.INPUT};
        color:{U.TEXT};
        border:1px solid {U.BORDER};
        border-radius:6px;
        padding:6px 10px;
        font-size:11px;
        selection-background-color:{U.RED_DIM};
    }}
    QLineEdit:focus, QSpinBox:focus {{ border-color:{U.RED_SOFT}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width:0; height:0; }}
    """


def _summary_style() -> str:
    return f"""
    QPlainTextEdit {{
        background:{U.INPUT};
        color:{U.TEXT};
        border:1px solid {U.BORDER};
        border-radius:7px;
        padding:10px;
        font-size:11px;
        selection-background-color:{U.RED_DIM};
    }}
    QPlainTextEdit:focus {{ border-color:{U.RED_SOFT}; }}
    """


class _FunctionWorker(QObject):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as error:  # noqa: BLE001 - iş parçacığından UI'a taşınır
            self.failed.emit(str(error))


class BookModal(HudModal):
    """Kamera/ISBN araması, geçici sayfa OCR'ı ve kalıcı kişisel özet."""

    uses_camera = True

    def __init__(
        self,
        parent,
        settings=None,
        storage=None,
        auto_start: bool = True,
        service: Optional[BookService] = None,
        store: Optional[BookStore] = None,
        ai: Optional[AI] = None,
        camera: Optional[CameraHub] = None,
        camera_keep=None,
    ) -> None:
        super().__init__(
            parent,
            "Kitap Tarayıcı",
            "KAMERA · ISBN · OCR · KİŞİSEL ÖZET",
            width=1240,
            height=780,
            fade=False,
        )
        self.settings = settings
        self.service = service if service is not None else BookService.default(settings)
        self.store = store if store is not None else (
            self.service.store if self.service is not None else BookStore.default(settings)
        )
        self.ai = ai if ai is not None else AI(storage=storage, settings=settings)
        self.book: Optional[dict] = None
        self._pages: List[dict] = []
        self._profiles: List[dict] = []
        self._results: List[dict] = []
        self._last_mode = "profile"
        self.camera = camera if camera is not None else CameraHub(self)
        self._camera_keep = camera_keep
        self._ocr_busy = False
        self._lookup_busy = False
        self._cover_busy = False
        self._summary_busy = False
        self._ocr_temp: Optional[Path] = None
        self._jobs: List[_FunctionWorker] = []
        self._closing = False

        self.camera.frame_ready.connect(self._on_video_frame)
        self.camera.error.connect(self._on_camera_error)
        self.camera.active_changed.connect(self._on_camera_active)
        self._build_body()
        self._load_library()
        self._update_pages_label()
        if auto_start:
            QTimer.singleShot(180, self._start_camera)
        else:
            self.camera_preview.set_message("KAMERA KAPALI")
            self._set_status("Kamera kapalı. Dosyadan sayfa tarayabilir veya ISBN arayabilirsin.")

    # ------------------------------------------------------------------ görünüm
    def _build_body(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(self._build_camera_card(), 3)
        row.addWidget(self._build_book_card(), 2)
        self.content.addLayout(row, 1)

    def _build_camera_card(self) -> QWidget:
        holder = card(padding=12)
        layout = holder.layout()
        layout.setSpacing(8)

        self.camera_preview = CameraPreview()
        layout.addWidget(self.camera_preview, 1)

        device_row = QHBoxLayout()
        device_row.setSpacing(8)
        device_row.addWidget(hud_label("Kamera", 7, U.DIM, QFont.Weight.Medium, 0.2))
        self.device_combo = QComboBox()
        self.device_combo.setStyleSheet(_combo_style())
        self.device_combo.setMinimumWidth(170)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_row.addWidget(self.device_combo, 1)
        self.camera_toggle = HudButton("BAŞLAT", small=True)
        self.camera_toggle.clicked.connect(self._toggle_camera)
        device_row.addWidget(self.camera_toggle)
        layout.addLayout(device_row)

        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        self.cover_button = HudButton("KAPAK / ISBN TARA", accent=True)
        self.cover_button.clicked.connect(lambda: self._capture("cover"))
        self.page_button = HudButton("SAYFA TARA")
        self.page_button.clicked.connect(lambda: self._capture("page"))
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 9999)
        self.page_spin.setValue(1)
        self.page_spin.setStyleSheet(_edit_style())
        self.page_spin.setFixedWidth(74)
        scan_row.addWidget(self.cover_button, 2)
        scan_row.addWidget(self.page_button, 2)
        scan_row.addWidget(self.page_spin)
        layout.addLayout(scan_row)

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self.import_mode = QComboBox()
        self.import_mode.addItems(["SAYFA", "KAPAK / ISBN"])
        self.import_mode.setStyleSheet(_combo_style())
        self.import_mode.setFixedWidth(140)
        import_button = HudButton("DOSYADAN TARA", small=True)
        import_button.clicked.connect(self._import_image)
        self.clear_pages_button = HudButton("SAYFALARI TEMİZLE", small=True)
        self.clear_pages_button.clicked.connect(self._clear_pages)
        file_row.addWidget(self.import_mode)
        file_row.addWidget(import_button)
        file_row.addStretch(1)
        file_row.addWidget(self.clear_pages_button)
        layout.addLayout(file_row)

        self.pages_label = hud_label("Taranan sayfa yok.", 8, U.MUTED, QFont.Weight.Medium, 0.6)
        self.pages_label.setWordWrap(True)
        layout.addWidget(self.pages_label)

        self.status_label = hud_label("", 8, U.TEXT_SOFT, QFont.Weight.Medium, 0.5)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.last_scan_label = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.4)
        self.last_scan_label.setWordWrap(True)
        layout.addWidget(self.last_scan_label)

        privacy = hud_label(
            "GİZLİLİK · OCR metni yalnızca bellekte tutulur; kapatınca silinir. "
            "Kalıcı kayıt: künye, kapak, özet ve sayfa numaraları.",
            7,
            U.FAINT,
            QFont.Weight.Medium,
            0.5,
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        return holder

    def _build_book_card(self) -> QWidget:
        holder = card(padding=12)
        layout = holder.layout()
        layout.setSpacing(9)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("ISBN veya kitap adı")
        self.search_input.setStyleSheet(_edit_style())
        self.search_input.returnPressed.connect(self._manual_lookup)
        self.search_button = HudButton("ARA", accent=True, small=True)
        self.search_button.clicked.connect(self._manual_lookup)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.results_combo = QComboBox()
        self.results_combo.setStyleSheet(_combo_style())
        self.results_combo.currentIndexChanged.connect(self._on_result_selected)
        self.results_combo.hide()
        layout.addWidget(self.results_combo)

        head = QHBoxLayout()
        head.setSpacing(12)
        self.cover_label = QLabel("KAPAK\nYOK")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setFixedSize(118, 170)
        self.cover_label.setFont(hud_font(8, QFont.Weight.DemiBold, 1.4))
        self.cover_label.setStyleSheet(
            f"background:{U.INPUT};color:{U.FAINT};border:1px solid {U.BORDER};border-radius:7px;"
        )
        head.addWidget(self.cover_label)

        info = QVBoxLayout()
        info.setSpacing(5)
        self.title_label = hud_label("KİTAP SEÇİLMEDİ", 16, U.TEXT, QFont.Weight.DemiBold, 0.5)
        self.title_label.setWordWrap(True)
        self.authors_label = hud_label("Kamera veya ISBN ile künye getir.", 8, U.MUTED, QFont.Weight.Medium, 0.4)
        self.authors_label.setWordWrap(True)
        self.meta_label = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.4)
        self.meta_label.setWordWrap(True)
        self.source_label = hud_label("", 7, U.FAINT, QFont.Weight.Medium, 0.4)
        self.source_label.setWordWrap(True)
        info.addWidget(self.title_label)
        info.addWidget(self.authors_label)
        info.addWidget(self.meta_label)
        info.addWidget(self.source_label)
        info.addStretch(1)
        head.addLayout(info, 1)
        layout.addLayout(head)

        self.toc_label = hud_label("İÇİNDEKİLER · veride yok", 7, U.DIM, QFont.Weight.Medium, 0.4)
        self.toc_label.setWordWrap(True)
        layout.addWidget(self.toc_label)

        self.description_label = hud_label("", 7, U.DIM, QFont.Weight.Medium, 0.4)
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        layout.addWidget(Divider())

        summary_head = QHBoxLayout()
        summary_head.addWidget(hud_label("Kişisel özet / notlar", 7, U.MUTED, QFont.Weight.Medium, 0.3))
        summary_head.addStretch(1)
        self.summary_state = hud_label("KAYIT YOK", 7, U.FAINT, QFont.Weight.Medium, 1.0)
        summary_head.addWidget(self.summary_state)
        layout.addLayout(summary_head)

        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText(
            "Kitap profili veya taranan sayfalardan üretilen kişisel özet burada görünür. "
            "Düzenleyip kaydedebilirsin."
        )
        self.summary_edit.setStyleSheet(_summary_style())
        self.summary_edit.setMinimumHeight(220)
        layout.addWidget(self.summary_edit, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.profile_button = HudButton("KİTAP PROFİLİ", small=True)
        self.profile_button.clicked.connect(lambda: self._generate_summary("profile"))
        self.pages_button = HudButton("SAYFA ÖZETİ", small=True, accent=True)
        self.pages_button.clicked.connect(lambda: self._generate_summary("pages"))
        self.save_button = HudButton("KAYDET", small=True)
        self.save_button.clicked.connect(self._save_edited_summary)
        self.copy_button = HudButton("KOPYALA", small=True)
        self.copy_button.clicked.connect(self._copy_summary)
        for button in (self.profile_button, self.pages_button, self.save_button, self.copy_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        library_row = QHBoxLayout()
        library_row.addWidget(hud_label("Kitaplık", 7, U.DIM, QFont.Weight.Medium, 0.2))
        self.library_combo = QComboBox()
        self.library_combo.setStyleSheet(_combo_style())
        self.library_combo.currentIndexChanged.connect(self._on_library_selected)
        library_row.addWidget(self.library_combo, 1)
        layout.addLayout(library_row)

        for button in (self.profile_button, self.pages_button, self.save_button, self.copy_button):
            button.setEnabled(False)
        return holder

    # ------------------------------------------------------------------- kamera
    def _populate_devices(self) -> None:
        devices = CameraHub.devices()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for name in devices:
            self.device_combo.addItem(name)
        self.device_combo.blockSignals(False)
        if not devices:
            self.device_combo.addItem("Kamera bulunamadı")
            self.device_combo.setEnabled(False)
            self.camera_toggle.setEnabled(False)
            self.camera_preview.set_message("KAMERA BULUNAMADI\nDosyadan sayfa tarayabilirsin.")
            self._set_status(
                "Kamera bulunamadı. 'DOSYADAN TARA' ile bir kitap sayfası görseli seçebilirsin.",
                error=True,
            )

    def _start_camera(self) -> None:
        if self._closing:
            return
        if not self.camera.available():
            self._set_status("QtMultimedia bulunamadı; kamera kullanılamıyor. Dosyadan tarama açık.", error=True)
            self.camera_preview.set_message("KAMERA MOTORU YOK")
            return
        if self.device_combo.count() == 0:
            self._populate_devices()
        if self.device_combo.count() == 0 or not self.device_combo.isEnabled():
            return
        if self.camera.is_active():
            self.camera_toggle.setText("DURDUR")
            latest = self.camera.latest_image()
            if latest is not None:
                self.camera_preview.set_frame(latest)
            return
        self.camera_preview.set_message("KAMERA AÇILIYOR…")
        self._set_status("Kamera başlatılıyor…")
        if not self.camera.start(max(0, self.device_combo.currentIndex())):
            self.camera_preview.set_message("KAMERA AÇILAMADI")
        else:
            self.camera_toggle.setText("DURDUR")

    def _stop_camera(self, quiet: bool = False) -> None:
        if self.camera_should_keep():
            return
        self.camera.stop()
        self.camera_preview.set_scanning(False)
        self.camera_preview.set_message("KAMERA KAPALI")
        if not quiet:
            self.camera_toggle.setText("BAŞLAT")
            self._set_status("Kamera durduruldu. Kare almak için BAŞLAT'a bas.")

    def camera_should_keep(self) -> bool:
        try:
            return bool(self._camera_keep and self._camera_keep())
        except Exception:
            return False

    def _toggle_camera(self) -> None:
        if self.camera.is_active():
            if self.camera_should_keep():
                self._set_status("Kamera arka plan algısı için açık kalıyor; üstteki KAMERA düğmesinden kapatabilirsin.")
                return
            self.camera.stop()
            self.camera_preview.set_scanning(False)
            self.camera_preview.set_message("KAMERA KAPALI")
            self._set_status("Kamera durduruldu. Kare almak için BAŞLAT'a bas.")
        else:
            self._start_camera()

    def _restart_camera(self) -> None:
        self.camera.restart(max(0, self.device_combo.currentIndex()))

    def _on_device_changed(self, index: int) -> None:
        if index < 0 or self._closing:
            return
        if self.camera.is_active():
            QTimer.singleShot(60, self._restart_camera)

    def _on_video_frame(self, image) -> None:
        if self._closing or image is None or image.isNull():
            return
        self.camera_preview.set_frame(image)
        if self.status_label.text().startswith(("Kamera başlatılıyor", "KAMERA AÇILIYOR")):
            self._set_status("CANLI · Kapağı veya sayfayı kare içine hizala.")

    def _on_camera_active(self, active: bool) -> None:
        if self._closing:
            return
        self.camera_toggle.setText("DURDUR" if active else "BAŞLAT")
        if active:
            self.camera_preview.set_message("KAMERA AÇILIYOR…")
        else:
            self.camera_preview.set_frame(None)
            self.camera_preview.set_message("KAMERA KAPALI")

    def _on_camera_error(self, message: str) -> None:
        self.camera_toggle.setText("BAŞLAT")
        self.camera_preview.set_message("KAMERA HATASI\nDosyadan tarama kullanılabilir.")
        self._set_status(f"Kamera hatası: {message}", error=True)

    # --------------------------------------------------------------------- OCR
    def _capture(self, kind: str) -> None:
        if self._ocr_busy or self._lookup_busy or self._cover_busy or self._summary_busy:
            return
        image = self.camera_preview.image()
        if image is None or image.isNull():
            self._set_status("Kamera karesi yok. Kamerayı başlat veya dosyadan tara.", error=True)
            return
        handle = tempfile.NamedTemporaryFile(prefix="jarvis_book_frame_", suffix=".png", delete=False)
        path = Path(handle.name)
        handle.close()
        if not image.save(str(path), "PNG"):
            path.unlink(missing_ok=True)
            self._set_status("Kamera karesi kaydedilemedi.", error=True)
            return
        self._ocr_temp = path
        self._start_ocr(path, kind)

    def _import_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Kitap sayfası görseli seç",
            str(Path.home()),
            "Görseller (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            return
        kind = "cover" if self.import_mode.currentIndex() == 1 else "page"
        self._start_ocr(Path(path), kind)

    def _start_ocr(self, source: Path, kind: str) -> None:
        if self._ocr_busy or self._cover_busy:
            return
        if not ScreenOCR.available():
            if kind == "cover":
                self._set_status(
                    "Windows OCR yok; kapak yapay zekâ ve görsel eşleşmeyle tanınacak…"
                )
                self._recognize_cover(source, "", "")
                return
            self._set_status(
                "Windows OCR motoru bulunamadı. Windows dil ayarlarından Türkçe OCR eklenmeli.",
                error=True,
            )
            self._cleanup_ocr_temp()
            return
        self._ocr_busy = True
        self._set_scan_buttons(False)
        self.camera_preview.set_scanning(True)
        self._set_status("TARANIYOR · Kare OCR için hazırlanıyor (metin kaydedilmez)…")

        def job() -> dict:
            processed = prepare_for_ocr(source)
            try:
                result = ScreenOCR().read_lines(processed, language="tr", limit=300)
                lines = [str(item.get("text") or "") for item in result.get("lines", [])]
                text = clean_ocr_text("\n".join(lines))
                return {
                    "text": text,
                    "isbn": extract_isbn(text) or "",
                    "kind": kind,
                    "source": str(source),
                }
            finally:
                processed.unlink(missing_ok=True)

        self._run_job(
            job,
            lambda payload: self._on_ocr_done(payload, kind),
            lambda error: self._on_ocr_failed(error, source, kind),
        )

    def _on_ocr_done(self, payload: dict, kind: str) -> None:
        if self._closing:
            return
        self._ocr_busy = False
        self.camera_preview.set_scanning(False)
        text = str(payload.get("text") or "").strip()
        isbn = str(payload.get("isbn") or "")
        source_text = str(payload.get("source") or "")
        source = Path(source_text) if source_text else None
        if kind == "cover":
            if source is not None and self.service is not None:
                if text:
                    self.last_scan_label.setText(
                        f"KAPAK OKUMASI · {len(text)} karakter (geçici)\n{text[:220]}"
                    )
                else:
                    self.last_scan_label.setText(
                        "KAPAK OCR · metin okunamadı; görsel tanıma kullanılıyor."
                    )
                self._recognize_cover(source, text, isbn)
                return
            self._cleanup_ocr_temp()
            self._set_scan_buttons(True)
            if isbn:
                self.search_input.setText(isbn)
                self._lookup(isbn)
            else:
                self._set_status(
                    "Kapak tanıma servisi yok; ISBN'i elle gir veya sayfa tara.",
                    error=True,
                )
            return
        self._cleanup_ocr_temp()
        self._set_scan_buttons(True)
        if not text:
            self._set_status(
                "Metin okunamadı. Kitabı daha iyi ışıkta, kareye dik ve yakın tutmayı dene.",
                error=True,
            )
            return
        length = len(text)
        if length < 25:
            self._set_status(
                "Sayfa metni çok kısa; daha iyi ışık ve odakla yeniden tara.",
                error=True,
            )
            return
        number = self.page_spin.value()
        self._upsert_page(number, text)
        self.page_spin.setValue(number + 1)
        self.last_scan_label.setText(
            f"SON TARAMA · s. {number} · {length} karakter (yalnızca bellekte)\n{text[:220]}"
        )
        self._set_status(
            f"Sayfa {number} belleğe alındı. Özet oluştururken yalnızca bu geçici metin kullanılır."
        )

    def _on_ocr_failed(self, error: str, source: Optional[Path] = None, kind: str = "") -> None:
        if self._closing:
            return
        self._ocr_busy = False
        self.camera_preview.set_scanning(False)
        if kind == "cover" and source is not None and self.service is not None:
            self.last_scan_label.setText(
                "KAPAK OCR · metin okunamadı; AI görsel tanıma kullanılıyor."
            )
            self._set_status(
                f"OCR metni okunamadı ({error}); kapak görselden tanınıyor…"
            )
            self._recognize_cover(source, "", "")
            return
        self._cleanup_ocr_temp()
        self._set_scan_buttons(True)
        self._set_status(f"OCR hatası: {error}", error=True)

    def _vision_identify(self, source: Path) -> tuple[dict, str]:
        try:
            answer = self.ai.ask(
                COVER_VISION_PROMPT,
                system_prompt=COVER_VISION_SYSTEM,
                record_history=False,
                image_paths=[str(source)],
            )
        except Exception as error:  # noqa: BLE001 - AI arızası aramayı durdurmaz
            return {}, str(error)[:220]
        text = str(answer or "").strip()
        if text.startswith(("❌", "⚠️")):
            return {}, text[:220]
        identified = parse_cover_vision(text)
        if not identified.get("title") and not identified.get("isbn"):
            return identified, "AI kapak metni okunamadı."
        return identified, ""

    def _recognize_cover(self, source: Path, ocr_text: str, isbn: str) -> None:
        if self._closing or self._cover_busy or self._ocr_busy:
            return
        if self.service is None:
            self._cleanup_ocr_temp()
            self._set_scan_buttons(True)
            self._set_status("Kapak tanıma için kitap servisi kullanılamıyor.", error=True)
            return
        self._cover_busy = True
        self._set_scan_buttons(False)
        self.camera_preview.set_scanning(True)
        self._set_status(
            "KAPAK TANIMA · AI künyesi + Google Books/Open Library + görsel eşleşme…"
            if ocr_text else
            "KAPAK TANIMA · AI görsel tanıma + Google Books/Open Library + görsel eşleşme…"
        )

        def job() -> dict:
            identified, ai_error = self._vision_identify(source)
            result = self.service.identify_cover(
                source,
                ocr_text=ocr_text,
                ocr_isbn=isbn,
                identified=identified,
            )
            return {"result": result, "ai_error": ai_error, "identified": identified}

        self._run_job(job, self._on_cover_done, self._on_cover_failed)

    def _candidate_label(self, item: dict) -> str:
        authors = ", ".join(item.get("authors") or []) or "bilinmiyor"
        label = f"{item.get('title') or 'Adsız'} — {authors}"
        similarity = item.get("cover_similarity")
        if similarity is not None:
            try:
                label += f" · görsel %{round(float(similarity) * 100)}"
            except (TypeError, ValueError):
                pass
        return label

    def _fill_cover_candidates(self, candidates: List[dict], chosen: Optional[dict]) -> int:
        self._results = list(candidates)
        self.results_combo.blockSignals(True)
        self.results_combo.clear()
        for item in self._results:
            self.results_combo.addItem(self._candidate_label(item))
        if chosen:
            chosen_key = chosen.get("isbn") or chosen.get("id")
            index = next(
                (
                    position for position, item in enumerate(self._results)
                    if (item.get("isbn") or item.get("id")) == chosen_key
                ),
                0,
            )
            self.results_combo.setCurrentIndex(max(0, index))
            self.results_combo.setVisible(len(self._results) > 1)
        else:
            self.results_combo.setCurrentIndex(-1)
            self.results_combo.setVisible(bool(self._results))
        self.results_combo.blockSignals(False)
        return len(self._results)

    def _on_cover_done(self, payload: dict) -> None:
        if self._closing:
            return
        self._cover_busy = False
        self._cleanup_ocr_temp()
        self._set_scan_buttons(True)
        self.camera_preview.set_scanning(False)
        result = payload.get("result") or {}
        book = result.get("book") or {}
        candidates = result.get("candidates") or []
        match = result.get("match") or {}
        queries = result.get("queries") or []
        ai_error = str(payload.get("ai_error") or "")
        self._fill_cover_candidates(candidates, book if book else None)
        reason = str(match.get("reason") or "").strip()
        note = f"AI notu: {ai_error}" if ai_error else ""
        if book:
            self._apply_book(book)
            self.search_input.setText(str(book.get("isbn") or book.get("title") or ""))
            details = []
            similarity = match.get("similarity")
            if similarity is not None:
                details.append(f"görsel %{round(float(similarity) * 100)}")
            if book.get("publisher"):
                details.append(str(book["publisher"]))
            translators = ", ".join(book.get("translators") or [])
            if translators:
                details.append(f"çeviri: {translators}")
            suffix = (" · " + " · ".join(details)) if details else ""
            self._set_status(
                f"Kapak tanındı · {book.get('title') or 'Adsız'}{suffix}. "
                f"{reason}"
            )
            self.last_scan_label.setText(
                f"KAPAK EŞLEŞTİ · {len(queries)} sorgu · {len(candidates)} aday · "
                f"{reason}"
            )
            return
        if candidates:
            self._set_status(
                f"Güvenli eşleşme yok · {len(candidates)} aday listelendi. "
                "Listeden seçebilir veya ISBN girebilirsin."
                + (f" · {note}" if note else "")
            )
            self.last_scan_label.setText(
                f"KAPAK ADAYLARI · {len(queries)} sorgu · {len(candidates)} aday · {reason}"
            )
            return
        self._set_status(
            "Kapak tanınamadı. "
            + (reason or "Daha iyi ışıkta, kapağı kareye dik tutarak yeniden tara.")
            + (f" · {note}" if note else ""),
            error=True,
        )

    def _on_cover_failed(self, error: str) -> None:
        if self._closing:
            return
        self._cover_busy = False
        self._cleanup_ocr_temp()
        self._set_scan_buttons(True)
        self.camera_preview.set_scanning(False)
        self._set_status(f"Kapak tanıma hatası: {error}", error=True)

    def _cleanup_ocr_temp(self) -> None:
        if self._ocr_temp is not None:
            try:
                self._ocr_temp.unlink(missing_ok=True)
            except OSError:
                pass
            self._ocr_temp = None

    def _set_scan_buttons(self, enabled: bool) -> None:
        for button in (self.cover_button, self.page_button, self.clear_pages_button):
            button.setEnabled(enabled)
        self.import_mode.setEnabled(enabled)

    # ------------------------------------------------------------------- künye
    def _manual_lookup(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self._set_status("ISBN veya kitap adı gir.", error=True)
            return
        self._lookup(query)

    def _lookup(self, query: str) -> None:
        if self.service is None:
            self._set_status("Kitap servisi bu oturumda kullanılamıyor.", error=True)
            return
        if self._lookup_busy or self._summary_busy or self._cover_busy:
            return
        self._lookup_busy = True
        self.search_button.setEnabled(False)
        self._set_status("Künye aranıyor · Open Library / Google Books…")
        normalized = normalize_isbn(query)

        def job() -> dict:
            if normalized:
                return {"book": self.service.lookup_isbn(normalized), "results": []}
            results = self.service.search(query, limit=8)
            if not results:
                raise BookError("Kitap künyesi bulunamadı. ISBN veya daha belirgin bir başlık dene.")
            return {"book": self._enrich(results[0]), "results": results}

        self._run_job(job, self._on_lookup_done, self._on_lookup_failed)

    def _enrich(self, book: dict) -> dict:
        """Arama sonucunu kapak/içindekiler/açıklama için tam künyeyle zenginleştirir."""
        if self.service is None:
            return dict(book)
        isbn = normalize_isbn(book.get("isbn"))
        if isbn:
            try:
                return self.service.lookup_isbn(isbn)
            except BookError:
                pass
        if book.get("cover_url"):
            try:
                return self.service.prepare(book)
            except BookError:
                pass
        return dict(book)

    def _on_lookup_done(self, payload: dict) -> None:
        if self._closing:
            return
        self._lookup_busy = False
        self.search_button.setEnabled(True)
        book = payload.get("book") or {}
        results = payload.get("results") or []
        self._results = results
        self.results_combo.blockSignals(True)
        self.results_combo.clear()
        if len(results) > 1:
            for item in results:
                authors = ", ".join(item.get("authors") or []) or "bilinmiyor"
                self.results_combo.addItem(f"{item.get('title') or 'Adsız'} — {authors}")
            self.results_combo.show()
        else:
            self.results_combo.hide()
        self.results_combo.blockSignals(False)
        if book:
            self._apply_book(book)
            count = f" · {len(results)} aday" if len(results) > 1 else ""
            self._set_status(f"Künye yüklendi: {book.get('title') or 'Adsız'}{count}.")
        else:
            self._set_status("Kitap künyesi bulunamadı.", error=True)

    def _on_lookup_failed(self, error: str) -> None:
        if self._closing:
            return
        self._lookup_busy = False
        self.search_button.setEnabled(True)
        self._set_status(f"Künye alınamadı: {error}", error=True)

    def _on_result_selected(self, index: int) -> None:
        if index < 0 or index >= len(self._results) or self._closing:
            return
        item = self._results[index]
        if item.get("toc") or item.get("description"):
            self._apply_book(item)
            self._set_status(f"Seçildi: {item.get('title') or 'Adsız'}")
            return
        if self._lookup_busy or self._cover_busy:
            return
        self._lookup_busy = True
        self.search_button.setEnabled(False)
        self._set_status(f"Aday künyesi alınıyor: {item.get('title') or 'Adsız'}…")

        def done(book: dict) -> None:
            if self._closing:
                return
            self._lookup_busy = False
            self.search_button.setEnabled(True)
            for key in ("cover_similarity", "metadata_score", "match_score", "match_accepted"):
                if key in item:
                    book[key] = item[key]
            self._results[index] = book
            self._apply_book(book)
            self._set_status(f"Seçildi: {book.get('title') or 'Adsız'}")

        def failed(error: str) -> None:
            if self._closing:
                return
            self._lookup_busy = False
            self.search_button.setEnabled(True)
            self._apply_book(item)
            self._set_status(f"Aday künyesi kısmi; seçim yine de yüklendi: {error}", error=True)

        self._run_job(lambda: self._enrich(item), done, failed)

    def _apply_book(self, book: dict, load_profile: bool = True) -> None:
        previous_id = (self.book or {}).get("id")
        new_id = book.get("id")
        if previous_id and previous_id != new_id:
            self._pages.clear()
            self.page_spin.setValue(1)
            self.last_scan_label.clear()
            self._update_pages_label()
        self.book = dict(book)
        self.title_label.setText(str(book.get("title") or "Adsız kitap"))
        authors = ", ".join(book.get("authors") or []) or "Yazar veride yok"
        self.authors_label.setText(authors)
        parts = []
        if book.get("publisher"):
            parts.append(str(book["publisher"]))
        if book.get("published"):
            parts.append(str(book["published"]))
        if book.get("page_count"):
            parts.append(f"{book['page_count']} sayfa")
        translators = ", ".join(book.get("translators") or [])
        if translators:
            parts.append(f"Çeviri: {translators}")
        if book.get("isbn"):
            parts.append(f"ISBN {book['isbn']}")
        self.meta_label.setText(" · ".join(parts) or "Künye alanları eksik.")
        categories = ", ".join(book.get("categories") or [])
        self.source_label.setText(
            f"KAYNAK · {book.get('source') or 'veride yok'}" + (f" · {categories}" if categories else "")
        )
        toc = book.get("toc") or []
        self.toc_label.setText(
            "İÇİNDEKİLER\n" + "\n".join(f"· {item}" for item in toc[:8])
            if toc else "İÇİNDEKİLER · veride yok"
        )
        description = str(book.get("description") or "").strip()
        self.description_label.setText(f"AÇIKLAMA · {description[:360]}" if description else "")
        self._set_cover(book)
        for button in (self.profile_button, self.pages_button, self.save_button, self.copy_button):
            button.setEnabled(True)
        if load_profile:
            profile = self.store.latest_profile(book["id"]) if self.store is not None else None
            if profile:
                self.summary_edit.setPlainText(str(profile.get("summary") or ""))
                self._last_mode = str(profile.get("kind") or "profile")
                self.summary_state.setText(
                    f"KAYITLI · {'PROFİL' if self._last_mode == 'profile' else 'SAYFA ÖZETİ'}"
                )
            else:
                self.summary_edit.clear()
                self.summary_state.setText("KAYIT YOK")
        self._refresh_summary_buttons()
        self.set_subtitle(f"KİTAP · {str(book.get('title') or 'Adsız')[:70]}".upper())

    def _set_cover(self, book: dict) -> None:
        path = str(book.get("cover_path") or "")
        pixmap = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        if pixmap.isNull():
            self.cover_label.setPixmap(QPixmap())
            self.cover_label.setText("KAPAK\nYOK")
            return
        self.cover_label.setText("")
        self.cover_label.setPixmap(
            pixmap.scaled(
                self.cover_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # ------------------------------------------------------------------- sayfa
    def _upsert_page(self, number: int, text: str) -> None:
        cleaned = clean_ocr_text(text)
        if not cleaned:
            return
        self._pages = [page for page in self._pages if int(page.get("number") or 0) != int(number)]
        self._pages.append({"number": int(number), "text": cleaned})
        self._pages.sort(key=lambda page: int(page["number"]))
        self._update_pages_label()

    def _update_pages_label(self) -> None:
        if not self._pages:
            self.pages_label.setText("Taranan sayfa yok · sayfa tarayınca özet sayfa referansı kullanır.")
        else:
            numbers = ", ".join(str(page["number"]) for page in self._pages[:16])
            extra = "…" if len(self._pages) > 16 else ""
            self.pages_label.setText(
                f"TARANAN SAYFALAR · {len(self._pages)} · s. {numbers}{extra} · metin yalnızca bellekte"
            )
        self._refresh_summary_buttons()

    def _clear_pages(self) -> None:
        self._pages.clear()
        self.page_spin.setValue(1)
        self.last_scan_label.clear()
        self._update_pages_label()
        self._set_status("Taranan sayfalar ve geçici OCR metinleri silindi.")

    def _refresh_summary_buttons(self) -> None:
        ready = self.book is not None
        self.profile_button.setEnabled(ready and not self._summary_busy)
        self.pages_button.setEnabled(ready and bool(self._pages) and not self._summary_busy)
        self.save_button.setEnabled(ready and not self._summary_busy)
        self.copy_button.setEnabled(ready and not self._summary_busy)

    # ------------------------------------------------------------------- özet
    def _generate_summary(self, mode: str) -> None:
        if self.book is None:
            self._set_status("Önce bir kitap künyesi yükle.", error=True)
            return
        if mode == "pages" and not self._pages:
            self._set_status("Sayfa özeti için en az bir sayfa tara.", error=True)
            return
        if self._summary_busy or self._ocr_busy or self._lookup_busy or self._cover_busy:
            return
        self._summary_busy = True
        self._last_mode = mode
        self._refresh_summary_buttons()
        pages = [dict(page) for page in self._pages] if mode == "pages" else []
        prompt = build_summary_prompt(self.book, pages, mode)
        self._set_status(
            "ÖZET ÜRETİLİYOR · Taranan metin geçici olarak modele gönderilir; sohbet geçmişine yazılmaz."
        )

        def job() -> str:
            text = self.ai.ask(
                prompt,
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                record_history=False,
            )
            return str(text or "")

        self._run_job(job, lambda text: self._on_summary_done(text, mode), self._on_summary_failed)

    def _on_summary_done(self, text: str, mode: str) -> None:
        if self._closing:
            return
        self._summary_busy = False
        self._refresh_summary_buttons()
        text = str(text or "").strip()
        if not text or text.startswith(("❌", "⚠️")) or len(text) < 20:
            self._set_status(text or "Özet üretilemedi.", error=True)
            return
        self.summary_edit.setPlainText(text)
        saved = self._persist_summary(text, mode)
        if saved:
            self._set_status(
                f"{summary_title(self.book or {}, mode)} kaydedildi. "
                "Yalnızca özet ve sayfa referansları saklandı; OCR metni silindi."
            )
        else:
            self._set_status("Özet üretildi ancak kitaplığa kaydedilemedi.")

    def _on_summary_failed(self, error: str) -> None:
        if self._closing:
            return
        self._summary_busy = False
        self._refresh_summary_buttons()
        self._set_status(f"Özet hatası: {error}", error=True)

    def _persist_summary(self, text: str, mode: str) -> bool:
        if self.store is None or self.book is None:
            return False
        model = ""
        try:
            if getattr(self.settings, "provider", "gemini") == "ollama":
                model = str(getattr(self.settings, "ollama_model", ""))
            elif getattr(self.settings, "provider", "gemini") == "tokenharbor":
                model = str(getattr(self.settings, "tokenharbor_model", ""))
            else:
                model = str(getattr(self.settings, "model", ""))
        except Exception:
            model = ""
        refs = [int(page["number"]) for page in self._pages] if mode == "pages" else []
        try:
            self.store.save_profile(
                str(self.book.get("id") or ""),
                mode,
                text,
                page_refs=refs,
                model=model,
            )
        except Exception as error:  # noqa: BLE001 - kullanıcıya durum bildirilir
            self._set_status(f"Özet kaydedilemedi: {error}", error=True)
            return False
        self.summary_state.setText(f"KAYITLI · {'PROFİL' if mode == 'profile' else 'SAYFA ÖZETİ'}")
        self._load_library()
        return True

    def _save_edited_summary(self) -> None:
        text = self.summary_edit.toPlainText().strip()
        if not text:
            self._set_status("Kaydedilecek özet boş.", error=True)
            return
        if self.book is None:
            self._set_status("Önce bir kitap künyesi yükle.", error=True)
            return
        mode = self._last_mode if self._last_mode in {"profile", "pages"} else "profile"
        if self._persist_summary(text, mode):
            self._set_status("Düzenlenen özet kitaplığa kaydedildi.")

    def _copy_summary(self) -> None:
        text = self.summary_edit.toPlainText().strip()
        if not text:
            self._set_status("Panoya kopyalanacak özet yok.", error=True)
            return
        QApplication.clipboard().setText(text)
        self._set_status("Özet panoya kopyalandı.")

    # ----------------------------------------------------------------- kitaplık
    def _load_library(self) -> None:
        if self.store is None or not hasattr(self, "library_combo"):
            return
        try:
            self._profiles = self.store.profiles(limit=20)
        except Exception:
            self._profiles = []
        self.library_combo.blockSignals(True)
        self.library_combo.clear()
        self.library_combo.addItem("Son özetler…")
        for profile in self._profiles:
            title = str(profile.get("book_title") or "Adsız kitap")
            kind = "profil" if profile.get("kind") == "profile" else "sayfa özeti"
            self.library_combo.addItem(f"{title[:44]} · {kind}")
        self.library_combo.blockSignals(False)

    def _on_library_selected(self, index: int) -> None:
        if index <= 0 or index - 1 >= len(self._profiles) or self._closing:
            return
        profile = self._profiles[index - 1]
        if self.store is None:
            return
        book = self.store.get_book(str(profile.get("book_id") or ""))
        if book is None:
            self._set_status("Bu özetin kitabı kitaplıkta bulunamadı.", error=True)
            return
        self._apply_book(book, load_profile=False)
        self.summary_edit.setPlainText(str(profile.get("summary") or ""))
        self._last_mode = str(profile.get("kind") or "profile")
        self.summary_state.setText("KAYITLI · GEÇMİŞ")
        self._set_status(f"Kitaplıktan yüklendi: {book.get('title') or 'Adsız'} · {profile.get('kind')}")

    # ------------------------------------------------------------------- ortak
    def _run_job(self, fn, on_done, on_failed) -> None:
        worker = _FunctionWorker(fn)
        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        self._jobs.append(worker)
        if len(self._jobs) > 8:
            self._jobs = self._jobs[-8:]
        threading.Thread(target=worker.run, daemon=True).start()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setStyleSheet(
            f"color:{U.RED_BRIGHT if error else U.TEXT_SOFT};background:transparent;border:none;"
        )
        self.status_label.setText(str(text or ""))

    # ------------------------------------------------------------------- kapanış
    def close_modal(self) -> None:
        self._closing = True
        self._stop_camera(quiet=True)
        self._cleanup_ocr_temp()
        self._pages.clear()
        self.summary_edit.clear()
        self.last_scan_label.clear()
        try:
            self.ai.cancel()
        except Exception:
            pass
        super().close_modal()

    def closeEvent(self, event):
        self._closing = True
        self._stop_camera(quiet=True)
        self._cleanup_ocr_temp()
        self._pages.clear()
        super().closeEvent(event)
