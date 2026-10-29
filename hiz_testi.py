"""Jarvis hız testi.

Google'a bağlantı süresini ve her Gemini modelinin ilk kelimeyi / tüm cevabı
kaç saniyede verdiğini ölçer; en hızlı modeli Jarvis'e kaydetmeyi önerir.
Çalıştırmak için Hiz_Testi.bat dosyasına çift tıkla.
"""

from __future__ import annotations

import sys
import time
from typing import Callable, Dict, List, Optional

from app.core.ai import AI, model_rank, ranked_models
from app.core.settings import Settings

PROMPT = "Merhaba! Kendini tek cümleyle tanıt."
RUNS_PER_MODEL = 2


class _NoStorage:
    """Test sırasında sohbet veritabanına dokunmamak için boş depo."""

    def get_all_memory(self):
        return []

    def get_conversation_history(self, limit=16):
        return []

    def add_message(self, *args, **kwargs):
        pass


def measure(ai: AI, model: str) -> Dict[str, object]:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "generationConfig": {"maxOutputTokens": 120},
    }
    best: Optional[Dict[str, object]] = None
    for _ in range(RUNS_PER_MODEL):
        ai._cancelled = False
        first: List[float] = []
        start = time.perf_counter()
        try:
            response = ai._post(model, payload, stream=True)
            if not response.ok:
                return {"error": f"HTTP {response.status_code}: {ai._error_text(response)[:70]}"}
            text, ok = ai._read_stream(response, lambda _chunk: first.append(time.perf_counter() - start) if not first else None)
        except Exception as error:  # noqa: BLE001
            return {"error": type(error).__name__}
        total = time.perf_counter() - start
        if not ok:
            return {"error": "yanıt üretmedi"}
        run = {"first": first[0] if first else total, "total": total}
        if best is None or run["total"] < best["total"]:
            best = run
    return best or {"error": "ölçülemedi"}


def candidate_models(names: List[str], current: str) -> List[str]:
    ranked = ranked_models(names)
    flash = [n for n in ranked if model_rank(n)[0] == 2][:3]
    lite = [n for n in ranked if model_rank(n)[0] == 1][:2]
    chosen = flash + lite
    if current and current not in chosen:
        chosen.append(current)
    return chosen


def main(settings: Optional[Settings] = None, input_fn: Callable[[str], str] = input, out: Callable[[str], None] = print) -> int:
    settings = settings or Settings()
    if not settings.api_key:
        out("API anahtarı yok. Önce Jarvis'te Gemini Ayarları'ndan anahtarını gir.")
        return 1
    ai = AI(storage=_NoStorage(), settings=settings)

    out("1) Google'a bağlantı testi")
    start = time.perf_counter()
    names = ai.list_models()
    first_call = time.perf_counter() - start
    start = time.perf_counter()
    ai.list_models()
    second_call = time.perf_counter() - start
    if not names:
        out("   Model listesi alınamadı. İnternet/VPN'ini ve API anahtarını kontrol et.")
        return 1
    out(f"   İlk bağlantı: {first_call:.2f} sn | Aynı bağlantıyla ikinci istek: {second_call:.2f} sn")
    if first_call > 3:
        out("   -> Google'a ilk bağlanma yavaş. Sorun internet/VPN/IPv6 kaynaklı olabilir; VPN'i kapatıp tekrar dene.")
    else:
        out("   -> Ağ bağlantın hızlı.")

    models = candidate_models(names, settings.model)
    out(f"\n2) Model hız testi (her model {RUNS_PER_MODEL} kez denenir, en iyisi yazılır)")
    out(f"   Şu an Jarvis'in modeli: {settings.model}\n")
    out(f"   {'Model':<28}{'İlk kelime':>12}{'Toplam':>10}")
    results: Dict[str, Dict[str, object]] = {}
    for model in models:
        result = measure(ai, model)
        results[model] = result
        label = model + (" *" if model == settings.model else "")
        if "error" in result:
            out(f"   {label:<28}  hata: {result['error']}")
        else:
            out(f"   {label:<28}{result['first']:>10.2f} sn{result['total']:>8.2f} sn")

    working = {m: r for m, r in results.items() if "error" not in r}
    if not working:
        out("\nHiçbir model yanıt vermedi.")
        return 1
    fastest = min(working, key=lambda m: working[m]["total"])
    out(f"\nEn hızlı model: {fastest} ({working[fastest]['total']:.2f} sn)")
    if fastest == settings.model:
        out("Jarvis zaten en hızlı modeli kullanıyor.")
        return 0
    answer = input_fn(f"Jarvis'in modeli '{fastest}' olarak kaydedilsin mi? (e/h): ").strip().lower()
    if answer in ("e", "evet", "y", "yes"):
        settings.set_model(fastest)
        out("Kaydedildi. Jarvis'i yeniden başlat.")
    else:
        out("Model değiştirilmedi.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
