"""Gün içinde nazik, tekrarsız dürtüler üreten proaktif eşlikçi."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, List, Optional

from app.core.hud_data import local_moment

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis" / "data"
STATE_FILE = DATA_DIR / "companion.json"

DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "08:00"
DEFAULT_INTERVAL = 90
AGENDA_GAP_MINUTES = 10
EVENT_LEAD_MINUTES = 45
REMINDER_LEAD_MINUTES = 15
HYDRATION_GAP_MINUTES = 120
SENT_TTL = timedelta(days=7)


@dataclass(frozen=True)
class Nudge:
    kind: str
    text: str
    key: str


def parse_clock(value: Any, default: str = DEFAULT_QUIET_START) -> time:
    for candidate in (str(value or "").strip(), default):
        try:
            hour, minute = candidate.split(":", 1)
            return time(max(0, min(23, int(hour))), max(0, min(59, int(minute))))
        except (AttributeError, TypeError, ValueError):
            continue
    return time(23, 0)


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    current = now.time()
    start_time = parse_clock(start, DEFAULT_QUIET_START)
    end_time = parse_clock(end, DEFAULT_QUIET_END)
    if start_time == end_time:
        return False
    if start_time < end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


def _parse_moment(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _minutes_until(moment: datetime, now: datetime) -> float:
    return (moment - now).total_seconds() / 60.0


class CompanionPlanner:
    """Takvim, hatırlatıcı ve oturum süresinden en fazla bir dürtü seçer."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self.state_path = Path(state_path) if state_path else STATE_FILE
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp.replace(self.state_path)
        except OSError:
            pass

    def _sent_map(self, now: datetime) -> dict:
        raw = self._state.get("sent")
        if not isinstance(raw, dict):
            return {}
        cutoff = now - SENT_TTL
        return {
            str(key): value
            for key, value in raw.items()
            if (_parse_moment(value) or datetime.min) >= cutoff
        }

    def _ensure_water_clock(self, now: datetime) -> None:
        if _parse_moment(self._state.get("last_water")) is not None:
            return
        self._state["last_water"] = now.isoformat(timespec="seconds")
        self._save()

    @staticmethod
    def _agenda(
        events: Iterable[dict],
        reminders: Iterable[dict],
        now: datetime,
        sent: dict,
        event_lead: int,
        reminder_lead: int,
    ):
        candidates: List[tuple[float, Nudge]] = []
        for event in events or []:
            moment = local_moment(event.get("when"))
            if moment is None:
                continue
            minutes = _minutes_until(moment, now)
            if not 0 < minutes <= event_lead:
                continue
            title = " ".join(str(event.get("title") or "Etkinlik").split())[:120]
            key = f"event:{event.get('id') or title}:{event.get('when')}"
            if key in sent:
                continue
            candidates.append((
                minutes,
                Nudge("event", f"{moment:%H:%M} · {title} · {max(1, round(minutes))} dakika sonra", key),
            ))
        for reminder in reminders or []:
            if reminder.get("done") or reminder.get("notified"):
                continue
            moment = local_moment(reminder.get("when"))
            if moment is None:
                continue
            minutes = _minutes_until(moment, now)
            if not 0 < minutes <= reminder_lead:
                continue
            text = " ".join(str(reminder.get("text") or "Hatırlatıcı").split())[:120]
            key = f"reminder:{reminder.get('id') or text}:{reminder.get('when')}"
            if key in sent:
                continue
            candidates.append((
                minutes,
                Nudge("reminder", f"{text} · {max(1, round(minutes))} dakika sonra", key),
            ))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def next(
        self,
        now: Optional[datetime] = None,
        events: Iterable[dict] = (),
        reminders: Iterable[dict] = (),
        interval_minutes: int = DEFAULT_INTERVAL,
        quiet_start: str = DEFAULT_QUIET_START,
        quiet_end: str = DEFAULT_QUIET_END,
        hydration: bool = True,
        event_lead: int = EVENT_LEAD_MINUTES,
        reminder_lead: int = REMINDER_LEAD_MINUTES,
    ) -> Optional[Nudge]:
        now = now or datetime.now()
        if in_quiet_hours(now, quiet_start, quiet_end):
            return None
        try:
            interval = max(15, min(int(interval_minutes), 480))
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL
        with self._lock:
            self._ensure_water_clock(now)
            sent = self._sent_map(now)
            last_nudge = _parse_moment(self._state.get("last_nudge"))
            if last_nudge and now - last_nudge < timedelta(minutes=AGENDA_GAP_MINUTES):
                return None

            agenda = self._agenda(
                events,
                reminders,
                now,
                sent,
                max(1, min(int(event_lead), EVENT_LEAD_MINUTES)),
                max(1, min(int(reminder_lead), REMINDER_LEAD_MINUTES)),
            )
            if agenda is not None:
                return agenda

            if last_nudge and now - last_nudge < timedelta(minutes=interval):
                return None
            if hydration and 9 <= now.hour < 21:
                last_water = _parse_moment(self._state.get("last_water"))
                if last_water and now - last_water >= timedelta(minutes=HYDRATION_GAP_MINUTES):
                    return Nudge(
                        "hydration",
                        "Su molası: bir bardak su iyi gelir.",
                        f"hydration:{now.date().isoformat()}",
                    )
            return Nudge(
                "break",
                "Uzun bir oturum oldu; kısa bir mola gözlerini dinlendirir.",
                f"break:{now.date().isoformat()}:{now.hour}",
            )

    def mark(self, nudge: Nudge, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        with self._lock:
            self._state["last_nudge"] = now.isoformat(timespec="seconds")
            if nudge.kind in {"event", "reminder"}:
                sent = self._sent_map(now)
                sent[nudge.key] = now.isoformat(timespec="seconds")
                self._state["sent"] = sent
            if nudge.kind == "hydration":
                self._state["last_water"] = now.isoformat(timespec="seconds")
            self._save()

    def reset(self) -> None:
        with self._lock:
            self._state = {}
            self._save()
