"""Jarvis güvenlik çekirdeği.

Bu modül Gemini'nin (veya herhangi bir planlayıcının) isteyebileceği eylemlerin
önüne konan katmandır:

* ``PathPolicy``      hangi yollara dokunulabileceğini belirler (klasör sınırı).
* ``PermissionGate``  risk seviyesi + erişim modu -> izin ver / sor / reddet.
* ``AuditLog``        her işlemi ``audit.jsonl`` dosyasına yazar.
* ``UndoStore``       değişiklik öncesi yedek alır, ``geri al`` komutunu mümkün kılar.
* ``KillSwitch``      Durdur düğmesinin çalışan işlemleri kesmesini sağlar.

Erişim modundan bağımsız kural: *yasak* işlemler hiçbir modda yapılmaz.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse


# --------------------------------------------------------------------------- risk
class Risk(IntEnum):
    READ = 0        # okuma / listeleme
    CREATE = 1      # yeni şey oluşturma, uygulama açma
    MODIFY = 2      # var olanı değiştirme, taşıma, geri alma
    DANGEROUS = 3   # silme, komut çalıştırma
    FORBIDDEN = 9   # hiçbir modda yapılmaz


RISK_LABELS = {
    Risk.READ: "okuma",
    Risk.CREATE: "oluşturma",
    Risk.MODIFY: "değiştirme",
    Risk.DANGEROUS: "riskli işlem",
    Risk.FORBIDDEN: "yasak",
}


class AccessMode:
    APPROVAL = "approval"   # Seviye 2 ve 3 onay ister (varsayılan)
    STEP = "step"           # Okuma dışındaki her adım onay ister
    FULL = "full"           # sormadan yapar; yasaklar, günlük, yedek ve durdurma açık kalır
    ALL = (APPROVAL, STEP, FULL)

    @staticmethod
    def normalize(value: Any) -> str:
        return value if value in AccessMode.ALL else AccessMode.APPROVAL


class SafetyError(ValueError):
    """Güvenlik kuralı nedeniyle yapılamayan işlem. Mesaj kullanıcıya gösterilir."""


class PathNotAllowed(SafetyError):
    pass


class ActionForbidden(SafetyError):
    pass


class BackupError(SafetyError):
    pass


# ------------------------------------------------------------------ path helpers
DESKTOP_GUID = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
DOCUMENTS_GUID = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"


def _windows_known_folder(guid: str) -> Optional[Path]:
    """Windows'ta Masaüstü/Belgeler'in gerçek yerini bulur (OneDrive'a taşınmış olabilir)."""
    try:
        import ctypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        parsed = uuid.UUID(guid)
        folder_id = _GUID(
            parsed.time_low,
            parsed.time_mid,
            parsed.time_hi_version,
            (ctypes.c_ubyte * 8).from_buffer_copy(parsed.bytes[8:]),
        )
        buffer = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(buffer)) != 0:
            return None
        try:
            return Path(buffer.value) if buffer.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buffer)
    except Exception:  # noqa: BLE001 - Windows dışı veya erişilemezse varsayılana dön
        return None


def default_roots(home: Optional[Path] = None) -> List[Path]:
    """Varsayılan çalışma alanı: Masaüstü ve Belgeler."""
    home = home or Path.home()
    roots: List[Path] = []
    for guid, name in ((DESKTOP_GUID, "Desktop"), (DOCUMENTS_GUID, "Documents")):
        known = _windows_known_folder(guid) if os.name == "nt" else None
        for candidate in (known, home / name):
            if candidate and candidate not in roots:
                roots.append(candidate)
    return roots


def _norm(path: Path) -> str:
    return os.path.normcase(str(path))


def is_within(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # farklı sürücüler
        return False


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class PathPolicy:
    """Dosya yollarının güvenli olup olmadığını denetler."""

    SECRET_PATTERNS = (
        ".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.ppk", "*.kdbx",
        "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
    )
    SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".azure"}
    EXECUTABLE_EXTENSIONS = {
        ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe",
        ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg", ".jar", ".cpl", ".dll", ".sys",
        ".pif", ".url", ".appref-ms", ".py", ".pyw",
    }

    def __init__(
        self,
        roots: Iterable[Path],
        forbidden: Iterable[Tuple[Path, str]] = (),
        base: Optional[Path] = None,
    ) -> None:
        self.roots: List[Path] = [self._real(Path(r)) for r in roots]
        self.forbidden: List[Tuple[Path, str]] = [(self._real(Path(p)), label) for p, label in forbidden]
        self.base = self._real(Path(base)) if base else (self.roots[0] if self.roots else Path.home())

    @staticmethod
    def _real(path: Path) -> Path:
        return Path(os.path.realpath(path))

    # -- doğrulama -----------------------------------------------------------
    def resolve(self, raw: Any) -> Path:
        text = str(raw)
        if not text.strip() or "\x00" in text:
            raise PathNotAllowed("Geçersiz veya boş dosya yolu.")
        if text.startswith(("\\\\", "//")):
            raise PathNotAllowed("Ağ yolları (\\\\sunucu\\paylaşım) kullanılamaz.")
        remainder = text[2:] if len(text) > 1 and text[1] == ":" else text
        if ":" in remainder:
            raise PathNotAllowed("Dosya adında ':' kullanılamaz (alternatif veri akışı engellendi).")
        candidate = Path(text).expanduser()
        for part in (candidate.parts[1:] if candidate.anchor else candidate.parts):
            if part.endswith((".", " ")) and part not in (".", ".."):
                raise PathNotAllowed(f"Sonu nokta veya boşlukla biten ad kullanılamaz: {part!r}")
            if part.split(".")[0].upper() in _RESERVED:
                raise PathNotAllowed(f"Windows'ta ayrılmış ad kullanılamaz: {part}")
        if not candidate.is_absolute():
            candidate = self.base / candidate
        real = self._real(candidate)

        for zone, label in self.forbidden:
            if is_within(real, zone):
                raise PathNotAllowed(f"Bu konum korunuyor ({label}): {real}")
        if not any(is_within(real, root) for root in self.roots):
            names = ", ".join(str(r) for r in self.roots)
            raise PathNotAllowed(f"Güvenlik nedeniyle yalnızca izinli klasörler kullanılabilir ({names}).")
        if any(part.lower() in self.SECRET_DIRS for part in real.parts):
            raise PathNotAllowed(f"Anahtar/kimlik klasörlerine erişim yasak: {real}")
        lowered = real.name.lower()
        if any(fnmatch.fnmatch(lowered, pattern) for pattern in self.SECRET_PATTERNS):
            raise PathNotAllowed(f"Anahtar veya gizli bilgi dosyalarına erişim yasak: {real.name}")
        return real

    def is_protected_root(self, path: Path) -> bool:
        """İzinli kök klasörün kendisi veya bir sürücü kökü silinemez/taşınamaz."""
        return path.parent == path or any(_norm(path) == _norm(root) for root in self.roots)

    def is_executable(self, path: Path) -> bool:
        return path.suffix.lower() in self.EXECUTABLE_EXTENSIONS


# -------------------------------------------------------------------------- gate
@dataclass(frozen=True)
class Decision:
    action: str   # "allow" | "ask" | "deny"
    reason: str = ""


class PermissionGate:
    """Risk seviyesi ve erişim modundan izin kararı üretir."""

    def __init__(
        self,
        mode: Callable[[], str] = lambda: AccessMode.APPROVAL,
        strict_after_web: Callable[[], bool] = lambda: True,
    ) -> None:
        self._mode = mode
        self._strict_after_web = strict_after_web

    def decide(self, risk: Risk, tainted: bool = False) -> Decision:
        """``tainted``: model internetten okunan (güvenilmez) içerik gördü mü."""
        if risk >= Risk.FORBIDDEN:
            return Decision("deny", "Bu işlem yasak listesinde.")
        mode = AccessMode.normalize(self._mode())
        web_note = "İnternetten okunan içerikten sonra gelen işlem için onay gerekli."
        if mode == AccessMode.FULL:
            if risk >= Risk.MODIFY and tainted and self._strict_after_web():
                return Decision("ask", web_note)
            return Decision("allow")
        if risk == Risk.READ:
            return Decision("allow")
        if mode == AccessMode.STEP:
            reason = "Adım adım modu: her oluşturma/değiştirme/riskli işlem için onay gerekli."
            return Decision("ask", reason)
        if tainted:
            return Decision("ask", web_note)
        if risk == Risk.CREATE:
            return Decision("allow")
        if risk == Risk.MODIFY:
            return Decision("ask", "Var olan dosya veya klasör değişecek.")
        return Decision("ask", "Riskli işlem (silme / komut).")


# ------------------------------------------------------------------------- audit
class AuditLog:
    """Her eylemi satır satır JSON olarak kaydeder (içerik değil, yalnızca özet)."""

    SENSITIVE = ("key", "token", "password", "secret", "authorization")

    def __init__(self, path: Path, max_bytes: int = 2_000_000) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    @classmethod
    def _clean(cls, value: Any, key: str = "") -> Any:
        lowered = key.lower()
        if any(word in lowered for word in cls.SENSITIVE):
            return "***"
        if key == "content":
            return {"chars": len(str(value))}
        if isinstance(value, dict):
            return {str(k): cls._clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._clean(v) for v in value][:50]
        if isinstance(value, str):
            return value if len(value) <= 300 else value[:300] + "…"
        return value

    def record(self, tool: str, args: Optional[dict] = None, risk: Any = None, **extra: Any) -> None:
        entry: Dict[str, Any] = {"ts": datetime.now().isoformat(timespec="seconds"), "tool": tool}
        if args is not None:
            entry["args"] = self._clean(args)
        if risk is not None:
            entry["risk"] = int(risk)
        for key, value in extra.items():
            entry[key] = self._clean(value, key)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    os.replace(self.path, self.path.with_suffix(".1.jsonl"))
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # günlük hatası hiçbir zaman asıl işlemi bozmamalı

    def tail(self, count: int = 20) -> List[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-count:]
        except OSError:
            return []
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        return entries


# --------------------------------------------------------------------------- undo
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class UndoResult:
    ok: bool
    messages: List[str] = field(default_factory=list)
    label: Optional[str] = None


_order_lock = threading.Lock()
_last_order = 0


def _next_order() -> int:
    """Her çağrıda öncekinden büyük bir sayı verir: 'son işlem' asla karışmaz."""
    global _last_order
    with _order_lock:
        _last_order = max(time.time_ns(), _last_order + 1)
        return _last_order


class ChangeSet:
    """Tek bir eylemin geri alınabilir kaydı."""

    MAX_TREE_FILES = 2000

    def __init__(self, store: "UndoStore", label: str) -> None:
        self.store = store
        self.label = label
        self.dir = store.root / f"{_next_order():020d}-{uuid.uuid4().hex[:6]}"
        self.entries: List[dict] = []

    def backup(self, path: Path) -> None:
        """Var olan dosyanın değişmeden önceki halini saklar."""
        path = Path(path)
        if path.is_dir():
            raise BackupError("Klasörler bu araçla yedeklenip değiştirilemez.")
        size = path.stat().st_size
        if size > self.store.max_backup_bytes:
            raise BackupError(
                f"Dosya yedeklenemeyecek kadar büyük ({size // 1_000_000} MB); değiştirilmedi: {path.name}"
            )
        target = self.dir / "files" / str(len(self.entries)) / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        self.entries.append({"kind": "modified", "path": str(path), "backup": str(target.relative_to(self.dir))})

    def created(self, path: Path) -> None:
        """Eylemin oluşturduğu dosya/klasörü kaydeder (içerik özetiyle)."""
        path = Path(path)
        entry: Dict[str, Any] = {"kind": "created", "path": str(path), "is_dir": path.is_dir()}
        if path.is_dir():
            files: Dict[str, str] = {}
            for index, child in enumerate(sorted(p for p in path.rglob("*") if p.is_file())):
                if index >= self.MAX_TREE_FILES:
                    entry["truncated"] = True
                    break
                files[str(child.relative_to(path))] = _sha256(child)
            entry["files"] = files
        elif path.exists():
            entry["sha256"] = _sha256(path)
        self.entries.append(entry)

    def moved(self, source: Path, destination: Path) -> None:
        self.entries.append({"kind": "moved", "src": str(source), "dst": str(destination)})

    def trashed(self, path: Path) -> None:
        self.entries.append({"kind": "trashed", "path": str(path)})

    def discard(self) -> None:
        """İşlem yarıda kaldıysa yetim yedek klasörünü temizler."""
        shutil.rmtree(self.dir, ignore_errors=True)

    def commit(self) -> None:
        if not self.entries:
            self.discard()
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = {"label": self.label, "created": datetime.now().isoformat(timespec="seconds"), "entries": self.entries}
        (self.dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        self.store._prune()


class UndoStore:
    def __init__(
        self,
        root: Path,
        max_sets: int = 50,
        max_total_bytes: int = 500_000_000,
        max_backup_bytes: int = 100_000_000,
    ) -> None:
        self.root = Path(root)
        self.max_sets = max_sets
        self.max_total_bytes = max_total_bytes
        self.max_backup_bytes = max_backup_bytes
        self._lock = threading.Lock()

    def begin(self, label: str) -> ChangeSet:
        return ChangeSet(self, label)

    def _sets(self) -> List[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_dir() and (p / "manifest.json").exists())

    @staticmethod
    def _size(directory: Path) -> int:
        return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())

    def _prune(self) -> None:
        with self._lock:
            sets = self._sets()
            while len(sets) > self.max_sets:
                shutil.rmtree(sets.pop(0), ignore_errors=True)
            while len(sets) > 1 and sum(self._size(s) for s in sets) > self.max_total_bytes:
                shutil.rmtree(sets.pop(0), ignore_errors=True)

    def last_label(self) -> Optional[str]:
        sets = self._sets()
        if not sets:
            return None
        return json.loads((sets[-1] / "manifest.json").read_text(encoding="utf-8")).get("label")

    def undo_last(self) -> UndoResult:
        with self._lock:
            sets = self._sets()
            if not sets:
                return UndoResult(False, ["Geri alınacak bir işlem yok."])
            latest = sets[-1]
            manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
            messages: List[str] = []
            ok = True
            for entry in reversed(manifest["entries"]):
                try:
                    messages.append(self._undo_entry(latest, entry))
                except Exception as error:  # noqa: BLE001
                    ok = False
                    messages.append(f"⚠️ Geri alınamadı ({entry.get('path') or entry.get('dst')}): {error}")
            shutil.rmtree(latest, ignore_errors=True)
            return UndoResult(ok, messages, manifest.get("label"))

    @staticmethod
    def _undo_entry(set_dir: Path, entry: dict) -> str:
        kind = entry["kind"]
        if kind == "modified":
            target = Path(entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(set_dir / entry["backup"], target)
            return f"↩️ Eski haline döndürüldü: {target}"
        if kind == "created":
            target = Path(entry["path"])
            if not target.exists():
                return f"ℹ️ Zaten yok: {target}"
            if not entry.get("is_dir"):
                if _sha256(target) != entry.get("sha256"):
                    return f"⚠️ Oluşturulduktan sonra değiştirilmiş, silinmedi: {target}"
                target.unlink()
                return f"↩️ Oluşturulan dosya silindi: {target}"
            if entry.get("truncated"):
                return f"⚠️ Çok fazla dosya içeriyor, elle silmelisin: {target}"
            left = 0
            for rel, digest in entry.get("files", {}).items():
                child = target / rel
                if child.is_file() and _sha256(child) == digest:
                    child.unlink()
                elif child.exists():
                    left += 1
            for folder in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
                try:
                    folder.rmdir()
                except OSError:
                    pass
            try:
                target.rmdir()
            except OSError:
                left += 1
            if left:
                return f"⚠️ Sonradan eklenen/değişen dosyalar korundu, klasör silinmedi: {target}"
            return f"↩️ Oluşturulan klasör silindi: {target}"
        if kind == "moved":
            source, destination = Path(entry["src"]), Path(entry["dst"])
            if not destination.exists():
                return f"ℹ️ Taşınan öğe artık orada değil: {destination}"
            if source.exists():
                return f"⚠️ Eski konumda başka bir öğe var, geri taşınmadı: {source}"
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))
            return f"↩️ Geri taşındı: {source}"
        if kind == "trashed":
            return f"ℹ️ Geri Dönüşüm Kutusu'ndan elle geri yükle: {entry['path']}"
        return f"ℹ️ Bilinmeyen kayıt türü atlandı: {kind}"


# ------------------------------------------------------------------- kill switch
class KillSwitch:
    """Durdur düğmesi: çalışan çok adımlı işlemleri bir sonraki adımda keser."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()


KILL_SWITCH = KillSwitch()


# ----------------------------------------------------------------- classification
_PATH_KEYS = {
    "create_folder": ("path",),
    "write_text_file": ("path",),
    "append_text_file": ("path",),
    "copy_path": ("source", "destination"),
    "move_path": ("source", "destination"),
    "delete_path": ("path",),
    "open_path": ("path",),
    "inspect_image": ("path",),
    "save_file_dialog": ("path",),
    "list_project_files": ("path",),
    "read_project_file": ("path",),
    "search_project": ("path",),
    "preview_edit": ("path",),
    "replace_exact": ("path",),
    "download_file": ("path",),
    "run_tests": ("working_directory",),
    "prepare_git_branch": ("working_directory",),
    "diff_self_workspace": ("path",),
    "apply_self_changes": ("path",),
}


def classify_action(action_type: str, arguments: dict, policy: PathPolicy) -> Risk:
    """Eylemin risk seviyesini belirler. Güvenlik kuralını çiğneyen eylemde SafetyError fırlatır."""
    paths: Dict[str, Path] = {}
    for key in _PATH_KEYS.get(action_type, ()):
        if key not in arguments:
            raise ActionForbidden(f"'{action_type}' için '{key}' bilgisi eksik.")
        paths[key] = policy.resolve(arguments[key])

    if action_type == "create_folder":
        return Risk.CREATE
    if action_type == "write_text_file":
        path = paths["path"]
        return Risk.MODIFY if path.exists() and arguments.get("overwrite") else Risk.CREATE
    if action_type == "append_text_file":
        return Risk.MODIFY if paths["path"].exists() else Risk.CREATE
    if action_type == "save_file_dialog":
        return Risk.MODIFY if paths["path"].exists() else Risk.CREATE
    if action_type == "copy_path":
        return Risk.CREATE
    if action_type == "move_path":
        if policy.is_protected_root(paths["source"]):
            raise ActionForbidden(f"İzinli kök klasör taşınamaz: {paths['source']}")
        return Risk.MODIFY
    if action_type == "delete_path":
        if policy.is_protected_root(paths["path"]):
            raise ActionForbidden(f"İzinli kök klasör silinemez: {paths['path']}")
        return Risk.DANGEROUS
    if action_type == "open_path":
        if policy.is_executable(paths["path"]):
            raise ActionForbidden(
                f"Çalıştırılabilir veya betik dosyaları bu araçla açılamaz ({paths['path'].suffix})."
            )
        return Risk.CREATE
    if action_type == "browser_open":
        if not re.match(r"^https?://\S+$", str(arguments.get("url") or "").strip(), re.IGNORECASE):
            raise ActionForbidden("browser_open yalnızca http/https adresi kabul eder.")
        return Risk.CREATE
    if action_type == "youtube":
        action = str(arguments.get("action") or "open").strip().casefold()
        if action in {"latest", "highlights"}:
            channel = str(
                arguments.get("channel") or arguments.get("query") or arguments.get("url") or ""
            ).strip()
            if not channel:
                raise ActionForbidden("YouTube akışı için channel veya query gerekli.")
            if len(channel) > 300:
                raise ActionForbidden("YouTube kanal bilgisi çok uzun.")
            if "://" in channel:
                parsed = urlparse(channel)
                if (parsed.hostname or "").casefold() not in {
                    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                    "youtu.be", "www.youtu.be",
                }:
                    raise ActionForbidden("YouTube kanalı için yalnızca youtube.com bağlantısı kullanılabilir.")
            return Risk.READ
        if action != "open":
            raise ActionForbidden("YouTube için action: open, latest veya highlights olmalı.")
        url = str(arguments.get("url") or "").strip()
        query = str(arguments.get("query") or "").strip()
        if not url and not query:
            raise ActionForbidden("YouTube için query veya url gerekli.")
        if url:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").casefold() not in {
                "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                "youtu.be", "www.youtu.be",
            }:
                raise ActionForbidden("youtube yalnızca youtube.com veya youtu.be bağlantısı kabul eder.")
        return Risk.CREATE
    if action_type in {"open_application", "open_web_page", "search_web", "create_project", "prepare_self_workspace"}:
        return Risk.CREATE
    if action_type == "screen_wait":
        if not str(arguments.get("text") or "").strip() and not str(arguments.get("window_title") or "").strip():
            raise ActionForbidden("screen_wait için text veya window_title gerekli.")
        if len(str(arguments.get("text") or "")) > 200:
            raise ActionForbidden("screen_wait metni en fazla 200 karakter olabilir.")
        return Risk.READ
    if action_type in {"inspect_ui", "list_project_files", "read_project_file", "search_project", "preview_edit", "diff_self_workspace", "capture_screen", "inspect_image", "screen_map", "check_capabilities", "read_page", "read_rss", "browser_read", "news", "markets", "wiki", "weather", "github", "photo"}:
        return Risk.READ
    if action_type == "memory":
        action = str(arguments.get("action", "list")).strip().casefold()
        if action not in {"list", "add", "delete"}:
            raise ActionForbidden("memory için action: list, add veya delete olmalı.")
        if action == "list":
            return Risk.READ
        return Risk.CREATE if action == "add" else Risk.MODIFY
    if action_type == "skill":
        action = str(arguments.get("action", "list")).strip().casefold()
        if action not in {"list", "read", "create"}:
            raise ActionForbidden("skill için action: list, read veya create olmalı.")
        if action in {"list", "read"}:
            return Risk.READ
        # Kalıcı talimat dosyası gelecekteki ajan davranışını etkiler: her zaman onay ister.
        return Risk.MODIFY
    if action_type in {"replace_exact", "prepare_git_branch"}:
        return Risk.MODIFY
    if action_type == "download_file":
        return Risk.MODIFY if paths["path"].exists() and arguments.get("overwrite") else Risk.CREATE
    if action_type == "steam_workshop":
        action = str(arguments.get("action", "")).strip().casefold()
        if action not in {"search", "open", "status"}:
            raise ActionForbidden("steam_workshop için geçerli bir action gerekli.")
        return Risk.DANGEROUS if action == "open" else Risk.READ
    if action_type == "home":
        action = str(arguments.get("action", "")).strip().casefold()
        if action not in {"status", "light", "all_lights", "coffee_brew", "coffee_status", "coffee_cancel"}:
            raise ActionForbidden("home için geçerli bir action gerekli.")
        if action in {"status", "coffee_status"}:
            return Risk.READ
        return Risk.CREATE
    if action_type == "map":
        action = str(arguments.get("action", "")).strip().casefold()
        if action not in {"home", "set_home", "route", "nearby", "mall", "bus_stops", "bus_stop", "bus_line", "geocode"}:
            raise ActionForbidden("map için geçerli bir action gerekli.")
        return Risk.MODIFY if action == "set_home" else Risk.READ
    if action_type == "book":
        action = str(arguments.get("action", "lookup")).strip().casefold()
        if action not in {"open_camera", "lookup", "library", "profile"}:
            raise ActionForbidden("book için action: open_camera, lookup, library veya profile olmalı.")
        return Risk.READ
    if action_type == "personal":
        resource = str(arguments.get("resource", "")).strip().casefold()
        action = str(arguments.get("action", "list")).strip().casefold()
        if resource not in {"note", "reminder", "event"}:
            raise ActionForbidden("personal için resource: note, reminder veya event olmalı.")
        if action not in {"list", "add", "delete", "complete"}:
            raise ActionForbidden("personal için geçerli bir action gerekli.")
        if action == "complete" and resource != "reminder":
            raise ActionForbidden("Yalnızca hatırlatıcı tamamlanabilir.")
        if action == "list":
            return Risk.READ
        return Risk.CREATE if action == "add" else Risk.MODIFY
    if action_type == "system_control":
        action = str(arguments.get("action", "")).strip().casefold()
        if action not in {
            "volume_up", "volume_down", "mute", "media_play_pause", "media_next",
            "media_previous", "media_stop", "lock", "sleep", "shutdown", "restart",
            "cancel_shutdown",
        }:
            raise ActionForbidden("system_control için geçerli bir action gerekli.")
        if action in {"shutdown", "restart"}:
            if not arguments.get("confirm"):
                raise ActionForbidden("Kapatma veya yeniden başlatma için confirm=true gerekli.")
            return Risk.DANGEROUS
        if action in {"sleep", "lock"}:
            return Risk.MODIFY
        return Risk.CREATE
    if action_type == "clipboard":
        action = str(arguments.get("action", "read")).strip().casefold()
        if action not in {"read", "write", "clear"}:
            raise ActionForbidden("clipboard için action: read, write veya clear olmalı.")
        return Risk.READ if action == "read" else Risk.CREATE
    if action_type == "window_control":
        action = str(arguments.get("action", "list")).strip().casefold()
        aliases = {
            "list_windows": "list",
            "focus_window": "focus",
            "close_app": "close",
        }
        action = aliases.get(action, action)
        if action not in {
            "list", "focus", "minimize", "maximize", "restore",
            "move", "resize", "set_bounds", "close",
        }:
            raise ActionForbidden(
                "window_control action: list, focus, minimize, maximize, restore, move, resize, "
                "set_bounds veya close olmalı."
            )
        if action == "list":
            return Risk.READ
        if not str(arguments.get("title") or "").strip() and arguments.get("handle") in (None, ""):
            raise ActionForbidden("window_control için title veya handle gerekli.")
        if action == "close":
            return Risk.DANGEROUS
        return Risk.MODIFY
    if action_type == "find_text_on_screen":
        return Risk.DANGEROUS if arguments.get("click") else Risk.READ
    if action_type == "screen_act":
        ref = str(arguments.get("ref") or "").strip()
        if ref:
            if not re.match(r"^s\d+:e\d+$", ref):
                raise ActionForbidden("screen_act ref biçimi sN:eM olmalı.")
        else:
            try:
                int(arguments.get("element_id"))
            except (TypeError, ValueError):
                raise ActionForbidden("screen_act için ref veya geçerli bir element_id gerekli.")
        if str(arguments.get("action", "click")).strip().casefold() not in {
            "click", "double_click", "right_click", "type", "focus", "set_value", "clear",
            "toggle", "check", "uncheck", "expand", "collapse", "select", "scroll",
            "scroll_to", "hover", "drag",
        }:
            raise ActionForbidden(
                "screen_act action: click, double_click, right_click, type, focus, set_value, clear, "
                "toggle, check, uncheck, expand, collapse, select, scroll, scroll_to, hover veya drag olmalı."
            )
        if str(arguments.get("action") or "").strip().casefold() == "drag":
            if not str(arguments.get("to_ref") or "").strip() and (
                arguments.get("to_x") is None or arguments.get("to_y") is None
            ):
                raise ActionForbidden("drag için to_ref veya to_x/to_y gerekli.")
            if str(arguments.get("to_ref") or "").strip() and not re.match(
                r"^s\d+:e\d+$", str(arguments["to_ref"]).strip()
            ):
                raise ActionForbidden("to_ref biçimi sN:eM olmalı.")
        if len(str(arguments.get("text") or "")) > 10_000:
            raise ActionForbidden("screen_act metni çok uzun.")
        if len(str(arguments.get("expect") or "")) > 200 or len(str(arguments.get("expect_window") or "")) > 200:
            raise ActionForbidden("screen_act doğrulama metni çok uzun.")
        return Risk.DANGEROUS
    if action_type == "ui_control":
        action = str(arguments.get("action", "click")).strip().casefold()
        if action not in {
            "click", "type", "focus", "set_value", "clear", "toggle", "check", "uncheck",
            "expand", "collapse", "select", "scroll", "scroll_to", "hover",
        }:
            raise ActionForbidden("ui_control için geçerli bir action gerekli.")
        if len(str(arguments.get("text") or "")) > 10_000:
            raise ActionForbidden("ui_control metni çok uzun.")
        return Risk.DANGEROUS
    if action_type in {"send_keys", "run_command", "run_tests", "apply_self_changes", "mouse_control", "draw_shape", "install_dependency", "open_system_uri"}:
        return Risk.DANGEROUS
    if action_type == "undo_last":
        return Risk.MODIFY
    raise ActionForbidden(f"Tanınmayan işlem türü: {action_type}")


@dataclass
class PlanAssessment:
    decision: str                      # "allow" | "ask" | "deny"
    risk: Risk = Risk.READ
    reasons: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------- services
@dataclass
class SafetyServices:
    policy: PathPolicy
    gate: PermissionGate
    audit: AuditLog
    undo: UndoStore
    kill: KillSwitch
    approval: Optional[Callable[[str], bool]] = None
    settings: Any = None


def _app_roots() -> List[Path]:
    roots = [Path(__file__).resolve().parents[2]]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    return roots


def build_services(settings: Any, kill: Optional[KillSwitch] = None) -> SafetyServices:
    """Ayarlardan güvenlik servislerini kurar; mod ayarı her kararda canlı okunur."""
    data_dir = Path(settings.data_dir)
    forbidden: List[Tuple[Path, str]] = [(root, "Jarvis'in kendi kodu") for root in _app_roots()]
    forbidden.append((data_dir, "Jarvis ayarları, yedekleri ve günlükleri"))
    for env_name, label in (
        ("WINDIR", "Windows sistem klasörü"),
        ("ProgramFiles", "Program Files"),
        ("ProgramFiles(x86)", "Program Files (x86)"),
        ("ProgramData", "ProgramData"),
    ):
        value = os.environ.get(env_name)
        if value:
            forbidden.append((Path(value), label))
    forbidden.append((Path.home() / "AppData", "Uygulama verileri (AppData)"))

    roots = default_roots() + [Path(p) for p in settings.extra_roots]
    return SafetyServices(
        policy=PathPolicy(roots, forbidden, base=default_roots()[0]),
        gate=PermissionGate(lambda: settings.access_mode, lambda: settings.strict_after_web),
        audit=AuditLog(data_dir / "logs" / "audit.jsonl"),
        undo=UndoStore(data_dir / "undo"),
        kill=kill or KILL_SWITCH,
        settings=settings,
    )
