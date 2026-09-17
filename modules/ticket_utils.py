from __future__ import annotations

import re
from urllib.parse import quote

OPEN_NAMES = {"open", "unresolved", "未対応"}
PROGRESS_NAMES = {"in progress", "processing", "処理中"}
RESOLVED_NAMES = {"resolved", "processed", "処理済み"}
CLOSED_NAMES = {"closed", "done", "complete", "completed", "完了"}

def natural_key(value: str) -> tuple:
    """Sort key where embedded numbers compare numerically.

    Keeps DEV-2 before DEV-10. Every element is a (text, number) pair so two
    keys of different shapes never compare a string against an integer.
    """
    parts = re.split(r"(\d+)", str(value or ""))
    return tuple(("", int(part)) if index % 2 else (part.casefold(), 0) for index, part in enumerate(parts))

def issue_key(issue: dict) -> str:
    return str(issue.get("key") or issue.get("issueKey") or "")

def project_name(issue: dict) -> str:
    project = issue.get("project") or {}
    return str(project.get("name") or project.get("projectKey") or issue.get("projectId") or "Unassigned")

def status_bucket(issue: dict) -> str:
    status = issue.get("status") or {}
    name = str(status.get("name", "")).strip().casefold()
    if name in CLOSED_NAMES: return "closed"
    if name in RESOLVED_NAMES: return "resolved"
    if name in PROGRESS_NAMES: return "progress"
    if name in OPEN_NAMES: return "open"
    order = status.get("displayOrder")
    if isinstance(order, int):
        if order >= 4000: return "closed"
        if order >= 3000: return "resolved"
        if order >= 2000: return "progress"
    return "open"

def is_closed(issue: dict) -> bool:
    return status_bucket(issue) == "closed"

def issue_url(issue: dict, web_base: str = "https://mirax.backlog.com") -> str:
    return f"{web_base.rstrip('/')}/view/{quote(issue_key(issue))}"
