from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import re

from modules.ticket_utils import issue_key, issue_url, project_name

def _safe_name(value: str, fallback: str = "note") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip().strip(".")
    value = re.sub(r"\s+", "-", value)
    return value[:100] or fallback


def _ticket_date_folder(ticket: dict) -> str:
    raw = str(ticket.get("created") or "").strip()
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if match:
        try:
            created = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return f"{created.month}-{created.day}-{created.year}"
        except ValueError:
            pass
    today = date.today()
    return f"{today.month}-{today.day}-{today.year}"

def _yaml(value) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'

class ObsidianSync:
    def __init__(self, vault_path: str, tickets_folder: str = "Work/Tickets", daily_folder: str = "Work/Daily", web_base: str = "https://mirax.backlog.com", programming_folder: str = "Work/Programming"):
        self.vault_path = Path(vault_path).expanduser() if vault_path else Path()
        self.tickets_folder = Path(tickets_folder); self.daily_folder = Path(daily_folder); self.programming_folder = Path(programming_folder); self.web_base = web_base

    def _require_vault(self) -> Path:
        if not str(self.vault_path) or str(self.vault_path) == ".": raise ValueError("Choose an Obsidian vault in Settings first.")
        if not self.vault_path.exists(): raise ValueError(f"Obsidian vault does not exist: {self.vault_path}")
        return self.vault_path

    def note_path(self, ticket: dict) -> Path:
        vault = self._require_vault(); key = issue_key(ticket); project = _safe_name(project_name(ticket), "Unsorted")
        project_folder = vault / self.tickets_folder / project
        folder = project_folder / _ticket_date_folder(ticket)
        existing = next(folder.glob(f"{_safe_name(key)}_*.md"), None)
        if existing:
            return existing
        legacy = next(project_folder.glob(f"{_safe_name(key)}_*.md"), None)
        if legacy:
            return legacy
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{_safe_name(key)}_{_safe_name(str(ticket.get('summary', 'ticket')))}.md"

    def create_note(self, ticket: dict) -> Path:
        path = self.note_path(ticket); now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        frontmatter = self._frontmatter(ticket, now)
        if path.exists():
            current = path.read_text(encoding="utf-8", errors="replace")
            body = re.sub(r"\A---\s*\n.*?\n---\s*\n?", "", current, count=1, flags=re.DOTALL)
            body = re.sub(r"\*Last synced:.*?\*", f"*Last synced: {now}*", body)
            content = frontmatter + "\n" + body.lstrip()
        else:
            content = frontmatter + "\n" + self._ticket_body(ticket, now)
        self._atomic_write(path, content)
        self._link_daily_ticket(ticket, path)
        return path

    def read_note(self, ticket: dict) -> tuple[Path, str]:
        path = self.note_path(ticket)
        return path, path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    @staticmethod
    def _cell(value) -> str:
        return str(value or "—").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")

    @classmethod
    def _table(cls, headers: tuple[str, ...], rows: list[dict], fields: tuple[str, ...]) -> str:
        lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        if rows:
            lines.extend("| " + " | ".join(cls._cell(row.get(field)) for field in fields) + " |" for row in rows)
        else:
            lines.append("| " + " | ".join("Not documented" for _ in headers) + " |")
        return "\n".join(lines)

    @staticmethod
    def _bullets(values: list[str], empty: str = "- Not documented") -> str:
        return "\n".join(f"- {value}" for value in values if str(value).strip()) or empty

    @staticmethod
    def _time_log(current: str) -> str:
        match = re.search(r"(?ms)^## Time Log\s*\n(.*?)(?=^## |\Z)", current)
        return match.group(1).strip() if match else ""

    def render_ai_note(self, ticket: dict, analysis: dict, current: str = "", now: str | None = None) -> str:
        now = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        key = issue_key(ticket); status = ticket.get("status", {}).get("name", "")
        categories = ", ".join(item.get("name", "") for item in ticket.get("category", []) if item.get("name")) or "None"
        status_table = self._table(("Requirement", "Current handling", "Status / notes"), analysis.get("status_items", []), ("requirement", "current", "status"))
        decisions = self._table(("Decision", "Rationale"), analysis.get("design_decisions", []), ("decision", "rationale"))
        changed = self._table(("File", "Change", "Why"), analysis.get("changed_files", []), ("file", "change", "reason"))
        errors = self._table(("Scenario", "Handling"), analysis.get("error_cases", []), ("scenario", "handling"))
        plan = "\n".join(f"- [{'x' if item.get('done') else ' '}] {item.get('task', '')}" for item in analysis.get("work_plan", []) if item.get("task")) or "- [ ] Confirm the next action"
        flow = "\n    ↓\n".join(str(value) for value in analysis.get("implementation_flow", []) if str(value).strip()) or "Flow not documented"
        learning_links = [self._learning_link(reference) for reference in analysis.get("learning_references", [])]
        time_log = self._time_log(current)
        body = f"""# {key}: {ticket.get('summary', '')}

> [Open in Backlog]({issue_url(ticket, self.web_base)})

## AI Brief

### Current situation
{analysis.get('situation') or '_Not enough evidence to summarize._'}

### What was done before
{self._bullets(analysis.get('previous_work', []))}

### Next steps
{self._bullets(analysis.get('next_steps', []))}

### Risks / unknowns
{self._bullets(analysis.get('risks', []), '- None identified from the available evidence')}

## Backlog Context

| Field | Value |
| --- | --- |
| Status | {self._cell(status)} |
| Priority | {self._cell(ticket.get('priority', {}).get('name', ''))} |
| Project | {self._cell(project_name(ticket))} |
| Assignee | {self._cell(ticket.get('assignee', {}).get('name', ''))} |
| Due | {self._cell(ticket.get('dueDate') or 'Not set')} |
| Categories | {self._cell(categories)} |

## Description

{ticket.get('description') or '_No description provided._'}

## 修正方針 / Approach

{analysis.get('approach') or '_Approach requires confirmation._'}

## 対応状況 / Requirement status

{status_table}

## Work Plan

{plan}

## Implementation Notes

### 全体フロー / Overall flow

```text
{flow}
```

### 設計判断 / Design decisions

{decisions}

### 変更ファイル / Changed files

{changed}

### エラー処理 / Error handling

{errors}

## Scope intentionally excluded / 要確認

{self._bullets(analysis.get('scope_notes', []))}

## Verification

{self._bullets(analysis.get('verification', []))}

## Learning references

{chr(10).join(learning_links) or '- No reusable learning references generated'}

## Time Log

{time_log}

---
*Last synced: {now}*
"""
        return self._frontmatter(ticket, now) + "\n" + body

    def apply_ai_update(self, ticket: dict, analysis: dict) -> tuple[Path, list[Path], Path | None]:
        path = self.note_path(ticket)
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        backup = None
        if current:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self._require_vault() / self.tickets_folder / ".history" / issue_key(ticket) / f"{stamp}.md"
            backup.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(backup, current)
        self._atomic_write(path, self.render_ai_note(ticket, analysis, current, now))
        learning_paths = [self._write_learning_reference(ticket, path, reference, now) for reference in analysis.get("learning_references", [])]
        self._link_daily_ticket(ticket, path)
        return path, learning_paths, backup

    def apply_note_only(self, ticket: dict, analysis: dict) -> tuple[Path, Path | None]:
        """Rewrite just the ticket note. No lessons, no Backlog, nothing else.

        The same backup-then-replace rule as `apply_ai_update` applies, so the
        previous note is always recoverable from the history folder. Learning
        references present in the analysis are deliberately ignored here.
        """
        path = self.note_path(ticket)
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        backup = None
        if current:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self._require_vault() / self.tickets_folder / ".history" / issue_key(ticket) / f"{stamp}.md"
            backup.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(backup, current)
        self._atomic_write(path, self.render_ai_note(ticket, analysis, current))
        self._link_daily_ticket(ticket, path)
        return path, backup

    def _learning_link(self, reference: dict) -> str:
        """Wiki link to a lesson, named exactly as `_write_learning_reference` files it."""
        category = _safe_name(str(reference.get("category") or "01-Core-Concepts"), "01-Core-Concepts")
        slug = _safe_name(str(reference.get("slug") or reference.get("title") or "reference"), "reference")
        target = (self.programming_folder / category / slug).as_posix()
        return f"- [[{target}|{reference.get('title') or slug}]]"

    def apply_learning_references(self, ticket: dict, analysis: dict) -> tuple[list[Path], bool]:
        """Write only the study material, leaving the ticket note's own sections intact.

        The ticket note is never re-rendered here, so a lesson can be captured
        without the full template rewrite. Its only possible change is additive:
        missing lesson links appended inside `## Learning references`. Nothing is
        backed up because nothing the user authored is replaced.
        """
        references = analysis.get("learning_references", [])
        if not references:
            raise ValueError("There is no generated study material to save yet.")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        ticket_path = self.note_path(ticket)
        paths = [self._write_learning_reference(ticket, ticket_path, reference, now) for reference in references]
        return paths, self._link_learning_references(ticket_path, references)

    def _link_learning_references(self, path: Path, references: list[dict]) -> bool:
        """Append missing lesson links to an existing ticket note. Returns whether it changed."""
        if not path.exists():
            return False
        current = path.read_text(encoding="utf-8", errors="replace")
        missing = [link for link in (self._learning_link(item) for item in references) if link not in current]
        if not missing:
            return False
        heading = re.search(r"(?m)^## Learning references[^\n]*\n", current)
        if heading:
            rest = current[heading.end():]
            following = re.search(r"(?m)^## ", rest)
            tail = heading.end() + (following.start() if following else len(rest))
            kept = [
                line for line in current[heading.end():tail].splitlines()
                if line.strip() and line.strip() != "- No reusable learning references generated"
            ]
            updated = current[:heading.end()] + "\n".join(["", *kept, *missing, ""]) + "\n" + current[tail:]
        else:
            updated = current.rstrip() + "\n\n## Learning references\n\n" + "\n".join(missing) + "\n"
        self._atomic_write(path, updated)
        return True

    def _write_learning_reference(self, ticket: dict, ticket_path: Path, reference: dict, now: str) -> Path:
        category = _safe_name(str(reference.get("category") or "01-Core-Concepts"), "01-Core-Concepts")
        slug = _safe_name(str(reference.get("slug") or reference.get("title") or "reference"), "reference")
        path = self._require_vault() / self.programming_folder / category / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        relative_ticket = ticket_path.relative_to(self.vault_path).with_suffix("").as_posix()
        examples = []
        for example in reference.get("examples", [])[:5]:
            if not isinstance(example, dict):
                continue
            title = example.get("title") or "Example"
            code = str(example.get("code") or "").replace("```", "~~~").strip()
            explanation = example.get("explanation") or "_Explain why this example works._"
            block = f"### {title}\n\n"
            if code:
                block += f"```text\n{code}\n```\n\n"
            block += str(explanation)
            examples.append(block)
        if not examples and reference.get("example"):
            examples.append(f"### Original example\n\n{reference.get('example')}")
        examples_text = "\n\n".join(examples) or "_No example generated._"
        alternatives = []
        for alternative in reference.get("alternatives", [])[:4]:
            if not isinstance(alternative, dict):
                continue
            pros = "<br>".join(str(item) for item in alternative.get("pros", []) if str(item).strip()) or "Not documented"
            cons = "<br>".join(str(item) for item in alternative.get("cons", []) if str(item).strip()) or "Not documented"
            alternatives.append(
                "| " + " | ".join((
                    self._cell(alternative.get("name")), self._cell(alternative.get("when_to_choose")),
                    self._cell(pros), self._cell(cons), self._cell(alternative.get("example")),
                )) + " |"
            )
        alternatives_text = "\n".join((
            "| Alternative | When to choose it | Advantages | Costs / risks | Example |",
            "| --- | --- | --- | --- | --- |",
            *alternatives,
        )) if alternatives else "_No alternative was documented. Compare this approach with the simplest viable implementation before adopting it._"
        managed = f"""<!-- TYWORK-LEARNING:START -->
## Summary

{reference.get('summary') or '_Summary requires confirmation._'}

## Why this matters

{reference.get('why_it_matters') or '_Explain the problem this technique helps solve._'}

## Prerequisites

{self._bullets(reference.get('prerequisites', []))}

## Core principles

{self._bullets(reference.get('principles', []))}

## When to use

{self._bullets(reference.get('when_to_use', []))}

## When not to use

{self._bullets(reference.get('when_not_to_use', []), '- No clear exclusion identified; check the trade-offs below')}

## Examples

{examples_text}

## Alternatives and trade-offs

{alternatives_text}

## Practice exercises

{self._bullets(reference.get('exercises', []), '- Create a small variation and explain which invariant it preserves')}

## Lesson path

{self._bullets(reference.get('lesson_path', []), '- Define the problem, implement the smallest example, then test an edge case')}

## Related tickets

- [[{relative_ticket}|{issue_key(ticket)}]]
<!-- TYWORK-LEARNING:END -->"""
        pattern = r"<!-- TYWORK-LEARNING:START -->.*?<!-- TYWORK-LEARNING:END -->"
        if current:
            content = re.sub(pattern, managed, current, flags=re.DOTALL) if re.search(pattern, current, flags=re.DOTALL) else current.rstrip() + "\n\n" + managed + "\n"
        else:
            title = reference.get("title") or slug.replace("-", " ")
            content = f"---\ntitle: {_yaml(title)}\nsource_ticket: {_yaml(issue_key(ticket))}\nlast_updated: {_yaml(now)}\ntags: [programming, learning-reference]\n---\n\n# {title}\n\n{managed}\n"
        self._atomic_write(path, content)
        return path

    def _frontmatter(self, ticket: dict, now: str) -> str:
        status = ticket.get("status", {}).get("name", "")
        return "\n".join((
            "---", f"ticket_id: {_yaml(issue_key(ticket))}", f"title: {_yaml(ticket.get('summary', ''))}",
            f"status: {_yaml(status)}", f"status_id: {ticket.get('status', {}).get('id', '')}",
            f"project: {_yaml(project_name(ticket))}", f"assignee: {_yaml(ticket.get('assignee', {}).get('name', ''))}",
            f"deadline: {_yaml(ticket.get('dueDate') or '')}", f"priority: {_yaml(ticket.get('priority', {}).get('name', ''))}",
            f"backlog_url: {_yaml(issue_url(ticket, self.web_base))}", f"last_synced: {_yaml(now)}",
            "tags: [backlog, work, ticket]", "---", "",
        ))

    def _ticket_body(self, ticket: dict, now: str) -> str:
        key = issue_key(ticket); status = ticket.get("status", {}).get("name", "")
        categories = ", ".join(item.get("name", "") for item in ticket.get("category", []) if item.get("name")) or "None"
        comments = ticket.get("_comments") or []
        comment_text = "\n\n---\n\n".join(
            f"### {(item.get('createdUser') or {}).get('name') or 'Unknown author'} · {item.get('created') or 'Unknown date'}\n\n{item.get('content') or '_Empty comment._'}"
            for item in comments
        ) or "_No Backlog comments loaded._"
        return f"""# {key}: {ticket.get('summary', '')}

> [Open in Backlog]({issue_url(ticket, self.web_base)})

## Backlog Context

| Field | Value |
| --- | --- |
| Status | {status} |
| Priority | {ticket.get('priority', {}).get('name', '')} |
| Project | {project_name(ticket)} |
| Due | {ticket.get('dueDate') or 'Not set'} |
| Categories | {categories} |

## Description

{ticket.get('description') or '_No description provided._'}

## Backlog Comments

{comment_text}

## Work Plan

- [ ] Define the next action
- [ ] Record implementation notes
- [ ] Verify and close

## Notes


## Time Log


---
*Last synced: {now}*
"""

    def _link_daily_ticket(self, ticket: dict, ticket_path: Path) -> None:
        daily = self._require_vault() / self.daily_folder / f"{date.today().isoformat()}.md"; daily.parent.mkdir(parents=True, exist_ok=True)
        relative = ticket_path.relative_to(self.vault_path).with_suffix("").as_posix(); link = f"- [[{relative}|{issue_key(ticket)}]] — {ticket.get('summary', '')}"
        text = daily.read_text(encoding="utf-8", errors="replace") if daily.exists() else f"# {date.today().isoformat()}\n\n## Tickets Worked\n"
        if link not in text:
            marker = "## Tickets Worked"
            if marker not in text: text += f"\n{marker}\n"
            text += f"\n{link}\n"; self._atomic_write(daily, text)

    def create_standup_note(self, entry: dict) -> Path:
        day = entry.get("date") or date.today().isoformat(); path = self._require_vault() / self.daily_folder / f"{day}.md"; path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else f"# {day}\n"
        block = f"""<!-- TYWORK-STANDUP:START -->
## Morning Standup

### What I will say
{entry.get('what_i_will_say') or '_Not prepared._'}

### Yesterday
{entry.get('yesterday') or '-'}

### Today
{entry.get('today') or '-'}

### Blockers
{entry.get('blockers') or 'None'}

### Notes
{entry.get('notes') or '-'}
<!-- TYWORK-STANDUP:END -->"""
        pattern = r"<!-- TYWORK-STANDUP:START -->.*?<!-- TYWORK-STANDUP:END -->"
        updated = re.sub(pattern, block, current, flags=re.DOTALL) if re.search(pattern, current, flags=re.DOTALL) else current.rstrip() + "\n\n" + block + "\n"
        self._atomic_write(path, updated); return path

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp"); temporary.write_text(content, encoding="utf-8"); temporary.replace(path)
