"""Owned, cancellable background Git operations."""
from PyQt6.QtCore import QThread, pyqtSignal
from modules.git_service import GitService


class GitWorker(QThread):
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root, executable, operation, *args, parent=None):
        super().__init__(parent)
        self.root, self.executable, self.operation, self.args = root, executable, operation, args
        self.service = None

    def cancel(self):
        self.requestInterruption()
        if self.service:
            self.service.cancel()

    def run(self):
        try:
            self.service = GitService(self.root, self.executable)
            if self.isInterruptionRequested():
                return
            self.ready.emit(getattr(self.service, self.operation)(*self.args))
        except Exception as exc:
            self.failed.emit(str(exc))
