"""Small local workspace state shared by the dashboard and task list."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from config import APP_DIR


class WorkspaceStore:
    def __init__(self, path: Path | None = None):
        self.path = path or APP_DIR / "workspace.json"
        self._data = self._load()
        self._committed = json.dumps(self._data, ensure_ascii=False, indent=2)

    def _load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        favorites = value.get("favorites", []) if isinstance(value, dict) else []
        if not isinstance(favorites, list):
            favorites = []
        captures = value.get("captures", []) if isinstance(value, dict) else []
        clean_captures = []
        if isinstance(captures, list):
            for capture in captures:
                if not isinstance(capture, dict) or not str(capture.get("text", "")).strip():
                    continue
                clean_captures.append({
                    "id": str(capture.get("id") or uuid4()),
                    "text": str(capture["text"]).strip(),
                    "created": str(capture.get("created") or ""),
                    "done": bool(capture.get("done", False)),
                })
        my_day = value.get("my_day", {}) if isinstance(value, dict) else {}
        if not isinstance(my_day, dict):
            my_day = {}
        reminders = value.get("reminders", {}) if isinstance(value, dict) else {}
        clean_reminders = {}
        if isinstance(reminders, dict):
            for key, reminder in reminders.items():
                reminder = {"at": reminder, "notified": False} if isinstance(reminder, str) else reminder
                if not isinstance(reminder, dict):
                    continue
                try:
                    datetime.fromisoformat(str(reminder.get("at", "")))
                except ValueError:
                    continue
                clean_reminders[str(key)] = {"at": str(reminder["at"]), "notified": bool(reminder.get("notified", False))}
        seen_issues = value.get("seen_issues", {}) if isinstance(value, dict) else {}
        clean_seen = {}
        if isinstance(seen_issues, dict):
            for key, record in seen_issues.items():
                if not isinstance(record, dict) or not str(record.get("signature", "")).strip():
                    continue
                clean_seen[str(key)] = {
                    "signature": str(record["signature"]),
                    "seen_at": str(record.get("seen_at", "")),
                    "mode": "hidden" if record.get("mode") == "hidden" else "back",
                }
        watched = value.get("watched_tickets", []) if isinstance(value, dict) else []
        watched = sorted({str(key).strip().upper() for key in watched if str(key).strip()}) if isinstance(watched, list) else []
        followups = value.get("followups", []) if isinstance(value, dict) else []
        clean_followups = []
        if isinstance(followups, list):
            for item in followups:
                if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                    continue
                clean_followups.append({
                    "id": str(item.get("id") or uuid4()),
                    "title": str(item["title"]).strip()[:500],
                    "who": str(item.get("who", "")).strip()[:200],
                    "due": str(item.get("due", ""))[:10],
                    "created": str(item.get("created", "")),
                    "done": bool(item.get("done", False)),
                })
        routines = value.get("routines", []) if isinstance(value, dict) else []
        clean_routines = []
        if isinstance(routines, list):
            for item in routines:
                if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                    continue
                clean_routines.append({
                    "id": str(item.get("id") or uuid4()),
                    "title": str(item["title"]).strip()[:300],
                    "cadence": "weekly" if item.get("cadence") == "weekly" else "daily",
                    "last_completed": str(item.get("last_completed", ""))[:10],
                })
        local_workflow = value.get("local_workflow", {}) if isinstance(value, dict) else {}
        if not isinstance(local_workflow, dict):
            local_workflow = {}
        related = value.get("related_tickets", {}) if isinstance(value, dict) else {}
        if not isinstance(related, dict):
            related = {}
        pending_actions = value.get("pending_actions", []) if isinstance(value, dict) else []
        if not isinstance(pending_actions, list):
            pending_actions = []
        return {
            "favorites": sorted({str(key) for key in favorites if str(key).strip()}),
            "captures": clean_captures[:200],
            "my_day": {"date": str(my_day.get("date", "")), "keys": sorted({str(key) for key in my_day.get("keys", [])}) if isinstance(my_day.get("keys"), list) else []},
            "reminders": clean_reminders,
            "seen_issues": clean_seen,
            "watched_tickets": watched,
            "followups": clean_followups[:300],
            "routines": clean_routines[:100],
            "local_workflow": {str(key): str(state) for key, state in local_workflow.items() if str(key).strip()},
            "related_tickets": {str(key): [str(item).strip().upper() for item in items if str(item).strip()] for key, items in related.items() if isinstance(items, list)},
            "pending_actions": [item for item in pending_actions[:100] if isinstance(item, dict) and item.get("action") and item.get("issue_id")],
            "scratchpad": str(value.get("scratchpad", ""))[:100000] if isinstance(value, dict) else "",
        }

    def _save(self) -> None:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        if payload == self._committed:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            self._data = json.loads(self._committed)
            raise
        self._committed = payload

    def favorites(self) -> set[str]:
        return set(self._data["favorites"])

    def is_favorite(self, key: str) -> bool:
        return key in self.favorites()

    def toggle_favorite(self, key: str) -> bool:
        values = self.favorites()
        if key in values:
            values.remove(key)
            enabled = False
        else:
            values.add(key)
            enabled = True
        self._data["favorites"] = sorted(values)
        self._save()
        return enabled

    def captures(self, include_done: bool = True) -> list[dict]:
        values = self._data["captures"]
        if not include_done:
            values = [capture for capture in values if not capture["done"]]
        return [dict(capture) for capture in values]

    def add_capture(self, text: str) -> dict | None:
        text = " ".join(text.strip().split())
        if not text:
            return None
        capture = {
            "id": str(uuid4()),
            "text": text[:1000],
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "done": False,
        }
        self._data["captures"].insert(0, capture)
        self._data["captures"] = self._data["captures"][:200]
        self._save()
        return dict(capture)

    def toggle_capture(self, capture_id: str) -> bool | None:
        for capture in self._data["captures"]:
            if capture["id"] == capture_id:
                capture["done"] = not capture["done"]
                self._save()
                return capture["done"]
        return None

    def remove_capture(self, capture_id: str) -> bool:
        before = len(self._data["captures"])
        self._data["captures"] = [value for value in self._data["captures"] if value["id"] != capture_id]
        changed = len(self._data["captures"]) != before
        if changed:
            self._save()
        return changed

    def my_day_keys(self) -> set[str]:
        today = date.today().isoformat()
        if self._data["my_day"].get("date") != today:
            self._data["my_day"] = {"date": today, "keys": []}
            self._save()
        return set(self._data["my_day"]["keys"])

    def toggle_my_day(self, key: str) -> bool:
        keys = self.my_day_keys()
        if key in keys:
            keys.remove(key)
            enabled = False
        else:
            keys.add(key)
            enabled = True
        self._data["my_day"] = {"date": date.today().isoformat(), "keys": sorted(keys)}
        self._save()
        return enabled

    def add_to_my_day(self, keys: list[str] | set[str]) -> int:
        values = self.my_day_keys()
        before = len(values)
        values.update(str(key) for key in keys if str(key).strip())
        self._data["my_day"] = {"date": date.today().isoformat(), "keys": sorted(values)}
        self._save()
        return len(values) - before

    def reminders(self) -> dict[str, dict]:
        return {key: dict(value) for key, value in self._data["reminders"].items()}

    def reminder_for(self, key: str) -> dict | None:
        value = self._data["reminders"].get(key)
        return dict(value) if value else None

    def set_reminder(self, key: str, when: datetime) -> dict:
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.now().astimezone().tzinfo)
        reminder = {"at": when.astimezone().isoformat(timespec="minutes"), "notified": False}
        self._data["reminders"][key] = reminder
        self._save()
        return dict(reminder)

    def clear_reminder(self, key: str) -> bool:
        existed = self._data["reminders"].pop(key, None) is not None
        if existed:
            self._save()
        return existed

    def due_reminders(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now().astimezone()
        due = []
        for key, reminder in self._data["reminders"].items():
            if reminder.get("notified"):
                continue
            try:
                when = datetime.fromisoformat(reminder["at"])
                if when.tzinfo is None:
                    when = when.replace(tzinfo=now.tzinfo)
            except (KeyError, ValueError):
                continue
            if when <= now:
                due.append({"ticket_key": key, **reminder})
        return sorted(due, key=lambda value: value["at"])

    def mark_reminder_notified(self, key: str) -> None:
        if key in self._data["reminders"]:
            self._data["reminders"][key]["notified"] = True
            self._save()

    @staticmethod
    def issue_signature(issue: dict) -> str:
        """Return a stable revision fingerprint for fields that merit renewed attention."""
        assignee = issue.get("assignee") or {}
        status = issue.get("status") or {}
        priority = issue.get("priority") or {}
        payload = {
            "updated": issue.get("updated"),
            "summary": issue.get("summary"),
            "description": issue.get("description"),
            "status": status.get("id") or status.get("name"),
            "priority": priority.get("id") or priority.get("name"),
            "assignee": assignee.get("id") or assignee.get("name"),
            "due": issue.get("dueDate"),
            "comment_count": issue.get("commentCount") or 0,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    def issue_attention(self, issue: dict) -> str:
        record = self._data["seen_issues"].get(str(issue.get("key") or issue.get("issueKey") or ""))
        if not record:
            return "new"
        if record.get("signature") != self.issue_signature(issue):
            return "changed"
        return str(record.get("mode") or "back")

    def seen_records(self) -> dict[str, dict]:
        return {key: dict(value) for key, value in self._data["seen_issues"].items()}

    def mark_issue_seen(self, issue: dict, mode: str = "back") -> None:
        key = str(issue.get("key") or issue.get("issueKey") or "").strip()
        if not key:
            return
        self._data["seen_issues"][key] = {
            "signature": self.issue_signature(issue),
            "seen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "hidden" if mode == "hidden" else "back",
        }
        self._save()

    def clear_issue_seen(self, key: str) -> bool:
        changed = self._data["seen_issues"].pop(str(key), None) is not None
        if changed:
            self._save()
        return changed

    def watched_keys(self) -> list[str]:
        return list(self._data["watched_tickets"])

    def add_watched_ticket(self, key: str) -> bool:
        key = str(key).strip().upper()
        if not key or key in self._data["watched_tickets"]:
            return False
        self._data["watched_tickets"].append(key)
        self._data["watched_tickets"].sort()
        self._save()
        return True

    def remove_watched_ticket(self, key: str) -> bool:
        key = str(key).strip().upper()
        before = len(self._data["watched_tickets"])
        self._data["watched_tickets"] = [value for value in self._data["watched_tickets"] if value != key]
        changed = len(self._data["watched_tickets"]) != before
        if changed:
            self._save()
        return changed

    def followups(self) -> list[dict]:
        return [dict(item) for item in self._data["followups"]]

    def add_followup(self, title: str, who: str = "", due: str = "") -> dict | None:
        title = " ".join(str(title).strip().split())
        if not title:
            return None
        item = {
            "id": str(uuid4()), "title": title[:500], "who": str(who).strip()[:200],
            "due": str(due)[:10], "created": datetime.now().astimezone().isoformat(timespec="seconds"), "done": False,
        }
        self._data["followups"].insert(0, item)
        self._data["followups"] = self._data["followups"][:300]
        self._save()
        return dict(item)

    def toggle_followup(self, item_id: str) -> bool | None:
        for item in self._data["followups"]:
            if item["id"] == item_id:
                item["done"] = not item["done"]
                self._save()
                return item["done"]
        return None

    def remove_followup(self, item_id: str) -> bool:
        before = len(self._data["followups"])
        self._data["followups"] = [item for item in self._data["followups"] if item["id"] != item_id]
        changed = len(self._data["followups"]) != before
        if changed:
            self._save()
        return changed

    def routines(self) -> list[dict]:
        return [dict(item) for item in self._data["routines"]]

    def add_routine(self, title: str, cadence: str = "daily") -> dict | None:
        title = " ".join(str(title).strip().split())
        if not title:
            return None
        item = {"id": str(uuid4()), "title": title[:300], "cadence": "weekly" if cadence == "weekly" else "daily", "last_completed": ""}
        self._data["routines"].append(item)
        self._save()
        return dict(item)

    @staticmethod
    def routine_is_current(item: dict, today: date | None = None) -> bool:
        today = today or date.today()
        try:
            completed = date.fromisoformat(str(item.get("last_completed", "")))
        except ValueError:
            return False
        if item.get("cadence") == "weekly":
            return completed.isocalendar()[:2] == today.isocalendar()[:2]
        return completed == today

    def toggle_routine(self, item_id: str) -> bool | None:
        for item in self._data["routines"]:
            if item["id"] == item_id:
                current = self.routine_is_current(item)
                item["last_completed"] = "" if current else date.today().isoformat()
                self._save()
                return not current
        return None

    def remove_routine(self, item_id: str) -> bool:
        before = len(self._data["routines"])
        self._data["routines"] = [item for item in self._data["routines"] if item["id"] != item_id]
        changed = len(self._data["routines"]) != before
        if changed:
            self._save()
        return changed

    def scratchpad(self) -> str:
        return str(self._data.get("scratchpad", ""))

    def set_scratchpad(self, text: str) -> None:
        self._data["scratchpad"] = str(text)[:100000]
        self._save()

    def workflow_state(self, key: str) -> str:
        return str(self._data["local_workflow"].get(str(key), "Untriaged"))

    def set_workflow_state(self, key: str, state: str) -> None:
        self._data["local_workflow"][str(key)] = str(state).strip()[:80] or "Untriaged"
        self._save()

    def related_tickets(self, key: str) -> list[str]:
        return list(self._data["related_tickets"].get(str(key), []))

    def set_related_tickets(self, key: str, values: list[str]) -> None:
        self._data["related_tickets"][str(key)] = sorted({str(value).strip().upper() for value in values if str(value).strip()})[:30]
        self._save()

    def queue_action(self, action: str, issue_id: str, value) -> dict:
        action = str(action)
        issue_id = str(issue_id)
        if action == "status":
            # Only the newest requested status matters. Replaying stale status
            # transitions after connectivity returns would move a ticket backwards.
            self._data["pending_actions"] = [
                item for item in self._data["pending_actions"]
                if not (item.get("action") == "status" and str(item.get("issue_id")) == issue_id)
            ]
        elif action == "comment":
            duplicate = next((
                item for item in self._data["pending_actions"]
                if item.get("action") == "comment"
                and str(item.get("issue_id")) == issue_id
                and item.get("value") == value
            ), None)
            if duplicate:
                return dict(duplicate)
        item = {"action": action, "issue_id": issue_id, "value": value, "queued_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        self._data["pending_actions"].append(item)
        self._data["pending_actions"] = self._data["pending_actions"][-100:]
        self._save()
        return dict(item)

    def pending_actions(self) -> list[dict]:
        return [dict(item) for item in self._data["pending_actions"]]

    def pending_actions_for(self, issue_id: str) -> list[dict]:
        key = str(issue_id).strip().upper()
        return [
            dict(item) for item in self._data["pending_actions"]
            if str(item.get("issue_id") or "").strip().upper() == key
        ]

    def remove_pending_action(self, item: dict) -> None:
        self._data["pending_actions"] = [value for value in self._data["pending_actions"] if value != item]
        self._save()
