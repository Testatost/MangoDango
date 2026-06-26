from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .constants import APP_NAME, ORG_NAME
from .main_window import MainWindow


def resource_path(*parts: str) -> Path:
    """Return a resource path that works in source mode and PyInstaller one-file mode."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).joinpath(*parts)
    return Path(__file__).resolve().parents[1].joinpath(*parts)


def load_app_icon() -> QIcon:
    """Load the application icon from bundled or source assets."""
    candidates = [
        resource_path("icon.ico"),
        resource_path("icon.png"),
        resource_path("logo-small.png"),
        resource_path("logo.png"),
    ]
    for path in candidates:
        if path.exists():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def set_windows_app_user_model_id() -> None:
    """Make Windows group the taskbar entry under MangoDango and use the app icon."""
    if sys.platform != "win32":
        return
    try:
        app_id = "Testatost.MangoDango.1.0"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def main() -> int:
    set_windows_app_user_model_id()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)

    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = MainWindow()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)

    window.showMaximized()
    return app.exec()
