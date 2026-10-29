"""Kullanıcı hakkında kalıcı notlar.

Notlar mevcut SQLite deposunda tutulur ve sistem istemine eklenir. Araç
yalnızca not ekleme, listeleme ve silme yapar; parola/anahtar gibi hassas
bilgiler kullanıcı açıkça istemedikçe kaydedilmemelidir.
"""

from __future__ import annotations

import re
from typing import Optional

from app.core.storage import Storage

MAX_KEY = 80
MAX_VALUE = 2_000
MAX_NOTES = 200


class MemoryTools:
    def __init__(self, storage: Optional[Storage]) -> None:
        self.storage = storage

    def _require(self) -> Storage:
        if self.storage is None:
            raise RuntimeError("Kalıcı hafıza deposu kullanılamıyor.")
        return self.storage

    @staticmethod
    def _key(args: dict) -> str:
        raw = re.sub(r"\s+", " ", str(args.get("key") or "")).strip()
        if not raw:
            value = re.sub(r"\s+", " ", str(args.get("value") or "")).strip()
            raw = value[:50] or "not"
        return raw[:MAX_KEY]

    def run(self, args: dict) -> str:
        storage = self._require()
        action = str(args.get("action") or "list").strip().casefold()
        if action == "list":
            try:
                limit = max(1, min(int(args.get("limit", 50)), MAX_NOTES))
            except (TypeError, ValueError):
                limit = 50
            rows = storage.get_all_memory()[:limit]
            if not rows:
                return "ℹ️ Kalıcı hafızada kayıtlı not yok."
            lines = [f"✅ {len(rows)} kalıcı not:"]
            for row in rows:
                value = str(row.get("value") or "").replace("\n", " ").strip()
                lines.append(f"• {row.get('key')}: {value[:300]}")
            return "\n".join(lines)
        if action == "add":
            value = str(args.get("value") or "").strip()
            if not value:
                raise ValueError("Kaydedilecek not metni boş olamaz.")
            if len(value) > MAX_VALUE:
                raise ValueError(f"Not en fazla {MAX_VALUE} karakter olabilir.")
            existing = storage.get_all_memory()
            key = self._key(args)
            if len(existing) >= MAX_NOTES and not storage.get_memory(key):
                raise ValueError(f"Hafıza sınırı dolu ({MAX_NOTES} not); önce eski bir notu sil.")
            storage.save_memory(key, value)
            return f"✅ Kalıcı hafızaya kaydedildi: {key}"
        if action == "delete":
            key = str(args.get("key") or "").strip()[:MAX_KEY]
            if not key:
                raise ValueError("Silinecek not için key gerekli.")
            if storage.get_memory(key) is None:
                return f"ℹ️ '{key}' anahtarında kayıtlı not bulunamadı."
            storage.delete_memory(key)
            return f"✅ Kalıcı not silindi: {key}"
        raise ValueError("Hafıza için action: list/add/delete olabilir.")
