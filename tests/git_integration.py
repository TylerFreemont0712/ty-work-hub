"""Real local repositories only: Git workflow, path safety, and Qt integration."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

_home = TemporaryDirectory(prefix="ty-git-tests-")
os.environ["TY_WORK_APP_HOME"] = _home.name
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"

from modules.git_install import install_plan
from modules.git_service import GitError, GitService, discover_git, parse_status


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ty-git-case-")
        self.root = Path(self.temp.name) / "Repository 日本"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.service = GitService(str(self.root))

    def tearDown(self):
        # Git object files are read-only on Windows; clear only this test's files.
        if os.name == "nt":
            for path in self.root.parent.rglob("*"):
                if path.is_file():
                    path.chmod(0o600)
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output([discover_git(), *args], cwd=self.root, stderr=subprocess.STDOUT)

    def seed(self):
        (self.root / "hello.txt").write_text("one\n", encoding="utf-8")
        self.service.stage(["hello.txt"])
        self.service.commit("Initial", hashlib.sha256(self.service.staged_patch()).hexdigest())

    def test_initial_commit_unstage_and_literal_names(self):
        filename = "[special] 日本.txt"
        (self.root / filename).write_text("hello\n", encoding="utf-8")
        self.assertEqual(self.service.snapshot()["changes"][0].path, filename)
        self.assertIn("hello", self.service.diff([filename]))
        self.service.stage([filename])
        self.service.unstage([filename])
        self.assertTrue((self.root / filename).exists())
        self.assertFalse(self.service.snapshot()["changes"][0].staged)
        self.service.stage([filename])
        self.service.commit("Initial", hashlib.sha256(self.service.staged_patch()).hexdigest())
        self.assertEqual(self.service.snapshot()["changes"], [])

    def test_commit_rejects_changed_preview_and_preserves_unstaged(self):
        self.seed()
        target = self.root / "hello.txt"
        target.write_text("two\n", encoding="utf-8")
        self.service.stage(["hello.txt"])
        digest = hashlib.sha256(self.service.staged_patch()).hexdigest()
        target.write_text("three\n", encoding="utf-8")
        self.service.commit("Only staged", digest)
        self.assertEqual(self.git("show", "HEAD:hello.txt").replace(b"\r\n", b"\n"), b"two\n")
        self.assertIn("three", self.service.diff(["hello.txt"]))
        self.service.stage(["hello.txt"])
        with self.assertRaisesRegex(GitError, "changed after preview"):
            self.service.commit("Stale", digest)

    def test_stash_retained_and_patch_roundtrip(self):
        self.seed()
        (self.root / "hello.txt").write_text("two\n", encoding="utf-8")
        (self.root / "new.txt").write_text("new\n", encoding="utf-8")
        self.service.stage(["new.txt"])
        patch_bytes = self.service.patch()
        self.assertIn(b"new.txt", patch_bytes)
        self.service.stash("Saved work")
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["changes"], [])
        oid = snapshot["stashes"][0].split()[0]
        self.service.apply_stash(oid)
        self.assertEqual(len(self.service.snapshot()["stashes"]), 1)
        self.assertEqual((self.root / "hello.txt").read_text(), "two\n")
        self.assertTrue((self.root / "new.txt").exists())

    def test_scope_cancel_and_missing_dependency(self):
        for value in ("../escape", ".git/config", "/tmp/escape", ""):
            with self.assertRaises(GitError):
                self.service.stage([value])
        self.service.cancel()
        with self.assertRaisesRegex(GitError, "cancelled"):
            self.service.snapshot()
        with self.assertRaises(GitError):
            GitService(str(self.root), str(self.root / "missing-git"))
        with self.assertRaises(GitError):
            parse_status(b"bad")

    def test_remote_fetch_push_pull_local_bare_repository(self):
        self.seed()
        remote = self.root.parent / "remote.git"
        self.git("init", "--bare", "--initial-branch=main", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "--set-upstream", "origin", "main")
        self.service.remote_action("fetch")
        self.service.remote_action("pull")
        self.service.remote_action("push")
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "origin/main"))

    def test_install_plans_use_known_package_managers(self):
        for manager in ("apt-get", "dnf", "pacman", "zypper"):
            locate = lambda name: "/usr/bin/" + name if name in {manager, "pkexec"} else None
            command, _ = install_plan("linux", locate)
            self.assertEqual(command[:2], ["/usr/bin/pkexec", "/usr/bin/" + manager])
            self.assertEqual(command[-1], "git")
        command, help_text = install_plan("linux", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
        self.assertFalse(command)
        self.assertIn("sudo apt-get", help_text)
        self.assertFalse(install_plan("linux", lambda _: None)[0])
        self.assertIn("Git.Git", install_plan("win32", lambda _: "winget")[0])

    @unittest.skipIf(os.name == "nt", "Case-sensitive filesystem check")
    def test_linux_svn_paths_remain_case_sensitive(self):
        from modules.svn_service import normalize_scoped_paths
        from modules.svn_models import SvnChange
        from services.svn_workers import SvnWorker
        (self.root / "File.txt").touch()
        (self.root / "file.txt").touch()
        self.assertEqual(len(normalize_scoped_paths(self.root, ["File.txt", "file.txt"])), 2)
        upper, lower = SvnChange("File.txt", "modified"), SvnChange("file.txt", "modified")
        self.assertEqual(SvnWorker._selected_patchable_changes([upper, lower], ["File.txt"]), [upper])

    def test_git_settings_and_ui_worker(self):
        from PyQt6 import sip
        from PyQt6.QtCore import QItemSelectionModel
        from PyQt6.QtWidgets import QApplication
        from config import DEFAULTS
        from ui.git_widget import GitWidget
        from ui.git_settings import GitSettings
        from ui.typography import configure_typography
        from ui.styles import theme_styles
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        configure_typography(app)
        app.setStyleSheet(theme_styles("dark"))
        config = dict(DEFAULTS, git_repository=str(self.root))
        self.seed()
        (self.root / "hello.txt").write_text("changed\n")
        widget = GitWidget(config)
        settings = GitSettings(config)

        def wait():
            end = time.monotonic() + 10
            while widget.worker and time.monotonic() < end:
                app.processEvents()
                time.sleep(.005)
            self.assertIsNone(widget.worker)

        try:
            widget.resize(836, 550)
            widget.show()
            widget.refresh()
            wait()
            self.assertEqual(widget.table.rowCount(), 1)
            self.assertEqual(widget.branch.text(), "main")
            widget.table.selectionModel().select(widget.table.model().index(0, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            widget.run("stage", widget.selected())
            wait()
            self.assertEqual(widget.table.item(0, 0).text(), "M")
            with patch("ui.git_settings.execute_install") as install:
                settings.detect()
                self.assertTrue(settings.executable.text())
                settings.install_git()
                install.assert_not_called()
            self.assertEqual(settings.values()["git_repository"], str(self.root))
            image_path = Path(__file__).resolve().parents[1] / "artifacts" / ("git-linux.png" if os.name != "nt" else "git-windows.png")
            image_path.parent.mkdir(exist_ok=True)
            widget.grab().save(str(image_path))
        finally:
            if widget.worker:
                widget.worker.cancel()
                widget.worker.wait()
            sip.delete(widget)
            sip.delete(settings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
