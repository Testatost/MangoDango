from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QLocale, QSettings, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut, QTextCursor
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
    QStackedWidget,
    QTextEdit,
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
from .i18n import SUPPORTED_LANGUAGES, Translator, language_label, normalize_language
from .models import ChapterEntry, ItemSettings, MangaEntry
from .scraper import chapter_output_info, normalize_weebcentral_url, sanitize_filename
from .ui.dialogs import ItemSettingsDialog, PreferencesDialog
from .ui.styles import ThemeSettings, apply_theme
from .workers import QueueDownloadWorker, ResolveWorker

ROLE_KIND = int(Qt.ItemDataRole.UserRole) + 1
ROLE_MANGA_ID = int(Qt.ItemDataRole.UserRole) + 2
ROLE_CHAPTER_ID = int(Qt.ItemDataRole.UserRole) + 3


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
        self.resolve_worker: ResolveWorker | None = None
        self.download_worker: QueueDownloadWorker | None = None
        self._building_tree = False
        self._restoring = False
        self._log_visible = True
        self._undo_stack: list[str] = []
        self._redo_stack: list[str] = []
        self._shortcuts: list[QShortcut] = []
        self._library_view_active = True
        self._tree_dirty = False

        self.url_input = QLineEdit()
        self.output_input = QLineEdit()
        self.paste_button = QPushButton()
        self.browse_button = QPushButton()
        self.add_button = QPushButton()
        self.start_button = QPushButton()
        self.stop_button = QPushButton()
        self.resume_button = QPushButton()
        self.remove_button = QPushButton()
        self.clear_button = QPushButton()
        self.item_settings_button = QPushButton()
        self.save_button = QPushButton()
        self.load_button = QPushButton()
        self.search_button = QPushButton()
        self.log_toggle_button = QPushButton()
        self.reset_button = QPushButton()
        self.personalize_button = QPushButton()
        self.home_button = QPushButton()
        self.view_toggle_button = QPushButton()
        self.language_combo = QComboBox()
        self.reading_combo = QComboBox()
        self.output_combo = QComboBox()
        self.format_combo = QComboBox()
        self.keep_images = QCheckBox()
        self.threads = QSpinBox()
        self.delay = QDoubleSpinBox()
        self.tree = QTreeWidget()
        self.progress = QProgressBar()
        self.status_label = QLabel()
        self.log_view = QTextEdit()
        self.title_label = QLabel()
        self.input_group = QGroupBox()
        self.defaults_group = QGroupBox()
        self.hero_label = QLabel()

        self._build_ui()
        self._load_settings()
        self._connect_signals()
        self._install_shortcuts()
        self.retranslate_ui()
        apply_theme(QApplication.instance(), self.theme)
        self.refresh_tree()
        self.resize(1280, 820)
        QTimer.singleShot(0, self._log_startup_state)

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

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.setContentsMargins(0, 0, 0, 0)
        self.title_label.setObjectName("Title")
        self.home_button.setObjectName("HomeButton")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.home_button, 0, Qt.AlignmentFlag.AlignLeft)
        top.setContentsMargins(0, 0, 0, 0)
        top.addLayout(title_box, 1)
        top.addWidget(self.view_toggle_button)
        top.addWidget(self.language_combo)
        top.addWidget(self.personalize_button)
        outer.addLayout(top)

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
        self.downloader_page = QWidget()
        downloader_layout = QVBoxLayout(self.downloader_page)
        downloader_layout.setContentsMargins(0, 0, 0, 0)
        downloader_layout.setSpacing(6)
        downloader_layout.addWidget(self.input_group)

        self.defaults_group.setObjectName("Panel")
        defaults_layout = QHBoxLayout(self.defaults_group)
        defaults_layout.setContentsMargins(10, 8, 10, 8)
        defaults_layout.setSpacing(6)
        self._defaults_reading_label = QLabel()
        self._defaults_output_label = QLabel()
        self._defaults_format_label = QLabel()
        self._defaults_threads_label = QLabel()
        self._defaults_delay_label = QLabel()
        defaults_layout.addWidget(self._defaults_reading_label, 0)
        defaults_layout.addWidget(self.reading_combo, 1)
        defaults_layout.addWidget(self._defaults_output_label, 0)
        defaults_layout.addWidget(self.output_combo, 1)
        defaults_layout.addWidget(self._defaults_format_label, 0)
        defaults_layout.addWidget(self.format_combo, 1)
        defaults_layout.addWidget(self.keep_images)
        defaults_layout.addWidget(self._defaults_threads_label, 0)
        defaults_layout.addWidget(self.threads)
        defaults_layout.addWidget(self._defaults_delay_label, 0)
        defaults_layout.addWidget(self.delay)
        downloader_layout.addWidget(self.defaults_group)

        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(0, 0, 0, 0)
        action_bar.setSpacing(6)
        for button in (
            self.start_button,
            self.stop_button,
            self.resume_button,
            self.item_settings_button,
            self.remove_button,
            self.clear_button,
            self.search_button,
            self.save_button,
            self.load_button,
            self.log_toggle_button,
            self.reset_button,
        ):
            action_bar.addWidget(button)
        downloader_layout.addLayout(action_bar)

        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnCount(6)
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
        queue_layout.setSpacing(6)
        self.hero_label.setObjectName("HeroPanel")
        self.hero_label.setWordWrap(True)
        self.hero_label.setMinimumHeight(94)
        queue_layout.addWidget(self.hero_label)
        queue_layout.addWidget(self.tree)
        self.splitter.addWidget(queue_panel)
        self.splitter.addWidget(self.bottom_panel)
        self.splitter.setSizes([680, 160])
        downloader_layout.addWidget(self.splitter, 1)

        self.library_page = QWidget()
        library_page_layout = QVBoxLayout(self.library_page)
        library_page_layout.setContentsMargins(0, 0, 0, 0)
        library_page_layout.setSpacing(10)
        self.library_banner = QLabel("MangoDango")
        self.library_banner.setObjectName("NetflixBanner")
        self.library_banner.setWordWrap(True)
        library_page_layout.addWidget(self.library_banner)
        self.library_scroll = QScrollArea()
        self.library_scroll.setWidgetResizable(True)
        self.library_scroll.setObjectName("LibraryScroll")
        self.library_content = QWidget()
        self.library_content.setObjectName("LibraryContent")
        self.library_layout = QVBoxLayout(self.library_content)
        self.library_layout.setContentsMargins(14, 14, 14, 14)
        self.library_layout.setSpacing(14)
        self.library_scroll.setWidget(self.library_content)
        library_page_layout.addWidget(self.library_scroll, 1)

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.library_page)
        self.view_stack.addWidget(self.downloader_page)
        outer.addWidget(self.view_stack, 1)

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
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_queue)
        self.item_settings_button.clicked.connect(self.open_item_settings)
        self.save_button.clicked.connect(self.save_queue)
        self.load_button.clicked.connect(self.load_queue)
        self.search_button.clicked.connect(self.search_queue)
        self.log_toggle_button.clicked.connect(self.toggle_log)
        self.reset_button.clicked.connect(self.reset_application_data)
        self.personalize_button.clicked.connect(self.open_preferences)
        self.view_toggle_button.clicked.connect(self.toggle_main_view)
        self.home_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://weebcentral.com/")))
        self.language_combo.currentIndexChanged.connect(self.on_language_changed)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.itemSelectionChanged.connect(self._update_hero_panel)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self.output_input.editingFinished.connect(self._save_settings)
        for combo in (self.reading_combo, self.output_combo, self.format_combo):
            combo.currentIndexChanged.connect(lambda _index=0: self._save_settings())
        self.keep_images.stateChanged.connect(lambda _state=0: self._save_settings())
        self.threads.valueChanged.connect(lambda _value=0: self._save_settings())
        self.delay.valueChanged.connect(lambda _value=0.0: self._save_settings())

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
        self._set_combo_value_later = {
            "reading": str(self.settings.value("defaults/reading_style", "long_strip") or "long_strip"),
            "output": str(self.settings.value("defaults/output_mode", "images") or "images"),
            "format": str(self.settings.value("defaults/image_format", "original") or "original"),
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
        self.settings.setValue("defaults/image_threads", self.threads.value())
        self.settings.setValue("defaults/request_delay", self.delay.value())
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

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.tr("window_title"))
        self.title_label.setText(self.tr("app_title"))
        self.home_button.setText(self.tr("home_weebcentral"))
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
        self.remove_button.setText(self.tr("remove"))
        self.clear_button.setText(self.tr("clear"))
        self.item_settings_button.setText(self.tr("item_settings"))
        self.save_button.setText(self.tr("save_queue"))
        self.load_button.setText(self.tr("load_queue"))
        self.search_button.setText(self.tr("search"))
        self.log_toggle_button.setText(self.tr("toggle_log_hide" if self._log_visible else "toggle_log_show"))
        self.reset_button.setText(self.tr("reset"))
        self.personalize_button.setText(self.tr("personalize"))
        self.view_toggle_button.setText(self.tr("switch_to_downloader" if self._library_view_active else "switch_to_library"))
        self._defaults_reading_label.setText(self.tr("reading_style"))
        self._defaults_output_label.setText(self.tr("output_mode"))
        self._defaults_format_label.setText(self.tr("image_format"))
        self._defaults_threads_label.setText(self.tr("image_threads"))
        self._defaults_delay_label.setText(self.tr("request_delay"))
        self.keep_images.setText(self.tr("keep_images"))
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
        ])
        self._update_hero_panel()
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
        self.resolve_worker = ResolveWorker(urls, self.default_item_settings(), self.language, self)
        self.resolve_worker.log_message.connect(self.append_log)
        self.resolve_worker.resolved.connect(self.on_manga_resolved)
        self.resolve_worker.failed.connect(lambda _url, error: self.append_log(error))
        self.resolve_worker.finished_signal.connect(self.on_resolve_finished)
        self.resolve_worker.start()

    def set_busy(self, busy: bool, resolving: bool = False) -> None:
        self.add_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        self.resume_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy and not resolving)
        for widget in (self.item_settings_button, self.remove_button, self.clear_button, self.save_button, self.load_button, self.reset_button):
            widget.setEnabled(not busy)
        self.status_label.setText(self.tr("status_resolving" if resolving else "status_running") if busy else self.tr("ready"))

    @Slot(object)
    def on_manga_resolved(self, manga: MangaEntry) -> None:
        self._push_undo()
        self._scan_downloaded_chapters([manga])
        existing = self.find_manga_by_title_or_url(manga.title, manga.url)
        if existing:
            added = existing.merge_chapters(manga.chapters)
            existing.status = "ready"
            self.append_log(self.tr("log_merged_manga", title=existing.title, count=added))
        else:
            if manga.status not in {"done", "warning"}:
                manga.status = "ready"
            for chapter in manga.chapters:
                if chapter.status != "done":
                    chapter.status = "pending"
            self.mangas.append(manga)
            self.append_log(self.tr("log_added_manga", title=manga.title, count=len(manga.chapters)))
        self.refresh_tree()

    def on_resolve_finished(self) -> None:
        self.set_busy(False)
        self.status_label.setText(self.tr("resolve_finished"))
        self.url_input.clear()
        self.resolve_worker = None

    def find_manga_by_title_or_url(self, title: str, url: str) -> MangaEntry | None:
        for manga in self.mangas:
            if manga.url == url or manga.title.casefold() == title.casefold():
                return manga
        return None

    def _schedule_refresh_tree(self) -> None:
        QTimer.singleShot(0, self.refresh_tree)

    def refresh_tree(self) -> None:
        if self._library_view_active:
            self._tree_dirty = True
            self.refresh_library_view()
            self._update_hero_panel()
            return
        expanded_ids: set[str] = set()
        if not self._building_tree:
            for index in range(self.tree.topLevelItemCount()):
                top_item = self.tree.topLevelItem(index)
                if top_item and top_item.isExpanded():
                    expanded_ids.add(str(top_item.data(0, ROLE_MANGA_ID) or ""))

        self._building_tree = True
        self.tree.clear()
        for manga in self.mangas:
            parent = QTreeWidgetItem(self.tree)
            self._setup_item(parent, "manga", manga.item_id, "")
            self._apply_item_values(parent, manga.title, manga.url, manga.settings, manga.status, manga.enabled)
            self._install_row_setting_widgets(parent, manga, None)
            for chapter in manga.chapters:
                child = QTreeWidgetItem(parent)
                self._setup_item(child, "chapter", manga.item_id, chapter.item_id)
                self._apply_item_values(child, chapter.title, chapter.url, chapter.settings, chapter.status, chapter.enabled)
                self._install_row_setting_widgets(child, manga, chapter)
            parent.setExpanded(manga.item_id in expanded_ids or not expanded_ids)
        for col in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(col)
        self.tree.setColumnWidth(2, max(self.tree.columnWidth(2), 170))
        self.tree.setColumnWidth(3, max(self.tree.columnWidth(3), 190))
        self.tree.setColumnWidth(4, max(self.tree.columnWidth(4), 150))
        self._building_tree = False
        self._tree_dirty = False
        self._update_hero_panel()

    def _set_main_view(self, library_active: bool, *, refresh: bool = True) -> None:
        self._library_view_active = library_active
        self.view_stack.setCurrentIndex(0 if library_active else 1)
        self.view_toggle_button.setText(self.tr("switch_to_downloader" if library_active else "switch_to_library"))
        if not refresh:
            return
        if library_active:
            self.refresh_library_view()
            self._update_hero_panel()
        elif self._tree_dirty:
            self.refresh_tree()

    def toggle_main_view(self) -> None:
        self._set_main_view(not self._library_view_active)

    def _clear_library_layout(self) -> None:
        while self.library_layout.count():
            item = self.library_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def refresh_library_view(self) -> None:
        if not hasattr(self, "library_layout"):
            return
        self._clear_library_layout()
        if not self.mangas:
            empty = QLabel(self.tr("library_empty"))
            empty.setObjectName("LibraryEmpty")
            empty.setWordWrap(True)
            self.library_layout.addWidget(empty)
            self.library_layout.addStretch(1)
            return
        for manga in self.mangas:
            card = QFrame()
            card.setObjectName("MangaCard")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            layout = QHBoxLayout(card)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(14)

            poster = QLabel()
            poster.setObjectName("PosterPlaceholder")
            poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
            poster.setFixedSize(94, 132)
            poster.setToolTip(manga.cover_url or self.tr("hero_no_cover"))
            pixmap = self._local_cover_pixmap(manga, poster.size())
            if pixmap and not pixmap.isNull():
                poster.setPixmap(pixmap)
            else:
                poster.setText("M")
            layout.addWidget(poster)

            info = QVBoxLayout()
            title = QLabel(manga.title)
            title.setObjectName("CardTitle")
            title.setWordWrap(True)
            downloaded = sum(1 for chapter in manga.chapters if chapter.local.image_count or chapter.local.has_cbz or chapter.local.has_pdf or chapter.status == "done")
            meta = QLabel(self.tr("library_card_meta", downloaded=downloaded, chapters=len(manga.chapters), status=self._status_text(manga.status)))
            meta.setObjectName("CardMeta")
            meta.setWordWrap(True)
            description = QLabel(manga.description or self.tr("hero_no_description"))
            description.setObjectName("CardDescription")
            description.setWordWrap(True)
            info.addWidget(title)
            info.addWidget(meta)
            info.addWidget(description)
            info.addStretch(1)
            layout.addLayout(info, 1)
            card.mousePressEvent = lambda _event, selected=manga: self.select_manga_in_library(selected)
            self.library_layout.addWidget(card)
        self.library_layout.addStretch(1)

    def select_manga_in_library(self, manga: MangaEntry) -> None:
        self._update_hero_panel(manga)


    def _local_cover_pixmap(self, manga: MangaEntry, size) -> QPixmap | None:
        output_dir = self.output_input.text().strip() or DEFAULT_OUTPUT_DIR
        manga_dir = Path(output_dir) / sanitize_filename(manga.title, "Manga")
        for name in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp"):
            path = manga_dir / name
            if not path.exists() or path.stat().st_size <= 0:
                continue
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        return None

    def _setup_item(self, item: QTreeWidgetItem, kind: str, manga_id: str, chapter_id: str) -> None:
        item.setData(0, ROLE_KIND, kind)
        item.setData(0, ROLE_MANGA_ID, manga_id)
        item.setData(0, ROLE_CHAPTER_ID, chapter_id)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

    def _apply_item_values(self, item: QTreeWidgetItem, title: str, url: str, settings: ItemSettings, status: str, enabled: bool) -> None:
        marker = "✓ " if enabled else "  "
        item.setText(0, marker + title)
        item.setText(1, url)
        # Columns 2–4 use real inline comboboxes. Keeping text underneath those
        # widgets causes doubled/bold-looking text on several Qt themes.
        item.setText(2, "")
        item.setText(3, "")
        item.setText(4, "")
        item.setText(5, self._status_text(status))
        item.setToolTip(0, self.tr("toggle_enabled_hint"))
        item.setToolTip(1, url)

    def _install_row_setting_widgets(self, item: QTreeWidgetItem, manga: MangaEntry, chapter: ChapterEntry | None) -> None:
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

    def open_item_url(self, item: QTreeWidgetItem | None = None, _column: int = 0) -> None:
        item = item or self.tree.currentItem()
        if not item:
            return
        url = item.text(1).strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))

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
        answer = QMessageBox.question(self, self.tr("confirm_clear_title"), self.tr("confirm_clear_text"))
        if answer == QMessageBox.StandardButton.Yes:
            count = sum(1 + len(manga.chapters) for manga in self.mangas)
            self._push_undo()
            self.mangas.clear()
            self.refresh_tree()
            self.append_log(self.tr("log_queue_cleared", count=count))

    def save_queue(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("save_queue"), self.tr("queue_default_file"), self.tr("queue_filter"))
        if not path:
            return
        data = {
            "version": QUEUE_FILE_VERSION,
            "output_dir": self.output_input.text().strip(),
            "mangas": [manga.to_dict() for manga in self.mangas],
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_label.setText(self.tr("saved"))
        self.append_log(self.tr("log_queue_saved", path=path, count=len(self.mangas)))

    def load_queue(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("load_queue"), "", self.tr("queue_filter"))
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError(self.tr("error_invalid_queue"))
            mangas_data = data.get("mangas", [])
            if not isinstance(mangas_data, list):
                raise ValueError(self.tr("error_invalid_queue"))
            loaded_mangas = [MangaEntry.from_dict(item) for item in mangas_data if isinstance(item, dict)]
        except Exception as exc:
            QMessageBox.critical(self, self.tr("load_queue"), self.tr("error_load_queue", error=exc))
            self.append_log(self.tr("error_load_queue", error=exc))
            return
        self._push_undo()
        self.output_input.setText(str(data.get("output_dir", self.output_input.text()) or self.output_input.text()))
        self.mangas = loaded_mangas
        self._tree_dirty = True
        self._scan_downloaded_chapters()
        self._set_main_view(True)
        self.status_label.setText(self.tr("loaded"))
        self.append_log(self.tr("log_queue_loaded", path=path, count=len(self.mangas)))

    def search_queue(self) -> None:
        text, ok = QInputDialog.getText(self, self.tr("search_title"), self.tr("search_prompt"))
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

    def _start_download(self, resume: bool) -> None:
        if not self.mangas:
            QMessageBox.information(self, APP_NAME, self.tr("queue_empty"))
            return
        output_dir = self.output_input.text().strip() or DEFAULT_OUTPUT_DIR
        self._save_settings()
        self._scan_downloaded_chapters()
        active_chapters = sum(1 for manga in self.mangas if manga.enabled for chapter in manga.chapters if chapter.enabled and chapter.status != "done")
        self.append_log(self.tr("log_download_resumed" if resume else "log_download_started", count=active_chapters, path=output_dir))
        for manga in self.mangas:
            if not manga.enabled:
                manga.status = "skipped"
                for chapter in manga.chapters:
                    chapter.status = "skipped"
                continue
            manga.status = "pending"
            for chapter in manga.chapters:
                if not chapter.enabled:
                    chapter.status = "skipped"
                elif chapter.status == "done":
                    pass
                else:
                    chapter.status = "pending"
        self.refresh_tree()
        self.set_busy(True)
        self.download_worker = QueueDownloadWorker(self.mangas, output_dir, self.language, self, skip_done=resume)
        self.download_worker.log_message.connect(self.append_log)
        self.download_worker.chapter_status.connect(self.on_chapter_status)
        self.download_worker.manga_status.connect(self.on_manga_status)
        self.download_worker.global_progress.connect(self.on_progress)
        self.download_worker.finished_signal.connect(self.on_download_finished)
        self.download_worker.start()

    def stop_download(self) -> None:
        if self.download_worker:
            self.download_worker.stop()
            self.stop_button.setEnabled(False)
            self.resume_button.setEnabled(True)
            self.status_label.setText(self.tr("status_stopped"))
            self.append_log(self.tr("log_download_stop_requested"))

    @Slot(str, str, str)
    def on_chapter_status(self, manga_id: str, chapter_id: str, status: str) -> None:
        manga = self.get_manga(manga_id)
        if not manga:
            return
        chapter = self.get_chapter(manga, chapter_id)
        if chapter:
            chapter.status = status
        self.refresh_tree()

    @Slot(str, str)
    def on_manga_status(self, manga_id: str, status: str) -> None:
        manga = self.get_manga(manga_id)
        if manga:
            manga.status = status
        self.refresh_tree()

    @Slot(float, str)
    def on_progress(self, value: float, text: str) -> None:
        self.progress.setValue(max(0, min(100, int(value))))
        self.status_label.setText(text)

    @Slot(bool, bool)
    def on_download_finished(self, ok: bool, stopped: bool) -> None:
        self.set_busy(False)
        self.stop_button.setEnabled(False)
        self.download_worker = None
        result_key = "download_stopped" if stopped else ("download_finished" if ok else "download_failed")
        self.status_label.setText(self.tr(result_key))
        self.append_log(self.tr("log_download_result", result=self.tr(result_key)))


    def _scan_downloaded_chapters(self, mangas: list[MangaEntry] | None = None) -> None:
        output_dir = self.output_input.text().strip() or DEFAULT_OUTPUT_DIR
        scanned = mangas or self.mangas
        found = 0
        for manga in scanned:
            for chapter in manga.chapters:
                chapter.local = chapter_output_info(output_dir, manga.title, chapter.title)
                if chapter.local.image_count or chapter.local.has_cbz or chapter.local.has_pdf:
                    found += 1
                    if chapter.status in {"pending", "ready", "failed", "warning"}:
                        chapter.status = "done"
            done = sum(1 for chapter in manga.chapters if chapter.status == "done")
            if done and done == len(manga.chapters):
                manga.status = "done"
            elif done:
                manga.status = "warning"
        if found:
            self.append_log(self.tr("log_local_scan_found", count=found, total=sum(len(m.chapters) for m in scanned)))

    def _update_hero_panel(self) -> None:
        infos = self.selected_item_infos() if hasattr(self, "tree") else []
        manga = infos[0][1] if infos else (self.mangas[0] if self.mangas else None)
        if not manga:
            self.hero_label.setText(self.tr("hero_empty"))
            return
        downloaded = sum(1 for chapter in manga.chapters if chapter.local.image_count or chapter.local.has_cbz or chapter.local.has_pdf or chapter.status == "done")
        cover = manga.cover_url or self.tr("hero_no_cover")
        details = manga.description or self.tr("hero_no_description")
        self.hero_label.setText(self.tr("hero_details", title=manga.title, chapters=len(manga.chapters), downloaded=downloaded, status=self._status_text(manga.status), cover=cover, description=details))

    def append_log(self, message: str) -> None:
        self.log_view.append(str(message))
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
        self._save_settings()
        self.retranslate_ui()
        self.append_log(self.tr("log_language_set", language=language_label(self.language, self.language)))

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

    def reset_application_data(self) -> None:
        answer = QMessageBox.question(self, self.tr("reset_title"), self.tr("reset_confirm"))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.settings.clear()
        self.settings.sync()
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
