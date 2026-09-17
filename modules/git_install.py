"""Install plans use the OS package manager and its native authorization prompt."""
from __future__ import annotations
import os
import shutil
import subprocess
import sys


def install_plan(platform=None, which=None):
    platform, which = platform or sys.platform, which or shutil.which
    if platform == "win32":
        manager = which("winget")
        if manager:
            return [manager, "install", "--id", "Git.Git", "--exact", "--source", "winget", "--accept-source-agreements", "--accept-package-agreements"], "Git for Windows via winget"
        return [], "Install Git for Windows from https://git-scm.com/install/windows, then click Detect Git."
    if platform == "darwin":
        manager = which("brew")
        return ([manager, "install", "git"], "Git via Homebrew") if manager else ([], "Install Apple's command line tools with xcode-select --install, then click Detect Git.")
    managers = [("apt-get", ["install", "-y", "git"]), ("dnf", ["install", "-y", "git"]),
                ("zypper", ["--non-interactive", "install", "git"]), ("pacman", ["-S", "--needed", "--noconfirm", "git"])]
    for name, arguments in managers:
        manager = which(name)
        if manager:
            authorization = which("pkexec")
            if not authorization:
                return [], f"Run in your terminal: sudo {name} {' '.join(arguments)}\nThen click Detect Git. Install polkit to enable the in-app installer."
            return [authorization, manager, *arguments], f"Git via {name}. Your desktop will request administrator authorization."
    return [], "Use your distribution's package manager to install Git: https://git-scm.com/install/linux. Then click Detect Git."


def execute_install(command):
    if not command:
        raise RuntimeError("No supported package manager was found.")
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=600, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The installer timed out. Check your system package manager before retrying.") from exc
    output = result.stdout.decode("utf-8", "replace")
    if result.returncode:
        raise RuntimeError(output[-5000:] or "Git installation was cancelled or failed.")
    return output[-5000:] or "Git installation completed."
