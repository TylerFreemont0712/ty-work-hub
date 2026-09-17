from __future__ import annotations
import argparse
import os
import sys
import ctypes
from datetime import datetime
from pathlib import Path
import traceback
from tempfile import TemporaryDirectory

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from ui.styles import theme_styles
from ui.typography import configure_typography


ICON_PATH = Path(__file__).resolve().parent / "assets" / "ty-work-hub.ico"


def install_exception_handler() -> None:
    from config import APP_DIR
    def handle(exc_type, exc_value, exc_traceback):
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            with (APP_DIR / "crash.log").open("a", encoding="utf-8") as log:
                log.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}]\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=log)
            QMessageBox.critical(None, "Ty Work Hub error", f"An unexpected error was caught and logged to:\n{APP_DIR / 'crash.log'}\n\n{exc_value}")
        except Exception:
            traceback.print_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle


def main():
    parser = argparse.ArgumentParser(description="Ty Work Hub desktop workspace")
    parser.add_argument("--demo", action="store_true", help="Open a temporary demo workspace without using your Backlog credentials or saved app data")
    options = parser.parse_args()
    demo_home = None
    if options.demo:
        demo_home = TemporaryDirectory(prefix="ty-work-hub-demo-")
        os.environ["TY_WORK_APP_HOME"] = demo_home.name
    # Resolve data paths after selecting the workspace.
    from config import load_config
    import ui.main_window as window_module
    if options.demo:
        from services.demo import demo_tickets
        import services.sync_service as sync_module
        window_module.backlog_key = lambda: ""
        sync_module.demo_issues = demo_tickets
    install_exception_handler()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TyWorkHub.CommandCenter")
        except (AttributeError, OSError):
            pass
    config = load_config()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    configure_typography(app)
    app.setApplicationName("Ty Work Hub")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyleSheet(theme_styles(config["theme"], config["density"], config["font_size"]))
    window = window_module.MainWindow(config)
    if options.demo:
        from services.demo import seed_workspace
        seed_workspace(window)
        window.setWindowTitle("Ty Work Hub — Demo workspace")
    window.show()
    result = app.exec()
    # Qt objects are destroyed before the temporary data folder is removed.
    from PyQt6 import sip
    sip.delete(window)
    if demo_home:
        demo_home.cleanup()
    sys.exit(result)


if __name__ == "__main__":
    main()
