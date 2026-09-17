from PyQt6.QtCore import QThread, pyqtSignal
from modules.backlog_api import BacklogClient, BacklogError, demo_issues
from services.issue_cache import IssueCache

class SyncThread(QThread):
    synced = pyqtSignal(list, bool)
    failed = pyqtSignal(str)
    def __init__(self, api_key: str, config: dict | None = None, watched_keys: list[str] | None = None, comment_watch_keys: list[str] | None = None):
        super().__init__(); self.api_key = api_key; self.config = config or {}; self.watched_keys = watched_keys or []; self.comment_watch_keys = comment_watch_keys or []
    def run(self):
        try:
            if self.api_key:
                client = BacklogClient(self.api_key, self.config.get("backlog_base_url", "https://mirax.backlog.com/api/v2"), self.config.get("backlog_assignee_id"), self.config.get("backlog_hide_closed", True))
                issues = client.issues(self.watched_keys, self.comment_watch_keys); IssueCache().save(issues); self.synced.emit(issues, False)
            else: self.synced.emit(demo_issues(), True)
        except Exception as exc:
            cached = IssueCache().load()
            if cached: self.synced.emit(cached, False)
            self.failed.emit(str(exc) + ("\nShowing the last successful Personal Tasks cache." if cached else ""))

class BacklogActionThread(QThread):
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str, bool)
    def __init__(self, api_key: str, action: str, issue_id, value, config: dict | None = None):
        super().__init__(); self.api_key, self.action, self.issue_id, self.value, self.config = api_key, action, issue_id, value, config or {}
    def run(self):
        try:
            client = BacklogClient(self.api_key, self.config.get("backlog_base_url", "https://mirax.backlog.com/api/v2"), self.config.get("backlog_assignee_id"), self.config.get("backlog_hide_closed", True))
            if self.action == "status":
                result = client.update_status(self.issue_id, self.value)
            elif self.action == "comment":
                result = client.add_comment(self.issue_id, self.value)
            else:
                raise BacklogError(f"Unsupported queued Backlog action: {self.action}")
            self.completed.emit(self.action, result)
        except Exception as exc:
            retryable = exc.retryable if isinstance(exc, BacklogError) else False
            self.failed.emit(str(exc), retryable)


class BacklogLookupThread(QThread):
    found = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, api_key: str, query: str, config: dict | None = None):
        super().__init__(); self.api_key, self.query, self.config = api_key, query, config or {}

    def run(self):
        try:
            client = BacklogClient(
                self.api_key,
                self.config.get("backlog_base_url", "https://mirax.backlog.com/api/v2"),
                self.config.get("backlog_assignee_id"),
                False,
            )
            self.found.emit(client.lookup_issues(self.query))
        except Exception as exc:
            self.failed.emit(str(exc))


class BacklogCommentsThread(QThread):
    completed = pyqtSignal(str, list)
    failed = pyqtSignal(str)

    def __init__(self, api_key: str, issue_key: str, config: dict | None = None):
        super().__init__()
        self.api_key, self.issue_key, self.config = api_key, issue_key, config or {}

    def run(self) -> None:
        try:
            client = BacklogClient(self.api_key, self.config.get("backlog_base_url", "https://mirax.backlog.com/api/v2"), self.config.get("backlog_assignee_id"), False)
            self.completed.emit(self.issue_key, client.comments(self.issue_key))
        except Exception as exc:
            self.failed.emit(str(exc))
