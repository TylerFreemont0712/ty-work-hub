"""Optional live check against the configured llama.cpp server.

Skips cleanly when the server is unreachable, so it is safe to run anywhere:

  $env:QT_QPA_PLATFORM = "offscreen"
  python -m tests.llm_integration
"""
from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from config import load_config
from modules.patch_store import PatchStore
from modules.svn_models import SvnInfo
from services.local_llm import (
    DEFAULT_LLM_URL, LocalLLMClient, LocalLLMError, TicketAnalysisThread,
)


TICKET = {
    "key": "DEV-LIVE-1",
    "summary": "Video controller state mismatch at in-video pause points",
    "description": "The controller shows the video as playing while playback is actually paused.",
    "project": {"name": "Integration"},
    "status": {"name": "処理中"},
    "priority": {"name": "High"},
}

COMMENTS = [{
    "createdUser": {"name": "Reviewer"},
    "created": "2026-08-20T10:00:00Z",
    "content": "Reproduced in Chrome. The controller icon stays in the playing state after an automatic pause.",
}]


def drain(app: QApplication, worker: QThread, timeout_seconds: int = 600) -> None:
    deadline = time.monotonic() + timeout_seconds
    while worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        QThread.msleep(20)
    app.processEvents()


def run_thread(app: QApplication, config: dict, mode: str, cancel_after: float = 0.0) -> tuple[dict, str, list[str]]:
    results: dict = {}
    failure: list[str] = []
    progress: list[str] = []
    worker = TicketAnalysisThread(config, TICKET, "Existing note: player events were inspected.", "", mode, COMMENTS)
    worker.completed.connect(results.update)
    worker.failed.connect(failure.append)
    worker.progress.connect(progress.append)
    worker.start()
    if cancel_after:
        started = time.monotonic()
        while worker.isRunning() and time.monotonic() - started < cancel_after:
            app.processEvents()
            QThread.msleep(10)
        worker.cancel()
    drain(app, worker)
    return results, (failure[0] if failure else ""), progress


def main():
    app = QApplication.instance() or QApplication([])
    config = load_config()
    url = config.get("local_llm_url") or DEFAULT_LLM_URL
    probe = LocalLLMClient(url, timeout=10)
    try:
        info = probe.health()
    except LocalLLMError as exc:
        print(f"llm integration skipped: {url} is not reachable ({exc})")
        return

    assert info.context_tokens > 0, "The server must report a context window"
    print(f"server: {info.model_label} | {info.context_tokens} token context | {info.slots} slot(s)")
    models = probe.models()
    assert models, "The server must report at least one model"

    config = {**config, "local_llm_url": url, "local_llm_timeout_seconds": 600,
              "local_llm_context_tokens": info.context_tokens or 32768}
    shelf = TemporaryDirectory()
    patch_root = Path(shelf.name)
    PatchStore(patch_root).create(
        TICKET,
        "Integration",
        patch_root,
        SvnInfo(
            target=str(patch_root), working_copy_root=str(patch_root),
            url="https://svn.example.test/repos/app/trunk",
            repository_root="https://svn.example.test/repos/app",
            repository_uuid="integration", revision="42",
        ),
        (
            b"Index: src/player/controller.js\n"
            b"--- src/player/controller.js\t(revision 42)\n"
            b"+++ src/player/controller.js\t(working copy)\n"
            b"@@ -1 +1 @@\n-playing = true;\n+playing = !video.paused;\n"
        ),
        ["src/player/controller.js"],
    )
    config["svn_patch_root"] = str(patch_root)

    brief, error, progress = run_thread(app, config, "brief")
    assert not error, f"brief failed: {error}"
    assert brief["_mode"] == "brief" and brief["situation"].strip(), "The brief must describe the situation"
    assert brief["next_steps"], "The brief must propose next steps"
    assert progress, "The worker must report progress while generating"

    comment, error, _ = run_thread(app, config, "draft_comment")
    assert not error, f"draft_comment failed: {error}"
    assert comment["_mode"] == "draft_comment" and comment["comment"].strip(), "A comment draft must have content"
    assert all(heading in comment["comment"] for heading in ("【対応内容】", "【実施箇所】", "【詳細】"))
    assert "src/player/controller.js" in comment["comment"]

    analysis, error, _ = run_thread(app, config, "note")
    assert not error, f"note failed: {error}"
    assert analysis["_mode"] == "note"
    for field in ("situation", "approach"):
        assert isinstance(analysis[field], str)
    for field in ("status_items", "work_plan", "implementation_flow", "verification"):
        assert isinstance(analysis[field], list), f"{field} must survive normalization"
    assert all(isinstance(row, dict) and "task" in row for row in analysis["work_plan"])

    lessons, error, _ = run_thread(app, config, "lessons")
    assert not error, f"lessons failed: {error}"
    assert lessons["_mode"] == "lessons"
    references = lessons["learning_references"]
    assert references, "Study material must return at least one lesson"
    for reference in references:
        assert reference["title"].strip() and reference["summary"].strip()
        assert reference["slug"] and reference["category"], "The vault path fields must be filled"
        assert reference["examples"], "A lesson needs at least one worked example"
        for example in reference["examples"]:
            assert example["title"] and example["explanation"]
        # The lesson is study material, not a status report on the ticket.
        assert TICKET["key"] not in reference["summary"]
    # Study material carries no ticket-status fields; that is the note template's job.
    assert not {"status_items", "work_plan", "changed_files", "verification"} & set(lessons)

    # Cancelling mid-flight must stop the stream rather than run to completion.
    cancelled, error, _ = run_thread(app, config, "note", cancel_after=1.5)
    assert not cancelled, "A cancelled generation must not deliver a result"
    assert "cancel" in error.casefold(), f"Expected a cancellation message, got: {error}"

    # A reply budget far too small must explain itself instead of failing on JSON.
    starved = {**config, "local_llm_max_tokens": 512}
    _, error, _ = run_thread(app, starved, "note")
    assert not error or "budget" in error.casefold() or "cut off" in error.casefold(), (
        f"A starved budget should be explained clearly, got: {error}"
    )

    shelf.cleanup()
    print("llm integration ok")


if __name__ == "__main__":
    main()
