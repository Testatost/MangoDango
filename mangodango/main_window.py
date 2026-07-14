from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QLocale, QPointF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QDoubleSpinBox,
    QDialog,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    APP_NAME,
    DEFAULT_ACCENT,
    DEFAULT_OUTPUT_DIR,
    IMAGE_FORMATS,
    ORG_NAME,
    OUTPUT_MODES,
    QUEUE_FILE_VERSION,
    READING_STYLES,
)
from .i18n import SUPPORTED_LANGUAGES, TranslatableText, Translator, language_label, normalize_language, tr_message
from .automation import AutomationSchedule
from .models import ChapterEntry, ItemSettings, MangaEntry
from .reading_state import ReadingStateStore, manga_id_for_path
from .mobile_server import (
    DEFAULT_MOBILE_READER_HOST,
    DEFAULT_MOBILE_READER_PORT,
    MobileLibraryServer,
    MobileReaderConfigurationError,
    mobile_reader_urls,
)
from .scraper import IMAGE_EXTENSIONS, chapter_exists_on_disk, chapter_sort_key, natural_sort_key, normalize_weebcentral_url, read_manga_metadata_dir, sanitize_filename, write_manga_metadata, write_manga_metadata_dir
from .ui.dialogs import AppSettingsDialog, BusyProgressDialog, ItemSettingsDialog, MangaReaderDialog, PreferencesDialog, UpdateResultsDialog, localized_question, localized_text_input
from .ui.styles import ThemeSettings, apply_theme
from .workers import QueueDownloadWorker, ResolveWorker, UpdateCheckWorker

ROLE_KIND = int(Qt.ItemDataRole.UserRole) + 1
ROLE_MANGA_ID = int(Qt.ItemDataRole.UserRole) + 2
ROLE_CHAPTER_ID = int(Qt.ItemDataRole.UserRole) + 3

INLINE_WIDGET_ROW_LIMIT = 600


class LibraryCardButton(QToolButton):
    openRequested = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def make_favorite_star(size: int = 18) -> QPixmap:
    """A yellow five-point star with a 1px black outline (favourite badge)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cx = cy = size / 2.0
    outer = size / 2.0 - 1.5
    inner = outer * 0.42
    points = []
    import math
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = -math.pi / 2 + index * math.pi / 5
        points.append(QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    painter.setBrush(QBrush(QColor("#FFCC00")))
    painter.setPen(QPen(QColor("#000000"), 1.0))
    painter.drawPolygon(QPolygonF(points))
    painter.end()
    return pixmap


class LibraryCard(QFrame):
    """A library cover card with an open action, a favourite star next to the
    title and a small "…" menu button by the chapter line.

    Built entirely with layouts (no manually positioned overlay widgets), which
    is both faster and far more robust than absolute geometry on every resize.
    """

    openRequested = Signal()
    menuRequested = Signal()

    def __init__(self, star_pixmap: QPixmap, menu_tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LibraryCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(190, 320)
        self.setMaximumWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.cover = QLabel()
        self.cover.setObjectName("LibraryCover")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedHeight(240)
        self.cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.cover)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        self.title_label = QLabel()
        self.title_label.setObjectName("LibraryTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.star = QLabel()
        self.star.setObjectName("LibraryStar")
        self.star.setPixmap(star_pixmap)
        self.star.setFixedSize(star_pixmap.size())
        self.star.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.star.setVisible(False)
        title_row.addStretch(1)
        title_row.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.star, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(4)
        self.info_label = QLabel()
        self.info_label.setObjectName("LibraryInfo")
        self.info_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("LibraryMenuButton")
        self.menu_button.setText("\u22ef")
        self.menu_button.setToolTip(menu_tooltip)
        self.menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_button.setFixedSize(30, 26)
        self.menu_button.clicked.connect(self.menuRequested)
        bottom_row.addWidget(self.info_label, 1)
        bottom_row.addWidget(self.menu_button, 0)
        layout.addLayout(bottom_row)

    def set_cover(self, pixmap: QPixmap) -> None:
        self.cover.setPixmap(pixmap)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)

    def set_favorite(self, favorite: bool) -> None:
        self.star.setVisible(favorite)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)



class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        saved_language = self.settings.value("ui/language", None)
        self.language = normalize_language(str(saved_language) if saved_language else QLocale.system().name())
        self.translator = Translator(self.language)
        self.custom_themes = self._load_custom_themes()
        self.theme = ThemeSettings.from_mapping({
            "mode": str(self.settings.value("ui/theme", "dark") or "dark"),
            "accent": str(self.settings.value("ui/accent", DEFAULT_ACCENT) or DEFAULT_ACCENT),
            "preset": str(self.settings.value("ui/preset", "midnight") or "midnight"),
            "window": str(self.settings.value("ui/window", "") or ""),
            "panel": str(self.settings.value("ui/panel", "") or ""),
            "input": str(self.settings.value("ui/input", "") or ""),
            "text": str(self.settings.value("ui/text", "") or ""),
            "button": str(self.settings.value("ui/button", "") or ""),
            "border": str(self.settings.value("ui/border", "") or ""),
        })
        self.mangas: list[MangaEntry] = []
        self.automation = AutomationSchedule.from_json(str(self.settings.value("automation/schedule", "") or ""))
        self._automation_last_run = datetime.now()
        self._automation_timer: QTimer | None = None
        self._closing = False
        self._await_close_timer: QTimer | None = None
        self._library_entries: list[dict] | None = None
        self._library_cards_by_id: dict[str, LibraryCard] = {}
        self._library_dirty: bool = True
        self._reading_store: ReadingStateStore | None = None
        self._reading_store_root: Path | None = None
        self._mobile_server: MobileLibraryServer | None = None
        self._mobile_reader_enabled: bool = False
        self._mobile_reader_port: int = DEFAULT_MOBILE_READER_PORT
        self._mobile_reader_host: str = DEFAULT_MOBILE_READER_HOST
        self._mobile_reader_last_error: str = ""
        self.resolve_worker: ResolveWorker | None = None
        self.update_worker: UpdateCheckWorker | None = None
        self.download_worker: QueueDownloadWorker | None = None
        self._download_finished_result: tuple[bool, bool] | None = None
        self.collect_dialog: BusyProgressDialog | None = None
        self.update_progress_dialog: BusyProgressDialog | None = None
        self._background_update_check = False
        self._download_updates_immediately = False
        self._building_tree = False
        self._refresh_tree_pending = False
        self._restoring = False
        self._log_visible = True
        self._undo_stack: list[str] = []
        self._redo_stack: list[str] = []
        self._shortcuts: list[QShortcut] = []
        self._use_inline_widgets = True
        self._log_entries: list[object] = []

        self.url_input = QLineEdit()
        self.output_input = QLineEdit()
        self.paste_button = QPushButton()
        self.browse_button = QPushButton()
        self.add_button = QPushButton()
        self.start_button = QPushButton()
        self.stop_button = QPushButton()
        self.resume_button = QPushButton()
        self.reader_button = QPushButton()
        self.remove_button = QPushButton()
        self.clear_button = QPushButton()
        self.item_settings_button = QPushButton()
        self.save_button = QPushButton()
        self.load_button = QPushButton()
        self.search_button = QPushButton()
        self.log_toggle_button = QPushButton()
        self.reset_button = QPushButton()
        self.settings_button = QPushButton()
        self.home_button = QPushButton()
        self.language_combo = QComboBox()
        self.reading_combo = QComboBox()
        self.output_combo = QComboBox()
        self.format_combo = QComboBox()
        self.keep_images = QCheckBox()
        self.check_updates_on_startup = QCheckBox()
        self.auto_download_updates = QCheckBox()
        self.threads = QSpinBox()
        self.delay = QDoubleSpinBox()
        self.tree = QTreeWidget()
        self.progress = QProgressBar()
        self.status_label = QLabel()
        self.log_view = QTextEdit()
        self.logo_label = QLabel()
        self.title_label = QLabel()
        self.library_button = QPushButton()
        self.downloader_button = QPushButton()
        self.refresh_library_button = QPushButton()
        self.library_sort_combo = QComboBox()
        self.library_page = QWidget()
        self.library_scroll = QScrollArea()
        self.library_content = QWidget()
        self.library_grid = QGridLayout(self.library_content)
        self.library_empty_label = QLabel()
        self.downloader_page = QWidget()
        self.input_group = QGroupBox()
        self.defaults_group = QGroupBox()

        self._build_ui()
        self._load_settings()
        self._connect_signals()
        self._install_shortcuts()
        self.retranslate_ui()
        apply_theme(QApplication.instance(), self.theme)
        self.refresh_tree()
        # Load the library only now that the saved output directory has been
        # applied, otherwise the first scan would run against the default folder
        # and cache an empty result.
        self._library_dirty = True
        self.show_library_view()
        QTimer.singleShot(0, self._sync_mobile_reader_server)
        self.resize(1280, 820)
        QTimer.singleShot(0, self._log_startup_state)
        QTimer.singleShot(900, self.maybe_check_updates_on_startup)
        self._start_automation_timer()

    def tr(self, key: str, **kwargs) -> str:
        return self.translator.tr(key, **kwargs)

    def _build_ui(self) -> None:
        self.setWindowTitle(APP_NAME)

        # Window icon fallback. QApplication also sets this in app.py, but keeping
        # it here makes direct window construction reliable in development.
        try:
            import sys
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            for icon_name in ("icon.ico", "icon.png", "logo-small.png", "logo.png"):
                icon_path = base / icon_name
                if icon_path.exists():
                    icon = QIcon(str(icon_path))
                    if not icon.isNull():
                        self.setWindowIcon(icon)
                        break
        except Exception:
            pass
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.logo_label.setObjectName("HeaderLogo")
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setMinimumHeight(58)
        self.logo_label.setMaximumHeight(78)
        self._load_header_logo()

        self.title_label.setObjectName("Title")
        self.title_label.setVisible(False)
        self.home_button.setObjectName("HomeButton")
        self.home_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_button.setMinimumHeight(24)
        self.home_button.setFlat(False)
        self.home_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        header_row = QGridLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setHorizontalSpacing(6)
        header_row.addWidget(QWidget(), 0, 0)
        header_row.addWidget(self.logo_label, 0, 1, Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self.home_button, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        header_row.setColumnStretch(0, 1)
        header_row.setColumnStretch(1, 0)
        header_row.setColumnStretch(2, 1)
        outer.addLayout(header_row)

        mode_bar = QGridLayout()
        mode_bar.setContentsMargins(0, 0, 0, 0)
        mode_bar.setHorizontalSpacing(6)
        mode_bar.setVerticalSpacing(0)
        mode_buttons = QHBoxLayout()
        mode_buttons.setContentsMargins(0, 0, 0, 0)
        mode_buttons.setSpacing(6)
        mode_buttons.addWidget(self.library_button)
        mode_buttons.addWidget(self.downloader_button)

        right_controls = QHBoxLayout()
        right_controls.setContentsMargins(0, 0, 0, 0)
        right_controls.setSpacing(6)
        self.library_sort_combo.setMinimumWidth(190)
        self.library_sort_combo.setToolTip(self.tr("library_sort_tooltip"))
        right_controls.addWidget(self.library_sort_combo)
        right_controls.addWidget(self.language_combo)
        right_controls.addWidget(self.settings_button)
        right_controls.addWidget(self.refresh_library_button)

        mode_bar.addWidget(QWidget(), 0, 0)
        mode_bar.addLayout(mode_buttons, 0, 1, Qt.AlignmentFlag.AlignCenter)
        mode_bar.addLayout(right_controls, 0, 2, Qt.AlignmentFlag.AlignRight)
        mode_bar.setColumnStretch(0, 1)
        mode_bar.setColumnStretch(1, 0)
        mode_bar.setColumnStretch(2, 1)
        outer.addLayout(mode_bar)

        self.library_page.setObjectName("LibraryPage")
        library_layout = QVBoxLayout(self.library_page)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.setSpacing(6)
        self.library_empty_label.setObjectName("Muted")
        self.library_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.library_grid.setContentsMargins(10, 10, 10, 10)
        self.library_grid.setHorizontalSpacing(14)
        self.library_grid.setVerticalSpacing(14)
        self.library_scroll.setWidget(self.library_content)
        self.library_scroll.setWidgetResizable(True)
        library_layout.addWidget(self.library_empty_label)
        library_layout.addWidget(self.library_scroll, 1)
        outer.addWidget(self.library_page, 1)

        downloader_layout = QVBoxLayout(self.downloader_page)
        downloader_layout.setContentsMargins(0, 0, 0, 0)
        downloader_layout.setSpacing(6)

        self.input_group.setObjectName("Panel")
        input_layout = QGridLayout(self.input_group)
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setHorizontalSpacing(6)
        input_layout.setVerticalSpacing(6)
        self.url_input.setMinimumHeight(32)
        self.url_input.setMaximumHeight(32)
        self.url_input.setClearButtonEnabled(True)
        self.output_input.setPlaceholderText(self.tr("target_dir_placeholder"))
        self._input_url_label = QLabel()
        self._input_dir_label = QLabel()
        input_layout.addWidget(self._input_url_label, 0, 0)
        input_layout.addWidget(self.url_input, 0, 1, 1, 4)
        input_layout.addWidget(self.paste_button, 0, 5)
        input_layout.addWidget(self.add_button, 0, 6)
        input_layout.addWidget(self._input_dir_label, 1, 0)
        input_layout.addWidget(self.output_input, 1, 1, 1, 4)
        input_layout.addWidget(self.browse_button, 1, 5, 1, 2)
        downloader_layout.addWidget(self.input_group)

        self.defaults_group.setObjectName("Panel")
        defaults_layout = QGridLayout(self.defaults_group)
        defaults_layout.setContentsMargins(10, 8, 10, 8)
        defaults_layout.setHorizontalSpacing(8)
        defaults_layout.setVerticalSpacing(8)
        self._defaults_reading_label = QLabel()
        self._defaults_output_label = QLabel()
        self._defaults_format_label = QLabel()
        self._defaults_threads_label = QLabel()
        self._defaults_delay_label = QLabel()

        for checkbox in (self.keep_images, self.check_updates_on_startup, self.auto_download_updates):
            checkbox.setObjectName("SettingsCheckBox")
            checkbox.setMinimumHeight(30)
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)

        self.keep_images.setMinimumWidth(225)
        self.check_updates_on_startup.setMinimumWidth(285)
        self.auto_download_updates.setMinimumWidth(295)

        # Row 1: format defaults
        defaults_layout.addWidget(self._defaults_reading_label, 0, 0)
        defaults_layout.addWidget(self.reading_combo, 0, 1)
        defaults_layout.addWidget(self._settings_separator(), 0, 2, 2, 1)

        defaults_layout.addWidget(self._defaults_output_label, 0, 3)
        defaults_layout.addWidget(self.output_combo, 0, 4)
        defaults_layout.addWidget(self._settings_separator(), 0, 5, 2, 1)

        defaults_layout.addWidget(self._defaults_format_label, 0, 6)
        defaults_layout.addWidget(self.format_combo, 0, 7)

        # Row 2: behavior and performance
        defaults_layout.addWidget(self.keep_images, 1, 0, 1, 2)
        defaults_layout.addWidget(self._settings_separator(), 1, 2)

        defaults_layout.addWidget(self.check_updates_on_startup, 1, 3, 1, 2)
        defaults_layout.addWidget(self._settings_separator(), 1, 5)

        defaults_layout.addWidget(self.auto_download_updates, 1, 6, 1, 2)
        defaults_layout.addWidget(self._settings_separator(), 1, 8)

        defaults_layout.addWidget(self._defaults_threads_label, 1, 9)
        defaults_layout.addWidget(self.threads, 1, 10)
        defaults_layout.addWidget(self._settings_separator(), 1, 11)

        defaults_layout.addWidget(self._defaults_delay_label, 1, 12)
        defaults_layout.addWidget(self.delay, 1, 13)

        defaults_layout.setColumnStretch(1, 2)
        defaults_layout.setColumnStretch(4, 2)
        defaults_layout.setColumnStretch(7, 2)
        downloader_layout.addWidget(self.defaults_group)
        # The visible "Globale Einstellungen" panel has moved into the
        # "Einstellungen" dialog. The widgets stay alive as the backing store for
        # the download defaults, so they are kept but hidden here.
        self.defaults_group.setVisible(False)

        action_bar = QGridLayout()
        action_bar.setContentsMargins(0, 0, 0, 0)
        action_bar.setHorizontalSpacing(6)
        action_bar.setVerticalSpacing(6)
        first_row = (
            self.start_button,
            self.stop_button,
            self.resume_button,
            self.reader_button,
            self.item_settings_button,
            self.remove_button,
        )
        second_row = (
            self.clear_button,
            self.search_button,
            self.save_button,
            self.load_button,
            self.log_toggle_button,
            self.reset_button,
        )
        for column, button in enumerate(first_row):
            action_bar.addWidget(button, 0, column)
        for column, button in enumerate(second_row):
            action_bar.addWidget(button, 1, column)
        for column in range(6):
            action_bar.setColumnStretch(column, 1)
        downloader_layout.addLayout(action_bar)

        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnCount(8)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemDoubleClicked.connect(self.open_item_url)

        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_view.setMinimumHeight(120)

        self.bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(self.bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(2)
        bottom_layout.addWidget(self.progress)
        bottom_layout.addWidget(self.log_view)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setHandleWidth(2)
        queue_panel = QWidget()
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.setSpacing(0)
        queue_layout.addWidget(self.tree)
        self.splitter.addWidget(queue_panel)
        self.splitter.addWidget(self.bottom_panel)
        self.splitter.setSizes([680, 160])
        downloader_layout.addWidget(self.splitter, 1)
        outer.addWidget(self.downloader_page, 1)

        self.progress.setRange(0, 100)
        self.stop_button.setEnabled(False)
        self.resume_button.setEnabled(True)
        self.threads.setRange(1, 10)
        self.delay.setRange(0, 30)
        self.delay.setDecimals(1)
        self.delay.setSingleStep(0.5)
        self._sync_log_layout()

    def _connect_signals(self) -> None:
        self.paste_button.clicked.connect(self.paste_urls)
        self.browse_button.clicked.connect(self.choose_output_folder)
        self.add_button.clicked.connect(self.add_urls)
        self.start_button.clicked.connect(self.start_download)
        self.stop_button.clicked.connect(self.stop_download)
        self.resume_button.clicked.connect(self.resume_download)
        self.reader_button.clicked.connect(self.open_reader)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_queue)
        self.item_settings_button.clicked.connect(self.open_item_settings)
        self.save_button.clicked.connect(self.save_queue)
        self.load_button.clicked.connect(self.load_queue)
        self.search_button.clicked.connect(self.search_queue)
        self.log_toggle_button.clicked.connect(self.toggle_log)
        self.reset_button.clicked.connect(self.reset_application_data)
        self.settings_button.clicked.connect(self.open_app_settings)
        self.home_button.pressed.connect(self.open_weebcentral_homepage)
        self.library_button.clicked.connect(self.show_library_view)
        self.downloader_button.clicked.connect(self.show_downloader_view)
        self.refresh_library_button.clicked.connect(self.refresh_library)
        self.library_sort_combo.currentIndexChanged.connect(self.on_library_sort_changed)
        self.language_combo.currentIndexChanged.connect(self.on_language_changed)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self.output_input.editingFinished.connect(self._save_settings)
        for combo in (self.reading_combo, self.output_combo, self.format_combo):
            combo.currentIndexChanged.connect(lambda _index=0: self._save_settings())
        self.keep_images.stateChanged.connect(lambda _state=0: self._save_settings())
        self.check_updates_on_startup.stateChanged.connect(lambda _state=0: self._save_settings())
        self.auto_download_updates.stateChanged.connect(lambda _state=0: self._save_settings())
        self.threads.valueChanged.connect(lambda _value=0: self._save_settings())
        self.delay.valueChanged.connect(lambda _value=0.0: self._save_settings())

    def _settings_separator(self) -> QFrame:
        separator = QFrame()
        separator.setObjectName("SettingsSeparator")
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setMinimumHeight(26)
        return separator


    def _install_shortcuts(self) -> None:
        specs = [
            (QKeySequence.StandardKey.Delete, self.remove_selected),
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
            (QKeySequence("Del"), self.remove_selected),
            (QKeySequence("Ctrl+Z"), self.undo),
            (QKeySequence("Ctrl+Y"), self.redo),
        ]
        for sequence, slot in specs:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _load_settings(self) -> None:
        if self.settings.contains("paths/output_dir"):
            saved_output = str(self.settings.value("paths/output_dir", "") or "").strip()
            if saved_output and saved_output != DEFAULT_OUTPUT_DIR:
                self.output_input.setText(saved_output)
        self.threads.setValue(int(self.settings.value("defaults/image_threads", 4) or 4))
        self.delay.setValue(float(self.settings.value("defaults/request_delay", 1.0) or 1.0))
        self.keep_images.setChecked(str(self.settings.value("defaults/keep_images", "true")).lower() == "true")
        self.check_updates_on_startup.setChecked(str(self.settings.value("updates/check_on_startup", "false")).lower() == "true")
        self.auto_download_updates.setChecked(str(self.settings.value("updates/auto_download", "false")).lower() == "true")
        self._mobile_reader_enabled = str(self.settings.value("mobile_reader/enabled", "false") or "false").lower() == "true"
        try:
            self._mobile_reader_port = max(1024, min(65535, int(self.settings.value("mobile_reader/port", DEFAULT_MOBILE_READER_PORT) or DEFAULT_MOBILE_READER_PORT)))
        except (TypeError, ValueError):
            self._mobile_reader_port = DEFAULT_MOBILE_READER_PORT
        self._mobile_reader_host = str(self.settings.value("mobile_reader/host", DEFAULT_MOBILE_READER_HOST) or DEFAULT_MOBILE_READER_HOST).strip() or DEFAULT_MOBILE_READER_HOST
        self._set_combo_value_later = {
            "reading": str(self.settings.value("defaults/reading_style", "long_strip") or "long_strip"),
            "output": str(self.settings.value("defaults/output_mode", "images") or "images"),
            "format": str(self.settings.value("defaults/image_format", "original") or "original"),
            "library_sort": str(self.settings.value("library/sort_mode", "latest") or "latest"),
        }

    def _load_custom_themes(self) -> list[dict]:
        raw = str(self.settings.value("ui/custom_themes", "[]") or "[]")
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_custom_themes(self) -> None:
        self.settings.setValue("ui/custom_themes", json.dumps(self.custom_themes, ensure_ascii=False))

    def _save_settings(self) -> None:
        output_path = self.output_input.text().strip()
        if output_path:
            self.settings.setValue("paths/output_dir", output_path)
        else:
            self.settings.remove("paths/output_dir")
        self.settings.setValue("defaults/reading_style", self.reading_combo.currentData() or "long_strip")
        self.settings.setValue("defaults/output_mode", self.output_combo.currentData() or "images")
        self.settings.setValue("defaults/image_format", self.format_combo.currentData() or "original")
        self.settings.setValue("defaults/keep_images", "true" if self.keep_images.isChecked() else "false")
        self.settings.setValue("updates/check_on_startup", "true" if self.check_updates_on_startup.isChecked() else "false")
        self.settings.setValue("updates/auto_download", "true" if self.auto_download_updates.isChecked() else "false")
        self.settings.setValue("defaults/image_threads", self.threads.value())
        self.settings.setValue("defaults/request_delay", self.delay.value())
        self.settings.setValue("library/sort_mode", self.library_sort_mode())
        self.settings.setValue("automation/schedule", self.automation.to_json())
        self.settings.setValue("mobile_reader/enabled", "true" if self._mobile_reader_enabled else "false")
        self.settings.setValue("mobile_reader/port", int(self._mobile_reader_port))
        self.settings.setValue("mobile_reader/host", self._mobile_reader_host)
        self.settings.setValue("ui/language", self.language)
        theme = self.theme.normalized()
        self.settings.setValue("ui/theme", theme.mode)
        self.settings.setValue("ui/accent", theme.accent)
        self.settings.setValue("ui/preset", theme.preset)
        self.settings.setValue("ui/window", theme.window)
        self.settings.setValue("ui/panel", theme.panel)
        self.settings.setValue("ui/input", theme.input)
        self.settings.setValue("ui/text", theme.text)
        self.settings.setValue("ui/button", theme.button)
        self.settings.setValue("ui/border", theme.border)
        self._save_custom_themes()

    def _prepare_combo_popup(self, combo: QComboBox, item_count: int) -> None:
        # Qt styles can otherwise render very small popups with scrollbars even
        # for four or seven entries. Make the popup tall enough for all values.
        visible = max(1, int(item_count))
        combo.setMaxVisibleItems(visible)
        combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        combo.view().setMinimumHeight((visible * 28) + 8)

    def _fill_combo(self, combo: QComboBox, values: Iterable[str], prefix: str, selected: str | None = None) -> None:
        values = tuple(values)
        current = selected or str(combo.currentData() or "")
        combo.blockSignals(True)
        combo.clear()
        for value in values:
            combo.addItem(self.tr(prefix + value), value)
        self._prepare_combo_popup(combo, len(values))
        idx = combo.findData(current)
        if idx < 0 and combo.count():
            idx = 0
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _status_text(self, status: str) -> str:
        return self.tr("status_" + (status or "pending"))

    def _fill_library_sort_combo(self, selected: str | None = None) -> None:
        current = selected or self.library_sort_mode()
        self.library_sort_combo.blockSignals(True)
        self.library_sort_combo.clear()
        # "Favorites" is always offered; when nothing is favorited the library
        # simply shows an empty view under that filter.
        for value in ("latest", "az", "favorites"):
            self.library_sort_combo.addItem(self.tr("library_sort_" + value), value)
        index = self.library_sort_combo.findData(current)
        if index < 0:
            index = 0
        self.library_sort_combo.setCurrentIndex(index)
        self.library_sort_combo.blockSignals(False)

    def _has_favorites(self) -> bool:
        return any(entry.get("favorite") for entry in (self._library_entries or []))

    def library_sort_mode(self) -> str:
        return str(self.library_sort_combo.currentData() or getattr(self, "_set_combo_value_later", {}).get("library_sort") or "latest")

    def on_library_sort_changed(self) -> None:
        self._save_settings()
        if not self.library_page.isVisible():
            return
        # Re-sort the cached scan instead of hitting the disk again. This makes
        # switching between "A–Z" and "Zuletzt aktualisiert" instant.
        if self._library_entries is not None:
            self._render_library(self._sort_library_entries(self._library_entries))
        else:
            self.refresh_library()


    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.tr("window_title"))
        self.title_label.setText("")
        self.title_label.setVisible(False)
        self.home_button.setText(self.tr("home_weebcentral_button"))
        self.home_button.setToolTip(self.tr("open_homepage"))
        self.input_group.setTitle("")
        self.defaults_group.setTitle(self.tr("defaults"))
        self._input_url_label.setText(self.tr("url_label"))
        self._input_dir_label.setText(self.tr("target_dir"))
        self.url_input.setPlaceholderText(self.tr("url_placeholder"))
        self.output_input.setPlaceholderText(self.tr("target_dir_placeholder"))
        self.paste_button.setText(self.tr("paste"))
        self.browse_button.setText(self.tr("choose_dir"))
        self.add_button.setText(self.tr("add_to_queue"))
        self.start_button.setText(self.tr("download"))
        self.stop_button.setText(self.tr("stop"))
        self.resume_button.setText(self.tr("resume"))
        self.reader_button.setText(self.tr("reader"))
        self.remove_button.setText(self.tr("remove"))
        self.clear_button.setText(self.tr("clear"))
        self.item_settings_button.setText(self.tr("item_settings_button"))
        self.save_button.setText(self.tr("save_queue"))
        self.load_button.setText(self.tr("load_queue"))
        self.search_button.setText(self.tr("search"))
        self.log_toggle_button.setText(self.tr("toggle_log_hide" if self._log_visible else "toggle_log_show"))
        self.reset_button.setText(self.tr("reset"))
        self.settings_button.setText(self.tr("app_settings_button"))
        self.library_button.setText(self.tr("library_view"))
        self.downloader_button.setText(self.tr("downloader_view"))
        self.refresh_library_button.setText(self.tr("refresh_library"))
        self.library_sort_combo.setToolTip(self.tr("library_sort_tooltip"))
        self._fill_library_sort_combo(getattr(self, "_set_combo_value_later", {}).get("library_sort"))
        self.library_empty_label.setText(self.tr("library_empty"))
        self._defaults_reading_label.setText(self.tr("reading_style"))
        self._defaults_output_label.setText(self.tr("output_mode"))
        self._defaults_format_label.setText(self.tr("image_format"))
        self._defaults_threads_label.setText(self.tr("image_threads"))
        self._defaults_delay_label.setText(self.tr("request_delay"))
        self.keep_images.setText(self.tr("keep_images"))
        self.check_updates_on_startup.setText(self.tr("check_updates_on_startup"))
        self.check_updates_on_startup.setToolTip(self.tr("check_updates_on_startup_hint"))
        self.auto_download_updates.setText(self.tr("auto_download_updates"))
        self.auto_download_updates.setToolTip(self.tr("auto_download_updates_hint"))
        self.delay.setSuffix(self.tr("seconds_suffix"))
        if not self.status_label.text():
            self.status_label.setText(self.tr("ready"))
        self.tree.setHeaderLabels([
            self.tr("columns_name"),
            self.tr("columns_link"),
            self.tr("columns_reading"),
            self.tr("columns_saving"),
            self.tr("columns_format"),
            self.tr("columns_status"),
            self.tr("columns_progress"),
            self.tr("columns_eta"),
        ])
        self._fill_combo(self.reading_combo, READING_STYLES, "reading_", getattr(self, "_set_combo_value_later", {}).get("reading"))
        self._fill_combo(self.output_combo, OUTPUT_MODES, "mode_", getattr(self, "_set_combo_value_later", {}).get("output"))
        self._fill_combo(self.format_combo, IMAGE_FORMATS, "format_", getattr(self, "_set_combo_value_later", {}).get("format"))
        self._set_combo_value_later = {}
        self._rebuild_language_combo()
        self.refresh_tree()

    def _rebuild_language_combo(self) -> None:
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        for code in SUPPORTED_LANGUAGES:
            self.language_combo.addItem(language_label(code, self.language), code)
        idx = self.language_combo.findData(self.language)
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        self.language_combo.blockSignals(False)

    def default_item_settings(self) -> ItemSettings:
        return ItemSettings(
            reading_style=str(self.reading_combo.currentData() or "long_strip"),
            output_mode=str(self.output_combo.currentData() or "images"),
            image_format=str(self.format_combo.currentData() or "original"),
            keep_images=self.keep_images.isChecked(),
            image_threads=self.threads.value(),
            request_delay=self.delay.value(),
        )


    def _log_startup_state(self) -> None:
        self.append_log(self.tr("log_app_started", app=APP_NAME))
        self.append_log(self.tr("log_language_set", language=language_label(self.language, self.language)))
        self.append_log(self.tr("log_theme_applied", theme=self.tr("preset_" + self.theme.normalized().preset)))
        self.append_log(self.tr("log_default_output", folder=self.tr("target_dir_placeholder")))


    def _load_header_logo(self) -> None:
        candidates = [
            Path.cwd() / "logo-small.png",
            Path.cwd() / "logo_small.png",
            Path(__file__).resolve().parents[1] / "logo-small.png",
            Path(__file__).resolve().parents[1] / "logo_small.png",
            Path(__file__).resolve().parent / "logo-small.png",
            Path(__file__).resolve().parent / "logo_small.png",
        ]
        for path in candidates:
            try:
                if path.exists() and path.is_file():
                    pixmap = QPixmap(str(path))
                    if not pixmap.isNull():
                        self.logo_label.setPixmap(
                            pixmap.scaled(
                                180,
                                64,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                        self.logo_label.setVisible(True)
                        return
            except Exception:
                pass
        self.logo_label.setVisible(False)

    def _find_queue_manga_for_folder(self, folder_title: str) -> MangaEntry | None:
        normalized_folder = sanitize_filename(folder_title, folder_title).lower()
        for manga in self.mangas:
            if manga.title == folder_title:
                return manga
            if sanitize_filename(manga.title, manga.title).lower() == normalized_folder:
                return manga
        return None

    def _manga_last_chapter_info(self, manga_dir: Path) -> tuple[str, int]:
        chapters: list[tuple[str, int]] = []
        try:
            entries = list(manga_dir.iterdir())
        except Exception:
            return "", 0
        for item in entries:
            try:
                if item.is_file() and item.suffix.lower() == ".cbz" and item.stat().st_size > 0:
                    page_count = 0
                    try:
                        with zipfile.ZipFile(item, "r") as archive:
                            page_count = sum(1 for member in archive.namelist() if Path(member).suffix.lower() in IMAGE_EXTENSIONS)
                    except Exception:
                        page_count = 0
                    chapters.append((item.stem, page_count))
                elif item.is_file() and item.suffix.lower() == ".pdf" and item.stat().st_size > 0:
                    chapters.append((item.stem, 0))
                elif item.is_dir():
                    images = [
                        child for child in item.iterdir()
                        if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS and child.stat().st_size > 0
                    ]
                    if images:
                        chapters.append((item.name, len(images)))
            except Exception:
                pass
        if not chapters:
            return "", 0
        chapters.sort(key=lambda entry: chapter_sort_key(entry[0]))
        return chapters[-1]

    def _reading_state_store(self) -> ReadingStateStore:
        root = Path(self.current_output_dir()).expanduser()
        if self._reading_store is None or self._reading_store_root != root:
            self._reading_store = ReadingStateStore(root)
            self._reading_store_root = root
        return self._reading_store

    def _library_info_for_manga(self, manga: MangaEntry) -> dict | None:
        for info in self._library_entries or []:
            if manga.url and str(info.get("url") or "") == manga.url:
                return info
            if str(info.get("title") or "").strip().casefold() == manga.title.strip().casefold():
                return info
        return None

    @staticmethod
    def _latest_chapter_is_read(reading: dict, latest_title: str, latest_pages: int) -> bool:
        """Return True only when the final page of the current newest chapter was read."""
        if not reading or not latest_title or latest_pages <= 0:
            return False
        saved_title = str(reading.get("chapter_title") or "").strip().casefold()
        if saved_title != str(latest_title).strip().casefold():
            return False
        try:
            raw_page_index = reading.get("page_index", -1)
            page_index = int(raw_page_index if raw_page_index is not None else -1)
        except (TypeError, ValueError):
            return False
        return page_index >= latest_pages - 1

    def _update_cached_library_card(self, info: dict) -> None:
        manga_id = str(info.get("manga_id") or "")
        card = self._library_cards_by_id.get(manga_id)
        if card is None:
            return
        read_marker = "✓ " if info.get("latest_read") else ""
        card.set_title(read_marker + str(info.get("title") or ""))
        card.set_info(
            self.tr(
                "library_card_meta",
                chapters=info.get("chapters", 0),
                last=info.get("last_chapter", ""),
            )
        )
        card.set_favorite(bool(info.get("favorite")))

    def _refresh_library_reading_state_only(self, manga_id: str = "") -> None:
        """Update shared reading progress without rescanning or rebuilding the library.

        Returning from the reader changes only progress. Updating the one
        affected card in-place avoids both the filesystem scan and rebuilding all
        cover widgets, which keeps large libraries responsive.
        """
        if self._library_entries is None:
            return
        store = self._reading_state_store()
        candidates = self._library_entries
        if manga_id:
            candidates = [info for info in self._library_entries if str(info.get("manga_id") or "") == manga_id]
        for info in candidates:
            entry_id = str(info.get("manga_id") or manga_id_for_path(info["path"]))
            reading = store.get(
                entry_id,
                path=Path(info["path"]),
                title=str(info.get("title") or ""),
                source_url=str(info.get("url") or ""),
            ) or {}
            info["reading"] = reading
            info["last_read_at"] = float(reading.get("updated_at", 0) or 0)
            info["latest_read"] = self._latest_chapter_is_read(
                reading,
                str(info.get("last_chapter") or ""),
                int(info.get("last_pages", 0) or 0),
            )
            self._update_cached_library_card(info)

    def show_library_view(self) -> None:
        self.library_page.setVisible(True)
        self.downloader_page.setVisible(False)
        self.library_button.setEnabled(False)
        self.downloader_button.setEnabled(True)
        self.refresh_library_button.setVisible(True)
        try:
            # Reuse the cached scan when nothing changed; a full disk rescan on
            # every page switch is what made the library feel slow.
            if self._library_entries is None or getattr(self, "_library_dirty", True):
                self.refresh_library()
            else:
                self._fill_library_sort_combo()
                self._render_library(self._sort_library_entries(self._library_entries))
        except Exception as exc:
            self.library_empty_label.setText(self.tr("library_load_failed", error=exc))
            self.library_empty_label.setVisible(True)
            self.library_scroll.setVisible(False)
            self.append_log(self.tr("library_load_failed", error=exc))

    def show_downloader_view(self) -> None:
        self.library_page.setVisible(False)
        self.downloader_page.setVisible(True)
        self.library_button.setEnabled(True)
        self.downloader_button.setEnabled(False)
        self.refresh_library_button.setVisible(False)

    def _clear_library_grid(self) -> None:
        self._library_cards_by_id.clear()
        while self.library_grid.count():
            item = self.library_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _manga_dir_metadata(self, manga_dir: Path) -> dict:
        metadata_path = manga_dir / ".mangodango.json"
        if not metadata_path.exists():
            return {}
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _manga_cover_path(self, manga_dir: Path) -> Path | None:
        for pattern in ("cover.*", "folder.*", "poster.*"):
            for path in sorted(manga_dir.glob(pattern), key=lambda item: natural_sort_key(item.name)):
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and path.stat().st_size > 0:
                    return path
        for chapter_dir in sorted([item for item in manga_dir.iterdir() if item.is_dir()], key=lambda item: chapter_sort_key(item.name)):
            try:
                for image in sorted(chapter_dir.iterdir(), key=lambda item: natural_sort_key(item.name)):
                    if image.is_file() and image.suffix.lower() in IMAGE_EXTENSIONS and image.stat().st_size > 0:
                        return image
            except Exception:
                pass
        return None

    def _library_chapter_count(self, manga_dir: Path) -> int:
        count = 0
        try:
            entries = list(manga_dir.iterdir())
        except Exception:
            return 0
        for item in entries:
            try:
                if item.is_file() and item.suffix.lower() in {".cbz", ".pdf"} and item.stat().st_size > 0:
                    count += 1
                elif item.is_dir():
                    if any(child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS and child.stat().st_size > 0 for child in item.iterdir()):
                        count += 1
            except Exception:
                pass
        return count

    def _library_updated_sort_value(self, manga_dir: Path, metadata: dict) -> float:
        raw = metadata.get("updated_at", 0)
        try:
            value = float(raw)
            if value > 0:
                return value
        except Exception:
            pass
        # Fall back to the folder's own modification time (one stat call) instead
        # of walking every file, which is far too slow for large libraries.
        try:
            return manga_dir.stat().st_mtime
        except Exception:
            return 0.0


    @staticmethod
    def _flag(value, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)



    def scan_library_mangas(self) -> list[dict]:
        root = Path(self.current_output_dir()).expanduser()
        if not root.exists() or not root.is_dir():
            return []

        result: list[dict] = []
        reading_store = self._reading_state_store()
        for manga_dir in sorted([item for item in root.iterdir() if item.is_dir() and item.name != ".mangodango"], key=lambda item: natural_sort_key(item.name)):
            metadata = self._manga_dir_metadata(manga_dir)
            title = str(metadata.get("title") or manga_dir.name)
            url = str(metadata.get("url") or "")
            queue_manga = self._find_queue_manga_for_folder(manga_dir.name)
            if queue_manga:
                title = queue_manga.title or title
                url = queue_manga.url or url
                # Backfill missing metadata so future update checks work even
                # for old downloads that were created before .mangodango.json.
                if url and not (manga_dir / ".mangodango.json").exists():
                    try:
                        write_manga_metadata(self.current_output_dir(), title, url)
                    except Exception:
                        pass
            count = self._library_chapter_count(manga_dir)
            if count <= 0:
                continue
            last_chapter, last_pages = self._manga_last_chapter_info(manga_dir)
            manga_id = manga_id_for_path(manga_dir)
            reading = reading_store.get(manga_id, path=manga_dir, title=title, source_url=url) or {}
            result.append({
                "manga_id": manga_id,
                "title": title,
                "url": url,
                "path": manga_dir,
                "cover": self._manga_cover_path(manga_dir),
                "chapters": count,
                "last_chapter": last_chapter,
                "last_pages": last_pages,
                "reading": reading,
                "latest_read": self._latest_chapter_is_read(reading, last_chapter, last_pages),
                "last_read_at": float(reading.get("updated_at", 0) or 0),
                "updated_at": metadata.get("updated_at", ""),
                "updated_sort": self._library_updated_sort_value(manga_dir, metadata),
                "favorite": self._flag(metadata.get("favorite"), False),
                "check_updates": self._flag(metadata.get("check_updates"), True),
                "auto_download": self._flag(metadata.get("auto_download"), True),
            })

        # Return the complete set; the current sort/filter (incl. the favorites
        # filter) is applied later at render time so the cache stays complete.
        return result

    def _sort_library_entries(self, entries: list[dict]) -> list[dict]:
        mode = self.library_sort_mode()
        entries = list(entries)
        if mode == "favorites":
            entries = [item for item in entries if item.get("favorite")]
            entries.sort(key=lambda item: str(item.get("title", "")).casefold())
        elif mode == "az":
            entries.sort(key=lambda item: str(item.get("title", "")).casefold())
        else:
            entries.sort(key=lambda item: (-float(item.get("updated_sort", 0.0) or 0.0), str(item.get("title", "")).casefold()))
        return entries

    def refresh_library(self) -> None:
        if self._mobile_server is not None:
            self._mobile_server.invalidate()
        # Full refresh: scan the disk, cache the result, then render. Toggling the
        # sort order afterwards reuses the cache instead of re-scanning the disk.
        self._library_entries = self.scan_library_mangas()
        self._library_dirty = False
        # Re-fill the sort combo so "Favorites" appears only when favorites exist.
        self._fill_library_sort_combo()
        self._render_library(self._sort_library_entries(self._library_entries))
        self.append_log(self.tr("library_loaded", count=len(self._library_entries), path=self.current_output_dir()))

    def _render_library(self, mangas: list[dict]) -> None:
        self._clear_library_grid()
        self.library_empty_label.setVisible(not mangas)
        self.library_scroll.setVisible(bool(mangas))

        if not hasattr(self, "_favorite_star_pixmap"):
            self._favorite_star_pixmap = make_favorite_star(18)

        columns = 6
        for index, info in enumerate(mangas):
            card = LibraryCard(self._favorite_star_pixmap, self.tr("library_menu_tooltip"))
            read_marker = "✓ " if info.get("latest_read") else ""
            card.set_title(read_marker + str(info["title"]))
            card.set_info(
                self.tr(
                    "library_card_meta",
                    chapters=info["chapters"],
                    last=info.get("last_chapter", ""),
                )
            )
            card.setToolTip(self.tr(
                "library_card_tooltip",
                title=read_marker + str(info["title"]),
                chapters=info["chapters"],
                last=info.get("last_chapter", ""),
                pages=info.get("last_pages", 0),
                path=str(info["path"]),
            ))

            cover = info.get("cover")
            pixmap = QPixmap(str(cover)) if cover else QPixmap()
            if not pixmap.isNull():
                card.set_cover(pixmap.scaled(QSize(168, 240), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

            card.set_favorite(bool(info.get("favorite")))
            self._library_cards_by_id[str(info.get("manga_id") or "")] = card
            card.openRequested.connect(lambda data=info: self.open_library_manga(data))
            card.menuRequested.connect(lambda data=info, c=card: self.open_library_menu(data, c))
            row = index // columns
            col = index % columns
            # Keep cards at their natural height. Without a vertical alignment,
            # a single-row result (most visible with the Favorites filter) is
            # stretched to the full height of the scroll viewport.
            self.library_grid.addWidget(card, row, col, Qt.AlignmentFlag.AlignTop)

        for col in range(columns):
            self.library_grid.setColumnStretch(col, 1)

    def open_library_manga(self, info: dict, continue_direct: bool = False) -> None:
        title = str(info.get("title") or "")
        url = str(info.get("url") or "")
        manga = self._manga_from_disk(title, url, self.current_output_dir())
        if not manga:
            QMessageBox.information(self, self.tr("reader_title"), self.tr("reader_no_pages"))
            return

        saved_position = self._saved_reader_position_for_manga(manga)
        if continue_direct and saved_position:
            choice = self._reader_start_continue(saved_position)
        else:
            choice = self._ask_reader_start_choice(manga, saved_position)
            if choice is None:
                return

        start_chapter_id, start_chapter_title, start_page_index, start_global_index, start_after = choice
        self.open_reader_for_manga(
            manga,
            start_chapter_id=start_chapter_id,
            start_chapter_title=start_chapter_title,
            start_page_index=start_page_index,
            start_global_index=start_global_index,
            start_after=start_after,
            manga_dir=Path(info["path"]),
        )

    def open_library_menu(self, info: dict, card) -> None:
        menu = QMenu(self)
        favorite = bool(info.get("favorite"))
        in_auto = bool(info.get("check_updates"))

        act_rename = menu.addAction(self.tr("menu_rename"))
        act_cover = menu.addAction(self.tr("menu_change_cover"))
        act_fav = menu.addAction(self.tr("menu_unfavorite") if favorite else self.tr("menu_favorite"))
        act_auto = menu.addAction(self.tr("menu_auto_remove") if in_auto else self.tr("menu_auto_add"))
        act_source = menu.addAction(self.tr("menu_open_source"))
        act_source.setEnabled(bool(str(info.get("url") or "").strip()))
        menu.addSeparator()
        act_delete = menu.addAction(self.tr("menu_delete"))

        chosen = menu.exec(card.menu_button.mapToGlobal(card.menu_button.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen == act_rename:
            self._library_rename(info)
        elif chosen == act_cover:
            self._library_change_cover(info)
        elif chosen == act_fav:
            self._library_set_metadata(info, favorite=not favorite)
            self.append_log(self.tr("favorite_removed" if favorite else "favorite_added", title=info.get("title", "")))
            self.refresh_library()
        elif chosen == act_auto:
            new_state = not in_auto
            self._library_set_metadata(info, check_updates=new_state, auto_download=new_state)
            self.append_log(self.tr("menu_auto_removed" if not new_state else "menu_auto_added", title=info.get("title", "")))
            self.refresh_library()
        elif chosen == act_source:
            self.open_external_url(str(info.get("url") or ""))
        elif chosen == act_delete:
            self._library_delete(info)

    def _library_set_metadata(self, info: dict, **fields) -> None:
        manga_dir = Path(info["path"])
        data = read_manga_metadata_dir(manga_dir)
        data.setdefault("app", "MangoDango")
        data.setdefault("title", str(info.get("title") or manga_dir.name))
        if info.get("url"):
            data.setdefault("url", str(info["url"]))
        data.update(fields)
        write_manga_metadata_dir(manga_dir, data)

    def _library_rename(self, info: dict) -> None:
        old_dir = Path(info["path"])
        old_manga_id = str(info.get("manga_id") or manga_id_for_path(old_dir))
        old_title = str(info.get("title") or old_dir.name)
        new_title, ok = localized_text_input(self, self.tr, self.tr("menu_rename"), self.tr("rename_prompt"), text=old_title)
        new_title = (new_title or "").strip()
        if not ok or not new_title or new_title == old_title:
            return
        new_folder = sanitize_filename(new_title, new_title)
        new_dir = old_dir.parent / new_folder
        try:
            if new_dir != old_dir:
                if new_dir.exists():
                    QMessageBox.warning(self, self.tr("menu_rename"), self.tr("rename_exists"))
                    return
                shutil.move(str(old_dir), str(new_dir))
            self._library_set_metadata({"path": new_dir, "title": new_title, "url": info.get("url", "")}, title=new_title)
        except Exception as exc:
            QMessageBox.warning(self, self.tr("menu_rename"), self.tr("rename_failed", error=exc))
            return
        target = self.find_manga_by_title_or_url(old_title, str(info.get("url") or ""))
        if target:
            target.title = new_title
            self.refresh_tree()
        try:
            self._reading_state_store().migrate(old_manga_id, manga_id_for_path(new_dir), title=new_title, path=new_dir)
        except Exception:
            pass
        self.append_log(self.tr("rename_done", old=old_title, new=new_title))
        self.refresh_library()

    def _library_change_cover(self, info: dict) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("menu_change_cover"), str(Path.home()), self.tr("cover_filter"),
        )
        if not path:
            return
        manga_dir = Path(info["path"])
        source = Path(path)
        suffix = source.suffix.lower() or ".jpg"
        try:
            for existing in manga_dir.glob("cover.*"):
                existing.unlink()
            shutil.copyfile(source, manga_dir / f"cover{suffix}")
            self._library_set_metadata(info, cover=f"cover{suffix}")
        except Exception as exc:
            QMessageBox.warning(self, self.tr("menu_change_cover"), self.tr("cover_failed", error=exc))
            return
        self.append_log(self.tr("cover_changed", title=info.get("title", "")))
        self.refresh_library()


    def _library_delete(self, info: dict) -> None:
        title = str(info.get("title") or "")
        if not localized_question(
            self, self.tr, self.tr("menu_delete"), self.tr("delete_confirm", title=title), default_yes=False
        ):
            return
        try:
            shutil.rmtree(Path(info["path"]))
        except Exception as exc:
            QMessageBox.warning(self, self.tr("menu_delete"), self.tr("delete_failed", error=exc))
            return
        try:
            self._reading_state_store().remove(str(info.get("manga_id") or manga_id_for_path(info["path"])))
        except Exception:
            pass
        target = self.find_manga_by_title_or_url(title, str(info.get("url") or ""))
        if target in self.mangas:
            self.mangas.remove(target)
            self.refresh_tree()
        self.append_log(self.tr("delete_done", title=title))
        self.refresh_library()

    def maybe_check_updates_on_startup(self) -> None:
        if self.check_updates_on_startup.isChecked():
            self.start_update_check(background=True)

    def start_update_check(
        self,
        background: bool = False,
        selected_titles: Iterable[str] | None = None,
        download_immediately: bool = False,
    ) -> None:
        if self.update_worker or self.resolve_worker or self.download_worker:
            return
        output_dir = self.current_output_dir()
        title_filter = {
            str(title).strip().casefold()
            for title in (selected_titles or [])
            if str(title).strip()
        }
        if selected_titles is not None and not title_filter:
            return
        self._background_update_check = background
        self._download_updates_immediately = bool(download_immediately)
        self.append_log(self.tr("updates_started", path=output_dir))
        if not background:
            self.update_progress_dialog = BusyProgressDialog(self.tr, "updates_progress_title", "updates_progress_message", self)
            self.update_progress_dialog.cancel_button.clicked.connect(self.cancel_update_check)
            self.update_progress_dialog.show()
            self.update_progress_dialog.raise_()
            self.update_progress_dialog.activateWindow()
        self.update_worker = UpdateCheckWorker(
            output_dir,
            self.default_item_settings(),
            self.language,
            self.mangas,
            self,
            candidate_titles=title_filter if selected_titles is not None else None,
            include_disabled=selected_titles is not None,
        )
        self.update_worker.log_message.connect(self.append_log)
        self.update_worker.progress_message.connect(self.on_update_progress)
        self.update_worker.updates_found.connect(self.on_updates_found)
        # Use QThread.finished for lifecycle cleanup. A custom signal emitted at
        # the end of run() can still be delivered before the native thread has
        # actually stopped, and deleting the QThread in that tiny window aborts
        # the process with "QThread: Destroyed while thread is still running".
        self.update_worker.finished.connect(self.on_update_finished)
        self.update_worker.start()

    def cancel_update_check(self) -> None:
        if self.update_worker:
            self.update_worker.stop()
        if self.update_progress_dialog:
            self.update_progress_dialog.set_detail(self.tr("updates_canceling"))

    @Slot(str, int, int)
    def on_update_progress(self, message: str, index: int, total: int) -> None:
        if self.update_progress_dialog:
            self.update_progress_dialog.set_detail(message, index, total)
        elif self._background_update_check:
            self.status_label.setText(self.tr("updates_background_status", index=index, total=total))

    def add_update_manga_to_queue(self, manga: MangaEntry) -> int:
        """Add update-check results without accidentally disabling selected new chapters."""
        existing = self.find_manga_by_title_or_url(manga.title, manga.url)
        target = existing or manga
        target.enabled = True
        target.status = "ready"

        existing_by_url = {chapter.url: chapter for chapter in target.chapters if chapter.url}
        added_or_enabled = 0

        for chapter in manga.chapters:
            if chapter_exists_on_disk(self.current_output_dir(), manga.title, chapter.title):
                chapter.status = "done"
                chapter.enabled = False
                chapter.progress_text = self.tr("download_present")
                chapter.eta_text = ""
            else:
                chapter.status = "pending"
                chapter.enabled = True
                chapter.progress_text = ""
                chapter.eta_text = ""

            if existing:
                current = existing_by_url.get(chapter.url)
                if current:
                    # Re-enable an already queued update if it was disabled by a
                    # previous failed/partial run and is not complete on disk.
                    current.title = chapter.title
                    current.url = chapter.url
                    current.settings = chapter.settings
                    current.status = chapter.status
                    current.enabled = chapter.enabled
                    current.progress_text = chapter.progress_text
                    current.eta_text = chapter.eta_text
                    added_or_enabled += 1 if current.enabled else 0
                else:
                    target.chapters.append(chapter)
                    existing_by_url[chapter.url] = chapter
                    added_or_enabled += 1 if chapter.enabled else 0
            else:
                added_or_enabled += 1 if chapter.enabled else 0

        if not existing:
            self.mangas.append(target)
            self.append_log(self.tr("log_added_manga", title=target.title, count=len(target.chapters)))
        else:
            self.append_log(self.tr("log_merged_manga", title=target.title, count=len(manga.chapters)))

        target.chapters.sort(key=lambda chapter: chapter_sort_key(chapter.title))
        self.persist_manga_metadata(target)
        return added_or_enabled


    def _disable_non_auto_download_mangas(self, found: list[MangaEntry]) -> None:
        from .engine import manga_wants_auto_download
        output_dir = self.current_output_dir()
        for found_manga in found:
            if manga_wants_auto_download(output_dir, found_manga.title, found_manga.url):
                continue
            target = self.find_manga_by_title_or_url(found_manga.title, found_manga.url)
            if target:
                target.enabled = False
        self.refresh_tree()

    def _count_active_chapters(self) -> int:
        return sum(
            1
            for manga in self.mangas
            if manga.enabled
            for chapter in manga.chapters
            if chapter.enabled and chapter.status != "done"
        )

    @Slot(object)
    def on_updates_found(self, mangas: list[MangaEntry]) -> None:
        if self._closing:
            return
        if self.update_progress_dialog:
            self.update_progress_dialog.close()
        if not mangas:
            self.append_log(self.tr("updates_none_found"))
            return

        if self._download_updates_immediately:
            selected = mangas
            self._push_undo()
            active_updates = 0
            chapter_ids_by_manga: dict[str, set[str]] = {}
            for found_manga in selected:
                active_updates += self.add_update_manga_to_queue(found_manga)
                target = self.find_manga_by_title_or_url(found_manga.title, found_manga.url)
                if target is None:
                    continue

                found_urls = {chapter.url for chapter in found_manga.chapters if chapter.url}
                found_titles = {chapter.title.casefold() for chapter in found_manga.chapters}
                matching_ids = {
                    chapter.item_id
                    for chapter in target.chapters
                    if (chapter.url and chapter.url in found_urls)
                    or chapter.title.casefold() in found_titles
                }
                if matching_ids:
                    chapter_ids_by_manga[target.item_id] = matching_ids

            self.refresh_tree()
            self.append_log(self.tr("updates_added_to_queue", count=sum(len(m.chapters) for m in selected)))
            if active_updates > 0 and chapter_ids_by_manga:
                self._start_download(resume=False, chapter_ids_by_manga=chapter_ids_by_manga)
            else:
                self.append_log(self.tr("updates_no_downloadable_chapters"))
                self.status_label.setText(self.tr("download_no_active_chapters"))
            return

        if self._background_update_check:
            selected = mangas
            self._push_undo()
            active_updates = 0
            for manga in selected:
                active_updates += self.add_update_manga_to_queue(manga)
            self.refresh_tree()
            self.append_log(self.tr("updates_background_added", count=sum(len(m.chapters) for m in selected)))
            if self.auto_download_updates.isChecked():
                # Only auto-download mangas that are flagged for automatic
                # downloading; the rest stay in the queue for a manual start.
                self._disable_non_auto_download_mangas(selected)
                if self._count_active_chapters() > 0:
                    self._start_download(resume=False)
                else:
                    self.append_log(self.tr("updates_no_downloadable_chapters"))
                    self.status_label.setText(self.tr("download_no_active_chapters"))
            return

        dialog = UpdateResultsDialog(mangas, self.tr, self.auto_download_updates.isChecked(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.append_log(self.tr("updates_dismissed"))
            return
        selected = dialog.selected_mangas()
        if not selected:
            self.append_log(self.tr("updates_no_selection"))
            return
        self._push_undo()
        active_updates = 0
        for manga in selected:
            active_updates += self.add_update_manga_to_queue(manga)
        self.refresh_tree()
        self.append_log(self.tr("updates_added_to_queue", count=sum(len(m.chapters) for m in selected)))
        if dialog.download_after:
            if active_updates > 0:
                self._start_download(resume=False)
            else:
                self.append_log(self.tr("updates_no_downloadable_chapters"))
                self.status_label.setText(self.tr("download_no_active_chapters"))

    @Slot()
    def on_update_finished(self) -> None:
        worker = self.sender()
        if self.update_progress_dialog:
            self.update_progress_dialog.close()
            self.update_progress_dialog.deleteLater()
            self.update_progress_dialog = None
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
        if worker is self.update_worker:
            self.update_worker = None
        self._background_update_check = False
        self._download_updates_immediately = False

    def paste_urls(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            current = self.url_input.text().strip()
            self.url_input.setText((current + " " + text).strip() if current else text)

    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("choose_dir"), self.output_input.text().strip() or DEFAULT_OUTPUT_DIR)
        if folder:
            self.output_input.setText(folder)
            self._save_settings()
            self.append_log(self.tr("log_output_dir_changed", path=folder))
            if self.library_page.isVisible():
                self.refresh_library()

    def parse_urls(self) -> list[str]:
        raw = self.url_input.text().strip()
        if not raw:
            raise ValueError(self.tr("error_empty_url"))
        candidates = []
        for part in re.split(r"[\s,]+", raw):
            value = part.strip()
            if value:
                candidates.append(normalize_weebcentral_url(value))
        seen: set[str] = set()
        result: list[str] = []
        for url in candidates:
            if url not in seen:
                result.append(url)
                seen.add(url)
        return result

    def add_urls(self) -> None:
        try:
            urls = self.parse_urls()
        except Exception as exc:
            message = str(exc)
            if message.startswith("error_"):
                message = self.tr(message)
            QMessageBox.warning(self, APP_NAME, message)
            return

        self.set_busy(True, resolving=True)
        self.collect_dialog = BusyProgressDialog(self.tr, "collector_title", "collector_message", self)
        self.collect_dialog.cancel_button.clicked.connect(self.cancel_resolve)
        self.collect_dialog.show()
        self.collect_dialog.raise_()
        self.collect_dialog.activateWindow()

        self.resolve_worker = ResolveWorker(urls, self.default_item_settings(), self.language, self)
        self.resolve_worker.log_message.connect(self.append_log)
        self.resolve_worker.progress_message.connect(self.on_resolve_progress)
        self.resolve_worker.resolved.connect(self.on_manga_resolved)
        self.resolve_worker.failed.connect(lambda _url, error: self.append_log(error))
        # Cleanup only after the native QThread has actually stopped.
        self.resolve_worker.finished.connect(self.on_resolve_finished)
        self.resolve_worker.start()

    @Slot(str, int, int)
    def on_resolve_progress(self, message: str, index: int, total: int) -> None:
        if self.collect_dialog:
            self.collect_dialog.set_detail(message, index, total)

    def cancel_resolve(self) -> None:
        if self.resolve_worker:
            self.resolve_worker.stop()
        if self.collect_dialog:
            self.collect_dialog.set_detail(self.tr("collector_canceling"))

    def set_busy(self, busy: bool, resolving: bool = False) -> None:
        self.add_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        self.resume_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy and not resolving)
        # Keep Reader available during downloads so already downloaded chapters can be read.
        self.reader_button.setEnabled(not resolving)
        for widget in (self.item_settings_button, self.remove_button, self.clear_button, self.save_button, self.load_button, self.reset_button):
            widget.setEnabled(not busy)
        self.status_label.setText(self.tr("status_resolving" if resolving else "status_running") if busy else self.tr("ready"))

    @Slot(object)
    def on_manga_resolved(self, manga: MangaEntry) -> None:
        self._push_undo()
        self.mark_existing_chapters(manga, disable=True)
        existing = self.find_manga_by_title_or_url(manga.title, manga.url)
        if existing:
            added = existing.merge_chapters(manga.chapters)
            self.mark_existing_chapters(existing, disable=True)
            existing.status = "ready"
            self.append_log(self.tr("log_merged_manga", title=existing.title, count=added))
        else:
            manga.status = "ready"
            for chapter in manga.chapters:
                if chapter.status != "done":
                    chapter.status = "pending"
            self.mangas.append(manga)
            self.append_log(self.tr("log_added_manga", title=manga.title, count=len(manga.chapters)))
        self.persist_manga_metadata(manga)
        self.refresh_tree()

    @Slot()
    def on_resolve_finished(self) -> None:
        worker = self.sender()
        self.set_busy(False)
        self.status_label.setText(self.tr("resolve_finished"))
        self.url_input.clear()
        if self.collect_dialog:
            self.collect_dialog.close()
            self.collect_dialog.deleteLater()
            self.collect_dialog = None
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
        if worker is self.resolve_worker:
            self.resolve_worker = None

    def find_manga_by_title_or_url(self, title: str, url: str) -> MangaEntry | None:
        for manga in self.mangas:
            if manga.url == url or manga.title.casefold() == title.casefold():
                return manga
        return None

    def current_output_dir(self) -> str:
        return self.output_input.text().strip() or DEFAULT_OUTPUT_DIR

    def persist_manga_metadata(self, manga: MangaEntry) -> None:
        try:
            write_manga_metadata(self.current_output_dir(), manga.title, manga.url)
        except Exception as exc:
            self.append_log(self.tr("metadata_write_failed", title=manga.title, error=exc))

    def mark_existing_chapters(self, manga: MangaEntry, disable: bool = True) -> int:
        count = 0
        output_dir = self.current_output_dir()
        for chapter in manga.chapters:
            if chapter_exists_on_disk(output_dir, manga.title, chapter.title):
                chapter.status = "done"
                chapter.progress_text = self.tr("download_present")
                chapter.eta_text = ""
                if disable:
                    chapter.enabled = False
                count += 1
        if count:
            self.append_log(self.tr("log_existing_chapters_detected", title=manga.title, count=count))
        return count

    def mark_all_existing_chapters(self, disable: bool = True) -> int:
        total = 0
        for manga in self.mangas:
            total += self.mark_existing_chapters(manga, disable=disable)
        return total

    def _schedule_refresh_tree(self) -> None:
        if self._refresh_tree_pending:
            return
        self._refresh_tree_pending = True
        QTimer.singleShot(0, self.refresh_tree)

    def refresh_tree(self) -> None:
        self._refresh_tree_pending = False
        expanded_ids: set[str] = set()
        had_items = self.tree.topLevelItemCount() > 0
        if not self._building_tree:
            for index in range(self.tree.topLevelItemCount()):
                top_item = self.tree.topLevelItem(index)
                if top_item and top_item.isExpanded():
                    expanded_ids.add(str(top_item.data(0, ROLE_MANGA_ID) or ""))

        total_rows = sum(1 + len(manga.chapters) for manga in self.mangas)
        self._use_inline_widgets = total_rows <= INLINE_WIDGET_ROW_LIMIT

        self._building_tree = True
        try:
            self.tree.setUpdatesEnabled(False)
            self.tree.clear()
            for manga in self.mangas:
                parent = QTreeWidgetItem(self.tree)
                self._setup_item(parent, "manga", manga.item_id, "")
                self._apply_item_values(parent, manga.title, manga.url, manga.settings, manga.status, manga.enabled, manga.progress_text, manga.eta_text)
                self._install_row_setting_widgets(parent, manga, None)
                for chapter in manga.chapters:
                    child = QTreeWidgetItem(parent)
                    self._setup_item(child, "chapter", manga.item_id, chapter.item_id)
                    self._apply_item_values(child, chapter.title, chapter.url, chapter.settings, chapter.status, chapter.enabled, chapter.progress_text, chapter.eta_text)
                    self._install_row_setting_widgets(child, manga, chapter)
                if expanded_ids:
                    parent.setExpanded(manga.item_id in expanded_ids)
                else:
                    parent.setExpanded(total_rows <= INLINE_WIDGET_ROW_LIMIT and not had_items)
            if total_rows <= INLINE_WIDGET_ROW_LIMIT:
                for col in range(self.tree.columnCount()):
                    self.tree.resizeColumnToContents(col)
                self.tree.setColumnWidth(2, max(self.tree.columnWidth(2), 170))
                self.tree.setColumnWidth(3, max(self.tree.columnWidth(3), 190))
                self.tree.setColumnWidth(4, max(self.tree.columnWidth(4), 150))
                self.tree.setColumnWidth(6, max(self.tree.columnWidth(6), 120))
                self.tree.setColumnWidth(7, max(self.tree.columnWidth(7), 100))
            else:
                self.tree.setColumnWidth(0, 260)
                self.tree.setColumnWidth(1, 520)
                self.tree.setColumnWidth(2, 170)
                self.tree.setColumnWidth(3, 190)
                self.tree.setColumnWidth(4, 150)
                self.tree.setColumnWidth(6, 120)
                self.tree.setColumnWidth(7, 120)
        finally:
            self.tree.setUpdatesEnabled(True)
            self._building_tree = False

    def _setup_item(self, item: QTreeWidgetItem, kind: str, manga_id: str, chapter_id: str) -> None:
        item.setData(0, ROLE_KIND, kind)
        item.setData(0, ROLE_MANGA_ID, manga_id)
        item.setData(0, ROLE_CHAPTER_ID, chapter_id)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

    def _apply_item_values(self, item: QTreeWidgetItem, title: str, url: str, settings: ItemSettings, status: str, enabled: bool, progress_text: str = "", eta_text: str = "") -> None:
        marker = "✓ " if enabled else "  "
        item.setText(0, marker + title)
        item.setText(1, url)
        if self._use_inline_widgets:
            # Columns 2–4 use real inline comboboxes. Keeping text underneath those
            # widgets causes doubled/bold-looking text on several Qt themes.
            item.setText(2, "")
            item.setText(3, "")
            item.setText(4, "")
        else:
            item.setText(2, self.tr("reading_" + settings.reading_style))
            item.setText(3, self.tr("mode_" + settings.output_mode))
            item.setText(4, self.tr("format_" + settings.image_format))
        item.setText(5, self._status_text(status))
        item.setText(6, progress_text)
        item.setText(7, eta_text)
        item.setToolTip(0, self.tr("toggle_enabled_hint"))
        item.setToolTip(1, url)

    def _install_row_setting_widgets(self, item: QTreeWidgetItem, manga: MangaEntry, chapter: ChapterEntry | None) -> None:
        if not self._use_inline_widgets:
            return
        settings = chapter.settings if chapter else manga.settings
        chapter_id = chapter.item_id if chapter else ""
        self.tree.setItemWidget(item, 2, self._make_inline_combo(READING_STYLES, "reading_", settings.reading_style, manga.item_id, chapter_id, "reading_style"))
        self.tree.setItemWidget(item, 3, self._make_inline_combo(OUTPUT_MODES, "mode_", settings.output_mode, manga.item_id, chapter_id, "output_mode"))
        self.tree.setItemWidget(item, 4, self._make_inline_combo(IMAGE_FORMATS, "format_", settings.image_format, manga.item_id, chapter_id, "image_format"))

    def _make_inline_combo(self, values: Iterable[str], prefix: str, current: str, manga_id: str, chapter_id: str, field: str) -> QComboBox:
        values = tuple(values)
        combo = QComboBox(self.tree)
        combo.setObjectName("InlineCombo")
        combo.setFrame(False)
        combo.setMinimumHeight(26)
        combo.setMaximumHeight(28)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for value in values:
            combo.addItem(self.tr(prefix + value), value)
        self._prepare_combo_popup(combo, len(values))
        idx = combo.findData(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.currentIndexChanged.connect(lambda _index=0, cb=combo, mid=manga_id, cid=chapter_id, f=field: self.on_inline_setting_changed(mid, cid, f, str(cb.currentData() or "")))
        return combo

    def on_inline_setting_changed(self, manga_id: str, chapter_id: str, field: str, value: str) -> None:
        if self._building_tree or self._restoring or not value:
            return
        manga = self.get_manga(manga_id)
        if not manga:
            return
        target_settings = manga.settings if not chapter_id else None
        chapter = None
        if chapter_id:
            chapter = self.get_chapter(manga, chapter_id)
            if not chapter:
                return
            target_settings = chapter.settings
        if target_settings is None or getattr(target_settings, field) == value:
            return
        self._push_undo()
        setattr(target_settings, field, value)
        # Inline changes on the manga row become the default for all chapters below it.
        if not chapter_id:
            for child in manga.chapters:
                setattr(child.settings, field, value)
        self._schedule_refresh_tree()

    def on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building_tree or self._restoring or column != 0:
            return
        self.toggle_item_enabled(item)

    def toggle_item_enabled(self, item: QTreeWidgetItem) -> None:
        manga = self.get_manga(str(item.data(0, ROLE_MANGA_ID) or ""))
        if not manga:
            return
        kind = item.data(0, ROLE_KIND)
        chapter_id = str(item.data(0, ROLE_CHAPTER_ID) or "")
        self._push_undo()
        if kind == "manga":
            manga.enabled = not manga.enabled
            for chapter in manga.chapters:
                chapter.enabled = manga.enabled
        else:
            chapter = self.get_chapter(manga, chapter_id)
            if chapter:
                chapter.enabled = not chapter.enabled
        self._schedule_refresh_tree()

    def on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        # Kept as a compatibility no-op. Enabled/disabled state is now handled
        # by the text checkmark in column 0 instead of a Qt checkbox widget.
        return

    def get_manga(self, manga_id: str) -> MangaEntry | None:
        for manga in self.mangas:
            if manga.item_id == manga_id:
                return manga
        return None

    def get_chapter(self, manga: MangaEntry, chapter_id: str) -> ChapterEntry | None:
        for chapter in manga.chapters:
            if chapter.item_id == chapter_id:
                return chapter
        return None

    def find_tree_item(self, manga_id: str, chapter_id: str = "") -> QTreeWidgetItem | None:
        for index in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(index)
            if not parent:
                continue
            if str(parent.data(0, ROLE_MANGA_ID) or "") != manga_id:
                continue
            if not chapter_id:
                return parent
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if child and str(child.data(0, ROLE_CHAPTER_ID) or "") == chapter_id:
                    return child
        return None


    def selected_item_infos(self) -> list[tuple[QTreeWidgetItem, MangaEntry, ChapterEntry | None]]:
        items = self.tree.selectedItems() or ([self.tree.currentItem()] if self.tree.currentItem() else [])
        result: list[tuple[QTreeWidgetItem, MangaEntry, ChapterEntry | None]] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            if item is None:
                continue
            manga = self.get_manga(str(item.data(0, ROLE_MANGA_ID) or ""))
            if not manga:
                continue
            chapter = None
            chapter_id = str(item.data(0, ROLE_CHAPTER_ID) or "")
            if item.data(0, ROLE_KIND) == "chapter":
                chapter = self.get_chapter(manga, chapter_id)
            key = (manga.item_id, chapter.item_id if chapter else "")
            if key not in seen:
                result.append((item, manga, chapter))
                seen.add(key)
        return result

    def open_item_settings(self) -> None:
        infos = self.selected_item_infos()
        if not infos:
            QMessageBox.information(self, APP_NAME, self.tr("no_selection"))
            return
        first_settings = infos[0][2].settings if infos[0][2] else infos[0][1].settings
        allow_children = len(infos) == 1 and infos[0][2] is None
        dialog = ItemSettingsDialog(first_settings, self.tr, allow_children=allow_children, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._push_undo()
        new_settings = dialog.selected_settings()
        for _item, manga, chapter in infos:
            if chapter:
                chapter.settings = new_settings.clone()
            else:
                manga.settings = new_settings.clone()
                if dialog.should_apply_to_children():
                    for child in manga.chapters:
                        child.settings = new_settings.clone()
        self.refresh_tree()



    def open_weebcentral_homepage(self) -> None:
        self.append_log(self.tr("opening_url", url="https://weebcentral.com/"))
        self.open_external_url("https://weebcentral.com/")

    def _external_open_env(self) -> dict[str, str]:
        """Return a clean environment for launching external desktop apps from PyInstaller."""
        env = os.environ.copy()

        # PyInstaller modifies library paths for the bundled app. If those values leak
        # into xdg-open, KDE helpers or the browser, external programs can silently fail
        # because they load bundled Qt/libstdc++ libraries instead of system libraries.
        original_ld_path = env.get("LD_LIBRARY_PATH_ORIG")
        if original_ld_path is not None:
            env["LD_LIBRARY_PATH"] = original_ld_path
        else:
            env.pop("LD_LIBRARY_PATH", None)

        for variable in (
            "PYTHONHOME",
            "PYTHONPATH",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            "QTWEBENGINEPROCESS_PATH",
            "QML2_IMPORT_PATH",
        ):
            env.pop(variable, None)

        return env

    def _launch_command(self, command: tuple[str, ...], wait_briefly: bool = True) -> bool:
        """Launch a system opener/browser command and detect immediate failures."""
        executable = command[0]
        if not shutil.which(executable):
            return False
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=self._external_open_env(),
            )
            if not wait_briefly:
                return True
            try:
                return process.wait(timeout=0.75) == 0
            except subprocess.TimeoutExpired:
                return True
        except Exception:
            return False

    def open_external_url(self, url: str) -> None:
        """Open a URL reliably in source and PyInstaller builds on Linux, Windows and macOS."""
        url = str(url or "").strip()
        if not url:
            return

        try:
            if sys.platform.startswith("linux"):
                # On Linux/Fedora/KDE, prefer clean subprocess launches first.
                # QDesktopServices can report success in PyInstaller builds even when
                # the desktop helper fails because of inherited bundled library paths.
                opener_commands = (
                    ("xdg-open", url),
                    ("kde-open6", url),
                    ("kde-open5", url),
                    ("kioclient6", "exec", url),
                    ("kioclient5", "exec", url),
                    ("kioclient", "exec", url),
                    ("gio", "open", url),
                )
                for command in opener_commands:
                    if self._launch_command(command, wait_briefly=True):
                        self.append_log(self.tr("url_opened_with", command=command[0]))
                        return

                browser_commands = (
                    ("firefox", "--new-tab", url),
                    ("flatpak", "run", "org.mozilla.firefox", url),
                    ("brave-browser", url),
                    ("google-chrome", url),
                    ("chromium", url),
                    ("chromium-browser", url),
                    ("microsoft-edge", url),
                    ("vivaldi", url),
                )
                for command in browser_commands:
                    if self._launch_command(command, wait_briefly=False):
                        self.append_log(self.tr("url_opened_with", command=command[0]))
                        return

                # Final fallback through Qt after the system helpers.
                try:
                    qurl = QUrl.fromUserInput(url)
                    if QDesktopServices.openUrl(qurl):
                        self.append_log(self.tr("url_opened_with", command="Qt"))
                        return
                except Exception:
                    pass

            elif sys.platform == "win32":
                os.startfile(url)  # type: ignore[attr-defined]
                return

            elif sys.platform == "darwin":
                if self._launch_command(("open", url), wait_briefly=True):
                    return

            else:
                try:
                    qurl = QUrl.fromUserInput(url)
                    if QDesktopServices.openUrl(qurl):
                        return
                except Exception:
                    pass

            try:
                if webbrowser.open(url, new=2, autoraise=True):
                    return
            except Exception:
                pass

        except Exception:
            pass

        QMessageBox.warning(
            self,
            self.tr("open_url_failed_title"),
            self.tr("open_url_failed_message", url=url),
        )
        self.append_log(self.tr("open_url_failed_message", url=url))

    def open_item_url(self, item: QTreeWidgetItem | None = None, _column: int = 0) -> None:
        item = item or self.tree.currentItem()
        if not item:
            return
        url = item.text(1).strip()
        if url:
            self.open_external_url(url)


    def _saved_reader_position_for_manga(self, manga: MangaEntry) -> dict | None:
        self.settings.sync()
        try:
            info = self._library_info_for_manga(manga)
            record = self._reading_state_store().get(
                str(info.get("manga_id") or "") if info else "",
                path=Path(info["path"]) if info else None,
                title=manga.title,
                source_url=manga.url,
            )
            if record and record.get("chapter_title"):
                global_index = record.get("global_index")
                return {
                    "title": str(record.get("title") or manga.title),
                    "chapter_id": str(record.get("chapter_id") or ""),
                    "chapter_title": str(record.get("chapter_title") or ""),
                    "page_index": max(0, int(record.get("page_index", 0) or 0)),
                    "global_index": None if global_index is None else max(0, int(global_index)),
                    "remembered_page": max(0, int(record.get("page_index", 0) or 0)) + 1,
                }
        except Exception:
            pass
        has_position = str(self.settings.value("reader/has_position", "false") or "false").lower() == "true"
        if not has_position:
            return None

        saved_title = str(self.settings.value("reader/last_manga_title", "") or "").strip()
        saved_url = str(self.settings.value("reader/last_manga_url", "") or "").strip()
        same_manga = bool(saved_title and saved_title == manga.title) or bool(saved_url and saved_url == manga.url)
        if not same_manga:
            return None

        try:
            page_index = int(self.settings.value("reader/last_page_index", 0) or 0)
        except Exception:
            page_index = 0
        try:
            global_index = int(self.settings.value("reader/last_global_index", -1) or -1)
        except Exception:
            global_index = -1
        if global_index < 0:
            global_index = None

        return {
            "title": saved_title or manga.title,
            "chapter_id": str(self.settings.value("reader/last_chapter_id", "") or "").strip(),
            "chapter_title": str(self.settings.value("reader/last_chapter_title", "") or "").strip(),
            "page_index": page_index,
            "global_index": global_index,
            "remembered_page": page_index + 1,
        }

    def _latest_chapter_start_for_manga(self, manga: MangaEntry) -> tuple[str, str, int | None]:
        for chapter in reversed(manga.chapters):
            if chapter_exists_on_disk(self.current_output_dir(), manga.title, chapter.title):
                return chapter.item_id, chapter.title, None
        if manga.chapters:
            chapter = manga.chapters[-1]
            return chapter.item_id, chapter.title, None
        return "", "", None

    def _reader_start_beginning(self) -> tuple[str, str, int, int | None, bool]:
        return "", "", 0, None, False

    def _reader_start_latest(self, manga: MangaEntry) -> tuple[str, str, int, int | None, bool]:
        chapter_id, chapter_title, global_index = self._latest_chapter_start_for_manga(manga)
        return chapter_id, chapter_title, 0, global_index, False

    def _reader_start_continue(self, saved_position: dict) -> tuple[str, str, int, int | None, bool]:
        # Resume the exact page stored by either reader. Using the next page here
        # would make desktop and mobile progress disagree by one page.
        return (
            saved_position["chapter_id"],
            saved_position["chapter_title"],
            saved_position["page_index"],
            saved_position["global_index"],
            False,
        )

    def _ask_reader_start_choice(self, manga: MangaEntry, saved_position: dict | None) -> tuple[str, str, int, int | None, bool] | None:
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("reader_start_title"))
        if saved_position:
            box.setText(self.tr(
                "reader_start_text_with_continue",
                manga=manga.title,
                chapter=saved_position["chapter_title"],
                page=saved_position["remembered_page"],
            ))
        else:
            box.setText(self.tr("reader_start_text", manga=manga.title))

        beginning_button = box.addButton(self.tr("reader_start_beginning"), QMessageBox.ButtonRole.AcceptRole)
        continue_button = box.addButton(self.tr("reader_start_continue"), QMessageBox.ButtonRole.AcceptRole)
        latest_button = box.addButton(self.tr("reader_start_latest"), QMessageBox.ButtonRole.AcceptRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        cancel_button.setText(self.tr("cancel"))

        if not saved_position:
            continue_button.setEnabled(False)
            continue_button.setToolTip(self.tr("reader_start_continue_unavailable"))

        box.setDefaultButton(continue_button if saved_position else latest_button)
        box.exec()
        clicked = box.clickedButton()

        if clicked is cancel_button:
            return None
        if clicked is continue_button and saved_position:
            return self._reader_start_continue(saved_position)
        if clicked is latest_button:
            return self._reader_start_latest(manga)
        if clicked is beginning_button:
            return self._reader_start_beginning()
        return None


    def open_reader(self) -> None:
        infos = self.selected_item_infos()
        if not infos:
            QMessageBox.information(self, APP_NAME, self.tr("reader_no_selection"))
            return

        _item, manga, chapter = infos[0]
        start_chapter_id = chapter.item_id if chapter else ""
        start_chapter_title = chapter.title if chapter else ""
        start_page_index = 0
        start_global_index = None
        start_after = False

        # If a concrete chapter row is selected, open that chapter directly.
        # If the manga row is selected, ask where reading should start.
        if chapter is None:
            choice = self._ask_reader_start_choice(manga, self._saved_reader_position_for_manga(manga))
            if choice is None:
                return
            start_chapter_id, start_chapter_title, start_page_index, start_global_index, start_after = choice

        self.open_reader_for_manga(
            manga,
            start_chapter_id=start_chapter_id,
            start_chapter_title=start_chapter_title,
            start_page_index=start_page_index,
            start_global_index=start_global_index,
            start_after=start_after,
        )

    def open_reader_for_manga(
        self,
        manga: MangaEntry,
        start_chapter_id: str = "",
        start_chapter_title: str = "",
        start_page_index: int = 0,
        start_global_index: int | None = None,
        start_after: bool = False,
        manga_dir: str | Path | None = None,
    ) -> None:
        if manga_dir is None:
            info = self._library_info_for_manga(manga)
            manga_dir = Path(info["path"]) if info else (Path(self.current_output_dir()) / sanitize_filename(manga.title, "Manga"))
        dialog = MangaReaderDialog(
            manga=manga,
            output_dir=self.current_output_dir(),
            tr=self.tr,
            settings=self.settings,
            start_chapter_id=start_chapter_id,
            start_chapter_title=start_chapter_title,
            start_page_index=start_page_index,
            start_global_index=start_global_index,
            start_after=start_after,
            manga_dir=manga_dir,
            reading_store=self._reading_state_store(),
            parent=self,
        )
        if not dialog.pages:
            QMessageBox.information(self, self.tr("reader_title"), self.tr("reader_no_pages"))
            dialog.deleteLater()
            return
        dialog.exec()
        if self.library_page.isVisible():
            self._refresh_library_reading_state_only(manga_id_for_path(Path(manga_dir)))

    def _manga_from_disk(self, title: str, url: str, output_dir: str) -> MangaEntry | None:
        manga_dir = Path(output_dir) / title
        if not manga_dir.exists() or not manga_dir.is_dir():
            # The stored title is already sanitized in most cases, but keep a fallback
            # for older settings where the display title was stored.
            from .scraper import sanitize_filename
            manga_dir = Path(output_dir) / sanitize_filename(title, "Manga")
        if not manga_dir.exists() or not manga_dir.is_dir():
            return None

        chapters: list[ChapterEntry] = []
        seen: set[str] = set()

        for folder in sorted([item for item in manga_dir.iterdir() if item.is_dir()], key=lambda item: chapter_sort_key(item.name)):
            has_images = any(
                item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS and item.stat().st_size > 0
                for item in folder.iterdir()
            )
            if has_images and folder.name not in seen:
                chapters.append(ChapterEntry(title=folder.name, url=""))
                seen.add(folder.name)

        for archive in sorted([item for item in manga_dir.iterdir() if item.is_file() and item.suffix.lower() == ".cbz"], key=lambda item: chapter_sort_key(item.name)):
            chapter_title = archive.stem
            if chapter_title not in seen and archive.stat().st_size > 0:
                chapters.append(ChapterEntry(title=chapter_title, url=""))
                seen.add(chapter_title)

        if not chapters:
            return None
        chapters.sort(key=lambda chapter: chapter_sort_key(chapter.title))
        return MangaEntry(title=title, url=url, chapters=chapters)

    def maybe_resume_reader_on_startup(self) -> None:
        self.settings.sync()
        try:
            recent = self._reading_state_store().recent(1)
        except Exception:
            recent = []
        if recent:
            shared = recent[0]
            title = str(shared.get("title") or "").strip()
            chapter = str(shared.get("chapter_title") or "").strip()
            if title and chapter:
                try:
                    remembered_page = int(shared.get("page_index", 0) or 0) + 1
                except Exception:
                    remembered_page = 1
                if not localized_question(
                    self, self.tr, self.tr("reader_continue_title"),
                    self.tr("reader_continue_text", manga=title, chapter=chapter, page=remembered_page),
                    default_yes=True,
                ):
                    return
                manga = self.find_manga_by_title_or_url(title, str(shared.get("source_url") or ""))
                output_dir = self.current_output_dir()
                if not manga:
                    manga = self._manga_from_disk(title, str(shared.get("source_url") or ""), output_dir)
                if manga:
                    self.open_reader_for_manga(
                        manga,
                        start_chapter_id=str(shared.get("chapter_id") or ""),
                        start_chapter_title=chapter,
                        start_page_index=int(shared.get("page_index", 0) or 0),
                        start_global_index=shared.get("global_index"),
                        start_after=False,
                        manga_dir=Path(shared.get("path")) if shared.get("path") else None,
                    )
                    return
        has_position = str(self.settings.value("reader/has_position", "false") or "false").lower() == "true"
        title = str(self.settings.value("reader/last_manga_title", "") or "").strip()
        chapter = str(self.settings.value("reader/last_chapter_title", "") or "").strip()
        if not has_position or not title or not chapter:
            return
        if self.download_worker or self.resolve_worker or self.update_worker:
            return

        try:
            remembered_page = int(self.settings.value("reader/last_page_index", 0) or 0) + 1
        except Exception:
            remembered_page = 1
        if not localized_question(
            self,
            self.tr,
            self.tr("reader_continue_title"),
            self.tr("reader_continue_text", manga=title, chapter=chapter, page=remembered_page),
            default_yes=True,
        ):
            return

        url = str(self.settings.value("reader/last_manga_url", "") or "").strip()
        chapter_id = str(self.settings.value("reader/last_chapter_id", "") or "").strip()
        try:
            page_index = int(self.settings.value("reader/last_page_index", 0) or 0)
        except Exception:
            page_index = 0
        try:
            global_index = int(self.settings.value("reader/last_global_index", -1) or -1)
        except Exception:
            global_index = -1
        if global_index < 0:
            global_index = None
        output_dir = str(self.settings.value("reader/last_output_dir", "") or "").strip() or self.current_output_dir()

        manga = self.find_manga_by_title_or_url(title, url)
        if not manga:
            manga = self._manga_from_disk(title, url, output_dir)
        if not manga:
            QMessageBox.information(self, self.tr("reader_title"), self.tr("reader_saved_not_found"))
            return

        # Temporarily use the saved output path if it differs from the visible target folder.
        previous_output = self.output_input.text()
        if output_dir and output_dir != self.current_output_dir():
            self.output_input.setText(output_dir)
        try:
            self.open_reader_for_manga(
                manga,
                start_chapter_id=chapter_id,
                start_chapter_title=chapter,
                start_page_index=page_index,
                start_global_index=global_index,
                start_after=False,
            )
        finally:
            if previous_output != self.output_input.text() and not previous_output:
                self.output_input.clear()


    def show_tree_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item and not item.isSelected():
            self.tree.setCurrentItem(item)
        menu = QMenu(self)
        settings_action = menu.addAction(self.tr("item_settings"))
        toggle_action = menu.addAction(self.tr("toggle_enabled"))
        open_action = menu.addAction(self.tr("open_in_browser"))
        remove_action = menu.addAction(self.tr("remove"))
        action = menu.exec(self.tree.viewport().mapToGlobal(position))
        if action == settings_action:
            self.open_item_settings()
        elif action == toggle_action:
            current = self.tree.currentItem()
            if current:
                self.toggle_item_enabled(current)
        elif action == open_action:
            self.open_item_url()
        elif action == remove_action:
            self.remove_selected()

    def remove_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, APP_NAME, self.tr("no_selection"))
            return
        self._push_undo()
        remove_manga_ids = set()
        remove_chapters: set[tuple[str, str]] = set()
        removed_messages: list[str] = []
        for item in items:
            kind = item.data(0, ROLE_KIND)
            manga_id = str(item.data(0, ROLE_MANGA_ID) or "")
            chapter_id = str(item.data(0, ROLE_CHAPTER_ID) or "")
            manga = self.get_manga(manga_id)
            if not manga:
                continue
            if kind == "manga":
                remove_manga_ids.add(manga_id)
                removed_messages.append(self.tr("log_removed_manga", title=manga.title, count=len(manga.chapters)))
            else:
                chapter = self.get_chapter(manga, chapter_id)
                if chapter:
                    remove_chapters.add((manga_id, chapter_id))
                    removed_messages.append(self.tr("log_removed_chapter", manga=manga.title, chapter=chapter.title))
        self.mangas = [manga for manga in self.mangas if manga.item_id not in remove_manga_ids]
        for manga in self.mangas:
            manga.chapters = [chapter for chapter in manga.chapters if (manga.item_id, chapter.item_id) not in remove_chapters]
        self.mangas = [manga for manga in self.mangas if manga.chapters]
        self.refresh_tree()
        for message in removed_messages:
            self.append_log(message)
        if removed_messages:
            self.append_log(self.tr("log_removed_summary", count=len(removed_messages)))

    def clear_queue(self) -> None:
        if not self.mangas:
            return
        if localized_question(
            self, self.tr, self.tr("confirm_clear_title"), self.tr("confirm_clear_text"), default_yes=False
        ):
            count = sum(1 + len(manga.chapters) for manga in self.mangas)
            self._push_undo()
            self.mangas.clear()
            self.refresh_tree()
            self.append_log(self.tr("log_queue_cleared", count=count))

    def queue_dialog_filter(self) -> str:
        return f"{self.tr('queue_filter')};;{self.tr('all_files_filter')}"

    def read_queue_file(self, path: str) -> tuple[list[MangaEntry], str]:
        queue_path = Path(path)
        raw_text = queue_path.read_text(encoding="utf-8-sig")
        data = json.loads(raw_text)

        output_dir = ""
        if isinstance(data, dict):
            output_dir = str(data.get("output_dir", "") or "")
            manga_payload = data.get("mangas", [])
        elif isinstance(data, list):
            manga_payload = data
        else:
            raise ValueError(self.tr("load_queue_invalid_format"))

        if not isinstance(manga_payload, list):
            raise ValueError(self.tr("load_queue_invalid_format"))

        mangas: list[MangaEntry] = []
        skipped = 0
        for item in manga_payload:
            if not isinstance(item, dict):
                skipped += 1
                continue
            try:
                manga = MangaEntry.from_dict(item)
            except Exception:
                skipped += 1
                continue
            if manga.chapters:
                mangas.append(manga)
            else:
                skipped += 1

        if skipped:
            self.append_log(self.tr("log_queue_load_skipped", count=skipped))
        return mangas, output_dir


    def save_queue(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("save_queue"), self.tr("queue_default_file"), self.queue_dialog_filter())
        if not path:
            return
        data = {
            "version": QUEUE_FILE_VERSION,
            "output_dir": self.output_input.text().strip(),
            "mangas": [manga.to_dict() for manga in self.mangas],
        }
        if Path(path).suffix == "":
            path += ".json"
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_label.setText(self.tr("saved"))
        self.append_log(self.tr("log_queue_saved", path=path, count=len(self.mangas)))

    def load_queue(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("load_queue"), "", self.queue_dialog_filter())
        if not path:
            return
        self.append_log(self.tr("log_queue_load_started", path=path))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            loaded_mangas, output_dir = self.read_queue_file(path)
            self._push_undo()
            if output_dir:
                self.output_input.setText(output_dir)
            self.mangas = loaded_mangas
            row_count = sum(1 + len(manga.chapters) for manga in self.mangas)
            if row_count <= 2000:
                self.mark_all_existing_chapters(disable=True)
            else:
                self.append_log(self.tr("log_large_queue_disk_scan_skipped", rows=row_count))
            self.refresh_tree()
            if row_count > INLINE_WIDGET_ROW_LIMIT:
                self.append_log(self.tr("log_queue_large_optimized", rows=row_count))
            if self.library_page.isVisible():
                self.refresh_library()
            self.status_label.setText(self.tr("loaded"))
            self.append_log(self.tr("log_queue_loaded", path=path, count=len(self.mangas)))
        except Exception as exc:
            self.status_label.setText(self.tr("load_queue_failed"))
            self.append_log(self.tr("log_queue_load_failed", path=path, error=exc))
            QMessageBox.warning(
                self,
                self.tr("load_queue_failed_title"),
                self.tr("load_queue_failed_text", error=exc),
            )
        finally:
            QApplication.restoreOverrideCursor()

    def search_queue(self) -> None:
        text, ok = localized_text_input(self, self.tr, self.tr("search_title"), self.tr("search_prompt"))
        if not ok or not text.strip():
            return
        needle = text.strip().casefold()
        iterator = self.tree.findItems("*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive)
        for item in iterator:
            if any(needle in item.text(col).casefold() for col in range(self.tree.columnCount())):
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return
        QMessageBox.information(self, self.tr("search_title"), self.tr("search_not_found"))

    def start_download(self) -> None:
        self._start_download(resume=False)

    def resume_download(self) -> None:
        self._start_download(resume=True)

    def _start_download(
        self,
        resume: bool,
        chapter_ids_by_manga: dict[str, set[str]] | None = None,
    ) -> None:
        if self._closing:
            return
        if self.download_worker is not None and self.download_worker.isRunning():
            self.append_log(self.tr("log_download_already_running"))
            return
        if not self.mangas:
            QMessageBox.information(self, APP_NAME, self.tr("queue_empty"))
            return
        output_dir = self.current_output_dir()
        targeted = chapter_ids_by_manga is not None

        def chapter_is_targeted(manga: MangaEntry, chapter: ChapterEntry) -> bool:
            if chapter_ids_by_manga is None:
                return True
            return chapter.item_id in chapter_ids_by_manga.get(manga.item_id, set())

        self._save_settings()
        if targeted:
            for manga in self.mangas:
                if manga.item_id in chapter_ids_by_manga:
                    self.mark_existing_chapters(manga, disable=True)
        else:
            self.mark_all_existing_chapters(disable=True)

        active_chapters = sum(
            1
            for manga in self.mangas
            if manga.enabled
            for chapter in manga.chapters
            if chapter_is_targeted(manga, chapter)
            and chapter.enabled
            and not (resume and chapter.status == "done")
        )
        if active_chapters <= 0:
            self.status_label.setText(self.tr("download_no_active_chapters"))
            self.append_log(self.tr("log_no_active_chapters"))
            self.refresh_tree()
            return
        self.append_log(self.tr("log_download_resumed" if resume else "log_download_started", count=active_chapters, path=output_dir))
        for manga in self.mangas:
            if targeted and manga.item_id not in chapter_ids_by_manga:
                continue
            if not manga.enabled:
                if not targeted:
                    manga.status = "skipped"
                    for chapter in manga.chapters:
                        chapter.status = "skipped"
                continue
            manga.status = "pending"
            manga.progress_text = ""
            manga.eta_text = ""
            for chapter in manga.chapters:
                if not chapter_is_targeted(manga, chapter):
                    continue
                chapter.progress_text = ""
                chapter.eta_text = ""
                if not chapter.enabled:
                    if chapter.status != "done":
                        chapter.status = "skipped"
                elif resume and chapter.status == "done":
                    chapter.progress_text = self.tr("download_present")
                else:
                    chapter.status = "pending"
        self.refresh_tree()
        self.set_busy(True)
        worker_mangas = self.mangas
        if targeted:
            worker_mangas = []
            for manga in self.mangas:
                target_ids = chapter_ids_by_manga.get(manga.item_id, set())
                if not target_ids:
                    continue
                data = manga.to_dict()
                data["chapters"] = [
                    chapter.to_dict()
                    for chapter in manga.chapters
                    if chapter.item_id in target_ids
                ]
                worker_mangas.append(MangaEntry.from_dict(data))

        self.download_worker = QueueDownloadWorker(worker_mangas, output_dir, self.language, self, skip_done=resume)
        self.download_worker.log_message.connect(self.append_log)
        self.download_worker.chapter_status.connect(self.on_chapter_status)
        self.download_worker.manga_status.connect(self.on_manga_status)
        self.download_worker.chapter_progress.connect(self.on_chapter_progress)
        self.download_worker.manga_progress.connect(self.on_manga_progress)
        self._download_finished_result = None
        self.download_worker.global_progress.connect(self.on_progress)
        self.download_worker.finished_signal.connect(self.on_download_finished)
        self.download_worker.finished.connect(self.on_download_thread_finished)
        self.download_worker.start()

    def stop_download(self) -> None:
        if self.download_worker:
            self.download_worker.stop()
            self.stop_button.setEnabled(False)
            self.resume_button.setEnabled(True)
            self.status_label.setText(self.tr("status_stopping"))
            self.append_log(self.tr("log_download_stop_requested"))

    @Slot(str, str, str)
    def on_chapter_status(self, manga_id: str, chapter_id: str, status: str) -> None:
        manga = self.get_manga(manga_id)
        if not manga:
            return
        chapter = self.get_chapter(manga, chapter_id)
        if chapter:
            chapter.status = status
        item = self.find_tree_item(manga_id, chapter_id)
        if item:
            item.setText(5, self._status_text(status))
            if chapter:
                item.setText(6, chapter.progress_text)
                item.setText(7, chapter.eta_text)

    @Slot(str, str)
    def on_manga_status(self, manga_id: str, status: str) -> None:
        manga = self.get_manga(manga_id)
        if manga:
            manga.status = status
        item = self.find_tree_item(manga_id)
        if item:
            item.setText(5, self._status_text(status))
            if manga:
                item.setText(6, manga.progress_text)
                item.setText(7, manga.eta_text)

    @Slot(str, str, str, str)
    def on_chapter_progress(self, manga_id: str, chapter_id: str, progress_text: str, eta_text: str) -> None:
        manga = self.get_manga(manga_id)
        if not manga:
            return
        chapter = self.get_chapter(manga, chapter_id)
        if chapter:
            chapter.progress_text = progress_text
            chapter.eta_text = eta_text
        item = self.find_tree_item(manga_id, chapter_id)
        if item:
            item.setText(6, progress_text)
            item.setText(7, eta_text)

    @Slot(str, str, str)
    def on_manga_progress(self, manga_id: str, progress_text: str, eta_text: str) -> None:
        manga = self.get_manga(manga_id)
        if manga:
            manga.progress_text = progress_text
            manga.eta_text = eta_text
        item = self.find_tree_item(manga_id)
        if item:
            item.setText(6, progress_text)
            item.setText(7, eta_text)

    @Slot(float, str)
    def on_progress(self, value: float, text: str) -> None:
        self.progress.setValue(max(0, min(100, int(value))))
        self.status_label.setText(text)

    @Slot(bool, bool)
    def on_download_finished(self, ok: bool, stopped: bool) -> None:
        # This signal is emitted from QueueDownloadWorker.run() just before the
        # native QThread actually finishes. Do not delete the QThread here:
        # deleting it too early can crash Qt, especially when Stop is pressed
        # while image worker futures are winding down.
        self._download_finished_result = (ok, stopped)
        self.set_busy(False)
        self.stop_button.setEnabled(False)
        self.resume_button.setEnabled(True)
        result_key = "download_stopped" if stopped else ("download_finished" if ok else "download_failed")
        self.status_label.setText(self.tr(result_key))
        self.append_log(self.tr("log_download_result", result=self.tr(result_key)))
        self._library_dirty = True
        if self.library_page.isVisible():
            self.refresh_library()

    @Slot()
    def on_download_thread_finished(self) -> None:
        worker = self.sender()
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
        if worker is self.download_worker:
            self.download_worker = None
        if self._download_finished_result is None:
            self.set_busy(False)
            self.stop_button.setEnabled(False)
            self.resume_button.setEnabled(True)
            self.status_label.setText(self.tr("download_stopped"))
            self.append_log(self.tr("log_download_result", result=self.tr("download_stopped")))
        self._download_finished_result = None

    def _normalize_log_entry(self, message: object) -> object:
        if isinstance(message, TranslatableText):
            return message
        identified = self.translator.identify(message)
        return identified if identified is not None else str(message)

    def _render_log_entry(self, entry: object) -> str:
        return self.translator.render(entry)

    def _rerender_log(self) -> None:
        self.log_view.clear()
        for entry in self._log_entries:
            self.log_view.append(self._render_log_entry(entry))
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def append_log(self, message: object) -> None:
        entry = self._normalize_log_entry(message)
        self._log_entries.append(entry)
        max_blocks = 2500
        if len(self._log_entries) > max_blocks:
            del self._log_entries[:-max_blocks]
        self.log_view.append(self._render_log_entry(entry))
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def toggle_log(self) -> None:
        self._log_visible = not self._log_visible
        self._sync_log_layout()
        self.log_toggle_button.setText(self.tr("toggle_log_hide" if self._log_visible else "toggle_log_show"))
        self.append_log(self.tr("log_output_shown" if self._log_visible else "log_output_hidden"))

    def _sync_log_layout(self) -> None:
        self.log_view.setVisible(self._log_visible)
        if hasattr(self, "bottom_panel"):
            if self._log_visible:
                self.bottom_panel.setMaximumHeight(16777215)
                if hasattr(self, "splitter"):
                    self.splitter.setSizes([680, 160])
            else:
                self.bottom_panel.setMaximumHeight(26)
                if hasattr(self, "splitter"):
                    self.splitter.setSizes([9999, 24])

    def on_language_changed(self, index: int) -> None:
        code = self.language_combo.itemData(index)
        if not code or code == self.language:
            return
        self.language = normalize_language(str(code))
        self.translator.set_language(self.language)
        if self._mobile_server is not None:
            self._mobile_server.set_language(self.language)
        for worker in (self.resolve_worker, self.update_worker, self.download_worker):
            translator = getattr(worker, "translator", None) if worker is not None else None
            if translator is not None:
                translator.set_language(self.language)
        self._save_settings()
        self.retranslate_ui()
        if self._library_entries is not None:
            self._render_library(self._sort_library_entries(self._library_entries))
        if self.download_worker is not None:
            self.status_label.setText(self.tr("status_running"))
        elif self.update_worker is not None or self.resolve_worker is not None:
            self.status_label.setText(self.tr("status_resolving"))
        else:
            self.status_label.setText(self.tr("ready"))
        self._rerender_log()
        self.append_log(tr_message("log_language_set", language=language_label(self.language, self.language)))

    def open_preferences(self) -> None:
        dialog = PreferencesDialog(self.theme, self.tr, self.custom_themes, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.theme = dialog.selected_theme()
        self.custom_themes = dialog.selected_custom_themes()
        template_name = dialog.selected_template_name()
        apply_theme(QApplication.instance(), self.theme)
        self._save_settings()
        self.retranslate_ui()
        self.append_log(self.tr("log_theme_applied", theme=template_name))

    def open_app_settings(self) -> None:
        manga_list = self._library_entries if self._library_entries is not None else self.scan_library_mangas()
        dialog = AppSettingsDialog(
            self.default_item_settings(),
            self.check_updates_on_startup.isChecked(),
            self.auto_download_updates.isChecked(),
            self.automation,
            self.tr,
            output_dir=self.current_output_dir(),
            manga_list=manga_list,
            theme=self.theme,
            custom_themes=self.custom_themes,
            mobile_reader_enabled=self._mobile_reader_enabled,
            mobile_reader_port=self._mobile_reader_port,
            mobile_reader_host=self._mobile_reader_host,
            mobile_reader_urls=self._mobile_reader_display_urls(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        requested_manga_action = dialog.requested_manga_action()

        settings = dialog.selected_settings()
        # Push the chosen defaults back into the hidden backing widgets so that
        # default_item_settings() and _save_settings() keep working unchanged.
        for combo, value in (
            (self.reading_combo, settings.reading_style),
            (self.output_combo, settings.output_mode),
            (self.format_combo, settings.image_format),
        ):
            index = combo.findData(value)
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        for widget, value in (
            (self.keep_images, settings.keep_images),
            (self.check_updates_on_startup, dialog.check_updates_on_startup_enabled()),
            (self.auto_download_updates, dialog.auto_download_updates_enabled()),
        ):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)
        self.threads.blockSignals(True)
        self.threads.setValue(settings.image_threads)
        self.threads.blockSignals(False)
        self.delay.blockSignals(True)
        self.delay.setValue(settings.request_delay)
        self.delay.blockSignals(False)

        previous_active = self.automation.active
        self.automation = dialog.automation_schedule()
        previous_mobile_enabled = self._mobile_reader_enabled
        previous_mobile_port = self._mobile_reader_port
        previous_mobile_host = self._mobile_reader_host
        self._mobile_reader_enabled = dialog.mobile_reader_enabled()
        self._mobile_reader_port = dialog.mobile_reader_port()
        self._mobile_reader_host = dialog.mobile_reader_host()

        # Apply per-manga update/download flags only when values actually changed.
        # The former implementation rewrote every metadata file on every OK click,
        # marked the library dirty and then performed a full rescan, which caused a
        # noticeable freeze when returning from Settings.
        flags_changed = False
        cached_by_path = {
            str(Path(info["path"]).expanduser().resolve(strict=False)): info
            for info in (self._library_entries or [])
            if info.get("path")
        }
        for entry in dialog.manga_flags():
            raw_path = entry.get("path")
            if not raw_path:
                continue
            manga_path = Path(raw_path)
            try:
                data = read_manga_metadata_dir(manga_path)
                old_check = self._flag(data.get("check_updates"), True)
                old_auto = self._flag(data.get("auto_download"), True)
                new_check = bool(entry.get("check_updates"))
                new_auto = bool(entry.get("auto_download"))
                if old_check == new_check and old_auto == new_auto:
                    continue
                data.setdefault("app", "MangoDango")
                data["check_updates"] = new_check
                data["auto_download"] = new_auto
                write_manga_metadata_dir(manga_path, data)
                flags_changed = True
                cache_key = str(manga_path.expanduser().resolve(strict=False))
                cached = cached_by_path.get(cache_key)
                if cached is not None:
                    cached["check_updates"] = new_check
                    cached["auto_download"] = new_auto
            except Exception:
                pass
        if flags_changed and self._mobile_server is not None:
            self._mobile_server.invalidate()

        # Apply appearance changes made in the personalisation tab.
        new_theme = dialog.selected_theme()
        if new_theme is not None:
            self.theme = new_theme
            self.custom_themes = dialog.selected_custom_themes()
            apply_theme(QApplication.instance(), self.theme)

        # Apply the manga directory if it changed.
        new_dir = dialog.selected_output_dir()
        dir_changed = bool(new_dir) and new_dir != self.current_output_dir()
        if dir_changed:
            self.output_input.setText(new_dir)

        self._save_settings()
        self._start_automation_timer()
        if (
            previous_mobile_enabled != self._mobile_reader_enabled
            or previous_mobile_port != self._mobile_reader_port
            or previous_mobile_host != self._mobile_reader_host
            or dir_changed
        ):
            self._sync_mobile_reader_server()
        if new_theme is not None:
            self.retranslate_ui()
        self.append_log(self.tr("log_settings_saved"))
        if dir_changed:
            self.append_log(self.tr("log_default_output", folder=new_dir))
            self._library_entries = None
            self._library_dirty = True
            if self.library_page.isVisible():
                self.refresh_library()
        if self.automation.active:
            self.append_log(self.tr("automation_enabled_log", count=len(self.automation.slots)))
        elif previous_active:
            self.append_log(self.tr("automation_disabled_log"))

        if requested_manga_action is not None:
            action, selected_mangas = requested_manga_action
            selected_titles = [str(item.get("title", "")).strip() for item in selected_mangas]
            self.start_update_check(
                background=False,
                selected_titles=selected_titles,
                download_immediately=action == "download",
            )

    def _mobile_reader_display_urls(self) -> list[str]:
        if self._mobile_server is not None and self._mobile_server.is_running:
            return self._mobile_server.urls()
        return mobile_reader_urls(self._mobile_reader_port, host=self._mobile_reader_host)

    def _stop_mobile_reader_server(self) -> None:
        server = self._mobile_server
        self._mobile_server = None
        if server is None:
            return
        try:
            server.stop()
        except Exception:
            pass

    def _sync_mobile_reader_server(self) -> None:
        if not self._mobile_reader_enabled:
            self._stop_mobile_reader_server()
            self._mobile_reader_last_error = ""
            return

        output_dir = self.current_output_dir()
        current = self._mobile_server
        if (
            current is not None
            and current.is_running
            and current.port == self._mobile_reader_port
            and current.host == self._mobile_reader_host
        ):
            current.set_library_dir(output_dir)
            current.set_language(self.language)
            current.invalidate()
            self._mobile_reader_last_error = ""
            return

        self._stop_mobile_reader_server()
        try:
            server = MobileLibraryServer(
                output_dir,
                port=self._mobile_reader_port,
                host=self._mobile_reader_host,
                language=self.language,
            )
            urls = server.start()
        except MobileReaderConfigurationError as exc:
            message = self.tr(exc.translation_key, **exc.translation_kwargs)
            self._mobile_reader_last_error = message
            self.append_log(self.tr("mobile_reader_start_failed", error=message))
            return
        except OSError as exc:
            self._mobile_reader_last_error = str(exc)
            self.append_log(self.tr("mobile_reader_start_failed", error=exc))
            return
        except Exception as exc:
            self._mobile_reader_last_error = str(exc)
            self.append_log(self.tr("mobile_reader_start_failed", error=exc))
            return

        self._mobile_server = server
        self._mobile_reader_last_error = ""
        if urls:
            self.append_log(self.tr("mobile_reader_started", url=urls[0]))
        else:
            self.append_log(self.tr("mobile_reader_started_local_only", port=self._mobile_reader_port))

    def _start_automation_timer(self) -> None:
        # A single lightweight timer polls every 60 s and fires an update check
        # when a scheduled slot has passed. Reset the baseline so slots that were
        # already in the past today do not trigger immediately.
        self._automation_last_run = datetime.now()
        if self._automation_timer is None:
            self._automation_timer = QTimer(self)
            self._automation_timer.timeout.connect(self._check_automation)
        if self.automation.active:
            if not self._automation_timer.isActive():
                self._automation_timer.start(60_000)
            next_run = self.automation.next_run(self._automation_last_run)
            if next_run is not None:
                self.append_log(self.tr("server_next_run", time=next_run.strftime("%Y-%m-%d %H:%M")))
        else:
            self._automation_timer.stop()

    def _check_automation(self) -> None:
        if self._closing or not self.automation.active:
            return
        if self.resolve_worker or self.update_worker or self.download_worker:
            return
        now = datetime.now()
        if not self.automation.due_since(self._automation_last_run, now):
            return
        self._automation_last_run = now
        self.append_log(self.tr("automation_triggered"))
        self.start_update_check(background=True)

    def _running_workers(self) -> list[QThread]:
        """Return every live QThread owned by the main window.

        Besides the three primary queue workers, the settings dialog can create
        an AppUpdateWorker parented to this window. Including all child QThreads
        prevents application shutdown from destroying such an auxiliary worker
        while it is still running.
        """
        candidates: list[QThread] = []
        seen: set[int] = set()
        for worker in (
            self.download_worker,
            self.update_worker,
            self.resolve_worker,
            *self.findChildren(QThread),
        ):
            if worker is None:
                continue
            marker = id(worker)
            if marker in seen:
                continue
            seen.add(marker)
            try:
                if worker.isRunning():
                    candidates.append(worker)
            except RuntimeError:
                # The wrapped QObject may already be scheduled for deletion.
                continue
        return candidates

    def closeEvent(self, event) -> None:
        self._stop_mobile_reader_server()
        # Destroying the window (and therefore its child QThread workers) while a
        # worker is still inside a network call crashes Qt with
        # "QThread: Destroyed while thread is still running" (SIGABRT). A blocking
        # wait() is not enough, because a single request can run up to its 30 s
        # timeout. Instead: ask the workers to stop, refuse the close for now, and
        # poll until every thread has finished — then close for real.
        if self._automation_timer is not None:
            self._automation_timer.stop()

        running = self._running_workers()
        if not running:
            super().closeEvent(event)
            return

        self._closing = True
        for worker in running:
            try:
                worker.stop()
            except Exception:
                pass

        self.status_label.setText(self.tr("status_closing"))
        self.append_log(self.tr("log_closing_wait"))
        # Disable the central content so nothing new can be started while we wait,
        # but keep the window itself alive until the threads are done.
        central = self.centralWidget()
        if central is not None:
            central.setEnabled(False)

        if self._await_close_timer is None:
            self._await_close_timer = QTimer(self)
            self._await_close_timer.timeout.connect(self._finalize_close)
        self._await_close_timer.start(200)
        event.ignore()

    def _finalize_close(self) -> None:
        if self._running_workers():
            return  # a worker is still finishing its current request; keep waiting
        if self._await_close_timer is not None:
            self._await_close_timer.stop()
        # All threads have finished cleanly; the real close is now safe.
        self._closing = False
        self.close()

    def reset_application_data(self) -> None:
        if not localized_question(
            self, self.tr, self.tr("reset_title"), self.tr("reset_confirm"), default_yes=False
        ):
            return
        self.settings.clear()
        self.settings.sync()
        self._mobile_reader_enabled = False
        self._mobile_reader_port = DEFAULT_MOBILE_READER_PORT
        self._mobile_reader_host = DEFAULT_MOBILE_READER_HOST
        self._stop_mobile_reader_server()
        for path in self._reset_paths():
            try:
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass
        self._push_undo()
        self.mangas.clear()
        self.custom_themes = []
        self.theme = ThemeSettings().normalized()
        self.language = normalize_language(QLocale.system().name())
        self.translator.set_language(self.language)
        self.output_input.clear()
        self.threads.setValue(4)
        self.delay.setValue(1.0)
        self.keep_images.setChecked(True)
        self.check_updates_on_startup.setChecked(False)
        self.auto_download_updates.setChecked(False)
        self.log_view.clear()
        self.progress.setValue(0)
        apply_theme(QApplication.instance(), self.theme)
        self.retranslate_ui()
        self.status_label.setText(self.tr("reset_done"))
        self.append_log(self.tr("log_reset_done"))

    def _reset_paths(self) -> list[Path]:
        home = Path.home()
        return [
            home / ".cache" / APP_NAME,
            home / ".config" / APP_NAME,
            home / ".local" / "share" / APP_NAME,
            Path(tempfile.gettempdir()) / APP_NAME,
        ]

    def _snapshot_queue(self) -> str:
        return json.dumps([manga.to_dict() for manga in self.mangas], ensure_ascii=False, sort_keys=True)

    def _push_undo(self) -> None:
        if self._restoring or self._building_tree:
            return
        snapshot = self._snapshot_queue()
        if not self._undo_stack or self._undo_stack[-1] != snapshot:
            self._undo_stack.append(snapshot)
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _restore_snapshot(self, snapshot: str) -> None:
        self._restoring = True
        try:
            data = json.loads(snapshot or "[]")
            self.mangas = [MangaEntry.from_dict(item) for item in data]
            self.refresh_tree()
        finally:
            self._restoring = False

    def undo(self) -> None:
        if not self._undo_stack:
            return
        current = self._snapshot_queue()
        previous = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._restore_snapshot(previous)

    def redo(self) -> None:
        if not self._redo_stack:
            return
        current = self._snapshot_queue()
        next_snapshot = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._restore_snapshot(next_snapshot)
