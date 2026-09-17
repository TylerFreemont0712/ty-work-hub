"""Focused deterministic checks for SVN command and ticket-patch workflows."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from modules.patch_store import PatchStore
from modules.ticket_utils import natural_key
from modules.svn_models import IGNORE_CHANGELIST, SvnChange, SvnCommandResult, SvnInfo, state_sort_key
from modules.svn_service import (
    SvnCommandError, SvnService, SvnValidationError, normalize_revision, normalize_scoped_paths,
    parse_info_xml, parse_patch_summary, parse_status_xml, patch_output_has_conflicts,
)
from services.svn_workers import SvnWorker


INFO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<info>
  <entry kind="dir" path="." revision="42">
    <url>https://svn.example.test/repos/app/trunk</url>
    <repository><root>https://svn.example.test/repos/app</root><uuid>test-uuid</uuid></repository>
    <wc-info><wcroot-abspath>{root}</wcroot-abspath></wc-info>
  </entry>
</info>
"""

STATUS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<status><target path=".">
  <entry path="src/日本 file.txt"><wc-status item="modified" props="none" revision="42" /></entry>
  <entry path="src/new file.txt"><wc-status item="added" props="none" revision="-1" /></entry>
  <entry path="notes/local.txt"><wc-status item="unversioned" props="none" /></entry>
</target>
<changelist name="ignore-on-commit">
  <entry path="web.config"><wc-status item="modified" props="none" revision="42" /></entry>
</changelist>
</status>
"""

PATCH_BYTES = """Index: src/日本 file.txt
===================================================================
--- src/日本 file.txt\t(revision 42)
+++ src/日本 file.txt\t(working copy)
@@ -1 +1 @@
-before
+after
Index: src/new file.txt
===================================================================
--- src/new file.txt\t(nonexistent)
+++ src/new file.txt\t(working copy)
@@ -0,0 +1 @@
+new
""".encode("utf-8")


def command_result(action: str, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> SvnCommandResult:
    return SvnCommandResult(
        action=action,
        executable="svn.exe",
        arguments=(),
        cwd="",
        returncode=returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        duration_seconds=0.01,
        stdout_bytes=stdout,
        stderr_bytes=stderr,
    )


class FakeService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.reverted: list[str] = []
        self.applied: list[tuple[str, bool]] = []
        self.changelists: list[tuple[list, str]] = []
        self.cancelled = False

    def info(self, _root):
        return SvnInfo(
            target=str(self.root), working_copy_root=str(self.root),
            url="https://svn.example.test/repos/app/trunk",
            repository_root="https://svn.example.test/repos/app",
            repository_uuid="test-uuid", revision="42",
        ), command_result("info")

    def status(self, _root):
        return [
            SvnChange("src/日本 file.txt", "modified", revision="42"),
            SvnChange("src/new file.txt", "added"),
            SvnChange("notes/local.txt", "unversioned"),
        ], command_result("status")

    def diff(self, _root, paths):
        assert list(paths) == ["src/日本 file.txt", "src/new file.txt"]
        return command_result("diff", PATCH_BYTES)

    def changelist(self, _root, paths, name=IGNORE_CHANGELIST):
        self.changelists.append((list(paths), name))
        return command_result("changelist", b"ok")

    def revert(self, _root, paths, remove_added=True):
        assert remove_added is True
        self.reverted = list(paths)
        return command_result("revert", b"Reverted selected paths")

    def apply_patch(self, _root, patch_file, dry_run=False):
        self.applied.append((str(patch_file), dry_run))
        return command_result("patch_dry_run" if dry_run else "apply_patch", b"U         src/\xe6\x97\xa5\xe6\x9c\xac file.txt\n")

    def cat_base(self, _root, path):
        if "new file" in str(path):
            return command_result("cat_base", b"", b"is not under version control", returncode=1)
        return command_result("cat_base", b"before\n")

    def cancel_current(self):
        self.cancelled = True


def main():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        working_copy = root / "Working Copy 日本"
        working_copy.mkdir()
        (working_copy / "src").mkdir()
        patch_root = root / "Patch Shelf"

        info = parse_info_xml(INFO_XML.format(root=working_copy.as_posix()), working_copy)
        assert info.revision == "42" and info.repository_root.endswith("/app")
        changes = parse_status_xml(STATUS_XML, working_copy)
        assert [change.path for change in changes] == [
            "notes/local.txt", "src/new file.txt", "src/日本 file.txt", "web.config",
        ]
        assert sum(change.patchable for change in changes) == 3
        # An ignore-on-commit entry is reported under <changelist>, not <target>.
        locked = {change.path: change for change in changes}["web.config"]
        assert locked.locked is True and locked.changelist == IGNORE_CHANGELIST
        assert locked.patchable is True, "Locking is a label, not a restriction"
        assert all(not change.locked for change in changes if change.path != "web.config")
        # Locked rows sort below everything else so undecided rows stay together.
        assert sorted(changes, key=state_sort_key)[-1].path == "web.config"

        # Patch text parses into per-file operations and line counts.
        summaries = {item.path: item for item in parse_patch_summary(PATCH_BYTES.decode("utf-8"))}
        assert summaries["src/日本 file.txt"].operation == "modified"
        assert summaries["src/日本 file.txt"].line_summary == "+1 / -1"
        assert summaries["src/new file.txt"].operation == "added"
        assert summaries["src/new file.txt"].to_change().item_status == "added"

        # Ticket keys sort numerically, so DEV-2 stays ahead of DEV-10.
        assert sorted(["DEV-10", "DEV-2", "DEV-1"], key=natural_key) == ["DEV-1", "DEV-2", "DEV-10"]
        assert natural_key("dev-2") == natural_key("DEV-2"), "Case must not change the order"
        assert natural_key("") == (("", 0),)

        # Revision input is validated before it ever reaches the command line.
        assert normalize_revision("r42") == "42" and normalize_revision(" head ") == "HEAD"
        for bad in ("--force", "HEAD; rm", "-1"):
            try:
                normalize_revision(bad)
            except SvnValidationError:
                continue
            raise AssertionError(f"Unsafe revision accepted: {bad}")
        try:
            normalize_scoped_paths(working_copy, [root / "outside.txt"])
        except SvnValidationError:
            pass
        else:
            raise AssertionError("Out-of-scope path was accepted")
        assert patch_output_has_conflicts("C         src/conflict.txt") is True

        # Path sorting keeps siblings together instead of interleaving similar prefixes.
        unsorted = [
            SvnChange("src/app-web/index.jsp", "modified"),
            SvnChange("src/app/service/Order.java", "modified"),
            SvnChange("src/app/Main.java", "added"),
            SvnChange("README.md", "unversioned"),
        ]
        by_path = [change.path for change in sorted(unsorted, key=lambda item: item.sort_key)]
        assert by_path == ["README.md", "src/app/Main.java", "src/app/service/Order.java", "src/app-web/index.jsp"]
        assert unsorted[1].folder == "src/app/service" and unsorted[1].name == "Order.java"
        assert unsorted[3].folder == "" and unsorted[3].folder_parts == ()
        # State sorting surfaces conflicts first and pushes unversioned noise last.
        by_state = [change.item_status for change in sorted(
            [SvnChange("a.txt", "unversioned"), SvnChange("b.txt", "modified"), SvnChange("c.txt", "conflicted")],
            key=state_sort_key,
        )]
        assert by_state == ["conflicted", "modified", "unversioned"]

        calls = []

        def runner(**kwargs):
            calls.append(kwargs)
            action = kwargs["action"]
            if action == "info":
                output = INFO_XML.format(root=working_copy.as_posix()).encode("utf-8")
            elif action == "status":
                output = STATUS_XML.encode("utf-8")
            elif action == "diff":
                output = PATCH_BYTES
            else:
                output = b"ok"
            result = command_result(action, output)
            return SvnCommandResult(
                **{**result.__dict__, "executable": kwargs["executable"], "arguments": kwargs["arguments"], "cwd": kwargs["cwd"]}
            )

        service = SvnService("C:/fake/svn.exe", runner=runner)
        # Preserve an injected executable in tests even when it is not installed.
        service.executable = "C:/fake/svn.exe"
        service.info(working_copy)
        parsed, _ = service.status(working_copy)
        assert len(parsed) == 4
        service.diff(working_copy, ["src/日本 file.txt"])
        diff_call = calls[-1]
        assert diff_call["arguments"][-1] == str(Path("src/日本 file.txt"))
        assert isinstance(diff_call["arguments"], tuple)

        def failed_runner(**kwargs):
            result = command_result(kwargs["action"], stderr=b"working copy locked", returncode=1)
            return SvnCommandResult(**{**result.__dict__, "executable": kwargs["executable"], "arguments": kwargs["arguments"], "cwd": kwargs["cwd"]})

        failed_service = SvnService("C:/fake/svn.exe", runner=failed_runner)
        failed_service.executable = "C:/fake/svn.exe"
        try:
            failed_service.status(working_copy)
        except SvnCommandError as exc:
            assert "working copy locked" in str(exc)
        else:
            raise AssertionError("Failed SVN result was treated as success")

        def timeout_runner(**kwargs):
            return SvnCommandResult(
                action=kwargs["action"], executable=kwargs["executable"], arguments=kwargs["arguments"], cwd=kwargs["cwd"],
                returncode=-1, stdout="", stderr="", duration_seconds=10.0, timed_out=True,
            )

        timeout_service = SvnService("C:/fake/svn.exe", timeout_seconds=10, runner=timeout_runner)
        timeout_service.executable = "C:/fake/svn.exe"
        try:
            timeout_service.info(working_copy)
        except SvnCommandError as exc:
            assert "exceeded 10 seconds" in str(exc)
        else:
            raise AssertionError("Timed-out SVN result was treated as success")

        tortoise_service = SvnService("C:/fake/svn.exe")
        tortoise_service.executable = "C:/fake/svn.exe"
        tortoise_service.tortoise_executable = "C:/Program Files/TortoiseSVN/bin/TortoiseProc.exe"
        with patch("modules.svn_service.subprocess.Popen") as popen:
            tortoise_service.launch_tortoise("commit", working_copy)
            arguments = popen.call_args.args[0]
            assert arguments[0].endswith("TortoiseProc.exe") and "/command:commit" in arguments

        config = {"svn_patch_root": str(patch_root)}
        ticket = {"key": "DEV-123", "summary": "Unicode patch flow", "project": {"name": "Project 日本"}}
        fake = FakeService(working_copy)
        cancel_worker = SvnWorker("status", config, {"root": str(working_copy)}, fake)
        cancel_worker.cancel()
        assert fake.cancelled is True
        payload = {
            "root": str(working_copy), "profile_name": "Main app", "ticket": ticket,
            "paths": ["src/日本 file.txt", "src/new file.txt"], "label": "Ticket changes",
        }
        created = SvnWorker("create_patch", config, payload, fake)._dispatch()["artifact"]
        assert created.state == "created" and Path(created.patch_file).is_file()
        assert created.warnings == ("1 unversioned path(s) were not included in this patch.",)

        shelved = SvnWorker("shelve", config, payload, fake)._dispatch()["artifact"]
        assert shelved.state == "shelved"
        assert fake.reverted == ["src/日本 file.txt", "src/new file.txt"]
        store = PatchStore(patch_root)
        artifacts = store.list_for_ticket("dev-123")
        assert len(artifacts) == 2
        store.verify(shelved)

        # Two patches created in the same clock tick must not overwrite each other.
        frozen = datetime(2026, 8, 26, 9, 30, 0)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen.astimezone(tz) if tz else frozen.astimezone()

        with patch("modules.patch_store.datetime", FrozenDatetime):
            first = SvnWorker("create_patch", config, payload, fake)._dispatch()["artifact"]
            second = SvnWorker("create_patch", config, payload, fake)._dispatch()["artifact"]
        assert first.patch_file != second.patch_file
        assert Path(first.patch_file).is_file() and Path(second.patch_file).is_file()
        assert len(store.list_for_ticket("dev-123")) == 4
        for extra in (first, second):
            store.delete(extra)
        assert len(store.list_for_ticket("dev-123")) == 2

        apply_payload = {"root": str(working_copy), "manifest_file": shelved.manifest_file}
        preview = SvnWorker("patch_dry_run", config, apply_payload, fake)._dispatch()
        assert preview["conflicts"] is False and fake.applied[-1][1] is True
        applied = SvnWorker("apply_patch", config, apply_payload, fake)._dispatch()["artifact"]
        assert applied.state == "applied" and fake.applied[-1][1] is False
        assert store.load(applied.manifest_file).state == "applied"

        other_root = root / "Other Working Copy"
        other_root.mkdir()
        mismatched = {"root": str(other_root), "manifest_file": shelved.manifest_file}
        try:
            SvnWorker("patch_dry_run", config, mismatched, fake)._dispatch()
        except SvnValidationError:
            pass
        else:
            raise AssertionError("Patch was allowed against a different working-copy root")

        class BinaryService(FakeService):
            def diff(self, _root, paths):
                return command_result("diff", b"Cannot display: file marked as a binary type.\n")

        binary = BinaryService(working_copy)
        try:
            SvnWorker("shelve", config, payload, binary)._dispatch()
        except SvnValidationError as exc:
            assert "binary" in str(exc).casefold() and binary.reverted == []
        else:
            raise AssertionError("Binary change was reverted without a restorable patch")

        # Compare with base returns per-file before/after content, including the
        # added-file case where no base revision exists.
        (working_copy / "src" / "\u65e5\u672c file.txt").write_text("after\n", encoding="utf-8")
        compare = SvnWorker(
            "compare_base", config,
            {"root": str(working_copy), "paths": ["src/\u65e5\u672c file.txt", "src/new file.txt"]}, fake,
        )._dispatch()
        compared = {item["path"]: item for item in compare["files"]}
        assert compared["src/\u65e5\u672c file.txt"]["before"] == "before\n"
        assert compared["src/\u65e5\u672c file.txt"]["after"] == "after\n"
        assert compared["src/new file.txt"]["before"] == "" and compared["src/new file.txt"]["before_note"]
        assert compared["src/new file.txt"]["after_note"], "A missing working-copy file must explain itself"

        # Counting is cheap and stays scoped to the ticket folder.
        assert store.count_for_ticket("DEV-123") == 2
        assert store.count_for_ticket("DEV-999") == 0

        patch_file = Path(applied.patch_file)
        patch_file.write_bytes(patch_file.read_bytes() + b"tampered")
        try:
            store.verify(applied)
        except SvnValidationError as exc:
            assert "integrity" in str(exc).casefold()
        else:
            raise AssertionError("Tampered patch passed integrity verification")

        # The shelf reads every project and ticket folder, not just one ticket.
        other = {"key": "OPS-9", "summary": "Other project patch", "project": {"name": "Operations"}}
        SvnWorker("create_patch", config, {**payload, "ticket": other}, fake)._dispatch()
        every, unreadable = store.scan()
        assert unreadable == 0
        assert {artifact.ticket_key for artifact in every} == {"DEV-123", "OPS-9"}
        assert len(every) == len(store.scan("DEV-123")[0]) + len(store.scan("OPS-9")[0])
        assert [artifact.created_at for artifact in every] == sorted(
            (artifact.created_at for artifact in every), reverse=True
        ), "The shelf lists newest first"
        assert store.list_for_ticket("OPS-9")[0].project == "Operations"
        assert store.list_for_ticket("") == []

        # A corrupt manifest is counted rather than silently disappearing.
        broken = Path(every[0].manifest_file).parent / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        readable, unreadable = store.scan()
        assert unreadable == 1 and len(readable) == len(every)
        broken.unlink()
        for artifact in store.scan("OPS-9")[0]:
            store.delete(artifact)

        # Deleting a patch removes both files, prunes empty folders, and is scoped.
        remaining = store.list_for_ticket("DEV-123")
        assert len(remaining) == 2
        removed = SvnWorker(
            "delete_patch", config, {"manifest_file": remaining[0].manifest_file}, fake,
        )._dispatch()["removed"]
        assert len(removed) == 2
        assert not Path(remaining[0].patch_file).exists() and not Path(remaining[0].manifest_file).exists()
        assert store.count_for_ticket("DEV-123") == 1
        store.delete(remaining[1])
        assert store.count_for_ticket("DEV-123") == 0
        assert not (patch_root / "Project-\u65e5\u672c" / "DEV-123").exists(), "Empty ticket folder should be pruned"
        try:
            store.delete(remaining[1])
        except SvnValidationError:
            pass
        else:
            raise AssertionError("Deleting an already-removed patch was silently accepted")

        # The worker forwards lock and unlock through the changelist command.
        SvnWorker("changelist", config, {
            "root": str(working_copy), "paths": ["web.config"], "changelist": IGNORE_CHANGELIST,
        }, fake)._dispatch()
        SvnWorker("changelist", config, {
            "root": str(working_copy), "paths": ["web.config"], "changelist": "",
        }, fake)._dispatch()
        assert fake.changelists == [(["web.config"], IGNORE_CHANGELIST), (["web.config"], "")]

    print("svn workflow smoke ok")


if __name__ == "__main__":
    main()
