"""Typed value objects shared by the SVN service, patch store, and UI."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re


PATCHABLE_ITEM_STATES = {"modified", "added", "deleted", "replaced"}
REVERTIBLE_ITEM_STATES = PATCHABLE_ITEM_STATES | {"conflicted", "missing", "obstructed", "incomplete"}

# SVN has no file lock for "leave this alone locally". TortoiseSVN and this app
# use the conventional ignore-on-commit changelist as that label instead.
IGNORE_CHANGELIST = "ignore-on-commit"


@dataclass(frozen=True)
class SvnWorkingCopyProfile:
    name: str
    root: str
    project: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "SvnWorkingCopyProfile":
        return cls(
            name=str(value.get("name") or "Working copy").strip()[:100],
            root=str(value.get("root") or "").strip(),
            project=str(value.get("project") or "").strip()[:200],
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SvnInfo:
    target: str
    working_copy_root: str
    url: str
    repository_root: str
    repository_uuid: str
    revision: str


@dataclass(frozen=True)
class SvnChange:
    path: str
    item_status: str
    property_status: str = "none"
    revision: str = ""
    copied: bool = False
    switched: bool = False
    tree_conflicted: bool = False
    changelist: str = ""

    @property
    def locked(self) -> bool:
        """True when the file carries the ignore-on-commit label.

        A locked file is still patchable and revertible on request; it is simply
        never selected automatically.
        """
        return self.changelist == IGNORE_CHANGELIST

    @property
    def patchable(self) -> bool:
        return self.item_status in PATCHABLE_ITEM_STATES or self.property_status == "modified"

    @property
    def revertible(self) -> bool:
        return self.item_status in REVERTIBLE_ITEM_STATES or self.property_status in {"modified", "conflicted"}

    @property
    def display_status(self) -> str:
        if self.tree_conflicted or self.item_status == "conflicted" or self.property_status == "conflicted":
            return "Conflicted"
        return self.item_status.replace("_", " ").title()

    @property
    def folder(self) -> str:
        """Working-copy-relative parent folder, or "" for a file at the root."""
        head, separator, _ = self.path.rpartition("/")
        return head if separator else ""

    @property
    def name(self) -> str:
        return self.path.rpartition("/")[2] or self.path

    @property
    def folder_parts(self) -> tuple[str, ...]:
        return tuple(part for part in self.folder.split("/") if part)

    @property
    def sort_key(self) -> tuple:
        """Group siblings together: every folder segment first, then the file name.

        Sorting on the raw string would interleave "a/b.txt" with "a-b/c.txt",
        so compare folder depth-first and fold case for a stable Windows order.
        """
        return (tuple(part.casefold() for part in self.folder_parts), self.name.casefold())


def state_sort_key(change: "SvnChange") -> tuple:
    """Order states by how much attention they need, then by path.

    Locked files sink below everything else so the rows that still need a
    decision stay together at the top.
    """
    priority = {
        "conflicted": 0, "obstructed": 1, "missing": 2, "incomplete": 3,
        "deleted": 4, "replaced": 5, "added": 6, "modified": 7, "unversioned": 9,
    }
    display = change.display_status.casefold()
    rank = 0 if display == "conflicted" else priority.get(change.item_status, 8)
    return (int(change.locked), rank, change.display_status.casefold(), change.sort_key)


PATCH_OPERATIONS = {"added": "added", "deleted": "deleted", "modified": "modified"}


@dataclass(frozen=True)
class PatchFileSummary:
    """One file inside a saved patch, derived from the patch text itself."""

    path: str
    operation: str = "modified"
    added_lines: int = 0
    removed_lines: int = 0
    binary: bool = False

    @property
    def line_summary(self) -> str:
        if self.binary:
            return "binary"
        return f"+{self.added_lines} / -{self.removed_lines}"

    def to_change(self) -> "SvnChange":
        """Render through the same table and tree code as a working-copy change."""
        return SvnChange(path=self.path, item_status=PATCH_OPERATIONS.get(self.operation, "modified"))


@dataclass(frozen=True)
class SvnCommandResult:
    action: str
    executable: str
    arguments: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    cancelled: bool = False
    stdout_bytes: bytes = field(default=b"", repr=False, compare=False)
    stderr_bytes: bytes = field(default=b"", repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled

    @property
    def command_display(self) -> str:
        return " ".join((self.executable, *self.arguments))


@dataclass(frozen=True)
class PatchArtifact:
    """One saved patch.

    A long ticket produces many of these, so each one carries its own identity:
    a `sequence` within the ticket, a short `label`, an optional `group` that
    sorts related patches together, and an optional `note`. Manifests written
    before those fields existed load with empty values and fall back to the
    patch file name.
    """

    schema_version: int
    artifact_id: str
    ticket_key: str
    ticket_title: str
    project: str
    profile_name: str
    working_copy_root: str
    repository_url: str
    repository_root: str
    base_revision: str
    created_at: str
    patch_file: str
    manifest_file: str
    affected_paths: tuple[str, ...]
    scope: str
    sha256: str
    state: str
    warnings: tuple[str, ...] = ()
    last_operation_at: str = ""
    last_operation_message: str = ""
    label: str = ""
    group: str = ""
    note: str = ""
    sequence: int = 0

    @property
    def display_label(self) -> str:
        """What to show in a list. Older patches fall back to their file name."""
        if self.label.strip():
            return self.label.strip()
        # Shelf stems are "<date>_<time>_<micro>[_<sequence>]_<slug>"; keep the slug.
        parts = Path(self.patch_file).stem.split("_", 3)
        remainder = parts[3] if len(parts) == 4 else Path(self.patch_file).stem
        remainder = re.sub(r"^\d{1,5}_", "", remainder)
        return remainder.replace("-", " ").strip() or "Untitled patch"

    @property
    def series(self) -> str:
        """Position within the ticket, blank when the manifest predates numbering."""
        return f"#{self.sequence}" if self.sequence > 0 else ""

    @property
    def headline(self) -> str:
        """Ticket, position, and label in one line, for menus and confirmations."""
        parts = [self.ticket_key, self.series, self.display_label]
        if self.group.strip():
            parts.insert(2, f"[{self.group.strip()}]")
        return "  ".join(part for part in parts if part)

    def matches(self, needle: str) -> bool:
        """Case-insensitive substring search across every field a person types."""
        text = str(needle or "").strip().casefold()
        if not text:
            return True
        haystack = " ".join((
            self.ticket_key, self.ticket_title, self.project, self.display_label,
            self.group, self.note, self.state, self.series, Path(self.patch_file).name,
            *self.affected_paths,
        ))
        return text in haystack.casefold()

    @classmethod
    def from_dict(cls, value: dict, patch_file: str = "", manifest_file: str = "") -> "PatchArtifact":
        return cls(
            schema_version=int(value.get("schema_version") or 1),
            artifact_id=str(value.get("artifact_id") or ""),
            ticket_key=str(value.get("ticket_key") or "").strip().upper(),
            ticket_title=str(value.get("ticket_title") or ""),
            project=str(value.get("project") or ""),
            profile_name=str(value.get("profile_name") or ""),
            working_copy_root=str(value.get("working_copy_root") or ""),
            repository_url=str(value.get("repository_url") or ""),
            repository_root=str(value.get("repository_root") or ""),
            base_revision=str(value.get("base_revision") or ""),
            created_at=str(value.get("created_at") or ""),
            patch_file=patch_file or str(value.get("patch_file") or ""),
            manifest_file=manifest_file or str(value.get("manifest_file") or ""),
            affected_paths=tuple(str(item) for item in value.get("affected_paths", []) if str(item).strip()),
            scope=str(value.get("scope") or "selected"),
            sha256=str(value.get("sha256") or ""),
            state=str(value.get("state") or "created"),
            warnings=tuple(str(item) for item in value.get("warnings", []) if str(item).strip()),
            last_operation_at=str(value.get("last_operation_at") or ""),
            last_operation_message=str(value.get("last_operation_message") or ""),
            label=str(value.get("label") or "")[:120],
            group=str(value.get("group") or "")[:60],
            note=str(value.get("note") or "")[:2000],
            sequence=cls._as_sequence(value.get("sequence")),
        )

    @staticmethod
    def _as_sequence(value) -> int:
        try:
            return max(0, min(99999, int(value)))
        except (TypeError, ValueError):
            return 0

    def to_dict(self, portable: bool = False) -> dict:
        value = asdict(self)
        value["affected_paths"] = list(self.affected_paths)
        value["warnings"] = list(self.warnings)
        if portable:
            value["patch_file"] = Path(self.patch_file).name
            value["manifest_file"] = Path(self.manifest_file).name
        return value
