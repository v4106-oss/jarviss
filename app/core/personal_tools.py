"""Kişisel yardımcılar: pano geçmişi, sistem kontrolleri ve ajan verileri."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional, Sequence

from app.core.hud_data import HudStore, turkish_date


# --------------------------------------------------------------------- metin
def turkish_fold(value: Any) -> str:
    """Türkçe harfleri arama için sadeleştirir (İ/ı/ş/ğ/ç/ö/ü)."""
    text = str(value or "").replace("İ", "i").replace("I", "i").casefold()
    for source, target in (
        ("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"),
        ("ö", "o"), ("ç", "c"), ("â", "a"), ("î", "i"), ("û", "u"),
    ):
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def matches_query(query: str, *fields: Any) -> bool:
    folded = turkish_fold(query)
    if not folded:
        return True
    haystack = turkish_fold(" ".join(str(field) for field in fields if field))
    return all(token in haystack for token in folded.split())


def _moment(value: Any) -> Optional[datetime]:
    try:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is not None:
            moment = moment.astimezone().replace(tzinfo=None)
        return moment
    except (TypeError, ValueError):
        return None


def _format_moment(value: Any) -> str:
    moment = _moment(value)
    return moment.strftime("%d.%m.%Y %H:%M") if moment else str(value or "—")


# --------------------------------------------------------------------- pano
class ClipboardHistory:
    """Oturum boyunca bellekte tutulan pano geçmişi; diske yazılmaz."""

    def __init__(self, limit: int = 30, max_chars: int = 20_000) -> None:
        self.limit = max(1, int(limit))
        self.max_chars = max(1, int(max_chars))
        self._items: List[str] = []
        self._lock = threading.Lock()

    def add(self, text: Any) -> bool:
        value = str(text or "").strip()
        if not value or len(value) > self.max_chars:
            return False
        with self._lock:
            self._items = [item for item in self._items if item != value]
            self._items.insert(0, value)
            del self._items[self.limit:]
        return True

    def items(self) -> List[str]:
        with self._lock:
            return list(self._items)

    def remove(self, text: str) -> None:
        with self._lock:
            self._items = [item for item in self._items if item != text]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def read_clipboard_text() -> str:
    """Windows panosundaki Unicode metni okur (Qt bağımlılığı olmadan)."""
    if os.name != "nt":
        raise RuntimeError("Pano okuma yalnızca Windows'ta destekleniyor.")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    opened = False
    try:
        for _ in range(6):
            if user32.OpenClipboard(None):
                opened = True
                break
            threading.Event().wait(0.04)
        if not opened:
            raise RuntimeError("Pano şu anda başka bir uygulama tarafından kullanılıyor.")
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return str(ctypes.wstring_at(pointer))
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        if opened:
            user32.CloseClipboard()


def write_clipboard_text(text: Any) -> None:
    if os.name != "nt":
        raise RuntimeError("Pano yazma yalnızca Windows'ta destekleniyor.")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE
    value = str(text or "")
    opened = False
    try:
        for _ in range(6):
            if user32.OpenClipboard(None):
                opened = True
                break
            threading.Event().wait(0.04)
        if not opened:
            raise RuntimeError("Pano şu anda başka bir uygulama tarafından kullanılıyor.")
        user32.EmptyClipboard()
        buffer = value.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(0x0042, len(buffer))  # GMEM_MOVEABLE
        if not handle:
            raise RuntimeError("Pano belleği ayrılamadı.")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise RuntimeError("Pano belleği kilitlenemedi.")
        try:
            ctypes.memmove(pointer, buffer, len(buffer))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(13, handle):
            kernel32.GlobalFree(handle)
            raise RuntimeError("Pano güncellenemedi.")
    finally:
        if opened:
            user32.CloseClipboard()


def clear_clipboard() -> None:
    if os.name != "nt":
        raise RuntimeError("Pano temizleme yalnızca Windows'ta destekleniyor.")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    opened = False
    try:
        for _ in range(6):
            if user32.OpenClipboard(None):
                opened = True
                break
            threading.Event().wait(0.04)
        if not opened:
            raise RuntimeError("Pano şu anda başka bir uygulama tarafından kullanılıyor.")
        if not user32.EmptyClipboard():
            raise RuntimeError("Pano temizlenemedi.")
    finally:
        if opened:
            user32.CloseClipboard()


# ------------------------------------------------------------- sistem kontrol
class SystemControls:
    """Ses, medya ve güç işlemleri; testlerde arka uç enjekte edilebilir."""

    KEY_ACTIONS = {
        "volume_up": 0xAF,
        "volume_down": 0xAE,
        "mute": 0xAD,
        "media_play_pause": 0xB3,
        "media_next": 0xB0,
        "media_previous": 0xB1,
        "media_stop": 0xB2,
    }
    MESSAGES = {
        "volume_up": "Ses seviyesi artırıldı.",
        "volume_down": "Ses seviyesi azaltıldı.",
        "mute": "Ses sessize alındı / geri açıldı.",
        "media_play_pause": "Medya oynat/duraklat gönderildi.",
        "media_next": "Sonraki parçaya geçildi.",
        "media_previous": "Önceki parçaya geçildi.",
        "media_stop": "Medya durduruldu.",
    }
    POWER_ACTIONS = {"shutdown", "restart", "sleep", "cancel_shutdown", "lock"}

    def __init__(
        self,
        key_sender: Optional[Callable[[int], None]] = None,
        locker: Optional[Callable[[], None]] = None,
        command_runner: Optional[Callable[[List[str]], tuple]] = None,
    ) -> None:
        self._key_sender = key_sender
        self._locker = locker
        self._command_runner = command_runner

    @staticmethod
    def _default_key(vk: int) -> None:
        if os.name != "nt":
            raise RuntimeError("Sistem tuşları yalnızca Windows'ta destekleniyor.")
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)

    @staticmethod
    def _default_lock() -> None:
        if os.name != "nt" or not ctypes.windll.user32.LockWorkStation():
            raise RuntimeError("Ekran kilidi başlatılamadı.")

    @staticmethod
    def _default_command(args: List[str]) -> tuple:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        return completed.returncode, output

    def _send_key(self, vk: int) -> None:
        (self._key_sender or self._default_key)(vk)

    def _lock(self) -> None:
        (self._locker or self._default_lock)()

    def _command(self, args: List[str]) -> tuple:
        return (self._command_runner or self._default_command)(args)

    def action(self, action: str, seconds: int = 60, reason: str = "Jarvis") -> str:
        key = str(action or "").strip().casefold()
        if key in self.KEY_ACTIONS:
            self._send_key(self.KEY_ACTIONS[key])
            return f"✅ {self.MESSAGES[key]}"
        if key == "lock":
            self._lock()
            return "✅ Ekran kilitlendi."
        if key == "sleep":
            code, output = self._command(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            if code != 0:
                raise RuntimeError(output or "Uyku modu başlatılamadı.")
            return "✅ Bilgisayar uyku moduna alınıyor."
        if key == "cancel_shutdown":
            code, output = self._command(["shutdown", "/a"])
            if code == 1116:
                return "ℹ️ İptal edilecek zamanlanmış kapatma yok."
            if code != 0:
                raise RuntimeError(output or "Zamanlanmış kapatma iptal edilemedi.")
            return "✅ Zamanlanmış kapatma/yeniden başlatma iptal edildi."
        if key in {"shutdown", "restart"}:
            delay = max(15, min(int(seconds or 60), 3600))
            flag = "/s" if key == "shutdown" else "/r"
            note = " ".join(str(reason or "Jarvis").split())[:180] or "Jarvis"
            code, output = self._command([
                "shutdown", flag, "/t", str(delay), "/c", note,
            ])
            if code != 0:
                raise RuntimeError(output or "Güç işlemi başlatılamadı.")
            verb = "kapanacak" if key == "shutdown" else "yeniden başlatılacak"
            return (
                f"✅ Bilgisayar {delay} saniye içinde {verb}. "
                "İptal için system_control action=cancel_shutdown kullan."
            )
        raise ValueError(f"Desteklenmeyen sistem işlemi: {action}")


# ------------------------------------------------------------------- brifing
def build_briefing_text(
    now: Optional[datetime] = None,
    weather: Optional[dict] = None,
    events: Optional[Sequence[dict]] = None,
    reminders: Optional[Sequence[dict]] = None,
    notes: Optional[Sequence[dict]] = None,
    snapshot: Optional[dict] = None,
) -> str:
    now = now or datetime.now()
    lines: List[str] = [f"{turkish_date(now).upper()} · {now:%H:%M}", ""]

    current = (weather or {}).get("current") or {}
    if current:
        location = str((weather or {}).get("location") or "Konum").split("·")[0].strip()
        lines.append(
            f"HAVA · {location} · {current.get('temp', '—')}° · {current.get('text', '—')}"
        )
        daily = (weather or {}).get("daily") or []
        if daily:
            first = daily[0]
            lines.append(
                f"En düşük {first.get('min', '—')}°, en yüksek {first.get('max', '—')}° · "
                f"Rüzgar {current.get('wind', '—')} km/s · Nem %{current.get('humidity', '—')}"
            )
    else:
        lines.append("HAVA · Veri henüz hazır değil; birazdan güncellenecek.")

    lines.append("")
    todays = []
    upcoming = []
    for event in events or []:
        moment = _moment(event.get("when"))
        if moment is None:
            continue
        if moment.date() == now.date() and moment >= now - timedelta(minutes=1):
            todays.append((moment, event))
        elif moment > now:
            upcoming.append((moment, event))
    todays.sort(key=lambda pair: pair[0])
    upcoming.sort(key=lambda pair: pair[0])
    if todays:
        first_moment, first_event = todays[0]
        extra = f" (+{len(todays) - 1} etkinlik daha)" if len(todays) > 1 else ""
        lines.append(f"TAKVİM · Bugün {first_moment:%H:%M} · {first_event.get('title', '—')}{extra}")
    elif upcoming:
        first_moment, first_event = upcoming[0]
        lines.append(
            f"TAKVİM · Bugün etkinlik yok. En yakın: "
            f"{first_moment:%d.%m %H:%M} · {first_event.get('title', '—')}"
        )
    else:
        lines.append("TAKVİM · Bugün ve yakın zamanda etkinlik yok.")

    pending = []
    for reminder in reminders or []:
        if reminder.get("done"):
            continue
        moment = _moment(reminder.get("when"))
        if moment is not None:
            pending.append((moment, reminder))
    pending.sort(key=lambda pair: pair[0])
    if pending:
        first_moment, first_reminder = pending[0]
        when = first_moment.strftime("%H:%M") if first_moment.date() == now.date() else first_moment.strftime("%d.%m %H:%M")
        lines.append(f"HATIRLATICI · {len(pending)} bekleyen · İlk: {when} · {first_reminder.get('text', '—')}")
    else:
        lines.append("HATIRLATICI · Bekleyen hatırlatıcı yok.")

    note_items = list(notes or [])
    if note_items:
        latest = str(note_items[-1].get("text") or "").replace("\n", " ").strip()
        lines.append(f"NOT · {len(note_items)} kayıtlı · Son: {latest[:120]}")
    else:
        lines.append("NOT · Kayıtlı not yok.")

    if snapshot and snapshot.get("available"):
        system_line = (
            f"SİSTEM · CPU %{float(snapshot.get('cpu', 0)):.0f} · "
            f"RAM %{float(snapshot.get('memory_percent', 0)):.0f} · "
            f"Disk %{float(snapshot.get('disk_percent', 0)):.0f}"
        )
        battery = snapshot.get("battery_percent")
        if battery is not None:
            system_line += f" · Pil %{float(battery):.0f}"
        lines.append(system_line)
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------- dışa aktarma
def _message_field(message: Any, name: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def messages_to_markdown(messages: Sequence[Any], title: str = "Jarvis Sohbet Geçmişi") -> str:
    lines = [f"# {title}", ""]
    for message in messages:
        role = str(_message_field(message, "role", "assistant") or "assistant")
        content = str(_message_field(message, "content", "") or "").strip()
        created = _message_field(message, "created_at")
        stamp = ""
        if isinstance(created, datetime):
            stamp = created.strftime("%d.%m.%Y %H:%M")
        elif created:
            moment = _moment(created)
            stamp = moment.strftime("%d.%m.%Y %H:%M") if moment else str(created)
        name = "Sen" if role == "user" else "Jarvis"
        lines.append(f"### {name}" + (f" · {stamp}" if stamp else ""))
        lines.append("")
        lines.append(content or "_(boş yanıt)_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# -------------------------------------------------------------------- ajan
class PersonalTools:
    """Ajanın not, hatırlatıcı ve takvim üzerinde çalışmasını sağlar."""

    def __init__(self, store: Optional[HudStore]) -> None:
        self.store = store

    @staticmethod
    def _limit(args: dict) -> int:
        try:
            return max(1, min(int(args.get("limit", 10)), 50))
        except (TypeError, ValueError):
            return 10

    def _require_store(self) -> HudStore:
        if self.store is None:
            raise RuntimeError("Kişisel veri deposu kullanılamıyor.")
        return self.store

    @staticmethod
    def _when(args: dict, required: bool = True) -> Optional[str]:
        raw = str(args.get("when") or "").strip()
        if raw:
            moment = _moment(raw)
            if moment is None:
                raise ValueError("when alanı ISO biçiminde olmalı (örn. 2026-10-25T18:30).")
            return moment.replace(second=0, microsecond=0).isoformat(timespec="minutes")
        try:
            minutes = int(args.get("in_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes:
            moment = datetime.now() + timedelta(minutes=max(1, min(minutes, 525_600)))
            return moment.replace(second=0, microsecond=0).isoformat(timespec="minutes")
        if required:
            raise ValueError("Zaman bilgisi gerekli: when (ISO) veya in_minutes.")
        return None

    def run(self, args: dict) -> str:
        store = self._require_store()
        resource = str(args.get("resource") or "").strip().casefold()
        action = str(args.get("action") or "list").strip().casefold()
        limit = self._limit(args)

        if resource == "note":
            if action == "list":
                notes = list(reversed(store.notes()))[:limit]
                if not notes:
                    return "ℹ️ Kayıtlı not yok."
                rows = [
                    f"• [{item.get('id')}] {str(item.get('text') or '')[:200]}"
                    for item in notes
                ]
                return f"✅ {len(notes)} not:\n" + "\n".join(rows)
            if action == "add":
                text = str(args.get("text") or "").strip()
                if not text:
                    raise ValueError("Not metni boş olamaz.")
                item = store.add_note(text[:4000])
                return f"✅ Not eklendi. id={item['id']}"
            if action == "delete":
                note_id = str(args.get("id") or "").strip()
                if not note_id:
                    raise ValueError("Silinecek not için id gerekli.")
                before = len(store.notes())
                store.delete_note(note_id)
                if len(store.notes()) == before:
                    return "ℹ️ Bu kimlikte not bulunamadı."
                return "✅ Not silindi."
            raise ValueError("Not için action: list/add/delete olabilir.")

        if resource == "reminder":
            if action == "list":
                items = store.pending_reminders()[:limit]
                if not items:
                    return "ℹ️ Bekleyen hatırlatıcı yok."
                rows = [
                    f"• [{item.get('id')}] {_format_moment(item.get('when'))} · {item.get('text', '—')}"
                    for item in items
                ]
                return f"✅ {len(items)} hatırlatıcı:\n" + "\n".join(rows)
            if action == "add":
                text = str(args.get("text") or "").strip()
                if not text:
                    raise ValueError("Hatırlatıcı metni boş olamaz.")
                when = self._when(args, required=True)
                item = store.add_reminder(text[:1000], when or "")
                return f"✅ Hatırlatıcı kuruldu: {_format_moment(item['when'])} · id={item['id']}"
            if action == "complete":
                reminder_id = str(args.get("id") or "").strip()
                if not reminder_id:
                    raise ValueError("Tamamlanacak hatırlatıcı için id gerekli.")
                if not store.complete_reminder(reminder_id):
                    return "ℹ️ Bu kimlikte hatırlatıcı bulunamadı."
                return "✅ Hatırlatıcı tamamlandı."
            if action == "delete":
                reminder_id = str(args.get("id") or "").strip()
                if not reminder_id:
                    raise ValueError("Silinecek hatırlatıcı için id gerekli.")
                if not store.delete_reminder(reminder_id):
                    return "ℹ️ Bu kimlikte hatırlatıcı bulunamadı."
                return "✅ Hatırlatıcı silindi."
            raise ValueError("Hatırlatıcı için action: list/add/complete/delete olabilir.")

        if resource == "event":
            if action == "list":
                events = store.upcoming_events()[:limit]
                if not events:
                    return "ℹ️ Yaklaşan etkinlik yok."
                rows = [
                    f"• [{item.get('id')}] {_format_moment(item.get('when'))} · {item.get('title', '—')}"
                    for item in events
                ]
                return f"✅ {len(events)} etkinlik:\n" + "\n".join(rows)
            if action == "add":
                title = str(args.get("text") or args.get("title") or "").strip()
                if not title:
                    raise ValueError("Etkinlik adı boş olamaz.")
                when = self._when(args, required=True)
                item = store.add_event(title[:500], when or "")
                return f"✅ Etkinlik eklendi: {_format_moment(item['when'])} · id={item['id']}"
            if action == "delete":
                event_id = str(args.get("id") or "").strip()
                if not event_id:
                    raise ValueError("Silinecek etkinlik için id gerekli.")
                if not store.delete_event(event_id):
                    return "ℹ️ Bu kimlikte etkinlik bulunamadı."
                return "✅ Etkinlik silindi."
            raise ValueError("Etkinlik için action: list/add/delete olabilir.")

        raise ValueError("resource: note, reminder veya event olmalı.")
