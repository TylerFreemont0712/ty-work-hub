"""Optional real-SVN integration check using an isolated temporary repository."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory

from modules.patch_store import PatchStore
from modules.svn_models import IGNORE_CHANGELIST
from modules.svn_service import SvnService, discover_svn_executable, parse_patch_summary
from services.svn_workers import SvnWorker


def run(arguments: list[str], cwd: Path | None = None) -> None:
    completed = subprocess.run(arguments, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace") or completed.stdout.decode("utf-8", errors="replace")
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(arguments)}\n{detail}")


def main():
    svn = discover_svn_executable()
    svnadmin = shutil.which("svnadmin.exe" if os.name == "nt" else "svnadmin")
    if not svn or not svnadmin:
        print("svn integration skipped: svn/svnadmin not installed")
        return

    with TemporaryDirectory(prefix="ty-work-svn-") as folder:
        root = Path(folder)
        # Keep the file:// repository URL ASCII; the integration target is a
        # working-copy path with spaces and non-ASCII characters.
        repository = root / "Repository"
        seed = root / "Seed"
        working_copy = root / "Working Copy 日本"
        patch_root = root / "Patch Shelf"
        repository.mkdir()
        seed.mkdir()
        source = seed / "src"
        source.mkdir()
        (source / "tracked file.txt").write_text("before\n", encoding="utf-8")

        run([svnadmin, "create", str(repository)])
        repository_url = repository.as_uri()
        run([svn, "import", str(seed), repository_url + "/trunk", "-m", "Initial import", "--non-interactive"])
        working_copy.mkdir()
        run([svn, "checkout", repository_url + "/trunk", ".", "--non-interactive"], working_copy)

        tracked = working_copy / "src" / "tracked file.txt"
        added = working_copy / "src" / "new file.txt"
        tracked.write_text("after\n", encoding="utf-8")
        added.write_text("new\n", encoding="utf-8")
        run([svn, "add", str(Path("src") / "new file.txt"), "--non-interactive"], working_copy)

        service = SvnService(svn, timeout_seconds=60)
        changes, _ = service.status(working_copy)
        patchable = [change.path for change in changes if change.patchable]
        assert patchable == ["src/new file.txt", "src/tracked file.txt"]

        # Locking uses the real ignore-on-commit changelist and survives a status refresh.
        config_probe = {"svn_patch_root": str(patch_root), "svn_executable": svn, "svn_timeout_seconds": 60}
        SvnWorker("changelist", config_probe, {
            "root": str(working_copy), "paths": ["src/tracked file.txt"], "changelist": IGNORE_CHANGELIST,
        }, service)._dispatch()
        locked_changes, _ = service.status(working_copy)
        by_path = {change.path: change for change in locked_changes}
        assert by_path["src/tracked file.txt"].locked is True
        assert by_path["src/tracked file.txt"].changelist == IGNORE_CHANGELIST
        assert by_path["src/new file.txt"].locked is False
        assert by_path["src/tracked file.txt"].patchable is True, "A locked file stays patchable on request"
        assert sorted(change.path for change in locked_changes) == sorted(patchable)
        SvnWorker("changelist", config_probe, {
            "root": str(working_copy), "paths": ["src/tracked file.txt"], "changelist": "",
        }, service)._dispatch()
        assert all(not change.locked for change in service.status(working_copy)[0])

        config = {"svn_patch_root": str(patch_root), "svn_executable": svn, "svn_timeout_seconds": 60}
        ticket = {"key": "DEV-REAL-1", "summary": "Real SVN patch flow", "project": {"name": "Integration 日本"}}
        payload = {
            "root": str(working_copy), "profile_name": "Integration", "ticket": ticket,
            "paths": patchable, "label": "real integration",
        }
        shelved = SvnWorker("shelve", config, payload, service)._dispatch()["artifact"]
        assert shelved.state == "shelved"
        assert tracked.read_text(encoding="utf-8") == "before\n"
        assert not added.exists()
        assert service.status(working_copy)[0] == []

        apply_payload = {"root": str(working_copy), "manifest_file": shelved.manifest_file}
        preview = SvnWorker("patch_dry_run", config, apply_payload, service)._dispatch()
        assert preview["conflicts"] is False
        applied = SvnWorker("apply_patch", config, apply_payload, service)._dispatch()["artifact"]
        assert applied.state == "applied"
        assert tracked.read_text(encoding="utf-8") == "after\n"
        assert added.read_text(encoding="utf-8") == "new\n"
        assert len(service.status(working_copy)[0]) == 2
        store = PatchStore(patch_root)
        store.verify(applied)
        patch_text = Path(applied.patch_file).read_text(encoding="utf-8")

        # Compare with base reads real BASE content through svn cat.
        compare = SvnWorker(
            "compare_base", config, {"root": str(working_copy), "paths": patchable}, service,
        )._dispatch()
        compared = {item["path"]: item for item in compare["files"]}
        assert compared["src/tracked file.txt"]["before"] == "before\n"
        assert compared["src/tracked file.txt"]["after"] == "after\n"
        assert compared["src/new file.txt"]["after"] == "new\n"
        assert compared["src/new file.txt"]["before"] == "", "An added file has no base revision"
        assert compared["src/new file.txt"]["before_note"]

        # Deleting the artifact clears the shelf without touching the working copy.
        assert store.count_for_ticket("DEV-REAL-1") == 1
        removed = SvnWorker(
            "delete_patch", config, {"manifest_file": applied.manifest_file}, service,
        )._dispatch()["removed"]
        assert len(removed) == 2 and not Path(applied.patch_file).exists()
        assert store.count_for_ticket("DEV-REAL-1") == 0
        assert tracked.read_text(encoding="utf-8") == "after\n"

        # A real SVN patch parses into the per-file summary the Patch Changes tab shows.
        summaries = {item.path: item for item in parse_patch_summary(patch_text)}
        assert summaries["src/tracked file.txt"].operation == "modified"
        assert summaries["src/new file.txt"].operation == "added"
        assert summaries["src/tracked file.txt"].added_lines == 1
        assert summaries["src/tracked file.txt"].removed_lines == 1

        # Update accepts an explicit revision as well as HEAD.
        service.update(working_copy, "1")
        assert service.info(working_copy)[0].revision == "1"
        service.update(working_copy, "HEAD")

    print("real svn integration ok")


if __name__ == "__main__":
    main()
