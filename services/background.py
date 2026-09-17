"""Short background operations whose lifetime is independent of an open dialog."""
from __future__ import annotations
from collections.abc import Callable
from PyQt6.QtCore import QThread, pyqtSignal

_running: set[BackgroundTask] = set()


class BackgroundTask(QThread):
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation: Callable):
        super().__init__()
        self.operation = operation
        self.finished.connect(self._cleanup)

    def start(self):
        _running.add(self)
        super().start()

    def run(self):
        try:
            self.ready.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))

    def _cleanup(self):
        _running.discard(self)
        self.deleteLater()


def running_tasks():
    return list(_running)
