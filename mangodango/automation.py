"""GUI-free automation scheduling for MangoDango.

This module has no Qt/widget dependencies so it can be reused by both the
desktop GUI (via a QTimer) and the headless server runner. It models a set of
"schedule slots" (a weekday + a time of day) and answers two questions:

* Has a scheduled slot become due since the last run? (used by the GUI timer)
* When is the next scheduled slot? (used by the server sleep loop)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Ordered weekday codes. "daily" means every day at the given time.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_CHOICES = ("daily",) + WEEKDAYS
_WEEKDAY_INDEX = {code: index for index, code in enumerate(WEEKDAYS)}


def normalize_day(value: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in DAY_CHOICES else "daily"


def normalize_time(value: str) -> str:
    """Return a valid ``HH:MM`` string, falling back to ``00:00``."""
    text = str(value or "").strip()
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return "00:00"
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return f"{hour:02d}:{minute:02d}"


@dataclass(frozen=True)
class AutomationSlot:
    day: str = "daily"
    time: str = "00:00"

    @classmethod
    def from_dict(cls, data: dict | None) -> "AutomationSlot":
        data = data or {}
        return cls(day=normalize_day(data.get("day", "daily")), time=normalize_time(data.get("time", "00:00")))

    def to_dict(self) -> dict:
        return {"day": self.day, "time": self.time}

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])

    def _at(self, day: datetime) -> datetime:
        return day.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)

    def most_recent_occurrence(self, now: datetime) -> datetime:
        """Latest datetime this slot fired at or before ``now``."""
        if self.day == "daily":
            candidate = self._at(now)
            if candidate > now:
                candidate -= timedelta(days=1)
            return candidate
        target = _WEEKDAY_INDEX.get(self.day, 0)
        delta = (now.weekday() - target) % 7
        candidate = self._at(now - timedelta(days=delta))
        if candidate > now:
            candidate -= timedelta(days=7)
        return candidate

    def next_occurrence(self, now: datetime) -> datetime:
        """Earliest datetime this slot fires strictly after ``now``."""
        if self.day == "daily":
            candidate = self._at(now)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate
        target = _WEEKDAY_INDEX.get(self.day, 0)
        delta = (target - now.weekday()) % 7
        candidate = self._at(now + timedelta(days=delta))
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate


@dataclass
class AutomationSchedule:
    enabled: bool = False
    slots: list[AutomationSlot] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "AutomationSchedule":
        data = data or {}
        raw_slots = data.get("slots", [])
        slots: list[AutomationSlot] = []
        seen: set[tuple[str, str]] = set()
        if isinstance(raw_slots, list):
            for item in raw_slots:
                if not isinstance(item, dict):
                    continue
                slot = AutomationSlot.from_dict(item)
                key = (slot.day, slot.time)
                if key not in seen:
                    seen.add(key)
                    slots.append(slot)
        slots.sort(key=_slot_sort_key)
        return cls(enabled=bool(data.get("enabled", False)), slots=slots)

    @classmethod
    def from_json(cls, raw: str | None) -> "AutomationSchedule":
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {}
        return cls.from_dict(data if isinstance(data, dict) else {})

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "slots": [slot.to_dict() for slot in self.slots]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def clone(self) -> "AutomationSchedule":
        return AutomationSchedule(enabled=self.enabled, slots=list(self.slots))

    def add_slot(self, slot: AutomationSlot) -> bool:
        if slot in self.slots:
            return False
        self.slots.append(slot)
        self.slots.sort(key=_slot_sort_key)
        return True

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.slots)

    def due_since(self, last_run: datetime | None, now: datetime | None = None) -> bool:
        """True if any slot fired in the window ``(last_run, now]``."""
        if not self.active:
            return False
        now = now or datetime.now()
        if last_run is None:
            return False
        return any(slot.most_recent_occurrence(now) > last_run for slot in self.slots)

    def next_run(self, now: datetime | None = None) -> datetime | None:
        if not self.active:
            return None
        now = now or datetime.now()
        candidates = [slot.next_occurrence(now) for slot in self.slots]
        return min(candidates) if candidates else None


def _slot_sort_key(slot: AutomationSlot) -> tuple[int, int, int]:
    day_rank = 0 if slot.day == "daily" else (_WEEKDAY_INDEX.get(slot.day, 0) + 1)
    return (day_rank, slot.hour, slot.minute)
