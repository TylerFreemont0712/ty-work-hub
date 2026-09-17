"""Render isolated demo screens. Never reads credentials or writes real app data."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_demo = TemporaryDirectory(prefix="ty-work-preview-")
os.environ["TY_WORK_APP_HOME"] = _demo.name
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication
from config import DEFAULTS
from modules.backlog_api import demo_issues
from modules.workspace_store import WorkspaceStore
import ui.main_window as main_window
from ui.styles import theme_styles
from ui.theme import THEME_KEYS
from ui.typography import configure_typography


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    configure_typography(app)
    config = dict(DEFAULTS, theme="dark", font_size=12, density="comfortable", svn_profiles=[], obsidian_vault_path="", task_filter_panel=False)
    app.setStyleSheet(theme_styles(config["theme"], config["density"], config["font_size"]))
    main_window.backlog_key = lambda: ""
    workspace = WorkspaceStore(Path(_demo.name) / "workspace.json")
    window = main_window.MainWindow(config, workspace, persist_preferences=False)
    for _ in range(200):
        app.processEvents()
        if window.sync_thread is None:
            break
        QThread.msleep(5)
    issues = demo_issues()
    names = ["Refine the video call connection flow", "Review the new chapter editor", "Make the activity feed easier to scan", "Document the release checklist", "Polish the mobile navigation", "Improve lesson search performance", "Add keyboard shortcuts to the editor"]
    for i, name in enumerate(names):
        seed = dict(issues[i % 3])
        key = f"DEV_3TS-{1500 + i}"
        seed.update(id=1500+i, key=key, issueKey=key, summary=name)
        issues.append(seed)
    for i, issue in enumerate(issues):
        issue["dueDate"] = (datetime.now().date() + timedelta(days=i-1)).isoformat()
    workspace.add_to_my_day([issues[0]["key"], issues[1]["key"]])
    workspace.add_capture("Check the staging build after lunch")
    workspace.add_capture("Discuss editor feedback with the team")
    workspace.add_routine("Review today's priorities")
    workspace.add_routine("Clear the review queue", "weekly")
    now = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
    for offset, minutes in enumerate([95, 130, 75, 160, 40, 115, 85]):
        start = now - timedelta(days=6-offset, hours=3)
        window.calendar.store.upsert({"title": "Focused development", "kind": "work", "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat()})
    for i, title in enumerate(["Team standup", "Design & engineering review", "Release planning"]):
        start = now + timedelta(hours=i+1)
        window.calendar.store.upsert({"title": title, "start": start.isoformat(), "end": (start+timedelta(minutes=30)).isoformat()})
    # A few finished sessions so the insights charts have something to draw.
    for offset, (minutes, key, project) in enumerate([
        (95, "DEV_3TS-612", "3TS"), (130, "DEV_XTALK-185", "CrossTalk"), (75, "", "Company General"),
        (160, "DEV_3TS-1494", "3TS"), (40, "DEV_3TS-612", "3TS"), (115, "DEV_3TS-1500", "3TS"),
    ]):
        day = now - timedelta(days=5 - offset)
        start = day.replace(hour=10 if offset % 2 else 14, minute=0)
        window.calendar.store.upsert({
            "title": f"Work: {key or project}", "kind": "work", "ticket_key": key, "project": project,
            "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat(),
        })
    window.synced(issues, True)
    window.calendar.render()
    window.show()
    output = Path(__file__).resolve().parents[1] / "artifacts"
    output.mkdir(exist_ok=True)
    for name, page, width, height, theme in (
        ("today-dark", "Command Center", 1480, 940, "dark"),
        ("tasks-dark", "Personal Tasks", 1480, 940, "dark"),
        ("calendar-dark", "Calendar", 1480, 940, "dark"),
        ("standup-dark", "Morning Standup", 1480, 940, "dark"),
        ("source-dark", "Source Control", 1480, 940, "dark"),
        ("review-dark", "Review Board", 1480, 940, "dark"),
        ("notes-dark", "Obsidian", 1480, 940, "dark"),
        ("studio-dark", "Ticket Detail", 1480, 940, "dark"),
        ("insights-dark", "Time Insights", 1480, 940, "dark"),
        ("insights-nordic", "Time Insights", 1480, 940, "nordic"),
        ("insights-small", "Time Insights", 900, 600, "dark"),
        ("today-small", "Command Center", 900, 600, "dark"),
        ("tasks-small", "Personal Tasks", 900, 600, "dark"),
        ("calendar-small", "Calendar", 900, 600, "dark"),
        ("standup-small", "Morning Standup", 900, 600, "dark"),
        ("source-small", "Source Control", 900, 600, "dark"),
        ("notes-small", "Obsidian", 900, 600, "dark"),
        ("studio-small", "Ticket Detail", 900, 600, "dark"),
        ("inspector-small", "Personal Tasks", 900, 600, "dark"),
        *(("today-" + key, "Command Center", 1480, 940, key) for key in THEME_KEYS),
        *(("tasks-" + key, "Personal Tasks", 1480, 940, key) for key in THEME_KEYS),
    ):
        config["theme"] = theme
        window.refresh_theme()
        window.navigate(page)
        if name == "inspector-small":
            window.show_ticket(issues[0])
        window.resize(width, height)
        for _ in range(12):
            app.processEvents()
        window.grab().save(str(output / f"{name}.png"))
        print(f"{name}: {window.width()} x {window.height()}", flush=True)
    window.close()
    for _ in range(200):
        app.processEvents()
        QThread.msleep(2)


if __name__ == "__main__":
    main()
