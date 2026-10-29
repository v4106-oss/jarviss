"""Hava durumunu ajan için kısa metne çevirir."""

from __future__ import annotations

from typing import Any, Dict


def format_weather(data: Dict[str, Any]) -> str:
    if not data:
        raise ValueError("Hava durumu verisi boş.")
    current = data.get("current") or {}
    daily = data.get("daily") or []
    location = str(data.get("location") or "Konum")
    lines = [
        f"✅ Hava · {location} · {current.get('temp', '—')}° · {current.get('text', '—')}"
        + (" · ÖNBELLEK" if data.get("stale") else ""),
        (
            f"Hissedilen {current.get('feels', '—')}° · Nem %{current.get('humidity', '—')} · "
            f"Rüzgar {current.get('wind', '—')} km/s ({current.get('wind_dir', '—')}) · "
            f"Basınç {current.get('pressure', '—')} hPa"
        ),
    ]
    if daily:
        today = daily[0]
        lines.append(
            f"Bugün {today.get('min', '—')}° / {today.get('max', '—')}° · {today.get('text', '—')}"
        )
    if len(daily) > 1:
        tomorrow = daily[1]
        lines.append(
            f"Yarın {tomorrow.get('min', '—')}° / {tomorrow.get('max', '—')}° · "
            f"{tomorrow.get('text', '—')}"
        )
    if len(daily) > 2:
        third = daily[2]
        lines.append(
            f"Öbür gün {third.get('min', '—')}° / {third.get('max', '—')}° · "
            f"{third.get('text', '—')}"
        )
    details = []
    if current.get("uv") is not None:
        details.append(f"UV {current['uv']}")
    if current.get("sunrise") and current.get("sunset"):
        details.append(f"Gün doğumu {current['sunrise']} · Gün batımı {current['sunset']}")
    if details:
        lines.append(" · ".join(details))
    hourly = data.get("hourly") or []
    rainy = [hour for hour in hourly[:6] if int(hour.get("precip") or 0) >= 40]
    if rainy:
        lines.append(f"Önümüzdeki saatlerde yağış olasılığı yüksek (%{rainy[0].get('precip')} civarı).")
    lines.append(f"Güncelleme: {data.get('updated', '—')}")
    if data.get("stale"):
        lines.append("⚠️ Canlı veri alınamadı; son önbellek gösteriliyor.")
    return "\n".join(lines)
