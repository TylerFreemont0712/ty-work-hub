from __future__ import annotations
import os, subprocess, sys, webbrowser
from pathlib import Path

def open_codex():
    if sys.platform == "darwin": subprocess.Popen(["open", "-a", "Codex"])
    elif sys.platform == "win32": os.startfile("codex://")
    else: subprocess.Popen(["codex"])
def open_backlog(): webbrowser.open("https://mirax.backlog.com/find/")
def run_custom(command: str):
    """Run a command explicitly configured by the local user."""
    if command.strip(): subprocess.Popen(command, shell=True)
def open_path(path: Path):
    if sys.platform == "win32": os.startfile(str(path))
    elif sys.platform == "darwin": subprocess.Popen(["open", str(path)])
    else: subprocess.Popen(["xdg-open", str(path)])
