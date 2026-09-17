"""Opt-in demo fixtures. These are only written to the temporary --demo workspace."""
from __future__ import annotations
from datetime import datetime, timedelta
from modules.backlog_api import demo_issues


def demo_tickets():
    issues = demo_issues()
    titles = ["Refine the video call connection flow", "Review the new chapter editor",
              "Make the activity feed easier to scan", "Document the release checklist",
              "Polish the mobile navigation", "Improve lesson search performance",
              "Add keyboard shortcuts to the editor"]
    for index, title in enumerate(titles):
        seed = dict(issues[index % 3])
        key = f"DEV_3TS-{1500 + index}"
        seed.update(id=1500 + index, key=key, issueKey=key, summary=title)
        issues.append(seed)
    for index, issue in enumerate(issues):
        issue["dueDate"] = (datetime.now().date() + timedelta(days=index - 1)).isoformat()
    return issues


def seed_workspace(window):
    issues = demo_tickets()
    workspace = window.workspace
    workspace.add_to_my_day([issue["key"] for issue in issues[:2]])
    workspace.add_capture("Check the staging build after lunch")
    workspace.add_capture("Discuss editor feedback with the team")
    workspace.add_routine("Review today's priorities")
    workspace.add_routine("Clear the review queue", "weekly")
    workspace.add_watched_ticket(issues[3]["key"])
    workspace.add_watched_ticket(issues[4]["key"])
    now = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
    for offset, minutes in enumerate([95, 130, 75, 160, 40, 115, 85]):
        start = now - timedelta(days=6 - offset, hours=3)
        window.calendar.store.upsert({"title": "Focused development", "kind": "work", "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat()})
    for index, title in enumerate(["Team standup", "Design & engineering review", "Release planning"]):
        start = now + timedelta(hours=index + 1)
        window.calendar.store.upsert({"title": title, "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat()})
    window.dashboard.daily_tools.bind(workspace)
    window.calendar.render()
    window._refresh_workspace_views()
