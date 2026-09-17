"""Atomic ticket-scoped storage for SVN patch artifacts and manifests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from modules.svn_models import PatchArtifact, SvnInfo
from modules.svn_service import SvnValidationError


SCHEMA_VERSION = 1


def safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(value or "")).strip().strip(".")
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned[:100] or fallback


class PatchStore:
    def __init__(self, root: str | Path):
        raw = str(root or "").strip()
        if not raw:
            raise SvnValidationError("Choose a patch shelf folder in Settings > Source Control.")
        self.root = Path(raw).expanduser().resolve(strict=False)

    def create(
        self,
        ticket: dict,
        profile_name: str,
        working_copy_root: str | Path,
        info: SvnInfo,
        patch_data: bytes,
        affected_paths: list[str],
        scope: str = "selected",
        label: str = "",
        group: str = "",
        note: str = "",
        warnings: list[str] | None = None,
    ) -> PatchArtifact:
        """Save one patch for a ticket.

        A ticket collects many patches over its life, so each one is numbered
        within the ticket and named after `label` rather than the ticket title.
        """
        ticket_key = str(ticket.get("key") or ticket.get("issueKey") or "MANUAL").strip().upper() or "MANUAL"
        project = ticket.get("project") or {}
        project_name = str(project.get("name") or project.get("projectKey") or "Manual")
        ticket_title = str(ticket.get("summary") or "")[:500]
        if not patch_data.strip():
            raise SvnValidationError("SVN did not produce patch content. There may be no patchable changes in the selection.")
        folder = self.root / safe_component(project_name, "Manual") / safe_component(ticket_key, "MANUAL")
        folder.mkdir(parents=True, exist_ok=True)
        sequence = self.next_sequence(ticket_key)
        clean_label = str(label or "").strip()[:120] or f"Part {sequence}"
        clean_group = str(group or "").strip()[:60]
        now = datetime.now().astimezone()
        stamp = now.strftime("%Y-%m-%d_%H%M%S_%f")
        suffix = safe_component(clean_label, "changes")[:50]
        stem = self._unique_stem(folder, f"{stamp}_{sequence:02d}_{suffix}")
        patch_path = folder / f"{stem}.patch"
        manifest_path = folder / f"{stem}.json"
        artifact = PatchArtifact(
            schema_version=SCHEMA_VERSION,
            artifact_id=uuid4().hex,
            ticket_key=ticket_key,
            ticket_title=ticket_title,
            project=project_name,
            profile_name=str(profile_name or "Working copy")[:100],
            working_copy_root=str(Path(working_copy_root).resolve()),
            repository_url=info.url,
            repository_root=info.repository_root,
            base_revision=info.revision,
            created_at=now.isoformat(timespec="microseconds"),
            patch_file=str(patch_path),
            manifest_file=str(manifest_path),
            affected_paths=tuple(sorted({str(path).replace("\\", "/") for path in affected_paths if str(path).strip()})),
            scope="all" if scope == "all" else "selected",
            sha256=hashlib.sha256(patch_data).hexdigest(),
            state="created",
            warnings=tuple(warnings or ()),
            label=clean_label,
            group=clean_group,
            note=str(note or "").strip()[:2000],
            sequence=sequence,
        )
        self._atomic_write_bytes(patch_path, patch_data)
        try:
            self._write_manifest(artifact)
        except Exception:
            try:
                patch_path.unlink()
            except OSError:
                pass
            raise
        return artifact

    @staticmethod
    def _unique_stem(folder: Path, stem: str) -> str:
        """Never reuse a shelf file name.

        The timestamp alone is not enough: the Windows clock can report the same
        microsecond for two patches created back to back, and reusing the stem
        would silently overwrite the earlier patch and its manifest.
        """
        candidate = stem
        for index in range(2, 1000):
            if not (folder / f"{candidate}.patch").exists() and not (folder / f"{candidate}.json").exists():
                return candidate
            candidate = f"{stem}-{index}"
        raise SvnValidationError("Too many patches were created for this ticket at the same moment.")

    def scan(self, ticket_key: str = "", limit: int = 1000) -> tuple[list[PatchArtifact], int]:
        """Walk the whole shelf, newest first.

        An empty `ticket_key` returns every patch under every project and ticket
        folder. Returns the artifacts plus the number of manifests that could not
        be read, so a corrupt file is reported rather than silently vanishing.
        """
        key = str(ticket_key or "").strip().upper()
        if not self.root.exists():
            return [], 0
        artifacts: list[PatchArtifact] = []
        unreadable = 0
        for path in self.root.rglob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            try:
                artifact = self.load(path)
            except (OSError, ValueError, json.JSONDecodeError, SvnValidationError):
                unreadable += 1
                continue
            if not key or artifact.ticket_key == key:
                artifacts.append(artifact)
        artifacts.sort(key=lambda item: (item.created_at, Path(item.patch_file).name), reverse=True)
        return artifacts[: max(1, int(limit))], unreadable

    def ticket_folders(self, ticket_key: str) -> list[Path]:
        """Every project folder that holds patches for this ticket.

        Artifacts live in `<root>/<project>/<TICKET>/`, so one glob level finds
        them without walking the whole shelf.
        """
        key = str(ticket_key or "").strip().upper()
        if not key or not self.root.exists():
            return []
        folder = safe_component(key, "MANUAL")
        try:
            return [path for path in self.root.glob(f"*/{folder}") if path.is_dir()]
        except OSError:
            return []

    def ticket_artifacts(self, ticket_key: str) -> list[PatchArtifact]:
        """Readable manifests for one ticket, newest first. Skips corrupt files."""
        artifacts = []
        for folder in self.ticket_folders(ticket_key):
            for manifest in folder.glob("*.json"):
                if manifest.name.endswith(".tmp"):
                    continue
                try:
                    artifacts.append(self.load(manifest))
                except (OSError, ValueError, json.JSONDecodeError, SvnValidationError):
                    continue
        artifacts.sort(key=lambda item: (item.created_at, Path(item.patch_file).name), reverse=True)
        return artifacts

    def next_sequence(self, ticket_key: str) -> int:
        """The next position in this ticket's patch series, starting at 1.

        Unreadable manifests are counted as occupied positions so a corrupt file
        can never make two patches share a number.
        """
        highest = 0
        files = 0
        for folder in self.ticket_folders(ticket_key):
            for manifest in folder.glob("*.json"):
                if manifest.name.endswith(".tmp"):
                    continue
                files += 1
                try:
                    highest = max(highest, self.load(manifest).sequence)
                except (OSError, ValueError, json.JSONDecodeError, SvnValidationError):
                    continue
        return max(highest, files) + 1

    def groups_for_ticket(self, ticket_key: str) -> list[str]:
        """Group names already used on this ticket, so the next patch can reuse one."""
        names = {artifact.group.strip() for artifact in self.ticket_artifacts(ticket_key)}
        return sorted((name for name in names if name), key=str.casefold)

    def list_for_ticket(self, ticket_key: str) -> list[PatchArtifact]:
        key = str(ticket_key or "").strip().upper()
        if not key:
            return []
        return self.scan(key)[0]

    def count_for_ticket(self, ticket_key: str) -> int:
        """Cheap patch count for badges.

        Artifacts live in `<root>/<project>/<TICKET>/`, so this globs one folder
        level instead of walking the whole shelf like `list_for_ticket`.
        """
        key = str(ticket_key or "").strip().upper()
        if not key or not self.root.exists():
            return 0
        folder = safe_component(key, "MANUAL")
        try:
            return sum(1 for path in self.root.glob(f"*/{folder}/*.patch") if path.is_file())
        except OSError:
            return 0

    def delete(self, artifact: PatchArtifact) -> list[str]:
        """Remove a patch and its manifest. Returns the deleted file paths."""
        removed = []
        for value in (artifact.patch_file, artifact.manifest_file):
            if not str(value or "").strip():
                continue
            target = self._inside_root(value)
            if target.suffix.casefold() not in {".patch", ".json"}:
                raise SvnValidationError(f"Refusing to delete an unexpected shelf file: {target}")
            try:
                target.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SvnValidationError(f"Could not delete {target.name}: {exc}") from exc
            removed.append(str(target))
        if not removed:
            raise SvnValidationError("The patch files were already removed from the shelf.")
        self._prune_empty_folders(self._inside_root(artifact.manifest_file).parent)
        return removed

    def _prune_empty_folders(self, folder: Path) -> None:
        """Drop the ticket and project folders once their last patch is gone."""
        current = folder
        for _ in range(2):
            if current == self.root or self.root not in current.parents:
                return
            try:
                if any(current.iterdir()):
                    return
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def load(self, manifest_file: str | Path) -> PatchArtifact:
        manifest = self._inside_root(manifest_file)
        if not manifest.is_file():
            raise SvnValidationError(f"Patch manifest does not exist: {manifest}")
        value = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SvnValidationError(f"Patch manifest is invalid: {manifest}")
        patch_name = Path(str(value.get("patch_file") or "")).name
        patch = self._inside_root(manifest.parent / patch_name)
        artifact = PatchArtifact.from_dict(value, str(patch), str(manifest))
        if artifact.schema_version > SCHEMA_VERSION:
            raise SvnValidationError(f"Patch manifest schema {artifact.schema_version} is newer than this app supports.")
        return artifact

    def verify(self, artifact: PatchArtifact) -> None:
        patch = self._inside_root(artifact.patch_file)
        if not patch.is_file():
            raise SvnValidationError(f"Patch file is missing: {patch}")
        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        if digest != artifact.sha256:
            raise SvnValidationError(f"Patch integrity check failed: {patch.name}")

    def update_state(self, artifact: PatchArtifact, state: str, message: str = "") -> PatchArtifact:
        allowed = {"created", "shelved", "revert_failed", "applied", "conflicted", "apply_failed", "archived"}
        if state not in allowed:
            raise SvnValidationError(f"Unsupported patch state: {state}")
        updated = replace(
            artifact,
            state=state,
            last_operation_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            last_operation_message=str(message or "")[:4000],
        )
        self._write_manifest(updated)
        return updated

    def update_details(self, artifact: PatchArtifact, label: str = "", group: str = "", note: str = "") -> PatchArtifact:
        """Rename or regroup a saved patch.

        Only the manifest changes. The patch and manifest files keep the names
        they were created with, so a rename can never half-succeed and leave an
        artifact whose two files no longer belong together.
        """
        self.verify(artifact)
        updated = replace(
            artifact,
            label=str(label or "").strip()[:120] or artifact.display_label,
            group=str(group or "").strip()[:60],
            note=str(note or "").strip()[:2000],
        )
        self._write_manifest(updated)
        return updated

    def renumber_ticket(self, ticket_key: str) -> list[PatchArtifact]:
        """Give every patch of one ticket a position, oldest first.

        Patches saved before the shelf numbered them show no position at all.
        This assigns 1..N in creation order so a long ticket reads as a series.
        """
        artifacts = sorted(self.ticket_artifacts(ticket_key), key=lambda item: (item.created_at, Path(item.patch_file).name))
        renumbered = []
        for position, artifact in enumerate(artifacts, 1):
            if artifact.sequence == position:
                renumbered.append(artifact)
                continue
            updated = replace(artifact, sequence=position)
            self._write_manifest(updated)
            renumbered.append(updated)
        renumbered.reverse()
        return renumbered

    def _write_manifest(self, artifact: PatchArtifact) -> None:
        manifest = self._inside_root(artifact.manifest_file)
        payload = json.dumps(artifact.to_dict(portable=True), ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic_write_bytes(manifest, payload)

    def _inside_root(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve(strict=False)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise SvnValidationError(f"Patch path is outside the configured shelf: {path}") from exc
        return path

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
