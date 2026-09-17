from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, quote_plus
import requests

from modules.ticket_utils import is_closed

class BacklogError(RuntimeError):
    """A safe, user-facing Backlog failure with retry guidance."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = bool(retryable)

class BacklogClient:
    def __init__(self, api_key: str, base_url: str = "https://mirax.backlog.com/api/v2", assignee_id: int | None = None, hide_closed: bool = True):
        self.api_key = api_key; self.base_url = base_url.rstrip("/"); self.assignee_id = assignee_id; self.hide_closed = hide_closed
        self.session = requests.Session(); self.session.headers.update({"Accept": "application/json"})

    def _request(self, method: str, path: str, **kwargs):
        if not self.api_key: raise BacklogError("Backlog API key is not configured. Open Settings > Backlog to add it.")
        params = dict(kwargs.pop("params", {}) or {}); params["apiKey"] = self.api_key
        try:
            response = self.session.request(method, f"{self.base_url}{path}", params=params, timeout=20, **kwargs)
            response.raise_for_status()
        except requests.HTTPError as exc:
            failed_response = getattr(exc, "response", None) or locals().get("response")
            status_code = getattr(failed_response, "status_code", None)
            detail = self._response_error(failed_response)
            status = f" ({status_code})" if status_code else ""
            message = self._redact(f"Backlog rejected the request{status}: {detail or 'No error detail was returned.'}")
            retryable = status_code == 429 or bool(status_code and status_code >= 500)
            raise BacklogError(message, status_code=status_code, retryable=retryable) from exc
        except requests.RequestException as exc:
            raise BacklogError(
                self._redact(f"Could not reach Backlog: {exc}"), retryable=True,
            ) from exc
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise BacklogError(
                "Backlog returned a response that was not valid JSON. The action may be retried after refreshing.",
                status_code=getattr(response, "status_code", None), retryable=True,
            ) from exc

    def _redact(self, value: str) -> str:
        message = str(value or "")
        for sensitive in (self.api_key, quote(self.api_key, safe=""), quote_plus(self.api_key)):
            if sensitive:
                message = message.replace(sensitive, "[redacted]")
        return message

    @staticmethod
    def _response_error(response) -> str:
        """Extract Backlog's useful error messages without exposing the request URL."""
        if response is None:
            return ""
        try:
            body = response.json()
        except (ValueError, AttributeError):
            return str(getattr(response, "text", "") or "").strip()[:1200]
        if not isinstance(body, dict):
            return ""
        errors = body.get("errors")
        if isinstance(errors, list):
            details = []
            for item in errors[:5]:
                if not isinstance(item, dict):
                    continue
                message = str(item.get("message") or "").strip()
                code = item.get("code")
                more = str(item.get("moreInfo") or "").strip()
                label = f"[{code}] " if code not in (None, "") else ""
                detail = f"{label}{message}".strip()
                if more and more != message:
                    detail += f" ({more})"
                if detail:
                    details.append(detail)
            if details:
                return "; ".join(details)[:1200]
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("detail") or "").strip()[:1200]
        return str(error or body.get("message") or "").strip()[:1200]

    def own_user(self) -> dict: return self._request("GET", "/users/myself")
    def projects(self) -> list[dict]: return self._request("GET", "/projects")
    def statuses(self, project_id: int) -> list[dict]: return self._request("GET", f"/projects/{project_id}/statuses")

    def issues(self, watched_keys: list[str] | None = None, comment_watch_keys: list[str] | None = None) -> list[dict]:
        profile = self.own_user()
        assignee_id = self.assignee_id or int(profile["id"])
        raw = self._request("GET", "/issues", params={"assigneeId[]": assignee_id, "sort": "updated", "order": "desc", "count": 100})
        watched = {str(key).strip().upper() for key in (watched_keys or []) if str(key).strip()}
        existing = {str(issue.get("issueKey") or issue.get("key") or "").upper() for issue in raw}
        for key in sorted(watched - existing):
            try:
                raw.append(self.issue(key))
            except BacklogError:
                # A removed or inaccessible watched ticket must not prevent Personal Tasks from loading.
                continue
        normalized = self._normalize_many(raw, profile)
        comment_watch = watched | {str(key).strip().upper() for key in (comment_watch_keys or []) if str(key).strip()}
        for issue in normalized:
            issue["_watched"] = issue["key"].upper() in watched
            viewer_id = (issue.get("_viewer") or {}).get("id")
            issue["_review"] = bool(issue["_watched"] and (issue.get("assignee") or {}).get("id") != viewer_id)
            if issue["key"].upper() in comment_watch:
                try:
                    issue["commentCount"] = int(self.comment_count(issue["key"]))
                except BacklogError:
                    pass
        return [issue for issue in normalized if issue.get("_watched") or not self.hide_closed or not is_closed(issue)]

    def lookup_issues(self, query: str) -> list[dict]:
        """Resolve a ticket key directly or search Backlog titles for the watch-list picker."""
        query = str(query).strip()
        if not query:
            return []
        profile = self.own_user()
        if "-" in query and " " not in query:
            try:
                raw = [self.issue(query)]
            except BacklogError:
                raw = self._request("GET", "/issues", params={"keyword": query, "sort": "updated", "order": "desc", "count": 20})
        else:
            raw = self._request("GET", "/issues", params={"keyword": query, "sort": "updated", "order": "desc", "count": 20})
        return self._normalize_many(raw, profile)

    def _normalize_many(self, raw: list[dict], profile: dict) -> list[dict]:
        projects = {project["id"]: project for project in self.projects()}
        statuses: dict[int, list[dict]] = {}
        for project_id in {int(issue.get("projectId")) for issue in raw if issue.get("projectId") is not None}:
            try: statuses[project_id] = self.statuses(project_id)
            except BacklogError: statuses[project_id] = []
        return [self._normalize(issue, projects, statuses, profile) for issue in raw]

    @staticmethod
    def _normalize(issue: dict, projects: dict[int, dict], statuses: dict[int, list[dict]], profile: dict) -> dict:
        value = dict(issue); project_id = int(value.get("projectId") or 0)
        value["key"] = value.get("issueKey") or value.get("key") or str(value.get("id", ""))
        project = projects.get(project_id, value.get("project") or {})
        value["project"] = {"id": project_id, "key": project.get("projectKey", ""), "name": project.get("name") or project.get("projectKey") or str(project_id)}
        value["_availableStatuses"] = statuses.get(project_id, [])
        value["_viewer"] = profile
        return value

    def issue(self, issue_id_or_key) -> dict: return self._request("GET", f"/issues/{quote(str(issue_id_or_key), safe='')}")
    def update_status(self, issue_id_or_key, status_id: int) -> dict:
        try:
            normalized_status = int(status_id)
        except (TypeError, ValueError) as exc:
            raise BacklogError("Backlog status ID is missing or invalid.") from exc
        return self._request("PATCH", f"/issues/{quote(str(issue_id_or_key), safe='')}", data={"statusId": normalized_status})
    def add_comment(self, issue_id_or_key, content: str) -> dict:
        return self._request("POST", f"/issues/{quote(str(issue_id_or_key), safe='')}/comments", data={"content": content})
    def comment_count(self, issue_id_or_key) -> int:
        value = self._request("GET", f"/issues/{quote(str(issue_id_or_key), safe='')}/comments/count")
        return int(value.get("count", 0))
    def comments(self, issue_id_or_key, count: int = 100) -> list[dict]:
        value = self._request("GET", f"/issues/{quote(str(issue_id_or_key), safe='')}/comments", params={"order": "desc", "count": max(1, min(100, int(count)))})
        return value if isinstance(value, list) else []

def demo_issues() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    statuses = [
        {"id": 1, "name": "未対応", "color": "#ed8077", "displayOrder": 1000},
        {"id": 2, "name": "処理中", "color": "#4488c5", "displayOrder": 2000},
        {"id": 3, "name": "処理済み", "color": "#5eb5a6", "displayOrder": 3000},
        {"id": 4, "name": "完了", "color": "#b0be3c", "displayOrder": 4000},
    ]
    profile = {"id": 2419768, "name": "Ty"}
    return [
      {"id": 456, "issueKey": "DEV_3TS-612", "key": "DEV_3TS-612", "summary": "Adjust chapter object timing after adding a chapter", "description": "Review chapter timing behavior and update the editor logic.", "status": statuses[0], "priority": {"id": 2, "name": "High"}, "project": {"id": 671051, "key": "DEV_3TS", "name": "3TS"}, "projectId": 671051, "assignee": profile, "dueDate": "2026-07-18", "updated": now, "_availableStatuses": statuses, "_viewer": profile},
      {"id": 123, "issueKey": "DEV_XTALK-185", "key": "DEV_XTALK-185", "summary": "Improve translated video chat", "description": "Improve the translation and display workflow.", "status": statuses[1], "priority": {"id": 3, "name": "Normal"}, "project": {"id": 671052, "key": "DEV_XTALK", "name": "CrossTalk"}, "projectId": 671052, "assignee": profile, "dueDate": "2026-07-20", "updated": now, "_availableStatuses": statuses, "_viewer": profile},
      {"id": 789, "issueKey": "DEV_3TS-1494", "key": "DEV_3TS-1494", "summary": "Display furigana for comprehension questions", "description": "Add furigana to the question and choice display.", "status": statuses[2], "priority": {"id": 2, "name": "High"}, "project": {"id": 671051, "key": "DEV_3TS", "name": "3TS"}, "projectId": 671051, "assignee": profile, "dueDate": None, "updated": now, "_availableStatuses": statuses, "_viewer": profile},
    ]
