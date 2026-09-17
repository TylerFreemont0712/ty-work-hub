"""Persistent local calendar events, independent of optional Google Calendar sync."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from config import APP_DIR

EVENTS_PATH = APP_DIR / "events.json"

class EventStore:
    def __init__(self, path: Path = EVENTS_PATH):
        self.path = path
        self.events = self._load()
        self.revision = 0
        self._index()

    def _index(self) -> None:
        self._by_id = {event["id"]: event for event in self.events}
        self._active = next((event for event in self.events if event.get("kind") == "work" and event.get("active")), None)

    def active_work(self) -> dict | None:
        return dict(self._active) if self._active else None

    def _commit(self, events: list[dict]) -> None:
        previous = self.events
        self.events = events
        try:
            self._save()
        except OSError:
            self.events = previous
            raise
        self.revision += 1
        self._index()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            # Ignore incomplete/corrupt records rather than preventing the app
            # from starting because one saved event is malformed.
            events = []
            for event in data:
                if not isinstance(event, dict) or not isinstance(event.get("id"), (str, int)) or not event.get("title"):
                    continue
                try:
                    start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
                    if end < start:
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                events.append(event)
            return events
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.events, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def all(self) -> list[dict]:
        return list(self.events)

    def upsert(self, event: dict) -> dict:
        value = dict(event)
        value.setdefault("id", uuid.uuid4().hex)
        value.setdefault("kind", "event")
        value.setdefault("color", "#8b5cf6")
        value.setdefault("created_at", datetime.now().astimezone().isoformat())
        events = list(self.events)
        for index, existing in enumerate(events):
            if existing.get("id") == value["id"]:
                events[index] = value
                break
        else:
            events.append(value)
        self._commit(events)
        return dict(value)

    def delete(self, event_id: str) -> None:
        self._commit([event for event in self.events if event.get("id") != event_id])

    def get(self, event_id: str) -> dict | None:
        event = self._by_id.get(event_id)
        return dict(event) if event else None
