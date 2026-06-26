from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .constants import APP_NAME, ORG_NAME
from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    window = MainWindow()
    window.showMaximized()
    return app.exec()
