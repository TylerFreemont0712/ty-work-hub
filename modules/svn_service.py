"""Safe, synchronous SVN and TortoiseSVN process boundary.

Callers must run these operations outside the Qt GUI thread. Commands always use
argument arrays; no user-controlled value is interpolated into a shell command.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
import locale
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

from modules.svn_models import IGNORE_CHANGELIST, PatchFileSummary, SvnChange, SvnCommandResult, SvnInfo


class SvnError(RuntimeError):
    pass


class SvnValidationError(SvnError):
    pass


class SvnCommandError(SvnError):
    def __init__(self, message: str, result: SvnCommandResult):
        super().__init__(message)
        self.result = result


def discover_svn_executable(configured: str = "") -> str:
    return _discover_executable(
        configured,
        "svn.exe" if os.name == "nt" else "svn",
        (
            Path("C:/Program Files/TortoiseSVN/bin/svn.exe"),
            Path("C:/Program Files/SlikSvn/bin/svn.exe"),
            Path("C:/Program Files/VisualSVN Server/bin/svn.exe"),
        ),
    )


def discover_tortoise_executable(configured: str = "") -> str:
    if os.name != "nt":
        return ""
    return _discover_executable(
        configured,
        "TortoiseProc.exe",
        (Path("C:/Program Files/TortoiseSVN/bin/TortoiseProc.exe"),),
    )


def _discover_executable(configured: str, name: str, candidates: Iterable[Path]) -> str:
    value = str(configured or "").strip().strip('"')
    if value:
        path = Path(value).expanduser()
        if path.is_file():
            return str(path.resolve())
    located = shutil.which(name)
    if located:
        return str(Path(located).resolve())
    return next((str(path.resolve()) for path in candidates if path.is_file()), "")


def resolve_directory(value: str | Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise SvnValidationError("Choose a working-copy folder first.")
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise SvnValidationError(f"Working-copy folder does not exist: {path}")
    if not path.is_dir():
        raise SvnValidationError(f"Working-copy path is not a folder: {path}")
    return path


def normalize_scoped_paths(root: str | Path, values: Iterable[str | Path]) -> list[Path]:
    base = resolve_directory(root)
    clean: list[Path] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        candidate = Path(text).expanduser()
        candidate = candidate if candidate.is_absolute() else base / candidate
        candidate = candidate.resolve(strict=False)
        try:
            relative = candidate.relative_to(base)
        except ValueError as exc:
            raise SvnValidationError(f"Path is outside the configured working copy: {candidate}") from exc
        if not relative.parts or ".svn" in {part.casefold() for part in relative.parts}:
            raise SvnValidationError(f"Refusing unsafe SVN target: {candidate}")
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            clean.append(candidate)
    return clean


def parse_status_xml(value: str, root: str | Path) -> list[SvnChange]:
    """Parse `svn status --xml`, keeping the changelist each entry belongs to.

    Entries assigned to a changelist are reported under a `<changelist>` element
    rather than under `<target>`, so both containers are walked and the label is
    carried onto the change.
    """
    try:
        document = ET.fromstring(value)
    except ET.ParseError as exc:
        raise SvnError(f"SVN returned invalid status XML: {exc}") from exc
    base = resolve_directory(root)
    changes: list[SvnChange] = []
    containers = [(element, "") for element in document.findall("target")]
    containers.extend((element, str(element.get("name") or "")) for element in document.findall("changelist"))
    if not containers:
        containers = [(document, "")]
    for container, changelist in containers:
        for entry in container.findall(".//entry"):
            status = entry.find("wc-status")
            if status is None:
                continue
            item = str(status.get("item") or "none")
            props = str(status.get("props") or "none")
            if item in {"normal", "none"} and props in {"normal", "none"}:
                continue
            raw_path = str(entry.get("path") or "").strip()
            candidate = Path(raw_path)
            candidate = candidate if candidate.is_absolute() else base / candidate
            candidate = candidate.resolve(strict=False)
            try:
                relative = candidate.relative_to(base).as_posix()
            except ValueError:
                relative = raw_path.replace("\\", "/")
            changes.append(SvnChange(
                path=relative,
                item_status=item,
                property_status=props,
                revision=str(status.get("revision") or ""),
                copied=status.get("copied") == "true",
                switched=status.get("switched") == "true",
                tree_conflicted=status.get("tree-conflicted") == "true",
                changelist=changelist,
            ))
    return sorted(changes, key=lambda item: item.path.casefold())


def parse_info_xml(value: str, target: str | Path) -> SvnInfo:
    try:
        document = ET.fromstring(value)
    except ET.ParseError as exc:
        raise SvnError(f"SVN returned invalid info XML: {exc}") from exc
    entry = document.find("entry")
    if entry is None:
        raise SvnError("SVN did not return working-copy information.")
    wc_info = entry.find("wc-info")
    repository = entry.find("repository")
    return SvnInfo(
        target=str(Path(target).resolve()),
        working_copy_root=(wc_info.findtext("wcroot-abspath", "") if wc_info is not None else ""),
        url=entry.findtext("url", ""),
        repository_root=(repository.findtext("root", "") if repository is not None else ""),
        repository_uuid=(repository.findtext("uuid", "") if repository is not None else ""),
        revision=str(entry.get("revision") or ""),
    )


def parse_patch_summary(value: str) -> list[PatchFileSummary]:
    """Describe every file inside a unified SVN patch.

    SVN marks a created file with a `(nonexistent)` left side and a removed file
    with a `(nonexistent)` right side, which is how added/deleted are told apart
    from an ordinary modification.
    """
    summaries: list[PatchFileSummary] = []
    path = ""
    added = removed = 0
    binary = False
    from_missing = to_missing = False

    def flush() -> None:
        nonlocal path, added, removed, binary, from_missing, to_missing
        if path:
            operation = "added" if from_missing else "deleted" if to_missing else "modified"
            summaries.append(PatchFileSummary(path, operation, added, removed, binary))
        path, added, removed, binary = "", 0, 0, False
        from_missing = to_missing = False

    for line in str(value or "").splitlines():
        if line.startswith("Index: "):
            flush()
            path = line[len("Index: "):].strip().replace("\\", "/")
        elif line.startswith("--- "):
            from_missing = "(nonexistent)" in line or "(revision 0)" in line
        elif line.startswith("+++ "):
            to_missing = "(nonexistent)" in line
        elif line.startswith("@@"):
            continue
        elif line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        elif "Cannot display:" in line and "binary" in line.casefold():
            binary = True
    flush()
    return summaries


def patch_output_has_conflicts(value: str) -> bool:
    for line in str(value or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("C ") or stripped.startswith("C\t") or ".svnpatch.rej" in stripped:
            return True
    return False


def normalize_revision(value: str) -> str:
    """Accept HEAD/BASE/PREV/COMMITTED, `r123`, or a plain revision number."""
    raw = str(value or "HEAD").strip().upper().lstrip("R") or "HEAD"
    if raw in {"HEAD", "BASE", "PREV", "COMMITTED"}:
        return raw
    if raw.isdigit() and int(raw) >= 0:
        return raw
    raise SvnValidationError(f"Enter a revision number or HEAD, not {value!r}.")


def patch_output_has_unsupported_binary(value: str) -> bool:
    lowered = str(value or "").casefold()
    return "cannot display: file marked as a binary type" in lowered or "cannot display: file marked as binary" in lowered


class SvnService:
    def __init__(
        self,
        executable: str,
        tortoise_executable: str = "",
        timeout_seconds: int = 120,
        runner: Callable[..., SvnCommandResult] | None = None,
    ):
        self.executable = discover_svn_executable(executable)
        self.tortoise_executable = discover_tortoise_executable(tortoise_executable)
        self.timeout_seconds = max(10, min(1800, int(timeout_seconds)))
        self.runner = runner
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cancel_requested = False

    def cancel_current(self) -> None:
        self._cancel_requested = True
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def info(self, root: str | Path) -> tuple[SvnInfo, SvnCommandResult]:
        base = resolve_directory(root)
        result = self._svn("info", ["info", "--xml", "--non-interactive", "."], base)
        return parse_info_xml(result.stdout, base), result

    def status(self, root: str | Path) -> tuple[list[SvnChange], SvnCommandResult]:
        base = resolve_directory(root)
        result = self._svn("status", ["status", "--xml", "--ignore-externals", "--non-interactive", "."], base)
        return parse_status_xml(result.stdout, base), result

    def diff(self, root: str | Path, paths: Iterable[str | Path] = ()) -> SvnCommandResult:
        base = resolve_directory(root)
        targets = normalize_scoped_paths(base, paths)
        arguments = ["diff", "--internal-diff", "--non-interactive"]
        arguments.extend(str(path.relative_to(base)) for path in targets)
        if not targets:
            arguments.append(".")
        return self._svn("diff", arguments, base, timeout=max(self.timeout_seconds, 300))

    def cat_base(self, root: str | Path, path: str | Path) -> SvnCommandResult:
        """Read one file as it exists at the working copy's BASE revision.

        Used for the side-by-side compare view. An added file has no BASE
        content, so SVN exits non-zero; the caller treats that as an empty
        "before" side rather than as a failure.
        """
        base = resolve_directory(root)
        targets = normalize_scoped_paths(base, [path])
        if not targets:
            raise SvnValidationError("Select one file to compare with its base revision.")
        relative = str(targets[0].relative_to(base))
        return self._execute("cat_base", ["cat", "-r", "BASE", "--non-interactive", relative], base, self.timeout_seconds)

    def revert(self, root: str | Path, paths: Iterable[str | Path], remove_added: bool = True) -> SvnCommandResult:
        base = resolve_directory(root)
        targets = normalize_scoped_paths(base, paths)
        if not targets:
            raise SvnValidationError("Select one or more changed paths to revert.")
        arguments = ["revert", "--non-interactive"]
        if remove_added:
            arguments.append("--remove-added")
        arguments.extend(str(path.relative_to(base)) for path in targets)
        return self._svn("revert", arguments, base)

    def apply_patch(self, root: str | Path, patch_file: str | Path, dry_run: bool = False) -> SvnCommandResult:
        base = resolve_directory(root)
        patch = Path(patch_file).expanduser().resolve()
        if not patch.is_file():
            raise SvnValidationError(f"Patch file does not exist: {patch}")
        action = "patch_dry_run" if dry_run else "apply_patch"
        with tempfile.TemporaryDirectory(prefix="tywork-svn-patch-") as folder:
            safe_patch = Path(folder) / "ticket.patch"
            shutil.copyfile(patch, safe_patch)
            arguments = ["patch", "--non-interactive"]
            if dry_run:
                arguments.append("--dry-run")
            arguments.extend((str(safe_patch), "."))
            return self._svn(action, arguments, base, timeout=max(self.timeout_seconds, 300))

    def changelist(self, root: str | Path, paths: Iterable[str | Path], name: str = IGNORE_CHANGELIST) -> SvnCommandResult:
        """Add paths to a changelist, or remove them when `name` is empty.

        This only labels files; it never changes their content. The label is
        stored in the working copy so TortoiseSVN sees the same thing.
        """
        base = resolve_directory(root)
        targets = normalize_scoped_paths(base, paths)
        if not targets:
            raise SvnValidationError("Select one or more paths to change their lock state.")
        relative = [str(path.relative_to(base)) for path in targets]
        if not str(name or "").strip():
            return self._svn("changelist", ["changelist", "--remove", "--non-interactive", *relative], base)
        label = str(name).strip()
        if label.startswith("-"):
            raise SvnValidationError(f"Unsupported changelist name: {label}")
        return self._svn("changelist", ["changelist", label, "--non-interactive", *relative], base)

    def cleanup(self, root: str | Path) -> SvnCommandResult:
        base = resolve_directory(root)
        return self._svn("cleanup", ["cleanup", "--non-interactive", "."], base, timeout=max(self.timeout_seconds, 300))

    def update(self, root: str | Path, revision: str = "HEAD") -> SvnCommandResult:
        base = resolve_directory(root)
        target = normalize_revision(revision)
        return self._svn(
            "update", ["update", "-r", target, "--non-interactive", "."], base,
            timeout=max(self.timeout_seconds, 600),
        )

    def resolve(self, root: str | Path, paths: Iterable[str | Path], accept: str = "working") -> SvnCommandResult:
        base = resolve_directory(root)
        targets = normalize_scoped_paths(base, paths)
        if not targets:
            raise SvnValidationError("Select one or more conflicted paths to resolve.")
        allowed = {"working", "base", "mine-full", "theirs-full"}
        if accept not in allowed:
            raise SvnValidationError(f"Unsupported conflict resolution choice: {accept}")
        return self._svn("resolve", ["resolve", "--accept", accept, "--non-interactive", *(str(path.relative_to(base)) for path in targets)], base)

    def log(self, root: str | Path, limit: int = 50) -> SvnCommandResult:
        base = resolve_directory(root)
        count = max(1, min(200, int(limit)))
        return self._svn("log", ["log", "--limit", str(count), "--non-interactive", "."], base, timeout=max(self.timeout_seconds, 300))

    def blame(self, root: str | Path, path: str | Path) -> SvnCommandResult:
        base = resolve_directory(root)
        targets = normalize_scoped_paths(base, [path])
        return self._svn("blame", ["blame", "--non-interactive", str(targets[0].relative_to(base))], base, timeout=max(self.timeout_seconds, 300))

    def launch_tortoise(self, command: str, root: str | Path, path: str | Path = "") -> SvnCommandResult:
        base = resolve_directory(root)
        if not self.tortoise_executable:
            raise SvnValidationError("TortoiseProc.exe was not found. Configure it in Settings > Source Control.")
        allowed = {
            "about", "add", "blame", "cleanup", "commit", "copy", "createpatch", "diff", "export",
            "lock", "log", "merge", "properties", "remove", "rename", "repobrowser", "resolve",
            "repostatus", "revisiongraph", "switch", "unlock", "update",
        }
        if command not in allowed:
            raise SvnValidationError(f"Unsupported TortoiseSVN command: {command}")
        target = normalize_scoped_paths(base, [path])[0] if str(path or "").strip() else base
        arguments = (f"/command:{command}", f"/path:{target}", "/closeonend:0")
        started = time.monotonic()
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen([self.tortoise_executable, *arguments], cwd=str(base), creationflags=creationflags)
        except OSError as exc:
            raise SvnError(f"Could not launch TortoiseSVN: {exc}") from exc
        return SvnCommandResult(
            action=f"tortoise_{command}", executable=self.tortoise_executable, arguments=arguments,
            cwd=str(base), returncode=0, stdout=f"Opened TortoiseSVN {command} for {target}", stderr="",
            duration_seconds=time.monotonic() - started,
        )

    def _svn(self, action: str, arguments: list[str], cwd: Path, timeout: int | None = None) -> SvnCommandResult:
        if not self.executable:
            raise SvnValidationError("svn.exe was not found. Configure it in Settings > Source Control.")
        result = self._execute(action, arguments, cwd, timeout or self.timeout_seconds)
        if not result.ok:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            if result.cancelled:
                detail = "The operation was cancelled."
            elif result.timed_out:
                detail = f"The operation exceeded {timeout or self.timeout_seconds} seconds."
            raise SvnCommandError(f"SVN {action} failed: {detail}", result)
        return result

    def _execute(self, action: str, arguments: list[str], cwd: Path, timeout: int) -> SvnCommandResult:
        if not self.executable:
            raise SvnValidationError("svn.exe was not found. Configure it in Settings > Source Control.")
        if self.runner:
            return self.runner(action=action, executable=self.executable, arguments=tuple(arguments), cwd=str(cwd), timeout=timeout)
        self._cancel_requested = False
        started = time.monotonic()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [self.executable, *arguments], cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise SvnError(f"Could not start SVN: {exc}") from exc
        with self._lock:
            self._process = process
        timed_out = False
        cancelled = False
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = process.communicate()
        finally:
            with self._lock:
                self._process = None
            cancelled = self._cancel_requested
        return SvnCommandResult(
            action=action,
            executable=self.executable,
            arguments=tuple(arguments),
            cwd=str(cwd),
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=self._decode(stdout_bytes),
            stderr=self._decode(stderr_bytes),
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
        )

    @staticmethod
    def _decode(value: bytes) -> str:
        for encoding in ("utf-8-sig", locale.getpreferredencoding(False)):
            try:
                return value.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return value.decode("utf-8", errors="replace")
