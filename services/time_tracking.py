"""Small local work-session tracker backed by the calendar event store.

A session records the real wall-clock start and end. How much of that span
counts as work is decided by `modules.work_schedule`, so nothing is lost when
the timer is left running past the end of the day.
"""
from __future__ import annotations

from datetime import datetime

from modules.event_store import EventStore
from modules.ticket_utils import issue_key, project_name
from modules.work_schedule import WorkSchedule


class TimeTracker:
    def __init__(self, store: EventStore):
        self.store = store

    def active(self) -> dict | None:
        return self.store.active_work()

    def start(self, ticket: dict | None = None, project: str = "", note: str = "") -> dict:
        """Begin an open-ended session. A ticket is optional; a project is not.

        Returns the already-running session unchanged when one exists, so a
        double click can never split one stretch of work into two records.
        """
        existing = self.active()
        if existing:
            return existing
        ticket = ticket or {}
        key = issue_key(ticket)
        # An empty project is left empty; the reporting layer applies the
        # configured general project so its name lives in exactly one place.
        label = str(project or "").strip() or (project_name(ticket) if ticket else "")
        summary = str(ticket.get("summary") or "").strip()
        title = " — ".join(part for part in (f"Work: {key or label or 'general'}", summary) if part)
        now = datetime.now().astimezone()
        return self.store.upsert({
            "title": title,
            "kind": "work",
            "ticket_key": key,
            "ticket_id": ticket.get("id"),
            "project": label,
            "note": str(note or "").strip()[:400],
            "start": now.isoformat(),
            # An open session still needs a usable end for the calendar grid and
            # for the malformed-record filter in the store; it moves on stop.
            "end": now.isoformat(),
            "active": True,
        })

    def stop(self) -> dict | None:
        event = self.active()
        if not event:
            return None
        event["end"] = datetime.now().astimezone().isoformat()
        event["active"] = False
        return self.store.upsert(event)

    def elapsed_seconds(self) -> int:
        """Wall-clock seconds since the active session started."""
        event = self.active()
        if not event:
            return 0
        start = datetime.fromisoformat(event["start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return max(0, int((datetime.now().astimezone() - start).total_seconds()))

    def counted_seconds(self, schedule: WorkSchedule) -> int:
        """Seconds of the active session that fall inside the work schedule."""
        event = self.active()
        if not event:
            return 0
        zone = datetime.now().astimezone().tzinfo
        try:
            start = datetime.fromisoformat(event["start"])
        except (KeyError, TypeError, ValueError):
            return 0
        start = start.astimezone() if start.tzinfo else start.replace(tzinfo=zone)
        return schedule.countable_seconds(start, datetime.now().astimezone())
