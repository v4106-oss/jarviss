"""Uygulama içi tek motorlu tarayıcı servisi.

Harita, görünür tarayıcı ve ajan okumaları aynı kalıcı QWebEngineProfile'ı
paylaşır. Harita ve tarayıcı görünümleri uygulama boyunca yaşar; modallar
kapansa da yok edilmez. Böylece ikinci bir Chromium motoru doğmaz, disk
önbelleği ve çerezler korunur.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEventLoop,
    QObject,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
    Slot,
)

try:
    from PySide6.QtGui import QColor
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

    BROWSER_AVAILABLE = True
except ImportError:
    QColor = None
    QWebChannel = None
    QWebEnginePage = None
    QWebEngineProfile = None
    QWebEngineSettings = None
    QWebEngineView = None
    QSizePolicy = None
    QVBoxLayout = None
    QWidget = None
    BROWSER_AVAILABLE = False

from app.theme import ultron as U


READER_JS = (
    "(function(){var body=document.body?document.body.innerText:'';"
    "body=(body||'').replace(/\\s+/g,' ').trim().slice(0,12000);return body;})()"
)


class MapBridge(QObject):
    poi_clicked = Signal(float, float, float)
    category_requested = Signal(str, float, float, float)
    route_requested = Signal(float, float, str)
    external_link_requested = Signal(str)

    @Slot(float, float, float)
    def poiClick(self, lat, lon, zoom):
        self.poi_clicked.emit(float(lat), float(lon), float(zoom))

    @Slot(str, float, float, float)
    def categoryClick(self, category, lat, lon, zoom):
        self.category_requested.emit(str(category), float(lat), float(lon), float(zoom))

    @Slot(float, float, str)
    def routeTo(self, lat, lon, name):
        self.route_requested.emit(float(lat), float(lon), str(name or "Hedef"))

    @Slot(str)
    def openExternalLink(self, url):
        self.external_link_requested.emit(str(url))


def _browser_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    path = root / "Jarvis" / "browser"
    try:
        (path / "profile").mkdir(parents=True, exist_ok=True)
        (path / "cache").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


class BrowserService(QObject):
    """Tek profil; kalıcı harita, görünür tarayıcı ve gizli okuma sayfası."""

    map_ready = Signal(bool)
    poi_clicked = Signal(float, float, float)
    category_requested = Signal(str, float, float, float)
    route_requested = Signal(float, float, str)
    external_link_requested = Signal(str)
    _agent_read_requested = Signal(str, int, int)

    _instance: Optional["BrowserService"] = None

    @classmethod
    def instance(cls) -> "BrowserService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def existing(cls) -> Optional["BrowserService"]:
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._profile = None
        self._host = None
        self._host_layout = None
        self._attached_layout = None
        self._view = None
        self._page = None
        self._channel = None
        self._bridge = None
        self._map_loading = False
        self._map_loaded = False
        self._map_error = ""
        self._last_home = None
        self._reader_page = None
        self._reader_busy = False
        self._reader_done = False
        self._reader_result = ""
        self._reader_token = 0
        self._reader_loop = None
        self._read_lock = threading.Lock()
        self._read_waiters: dict[int, threading.Event] = {}
        self._read_results: dict[int, str] = {}
        self._read_request_id = 0
        self._agent_read_requested.connect(self._agent_read_url)

    # ---------- kurulum ----------

    def _ensure_profile(self) -> None:
        if self._profile is not None:
            return
        if not BROWSER_AVAILABLE:
            raise RuntimeError("Qt WebEngine yüklü değil")
        data = _browser_data_dir()
        profile = QWebEngineProfile("jarvis", self)
        profile.setPersistentStoragePath(str(data / "profile"))
        profile.setCachePath(str(data / "cache"))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setHttpCacheMaximumSize(256 * 1024 * 1024)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        self._profile = profile

    def _configure_page(self, page, local_files: bool) -> None:
        page.setBackgroundColor(QColor(U.BACKGROUND))
        try:
            settings = page.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
            )
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, bool(local_files)
            )
            settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False
            )
        except Exception:
            pass

        def deny(origin, feature, current=page):
            try:
                current.setFeaturePermission(
                    origin, feature, QWebEnginePage.PermissionPolicy.PermissionDenied
                )
            except Exception:
                pass

        page.featurePermissionRequested.connect(deny)

    def _ensure_view(self, parent) -> None:
        if self._view is not None:
            return
        self._ensure_profile()
        # Görünüm ilk kez doğru üst pencere altında kurulur; sonradan taşınmaz.
        self._host = QWidget(parent)
        self._host.setObjectName("jarvisBrowserHost")
        self._host.hide()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(0)

        view = QWebEngineView(self._host)
        page = QWebEnginePage(self._profile, view)
        view.setPage(page)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        view.hide()
        self._configure_page(page, local_files=True)
        page.loadFinished.connect(self._on_map_load_finished)

        channel = QWebChannel(page)
        page.setWebChannel(channel)
        bridge = MapBridge(self)
        channel.registerObject("mapBridge", bridge)
        bridge.poi_clicked.connect(self.poi_clicked.emit)
        bridge.category_requested.connect(self.category_requested.emit)
        bridge.route_requested.connect(self.route_requested.emit)
        bridge.external_link_requested.connect(self.external_link_requested.emit)

        self._view = view
        self._page = page
        self._channel = channel
        self._bridge = bridge
        self._host_layout.addWidget(view)

    def create_view(self, parent) -> "QWebEngineView":
        """Aynı profili kullanan bağımsız bir tarayıcı görünümü kurar."""
        self._ensure_profile()
        view = QWebEngineView(parent)
        page = QWebEnginePage(self._profile, view)
        view.setPage(page)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._configure_page(page, local_files=False)
        return view

    def prewarm(self) -> None:
        """Profil/önbellek motorunu arka planda hazırlar; görünüm oluşturmaz."""
        try:
            self._ensure_profile()
        except Exception:
            pass

    # ---------- harita yüzeyi ----------

    @property
    def map_loaded(self) -> bool:
        return self._map_loaded

    @property
    def map_error(self) -> str:
        return self._map_error

    def attach_map(self, layout, parent):
        host_parent = layout.parentWidget() if layout is not None else None
        self._ensure_view(host_parent or parent)
        if self._attached_layout is not layout:
            if self._attached_layout is not None:
                try:
                    self._attached_layout.removeWidget(self._host)
                except RuntimeError:
                    pass
            layout.addWidget(self._host)
            self._attached_layout = layout
        if self._map_loaded:
            self.show_map()
        return self._view

    def show_map(self) -> None:
        try:
            if self._host is not None:
                self._host.show()
            if self._view is not None:
                self._view.show()
        except RuntimeError:
            pass

    def detach_map(self) -> None:
        host = self._host
        if host is None:
            return
        try:
            if self._attached_layout is not None:
                self._attached_layout.removeWidget(host)
            host.hide()
            if self._view is not None:
                self._view.hide()
        except RuntimeError:
            pass
        self._attached_layout = None

    def load_map(self, html: str, base_url: QUrl, home: Optional[dict] = None) -> None:
        if self._map_loaded or self._map_loading or self._view is None:
            return
        if home:
            self._last_home = self._home_signature(home, True)
        self._map_loading = True
        self._map_error = ""
        self._page.setHtml(html, base_url)

    @Slot(bool)
    def _on_map_load_finished(self, ok: bool) -> None:
        if not self._map_loading:
            return
        self._map_loading = False
        self._map_loaded = bool(ok)
        if not ok:
            self._map_error = "harita sayfası yüklenemedi"
        self.map_ready.emit(bool(ok))

    def run_js(self, script: str, callback=None) -> None:
        if self._page is None:
            return
        try:
            if callback is None:
                self._page.runJavaScript(script)
            else:
                self._page.runJavaScript(script, callback)
        except RuntimeError:
            pass

    def remember_home(self, home: dict, is_set: bool = True) -> None:
        self._last_home = self._home_signature(home, is_set)

    def sync_home(self, home: dict, is_set: bool) -> bool:
        """Ev işaretçisini günceller; konum gerçekten değiştiyse True döner."""
        signature = self._home_signature(home, is_set)
        changed = signature != self._last_home
        self._last_home = signature
        try:
            lat = float(home["lat"])
            lon = float(home["lon"])
        except (KeyError, TypeError, ValueError):
            return changed
        name = json.dumps(str(home.get("name") or ""), ensure_ascii=False)
        self.run_js(
            "window.jarvisSetHome && window.jarvisSetHome("
            f"{lat:.6f}, {lon:.6f}, {name}, {'true' if is_set else 'false'});"
        )
        return changed

    @staticmethod
    def _home_signature(home: dict, is_set: bool):
        try:
            return (
                round(float(home.get("lat")), 6),
                round(float(home.get("lon")), 6),
                str(home.get("name") or ""),
                bool(is_set),
            )
        except (TypeError, ValueError):
            return None

    # ---------- ajan tarayıcısı ----------

    def _ensure_reader(self) -> None:
        if self._reader_page is not None:
            return
        if not BROWSER_AVAILABLE:
            raise RuntimeError("Qt WebEngine yüklü değil")
        self._ensure_profile()
        page = QWebEnginePage(self._profile, self)
        page.loadFinished.connect(self._on_reader_load_finished)
        self._reader_page = page

    def _release_reader(self) -> None:
        page = self._reader_page
        self._reader_page = None
        if page is None:
            return
        try:
            page.loadFinished.disconnect(self._on_reader_load_finished)
        except (RuntimeError, TypeError):
            pass
        try:
            page.triggerAction(QWebEnginePage.WebAction.Stop)
        except Exception:
            pass
        page.deleteLater()

    def read_url(self, url: str, timeout_ms: int = 20000) -> str:
        """Sayfayı gizli tarayıcıda açıp görünür metnini döndürür."""
        if not BROWSER_AVAILABLE:
            return "❌ Uygulama içi tarayıcı motoru yüklü değil."
        target = str(url or "").strip()
        if not re.match(r"^https?://", target, re.IGNORECASE):
            return "❌ Tarayıcı yalnızca http/https adreslerini okuyabilir."
        timeout = max(3000, int(timeout_ms or 20000))
        if QThread.currentThread() is self.thread():
            return self._read_url_blocking(target, timeout)
        with self._read_lock:
            self._read_request_id += 1
            token = self._read_request_id
            waiter = threading.Event()
            self._read_waiters[token] = waiter
        try:
            self._agent_read_requested.emit(target, timeout, token)
        except RuntimeError as error:
            with self._read_lock:
                self._read_waiters.pop(token, None)
            return f"❌ Tarayıcı çağrısı başarısız: {error}"
        if not waiter.wait(timeout / 1000 + 8):
            with self._read_lock:
                self._read_waiters.pop(token, None)
                self._read_results.pop(token, None)
            return f"❌ Tarayıcı yanıt vermedi: {target}"
        with self._read_lock:
            self._read_waiters.pop(token, None)
            return self._read_results.pop(token, "")

    @Slot(str, int, int)
    def _agent_read_url(self, url: str, timeout_ms: int, token: int) -> None:
        try:
            result = self._read_url_blocking(url, timeout_ms)
        except Exception as error:
            result = f"❌ Tarayıcı okuması başarısız: {error}"
        with self._read_lock:
            waiter = self._read_waiters.get(token)
            if waiter is not None:
                self._read_results[token] = result
        if waiter is not None:
            waiter.set()

    def _read_url_blocking(self, url: str, timeout_ms: int) -> str:
        if self._reader_busy:
            return "❌ Tarayıcı zaten başka bir sayfayı okuyor; biraz sonra tekrar dene."
        try:
            self._ensure_reader()
        except Exception as error:
            return f"❌ Tarayıcı hazırlanamadı: {error}"
        self._reader_busy = True
        self._reader_done = False
        self._reader_token += 1
        self._reader_result = ""
        loop = QEventLoop()
        self._reader_loop = loop
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        try:
            self._reader_page.load(QUrl(url))
            loop.exec()
        finally:
            timer.stop()
            timer.deleteLater()
            self._reader_loop = None
            self._reader_busy = False
            self._release_reader()
        if not self._reader_done:
            return f"❌ Tarayıcı zaman aşımına uğradı: {url}"
        return self._reader_result or f"❌ Tarayıcı sayfadan metin okuyamadı: {url}"

    @Slot(bool)
    def _on_reader_load_finished(self, ok: bool) -> None:
        if not self._reader_busy or self._reader_loop is None or self._reader_done:
            return
        token = self._reader_token
        page = self._reader_page
        if not ok:
            self._reader_done = True
            self._reader_result = f"❌ Tarayıcı sayfayı yükleyemedi: {page.url().toString()}"
            self._reader_loop.quit()
            return
        final_url = page.url().toString()
        title = page.title() or ""
        limit = self._reader_token

        def done(value):
            if limit != self._reader_token:
                return
            text = str(value or "").strip()
            if not text:
                self._reader_result = f"❌ Sayfada okunabilir metin yok: {final_url}"
            else:
                header = f"SAYFA: {title or '(başlıksız)'}\nURL: {final_url}\n\n"
                self._reader_result = header + text[:14000]
            self._reader_done = True
            if self._reader_loop is not None:
                self._reader_loop.quit()

        try:
            page.runJavaScript(READER_JS, done)
        except RuntimeError:
            self._reader_done = True
            self._reader_result = f"❌ Tarayıcı metni okuyamadı: {final_url}"
            self._reader_loop.quit()

    def shutdown(self) -> None:
        self.detach_map()
        self._reader_page = None
        self._view = None
        self._page = None
        self._profile = None
        self._host = None
        self._host_layout = None
        self._attached_layout = None


def browser_service() -> BrowserService:
    return BrowserService.instance()
