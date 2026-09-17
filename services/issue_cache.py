"""Small disk cache so Personal Tasks remain visible during API outages."""
from __future__ import annotations

import json
from pathlib import Path

from config import APP_DIR

class IssueCache:
    def __init__(self, path: Path | None = None): self.path = path or APP_DIR / "issues-cache.json"
    def load(self) -> list[dict]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError): return []
    def save(self, issues: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True); temporary = self.path.with_suffix(".tmp"); temporary.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8"); temporary.replace(self.path)
