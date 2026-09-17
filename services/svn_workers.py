"""Background Qt worker for SVN inspection and patch workflows."""
from __future__ import annotations

from pathlib import Path
import os

from PyQt6.QtCore import QThread, pyqtSignal

from modules.patch_store import PatchStore
from modules.svn_models import SvnChange
from modules.svn_service import (
    SvnCommandError,
    SvnService,
    SvnValidationError,
    parse_patch_summary,
    patch_output_has_conflicts,
    patch_output_has_unsupported_binary,
)


COMPARE_FILE_LIMIT = 12


def normalize_newlines(value: str) -> str:
    """Put both compare sides on the same line endings.

    `svn cat` hands back the stored bytes verbatim while a CRLF working copy
    reads back the same way, so without this a CRLF checkout would mark every
    line as changed.
    """
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


class SvnWorker(QThread):
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)
    progress = pyqtSignal(str)

    def __init__(self, action: str, config: dict, payload: dict | None = None, service: SvnService | None = None):
        super().__init__()
        self.action = str(action)
        self.config = dict(config)
        self.payload = dict(payload or {})
        self.service = service
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self.service:
            self.service.cancel_current()

    def run(self) -> None:
        try:
            self.service = self.service or SvnService(
                self.config.get("svn_executable", ""),
                self.config.get("tortoise_executable", ""),
                self.config.get("svn_timeout_seconds", 120),
            )
            result = self._dispatch()
            if self._cancelled:
                self.failed.emit(self.action, "The operation was cancelled.")
            else:
                self.completed.emit(self.action, result)
        except Exception as exc:
            self.failed.emit(self.action, str(exc))

    def _dispatch(self):
        root = self.payload.get("root", "")
        paths = self.payload.get("paths", [])
        if self.action == "list_patches":
            scope = str(self.payload.get("scope") or "all")
            self.progress.emit("Loading the patch shelf…" if scope == "all" else "Loading the ticket patch shelf…")
            store = PatchStore(self.config.get("svn_patch_root", ""))
            key = str(self.payload.get("ticket_key", "")) if scope == "ticket" else ""
            artifacts, unreadable = store.scan(key)
            return {"artifacts": artifacts, "unreadable": unreadable, "scope": scope, "root": str(store.root)}
        if self.action == "read_patch":
            self.progress.emit("Verifying the selected patch…")
            store = PatchStore(self.config.get("svn_patch_root", ""))
            artifact = store.load(self.payload.get("manifest_file", ""))
            store.verify(artifact)
            content = Path(artifact.patch_file).read_bytes().decode("utf-8", errors="replace")
            # Parse here so the Patch Changes tab never scans patch text on the GUI thread.
            return {"artifact": artifact, "content": content, "files": parse_patch_summary(content)}
        if self.action == "info":
            self.progress.emit("Reading working-copy information…")
            info, command = self.service.info(root)
            return {"info": info, "command": command}
        if self.action == "status":
            self.progress.emit("Checking local SVN changes…")
            info, info_command = self.service.info(root)
            changes, command = self.service.status(root)
            return {"info": info, "changes": changes, "command": command, "info_command": info_command}
        if self.action == "diff":
            self.progress.emit("Building the local diff…")
            return {"command": self.service.diff(root, paths)}
        if self.action == "compare_base":
            return self._compare_base()
        if self.action == "log":
            self.progress.emit("Loading SVN history…")
            return {"command": self.service.log(root, self.payload.get("limit", 50))}
        if self.action == "blame":
            self.progress.emit("Loading line history…")
            if len(paths) != 1:
                raise SvnValidationError("Select exactly one file for blame.")
            return {"command": self.service.blame(root, paths[0])}
        if self.action in {"create_patch", "shelve"}:
            return self._create_patch(shelve=self.action == "shelve")
        if self.action in {"patch_dry_run", "apply_patch"}:
            return self._apply_patch(dry_run=self.action == "patch_dry_run")
        if self.action == "update_patch":
            self.progress.emit("Saving the patch details…")
            store = PatchStore(self.config.get("svn_patch_root", ""))
            artifact = store.load(self.payload.get("manifest_file", ""))
            updated = store.update_details(
                artifact,
                label=self.payload.get("label", ""),
                group=self.payload.get("group", ""),
                note=self.payload.get("note", ""),
            )
            return {"artifact": updated}
        if self.action == "renumber_patches":
            self.progress.emit("Renumbering this ticket's patches…")
            store = PatchStore(self.config.get("svn_patch_root", ""))
            key = str(self.payload.get("ticket_key", ""))
            return {"ticket_key": key, "artifacts": store.renumber_ticket(key)}
        if self.action == "delete_patch":
            self.progress.emit("Deleting the selected patch…")
            store = PatchStore(self.config.get("svn_patch_root", ""))
            artifact = store.load(self.payload.get("manifest_file", ""))
            return {"artifact": artifact, "removed": store.delete(artifact)}
        if self.action == "revert":
            self.progress.emit("Reverting the selected paths…")
            return {"command": self.service.revert(root, paths, remove_added=True)}
        if self.action == "update":
            revision = str(self.payload.get("revision") or "HEAD")
            self.progress.emit(f"Updating the working copy to {revision}…")
            return {"command": self.service.update(root, revision), "revision": revision}
        if self.action == "changelist":
            name = str(self.payload.get("changelist") or "")
            self.progress.emit("Locking the selected paths…" if name else "Unlocking the selected paths…")
            return {"command": self.service.changelist(root, paths, name), "changelist": name}
        if self.action == "cleanup":
            self.progress.emit("Cleaning the working copy…")
            return {"command": self.service.cleanup(root)}
        if self.action == "resolve":
            self.progress.emit("Marking conflicts resolved…")
            return {"command": self.service.resolve(root, paths, self.payload.get("accept", "working"))}
        if self.action == "tortoise":
            command = str(self.payload.get("command") or "repostatus")
            return {"command": self.service.launch_tortoise(command, root, self.payload.get("path", ""))}
        raise SvnValidationError(f"Unsupported SVN action: {self.action}")

    def _compare_base(self) -> dict:
        """Collect the BASE and working-copy text of each selected file.

        The UI renders these side by side, so this returns per-file content
        rather than a unified diff. A file that is added locally, missing, or
        not decodable as text still produces a row explaining why.
        """
        root = self.payload.get("root", "")
        requested = [str(path) for path in self.payload.get("paths", []) if str(path).strip()]
        if not requested:
            raise SvnValidationError("Select one or more changed paths to compare with the base revision.")
        base_folder = Path(root).expanduser().resolve()
        files = []
        for index, relative in enumerate(requested[:COMPARE_FILE_LIMIT], 1):
            if self._cancelled:
                break
            self.progress.emit(f"Reading base revision {index}/{min(len(requested), COMPARE_FILE_LIMIT)}…")
            files.append(self._compare_one(base_folder, root, relative))
        self.progress.emit("Building the local diff…")
        return {
            "files": files,
            "command": self.service.diff(root, requested),
            "skipped": max(0, len(requested) - COMPARE_FILE_LIMIT),
        }

    def _compare_one(self, base_folder: Path, root: str, relative: str) -> dict:
        before, before_note = "", ""
        try:
            result = self.service.cat_base(root, relative)
            if result.ok:
                before = result.stdout
            else:
                before_note = "No base revision — this file is new in the working copy."
        except (SvnValidationError, SvnCommandError) as exc:
            before_note = f"Base revision unavailable: {exc}"
        after, after_note = "", ""
        target = (base_folder / relative).resolve(strict=False)
        try:
            after = target.read_bytes().decode("utf-8", errors="replace") if target.is_file() else ""
            if not target.is_file():
                after_note = "The file is not present in the working copy."
        except OSError as exc:
            after_note = f"Working-copy file unavailable: {exc}"
        return {
            "path": relative,
            "before": normalize_newlines(before),
            "after": normalize_newlines(after),
            "before_note": before_note,
            "after_note": after_note,
        }

    def _create_patch(self, shelve: bool) -> dict:
        root = self.payload.get("root", "")
        requested = [str(path) for path in self.payload.get("paths", []) if str(path).strip()]
        self.progress.emit("Inspecting the selected changes…")
        info, _ = self.service.info(root)
        changes, _ = self.service.status(root)
        selected = self._selected_patchable_changes(changes, requested)
        if not selected:
            raise SvnValidationError("No patchable versioned changes are selected. Unversioned and missing files are not included in SVN patches.")
        paths = [change.path for change in selected]
        self.progress.emit("Creating the ticket patch…")
        diff = self.service.diff(root, paths)
        if patch_output_has_unsupported_binary(diff.stdout):
            raise SvnValidationError("The selection contains binary content that an SVN text patch cannot restore. Nothing was reverted.")
        warnings = []
        unversioned = [change.path for change in changes if change.item_status == "unversioned"]
        if unversioned:
            warnings.append(f"{len(unversioned)} unversioned path(s) were not included in this patch.")
        store = PatchStore(self.config.get("svn_patch_root", ""))
        artifact = store.create(
            self.payload.get("ticket") or {},
            self.payload.get("profile_name", "Working copy"),
            root,
            info,
            diff.stdout_bytes or diff.stdout.encode("utf-8"),
            paths,
            scope="selected" if requested else "all",
            label=self.payload.get("label", ""),
            group=self.payload.get("group", ""),
            note=self.payload.get("note", ""),
            warnings=warnings,
        )
        if not shelve:
            return {"artifact": artifact, "command": diff, "changes": selected}
        self.progress.emit("Patch verified. Reverting the shelved paths…")
        store.verify(artifact)
        try:
            revert = self.service.revert(root, paths, remove_added=True)
        except SvnCommandError as exc:
            artifact = store.update_state(artifact, "revert_failed", str(exc))
            raise SvnValidationError(
                f"The patch was saved as {Path(artifact.patch_file).name}, but SVN could not fully revert the selected paths. "
                "The shelf is marked revert_failed; inspect the working copy before switching tickets."
            ) from exc
        artifact = store.update_state(artifact, "shelved", "Patch verified and selected paths reverted.")
        return {"artifact": artifact, "command": revert, "patch_command": diff, "changes": selected}

    def _apply_patch(self, dry_run: bool) -> dict:
        root = self.payload.get("root", "")
        store = PatchStore(self.config.get("svn_patch_root", ""))
        artifact = store.load(self.payload.get("manifest_file", ""))
        store.verify(artifact)
        configured_root = os.path.normcase(str(Path(root).expanduser().resolve()))
        recorded_root = os.path.normcase(str(Path(artifact.working_copy_root).expanduser().resolve()))
        if configured_root != recorded_root:
            raise SvnValidationError(
                "This patch belongs to a different working-copy root. Select the recorded profile before applying it.\n"
                f"Recorded: {artifact.working_copy_root}\nSelected: {Path(root).resolve()}"
            )
        info, _ = self.service.info(root)
        if artifact.repository_root and info.repository_root and artifact.repository_root != info.repository_root:
            raise SvnValidationError("The selected working copy belongs to a different SVN repository than this patch.")
        self.progress.emit("Previewing patch application…" if dry_run else "Applying the selected patch…")
        try:
            command = self.service.apply_patch(root, artifact.patch_file, dry_run=dry_run)
        except SvnCommandError as exc:
            if not dry_run:
                artifact = store.update_state(artifact, "apply_failed", str(exc))
            raise
        conflicts = patch_output_has_conflicts(command.stdout + "\n" + command.stderr)
        if not dry_run:
            state = "conflicted" if conflicts else "applied"
            message = "Patch applied with conflicts; inspect and resolve the listed files." if conflicts else "Patch applied successfully."
            artifact = store.update_state(artifact, state, message)
        return {"artifact": artifact, "command": command, "conflicts": conflicts, "info": info}

    @staticmethod
    def _selected_patchable_changes(changes: list[SvnChange], requested: list[str]) -> list[SvnChange]:
        by_path = {os.path.normcase(change.path): change for change in changes}
        if requested:
            selected = []
            unsupported = []
            for path in requested:
                change = by_path.get(os.path.normcase(path.replace("\\", "/")))
                if not change or not change.patchable:
                    unsupported.append(path)
                else:
                    selected.append(change)
            if unsupported:
                sample = ", ".join(unsupported[:3])
                raise SvnValidationError(f"These selected paths cannot be represented safely by an SVN text patch: {sample}")
            return selected
        return [change for change in changes if change.patchable]
