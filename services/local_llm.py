from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
import requests

from modules.backlog_api import BacklogClient, BacklogError
from modules.patch_store import PatchStore
from modules.svn_service import parse_patch_summary
from modules.ticket_utils import issue_key, project_name


LEARNING_REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "title": {"type": "string"},
        "slug": {"type": "string"},
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "principles": {"type": "array", "items": {"type": "string"}},
        "when_to_use": {"type": "array", "items": {"type": "string"}},
        "when_not_to_use": {"type": "array", "items": {"type": "string"}},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "examples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "code": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["title", "code", "explanation"],
            },
        },
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "when_to_choose": {"type": "string"},
                    "pros": {"type": "array", "items": {"type": "string"}},
                    "cons": {"type": "array", "items": {"type": "string"}},
                    "example": {"type": "string"},
                },
                "required": ["name", "when_to_choose", "pros", "cons", "example"],
            },
        },
        "exercises": {"type": "array", "items": {"type": "string"}},
        "lesson_path": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["category", "title", "slug", "summary", "why_it_matters", "principles", "when_to_use", "when_not_to_use", "prerequisites", "examples", "alternatives", "exercises", "lesson_path"],
}


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {"type": "string"},
        "previous_work": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "approach": {"type": "string"},
        "status_items": {"type": "array", "items": {"type": "string"}},
        "work_plan": {"type": "array", "items": {"type": "string"}},
        "implementation_flow": {"type": "array", "items": {"type": "string"}},
        "design_decisions": {"type": "array", "items": {"type": "string"}},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "error_cases": {"type": "array", "items": {"type": "string"}},
        "scope_notes": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "array", "items": {"type": "string"}},
        "learning_references": {"type": "array", "items": LEARNING_REFERENCE_SCHEMA},
    },
    "required": ["situation", "previous_work", "next_steps", "risks", "approach", "status_items", "work_plan", "implementation_flow", "design_decisions", "changed_files", "error_cases", "scope_notes", "verification", "learning_references"],
}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {"type": "string"},
        "previous_work": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["situation", "previous_work", "next_steps", "risks"],
}

DRAFT_COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "response_items": {"type": "array", "items": {"type": "string"}},
        "files": {"type": "array", "items": {"type": "string"}},
        "detail_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["response_items", "files", "detail_items"],
}

# Study material only. The ticket is evidence for the lesson, never its subject,
# so this schema deliberately carries no ticket-status fields.
LESSONS_SCHEMA = {
    "type": "object",
    "properties": {"learning_references": {"type": "array", "items": LEARNING_REFERENCE_SCHEMA}},
    "required": ["learning_references"],
}


DEFAULT_LLM_URL = "http://localhost:8080"


class LocalLLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerInfo:
    """What the local server reports about itself."""

    model: str
    context_tokens: int
    slots: int
    build: str = ""

    @property
    def model_label(self) -> str:
        """Short name for the UI; llama.cpp reports a full filesystem path."""
        name = self.model.replace("\\", "/").rsplit("/", 1)[-1]
        return name[:-5] if name.casefold().endswith(".gguf") else name


# Rough bytes-per-token used only to keep a prompt inside the server's window.
# Japanese ticket text is denser than English, so this stays deliberately low.
CHARS_PER_TOKEN = 2.5

# Share of the context window the prompt may use, leaving room for the reply.
PROMPT_CONTEXT_SHARE = 0.6


class LocalLLMClient:
    """Client for a llama.cpp server's OpenAI-compatible endpoints.

    Structured output uses `response_format: json_schema`, which llama.cpp
    enforces with a grammar, so the reply is valid JSON or the request fails.
    """

    def __init__(
        self,
        base_url: str,
        model: str = "",
        timeout: int = 600,
        session=None,
        thinking: bool = False,
        context_tokens: int = 32768,
        max_tokens: int = 8192,
    ):
        self.base_url = str(base_url or "").rstrip("/")
        self.model = str(model or "").strip()
        self.timeout = max(10, int(timeout))
        self.session = session or requests.Session()
        self.thinking = bool(thinking)
        self.context_tokens = max(2048, int(context_tokens or 32768))
        self.max_tokens = max(256, int(max_tokens or 8192))

    # ------------------------------------------------------------- discovery
    def health(self) -> ServerInfo:
        """Confirm the server answers and report the loaded model and window."""
        props = self._get_json("/props", "server properties")
        model = str(props.get("model_path") or props.get("model_alias") or "")
        settings = props.get("default_generation_settings")
        context = 0
        if isinstance(settings, dict):
            try:
                context = int(settings.get("n_ctx") or 0)
            except (TypeError, ValueError):
                context = 0
        try:
            slots = int(props.get("total_slots") or 0)
        except (TypeError, ValueError):
            slots = 0
        if not model:
            models = self.models()
            model = models[0] if models else ""
        return ServerInfo(model=model, context_tokens=context, slots=slots, build=str(props.get("build_info") or ""))

    def models(self) -> list[str]:
        body = self._get_json("/v1/models", "model list")
        entries = body.get("data") if isinstance(body.get("data"), list) else body.get("models")
        names = []
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("id") or item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def _get_json(self, path: str, what: str) -> dict:
        if not self.base_url:
            raise LocalLLMError("Set the local AI server URL in Settings > Local AI.")
        try:
            response = self.session.get(f"{self.base_url}{path}", timeout=min(self.timeout, 15))
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LocalLLMError(f"Could not read the {what} from {self.base_url}: {exc}") from exc
        if not isinstance(body, dict):
            raise LocalLLMError(f"The server returned an unexpected {what}.")
        return body

    # ----------------------------------------------------------------- tasks
    def analyze(self, ticket: dict, note_text: str = "", comments: list[dict] | None = None, patches: list[dict] | None = None, **kwargs) -> dict:
        context = self._context(ticket, note_text, comments or [], patches or [], self.context_tokens)
        system = (
            "You are a careful software ticket analyst and technical note curator. Return only JSON matching the supplied schema. "
            "Write the situation, previous work, next steps, risks, and all generated prose in concise English. Preserve Japanese identifiers and quoted requirements when useful. "
            "Use only facts present in the ticket, comments, existing note, or the recorded source-control patches. Never invent completed work, files, test results, or decisions. "
            "When evidence is missing, say it is unknown or requires confirmation. Learning references must become standalone educational programming lessons, not ticket summaries. Generalize beyond the observed implementation and clearly label examples that are illustrative. "
            "For status_items use tab-separated strings: requirement<TAB>current handling<TAB>status. For work_plan use '[x] task' only when completion is evidenced, otherwise '[ ] task'. "
            "For design_decisions use decision<TAB>rationale; changed_files use file<TAB>change<TAB>reason; error_cases use scenario<TAB>handling. "
            "Base changed_files on the SOURCE-CONTROL PATCHES section when it is present; do not guess file names that are not listed there. "
            "For each learning_references item provide why_it_matters, prerequisites, at least two examples (one minimal and one realistic but generalized), when_to_use, when_not_to_use, and a short lesson_path. Include one or more alternatives when a similar result can be achieved another way; explain when to choose it plus concrete pros and cons. Add small practice exercises. Do not copy only the ticket's code. "
            "Use safe folder categories such as 01-Core-Concepts, 02-JavaScript, 03-CSharp, 04-WebForms, 05-CSS-and-UI, 06-Quality, or another short technology category. "
            "Return zero to three genuinely useful learning references. Keep each lesson focused and the total output under 2200 words."
        )
        result = self._chat(context, system, ANALYSIS_SCHEMA, "ticket_analysis", self.max_tokens, **kwargs)
        return normalize_analysis(result)

    def brief(self, ticket: dict, note_text: str = "", comments: list[dict] | None = None, patches: list[dict] | None = None, **kwargs) -> dict:
        context = self._context(ticket, note_text, comments or [], patches or [], self.context_tokens)
        system = (
            "You are a careful software ticket analyst. Return only JSON matching the supplied schema. "
            "Write concise English. Preserve Japanese identifiers or quoted requirements where useful. "
            "Use only facts in the ticket, comments, existing note, and recorded source-control patches. Never invent completed work or test results. "
            "Separate the current situation, confirmed previous work, concrete next steps, and risks or unknowns."
        )
        result = self._chat(context, system, BRIEF_SCHEMA, "ticket_brief", min(self.max_tokens, 2048), **kwargs)
        return {
            "situation": str(result.get("situation", "")).strip()[:12000],
            "previous_work": _strings(result.get("previous_work")), "next_steps": _strings(result.get("next_steps")),
            "risks": _strings(result.get("risks")), "_mode": "brief",
        }

    def draft_comment(self, ticket: dict, note_text: str = "", comments: list[dict] | None = None, patches: list[dict] | None = None, **kwargs) -> dict:
        result = self._chat(
            self._context(ticket, note_text, comments or [], patches or [], self.context_tokens),
            "あなたはソフトウェア開発チケットのBacklogコメントを作成します。JSONの各配列要素は、簡潔で自然な日本語にしてください。"
            "response_itemsには対応結果の要約、filesには実施ファイル、detail_itemsには実装の詳細を入れてください。"
            "見出しや箇条書き記号は配列要素へ入れないでください。アプリ側で所定の形式に整形します。"
            "SVNパッチのdiffを最優先の実装根拠として使用し、filesにはSOURCE-CONTROL PATCHESに実在するパスだけをそのまま記載してください。"
            "チケット、コメント、既存ノート、検証済みSVNパッチにない完了作業、仕様、ファイル、テスト結果は推測しないでください。"
            "実装根拠が不足する内容は断定せず、確認が必要であることを明示してください。各配列は重複を避け、レビューしやすい粒度にしてください。"
            "返答は指定されたJSONスキーマだけにしてください。",
            DRAFT_COMMENT_SCHEMA,
            "draft_comment",
            min(self.max_tokens, 3072),
            **kwargs,
        )
        response_items = _comment_items(result.get("response_items"), 8)
        model_files = _comment_items(result.get("files"), 80)
        recorded_files = []
        for patch in patches or []:
            for file_value in patch.get("files", []) if isinstance(patch, dict) else []:
                path = str(file_value.get("path") or "").strip() if isinstance(file_value, dict) else ""
                if path and path not in recorded_files:
                    recorded_files.append(path)
        # A model never gets authority to add file names. When a patch exists,
        # its parsed Index paths are the canonical implementation locations.
        files = recorded_files or model_files
        detail_items = _comment_items(result.get("detail_items"), 30)
        comment = format_backlog_comment(response_items, files, detail_items)
        return {"comment": comment[:12000], "_mode": "draft_comment"}

    def lessons(self, ticket: dict, note_text: str = "", comments: list[dict] | None = None, patches: list[dict] | None = None, **kwargs) -> dict:
        """Standalone study material drawn from the ticket, without a note rewrite."""
        context = self._context(ticket, note_text, comments or [], patches or [], self.context_tokens)
        system = (
            "You are a programming educator. Return only JSON matching the supplied schema. Write in clear English. "
            "Use the ticket, its comments, the existing note, and the recorded source-control patches only to decide WHICH techniques are worth teaching. "
            "The output is study material, not a ticket report: never describe this ticket's status, deadlines, or remaining work, and never name the customer or the ticket key in the lesson prose. "
            "Generalize every technique beyond the observed implementation so the lesson stays useful on an unrelated project, and clearly label examples that are illustrative rather than copied. "
            "For each learning_references item provide why_it_matters, prerequisites, at least two examples (one minimal and one realistic but generalized), when_to_use, when_not_to_use, and a short lesson_path. "
            "Include one or more alternatives when a similar result can be achieved another way; explain when to choose it plus concrete pros and cons. Add small practice exercises the reader can do without this codebase. "
            "Use safe folder categories such as 01-Core-Concepts, 02-JavaScript, 03-CSharp, 04-WebForms, 05-CSS-and-UI, 06-Quality, or another short technology category. "
            "Return one to three genuinely useful lessons. Keep each lesson focused and the total output under 2200 words."
        )
        result = self._chat(context, system, LESSONS_SCHEMA, "ticket_lessons", self.max_tokens, **kwargs)
        if not isinstance(result, dict):
            raise LocalLLMError("The local AI server returned a response that was not a JSON object.")
        references = normalize_learning_references(result.get("learning_references"))
        if not references:
            raise LocalLLMError("The local AI server did not return any usable study material for this ticket.")
        return {"learning_references": references, "_mode": "lessons"}

    # ------------------------------------------------------------- transport
    def _chat(
        self,
        context: str,
        system: str,
        schema: dict,
        schema_name: str,
        max_tokens: int,
        on_progress=None,
        should_cancel=None,
    ) -> dict:
        if not self.base_url:
            raise LocalLLMError("Set the local AI server URL in Settings > Local AI.")
        budget = max(256, int(max_tokens))
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "Use the following ticket evidence to complete the requested task.\n\n" + context},
            ],
            "temperature": 0.15,
            "top_p": 0.9,
            "max_tokens": budget,
            "stream": True,
            "cache_prompt": True,
            # llama.cpp turns the schema into a grammar, so the reply cannot drift.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            # Qwen-style thinking spends the same budget as the answer, so it is
            # off unless the user asks for it in Settings.
            "chat_template_kwargs": {"enable_thinking": self.thinking},
        }
        if self.model:
            payload["model"] = self.model
        content, finish_reason = self._stream(payload, on_progress, should_cancel)
        return self._decode(content, finish_reason, budget)

    def _stream(self, payload: dict, on_progress, should_cancel) -> tuple[str, str]:
        """Read the SSE reply, reporting progress and honouring cancellation."""
        started = time.monotonic()
        chunks: list[str] = []
        finish_reason = ""
        try:
            response = self.session.post(
                f"{self.base_url}/v1/chat/completions", json=payload, timeout=self.timeout, stream=True,
            )
            self._raise_for_status(response)
            # `requests` may choose ISO-8859-1 when an SSE response omits a
            # charset. llama.cpp emits UTF-8 JSON, so letting requests guess
            # turns Japanese into mojibake such as `è¦...`. Keep the wire bytes
            # and decode the protocol explicitly.
            for wire_line in response.iter_lines(decode_unicode=False):
                try:
                    raw = wire_line.decode("utf-8") if isinstance(wire_line, bytes) else str(wire_line)
                except UnicodeDecodeError as exc:
                    response.close()
                    raise LocalLLMError("The local AI server returned a stream that was not valid UTF-8.") from exc
                if should_cancel and should_cancel():
                    response.close()
                    raise LocalLLMError("The generation was cancelled.")
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in event.get("choices", []) if isinstance(event, dict) else []:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    piece = delta.get("content") or ""
                    if piece:
                        chunks.append(str(piece))
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                if on_progress and len(chunks) % 16 == 0:
                    on_progress(len(chunks), time.monotonic() - started)
        except requests.Timeout as exc:
            raise LocalLLMError(
                f"The local AI server did not finish within {self.timeout} seconds. "
                "Increase the timeout in Settings > Local AI, or lower the reply budget."
            ) from exc
        except requests.RequestException as exc:
            raise LocalLLMError(f"Could not reach the local AI server at {self.base_url}: {exc}") from exc
        if on_progress:
            on_progress(len(chunks), time.monotonic() - started)
        return "".join(chunks), finish_reason

    @staticmethod
    def _raise_for_status(response) -> None:
        if response.status_code < 400:
            return
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                error = body.get("error")
                detail = str(error.get("message") if isinstance(error, dict) else error or "")
        except ValueError:
            detail = str(getattr(response, "text", "") or "")
        detail = detail.strip()[:600]
        if "context" in detail.casefold() and "exceed" in detail.casefold():
            raise LocalLLMError(
                f"The prompt is larger than the server's context window: {detail}\n\n"
                "Lower the context size in Settings > Local AI so the app trims the ticket text further."
            )
        raise LocalLLMError(f"The local AI server rejected the request ({response.status_code}): {detail or 'no detail'}")

    def _decode(self, content: str, finish_reason: str, budget: int) -> dict:
        cleaned = self._strip_fence(self._strip_reasoning(content)).strip()
        if not cleaned:
            if finish_reason == "length":
                raise LocalLLMError(
                    f"The model used its entire {budget}-token reply budget without producing an answer. "
                    "Raise the reply budget in Settings > Local AI, or turn model thinking off."
                )
            raise LocalLLMError(f"The local AI server returned no content (finish reason: {finish_reason or 'unknown'}).")
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            if finish_reason == "length":
                raise LocalLLMError(
                    f"The reply was cut off after {budget} tokens, so the JSON is incomplete. "
                    "Raise the reply budget in Settings > Local AI."
                ) from exc
            raise LocalLLMError(f"The local AI server returned invalid JSON: {exc}") from exc
        if not isinstance(result, dict):
            raise LocalLLMError("The local AI server returned a response that was not a JSON object.")
        return result

    @staticmethod
    def _strip_reasoning(value: str) -> str:
        """Drop <think> blocks.

        llama.cpp normally splits these into `reasoning_content`, but a model or
        template that inlines them would otherwise break JSON parsing.
        """
        return re.sub(r"<think>.*?</think>", "", str(value or ""), flags=re.DOTALL | re.IGNORECASE)

    @staticmethod
    def _strip_fence(value: str) -> str:
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value.strip(), flags=re.DOTALL | re.IGNORECASE)
        return match.group(1) if match else value

    # --------------------------------------------------------------- context
    @staticmethod
    def _context(ticket: dict, note_text: str, comments: list[dict], patches: list[dict], context_tokens: int) -> str:
        """Build the prompt, trimmed to fit the server's context window.

        The note is trimmed first and the comment history second, because the
        ticket itself and the recorded patches are the parts the model must not
        guess about.
        """
        selected = {
            "key": issue_key(ticket), "title": ticket.get("summary", ""), "description": ticket.get("description", ""),
            "project": project_name(ticket), "status": (ticket.get("status") or {}).get("name", ""),
            "priority": (ticket.get("priority") or {}).get("name", ""), "assignee": (ticket.get("assignee") or {}).get("name", ""),
            "due_date": ticket.get("dueDate"), "updated": ticket.get("updated"), "custom_fields": ticket.get("customFields", []),
        }
        ticket_block = json.dumps(selected, ensure_ascii=False, indent=2, default=str)
        allowance = int(max(2048, context_tokens) * PROMPT_CONTEXT_SHARE * CHARS_PER_TOKEN)
        patch_block = json.dumps(patches[:20], ensure_ascii=False, indent=2, default=str) if patches else ""
        patch_budget = max(3000, int(allowance * 0.58))
        if len(patch_block) > patch_budget:
            patch_block = patch_block[:patch_budget] + "\n[SVN diff truncated to fit the model context window.]"
        remaining = max(2000, allowance - len(ticket_block) - len(patch_block))
        comment_budget = int(remaining * 0.6)
        note_budget = max(1000, remaining - comment_budget)

        clean_comments = []
        used = 0
        for comment in comments[:30]:
            body = str(comment.get("content") or "")[:3000]
            if used + len(body) > comment_budget:
                break
            used += len(body)
            clean_comments.append({
                "author": (comment.get("createdUser") or {}).get("name", ""),
                "created": comment.get("created", ""), "updated": comment.get("updated", ""),
                "content": body, "change_log": comment.get("changeLog"),
            })
        dropped = len(comments) - len(clean_comments)

        note = str(note_text or "")
        truncated_note = note[:note_budget]
        if len(note) > len(truncated_note):
            truncated_note += "\n\n[Note truncated to fit the model context window.]"

        sections = ["TICKET\n" + ticket_block]
        if patch_block:
            sections.append("SOURCE-CONTROL PATCHES (verified files and diff evidence for this ticket)\n" + patch_block)
        header = "BACKLOG COMMENTS (newest first)"
        if dropped > 0:
            header += f" — {dropped} older comment(s) omitted to fit the context window"
        sections.append(header + "\n" + json.dumps(clean_comments, ensure_ascii=False, indent=2, default=str))
        sections.append("EXISTING OBSIDIAN NOTE\n" + (truncated_note or "No existing note."))
        return "\n\n".join(sections)


def _strings(value: Any, limit: int = 50) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:4000] for item in value[:limit] if str(item).strip()]


def _comment_items(value: Any, limit: int) -> list[str]:
    """Normalize model bullets without allowing it to duplicate our template."""
    items = []
    for item in _strings(value, limit):
        clean = re.sub(r"^\s*(?:[・●•*-]|\d+[.)])\s*", "", item).strip()
        if clean and clean not in items:
            items.append(clean)
    return items


def format_backlog_comment(response_items: list[str], files: list[str], detail_items: list[str]) -> str:
    """Render the fixed Japanese format used for reviewable Backlog comments."""
    response_items = response_items or ["対応内容をSVNパッチから特定できませんでした（要確認）"]
    files = files or ["SVNパッチから実施箇所を確認できませんでした（要確認）"]
    detail_items = detail_items or ["実装詳細をSVNパッチから特定できませんでした（要確認）"]

    def section(title: str, values: list[str]) -> str:
        return title + "\n" + "\n".join(f"・{value}" for value in values)

    return "\n".join((
        section("【対応内容】", response_items),
        section("【実施箇所】", files),
        section("【詳細】", detail_items),
    ))


def _rows(value: Any, fields: tuple[str, ...], limit: int = 50) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value[:limit]:
        if isinstance(item, dict):
            rows.append({field: (bool(item.get(field)) if field == "done" else str(item.get(field, "")).strip()[:4000]) for field in fields})
        elif isinstance(item, str):
            parts = [part.strip() for part in re.split(r"\t|\s+\|\s+", item, maxsplit=len(fields) - 1)]
            parts.extend([""] * (len(fields) - len(parts)))
            rows.append({field: parts[index][:4000] for index, field in enumerate(fields)})
    return rows


def _work_plan(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value[:50]:
        if isinstance(item, dict):
            rows.append({"task": str(item.get("task", "")).strip()[:4000], "done": bool(item.get("done"))})
        elif isinstance(item, str):
            match = re.match(r"^\s*\[([xX ])\]\s*(.*)$", item)
            rows.append({"task": (match.group(2) if match else item).strip()[:4000], "done": bool(match and match.group(1).casefold() == "x")})
    return [row for row in rows if row["task"]]


def _learning_examples(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    examples = []
    for raw in value[:5]:
        if isinstance(raw, dict):
            example = {
                "title": str(raw.get("title", "Example")).strip()[:500],
                "code": str(raw.get("code", "")).strip()[:16000],
                "explanation": str(raw.get("explanation", "")).strip()[:8000],
            }
        elif isinstance(raw, str):
            parts = [part.strip() for part in raw.split("\t", 2)]
            parts.extend([""] * (3 - len(parts)))
            example = {"title": parts[0][:500] or "Example", "code": parts[1][:16000], "explanation": parts[2][:8000]}
        else:
            continue
        if example["code"] or example["explanation"]:
            examples.append(example)
    return examples


def _learning_alternatives(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    alternatives = []
    for raw in value[:4]:
        if isinstance(raw, dict):
            alternative = {
                "name": str(raw.get("name", "Alternative")).strip()[:500],
                "when_to_choose": str(raw.get("when_to_choose", "")).strip()[:5000],
                "pros": _strings(raw.get("pros"), 10),
                "cons": _strings(raw.get("cons"), 10),
                "example": str(raw.get("example", "")).strip()[:8000],
            }
        elif isinstance(raw, str):
            parts = [part.strip() for part in raw.split("\t", 4)]
            parts.extend([""] * (5 - len(parts)))
            alternative = {
                "name": parts[0][:500] or "Alternative",
                "when_to_choose": parts[1][:5000],
                "pros": [part.strip() for part in parts[2].split(";") if part.strip()][:10],
                "cons": [part.strip() for part in parts[3].split(";") if part.strip()][:10],
                "example": parts[4][:8000],
            }
        else:
            continue
        alternatives.append(alternative)
    return alternatives


def normalize_learning_references(value: Any) -> list[dict]:
    """Coerce the model's lesson list into the shape the Obsidian writer expects.

    Shared by the note template and the study-material task so a lesson written
    from either entry point lands in the vault with identical fields.
    """
    learning = []
    raw_learning = value if isinstance(value, list) else []
    for raw in raw_learning[:3]:
        if isinstance(raw, dict):
            item = {
                "title": str(raw.get("title", "")).strip()[:500], "category": str(raw.get("category", "")).strip(),
                "slug": str(raw.get("slug", "")).strip(), "summary": str(raw.get("summary", "")).strip()[:12000],
                "principles": _strings(raw.get("principles"), 20), "example": str(raw.get("example", "")).strip()[:12000],
                "when_to_use": _strings(raw.get("when_to_use"), 20),
                "why_it_matters": str(raw.get("why_it_matters", "")).strip()[:12000],
                "when_not_to_use": _strings(raw.get("when_not_to_use"), 20),
                "prerequisites": _strings(raw.get("prerequisites"), 20),
                "examples": _learning_examples(raw.get("examples")),
                "alternatives": _learning_alternatives(raw.get("alternatives")),
                "exercises": _strings(raw.get("exercises"), 20),
                "lesson_path": _strings(raw.get("lesson_path"), 20),
            }
        elif isinstance(raw, str):
            parts = [part.strip() for part in raw.split("\t", 5)]
            parts.extend([""] * (6 - len(parts)))
            category, title, summary, principles, example, when_to_use = parts
            item = {
                "title": title[:500], "category": category, "slug": "", "summary": summary[:12000],
                "principles": [part.strip() for part in principles.split(";") if part.strip()][:20],
                "example": example[:12000], "when_to_use": [part.strip() for part in when_to_use.split(";") if part.strip()][:20],
                "why_it_matters": "",
                "when_not_to_use": [], "prerequisites": [], "examples": [], "alternatives": [], "exercises": [], "lesson_path": [],
            }
        else:
            continue
        item["slug"] = re.sub(r"[^A-Za-z0-9-]+", "-", str(item.get("slug") or item.get("title") or "reference")).strip("-")[:80] or "reference"
        item["category"] = re.sub(r"[^A-Za-z0-9-]+", "-", str(item.get("category") or "01-Core-Concepts")).strip("-")[:80] or "01-Core-Concepts"
        learning.append(item)
    return learning


def normalize_analysis(value: Any) -> dict:
    if not isinstance(value, dict):
        raise LocalLLMError("The local AI server returned a response that was not a JSON object.")
    learning = normalize_learning_references(value.get("learning_references"))
    return {
        "situation": str(value.get("situation", "")).strip()[:12000],
        "previous_work": _strings(value.get("previous_work")), "next_steps": _strings(value.get("next_steps")), "risks": _strings(value.get("risks")),
        "approach": str(value.get("approach", "")).strip()[:12000],
        "status_items": _rows(value.get("status_items"), ("requirement", "current", "status")),
        "work_plan": _work_plan(value.get("work_plan")),
        "implementation_flow": _strings(value.get("implementation_flow")),
        "design_decisions": _rows(value.get("design_decisions"), ("decision", "rationale")),
        "changed_files": _rows(value.get("changed_files"), ("file", "change", "reason")),
        "error_cases": _rows(value.get("error_cases"), ("scenario", "handling")),
        "scope_notes": _strings(value.get("scope_notes")), "verification": _strings(value.get("verification")),
        "learning_references": learning,
    }


def ticket_patch_context(config: dict, ticket_key: str, limit: int = 20, include_diff: bool = False) -> list[dict]:
    """Summarise the ticket's shelved patches for the prompt.

    This is what stops the model from guessing file names: it can only report
    changed files that a real patch actually contains.
    """
    key = str(ticket_key or "").strip().upper()
    if not key:
        return []
    try:
        store = PatchStore(config.get("svn_patch_root", ""))
        artifacts, _unreadable = store.scan(key)
    except Exception:
        # Source control is optional context; never fail an analysis over it.
        return []
    summaries = []
    remaining_diff_chars = 70000
    for artifact in artifacts:
        try:
            store.verify(artifact)
            content = Path(artifact.patch_file).read_text(encoding="utf-8", errors="replace")
            files = [
                {"path": item.path, "change": item.operation, "added_lines": item.added_lines, "removed_lines": item.removed_lines}
                for item in parse_patch_summary(content)[:60]
            ]
        except Exception:
            # A corrupt or missing patch must never become AI evidence. The UI's
            # Patch Shelf reports integrity failures and provides recovery paths.
            continue
        summary = {
            "patch": Path(artifact.patch_file).name,
            "created": artifact.created_at,
            "state": artifact.state,
            "base_revision": artifact.base_revision,
            "integrity": "verified",
            "files": files,
        }
        if include_diff and remaining_diff_chars > 0:
            excerpt = content[:remaining_diff_chars]
            remaining_diff_chars -= len(excerpt)
            if len(excerpt) < len(content):
                excerpt += "\n[Patch diff truncated.]"
            summary["diff"] = excerpt
        summaries.append(summary)
        if len(summaries) >= max(1, int(limit)):
            break
    return summaries


class TicketAnalysisThread(QThread):
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, config: dict, ticket: dict, note_text: str = "", api_key: str = "", mode: str = "brief", comments: list[dict] | None = None):
        super().__init__()
        self.config = dict(config)
        self.ticket = dict(ticket)
        self.note_text = note_text
        self.api_key = api_key
        self.mode = mode
        self.comments = comments
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _report(self, pieces: int, elapsed: float) -> None:
        self.progress.emit(f"Generating… {pieces} chunk(s) in {elapsed:0.0f}s")

    def run(self) -> None:
        try:
            comments = list(self.comments or [])
            if self.comments is None and self.api_key:
                self.progress.emit("Loading Backlog comments…")
                try:
                    backlog = BacklogClient(self.api_key, self.config.get("backlog_base_url", "https://mirax.backlog.com/api/v2"), self.config.get("backlog_assignee_id"), False)
                    comments = backlog.comments(issue_key(self.ticket))
                except BacklogError:
                    comments = []
            if self._cancelled:
                self.failed.emit("The generation was cancelled.")
                return
            self.progress.emit("Collecting source-control context…")
            patches = ticket_patch_context(
                self.config,
                issue_key(self.ticket),
                limit=1 if self.mode == "draft_comment" else 20,
                include_diff=self.mode == "draft_comment",
            )
            if self.mode == "draft_comment" and not patches:
                raise LocalLLMError(
                    "No verified SVN patch is available for this ticket. Inspect or recreate its newest patch in Source Control first."
                )
            client = LocalLLMClient(
                self.config.get("local_llm_url", DEFAULT_LLM_URL),
                self.config.get("local_llm_model", ""),
                self.config.get("local_llm_timeout_seconds", 600),
                thinking=self.config.get("local_llm_thinking", False),
                context_tokens=self.config.get("local_llm_context_tokens", 32768),
                max_tokens=self.config.get("local_llm_max_tokens", 8192),
            )
            self.progress.emit("Waiting for the local AI server…")
            hooks = {"on_progress": self._report, "should_cancel": lambda: self._cancelled}
            if self.mode == "note":
                result = client.analyze(self.ticket, self.note_text, comments, patches, **hooks)
                result["_mode"] = "note"
            elif self.mode == "lessons":
                result = client.lessons(self.ticket, self.note_text, comments, patches, **hooks)
            elif self.mode == "draft_comment":
                result = client.draft_comment(self.ticket, self.note_text, comments, patches, **hooks)
            else:
                result = client.brief(self.ticket, self.note_text, comments, patches, **hooks)
            result["_patch_count"] = len(patches)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
