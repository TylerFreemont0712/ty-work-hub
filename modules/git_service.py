"""Git process boundary. Call from workers; paths are literal and repository-scoped."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import threading


class GitError(RuntimeError):
    pass


def discover_git(configured: str = "") -> str:
    if configured.strip():
        path = Path(configured.strip().strip('"')).expanduser()
        return str(path.resolve()) if path.is_file() else ""
    found = shutil.which("git")
    if not found and os.name == "nt":
        for base in (os.environ.get("ProgramFiles", "C:/Program Files"), os.environ.get("LOCALAPPDATA", "")):
            for suffix in ("Git/cmd/git.exe", "Programs/Git/cmd/git.exe"):
                candidate = Path(base) / suffix
                if candidate.is_file():
                    return str(candidate)
    return found or ""


@dataclass(frozen=True)
class GitChange:
    path: str
    index: str
    worktree: str

    @property
    def staged(self):
        return self.index not in (" ", "?")


def parse_status(raw: bytes) -> list[GitChange]:
    changes = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            raise GitError("Git returned an invalid status record.")
        changes.append(GitChange(os.fsdecode(entry[3:]), chr(entry[0]), chr(entry[1])))
    return changes


class GitService:
    def __init__(self, root: str, executable: str = "", timeout: int = 120):
        if not str(root).strip():
            raise GitError("Choose a Git repository in Settings or use Open repository.")
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise GitError("The repository folder does not exist.")
        self.executable = discover_git(executable)
        if not self.executable:
            raise GitError("Git was not found. Use Settings > Git to install or locate it.")
        self.timeout = timeout
        self.cancelled = threading.Event()
        self.process = None

    def cancel(self):
        self.cancelled.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def run(self, *args: str, check=True) -> bytes:
        if self.cancelled.is_set():
            raise GitError("Git operation cancelled.")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", LC_ALL="C", GIT_OPTIONAL_LOCKS="0")
        # An inherited Git context must never redirect this workspace's commands.
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            env.pop(key, None)
        command = [self.executable, "--no-pager", "--literal-pathspecs", "-c", "color.ui=false", *args]
        try:
            self.process = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if self.cancelled.is_set():
                self.process.terminate()
            out, err = self.process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            self.process.kill()
            self.process.communicate()
            raise GitError("Git timed out. Check authentication or the remote, then refresh status.") from exc
        except OSError as exc:
            raise GitError(f"Could not start Git: {exc}") from exc
        if self.cancelled.is_set():
            raise GitError("Git operation cancelled. Refresh status before retrying.")
        if check and self.process.returncode:
            message = (err or out).decode("utf-8", "replace").strip()
            # Remotes may contain embedded HTTP credentials; never echo those.
            message = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", message)
            raise GitError(message or "Git command failed.")
        return out

    def validate(self):
        value = self.run("rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").strip()
        if Path(value).resolve() != self.root:
            raise GitError(f"Select the repository root: {value}")

    def paths(self, paths):
        values = []
        for value in paths:
            relative = PurePosixPath(value)
            if not value or relative.is_absolute() or ".." in relative.parts or any(p.casefold() == ".git" for p in relative.parts):
                raise GitError("Unsafe repository path.")
            target = self.root / value
            if not target.parent.resolve().is_relative_to(self.root):
                raise GitError("The selected path leaves the repository.")
            values.append(value)
        if not values:
            raise GitError("Select one or more files first.")
        return values

    def has_head(self):
        return bool(self.run("rev-parse", "--verify", "HEAD", check=False).strip())

    def snapshot(self):
        self.validate()
        changes = parse_status(self.run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"))
        branch = self.run("symbolic-ref", "--short", "-q", "HEAD", check=False).decode("utf-8", "replace").strip()
        head = self.run("rev-parse", "--short", "HEAD", check=False).decode().strip()
        history = self.run("log", "-15", "--format=%h  %s", check=False).decode("utf-8", "replace") if head else "No commits yet."
        stashes = self.run("stash", "list", "--format=%H %gd %s").decode("utf-8", "replace").splitlines()
        return {"changes": changes, "branch": branch or f"Detached at {head}", "history": history, "stashes": stashes}

    def diff(self, paths=(), staged=False):
        self.validate()
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames"]
        if staged:
            args.append("--cached")
        if paths:
            args += ["--", *self.paths(paths)]
        output = self.run(*args).decode("utf-8", "replace")
        if not output and paths and not staged:
            for relative in self.paths(paths):
                path = self.root / relative
                if path.is_file() and not path.is_symlink() and not self.run("ls-files", "--", relative):
                    if path.stat().st_size > 1_000_000:
                        output += f"\n{relative}: untracked file larger than 1 MB; open externally to review.\n"
                    else:
                        data = path.read_bytes()
                        output += f"\nUntracked: {relative}\n" + ("Binary file\n" if b"\0" in data else data.decode("utf-8", "replace"))
        return output or "No changes in this view. Switch between working tree and staged changes."

    def stage(self, paths):
        self.validate()
        self.run("add", "--", *self.paths(paths))

    def unstage(self, paths):
        self.validate()
        values = self.paths(paths)
        if self.has_head():
            self.run("restore", "--staged", "--", *values)
        else:
            self.run("rm", "--cached", "--", *values)

    def staged_patch(self):
        self.validate()
        return self.run("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv")

    def commit(self, message, expected):
        patch = self.staged_patch()
        if not message.strip() or not patch:
            raise GitError("Enter a commit message and stage at least one change.")
        if hashlib.sha256(patch).hexdigest() != expected:
            raise GitError("Staged changes changed after preview. Review them again before committing.")
        return self.run("commit", "-m", message.strip()).decode("utf-8", "replace")

    def remote_action(self, action):
        self.validate()
        commands = {"fetch": ("fetch",), "pull": ("pull", "--ff-only"), "push": ("push",)}
        if action not in commands:
            raise GitError("Unsupported Git action.")
        return self.run(*commands[action]).decode("utf-8", "replace") or f"Git {action} completed."

    def stash(self, label):
        self.validate()
        return self.run("stash", "push", "--include-untracked", "-m", label or "Ty Work Hub").decode("utf-8", "replace")

    def apply_stash(self, oid):
        self.validate()
        if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
            raise GitError("Select a saved stash first.")
        current = self.run("stash", "list", "--format=%H").decode().splitlines()
        if oid not in current:
            raise GitError("This stash is no longer present. Refresh the repository.")
        return self.run("stash", "apply", oid).decode("utf-8", "replace")

    def patch(self):
        self.validate()
        args = ["diff", "--binary", "--no-ext-diff", "--no-textconv"]
        args += ["HEAD"] if self.has_head() else ["--cached"]
        return self.run(*args)
