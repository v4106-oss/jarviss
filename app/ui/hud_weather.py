"""Hava durumu ve kırmızı temalı etkileşimli harita pencereleri."""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QFile, QIODevice, QObject, QPointF, QRectF, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from app.core.hud_data import LOCATION, weather_text
from app.theme import ultron as U
from .browser_service import BROWSER_AVAILABLE, browser_service
from .hud_modal import HudModal, card, stat_card
from .hud_widgets import (
    EmptyState,
    HudButton,
    TempCurve,
    WeatherIcon,
    hud_font,
    hud_label,
    round_pen,
)

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "leaflet"


class SunArc(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(118)
        self.sunrise = "—"
        self.sunset = "—"
        self.progress = 0.0

    def set_sun(self, sunrise: str, sunset: str) -> None:
        self.sunrise = sunrise or "—"
        self.sunset = sunset or "—"
        try:
            start = datetime.strptime(self.sunrise, "%H:%M")
            end = datetime.strptime(self.sunset, "%H:%M")
            now = datetime.now()
            total = (end - start).total_seconds()
            elapsed = (now - start).total_seconds()
            self.progress = max(0.0, min(1.0, elapsed / total)) if total > 0 else 0.0
        except (TypeError, ValueError):
            self.progress = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        left, right = 24.0, width - 24.0
        top, bottom = 24.0, height - 34.0
        rect = QRectF(left, top, right - left, (bottom - top) * 2)

        pen = QPen(QColor(255, 255, 255, 34), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 180 * 16)

        def point_at(fraction: float) -> QPointF:
            angle = math.pi * (1 - fraction)
            center_x = (left + right) / 2
            return QPointF(center_x + math.cos(angle) * (right - left) / 2, bottom - math.sin(angle) * (bottom - top))

        sun = point_at(self.progress)
        painter.setPen(QPen(QColor(U.GOLD_DIM), 1.0))
        painter.setBrush(QColor(U.GOLD))
        painter.drawEllipse(sun, 3.2, 3.2)
        glow = QColor(U.GOLD)
        glow.setAlpha(55)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(sun, 9, 9)

        painter.setFont(hud_font(8, QFont.Weight.DemiBold, 1.2))
        painter.setPen(QColor(U.TEXT_SOFT))
        painter.drawText(QRectF(left - 22, bottom + 8, 70, 16), Qt.AlignmentFlag.AlignLeft, self.sunrise)
        painter.drawText(QRectF(right - 48, bottom + 8, 70, 16), Qt.AlignmentFlag.AlignRight, self.sunset)
        painter.setPen(QColor(U.DIM))
        painter.setFont(hud_font(6, QFont.Weight.Medium, 1.2))
        painter.drawText(QRectF(left - 22, bottom + 24, 70, 12), Qt.AlignmentFlag.AlignLeft, "GÜN DOĞUMU")
        painter.drawText(QRectF(right - 48, bottom + 24, 70, 12), Qt.AlignmentFlag.AlignRight, "GÜN BATIMI")
        painter.setPen(QColor(U.GOLD))
        painter.setFont(hud_font(7, QFont.Weight.DemiBold, 1.2))
        painter.drawText(QRectF(0, 0, width, 18), Qt.AlignmentFlag.AlignHCenter, "GÜNEŞ YAYI")


class DailyRange(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(8)
        self.low = 0.0
        self.high = 1.0
        self.global_low = 0.0
        self.global_high = 1.0

    def set_range(self, low: float, high: float, global_low: float, global_high: float) -> None:
        self.low, self.high = float(low), float(high)
        self.global_low, self.global_high = float(global_low), float(global_high)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        span = max(1.0, self.global_high - self.global_low)
        start = (self.low - self.global_low) / span
        end = (self.high - self.global_low) / span
        margin = 3.0
        usable = self.width() - margin * 2
        y = self.height() / 2
        painter.setPen(QPen(QColor(U.FAINT), 1.0))
        painter.drawLine(QPointF(margin, y), QPointF(self.width() - margin, y))
        painter.setPen(round_pen(QColor(U.GOLD), 2.0))
        painter.drawLine(QPointF(margin + usable * start, y), QPointF(margin + usable * end, y))


class ForecastCard(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("forecastCard")
        self.setStyleSheet(
            f"QFrame#forecastCard{{background:{U.PANEL_2};border:1px solid {U.BORDER};border-radius:8px;}}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        self.day_label = hud_label("—", 8, U.TEXT_SOFT, QFont.Weight.Medium, 0.2)
        self.date_label = hud_label("—", 7, U.DIM, QFont.Weight.Medium, 0.8)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.day_label)
        top.addStretch(1)
        self.icon = WeatherIcon("cloud", U.GOLD, 26)
        top.addWidget(self.icon)
        layout.addLayout(top)
        layout.addWidget(self.date_label)
        temps = QHBoxLayout()
        temps.setSpacing(8)
        self.max_label = hud_label("—", 18, U.TEXT, QFont.Weight.DemiBold, 0.4)
        self.min_label = hud_label("—", 10, U.MUTED, QFont.Weight.Medium, 0.4)
        temps.addWidget(self.max_label)
        temps.addWidget(self.min_label)
        temps.addStretch(1)
        layout.addLayout(temps)
        self.range = DailyRange()
        layout.addWidget(self.range)
        self.text_label = hud_label("—", 8, U.MUTED, QFont.Weight.Medium, 0.4)
        layout.addWidget(self.text_label)

    def set_day(self, data: dict, global_low: float, global_high: float) -> None:
        self.day_label.setText(str(data.get("label", "—")))
        self.date_label.setText(str(data.get("date", "—")))
        self.max_label.setText(f"{data.get('max', '—')}°")
        self.min_label.setText(f"{data.get('min', '—')}°")
        self.icon.set_kind(str(data.get("kind", "cloud")))
        self.range.set_range(
            data.get("min", global_low),
            data.get("max", global_high),
            global_low,
            global_high,
        )
        self.text_label.setText(str(data.get("text", "—")))


class WeatherModal(HudModal):
    refresh_requested = Signal()

    def __init__(self, parent, weather: Optional[dict] = None) -> None:
        super().__init__(parent, "Hava Durumu", "Veri yükleniyor…", width=1000)
        self.weather = weather
        self.hero = card(padding=16)
        hero_layout = QHBoxLayout()
        hero_layout.setSpacing(16)
        icon_holder = QWidget()
        icon_layout = QHBoxLayout(icon_holder)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        self.hero_icon = WeatherIcon("cloud", U.GOLD, 58)
        icon_layout.addWidget(self.hero_icon)
        self.temp_label = hud_label("—°", 42, U.TEXT, QFont.Weight.DemiBold, 1.0)
        info = QVBoxLayout()
        info.setSpacing(4)
        self.desc_label = hud_label("Hava durumu yükleniyor…", 14, U.TEXT, QFont.Weight.DemiBold, 0.6)
        self.feels_label = hud_label("Hissedilen —", 8, U.MUTED, QFont.Weight.Medium, 0.8)
        self.range_label = hud_label("En yüksek — · En düşük —", 8, U.MUTED, QFont.Weight.Medium, 0.8)
        info.addWidget(self.desc_label)
        info.addWidget(self.feels_label)
        info.addWidget(self.range_label)
        info.addStretch(1)
        hero_layout.addWidget(self.hero_icon)
        hero_layout.addWidget(self.temp_label)
        hero_layout.addLayout(info, 1)
        self.hero.layout().addLayout(hero_layout)

        self.sun_card = card(padding=10)
        self.sun_arc = SunArc()
        self.sun_card.layout().addWidget(self.sun_arc)
        self.moon_card = card(padding=14)
        self.moon_card.layout().addWidget(hud_label("AY", 7, U.DIM, QFont.Weight.DemiBold, 1.8, upper=True))
        self.moon_label = hud_label("—", 13, U.TEXT, QFont.Weight.DemiBold, 0.6)
        self.moon_label.setWordWrap(True)
        self.moon_card.layout().addWidget(self.moon_label)
        self.moon_note = hud_label("AY EVRESİ", 7, U.MUTED, QFont.Weight.Medium, 0.8)
        self.moon_card.layout().addWidget(self.moon_note)
        self.moon_card.layout().addStretch(1)

        hero_row = QHBoxLayout()
        hero_row.setSpacing(10)
        hero_row.addWidget(self.hero, 4)
        hero_row.addWidget(self.sun_card, 2)
        hero_row.addWidget(self.moon_card, 2)
        self.content.addLayout(hero_row)

        metrics = QGridLayout()
        metrics.setSpacing(8)
        self.humidity_card = stat_card("Nem", "%—", "", 0)
        self.wind_card = stat_card("Rüzgar", "— km/s", "YÖN —", None)
        self.pressure_card = stat_card("Basınç", "— hPa", "—", None)
        self.visibility_card = stat_card("Görüş", "— km", "—", None)
        self.uv_card = stat_card("UV", "—", "—", None)
        self.cloud_card = stat_card("Bulut", "%—", "—", 0)
        metric_cards = [
            self.humidity_card, self.wind_card, self.pressure_card,
            self.visibility_card, self.uv_card, self.cloud_card,
        ]
        for index, widget in enumerate(metric_cards):
            metrics.addWidget(widget, 0, index)
        self.content.addLayout(metrics)

        curve_card = card(padding=12)
        curve_head = QHBoxLayout()
        curve_head.addWidget(hud_label("Saatlik Sıcaklık", 8, U.MUTED, QFont.Weight.Medium, 0.3))
        curve_head.addStretch(1)
        self.updated_label = hud_label("—", 7, U.DIM, QFont.Weight.Medium, 1.0)
        curve_head.addWidget(self.updated_label)
        curve_card.layout().addLayout(curve_head)
        self.curve = TempCurve()
        curve_card.layout().addWidget(self.curve)
        self.content.addWidget(curve_card)

        self.daily_row = QHBoxLayout()
        self.daily_row.setSpacing(10)
        self.daily_cards = [ForecastCard(), ForecastCard(), ForecastCard()]
        for forecast in self.daily_cards:
            self.daily_row.addWidget(forecast, 1)
        self.content.addLayout(self.daily_row)
        self.content.addStretch(1)

        self.actions = QHBoxLayout()
        self.actions.addStretch(1)
        refresh = HudButton("YENİLE")
        refresh.clicked.connect(self.refresh_requested.emit)
        close = HudButton("KAPAT", accent=True)
        close.clicked.connect(self.close_modal)
        self.actions.addWidget(refresh)
        self.actions.addWidget(close)
        self.content.addLayout(self.actions)

        if weather:
            self.set_weather(weather)
        elif self.weather is None:
            self.set_subtitle("VERİ BEKLENİYOR · BAĞLANTI KURULUYOR")

    def set_error(self, error: str) -> None:
        message = str(error or "").strip()
        self.set_subtitle(f"HAVA VERİSİ ALINAMADI · {message}" if message else "HAVA VERİSİ ALINAMADI")
        if not self.weather:
            self.desc_label.setText("Hava durumu alınamadı.")
            self.feels_label.setText("İNTERNET BAĞLANTISINI KONTROL EDİN")
            self.range_label.setText("OPEN-METEO SERVİSİNE ULAŞILAMADI")

    def set_weather(self, weather: dict) -> None:
        self.weather = weather or {}
        current = self.weather.get("current") or {}
        daily = self.weather.get("daily") or []
        location = str(self.weather.get("location", "Derince · Turkey"))
        updated = str(self.weather.get("updated", "—"))
        stale = " · ÖNBELLEK" if self.weather.get("stale") else ""
        self.set_subtitle(f"{location.upper()} · GÜNCELLENDİ {updated}{stale}")

        code = current.get("code", 0)
        kind = str(current.get("kind", "cloud"))
        text = str(current.get("text") or weather_text(code))
        self.hero_icon.set_kind(kind)
        self.temp_label.setText(f"{current.get('temp', '—')}°")
        self.desc_label.setText(text)
        self.feels_label.setText(f"HİSSEDİLEN {current.get('feels', '—')}°")
        if daily:
            self.range_label.setText(f"EN YÜKSEK {daily[0].get('max', '—')}° · EN DÜŞÜK {daily[0].get('min', '—')}°")
        self.sun_arc.set_sun(str(current.get("sunrise", "—")), str(current.get("sunset", "—")))
        self.moon_label.setText(str(current.get("moon", "—")))
        self.moon_note.setText("AY EVRESİ · TÜRKİYE")

        humidity = int(current.get("humidity", 0) or 0)
        self.humidity_card.value_label.setText(f"%{humidity}")
        self.humidity_card.note_label.setText("NEMLİ" if humidity >= 70 else ("KURU" if humidity <= 35 else "DENGELİ"))
        self.humidity_card.meter.set_value(humidity)

        wind = current.get("wind", "—")
        self.wind_card.value_label.setText(f"{wind} km/s")
        self.wind_card.note_label.setText(f"{current.get('wind_dir', '—')} YÖNÜNDEN")

        pressure = int(current.get("pressure", 0) or 0)
        self.pressure_card.value_label.setText(f"{pressure} hPa")
        self.pressure_card.note_label.setText("YÜKSEK BASINÇ" if pressure >= 1013 else "DÜŞÜK BASINÇ")

        visibility = current.get("visibility")
        self.visibility_card.value_label.setText(f"{visibility if visibility is not None else '—'} km")
        try:
            self.visibility_card.note_label.setText("AÇIK" if float(visibility) >= 8 else "SINIRLI")
        except (TypeError, ValueError):
            self.visibility_card.note_label.setText("—")

        uv = current.get("uv", 0)
        self.uv_card.value_label.setText(str(uv))
        try:
            uv_value = float(uv)
            self.uv_card.note_label.setText("YÜKSEK" if uv_value >= 6 else ("ORTA" if uv_value >= 3 else "DÜŞÜK"))
        except (TypeError, ValueError):
            self.uv_card.note_label.setText("—")

        cloud = int(current.get("cloud", 0) or 0)
        self.cloud_card.value_label.setText(f"%{cloud}")
        precipitation = float(current.get("precip", 0) or 0)
        self.cloud_card.note_label.setText(f"{precipitation:.1f} mm YAĞIŞ")
        self.cloud_card.meter.set_value(cloud)

        self.curve.set_hours(self.weather.get("hourly") or [])
        self.updated_label.setText(f"GÜNCELLENDİ {updated}")
        low = min((float(day.get("min", 0)) for day in daily), default=0.0)
        high = max((float(day.get("max", 0)) for day in daily), default=1.0)
        for index, forecast in enumerate(self.daily_cards):
            if index < len(daily):
                forecast.show()
                forecast.set_day(daily[index], low, high)
            else:
                forecast.hide()


def _read_leaflet_assets() -> tuple[str, str]:
    try:
        css = (ASSETS / "leaflet.css").read_text(encoding="utf-8")
        js = (ASSETS / "leaflet.js").read_text(encoding="utf-8")
    except OSError:
        return "", ""
    return css, js


def _read_qwebchannel_js() -> str:
    # PySide6 6.12 artık qrc:///qtwebchannel/qwebchannel.js yolunu web sayfasına
    # sunmuyor; bu yüzden betiği Qt kaynağından okuyup satır içi gömeriz.
    try:
        resource = QFile(":/qtwebchannel/qwebchannel.js")
        if resource.open(QIODevice.OpenModeFlag.ReadOnly):
            return bytes(resource.readAll()).decode("utf-8")
    except Exception:
        pass
    return ""


class _PoiQueryWorker(QObject):
    done = Signal(object)

    def __init__(self, querier, lat: float, lon: float, zoom: float) -> None:
        super().__init__()
        self._querier = querier
        self._lat = lat
        self._lon = lon
        self._zoom = zoom

    def run(self) -> None:
        try:
            payload = self._querier(self._lat, self._lon, self._zoom) or {}
        except Exception as error:
            payload = {"error": str(error)}
        self.done.emit(payload)


class MapModal(HudModal):
    home_changed = Signal(dict)

    def __init__(self, parent, settings=None) -> None:
        super().__init__(parent, "Harita", "", width=1120, height=700, fade=False, persistent=True)
        self.settings = settings
        self.home = self._read_home()
        self._pending_payload = None
        self._loaded = False
        self._init_started = False
        self._background_load = False
        self._initial_poi_done = False
        self.web = None
        self._browser = None
        self._link_opener = None
        self._click_querier = None
        self._category_querier = None
        self._route_querier = None
        self._poi_busy = False
        self._poi_silent = False
        self._poi_next = None
        self._poi_worker = None
        self._category_busy = False
        self._category_worker = None
        self._route_busy = False
        self._route_worker = None
        self._refresh_subtitle()
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.status_label = hud_label("HARİTA YÜKLENİYOR…", 7, U.MUTED, QFont.Weight.Medium, 1.4)
        controls.addWidget(self.status_label, 1)
        zoom_in = HudButton("YAKINLAŞ", small=True)
        zoom_in.clicked.connect(lambda: self._run("window.jarvisMap && window.jarvisMap.zoomIn();"))
        zoom_out = HudButton("UZAKLAŞ", small=True)
        zoom_out.clicked.connect(lambda: self._run("window.jarvisMap && window.jarvisMap.zoomOut();"))
        self.home_button = HudButton("EVİM YAP", small=True)
        self.home_button.clicked.connect(self._save_center_as_home)
        recenter = HudButton("EVİM", small=True, accent=True)
        recenter.clicked.connect(
            lambda: self._run("window.jarvisMap && window.jarvisRecenterHome(13);")
        )
        controls.addWidget(zoom_in)
        controls.addWidget(zoom_out)
        controls.addWidget(self.home_button)
        controls.addWidget(recenter)
        self.content.addLayout(controls)
        self.map_placeholder = EmptyState("HARİTA HAZIRLANIYOR…")
        self.map_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_placeholder.setMinimumHeight(470)
        self.content.addWidget(self.map_placeholder, 1)
        QTimer.singleShot(80, self._init_map_browser)

    def _init_map_browser(self) -> None:
        if self._closed or self._init_started:
            return
        self._init_started = True
        self.status_label.setText("HARİTA MOTORU HAZIRLANIYOR…")
        assets = _read_leaflet_assets()
        if not assets:
            self._set_placeholder("Leaflet dosyaları bulunamadı; assets/leaflet klasörünü kontrol edin.")
            self.status_label.setText("HARİTA KÜTÜPHANESİ EKSİK")
            return

        if not BROWSER_AVAILABLE:
            self._set_placeholder("Qt WebEngine bulunamadı; harita motoru eksik.")
            self.status_label.setText("HARİTA MOTORU EKSİK")
            return

        try:
            self._browser = browser_service()
            self.web = self._browser.attach_map(self.content, self.body)
            self.web.setMinimumHeight(470)
            self._browser.poi_clicked.connect(self._on_poi_clicked)
            self._browser.category_requested.connect(self._on_category_clicked)
            self._browser.route_requested.connect(self._on_route_requested)
            self._browser.external_link_requested.connect(self._on_external_link)
            self._browser.map_ready.connect(self._on_loaded)
            QTimer.singleShot(120, self._invalidate_map)
        except Exception as error:
            self._browser = None
            self.web = None
            self._set_placeholder(f"Harita motoru başlatılamadı: {error}")
            self.status_label.setText("HARİTA MOTORU EKSİK")
            return

        if self._browser.map_loaded:
            self._loaded = True
            self._reveal_map_view()
            self.status_label.setText("© OSM + OVERTURE · KIRMIZI MOD")
            if self._browser.sync_home(self.home, self._home_is_set()):
                self._run("window.jarvisRecenterHome && window.jarvisRecenterHome(15);")
        else:
            css, js = assets
            page = _MAP_HTML.replace("__LEAFLET_CSS__", css).replace("__LEAFLET_JS__", js)
            page = page.replace("__QTWEBCHANNEL_JS__", _read_qwebchannel_js())
            page = page.replace("__HOME_JSON__", json.dumps(self.home, ensure_ascii=False))
            self._browser.load_map(page, QUrl("https://jarvis.local/map"), self.home)

    def _set_placeholder(self, text: str) -> None:
        placeholder = getattr(self, "map_placeholder", None)
        if placeholder is not None:
            placeholder.setText(text)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _reveal_map_view(self) -> None:
        placeholder = getattr(self, "map_placeholder", None)
        if placeholder is not None:
            self.map_placeholder = None
            placeholder.hide()
            placeholder.deleteLater()
        browser = self._browser
        if browser is not None:
            try:
                browser.show_map()
            except RuntimeError:
                pass

    def reopen(self) -> None:
        if self._closed:
            self._closed = False
        if self._loaded:
            self.status_label.setText("© OSM + OVERTURE · KIRMIZI MOD")
            if self._background_load:
                self._background_load = False
                pending, self._pending_payload = self._pending_payload, None
                if pending:
                    self.apply_payload(pending)
                elif not self._initial_poi_done:
                    QTimer.singleShot(60, self._initial_poi)
        elif not self._init_started:
            QTimer.singleShot(0, self._init_map_browser)
        QTimer.singleShot(40, self._invalidate_map)

    def _initial_poi(self) -> None:
        if not self._closed and self._loaded:
            self._initial_poi_done = True
            self._on_poi_clicked(
                float(self.home["lat"]), float(self.home["lon"]), 15, silent=True
            )

    def showEvent(self, event):
        super().showEvent(event)
        if self._loaded:
            QTimer.singleShot(40, self._invalidate_map)

    def _read_home(self) -> dict:
        if self.settings is not None:
            try:
                home = dict(self.settings.home_location)
                home["lat"] = float(home["lat"])
                home["lon"] = float(home["lon"])
                home["is_set"] = bool(self.settings.home_is_set)
                return home
            except Exception:
                pass
        return {
            "name": str(LOCATION.get("name") or "Derince"),
            "region": "Kocaeli",
            "lat": float(LOCATION.get("lat") or 40.757),
            "lon": float(LOCATION.get("lon") or 29.815),
        }

    def _home_is_set(self) -> bool:
        try:
            return bool(self.settings is not None and self.settings.home_is_set)
        except Exception:
            return False

    def _refresh_subtitle(self) -> None:
        tag = "EVİM" if self._home_is_set() else "MERKEZ"
        raw_name = str(self.home.get("name") or "").strip()
        name = raw_name.upper()
        head = tag if not raw_name or raw_name.casefold() == "evim" else f"{tag} · {name}"
        region = str(self.home.get("region") or "").upper()
        region_text = f" · {region}" if region else ""
        self.set_subtitle(
            f"{head}{region_text} · "
            f"{float(self.home['lat']):.3f}°N {float(self.home['lon']):.3f}°E"
        )

    def _run(self, script: str) -> None:
        if getattr(self, "web", None) is not None:
            try:
                self.web.page().runJavaScript(script)
            except Exception:
                pass

    def _invalidate_map(self) -> None:
        if not self._closed:
            self._run("window.jarvisMap && window.jarvisMap.invalidateSize();")

    def apply_payload(self, payload: dict) -> None:
        if not payload:
            return
        self._initial_poi_done = True
        if payload.get("kind") == "home" and payload.get("home"):
            self.home = dict(payload["home"])
            self.home["is_set"] = True
            self._refresh_subtitle()
        if payload.get("kind") == "home":
            self.status_label.setText(
                f"EV KONUMU · {str(self.home.get('name', '')).upper()} · "
                f"{float(self.home['lat']):.3f}°N {float(self.home['lon']):.3f}°E"
            )
        elif payload.get("kind") == "route":
            self.status_label.setText(
                f"ROTA · {payload.get('mode_label', '')} · {payload.get('distance_km', 0)} KM · "
                f"{payload.get('duration_min', 0)} DK"
            )
        elif payload.get("kind") == "places":
            self.status_label.setText(f"HARİTA · {str(payload.get('title', '')).upper()}")
        elif payload.get("kind") == "mall":
            mall = payload.get("mall") or {}
            count = len(payload.get("stores") or [])
            meta = f"{count} MAĞAZA" if count or not payload.get("store_error") else "LİSTE ALINAMADI"
            self.status_label.setText(
                f"AVM · {str(mall.get('name', '')).upper()} · {meta}"
            )
        elif payload.get("kind") == "bus_line":
            line = payload.get("line") or {}
            self.status_label.setText(
                f"OTOBÜS {line.get('code', '')} · {str(line.get('label', '')).upper()}"
            )
        elif payload.get("kind") == "focus":
            center = payload.get("center") or {}
            self.status_label.setText(f"KONUM · {str(center.get('name', '')).upper()}")
        if not self._loaded:
            self._pending_payload = payload
            return
        try:
            script = "window.jarvisApply(" + json.dumps(payload, ensure_ascii=False) + ");"
            self._run(script)
        except Exception:
            pass

    def set_click_querier(self, querier) -> None:
        self._click_querier = querier

    def set_category_querier(self, querier) -> None:
        self._category_querier = querier

    def set_route_querier(self, querier) -> None:
        self._route_querier = querier

    def _on_poi_clicked(self, lat: float, lon: float, zoom: float, silent: bool = False) -> None:
        if not callable(self._click_querier):
            return
        if self._poi_busy:
            self._poi_next = (lat, lon, zoom, silent)
            return
        self._poi_busy = True
        self._poi_silent = silent
        if not silent:
            self.status_label.setText("MEKÂN TARANIYOR…")
        self._poi_worker = _PoiQueryWorker(self._click_querier, lat, lon, zoom)
        self._poi_worker.done.connect(self._on_poi_result)
        threading.Thread(target=self._poi_worker.run, daemon=True).start()

    def _on_poi_result(self, payload) -> None:
        self._poi_busy = False
        silent, self._poi_silent = self._poi_silent, False
        next_click, self._poi_next = self._poi_next, None
        if self._closed or getattr(self, "web", None) is None:
            return
        if next_click:
            self._on_poi_clicked(*next_click)
            return
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("error"):
            self.status_label.setText("MEKÂN BULUNAMADI · TEKRAR DENE")
        else:
            count = len(payload.get("items") or [])
            title = str(payload.get("title") or "MEKÂNLAR").upper()
            self.status_label.setText(
                f"HARİTA · {title}" if count else "MEKÂN BULUNAMADI"
            )
        try:
            self._run(
                "window.jarvisPoiDone(" + json.dumps(payload, ensure_ascii=False)
                + (", true" if silent else ", false") + ");"
            )
        except Exception:
            pass

    def _on_category_clicked(self, category: str, lat: float, lon: float, zoom: float) -> None:
        if not callable(self._category_querier) or self._category_busy:
            return
        self._category_busy = True
        self.status_label.setText(
            "AVM'LER ARANIYOR…" if category == "mall" else "MEKÂNLAR ARANIYOR…"
        )
        querier = self._category_querier
        worker = _PoiQueryWorker(
            lambda worker_lat, worker_lon, worker_zoom: querier(
                category, worker_lat, worker_lon, worker_zoom
            ),
            lat,
            lon,
            zoom,
        )
        self._category_worker = worker
        worker.done.connect(self._on_category_result)
        threading.Thread(target=worker.run, daemon=True).start()

    def _on_category_result(self, payload) -> None:
        self._category_busy = False
        if self._closed or getattr(self, "web", None) is None:
            return
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("error"):
            self.status_label.setText("AVM BULUNAMADI · TEKRAR DENE")
        else:
            count = len(payload.get("items") or [])
            self.status_label.setText(f"HARİTA · {count} AVM" if count else "YAKINDA AVM YOK")
        try:
            self._run(
                "window.jarvisCategoryDone("
                + json.dumps(payload, ensure_ascii=False)
                + ", 'mall');"
            )
        except Exception:
            pass

    def set_link_opener(self, opener) -> None:
        self._link_opener = opener

    def _on_external_link(self, url: str) -> None:
        if self._closed:
            return
        target = QUrl(str(url or "").strip())
        if target.scheme().lower() not in {"http", "https"} or not target.host():
            return
        if callable(self._link_opener):
            self._link_opener(target.toString())
            self.status_label.setText("BAĞLANTI JARVIS TARAYICISINDA AÇILIYOR")
            return
        if QDesktopServices.openUrl(target):
            self.status_label.setText("BAĞLANTI VARSAYILAN TARAYICIDA AÇILIYOR")
        else:
            self.status_label.setText("BAĞLANTI AÇILAMADI")

    def _on_route_requested(self, lat: float, lon: float, name: str) -> None:
        if self._closed or self._route_busy:
            return
        try:
            lat_value = float(lat)
            lon_value = float(lon)
        except (TypeError, ValueError):
            return
        if not (-90 <= lat_value <= 90 and -180 <= lon_value <= 180):
            return
        if not callable(self._route_querier):
            self.status_label.setText("ROTA HESAPLANAMADI · HARİTA SERVİSİ HAZIR DEĞİL")
            return
        self._route_busy = True
        target_name = str(name or "Hedef").strip() or "Hedef"
        self.status_label.setText(f"ROTA HESAPLANIYOR · {target_name.upper()}…")
        querier = self._route_querier
        worker = _PoiQueryWorker(
            lambda worker_lat, worker_lon, _zoom: querier(worker_lat, worker_lon, target_name),
            lat_value,
            lon_value,
            0,
        )
        self._route_worker = worker
        worker.done.connect(self._on_route_result)
        threading.Thread(target=worker.run, daemon=True).start()

    def _on_route_result(self, payload) -> None:
        self._route_busy = False
        if self._closed or getattr(self, "web", None) is None:
            return
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("error") or not payload.get("points"):
            message = str(payload.get("error") or "uygun rota bulunamadı").upper()
            self.status_label.setText(f"ROTA OLUŞTURULAMADI · {message}")
            return
        payload["kind"] = "route"
        self.apply_payload(payload)

    def _save_center_as_home(self) -> None:
        if getattr(self, "web", None) is None:
            return
        self.web.page().runJavaScript(
            "JSON.stringify(window.jarvisMap ? window.jarvisMap.getCenter() : null);",
            self._save_home_center,
        )

    def _save_home_center(self, value) -> None:
        try:
            center = json.loads(value) if isinstance(value, str) else value
            lat = float(center["lat"])
            lon = float(center["lng"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            self.status_label.setText("EV KONUMU ALINAMADI · HARİTAYI HAREKET ETTİRİP TEKRAR DENE")
            return
        region = "Kocaeli" if 40.4 <= lat <= 41.4 and 29.0 <= lon <= 30.6 else ""
        home = {"name": "Evim", "region": region, "lat": lat, "lon": lon}
        if self.settings is not None:
            try:
                home = dict(self.settings.set_home(lat, lon, "Evim", region))
            except Exception as error:
                self.status_label.setText(f"EV KONUMU KAYDEDİLEMEDİ · {error}")
                return
        self.home = home
        self._refresh_subtitle()
        self._run(
            "window.jarvisSetHome("
            + f"{lat:.6f}, {lon:.6f}, "
            + json.dumps(home["name"], ensure_ascii=False)
            + ", true);"
        )
        self.status_label.setText(
            f"EV KONUMU KAYDEDİLDİ · {home['name'].upper()} · {lat:.3f}°N {lon:.3f}°E"
        )
        if self._browser is not None:
            self._browser.remember_home(home)
        self.home_changed.emit(dict(home))

    def _on_loaded(self, ok: bool) -> None:
        if ok:
            self._loaded = True
            self._reveal_map_view()
        if self._closed or not self.isVisible():
            if ok:
                self._background_load = True
            return
        if ok:
            self.status_label.setText("© OSM + OVERTURE · KIRMIZI MOD")
            pending, self._pending_payload = self._pending_payload, None
            if pending:
                self.apply_payload(pending)
            else:
                self._initial_poi()
        else:
            self.status_label.setText("HARİTA YÜKLENEMEDİ · İNTERNET BAĞLANTISI GEREKLİ")


_MAP_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis Harita</title>
<style>__LEAFLET_CSS__</style>
<style>
  html, body { margin:0; padding:0; height:100%; background:#050506; overflow:hidden;
               font-family:"Segoe UI", sans-serif; }
  #map { position:absolute; inset:0; background:#121418; }
  .leaflet-tile { transform: translateZ(0); }
  .dark-tiles .leaflet-tile-pane {
      filter: invert(1) hue-rotate(180deg) brightness(0.82) contrast(0.85) saturate(0.35);
  }
  .leaflet-container { background:#121418; color:#c6c9d1; font-family:"Segoe UI", sans-serif; }
  .leaflet-control-zoom a {
      background:#0b0b0e !important; color:#ececee !important; border:1px solid #342029 !important;
      border-radius:0 !important; width:28px; height:28px; line-height:26px; font-weight:500;
  }
  .leaflet-control-zoom a:hover { background:#1a0d12 !important; color:#ff4d64 !important; }
  .leaflet-control-zoom { border:none !important; box-shadow:none !important; }
  .leaflet-control-attribution {
      background:rgba(5,5,6,0.72) !important; color:#57575f !important; font-size:9px !important;
      border:1px solid #1b1116;
  }
  .leaflet-control-attribution a { color:#8a8a92 !important; }
  .leaflet-control-scale-line {
      background:rgba(5,5,6,0.65) !important; color:#8a8a92 !important;
      border:1px solid #5a1622 !important; border-top:none !important;
  }
  .leaflet-popup-content-wrapper, .leaflet-popup-tip {
      background:#0c0c10; color:#ececee; border:1px solid #5a1622; border-radius:6px;
      box-shadow:0 0 24px rgba(232,57,79,0.16);
  }
  .leaflet-popup-content { margin:10px 14px; font-size:11px; letter-spacing:0.4px; line-height:1.55; }
  .leaflet-popup-close-button { color:#8a8a92 !important; }
  .marker-core { width:11px; height:11px; border-radius:50%; background:#ff4d64;
      box-shadow:0 0 0 4px rgba(255,77,100,0.18), 0 0 22px rgba(255,77,100,0.65);
      border:1px solid #ffd9df; }
  .marker-ring { position:absolute; left:-11px; top:-11px; width:32px; height:32px;
      border:1px solid rgba(255,77,100,0.55); border-radius:50%;
      animation: pulse 2.4s ease-out infinite; }
  @keyframes pulse { 0% {transform:scale(0.5); opacity:0.9;} 100% {transform:scale(1.8); opacity:0;} }
  .marker-label { position:absolute; left:22px; top:-5px; color:#f1f1f3; font-size:9px;
      letter-spacing:2px; font-weight:600; text-shadow:0 0 8px #000, 0 0 3px #000; white-space:nowrap; }
  .home-core { width:13px; height:13px; border-radius:50%; background:#ffd58a;
      box-shadow:0 0 0 4px rgba(255,213,138,0.16), 0 0 24px rgba(255,213,138,0.55);
      border:1px solid #fff4dc; }
  .home-ring { position:absolute; left:-12px; top:-12px; width:36px; height:36px;
      border:1px solid rgba(255,213,138,0.45); border-radius:50%;
      animation: pulse 2.8s ease-out infinite; }
  .home-label { position:absolute; left:24px; top:-5px; color:#ffe9bd; font-size:9px;
      letter-spacing:2px; font-weight:700; text-shadow:0 0 8px #000, 0 0 3px #000; white-space:nowrap; }
  .stop-pin { width:18px; height:18px; border-radius:5px; background:#c9a468; color:#1a0d12;
      font-size:9px; font-weight:700; text-align:center; line-height:18px;
      border:1px solid #ffe9bd; box-shadow:0 0 10px rgba(201,164,104,0.55); }
  .hud-corner { position:fixed; width:26px; height:26px; border-color:rgba(255,77,100,0.42);
      border-style:solid; border-width:0; pointer-events:none; z-index:500; }
  .tl { top:12px; left:12px; border-top-width:1px; border-left-width:1px; }
  .tr { top:12px; right:12px; border-top-width:1px; border-right-width:1px; }
  .bl { bottom:12px; left:12px; border-bottom-width:1px; border-left-width:1px; }
  .br { bottom:12px; right:12px; border-bottom-width:1px; border-right-width:1px; }
  .readout { position:fixed; left:48px; bottom:18px; z-index:550; pointer-events:none;
      color:#b3283c; font-size:9px; letter-spacing:1.8px; text-shadow:0 0 8px #000; }
  .live { position:fixed; right:46px; top:18px; z-index:550; pointer-events:none;
      color:#c9c9ce; font-size:9px; letter-spacing:2px; }
  .live i { display:inline-block; width:6px; height:6px; border-radius:50%; background:#e8394f;
      margin-right:6px; box-shadow:0 0 10px #ff4d64; animation: blink 1.6s infinite; }
  @keyframes blink { 50% { opacity:0.25; } }
  #summary { position:fixed; left:48px; top:52px; z-index:560; display:none; max-width:470px;
      background:rgba(9,7,9,0.9); border:1px solid #3a1a21; border-left:2px solid #e8394f;
      border-radius:6px; padding:10px 14px; color:#e6e6e9; font-size:10px; letter-spacing:0.8px;
      line-height:1.6; box-shadow:0 8px 30px rgba(0,0,0,0.35); pointer-events:none; }
  #summary b { color:#ff6b7e; font-weight:600; }
  #summary .sub { color:#8a8a92; font-size:9px; letter-spacing:1.2px; }
  #offline { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
      z-index:600; display:none; color:#8a8a92; font-size:11px; letter-spacing:1.2px;
      border:1px solid #342029; background:rgba(7,7,9,0.9); padding:12px 18px; }
  .mall-logo-wrap { position:relative; width:46px; height:46px; }
  .mall-logo { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
      border-radius:15px; background:linear-gradient(150deg,#ff6679 0%,#d21f35 48%,#7c0f1d 100%);
      border:1px solid rgba(255,231,235,0.72); color:#fff; cursor:pointer;
      box-shadow:0 0 0 6px rgba(232,57,79,0.14), 0 10px 26px rgba(0,0,0,0.55), 0 0 28px rgba(232,57,79,0.42);
      transition:transform .18s ease, box-shadow .18s ease; }
  .mall-logo:hover { transform:scale(1.07);
      box-shadow:0 0 0 7px rgba(232,57,79,0.22), 0 12px 30px rgba(0,0,0,0.6), 0 0 34px rgba(232,57,79,0.55); }
  .mall-logo svg { width:25px; height:25px; }
  .mall-logo-ring { position:absolute; left:-10px; top:-10px; width:66px; height:66px; border-radius:50%;
      border:1px solid rgba(255,77,100,0.5); animation:pulse 3s ease-out infinite; pointer-events:none; }
  .mall-logo-label { position:absolute; left:50%; top:52px; transform:translateX(-50%);
      max-width:230px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
      background:rgba(8,6,8,0.9); border:1px solid rgba(232,57,79,0.32); border-radius:9px;
      padding:4px 9px; color:#ffe7eb; font-size:9px; font-weight:700; letter-spacing:1.3px;
      text-shadow:0 0 8px #000; pointer-events:none; }
  .mall-panel { position:fixed; right:18px; top:62px; bottom:56px; width:336px; z-index:640;
      display:flex; flex-direction:column; overflow:hidden;
      background:linear-gradient(180deg, rgba(13,9,11,0.985), rgba(7,6,8,0.995));
      border:1px solid #4a1b26; border-radius:16px;
      box-shadow:0 24px 70px rgba(0,0,0,0.6), 0 0 34px rgba(232,57,79,0.12),
                 inset 0 1px 0 rgba(255,255,255,0.04);
      opacity:0; transform:translateX(18px); pointer-events:none;
      transition:opacity .2s ease, transform .2s ease; }
  .mall-panel.open { opacity:1; transform:none; pointer-events:auto; }
  .mall-head { display:flex; align-items:flex-start; gap:10px; padding:16px 16px 12px;
      border-bottom:1px solid rgba(232,57,79,0.14); }
  .mall-head-copy { min-width:0; flex:1; }
  .mall-kicker { color:#e8394f; font-size:8px; font-weight:700; letter-spacing:2.2px; }
  .mall-title { margin-top:5px; color:#f3eef0; font-size:15px; font-weight:600;
      letter-spacing:0.3px; line-height:1.3; overflow-wrap:anywhere; }
  .mall-meta { margin-top:5px; color:#8f8a90; font-size:9px; letter-spacing:0.7px; line-height:1.55; }
  .mall-close { flex:0 0 auto; width:28px; height:28px; border-radius:9px; border:1px solid #3a2028;
      background:#150d10; color:#b9b3b8; font-size:17px; line-height:1; cursor:pointer; }
  .mall-close:hover { color:#ff8a99; border-color:#6c2331; background:#1d1014; }
  .mall-view { display:flex; flex-direction:column; min-height:0; flex:1; padding:12px 14px 14px; }
  .mall-search { width:100%; box-sizing:border-box; border-radius:10px; border:1px solid #352029;
      background:#110b0e; color:#ece7ea; padding:9px 11px; font-size:11px; letter-spacing:0.4px;
      outline:none; }
  .mall-search:focus { border-color:#7a2537; box-shadow:0 0 0 3px rgba(232,57,79,0.09); }
  .mall-search::placeholder { color:#655f66; }
  .mall-list { margin-top:10px; overflow-y:auto; min-height:0; flex:1; padding-right:3px;
      display:flex; flex-direction:column; gap:6px; }
  .mall-list::-webkit-scrollbar { width:5px; }
  .mall-list::-webkit-scrollbar-thumb { background:#3b2029; border-radius:10px; }
  .mall-store { display:flex; align-items:center; gap:10px; width:100%; text-align:left;
      padding:9px 10px; border-radius:11px; border:1px solid transparent;
      background:rgba(255,255,255,0.025); color:#e9e4e7; cursor:pointer; font-family:inherit;
      transition:background .15s ease, border-color .15s ease; }
  .mall-store:hover { background:rgba(232,57,79,0.08); border-color:rgba(232,57,79,0.22); }
  .mall-avatar { flex:0 0 auto; width:30px; height:30px; border-radius:10px;
      display:flex; align-items:center; justify-content:center;
      background:linear-gradient(145deg, rgba(255,77,100,0.2), rgba(255,77,100,0.06));
      border:1px solid rgba(255,77,100,0.24); color:#ff9dab; font-size:12px; font-weight:700; }
  .mall-store-copy { min-width:0; flex:1; }
  .mall-store-name { display:block; color:#f0ebee; font-size:11px; font-weight:600;
      letter-spacing:0.25px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .mall-store-sub { display:block; margin-top:2px; color:#817b82; font-size:9px;
      letter-spacing:0.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .mall-chevron { flex:0 0 auto; color:#5f5960; font-size:13px; }
  .mall-empty { padding:18px 10px; text-align:center; color:#6f6970; font-size:10px; line-height:1.6; }
  .mall-note { margin-top:10px; color:#5f5960; font-size:8px; letter-spacing:0.5px; line-height:1.5; }
  .mall-back { align-self:flex-start; border:0; background:none; color:#e8394f; font-family:inherit;
      font-size:9px; font-weight:700; letter-spacing:1.4px; padding:2px 0 12px; cursor:pointer; }
  .mall-back:hover { color:#ff8a99; }
  #mallDetailBody { overflow-y:auto; min-height:0; flex:1; padding-right:3px; }
  #mallDetailBody::-webkit-scrollbar { width:5px; }
  #mallDetailBody::-webkit-scrollbar-thumb { background:#3b2029; border-radius:10px; }
  #infoDetailBody { overflow-y:auto; min-height:0; flex:1; padding-right:3px; }
  #infoDetailBody::-webkit-scrollbar { width:5px; }
  #infoDetailBody::-webkit-scrollbar-thumb { background:#3b2029; border-radius:10px; }
  .mall-detail-card { display:flex; gap:12px; align-items:center; padding:14px;
      border-radius:14px; background:linear-gradient(150deg, rgba(232,57,79,0.1), rgba(232,57,79,0.02));
      border:1px solid rgba(232,57,79,0.18); }
  .mall-detail-avatar { flex:0 0 auto; width:46px; height:46px; border-radius:14px;
      display:flex; align-items:center; justify-content:center; color:#fff; font-size:18px;
      font-weight:700; background:linear-gradient(150deg,#ff6679,#b3172b);
      border:1px solid rgba(255,231,235,0.45); box-shadow:0 0 20px rgba(232,57,79,0.3); }
  .mall-photo { flex:0 0 auto; width:64px; height:64px; border-radius:14px; object-fit:cover;
      background:#150d10; border:1px solid rgba(255,77,100,0.35);
      box-shadow:0 0 18px rgba(232,57,79,0.22); }
  .mall-hero { width:100%; max-height:120px; object-fit:cover; border-radius:12px;
      margin-bottom:10px; background:#150d10; border:1px solid rgba(255,77,100,0.3);
      box-shadow:0 0 20px rgba(232,57,79,0.18); }
  .mall-detail-name { color:#f5eff1; font-size:14px; font-weight:600; line-height:1.3; }
  .mall-detail-cat { margin-top:3px; color:#ff9dab; font-size:9px; letter-spacing:0.8px; }
  .mall-detail-mall { margin-top:3px; color:#7d767d; font-size:8px; letter-spacing:0.9px; }
  .mall-rows { margin-top:12px; display:flex; flex-direction:column; gap:7px; }
  .mall-row { display:grid; grid-template-columns:84px 1fr; gap:8px; padding:8px 10px;
      border-radius:9px; background:rgba(255,255,255,0.024);
      border:1px solid rgba(255,255,255,0.035); }
  .mall-row-key { color:#777178; font-size:8px; letter-spacing:1px; line-height:1.5; }
  .mall-row-val { color:#ded8dc; font-size:10px; line-height:1.5; overflow-wrap:anywhere; }
  .mall-row-val a { color:#ff8a99; text-decoration:none; }
  .mall-row-val a:hover { text-decoration:underline; }
  .mall-detail-note { margin-top:12px; color:#656066; font-size:8px; line-height:1.55; }
  .leaflet-tooltip { background:#14161b !important; color:#d9dce3 !important;
      border:1px solid rgba(255,255,255,0.12) !important; border-radius:7px !important; font-size:9px !important;
      letter-spacing:0.6px; box-shadow:0 4px 18px rgba(0,0,0,0.45) !important; }
  .leaflet-tooltip-top:before { border-top-color:rgba(255,255,255,0.12) !important; }
  .poi-filters { position:fixed; left:48px; bottom:42px; z-index:565; display:none;
      gap:6px; flex-wrap:wrap; max-width:72%; }
  .poi-chip { border:1px solid rgba(255,255,255,0.13); background:rgba(15,17,22,0.96);
      color:#a4a9b4; border-radius:999px; padding:6px 12px; font-family:inherit;
      font-size:9px; letter-spacing:1.1px; font-weight:600; cursor:pointer;
      transition:color .15s ease, border-color .15s ease,
      background .15s ease; }
  .poi-chip:hover { border-color:rgba(255,255,255,0.28); color:#f1f2f5; }
  .poi-chip.active { background:#cf3348; border-color:#ff8a99; color:#fff;
      box-shadow:0 4px 16px rgba(207,51,72,0.28); }
  .poi-icon-wrap { background:none; border:none; }
  .poi-icon { position:relative; width:30px; height:30px; border-radius:9px;
      display:flex; align-items:center; justify-content:center; cursor:pointer;
      background:rgba(15,17,22,0.95); border:1px solid rgba(255,255,255,0.18);
      box-shadow:0 4px 14px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.06);
      color:#c4c9d3; transition:transform .15s ease, border-color .15s ease,
      box-shadow .15s ease; }
  .poi-icon:hover { transform:translateY(-2px) scale(1.06);
      border-color:rgba(255,255,255,0.4); box-shadow:0 7px 20px rgba(0,0,0,0.55); }
  .poi-icon svg { width:17px; height:17px; display:block; }
  .poi-icon.mall { width:34px; height:34px; border-radius:11px; color:#ffc06b;
      border-color:rgba(255,192,107,0.5); box-shadow:0 0 0 4px rgba(255,192,107,0.08),
      0 6px 18px rgba(0,0,0,0.5); }
  .poi-icon.market { color:#8ce3ab; }
  .poi-icon.shop { color:#8dbdff; }
  .poi-icon.food { color:#ffab8c; }
  .poi-icon.health { color:#ff9aa6; }
  .poi-icon.place { color:#b9c0cc; }
  .poi-icon.stop { color:#d8bb87; }
  .poi-tag { position:absolute; left:50%; top:calc(100% + 5px); transform:translateX(-50%);
      background:rgba(12,14,18,0.93); border:1px solid rgba(255,255,255,0.1);
      border-radius:6px; padding:2px 7px; color:#dfe2e8; font-size:8.5px;
      font-weight:600; letter-spacing:0.4px; white-space:nowrap; max-width:170px;
      overflow:hidden; text-overflow:ellipsis; pointer-events:none; }
  .poi-icon-wrap.mall .poi-tag, .poi-icon.mall .poi-tag { color:#ffdca8; }
  .poi-hero { position:relative; width:100%; height:132px; border-radius:12px;
      overflow:hidden; margin-bottom:12px; border:1px solid rgba(255,255,255,0.08);
      background:linear-gradient(160deg,#20242c 0%,#111318 70%); }
  .poi-hero-photo { position:absolute; inset:0; width:100%; height:100%;
      object-fit:cover; display:block; }
  .poi-hero-photo.logo { object-fit:contain; padding:20px; background:#12151a; }
  .poi-hero-fallback { position:absolute; inset:0; display:flex; flex-direction:column;
      align-items:center; justify-content:center; gap:6px; color:#9aa1ad; }
  .poi-hero-fallback svg { width:34px; height:34px; color:#aeb5c1; }
  .poi-hero-fallback .hint { font-size:7.5px; letter-spacing:1.1px; color:#6d7480; }
  .poi-hero-badge { position:absolute; left:10px; bottom:9px; z-index:2;
      background:rgba(8,10,13,0.82); border:1px solid rgba(255,255,255,0.1);
      border-radius:6px; padding:3px 8px; font-size:8px; letter-spacing:0.8px;
      color:#c7ccd5; }
  .poi-hero-credit { position:absolute; right:10px; bottom:10px; z-index:2;
      max-width:56%; padding:2px 6px; border-radius:5px; background:rgba(8,10,13,0.72);
      font-size:7.5px; letter-spacing:0.3px; color:#9aa1ad; text-align:right;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .poi-hours { margin-bottom:12px; padding:11px 12px; border-radius:11px;
      background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.07); }
  .poi-hours-head { display:flex; align-items:center; gap:8px; color:#8b919c;
      font-size:8px; letter-spacing:1.3px; }
  .poi-hours-value { margin-top:6px; color:#e3e6eb; font-size:11px;
      line-height:1.45; overflow-wrap:anywhere; }
  .poi-hours-today { margin-top:4px; color:#9aa0ab; font-size:9px; }
  .poi-hours-missing { margin-top:6px; color:#7e8590; font-size:9px; line-height:1.5; }
  .poi-status { border-radius:999px; padding:2px 8px; font-size:8px;
      font-weight:700; letter-spacing:0.8px; }
  .poi-status.open { background:rgba(92,200,140,0.14); color:#8ce3ab;
      border:1px solid rgba(92,200,140,0.35); }
  .poi-status.closed { background:rgba(255,120,120,0.12); color:#ff9aa6;
      border:1px solid rgba(255,120,120,0.3); }
  .poi-status.unknown { background:rgba(255,255,255,0.06); color:#9aa0ab;
      border:1px solid rgba(255,255,255,0.12); }
  .poi-search-mall { margin:14px auto 4px; display:block; border:1px solid #8a2537;
      background:#1c1014; color:#ffa3b1; border-radius:9px; padding:9px 14px;
      font-family:inherit; font-size:9px; font-weight:700; letter-spacing:1.2px;
      cursor:pointer; }
  .poi-search-mall:hover { background:#2a141b; color:#ffd0d8; }
  .mall-avatar.poi-list-icon { background:rgba(255,255,255,0.05);
      border:1px solid rgba(255,255,255,0.1); color:#c4c9d3; }
  .mall-avatar.poi-list-icon svg { width:16px; height:16px; }
  .mall-avatar.poi-list-icon.mall { color:#ffc06b; border-color:rgba(255,192,107,0.4); }
  .mall-avatar.poi-list-icon.market { color:#8ce3ab; }
  .mall-avatar.poi-list-icon.shop { color:#8dbdff; }
  .mall-avatar.poi-list-icon.food { color:#ffab8c; }
  .mall-avatar.poi-list-icon.health { color:#ff9aa6; }
  .mall-detail-avatar.poi-detail-icon { background:rgba(255,255,255,0.05);
      border:1px solid rgba(255,255,255,0.1); color:#c4c9d3;
      box-shadow:none; }
  .poi-detail-icon svg { width:20px; height:20px; }
</style>
</head>
<body>
<div id="map"></div>
<div class="hud-corner tl"></div><div class="hud-corner tr"></div>
<div class="hud-corner bl"></div><div class="hud-corner br"></div>
<div class="live"><i></i>CANLI HARİTA</div>
<div class="readout" id="readout"></div>
<div id="summary"></div>
<div id="poiFilters" class="poi-filters" role="toolbar" aria-label="Mekân filtresi">
  <button class="poi-chip active" data-filter="all" type="button">TÜMÜ</button>
  <button class="poi-chip" data-filter="mall" type="button">AVM</button>
  <button class="poi-chip" data-filter="market" type="button">MARKET</button>
  <button class="poi-chip" data-filter="shop" type="button">DÜKKAN</button>
  <button class="poi-chip" data-filter="food" type="button">YEMEK</button>
  <button class="poi-chip" data-filter="health" type="button">ECZANE</button>
</div>
<div id="offline">HARİTA KAROLARINA ULAŞILAMIYOR · İNTERNET BAĞLANTISINI KONTROL ET</div>
<div id="mallPanel" class="mall-panel" aria-hidden="true">
  <div class="mall-head">
    <div class="mall-head-copy">
      <div class="mall-kicker">AVM · MAĞAZALAR</div>
      <div id="mallTitle" class="mall-title"></div>
      <div id="mallMeta" class="mall-meta"></div>
    </div>
    <button id="mallClose" class="mall-close" type="button" aria-label="Mağaza panelini kapat">×</button>
  </div>
  <div id="mallListView" class="mall-view">
    <img id="mallPhoto" class="mall-photo mall-hero" alt="" style="display:none" loading="lazy" referrerpolicy="no-referrer">
    <input id="mallSearch" class="mall-search" type="search" autocomplete="off"
           placeholder="Mağaza veya kategori ara…">
    <div id="mallList" class="mall-list"></div>
    <div id="mallNote" class="mall-note"></div>
  </div>
  <div id="mallDetailView" class="mall-view" style="display:none">
    <button id="mallBack" class="mall-back" type="button">← MAĞAZA LİSTESİ</button>
    <div id="mallDetailBody"></div>
  </div>
</div>
<div id="infoPanel" class="mall-panel" aria-hidden="true">
  <div class="mall-head">
    <div class="mall-head-copy">
      <div class="mall-kicker">HARİTA · MEKÂN BİLGİSİ</div>
      <div id="infoTitle" class="mall-title"></div>
      <div id="infoMeta" class="mall-meta"></div>
    </div>
    <button id="infoClose" class="mall-close" type="button" aria-label="Mekân panelini kapat">×</button>
  </div>
  <div id="infoListView" class="mall-view">
    <div id="infoList" class="mall-list"></div>
    <div id="infoNote" class="mall-note"></div>
  </div>
  <div id="infoDetailView" class="mall-view" style="display:none">
    <button id="infoBack" class="mall-back" type="button">← MEKÂN LİSTESİ</button>
    <div id="infoDetailBody"></div>
  </div>
</div>
<script>__QTWEBCHANNEL_JS__</script>
<script>__LEAFLET_JS__</script>
<script>
(function () {
    const HOME = __HOME_JSON__;
    window.jarvisMap = L.map('map', {
        center: [HOME.lat, HOME.lon], zoom: 12, zoomControl: false, attributionControl: true,
        preferCanvas: true, worldCopyJump: true, maxZoom: 22, minZoom: 3,
        zoomSnap: 0.5, zoomDelta: 0.5, wheelPxPerZoomLevel: 70, wheelDebounceTime: 30,
        inertia: true, inertiaDeceleration: 3600, easeLinearity: 0.25,
        scrollWheelZoom: true, doubleClickZoom: true, boxZoom: true, keyboard: true,
        touchZoom: true, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false
    });
    L.control.zoom({ position: 'topleft' }).addTo(window.jarvisMap);
    const mapElement = document.getElementById('map');
    mapElement.classList.add('dark-tiles');
    let tileErrors = 0;
    let usingFallbackTiles = false;
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 22, maxNativeZoom: 19, minZoom: 3,
        updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 2, fadeAnimation: false,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> katkıları'
    });
    function enableFallbackTiles() {
        if (usingFallbackTiles) return;
        usingFallbackTiles = true;
        mapElement.classList.remove('dark-tiles');
        window.jarvisMap.removeLayer(tiles);
        const fallback = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
            {
                maxZoom: 22, maxNativeZoom: 16, minZoom: 3,
                updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 2,
                attribution: '&copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a>'
            }
        );
        fallback.on('tileerror', function () {
            tileErrors += 1;
            if (tileErrors >= 10) {
                document.getElementById('offline').style.display = 'block';
            }
        });
        fallback.addTo(window.jarvisMap).bringToBack();
        tileErrors = 0;
    }
    tiles.on('tileerror', function () {
        tileErrors += 1;
        if (tileErrors >= 6) enableFallbackTiles();
    });
    tiles.addTo(window.jarvisMap);

    const placeLayer = L.layerGroup().addTo(window.jarvisMap);
    const routeLayer = L.layerGroup().addTo(window.jarvisMap);
    const lineLayer = L.layerGroup().addTo(window.jarvisMap);
    let homeMarker = null, homeCircle = null, focusMarker = null;
    let mallMarker = null, mallStores = [], mallInfo = null, mallStoreError = '';
    const mallPanel = document.getElementById('mallPanel');
    const mallList = document.getElementById('mallList');
    const mallSearch = document.getElementById('mallSearch');
    let infoPayload = null, infoItems = [], infoFocus = null;
    const infoPanel = document.getElementById('infoPanel');
    const infoList = document.getElementById('infoList');

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function homeLabel() {
        const raw = String(HOME.name || '').trim();
        const name = raw.toLocaleUpperCase('tr-TR');
        if (!HOME.is_set) return name || 'MERKEZ';
        if (!raw || raw.toLocaleLowerCase('tr-TR') === 'evim') return 'EVİM';
        return 'EVİM · ' + name;
    }
    function updateReadout(lat, lng) {
        document.getElementById('readout').textContent =
            homeLabel() + ' · ' + Number(lat).toFixed(3) + '°N ' + Number(lng).toFixed(3) + '°E';
    }
    function setSummary(html) {
        const box = document.getElementById('summary');
        if (!html) { box.style.display = 'none'; box.innerHTML = ''; return; }
        box.innerHTML = html;
        box.style.display = 'block';
    }
    function homeIcon() {
        return L.divIcon({
            className: '',
            html: '<div class="home-ring"></div><div class="home-core"></div>' +
                  '<div class="home-label">' + esc(homeLabel()) + '</div>',
            iconSize: [14, 14], iconAnchor: [7, 7]
        });
    }
    function labelledIcon(label, tone) {
        const core = tone === 'dest' ? '#ff4d64' : '#ffd58a';
        return L.divIcon({
            className: '',
            html: '<div class="marker-core" style="background:' + core + ';border-color:#fff"></div>' +
                  '<div class="marker-label" style="color:' + core + '">' + esc(label) + '</div>',
            iconSize: [12, 12], iconAnchor: [6, 6]
        });
    }

    const POI_ICONS = {
        mall: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M5.8 8.4h12.4l-1.1 10.4a1.7 1.7 0 0 1-1.7 1.5H8.6a1.7 1.7 0 0 1-1.7-1.5L5.8 8.4z" ' +
            'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
            '<path d="M9 8.4V7a3 3 0 0 1 6 0v1.4" stroke="currentColor" stroke-width="1.6" ' +
            'stroke-linecap="round"/><path d="M9.2 12.2h5.6" stroke="currentColor" ' +
            'stroke-width="1.6" stroke-linecap="round"/></svg>',
        market: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M3.2 4.6h1.9l1.6 8.9h9.8l1.6-6.3H6.1" stroke="currentColor" ' +
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>' +
            '<circle cx="9.2" cy="18.2" r="1.3" stroke="currentColor" stroke-width="1.5"/>' +
            '<circle cx="16.4" cy="18.2" r="1.3" stroke="currentColor" stroke-width="1.5"/></svg>',
        shop: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M4.4 9.3h15.2v9.1a1.6 1.6 0 0 1-1.6 1.6H6a1.6 1.6 0 0 1-1.6-1.6V9.3z" ' +
            'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
            '<path d="M3.4 9.3 4.8 5h14.4l1.4 4.3" stroke="currentColor" stroke-width="1.6" ' +
            'stroke-linejoin="round"/><path d="M9.4 20v-5h5.2v5" stroke="currentColor" ' +
            'stroke-width="1.6" stroke-linejoin="round"/></svg>',
        food: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M7.2 3.6v6.1M9.7 3.6v6.1M7.2 6.4h2.5M8.45 9.7v10.7" stroke="currentColor" ' +
            'stroke-width="1.6" stroke-linecap="round"/>' +
            '<path d="M16.4 3.6c-1.6.8-2.4 2.3-2.4 4.2 0 1.6.8 2.8 2.2 3.3v9.3" ' +
            'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        health: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M9.4 4h5.2v5.4H20v5.2h-5.4V20H9.4v-5.4H4V9.4h5.4V4z" stroke="currentColor" ' +
            'stroke-width="1.6" stroke-linejoin="round"/></svg>',
        stop: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<rect x="5" y="4" width="14" height="13" rx="2.4" stroke="currentColor" stroke-width="1.6"/>' +
            '<path d="M5 9.5h14M8 17v2M16 17v2" stroke="currentColor" stroke-width="1.6" ' +
            'stroke-linecap="round"/><circle cx="8.5" cy="13.6" r=".95" fill="currentColor"/>' +
            '<circle cx="15.5" cy="13.6" r=".95" fill="currentColor"/></svg>',
        place: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M12 21s6.4-5.6 6.4-10.4A6.4 6.4 0 1 0 5.6 10.6C5.6 15.4 12 21 12 21z" ' +
            'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
            '<circle cx="12" cy="10.4" r="2.2" stroke="currentColor" stroke-width="1.6"/></svg>'
    };
    const SHOP_KINDS = [
        'clothes', 'fashion', 'shoes', 'jewelry', 'watches', 'bag', 'accessories',
        'cosmetics', 'beauty', 'hairdresser', 'barber', 'electronics', 'computer',
        'mobile_phone', 'books', 'stationery', 'toys', 'sports', 'outdoor', 'furniture',
        'houseware', 'kitchen', 'hardware', 'doityourself', 'garden_centre', 'florist',
        'optician', 'pet', 'pet_grooming', 'music', 'musical_instrument', 'gift',
        'variety_store', 'general', 'car', 'car_repair', 'bicycle', 'laundry',
        'dry_cleaning', 'tailor', 'fabric', 'interior_decoration', 'travel_agency',
        'estate_agent', 'insurance', 'tattoo', 'games', 'video_games', 'photo',
        'frame', 'craft', 'second_hand'
    ];
    function foldText(value) {
        return String(value == null ? '' : value).toLocaleLowerCase('tr-TR');
    }
    function poiKey(item) {
        if (!item) return 'place';
        if (item.kind === 'stop') return 'stop';
        const kind = foldText(item.kind);
        const category = foldText(item.category);
        if (kind === 'mall' || kind === 'department_store' ||
            /avm|alışveriş merkezi|alisveris merkezi|büyük mağaza/.test(category)) {
            return 'mall';
        }
        if (kind === 'marketplace' ||
            /^(supermarket|convenience|grocery|bakery|butcher|greengrocer|deli|confectionery|coffee|tea|beverages|alcohol)$/.test(kind) ||
            /market|bakkal|fırın|kasap|manav|şarküteri|tatlı/.test(category)) {
            return 'market';
        }
        if (/^(pharmacy|chemist|medical_supply|hospital|clinic|doctors|dentist|veterinary)$/.test(kind) ||
            /eczane|hastane|klinik|diş|veteriner/.test(category)) {
            return 'health';
        }
        if (/^(cafe|restaurant|fast_food|food_court|ice_cream|bar|pub)$/.test(kind) ||
            /kafe|restoran|fast food|yeme|dondurma|bar/.test(category)) {
            return 'food';
        }
        if (SHOP_KINDS.indexOf(kind) !== -1) return 'shop';
        return 'place';
    }
    function poiIconHtml(item) {
        return POI_ICONS[poiKey(item)] || POI_ICONS.place;
    }
    function poiIcon(item, label) {
        const key = poiKey(item);
        const wide = key === 'mall';
        return L.divIcon({
            className: 'poi-icon-wrap' + (wide ? ' mall' : ''),
            html: '<div class="poi-icon ' + key + '">' + poiIconHtml(item) +
                  (label ? '<div class="poi-tag">' + esc(label) + '</div>' : '') + '</div>',
            iconSize: [wide ? 34 : 30, wide ? 34 : 30],
            iconAnchor: [wide ? 17 : 15, wide ? 17 : 15]
        });
    }
    const FILTER_LABELS = {
        all: 'TÜMÜ', mall: 'AVM', market: 'MARKET', shop: 'DÜKKAN',
        food: 'YEMEK', health: 'ECZANE'
    };
    let poiFilter = 'all';
    let infoSilent = false;
    const poiFilterBar = document.getElementById('poiFilters');
    function matchesPoiFilter(item) {
        return poiFilter === 'all' || poiKey(item) === poiFilter;
    }
    function filterChip(filter) {
        poiFilter = FILTER_LABELS[filter] ? filter : 'all';
        Array.prototype.forEach.call(poiFilterBar.querySelectorAll('.poi-chip'), function (button) {
            button.classList.toggle('active',
                button.getAttribute('data-filter') === poiFilter);
        });
    }
    function jarvisSetHome(lat, lng, name, isSet) {
        HOME.lat = Number(lat); HOME.lon = Number(lng);
        if (name) HOME.name = String(name);
        if (isSet !== undefined) HOME.is_set = !!isSet;
        if (homeMarker && homeCircle) {
            homeMarker.setLatLng([HOME.lat, HOME.lon]);
            homeMarker.setIcon(homeIcon());
            homeMarker.setPopupContent('<strong style="letter-spacing:2px;color:#ffd58a">' +
                esc(homeLabel()) + '</strong><br><span style="color:#8a8a92">' +
                esc(HOME.name) + '</span><br><span style="color:#57575f;font-size:9px">' +
                Number(HOME.lat).toFixed(5) + '°N · ' + Number(HOME.lon).toFixed(5) + '°E</span>');
            homeCircle.setLatLng([HOME.lat, HOME.lon]);
        }
        updateReadout(HOME.lat, HOME.lon);
    }
    window.jarvisSetHome = jarvisSetHome;
    window.jarvisRecenterHome = function (zoom) {
        window.jarvisMap.setView([HOME.lat, HOME.lon], zoom || 13);
        updateReadout(HOME.lat, HOME.lon);
    };

    if (window.qt && qt.webChannelTransport) {
        try {
            new QWebChannel(qt.webChannelTransport, function (channel) {
                window.mapBridge = channel.objects.mapBridge;
            });
        } catch (bridgeError) {
            window.mapBridge = null;
        }
    }

    let poiBusy = false;
    let poiPendingMarker = null;
    window.jarvisPoiPending = function (lat, lng) {
        poiBusy = true;
        if (poiPendingMarker) {
            window.jarvisMap.removeLayer(poiPendingMarker);
            poiPendingMarker = null;
        }
        poiPendingMarker = L.circleMarker([lat, lng], {
            radius: 8, color: '#c9a468', weight: 1.5, fillColor: '#c9a468',
            fillOpacity: 0.18, dashArray: '3 5'
        }).addTo(window.jarvisMap);
        setSummary('<b>TARANIYOR…</b><div class="sub">Bu noktadaki kayıtlı mekânlar aranıyor</div>');
    };
    window.jarvisPoiDone = function (payload, silent) {
        poiBusy = false;
        if (poiPendingMarker) {
            window.jarvisMap.removeLayer(poiPendingMarker);
            poiPendingMarker = null;
        }
        if (payload && payload.error) {
            if (!silent) {
                setSummary('<b>MEKÂN TARANAMADI</b><div class="sub">' + esc(payload.error) + '</div>');
            }
            return;
        }
        window.jarvisShowPlaces(payload || {
            title: 'Mekân bulunamadı', center: {}, items: []
        }, !!silent);
    };
    window.jarvisMap.on('click', function (event) {
        if (poiBusy) return;
        const original = event.originalEvent;
        if (original && original.target && original.target.closest) {
            if (original.target.closest(
                '.leaflet-marker-icon, .leaflet-tooltip, .leaflet-popup')) {
                return;
            }
        }
        if (!window.mapBridge) {
            setSummary('<b>BİLGİ KÖPRÜSÜ HAZIR DEĞİL</b><div class="sub">Haritayı kapatıp yeniden aç</div>');
            return;
        }
        const point = event.latlng;
        window.jarvisPoiPending(point.lat, point.lng);
        window.mapBridge.poiClick(Number(point.lat), Number(point.lng), window.jarvisMap.getZoom());
    });

    homeCircle = L.circle([HOME.lat, HOME.lon], {
        radius: 1200, color: '#c9a468', weight: 1, opacity: 0.45,
        fillColor: '#c9a468', fillOpacity: 0.04, dashArray: '4 7', interactive: false
    }).addTo(window.jarvisMap);
    homeMarker = L.marker([HOME.lat, HOME.lon], { icon: homeIcon(), zIndexOffset: 500 })
        .addTo(window.jarvisMap)
        .bindPopup('<strong style="letter-spacing:2px;color:#ffd58a">' + esc(homeLabel()) +
                   '</strong><br><span style="color:#8a8a92">' + esc(HOME.name || '') +
                   '</span><br><span style="color:#57575f;font-size:9px">' +
                   Number(HOME.lat).toFixed(5) + '°N · ' + Number(HOME.lon).toFixed(5) + '°E</span>');
    updateReadout(HOME.lat, HOME.lon);

    window.jarvisFocus = function (payload) {
        const center = payload.center || {};
        if (focusMarker) { window.jarvisMap.removeLayer(focusMarker); focusMarker = null; }
        if (center.lat != null && center.lon != null) {
            focusMarker = L.marker([center.lat, center.lon], {
                icon: labelledIcon(center.name || 'KONUM', 'dest')
            }).addTo(window.jarvisMap).bindPopup('<strong style="color:#ff4d64">' +
                esc(center.name || 'Konum') + '</strong><br><span style="color:#8a8a92">' +
                esc(payload.label || '') + '</span>');
            window.jarvisMap.setView([center.lat, center.lon], payload.zoom || 14);
            updateReadout(center.lat, center.lon);
        }
        setSummary('<b>' + esc(center.name || 'Konum') + '</b>' +
            (payload.label ? '<div class="sub">' + esc(payload.label) + '</div>' : ''));
    };

    window.jarvisShowRoute = function (payload) {
        routeLayer.clearLayers();
        const points = (payload.points || []).map(function (point) { return [point[0], point[1]]; });
        if (!points.length) { setSummary('<b>Rota bulunamadı</b>'); return; }
        L.polyline(points, { color: '#2a0a10', weight: 9, opacity: 0.9, lineCap: 'round' })
            .addTo(routeLayer);
        const line = L.polyline(points, { color: '#ff4d64', weight: 4, opacity: 0.95, lineCap: 'round' })
            .addTo(routeLayer);
        const origin = payload.origin || {}, destination = payload.destination || {};
        if (points.length) {
            L.marker(points[0], { icon: labelledIcon(origin.name || 'BAŞLANGIÇ', 'home') })
                .addTo(routeLayer);
            L.marker(points[points.length - 1], { icon: labelledIcon(destination.name || 'HEDEF', 'dest') })
                .addTo(routeLayer);
        }
        window.jarvisMap.fitBounds(line.getBounds(), { padding: [56, 56], maxZoom: 16 });
        const summary = '<b>' + esc(payload.mode_label || 'Rota') + ' · ' +
            esc(payload.distance_km || 0) + ' km · ' + esc(payload.duration_min || 0) + ' dk</b>' +
            '<div class="sub">' + esc(origin.name || '') + ' → ' + esc(destination.name || '') + '</div>';
        setSummary(summary);
    };

    function infoSub(item) {
        const parts = [];
        if (item.distance_m != null) parts.push(Number(item.distance_m) + ' m');
        if (item.category) parts.push(item.category);
        else if (item.kind === 'stop') parts.push('Durak');
        if (item.live && item.live.length) {
            parts.push(item.live.map(function (bus) {
                return bus.code + ' · ' + bus.eta_min + ' dk';
            }).join(', '));
        } else if (item.lines && item.lines.length) {
            parts.push(item.lines.slice(0, 3).join(', '));
        } else if (item.detail && item.kind === 'stop') {
            parts.push(item.detail);
        }
        return parts.join(' · ');
    }
    function infoWebsite(raw) {
        const text = String(raw || '').trim();
        if (!text) return '';
        let href = '';
        try {
            const parsed = new URL(text.indexOf('://') === -1 ? ('https://' + text) : text);
            if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                href = parsed.href;
            }
        } catch (error) {
            href = '';
        }
        return href
            ? ('<a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(text) + '</a>')
            : esc(text);
    }

    const wikiPhotoCache = {};
    function wikiPhotoQuery(item) {
        const brand = String(item.brand || '').trim();
        const name = String(item.name || '').trim();
        let query = brand || name;
        if (!brand && /avm|alışveriş merkezi|alisveris merkezi|büyük mağaza/.test(foldText(item.category))) {
            const cleaned = name
                .replace(/\\b(?:avm|alışveriş merkezi|alisveris merkezi)\\b/gi, ' ')
                .replace(/\\s+/g, ' ').trim();
            if (cleaned) query = cleaned;
        }
        return query.slice(0, 60);
    }
    function fetchWikiThumb(query) {
        const key = query.toLocaleLowerCase('tr-TR');
        if (key in wikiPhotoCache) return Promise.resolve(wikiPhotoCache[key]);
        const attempts = [query];
        const cleaned = query.replace(/\\s+/g, ' ').trim();
        if (cleaned && cleaned !== query) attempts.push(cleaned);
        function attempt(index) {
            if (index >= attempts.length) return Promise.resolve('');
            const target = attempts[index];
            const url = 'https://tr.wikipedia.org/w/api.php?action=query&format=json&origin=*' +
                '&redirects=1&prop=pageimages&piprop=thumbnail&pithumbsize=480&titles=' +
                encodeURIComponent(target);
            return fetch(url, { credentials: 'omit' })
                .then(function (response) { return response.ok ? response.json() : null; })
                .then(function (data) {
                    const pages = data && data.query && data.query.pages ? data.query.pages : {};
                    let photo = '';
                    Object.keys(pages).forEach(function (pageId) {
                        const page = pages[pageId] || {};
                        if (!photo && page.thumbnail && page.thumbnail.source) {
                            photo = page.thumbnail.source;
                        }
                    });
                    return photo || attempt(index + 1);
                })
                .catch(function () { return attempt(index + 1); });
        }
        return attempt(0).then(function (photo) {
            wikiPhotoCache[key] = photo || '';
            return wikiPhotoCache[key];
        });
    }
    function plainText(value) {
        return String(value || '')
            .replace(/<[^>]*>/g, ' ')
            .replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
            .replace(/\\s+/g, ' ').trim();
    }
    const commonsPhotoCache = {};
    const PHOTO_STOP_WORDS = {
        avm: 1, alisveris: 1, merkezi: 1, merkez: 1, shopping: 1, mall: 1,
        center: 1, centre: 1, kocaeli: 1, izmit: 1, magaza: 1, dukkan: 1,
        market: 1, supermarket: 1, store: 1
    };
    function photoFold(value) {
        return foldText(value)
            .replace(/ı/g, 'i').replace(/ç/g, 'c').replace(/ğ/g, 'g')
            .replace(/ö/g, 'o').replace(/ş/g, 's').replace(/ü/g, 'u')
            .replace(/[^0-9a-z]+/g, ' ').trim();
    }
    function photoNameWords(item) {
        return photoFold(item.name || '').split(' ').filter(function (word) {
            return word.length >= 3 && !PHOTO_STOP_WORDS[word];
        });
    }
    function photoTitleScore(title, item) {
        const words = photoNameWords(item);
        if (!words.length) return 0;
        const folded = photoFold(String(title || '').replace(/^File:/, ''));
        const hits = words.filter(function (word) {
            return folded.indexOf(word) !== -1;
        }).length;
        return hits / words.length;
    }
    function commonsApi(params) {
        return 'https://commons.wikimedia.org/w/api.php?format=json&origin=*&' + params;
    }
    function fetchCommonsInfo(titles) {
        if (!titles.length) return Promise.resolve({});
        return fetch(commonsApi(
            'action=query&prop=imageinfo&iiprop=url%7Cmime%7Cextmetadata'
            + '&iiextmetadatafilter=Artist%7CLicenseShortName&iiurlwidth=640&titles='
            + encodeURIComponent(titles.join('|'))
        ), { credentials: 'omit' })
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (data) {
                const pages = data && data.query && data.query.pages ? data.query.pages : {};
                const map = {};
                Object.keys(pages).forEach(function (id) {
                    if (pages[id] && pages[id].title) map[pages[id].title] = pages[id];
                });
                return map;
            })
            .catch(function () { return {}; });
    }
    function commonsCandidate(page, item) {
        const info = ((page || {}).imageinfo || [])[0] || {};
        const url = String(info.thumburl || info.url || '');
        const mime = String(info.mime || '');
        const title = String(page.title || '');
        if (!/^https:\\/\\/(?:upload|thumb)\\.wikimedia\\.org\\//.test(url)) return null;
        if (mime && !/^image\\/(jpeg|png|webp)$/.test(mime)) return null;
        if (/logo|map|harita|diagram|coat of arms|svg|satellite|nasa|\\biss\\b/i.test(title)) return null;
        const meta = info.extmetadata || {};
        const artist = plainText(meta.Artist && meta.Artist.value).slice(0, 60);
        const license = plainText(meta.LicenseShortName && meta.LicenseShortName.value).slice(0, 24);
        return {
            url: url,
            title: title,
            score: photoTitleScore(title, item),
            credit: artist,
            license: license
        };
    }
    function fetchCommonsPhoto(item) {
        const lat = Number(item.lat), lon = Number(item.lon);
        if (!isFinite(lat) || !isFinite(lon)) return Promise.resolve(null);
        const key = [item.name, lat.toFixed(5), lon.toFixed(5)].join('|');
        if (key in commonsPhotoCache) return Promise.resolve(commonsPhotoCache[key]);
        const params = 'action=query&list=geosearch&gscoord=' + lat + '%7C' + lon
            + '&gsradius=500&gslimit=16&gsnamespace=6';
        return fetch(commonsApi(params), { credentials: 'omit' })
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (data) {
                const hits = (data && data.query && data.query.geosearch) || [];
                if (!hits.length) return null;
                const distances = {};
                const titles = [];
                hits.forEach(function (hit) {
                    const title = String(hit.title || '');
                    if (!title) return;
                    distances[title] = Number(hit.dist) || 0;
                    titles.push(title);
                });
                return fetchCommonsInfo(titles).then(function (pages) {
                    let best = null;
                    titles.forEach(function (title) {
                        const candidate = commonsCandidate(pages[title], item);
                        if (!candidate) return;
                        const distance = distances[title] || 0;
                        candidate.distance = distance;
                        if (candidate.score >= 0.5 && distance <= 300) {
                            if (!best || candidate.score > best.score
                                || (candidate.score === best.score && distance < best.distance)) {
                                best = candidate;
                            }
                        }
                    });
                    return best;
                });
            })
            .catch(function () { return null; })
            .then(function (photo) {
                commonsPhotoCache[key] = photo;
                return photo;
            });
    }
    function heroPhotoHtml(item, photo) {
        const badge = ['WIKIMEDIA'];
        if (photo.license) badge.push(photo.license);
        const credit = 'Fotoğraf: ' + (photo.credit || 'Wikimedia Commons')
            + (photo.license ? ' · ' + photo.license : '');
        return heroFallback(item) +
            '<img class="poi-hero-photo" src="' + esc(photo.url) + '" alt="" ' +
            'referrerpolicy="no-referrer">' +
            '<div class="poi-hero-badge">' + esc(badge.join(' · ')) + '</div>' +
            '<div class="poi-hero-credit">' + esc(credit) + '</div>';
    }
    function heroFallback(item) {
        return '<div class="poi-hero-fallback">' + poiIconHtml(item) +
            '<span class="hint">FOTOĞRAF BULUNAMADI · TEMSİLİ GÖRSEL</span></div>';
    }
    function fillWikiHero(item, elementId) {
        if (poiKey(item) === 'stop') return;
        fetchCommonsPhoto(item).then(function (photo) {
            const hero = document.getElementById(elementId);
            if (!hero || !hero.getAttribute('data-photo')) return;
            if (photo) {
                hero.removeAttribute('data-photo');
                hero.innerHTML = heroPhotoHtml(item, photo);
                return;
            }
            const query = wikiPhotoQuery(item);
            if (!query) return;
            fetchWikiThumb(query).then(function (logo) {
                const node = document.getElementById(elementId);
                if (!node || !node.getAttribute('data-photo') || !logo) return;
                node.removeAttribute('data-photo');
                node.innerHTML = heroFallback(item) +
                    '<img class="poi-hero-photo logo" src="' + esc(logo) + '" alt="" ' +
                    'referrerpolicy="no-referrer">' +
                    '<div class="poi-hero-badge">MARKA GÖRSELİ</div>';
            }).catch(function () {});
        }).catch(function () {});
    }
    function heroHtml(item, elementId) {
        const photo = String(item.photo || '').trim();
        const fallback = heroFallback(item);
        if (photo) {
            return '<div class="poi-hero" id="' + elementId + '">' + fallback +
                '<img class="poi-hero-photo" src="' + esc(photo) + '" alt="" ' +
                'referrerpolicy="no-referrer">' +
                '<div class="poi-hero-badge">' + esc(item.category || 'Fotoğraf') + '</div></div>';
        }
        window.setTimeout(function () { fillWikiHero(item, elementId); }, 30);
        return '<div class="poi-hero" id="' + elementId + '" data-photo="1">' + fallback + '</div>';
    }

    const DAY_ALIASES = { mo: 1, tu: 2, we: 3, th: 4, fr: 5, sa: 6, su: 0 };
    function dayList(spec) {
        const days = [];
        String(spec || '').toLowerCase().split(',').forEach(function (part) {
            part = part.trim();
            const tokens = part.split('-');
            if (tokens.length === 2 && DAY_ALIASES[tokens[0]] != null &&
                DAY_ALIASES[tokens[1]] != null) {
                const start = DAY_ALIASES[tokens[0]];
                const end = DAY_ALIASES[tokens[1]];
                let cursor = start, guard = 0;
                while (guard < 8) {
                    days.push(cursor);
                    if (cursor === end) break;
                    cursor = (cursor + 1) % 7;
                    guard += 1;
                }
            } else if (DAY_ALIASES[part] != null) {
                days.push(DAY_ALIASES[part]);
            }
        });
        return days;
    }
    function minuteLabel(value) {
        return String(Math.floor(value / 60)).padStart(2, '0') + ':' +
            String(value % 60).padStart(2, '0');
    }
    function parseTimeRanges(text) {
        const ranges = [];
        const pattern = /(\\d{1,2}):(\\d{2})\\s*-\\s*(\\d{1,2}):(\\d{2})/g;
        let match;
        while ((match = pattern.exec(text)) !== null) {
            const start = Math.min(24 * 60, Number(match[1]) * 60 + Number(match[2]));
            const end = Math.min(24 * 60, Number(match[3]) * 60 + Number(match[4]));
            if (end > start) ranges.push([start, end]);
        }
        return ranges;
    }
    function openingInfo(raw) {
        const text = String(raw || '').trim();
        if (!text) return { state: 'unknown', label: 'BİLİNMİYOR', today: '', raw: '' };
        if (/24\\s*\\/\\s*7/.test(text)) {
            return { state: 'open', label: 'AÇIK', today: '24 saat açık', raw: text };
        }
        const now = new Date();
        const day = now.getDay();
        const minutes = now.getHours() * 60 + now.getMinutes();
        let sawRule = false, sawToday = false, closedToday = false;
        const todayRanges = [];
        text.split(';').forEach(function (rule) {
            rule = rule.trim();
            if (!rule || /week|month|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|easter|public holiday/i.test(rule)) {
                return;
            }
            const match = rule.match(/^((?:mo|tu|we|th|fr|sa|su)(?:\\s*-\\s*(?:mo|tu|we|th|fr|sa|su))?(?:\\s*,\\s*(?:mo|tu|we|th|fr|sa|su)(?:\\s*-\\s*(?:mo|tu|we|th|fr|sa|su))?)*)?\\s*(.*)$/i);
            if (!match) return;
            const days = match[1] ? dayList(match[1]) : [0, 1, 2, 3, 4, 5, 6];
            const times = match[2] || '';
            if (!days.length) return;
            sawRule = true;
            if (days.indexOf(day) === -1) return;
            sawToday = true;
            if (/\\b(off|closed)\\b/i.test(times)) {
                if (!parseTimeRanges(times).length) closedToday = true;
                return;
            }
            parseTimeRanges(times).forEach(function (range) { todayRanges.push(range); });
        });
        if (!sawRule) return { state: 'unknown', label: 'BİLİNMİYOR', today: '', raw: text };
        const todayText = todayRanges.map(function (range) {
            return minuteLabel(range[0]) + '–' + minuteLabel(range[1]);
        }).join(', ');
        const openNow = todayRanges.some(function (range) {
            return minutes >= range[0] && minutes < range[1];
        });
        if (openNow) return { state: 'open', label: 'AÇIK', today: todayText, raw: text };
        if (sawToday || closedToday) {
            return {
                state: 'closed', label: 'KAPALI',
                today: closedToday ? 'Bugün kapalı' : todayText, raw: text
            };
        }
        return { state: 'unknown', label: 'BİLİNMİYOR', today: todayText, raw: text };
    }
    function hoursBlock(item) {
        if (!String(item.open || '').trim()) {
            return '<div class="poi-hours"><div class="poi-hours-head">' +
                '<span class="poi-status unknown">BİLİNMİYOR</span>' +
                '<span>ÇALIŞMA SAATLERİ</span></div>' +
                '<div class="poi-hours-missing">Bu mekân için OSM\\'de açılış/kapanış saati ' +
                'kayıtlı değil.</div></div>';
        }
        const info = openingInfo(item.open);
        return '<div class="poi-hours"><div class="poi-hours-head">' +
            '<span class="poi-status ' + info.state + '">' + info.label + '</span>' +
            '<span>ÇALIŞMA SAATLERİ</span></div>' +
            '<div class="poi-hours-value">' + esc(item.open) + '</div>' +
            (info.today ? '<div class="poi-hours-today">Bugün: ' + esc(info.today) + '</div>' : '') +
            '<div class="poi-hours-today" style="color:#6d7480">Saatler OSM verisinden; ' +
            'resmî saatlerden farklı olabilir.</div></div>';
    }
    function openInfoPanel(animate) {
        infoPanel.classList.add('open');
        infoPanel.setAttribute('aria-hidden', 'false');
        if (infoFocus && window.jarvisMap.panInside) {
            window.jarvisMap.panInside(infoFocus, {
                paddingTopLeft: [36, 40],
                paddingBottomRight: [386, 72],
                animate: animate !== false
            });
        }
    }
    function closeInfoPanel() {
        infoPanel.classList.remove('open');
        infoPanel.setAttribute('aria-hidden', 'true');
    }
    function showInfoList() {
        const detail = document.getElementById('infoDetailView');
        const list = document.getElementById('infoListView');
        if (detail) detail.style.display = 'none';
        if (list) list.style.display = 'flex';
        infoFocus = null;
    }
    function renderInfoList() {
        if (!infoItems.length) {
            infoList.innerHTML = '<div class="mall-empty">Bu arama için haritada mekân bulunamadı.</div>';
            return;
        }
        const entries = visiblePoiEntries();
        if (!entries.length) {
            const label = FILTER_LABELS[poiFilter] || 'MEKÂN';
            infoList.innerHTML = '<div class="mall-empty">' +
                (poiFilter === 'mall'
                    ? 'Bu taramada AVM yok. Çevredeki AVM\\'leri ayrıca arayabilirsin.'
                    : 'Bu taramada ' + label.toLocaleLowerCase('tr-TR') + ' kategorisinde mekân yok.') +
                '</div>' +
                (poiFilter === 'mall'
                    ? '<button class="poi-search-mall" type="button" id="poiSearchMall">YAKIN AVM\\'LERİ ARA</button>'
                    : '');
            const searchMall = document.getElementById('poiSearchMall');
            if (searchMall) searchMall.addEventListener('click', requestMallScan);
            return;
        }
        infoList.innerHTML = entries.map(function (entry) {
            const item = entry.item;
            return '<button class="mall-store" type="button" data-info="' + entry.index + '">' +
                '<span class="mall-avatar poi-list-icon ' + poiKey(item) + '">' + poiIconHtml(item) + '</span>' +
                '<span class="mall-store-copy"><span class="mall-store-name">' + esc(item.name) + '</span>' +
                '<span class="mall-store-sub">' + esc(infoSub(item)) + '</span></span>' +
                '<span class="mall-chevron">›</span></button>';
        }).join('');
        Array.prototype.forEach.call(infoList.querySelectorAll('.mall-store'), function (button) {
            button.addEventListener('click', function () {
                showInfoDetail(Number(button.getAttribute('data-info')), true);
            });
        });
    }
    function showInfoDetail(index, openPanel) {
        const item = infoItems[index];
        if (!item) return;
        const category = item.category || (item.kind === 'stop' ? 'Durak' : 'Mekân');
        const liveText = (item.live || []).map(function (bus) {
            return bus.code + ' · ' + bus.eta_min + ' dk';
        }).join(', ');
        const hasCoords = item.lat != null && item.lon != null;
        const rows = [
            mallInfoRow('Kategori', esc(category)),
            mallInfoRow('Kaynak', esc(item.source_label || item.source || '')),
            mallInfoRow('Mesafe', item.distance_m != null ? esc(Number(item.distance_m) + ' m') : ''),
            mallInfoRow('Adres', esc(item.address || '')),
            mallInfoRow('Telefon', esc(item.phone || '')),
            mallInfoRow('Puan', esc(item.rating || '')),
            mallInfoRow('Fiyat', esc(item.price || '')),
            mallInfoRow('Marka', esc(item.brand || '')),
            mallInfoRow('İşletmeci', esc(item.operator || '')),
            mallInfoRow('Erişim', esc(item.wheelchair || '')),
            mallInfoRow('Kat', esc(item.level || '')),
            mallInfoRow('Durak No', esc(item.stop_id || '')),
            mallInfoRow('Hatlar', item.lines && item.lines.length ? esc(item.lines.join(', ')) : ''),
            mallInfoRow('Canlı', liveText ? esc(liveText) : ''),
            mallInfoRow('Web', infoWebsite(item.website)),
            mallInfoRow('Açıklama', esc(item.description || '')),
            mallInfoRow('Bilgi', item.detail && !liveText && !(item.lines && item.lines.length)
                ? esc(item.detail) : ''),
            mallInfoRow('Koordinat', item.lat != null && item.lon != null
                ? esc(Number(item.lat).toFixed(5) + '°N · ' + Number(item.lon).toFixed(5) + '°E')
                : ''),
            mallInfoRow('Harita', hasCoords
                ? '<a href="#" class="route-action" data-route-lat="' +
                   Number(item.lat).toFixed(6) + '" data-route-lon="' +
                   Number(item.lon).toFixed(6) + '" data-route-name="' +
                   esc(item.name || 'Hedef') + '" title="Rotayı bu haritada çiz">' +
                   'Yol tarifi çiz · uygulama içinde</a>'
                : '')
        ].join('');
        const subtitle = infoSub(item);
        const sourceLabel = String(item.source_label || item.source || '').trim();
        const note = item.kind === 'stop'
            ? 'Durak bilgileri Kocaeli e-komobil servisinden alınır; canlı süreler trafiğe göre değişebilir.'
            : (sourceLabel
                ? 'Kaynak: ' + sourceLabel + '. Veride kayıtlı olmayan bilgi gösterilmez; ' +
                  'eksik veya güncel olmayan kayıt olabilir.'
                : 'Bilgiler OpenStreetMap katkılarıdır; eksik veya güncel olmayabilir. ' +
                  'OSM verisinde kayıtlı olmayan bilgi gösterilmez.');
        document.getElementById('infoDetailBody').innerHTML =
            heroHtml(item, 'infoHero') +
            '<div class="mall-detail-card">' +
            '<div class="mall-detail-avatar poi-detail-icon">' + poiIconHtml(item) + '</div>' +
            '<div><div class="mall-detail-name">' + esc(item.name) + '</div>' +
            (subtitle ? '<div class="mall-detail-cat">' + esc(subtitle) + '</div>' : '') +
            '<div class="mall-detail-mall">' + esc(displayTitle((infoPayload || {}).title || '')) +
            '</div></div></div>' +
            hoursBlock(item) +
            (rows ? '<div class="mall-rows">' + rows + '</div>' : '') +
            '<div class="mall-detail-note">' + esc(note) +
            ' · Fotoğraf: OSM/Wikimedia Commons; bulunamazsa temsili görsel gösterilir.</div>';
        document.getElementById('infoListView').style.display = 'none';
        document.getElementById('infoDetailView').style.display = 'flex';
        infoFocus = (item.lat != null && item.lon != null) ? [item.lat, item.lon] : null;
        if (openPanel) openInfoPanel(true);
    }
    function clearInfo() {
        closeInfoPanel();
        infoPayload = null;
        infoItems = [];
        infoFocus = null;
        infoSilent = false;
        poiFilterBar.style.display = 'none';
        const detail = document.getElementById('infoDetailView');
        const list = document.getElementById('infoListView');
        if (detail) detail.style.display = 'none';
        if (list) list.style.display = 'flex';
        infoList.innerHTML = '';
        document.getElementById('infoDetailBody').innerHTML = '';
        document.getElementById('infoTitle').textContent = '';
        document.getElementById('infoMeta').textContent = '';
        document.getElementById('infoNote').textContent = '';
    }

    function displayTitle(text) {
        const value = String(text || '').trim();
        if (!value) return 'Mekânlar';
        return value.charAt(0).toLocaleUpperCase('tr-TR') + value.slice(1);
    }
    function visiblePoiEntries() {
        const entries = [];
        infoItems.forEach(function (item, index) {
            if (matchesPoiFilter(item)) entries.push({ item: item, index: index });
        });
        return entries;
    }
    function updatePoiHeader() {
        const total = infoItems.length;
        const visible = visiblePoiEntries().length;
        const centerName = ((infoPayload || {}).center || {}).name || '';
        const label = FILTER_LABELS[poiFilter] || 'TÜMÜ';
        const meta = poiFilter === 'all' ? total + ' mekân' : visible + '/' + total + ' · ' + label;
        document.getElementById('infoMeta').textContent =
            meta + (centerName ? ' · ' + centerName : '');
        poiFilterBar.style.display = total ? 'flex' : 'none';
        document.getElementById('infoNote').textContent = total
            ? 'Listeden bir mekân seçerek bilgi kartını açabilirsin. Filtrelerle AVM, market ve dükkânları ayırabilirsin.'
            : 'Bu arama için haritada mekân bulunamadı.';
    }
    function refreshPoiSummary() {
        if (!infoItems.length) return;
        const total = infoItems.length;
        const visible = visiblePoiEntries().length;
        const centerName = ((infoPayload || {}).center || {}).name || '';
        const filterText = poiFilter === 'all' ? '' : ' · ' + (FILTER_LABELS[poiFilter] || '');
        setSummary('<b>' + esc(displayTitle((infoPayload || {}).title || 'Harita')) + '</b>' +
            '<div class="sub">' + visible + (poiFilter === 'all' ? '' : '/' + total) +
            ' sonuç' + esc(filterText) + (centerName ? ' · ' + esc(centerName) : '') + '</div>');
    }
    function drawPlaceItems(fitMode) {
        placeLayer.clearLayers();
        const entries = visiblePoiEntries();
        const bounds = [];
        entries.forEach(function (entry, visibleIndex) {
            const item = entry.item;
            const label = infoSilent && visibleIndex < 8 ? item.name : '';
            L.marker([item.lat, item.lon], {
                icon: poiIcon(item, label),
                zIndexOffset: poiKey(item) === 'mall' ? 700 : 200,
                title: item.name || ''
            }).addTo(placeLayer)
              .bindTooltip(esc(item.name || ''), {
                  direction: 'top', offset: [0, -18], opacity: 0.95
              })
              .on('click', function () { showInfoDetail(entry.index, true); });
            bounds.push([item.lat, item.lon]);
        });
        if (!bounds.length || !fitMode) return;
        const maxZoom = fitMode === 'silent' ? 14 : (poiFilter === 'mall' ? 14 : 16);
        if (bounds.length === 1) {
            const zoom = Math.min(22, Math.max(maxZoom, window.jarvisMap.getZoom()));
            window.jarvisMap.setView(bounds[0], zoom);
            updateReadout(bounds[0][0], bounds[0][1]);
        } else {
            window.jarvisMap.fitBounds(L.latLngBounds(bounds), {
                padding: [56, 56], maxZoom: maxZoom
            });
        }
    }
    function setPoiFilter(filter, fit) {
        filterChip(filter);
        if (!infoItems.length) {
            renderInfoList();
            updatePoiHeader();
            return;
        }
        drawPlaceItems(fit === false ? null : 'filter');
        renderInfoList();
        updatePoiHeader();
        refreshPoiSummary();
    }
    window.jarvisSetFilter = function (filter) { setPoiFilter(filter, true); };
    function requestMallScan() {
        if (!window.mapBridge || typeof window.mapBridge.categoryClick !== 'function') {
            setSummary('<b>AVM ARAMASI HAZIR DEĞİL</b>' +
                '<div class="sub">Haritayı kapatıp yeniden aç</div>');
            return;
        }
        if (poiBusy) return;
        const center = window.jarvisMap.getCenter();
        window.jarvisPoiPending(center.lat, center.lng);
        setSummary('<b>YAKIN AVM\\'LER ARANIYOR…</b>' +
            '<div class="sub">Bu işlem birkaç saniye sürebilir</div>');
        window.mapBridge.categoryClick('mall', Number(center.lat), Number(center.lng),
            window.jarvisMap.getZoom());
    }
    window.jarvisCategoryDone = function (payload, filter) {
        poiBusy = false;
        if (poiPendingMarker) {
            window.jarvisMap.removeLayer(poiPendingMarker);
            poiPendingMarker = null;
        }
        if (payload && payload.error) {
            setSummary('<b>AVM\\'LER ARANAMADI</b>' +
                '<div class="sub">' + esc(payload.error) + '</div>');
            renderInfoList();
            updatePoiHeader();
            return;
        }
        filterChip(filter || 'mall');
        window.jarvisShowPlaces(payload || { title: 'Yakın AVM\\'ler', items: [] }, false, true);
    };

    window.jarvisShowPlaces = function (payload, silent, keepFilter) {
        placeLayer.clearLayers();
        clearInfo();
        const items = (payload.items || []).filter(function (item) {
            return item && item.lat != null && item.lon != null;
        });
        infoPayload = payload;
        infoItems = items;
        infoSilent = !!silent;
        if (!keepFilter) filterChip('all');
        document.getElementById('infoTitle').textContent = displayTitle(payload.title || 'Mekânlar');
        updatePoiHeader();
        renderInfoList();
        drawPlaceItems(silent ? 'silent' : 'always');
        refreshPoiSummary();
        if (items.length && !silent) {
            window.setTimeout(function () {
                if (infoItems !== items) return;
                const detailView = document.getElementById('infoDetailView');
                if (detailView && detailView.style.display !== 'none') return;
                if (infoItems.length === 1) showInfoDetail(0, false);
                else showInfoList();
                openInfoPanel(true);
            }, 420);
        }
    };

    window.jarvisShowLine = function (payload) {
        lineLayer.clearLayers();
        const stops = payload.stops || [];
        const points = stops.map(function (stop) { return [stop.lat, stop.lon]; });
        if (points.length > 1) {
            L.polyline(points, { color: '#2a0a10', weight: 8, opacity: 0.8 }).addTo(lineLayer);
            const line = L.polyline(points, {
                color: '#c9a468', weight: 3, opacity: 0.95, dashArray: '7 5'
            }).addTo(lineLayer);
            window.jarvisMap.fitBounds(line.getBounds(), { padding: [56, 56], maxZoom: 15 });
        }
        stops.forEach(function (stop, index) {
            const icon = L.divIcon({
                className: '', html: '<div class="stop-pin">' + esc(stop.stop_id || (index + 1)) + '</div>',
                iconSize: [18, 18], iconAnchor: [9, 9]
            });
            L.marker([stop.lat, stop.lon], { icon: icon }).addTo(lineLayer)
                .bindPopup('<strong style="color:#c9a468">' + esc(stop.name) + '</strong><br>' +
                    '<span style="color:#8a8a92">Durak No: ' + esc(stop.stop_id) + '</span>');
        });
        const line = payload.line || {};
        setSummary('<b>Hat ' + esc(line.code) + ' · ' + esc(line.label) + '</b><div class="sub">' +
            esc(line.from) + ' → ' + esc(line.to) + ' · ' + stops.length + ' durak</div>');
    };

    function mallInitial(name) {
        const text = String(name || '').trim();
        return text ? text.charAt(0).toLocaleUpperCase('tr-TR') : '•';
    }
    function mallLogoIcon(name) {
        return L.divIcon({
            className: '',
            html: '<div class="mall-logo-wrap"><div class="mall-logo-ring"></div>' +
                  '<div class="mall-logo"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
                  '<path d="M6.5 8.5h11l-1 10.2a1.6 1.6 0 0 1-1.6 1.4H9a1.6 1.6 0 0 1-1.6-1.4L6.5 8.5z" ' +
                  'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
                  '<path d="M9.2 8.5V7a2.8 2.8 0 0 1 5.6 0v1.5" stroke="currentColor" ' +
                  'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
                  '<path d="M9.4 12.1h5.2" stroke="currentColor" stroke-width="1.7" ' +
                  'stroke-linecap="round"/></svg></div>' +
                  '<div class="mall-logo-label">' + esc(name || 'AVM') + '</div></div>',
            iconSize: [46, 46], iconAnchor: [23, 23]
        });
    }
    function openMallPanel(animate) {
        mallPanel.classList.add('open');
        mallPanel.setAttribute('aria-hidden', 'false');
        if (mallMarker && window.jarvisMap.panInside) {
            window.jarvisMap.panInside(mallMarker.getLatLng(), {
                paddingTopLeft: [36, 40],
                paddingBottomRight: [386, 72],
                animate: animate !== false
            });
        }
    }
    function closeMallPanel() {
        mallPanel.classList.remove('open');
        mallPanel.setAttribute('aria-hidden', 'true');
    }
    function showMallList() {
        const detail = document.getElementById('mallDetailView');
        const list = document.getElementById('mallListView');
        if (detail) detail.style.display = 'none';
        if (list) list.style.display = 'flex';
    }
    function mallStoreSub(store) {
        const parts = [];
        if (store.category) parts.push(store.category);
        if (store.level) parts.push(store.level);
        return parts.join(' · ');
    }
    function renderMallList() {
        const term = mallSearch.value.trim().toLocaleLowerCase('tr-TR');
        const filtered = [];
        mallStores.forEach(function (store, index) {
            const haystack = ((store.name || '') + ' ' + (store.category || '') + ' ' +
                (store.brand || '')).toLocaleLowerCase('tr-TR');
            if (!term || haystack.indexOf(term) !== -1) {
                filtered.push({ store: store, index: index });
            }
        });
        if (!filtered.length) {
            const emptyText = mallStores.length
                ? 'Eşleşen mağaza yok.'
                : (mallStoreError
                    ? 'Mağaza listesi şu an alınamadı.'
                    : 'OSM verisinde bu AVM için mağaza kaydı bulunamadı.');
            mallList.innerHTML = '<div class="mall-empty">' + emptyText + '</div>';
            return;
        }
        mallList.innerHTML = filtered.map(function (entry) {
            const store = entry.store;
            return '<button class="mall-store" type="button" data-store="' + entry.index + '">' +
                '<span class="mall-avatar">' + esc(mallInitial(store.name)) + '</span>' +
                '<span class="mall-store-copy"><span class="mall-store-name">' + esc(store.name) + '</span>' +
                '<span class="mall-store-sub">' + esc(mallStoreSub(store)) + '</span></span>' +
                '<span class="mall-chevron">›</span></button>';
        }).join('');
        Array.prototype.forEach.call(mallList.querySelectorAll('.mall-store'), function (button) {
            button.addEventListener('click', function () {
                showMallStore(Number(button.getAttribute('data-store')));
            });
        });
    }
    function mallInfoRow(key, value) {
        if (!value) return '';
        return '<div class="mall-row"><div class="mall-row-key">' + esc(key) + '</div>' +
            '<div class="mall-row-val">' + value + '</div></div>';
    }
    function showMallStore(index) {
        const store = mallStores[index];
        if (!store) return;
        let website = '';
        const rawWebsite = String(store.website || '').trim();
        if (rawWebsite) {
            let href = '';
            try {
                const parsed = new URL(rawWebsite.indexOf('://') === -1
                    ? ('https://' + rawWebsite) : rawWebsite);
                if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                    href = parsed.href;
                }
            } catch (error) {
                href = '';
            }
            website = href
                ? ('<a href="' + esc(href) + '" target="_blank" rel="noopener">' +
                   esc(rawWebsite) + '</a>')
                : esc(rawWebsite);
        }
        const rows = [
            mallInfoRow('Kategori', esc(store.category || '')),
            mallInfoRow('Kat', esc(store.level || '')),
            mallInfoRow('Fiyat', esc(store.price || '')),
            mallInfoRow('Telefon', esc(store.phone || '')),
            mallInfoRow('Çalışma saatleri', esc(store.open || '')),
            mallInfoRow('Marka', esc(store.brand || '')),
            mallInfoRow('İşletmeci', esc(store.operator || '')),
            mallInfoRow('Erişim', esc(store.wheelchair || '')),
            mallInfoRow('Web', website),
            mallInfoRow('Açıklama', esc(store.description || ''))
        ].join('');
        const subtitle = mallStoreSub(store);
        const storePhotoHtml = store.photo
            ? '<img class="mall-photo" src="' + esc(store.photo) + '" alt="" loading="lazy" referrerpolicy="no-referrer">'
            : '';
        document.getElementById('mallDetailBody').innerHTML =
            '<div class="mall-detail-card">' + storePhotoHtml +
            (storePhotoHtml ? '' : '<div class="mall-detail-avatar">' + esc(mallInitial(store.name)) + '</div>') +
            '<div><div class="mall-detail-name">' +
            esc(store.name) + '</div>' +
            (subtitle ? '<div class="mall-detail-cat">' + esc(subtitle) + '</div>' : '') +
            '<div class="mall-detail-mall">' + esc((mallInfo || {}).name || '') + '</div></div></div>' +
            (rows ? '<div class="mall-rows">' + rows + '</div>' : '') +
            '<div class="mall-detail-note">Bilgiler OpenStreetMap katkılarıdır; eksik veya güncel ' +
            'olmayabilir. OSM verisinde kayıtlı olmayan bilgi gösterilmez.</div>';
        document.getElementById('mallListView').style.display = 'none';
        const detail = document.getElementById('mallDetailView');
        detail.style.display = 'flex';
    }
    function clearMall() {
        if (mallMarker) {
            window.jarvisMap.removeLayer(mallMarker);
            mallMarker = null;
        }
        mallInfo = null;
        mallStores = [];
        mallStoreError = '';
        const mallPhoto = document.getElementById('mallPhoto');
        if (mallPhoto) {
            mallPhoto.removeAttribute('src');
            mallPhoto.style.display = 'none';
        }
        closeMallPanel();
        showMallList();
        mallList.innerHTML = '';
        mallSearch.value = '';
    }
    window.jarvisShowMall = function (payload) {
        const mall = payload.mall || {};
        if (mall.lat == null || mall.lon == null) return;
        clearMall();
        clearInfo();
        placeLayer.clearLayers();
        routeLayer.clearLayers();
        lineLayer.clearLayers();
        if (focusMarker) {
            window.jarvisMap.removeLayer(focusMarker);
            focusMarker = null;
        }
        mallInfo = mall;
        mallStores = payload.stores || [];
        mallStoreError = String(payload.store_error || '');
        const mallPhoto = document.getElementById('mallPhoto');
        if (mall.photo) {
            mallPhoto.src = mall.photo;
            mallPhoto.style.display = 'block';
        } else {
            mallPhoto.removeAttribute('src');
            mallPhoto.style.display = 'none';
        }
        mallMarker = L.marker([mall.lat, mall.lon], {
            icon: mallLogoIcon(mall.name || 'AVM'),
            zIndexOffset: 900,
            title: mall.name || 'AVM'
        }).addTo(window.jarvisMap)
          .on('click', function () { openMallPanel(true); })
          .bindTooltip('Mağazaları görüntüle', { direction: 'top', offset: [0, -18], opacity: 0.92 });
        document.getElementById('mallTitle').textContent = mall.name || 'AVM';
        const meta = [];
        if (mall.address) meta.push(mall.address);
        meta.push(mallStoreError && !mallStores.length ? 'mağaza listesi alınamadı' : mallStores.length + ' mağaza');
        document.getElementById('mallMeta').textContent = meta.join(' · ');
        mallSearch.value = '';
        showMallList();
        renderMallList();
        document.getElementById('mallNote').textContent = mallStores.length
            ? 'Listeden bir mağaza seçerek bilgi kartını açabilirsin.'
            : (mallStoreError
                ? 'Mağaza servisi şu an yanıt vermedi; birkaç dakika sonra tekrar sorabilirsin.'
                : 'OSM üzerinde bu AVM için mağaza kaydı bulunamadı.');
        const zoom = Math.min(Number(payload.zoom) || 16, 16);
        window.jarvisMap.setView([mall.lat, mall.lon], zoom);
        updateReadout(mall.lat, mall.lon);
        setSummary('<b>' + esc(mall.name || 'AVM') + '</b><div class="sub">' +
            (mallStoreError && !mallStores.length
                ? 'mağaza listesi alınamadı'
                : mallStores.length + ' mağaza · logoya tıkla veya panelden seç') +
            '</div>');
        window.setTimeout(function () { openMallPanel(true); }, 420);
    };

    document.getElementById('mallClose').addEventListener('click', closeMallPanel);
    document.getElementById('mallBack').addEventListener('click', showMallList);
    mallSearch.addEventListener('input', renderMallList);
    document.getElementById('infoClose').addEventListener('click', closeInfoPanel);
    document.getElementById('infoBack').addEventListener('click', showInfoList);
    function hideBrokenPhoto(event) {
        const target = event.target;
        if (target && target.classList &&
            (target.classList.contains('mall-photo') ||
             target.classList.contains('poi-hero-photo'))) {
            target.style.display = 'none';
        }
    }
    document.getElementById('infoDetailBody').addEventListener('error', hideBrokenPhoto, true);
    document.getElementById('mallDetailBody').addEventListener('error', hideBrokenPhoto, true);
    document.getElementById('mallListView').addEventListener('error', hideBrokenPhoto, true);
    Array.prototype.forEach.call(poiFilterBar.querySelectorAll('.poi-chip'), function (button) {
        button.addEventListener('click', function () {
            const filter = button.getAttribute('data-filter') || 'all';
            const hasMall = infoItems.some(function (item) { return poiKey(item) === 'mall'; });
            setPoiFilter(filter, true);
            if (filter === 'mall' && !hasMall) requestMallScan();
        });
    });
    document.addEventListener('keydown', function (event) {
        const tag = String((event.target && event.target.tagName) || '').toUpperCase();
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (event.key === '+' || event.key === '=') {
            window.jarvisMap.zoomIn(1);
            event.preventDefault();
        } else if (event.key === '-' || event.key === '_') {
            window.jarvisMap.zoomOut(1);
            event.preventDefault();
        }
    });
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        if (mallPanel.classList.contains('open')) {
            const detail = document.getElementById('mallDetailView');
            if (detail && detail.style.display !== 'none') {
                showMallList();
            } else {
                closeMallPanel();
            }
            event.preventDefault();
            event.stopPropagation();
            return;
        }
        if (infoPanel.classList.contains('open')) {
            const detail = document.getElementById('infoDetailView');
            if (detail && detail.style.display !== 'none') {
                showInfoList();
            } else {
                closeInfoPanel();
            }
            event.preventDefault();
            event.stopPropagation();
        }
    }, true);

    document.addEventListener('click', function (event) {
        if (event.button !== 0) return;
        const target = event.target;
        const anchor = target && target.closest ? target.closest('a[href]') : null;
        if (!anchor) return;
        const routeLat = anchor.getAttribute('data-route-lat');
        const routeLon = anchor.getAttribute('data-route-lon');
        if (routeLat != null && routeLon != null) {
            event.preventDefault();
            event.stopPropagation();
            if (window.mapBridge && typeof window.mapBridge.routeTo === 'function') {
                window.mapBridge.routeTo(
                    Number(routeLat), Number(routeLon),
                    String(anchor.getAttribute('data-route-name') || 'Hedef')
                );
            }
            return;
        }
        const href = String(anchor.getAttribute('href') || '').trim();
        const lower = href.toLowerCase();
        if (lower.indexOf('http://') !== 0 && lower.indexOf('https://') !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        if (window.mapBridge && typeof window.mapBridge.openExternalLink === 'function') {
            window.mapBridge.openExternalLink(href);
        }
    }, true);

    window.jarvisApply = function (payload) {
        if (!payload) return;
        if (payload.kind === 'home') {
            jarvisSetHome(payload.home.lat, payload.home.lon, payload.home.name, true);
            setSummary('<b>Ev konumu · ' + esc(payload.home.name) + '</b><div class="sub">' +
                'Rotalar buradan başlar · ' + Number(payload.home.lat).toFixed(5) + '°N · ' +
                Number(payload.home.lon).toFixed(5) + '°E</div>');
        } else if (payload.kind === 'focus') {
            clearMall();
            clearInfo();
            window.jarvisFocus(payload);
        } else if (payload.kind === 'route') {
            clearMall();
            clearInfo();
            window.jarvisShowRoute(payload);
        } else if (payload.kind === 'places') {
            clearMall();
            window.jarvisShowPlaces(payload);
        } else if (payload.kind === 'mall') {
            window.jarvisShowMall(payload);
        } else if (payload.kind === 'bus_line') {
            clearMall();
            clearInfo();
            window.jarvisShowLine(payload);
        }
        setTimeout(function () { window.jarvisMap.invalidateSize(); }, 60);
    };

    L.control.scale({ imperial: false, position: 'bottomright' }).addTo(window.jarvisMap);
    window.jarvisMap.on('mousemove', function (event) {
        const p = event.latlng;
        document.getElementById('readout').textContent =
            homeLabel() + ' · ' + p.lat.toFixed(3) + '°N ' + p.lng.toFixed(3) + '°E';
    });
    setTimeout(function () { window.jarvisMap.invalidateSize(); }, 120);
})();
</script>
</body>
</html>
"""
