"""Date-aware local work totals shared by the overview, insights, and tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from modules.work_schedule import WorkSchedule

UNASSIGNED = "No ticket"


def _local(value, zone) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment.astimezone() if moment.tzinfo else moment.replace(tzinfo=zone)


def session_span(event: dict, zone=None, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """Local start/end of one work event, or None when it is not usable.

    A session that is still running is measured up to `now` so the current
    timer contributes to today's totals instead of appearing only once stopped.
    """
    if event.get("kind") != "work":
        return None
    zone = zone or datetime.now().astimezone().tzinfo
    start = _local(event.get("start"), zone)
    if start is None:
        return None
    end = now or datetime.now().astimezone() if event.get("active") else _local(event.get("end"), zone)
    if end is None or end <= start:
        return None
    return start, end


def logged_minutes(
    events: list[dict],
    days: list[date],
    schedule: WorkSchedule | None = None,
    now: datetime | None = None,
) -> list[int]:
    """Minutes worked on each local day.

    Without a schedule every recorded minute counts, which is what the calendar
    grid and older callers expect. With one, evenings, weekends, and breaks are
    deducted so the totals match the hours the user is actually paid for.
    """
    totals = [0] * len(days)
    zone = datetime.now().astimezone().tzinfo
    for event in events:
        span = session_span(event, zone, now)
        if span is None:
            continue
        start, end = span
        for index, day in enumerate(days):
            if schedule is not None:
                totals[index] += schedule.countable_seconds(start, end, within=day) // 60
                continue
            lower = datetime.combine(day, time.min, zone)
            upper = lower + timedelta(days=1)
            totals[index] += max(0, int((min(end, upper) - max(start, lower)).total_seconds() // 60))
    return totals


@dataclass
class WorkSummary:
    """Counted time over a date range, broken down for the insights charts."""

    days: list[date] = field(default_factory=list)
    daily_minutes: list[int] = field(default_factory=list)
    by_ticket: dict[str, int] = field(default_factory=dict)
    by_project: dict[str, int] = field(default_factory=dict)
    sessions: list[dict] = field(default_factory=list)

    @property
    def total_minutes(self) -> int:
        return sum(self.daily_minutes)

    @property
    def tracked_days(self) -> int:
        return sum(1 for value in self.daily_minutes if value > 0)

    def ranked_tickets(self, limit: int = 8) -> list[tuple[str, int]]:
        return _ranked(self.by_ticket, limit)

    def ranked_projects(self, limit: int = 8) -> list[tuple[str, int]]:
        return _ranked(self.by_project, limit)


def _ranked(totals: dict[str, int], limit: int) -> list[tuple[str, int]]:
    """Largest first, with everything past `limit` folded into one Other row."""
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0].casefold()))
    ordered = [item for item in ordered if item[1] > 0]
    if len(ordered) <= limit:
        return ordered
    head = ordered[:limit - 1]
    head.append((f"Other ({len(ordered) - limit + 1})", sum(value for _, value in ordered[limit - 1:])))
    return head


def summarize(
    events: list[dict],
    days: list[date],
    schedule: WorkSchedule | None = None,
    fallback_project: str = "Company General",
    now: datetime | None = None,
) -> WorkSummary:
    """Counted minutes per day, per ticket, and per project across `days`.

    Each session is attributed to the ticket and project recorded when it
    started. Sessions saved before projects were recorded fall back to the
    general project so nothing silently disappears from the totals.
    """
    zone = datetime.now().astimezone().tzinfo
    window = set(days)
    summary = WorkSummary(days=list(days), daily_minutes=[0] * len(days))
    index = {day: position for position, day in enumerate(days)}
    for event in events:
        span = session_span(event, zone, now)
        if span is None:
            continue
        start, end = span
        ticket = str(event.get("ticket_key") or "").strip() or UNASSIGNED
        project = str(event.get("project") or "").strip() or fallback_project
        counted = 0
        day = start.date()
        last = end.date()
        while day <= last:
            if day in window:
                minutes = (
                    schedule.countable_seconds(start, end, within=day) // 60 if schedule is not None
                    else max(0, int((
                        min(end, datetime.combine(day + timedelta(days=1), time.min, zone))
                        - max(start, datetime.combine(day, time.min, zone))
                    ).total_seconds() // 60))
                )
                summary.daily_minutes[index[day]] += minutes
                counted += minutes
            day += timedelta(days=1)
        if counted <= 0:
            continue
        summary.by_ticket[ticket] = summary.by_ticket.get(ticket, 0) + counted
        summary.by_project[project] = summary.by_project.get(project, 0) + counted
        summary.sessions.append({
            "start": start,
            "end": end,
            "minutes": counted,
            "ticket": ticket,
            "project": project,
            "title": str(event.get("title") or ""),
            "active": bool(event.get("active")),
        })
    summary.sessions.sort(key=lambda item: item["start"], reverse=True)
    return summary


def format_minutes(minutes: int) -> str:
    """`0h 00m` reads consistently in tables and headings at any magnitude."""
    value = max(0, int(minutes))
    return f"{value // 60}h {value % 60:02d}m"
