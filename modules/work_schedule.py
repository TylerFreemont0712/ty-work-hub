"""The working day: when tracked time counts and when it does not.

Pure logic with no Qt and no I/O. A work session is recorded with its real
start and end so nothing is lost, and every total in the application asks this
module how much of that span falls inside the configured schedule. Evenings,
weekends, and breaks are therefore deducted rather than never recorded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DEFAULT_START = "09:00"
DEFAULT_END = "18:00"
DEFAULT_DAYS = (0, 1, 2, 3, 4)
DEFAULT_BREAKS = ({"label": "Lunch", "start": "12:00", "end": "13:00"},)
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# A session left running over a holiday should not cost an unbounded scan.
MAX_SPAN_DAYS = 400


def parse_clock(value, fallback: str) -> time:
    """Read "HH:MM" (or "H:MM"), falling back to a known-good literal."""
    text = str(value or "").strip()
    for candidate in (text, fallback):
        parts = candidate.split(":")
        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            hour, minute = int(parts[0]), int(parts[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return time(hour, minute)
    return time(9, 0)


def format_clock(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


@dataclass(frozen=True)
class Break:
    """An unpaid gap inside the working day, such as lunch."""

    label: str
    start: time
    end: time

    @property
    def minutes(self) -> int:
        return max(0, _minutes(self.end) - _minutes(self.start))

    def to_dict(self) -> dict:
        return {"label": self.label, "start": format_clock(self.start), "end": format_clock(self.end)}


@dataclass(frozen=True)
class WorkSchedule:
    """The days and hours during which tracked work counts."""

    start: time = time(9, 0)
    end: time = time(18, 0)
    breaks: tuple[Break, ...] = ()
    days: tuple[int, ...] = DEFAULT_DAYS
    enforced: bool = True

    @classmethod
    def from_config(cls, config: dict | None) -> "WorkSchedule":
        raw = (config or {}).get("work_schedule")
        raw = raw if isinstance(raw, dict) else {}
        start = parse_clock(raw.get("start"), DEFAULT_START)
        end = parse_clock(raw.get("end"), DEFAULT_END)
        if _minutes(end) <= _minutes(start):
            start, end = parse_clock(DEFAULT_START, DEFAULT_START), parse_clock(DEFAULT_END, DEFAULT_END)
        days = tuple(sorted({int(day) for day in raw.get("days", DEFAULT_DAYS) if isinstance(day, int) and 0 <= day <= 6}))
        # An absent key means "unconfigured" and gets the default break; an
        # explicit empty list means the user deliberately removed every break.
        entries = raw.get("breaks", DEFAULT_BREAKS)
        breaks = []
        for entry in entries if isinstance(entries, (list, tuple)) else DEFAULT_BREAKS:
            if not isinstance(entry, dict):
                continue
            first = parse_clock(entry.get("start"), DEFAULT_BREAKS[0]["start"])
            last = parse_clock(entry.get("end"), DEFAULT_BREAKS[0]["end"])
            if _minutes(last) <= _minutes(first):
                continue
            breaks.append(Break(str(entry.get("label") or "Break").strip()[:40] or "Break", first, last))
        return cls(
            start=start,
            end=end,
            breaks=tuple(sorted(breaks, key=lambda item: _minutes(item.start))[:6]),
            days=days or DEFAULT_DAYS,
            enforced=bool((config or {}).get("track_business_hours_only", True)),
        )

    def to_config(self) -> dict:
        return {
            "start": format_clock(self.start),
            "end": format_clock(self.end),
            "days": list(self.days),
            "breaks": [item.to_dict() for item in self.breaks],
        }

    @property
    def daily_minutes(self) -> int:
        """Countable minutes in one full working day, breaks already removed."""
        return max(0, _minutes(self.end) - _minutes(self.start) - sum(item.minutes for item in self.breaks))

    def is_working_day(self, day: date) -> bool:
        return not self.enforced or day.weekday() in self.days

    def windows(self, day: date, zone=None) -> list[tuple[datetime, datetime]]:
        """Countable spans on one local day, in order. Empty on a non-working day.

        With enforcement off the whole day counts, which keeps every caller on
        one code path instead of branching on the setting.
        """
        zone = zone or datetime.now().astimezone().tzinfo
        midnight = datetime.combine(day, time.min, zone)
        if not self.enforced:
            return [(midnight, midnight + timedelta(days=1))]
        if day.weekday() not in self.days:
            return []
        spans = [(midnight + timedelta(minutes=_minutes(self.start)), midnight + timedelta(minutes=_minutes(self.end)))]
        for item in self.breaks:
            gap_start = midnight + timedelta(minutes=_minutes(item.start))
            gap_end = midnight + timedelta(minutes=_minutes(item.end))
            remaining = []
            for window_start, window_end in spans:
                if gap_end <= window_start or gap_start >= window_end:
                    remaining.append((window_start, window_end))
                    continue
                if window_start < gap_start:
                    remaining.append((window_start, gap_start))
                if gap_end < window_end:
                    remaining.append((gap_end, window_end))
            spans = remaining
        return spans

    def counts_now(self, moment: datetime | None = None) -> bool:
        """True when time being tracked right now is being counted."""
        moment = moment or datetime.now().astimezone()
        return any(start <= moment < end for start, end in self.windows(moment.date(), moment.tzinfo))

    def countable_seconds(self, start: datetime, end: datetime, within: date | None = None) -> int:
        """Seconds of [start, end) inside the schedule, optionally on one local day."""
        if end <= start:
            return 0
        zone = start.tzinfo or datetime.now().astimezone().tzinfo
        first = (within or start.astimezone(zone).date())
        last = (within or end.astimezone(zone).date())
        if (last - first).days > MAX_SPAN_DAYS:
            last = first + timedelta(days=MAX_SPAN_DAYS)
        total = 0
        day = first
        while day <= last:
            for window_start, window_end in self.windows(day, zone):
                overlap = (min(end, window_end) - max(start, window_start)).total_seconds()
                if overlap > 0:
                    total += int(overlap)
            day += timedelta(days=1)
        return total

    def next_window_start(self, moment: datetime | None = None) -> datetime | None:
        """When counting resumes, for the idle hint. None if it already counts."""
        moment = moment or datetime.now().astimezone()
        if not self.enforced or self.counts_now(moment):
            return None
        zone = moment.tzinfo
        for offset in range(0, 14):
            for window_start, _ in self.windows((moment + timedelta(days=offset)).date(), zone):
                if window_start > moment:
                    return window_start
        return None

    def describe(self) -> str:
        """One line for tooltips and settings, e.g. "Mon–Fri 09:00–18:00, less 1h"."""
        if not self.enforced:
            return "All tracked time counts"
        days = self._describe_days()
        hours = f"{format_clock(self.start)}–{format_clock(self.end)}"
        if not self.breaks:
            return f"{days} {hours}"
        gaps = ", ".join(f"{item.label} {format_clock(item.start)}–{format_clock(item.end)}" for item in self.breaks)
        return f"{days} {hours}, less {gaps}"

    def _describe_days(self) -> str:
        if not self.days:
            return "No days"
        runs, current = [], [self.days[0]]
        for day in self.days[1:]:
            if day == current[-1] + 1:
                current.append(day)
            else:
                runs.append(current)
                current = [day]
        runs.append(current)
        return ", ".join(
            DAY_NAMES[run[0]] if len(run) == 1 else f"{DAY_NAMES[run[0]]}–{DAY_NAMES[run[-1]]}"
            for run in runs
        )
