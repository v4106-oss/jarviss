from __future__ import annotations

import json
import os
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from app.core.safety import ActionForbidden
from app.core.web_tools import USER_AGENT, WebReader

BROWSE_URL = "https://steamcommunity.com/workshop/browse/"
DETAILS_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
ITEM_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={}"
MAX_ITEMS = 30
SORTS = {
    "relevance": "textsearch",
    "trend": "trend",
    "new": "mostrecent",
    "top": "toprated",
    "subscribed": "mostsubscribed",
}
_VDF_TOKEN = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')


class _WorkshopSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.order: List[str] = []
        self.titles: Dict[str, str] = {}
        self._anchors: List[Optional[str]] = []
        self._title_parts: List[str] = []
        self._in_title = False

    def _current(self) -> Optional[str]:
        return next((item for item in reversed(self._anchors) if item), None)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a":
            href = values.get("href", "")
            match = re.search(r"filedetails/\?id=(\d+)", href)
            item_id = match.group(1) if match else None
            self._anchors.append(item_id)
            if item_id and item_id not in self.order:
                self.order.append(item_id)
        elif tag == "img":
            item_id = self._current()
            alt = (values.get("alt") or "").strip()
            if item_id and alt and item_id not in self.titles:
                self.titles[item_id] = alt[:300]
        elif tag == "div" and "workshopItemTitle" in (values.get("class") or ""):
            if self._current():
                self._in_title = True
                self._title_parts = []

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._anchors:
            self._anchors.pop()
        elif tag == "div" and self._in_title:
            item_id = self._current()
            title = " ".join("".join(self._title_parts).split())
            if item_id and title:
                self.titles.setdefault(item_id, title[:300])
            self._in_title = False


def _iso_time(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def _parse_vdf(text: str) -> Dict[str, Any]:
    """Valve KeyValues biçimini (ACF/VDF) küçük bir ayrıştırıcıyla okur."""
    tokens: List[str] = []
    for match in _VDF_TOKEN.finditer(text):
        if match.group(2):
            tokens.append(match.group(2))
        else:
            tokens.append(match.group(1).replace("\\\\", "\\"))
    root: Dict[str, Any] = {}
    stack: List[Dict[str, Any]] = [root]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "}":
            if len(stack) > 1:
                stack.pop()
            index += 1
        elif token == "{":
            index += 1
        elif index + 1 < len(tokens) and tokens[index + 1] == "{":
            node: Dict[str, Any] = {}
            stack[-1][token] = node
            stack.append(node)
            index += 2
        elif index + 1 < len(tokens):
            stack[-1][token] = tokens[index + 1]
            index += 2
        else:
            break
    return root


def _dir_stats(path: Path) -> Dict[str, Any]:
    files = 0
    total = 0
    truncated = False
    try:
        for current, _, names in os.walk(path):
            for name in names:
                try:
                    total += (Path(current) / name).stat().st_size
                except OSError:
                    continue
                files += 1
                if files >= 5000:
                    truncated = True
                    break
            if truncated:
                break
    except OSError:
        pass
    return {"file_count": files, "bytes": total, "truncated": truncated}


class SteamWorkshop:
    def __init__(self) -> None:
        self.reader = WebReader()
        self._libraries: Optional[List[Path]] = None

    @staticmethod
    def _number(value: Any, label: str) -> str:
        text = str(value if value is not None else "").strip()
        if not re.fullmatch(r"\d{1,20}", text):
            raise ActionForbidden(f"{label} sayısal bir kimlik olmalı.")
        return text

    def _remote_details(self, item_ids: List[str]) -> Dict[str, dict]:
        data = {"itemcount": str(len(item_ids))}
        for index, item_id in enumerate(item_ids):
            data[f"publishedfileids[{index}]"] = item_id
        try:
            response = requests.post(
                DETAILS_URL,
                data=data,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=(5, 25),
            )
        except requests.RequestException as error:
            raise ActionForbidden(f"Steam Workshop ayrıntıları alınamadı: {error}")
        if not response.ok:
            raise ActionForbidden(f"Steam Workshop ayrıntıları alınamadı (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except ValueError:
            raise ActionForbidden("Steam Workshop yanıtı çözümlenemedi.")
        details = (payload.get("response") or {}).get("publishedfiledetails") or []
        return {
            str(item.get("publishedfileid")): item
            for item in details
            if item.get("publishedfileid") is not None
        }

    @staticmethod
    def _summary(item_id: str, detail: Optional[dict], fallback_title: str) -> Dict[str, Any]:
        item = {
            "id": item_id,
            "title": fallback_title or "(başlık alınamadı)",
            "url": ITEM_URL.format(item_id),
        }
        if not detail:
            item["details"] = "alınamadı"
            return item
        tags = [str(tag.get("tag")) for tag in detail.get("tags") or [] if tag.get("tag")]
        item.update({
            "title": detail.get("title") or item["title"],
            "size_bytes": int(detail.get("file_size") or 0),
            "subscriptions": int(detail.get("subscriptions") or 0),
            "favorited": int(detail.get("favorited") or 0),
            "views": int(detail.get("views") or 0),
            "updated": _iso_time(detail.get("time_updated")),
            "tags": tags[:8],
            "visibility": detail.get("visibility"),
            "banned": bool(detail.get("banned")),
        })
        return item

    def search(self, args: dict) -> str:
        app_id = self._number(args.get("app_id"), "app_id")
        query = str(args.get("query") or "").strip()[:120]
        try:
            page = int(args.get("page", 1) or 1)
        except (TypeError, ValueError):
            page = 1
        page = min(max(page, 1), 20)
        requested_sort = str(args.get("sort") or "relevance").strip().casefold()
        if requested_sort not in SORTS:
            requested_sort = "relevance"
        browse_sort = SORTS[requested_sort]
        if not query and browse_sort == "textsearch":
            browse_sort = "trend"
        params = {
            "appid": app_id,
            "browsesort": browse_sort,
            "actualsort": browse_sort,
            "section": "readytouseitems",
            "p": page,
        }
        if query:
            params["searchtext"] = query
        url = f"{BROWSE_URL}?{urlencode(params)}"
        raw, _, encoding, final_url = self.reader._get(url, "text/html")
        parser = _WorkshopSearchParser()
        parser.feed(raw.decode(encoding, "replace"))
        item_ids = parser.order[:MAX_ITEMS]
        if not item_ids:
            return json.dumps({
                "app_id": app_id,
                "query": query,
                "page": page,
                "count": 0,
                "items": [],
                "note": "Sonuç bulunamadı veya Steam sayfası beklenen biçimde gelmedi.",
                "source": final_url,
            }, ensure_ascii=False, indent=1)
        details: Dict[str, dict] = {}
        warning = ""
        try:
            details = self._remote_details(item_ids)
        except ActionForbidden as error:
            warning = str(error)
        items = [self._summary(item_id, details.get(item_id), parser.titles.get(item_id, "")) for item_id in item_ids]
        result = {
            "app_id": app_id,
            "query": query,
            "page": page,
            "sort": requested_sort,
            "count": len(items),
            "items": items,
            "source": final_url,
        }
        if warning:
            result["details_warning"] = warning
        result["note"] = (
            "Arama Steam'in herkese açık Workshop sayfasını kullanır. Yetişkin/yaş sınırlı içerik görünürlüğü "
            "kullanıcının Steam hesap tercihlerine bağlıdır; yaş kapısı veya hesap filtresi atlatılmaz."
        )
        return json.dumps(result, ensure_ascii=False, indent=1)

    def open_item(self, args: dict) -> str:
        item_id = self._number(args.get("item_id"), "item_id")
        uri = f"steam://url/CommunityFilePage/{item_id}"
        try:
            os.startfile(uri)
        except OSError as error:
            raise ActionForbidden(f"Steam bağlantısı açılamadı (Steam kurulu mu?): {error}")
        return (
            f"✅ Workshop sayfası Steam istemcisinde açıldı: {ITEM_URL.format(item_id)}\n"
            "Abonelik gerekiyorsa ekranı capture_screen ile gör, Abone Ol düğmesine mouse_control ile tıkla "
            "ve ardından steam_workshop status ile abonelik/indirme durumunu doğrula."
        )

    def _steam_libraries(self) -> List[Path]:
        if self._libraries is not None:
            return self._libraries
        candidates: List[Path] = []
        try:
            import winreg

            for hive, key_path, value in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        raw = str(winreg.QueryValueEx(key, value)[0]).strip()
                    if raw:
                        candidates.append(Path(raw))
                except OSError:
                    continue
        except ImportError:
            pass
        for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "PROGRAMW6432"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(Path(base) / "Steam")
        candidates.extend(
            [Path("C:/Steam"), Path("D:/Steam"), Path("C:/SteamLibrary"), Path("D:/SteamLibrary")]
        )
        libraries: List[Path] = []
        seen = set()

        def add(path: Path) -> None:
            try:
                resolved = path.resolve()
            except OSError:
                return
            key = str(resolved).casefold()
            if key in seen or not resolved.is_dir():
                return
            seen.add(key)
            libraries.append(resolved)

        for candidate in candidates:
            add(candidate)
            folder_file = candidate / "steamapps" / "libraryfolders.vdf"
            if not folder_file.is_file():
                continue
            try:
                text = folder_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in re.finditer(r'"path"\s*"([^"]+)"', text):
                add(Path(match.group(1).replace("\\\\", "\\")))
        self._libraries = libraries
        return libraries

    @staticmethod
    def _app_state(app_data: dict) -> Dict[str, Any]:
        return {
            "needs_download": str(app_data.get("NeedsDownload", "0")) not in {"0", ""},
            "needs_update": str(app_data.get("NeedsUpdate", "0")) not in {"0", ""},
            "size_on_disk": int(app_data.get("SizeOnDisk") or 0),
            "last_updated": _iso_time(app_data.get("TimeLastUpdated")),
        }

    def status(self, args: dict) -> str:
        app_id = self._number(args.get("app_id"), "app_id")
        item_id = self._number(args.get("item_id"), "item_id")
        include_remote = bool(args.get("include_remote", False))
        libraries = self._steam_libraries()
        if not libraries:
            return json.dumps({
                "app_id": app_id,
                "item_id": item_id,
                "found": False,
                "note": "Steam kütüphanesi bulunamadı; Steam kurulu değil veya kurulum standart dışı bir konumda.",
            }, ensure_ascii=False, indent=1)

        records = []
        subscribed = False
        installed = False
        app_states = []
        for library in libraries:
            acf_path = library / "steamapps" / "workshop" / f"appworkshop_{app_id}.acf"
            item_info: Optional[dict] = None
            app_data: dict = {}
            if acf_path.is_file():
                try:
                    parsed = _parse_vdf(acf_path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    parsed = {}
                app_data = parsed.get("AppWorkshop") or {}
                items = app_data.get("WorkshopItemsInstalled") or {}
                if isinstance(items, dict):
                    raw_info = items.get(item_id)
                    if isinstance(raw_info, dict):
                        item_info = raw_info
                if app_data:
                    app_states.append({"library": str(library), **self._app_state(app_data)})
            content_path = library / "steamapps" / "workshop" / "content" / app_id / item_id
            content_exists = content_path.is_dir()
            if item_info is not None:
                subscribed = True
            if content_exists:
                installed = True
            if item_info is None and not content_exists:
                continue
            record: Dict[str, Any] = {
                "library": str(library),
                "subscribed": item_info is not None,
                "installed": content_exists,
                "acf": str(acf_path) if acf_path.is_file() else "",
                "content_path": str(content_path) if content_exists else "",
            }
            if item_info:
                record.update({
                    "manifest": str(item_info.get("manifest", "")),
                    "size_bytes": int(item_info.get("size", 0) or 0),
                    "updated": _iso_time(item_info.get("timeupdated")),
                })
            if content_exists:
                record.update(_dir_stats(content_path))
            records.append(record)

        if subscribed and installed:
            summary = "Abone ve içerik indirilmiş görünüyor."
        elif subscribed:
            summary = "Abonelik kaydı var; içerik henüz indirilmemiş veya indirme sürüyor."
        elif installed:
            summary = "İçerik klasörü var ama ACF abonelik kaydı yok; Steam istemcisinde doğrula."
        else:
            summary = "Bu öğe bu bilgisayarda abone/kurulu görünmüyor."
        result: Dict[str, Any] = {
            "app_id": app_id,
            "item_id": item_id,
            "found": bool(records),
            "subscribed": subscribed,
            "installed": installed,
            "summary": summary,
            "libraries": records,
            "app_states": app_states,
            "note": (
                "Steam kapalıyken ACF dosyası güncel olmayabilir. Abonelik/indirme işlemini Steam istemcisinde "
                "tamamlayıp Steam açıkken yeniden denetle."
            ),
        }
        if include_remote:
            try:
                detail = self._remote_details([item_id]).get(item_id)
            except ActionForbidden as error:
                result["remote_warning"] = str(error)
            else:
                if detail:
                    result["remote"] = self._summary(item_id, detail, "")
        return json.dumps(result, ensure_ascii=False, indent=1)

    def run(self, args: dict) -> str:
        action = str(args.get("action") or "").strip().casefold()
        if action == "search":
            return self.search(args)
        if action == "open":
            return self.open_item(args)
        if action == "status":
            return self.status(args)
        raise ActionForbidden("steam_workshop action: search, open veya status olmalı.")
