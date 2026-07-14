from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import zipfile

from PySide6.QtCore import Qt, QSize, QTime, QTimer, QEvent, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..automation import AutomationSchedule, AutomationSlot, DAY_CHOICES
from ..constants import IMAGE_FORMATS, OUTPUT_MODES, READING_STYLES
from ..models import ChapterEntry, ItemSettings, MangaEntry
from ..reading_state import ReadingStateStore, manga_id_for_path
from .styles import PRESET_ORDER, THEME_PRESETS, ThemeSettings, preset_theme, _colors
from ..scraper import IMAGE_EXTENSIONS, chapter_output_paths, natural_sort_key, sanitize_filename
from ..updater import REPOSITORY_URL, ReleaseInfo, cleanup_download, schedule_update_install
from ..workers import AppUpdateWorker


def localized_question(parent, tr, title: str, text: str, default_yes: bool = False) -> bool:
    """Show a Yes/No question whose button labels follow the app language."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes_button = box.addButton(tr("yes"), QMessageBox.ButtonRole.AcceptRole)
    no_button = box.addButton(tr("no"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes_button if default_yes else no_button)
    box.exec()
    return box.clickedButton() is yes_button


def localized_text_input(parent, tr, title: str, label: str, text: str = "") -> tuple[str, bool]:
    """Show a text input dialog with app-language OK/Cancel buttons."""
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    dialog.setOkButtonText(tr("ok"))
    dialog.setCancelButtonText(tr("cancel"))
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.textValue(), accepted


class ItemSettingsDialog(QDialog):
    def __init__(self, settings: ItemSettings, tr, allow_children: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self.setWindowTitle(self.tr("item_settings"))
        self.reading_combo = QComboBox()
        self.output_combo = QComboBox()
        self.format_combo = QComboBox()
        self.keep_images = QCheckBox(self.tr("keep_images"))
        self.threads = QSpinBox()
        self.threads.setRange(1, 10)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 30)
        self.delay.setDecimals(1)
        self.delay.setSingleStep(0.5)
        self.delay.setSuffix(self.tr("seconds_suffix"))
        self.apply_children = QCheckBox(self.tr("apply_to_children"))
        self.apply_children.setVisible(allow_children)
        self.apply_children.setChecked(allow_children)
        self._fill_combo(self.reading_combo, READING_STYLES, "reading_")
        self._fill_combo(self.output_combo, OUTPUT_MODES, "mode_")
        self._fill_combo(self.format_combo, IMAGE_FORMATS, "format_")
        self._set_combo(self.reading_combo, settings.reading_style)
        self._set_combo(self.output_combo, settings.output_mode)
        self._set_combo(self.format_combo, settings.image_format)
        self.keep_images.setChecked(settings.keep_images)
        self.threads.setValue(settings.image_threads)
        self.delay.setValue(settings.request_delay)
        form = QFormLayout()
        form.addRow(self.tr("reading_style"), self.reading_combo)
        form.addRow(self.tr("output_mode"), self.output_combo)
        form.addRow(self.tr("image_format"), self.format_combo)
        form.addRow("", self.keep_images)
        form.addRow(self.tr("image_threads"), self.threads)
        form.addRow(self.tr("request_delay"), self.delay)
        form.addRow("", self.apply_children)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(self.tr("ok"))
        buttons.button(QDialogButtonBox.Cancel).setText(self.tr("cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _fill_combo(self, combo: QComboBox, values: tuple[str, ...], prefix: str) -> None:
        combo.clear()
        for value in values:
            combo.addItem(self.tr(prefix + value), value)
        combo.setMaxVisibleItems(len(values))
        combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        combo.view().setMinimumHeight((len(values) * 28) + 8)

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def selected_settings(self) -> ItemSettings:
        return ItemSettings(
            reading_style=str(self.reading_combo.currentData()),
            output_mode=str(self.output_combo.currentData()),
            image_format=str(self.format_combo.currentData()),
            keep_images=self.keep_images.isChecked(),
            image_threads=self.threads.value(),
            request_delay=self.delay.value(),
        )

    def should_apply_to_children(self) -> bool:
        return self.apply_children.isVisible() and self.apply_children.isChecked()


class AppSettingsDialog(QDialog):
    """Global defaults, update behaviour and the automation schedule.

    Replaces the old inline "Globale Einstellungen" panel. Opened from the
    "Einstellungen" button next to "Personalisieren".
    """

    def __init__(
        self,
        settings: ItemSettings,
        check_updates_on_startup: bool,
        auto_download_updates: bool,
        automation: AutomationSchedule,
        tr,
        output_dir: str = "",
        manga_list: list[dict] | None = None,
        theme=None,
        custom_themes: list[dict] | None = None,
        mobile_reader_enabled: bool = False,
        mobile_reader_port: int = 8765,
        mobile_reader_host: str = "0.0.0.0",
        mobile_reader_urls: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self._automation = automation.clone()
        self._output_dir = output_dir or ""
        self._manga_list = list(manga_list or [])
        self._requested_manga_action: tuple[str, list[dict]] | None = None
        self._resize_pending = False
        self._app_update_worker: AppUpdateWorker | None = None
        self._downloaded_update_path = ""
        self._quit_after_update_worker = False
        self.setWindowTitle(self.tr("app_settings_title"))
        self.setMinimumWidth(780)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general_tab(settings, check_updates_on_startup, auto_download_updates), self.tr("settings_tab_general"))
        self.tabs.addTab(self._build_manga_list_tab(), self.tr("settings_tab_manga_list"))
        self.appearance_prefs = None
        if theme is not None:
            self.tabs.addTab(self._build_appearance_tab(theme, custom_themes), self.tr("settings_tab_appearance"))
        self.tabs.addTab(self._build_automation_tab(), self.tr("settings_tab_automation"))
        self.tabs.addTab(
            self._build_mobile_reader_tab(
                enabled=mobile_reader_enabled,
                port=mobile_reader_port,
                host=mobile_reader_host,
                urls=list(mobile_reader_urls or []),
            ),
            self.tr("settings_tab_mobile_reader"),
        )
        self.tabs.addTab(self._build_help_tab(), self.tr("settings_tab_help"))

        self.dialog_buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.dialog_buttons.button(QDialogButtonBox.Ok).setText(self.tr("ok"))
        self.dialog_buttons.button(QDialogButtonBox.Cancel).setText(self.tr("cancel"))
        self.dialog_buttons.accepted.connect(self.accept)
        self.dialog_buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(self.tabs)
        layout.addWidget(self.dialog_buttons)

        self._refresh_slots()
        self._sync_automation_enabled()
        self.tabs.currentChanged.connect(self._schedule_resize_for_current_tab)
        for index in range(self.tabs.count()):
            self.tabs.widget(index).installEventFilter(self)
        QTimer.singleShot(0, self._resize_for_current_tab)

    # -- Appearance tab ---------------------------------------------------
    def _build_appearance_tab(self, theme, custom_themes) -> QWidget:
        # Reuse the full PreferencesDialog UI as an embedded widget so the
        # personalisation lives inside Settings instead of a separate button.
        self.appearance_prefs = PreferencesDialog(theme, self.tr, custom_themes, parent=self)
        self.appearance_prefs.setWindowFlags(Qt.WindowType.Widget)
        self.appearance_prefs.installEventFilter(self)
        # The embedded copy must not carry its own OK/Cancel/close row; the
        # settings dialog's buttons drive accept/reject for everything.
        for widget in (self.appearance_prefs.save_button, self.appearance_prefs.cancel_button):
            widget.setVisible(False)

        self.appearance_scroll = QScrollArea()
        self.appearance_scroll.setWidgetResizable(True)
        self.appearance_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.appearance_scroll.setWidget(self.appearance_prefs)
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.addWidget(self.appearance_scroll)
        self._appearance_tab = tab
        return tab

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._schedule_resize_for_current_tab()

    def eventFilter(self, watched, event) -> bool:
        current_page = self.tabs.currentWidget()
        appearance_content = getattr(self, "appearance_prefs", None)
        if (
            event.type() == QEvent.Type.LayoutRequest
            and (watched is current_page or watched is appearance_content)
        ):
            self._schedule_resize_for_current_tab()
        return super().eventFilter(watched, event)

    def _schedule_resize_for_current_tab(self, _index: int | None = None) -> None:
        if self._resize_pending:
            return
        self._resize_pending = True
        QTimer.singleShot(0, self._resize_for_current_tab)

    def _current_page_size_hint(self) -> QSize:
        page = self.tabs.currentWidget()
        if page is None:
            return QSize(640, 420)

        hint = page.sizeHint().expandedTo(page.minimumSizeHint())
        if page is getattr(self, "_appearance_tab", None) and self.appearance_prefs is not None:
            content_hint = self.appearance_prefs.sizeHint().expandedTo(self.appearance_prefs.minimumSizeHint())
            hint = hint.expandedTo(QSize(content_hint.width() + 12, content_hint.height() + 12))

        # The tree itself is intentionally scrollable, but the surrounding
        # controls should never be clipped when switching to this tab.
        if page is getattr(self, "_manga_list_tab", None):
            hint = hint.expandedTo(QSize(660, 410))
        if page is getattr(self, "_mobile_reader_tab", None):
            hint = hint.expandedTo(QSize(640, 360))
        if page is getattr(self, "_help_tab", None):
            hint = hint.expandedTo(QSize(620, 360))
        return hint

    def _resize_for_current_tab(self) -> None:
        self._resize_pending = False
        page_hint = self._current_page_size_hint()
        margins = self.layout().contentsMargins()
        spacing = max(0, self.layout().spacing())
        tab_bar_height = self.tabs.tabBar().sizeHint().height()
        button_hint = self.dialog_buttons.sizeHint()

        target_width = page_hint.width() + margins.left() + margins.right() + 14
        target_height = (
            page_hint.height()
            + tab_bar_height
            + button_hint.height()
            + margins.top()
            + margins.bottom()
            + spacing
            + 14
        )

        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            target_width = min(target_width, max(780, int(available.width() * 0.94)))
            target_height = min(target_height, max(420, int(available.height() * 0.94)))
        else:
            available = None

        old_center = self.frameGeometry().center()
        tab_bar_width = self.tabs.tabBar().sizeHint().width() + margins.left() + margins.right() + 28
        target_size = QSize(max(780, target_width, tab_bar_width), max(420, target_height))
        if self.size() != target_size:
            self.resize(target_size)

        if self.isVisible() and available is not None:
            geometry = self.frameGeometry()
            geometry.moveCenter(old_center)
            x = min(max(geometry.x(), available.left()), available.right() - geometry.width() + 1)
            y = min(max(geometry.y(), available.top()), available.bottom() - geometry.height() + 1)
            self.move(x, y)

    def selected_theme(self):
        return self.appearance_prefs.selected_theme() if self.appearance_prefs else None

    def selected_template_name(self) -> str:
        return self.appearance_prefs.selected_template_name() if self.appearance_prefs else ""

    def selected_custom_themes(self) -> list[dict]:
        return self.appearance_prefs.selected_custom_themes() if self.appearance_prefs else []

    # -- General tab ------------------------------------------------------
    def _build_general_tab(self, settings: ItemSettings, check_updates: bool, auto_download: bool) -> QWidget:
        self.reading_combo = QComboBox()
        self.output_combo = QComboBox()
        self.format_combo = QComboBox()
        self._fill_combo(self.reading_combo, READING_STYLES, "reading_")
        self._fill_combo(self.output_combo, OUTPUT_MODES, "mode_")
        self._fill_combo(self.format_combo, IMAGE_FORMATS, "format_")
        self._set_combo(self.reading_combo, settings.reading_style)
        self._set_combo(self.output_combo, settings.output_mode)
        self._set_combo(self.format_combo, settings.image_format)

        self.output_dir_edit = QLineEdit(self._output_dir)
        self.output_dir_edit.setPlaceholderText(self.tr("output_dir_placeholder"))
        browse = QPushButton(self.tr("choose_folder"))
        browse.clicked.connect(self._choose_output_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.output_dir_edit, 1)
        dir_row.addWidget(browse)

        self.keep_images = QCheckBox(self.tr("keep_images"))
        self.keep_images.setChecked(settings.keep_images)
        self.check_updates_on_startup = QCheckBox(self.tr("check_updates_on_startup"))
        self.check_updates_on_startup.setToolTip(self.tr("check_updates_on_startup_hint"))
        self.check_updates_on_startup.setChecked(check_updates)
        self.auto_download_updates = QCheckBox(self.tr("auto_download_updates"))
        self.auto_download_updates.setToolTip(self.tr("auto_download_updates_hint"))
        self.auto_download_updates.setChecked(auto_download)

        self.threads = QSpinBox()
        self.threads.setRange(1, 10)
        self.threads.setValue(settings.image_threads)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 30)
        self.delay.setDecimals(1)
        self.delay.setSingleStep(0.5)
        self.delay.setSuffix(self.tr("seconds_suffix"))
        self.delay.setValue(settings.request_delay)

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow(self.tr("manga_directory"), dir_row)
        form.addRow(self.tr("reading_style"), self.reading_combo)
        form.addRow(self.tr("output_mode"), self.output_combo)
        form.addRow(self.tr("image_format"), self.format_combo)
        form.addRow("", self.keep_images)
        form.addRow("", self.check_updates_on_startup)
        form.addRow("", self.auto_download_updates)
        form.addRow(self.tr("image_threads"), self.threads)
        form.addRow(self.tr("request_delay"), self.delay)

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addLayout(form)
        outer.addStretch(1)
        return tab

    def _choose_output_dir(self) -> None:
        start = self.output_dir_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, self.tr("choose_folder"), start)
        if folder:
            self.output_dir_edit.setText(folder)

    # -- Manga list tab ---------------------------------------------------
    def _build_manga_list_tab(self) -> QWidget:
        hint = QLabel(self.tr("manga_list_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")

        self.manga_tree = QTreeWidget()
        self.manga_tree.setColumnCount(3)
        self.manga_tree.setHeaderLabels([
            self.tr("manga_list_col_name"),
            self.tr("manga_list_col_check"),
            self.tr("manga_list_col_download"),
        ])
        self.manga_tree.setRootIsDecorated(False)
        self.manga_tree.setAlternatingRowColors(True)
        header = self.manga_tree.header()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._toggle_manga_column_from_header)
        self.manga_tree.headerItem().setToolTip(1, self.tr("manga_list_header_toggle_hint"))
        self.manga_tree.headerItem().setToolTip(2, self.tr("manga_list_header_toggle_hint"))
        try:
            from PySide6.QtWidgets import QHeaderView
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        except Exception:
            pass

        for info in self._manga_list:
            item = QTreeWidgetItem([str(info.get("title", "")), "", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, str(info.get("path", "")))
            item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, str(info.get("url", "")))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            item.setCheckState(1, Qt.CheckState.Checked if info.get("check_updates", True) else Qt.CheckState.Unchecked)
            item.setCheckState(2, Qt.CheckState.Checked if info.get("auto_download", True) else Qt.CheckState.Unchecked)
            self.manga_tree.addTopLevelItem(item)

        check_now = QPushButton(self.tr("manga_list_check_now"))
        download_now = QPushButton(self.tr("manga_list_download_now"))
        check_now.clicked.connect(lambda: self._request_manga_action("check", 1))
        download_now.clicked.connect(lambda: self._request_manga_action("download", 2))

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(check_now)
        buttons_row.addWidget(download_now)

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        outer.addWidget(hint)
        outer.addWidget(self.manga_tree, 1)
        outer.addLayout(buttons_row)
        if not self._manga_list:
            empty = QLabel(self.tr("manga_list_empty"))
            empty.setObjectName("Muted")
            outer.addWidget(empty)
        self._manga_list_tab = tab
        return tab

    def _set_all_checks(self, column: int, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.manga_tree.topLevelItemCount()):
            self.manga_tree.topLevelItem(index).setCheckState(column, state)

    def _toggle_manga_column_from_header(self, column: int) -> None:
        if column not in (1, 2) or self.manga_tree.topLevelItemCount() <= 0:
            return
        all_checked = all(
            self.manga_tree.topLevelItem(index).checkState(column) == Qt.CheckState.Checked
            for index in range(self.manga_tree.topLevelItemCount())
        )
        self._set_all_checks(column, not all_checked)

    def _mangas_checked_in_column(self, column: int) -> list[dict]:
        result: list[dict] = []
        for index in range(self.manga_tree.topLevelItemCount()):
            item = self.manga_tree.topLevelItem(index)
            if item.checkState(column) != Qt.CheckState.Checked:
                continue
            result.append({
                "title": item.text(0),
                "path": str(item.data(0, Qt.ItemDataRole.UserRole) or ""),
                "url": str(item.data(0, int(Qt.ItemDataRole.UserRole) + 1) or ""),
            })
        return result

    def _request_manga_action(self, action: str, column: int) -> None:
        selected = self._mangas_checked_in_column(column)
        if not selected:
            QMessageBox.information(
                self,
                self.tr("settings_tab_manga_list"),
                self.tr("manga_list_action_no_selection"),
            )
            return
        self._requested_manga_action = (action, selected)
        self.accept()

    # -- Automation tab ---------------------------------------------------
    def _build_automation_tab(self) -> QWidget:
        self.automation_enabled = QCheckBox(self.tr("automation_enable"))
        self.automation_enabled.setChecked(self._automation.enabled)
        self.automation_enabled.stateChanged.connect(self._sync_automation_enabled)

        hint = QLabel(self.tr("automation_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")

        self.automation_group = QGroupBox(self.tr("automation_schedule"))
        group_layout = QVBoxLayout(self.automation_group)
        group_layout.setSpacing(8)

        add_row = QHBoxLayout()
        self.day_combo = QComboBox()
        for code in DAY_CHOICES:
            self.day_combo.addItem(self.tr("weekday_" + code), code)
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(QTime(8, 0))
        self.add_slot_button = QPushButton(self.tr("automation_add"))
        self.add_slot_button.clicked.connect(self._add_slot)
        add_row.addWidget(QLabel(self.tr("automation_day")))
        add_row.addWidget(self.day_combo, 1)
        add_row.addWidget(QLabel(self.tr("automation_time")))
        add_row.addWidget(self.time_edit)
        add_row.addWidget(self.add_slot_button)

        self.slots_list = QListWidget()
        self.slots_list.setMinimumHeight(150)

        self.remove_slot_button = QPushButton(self.tr("automation_remove"))
        self.remove_slot_button.clicked.connect(self._remove_slot)
        remove_row = QHBoxLayout()
        remove_row.addStretch(1)
        remove_row.addWidget(self.remove_slot_button)

        group_layout.addLayout(add_row)
        group_layout.addWidget(self.slots_list, 1)
        group_layout.addLayout(remove_row)

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)
        outer.addWidget(self.automation_enabled)
        outer.addWidget(hint)
        outer.addWidget(self.automation_group, 1)
        return tab

    def _sync_automation_enabled(self) -> None:
        self.automation_group.setEnabled(self.automation_enabled.isChecked())

    def _slot_label(self, slot: AutomationSlot) -> str:
        return self.tr("automation_slot_label", day=self.tr("weekday_" + slot.day), time=slot.time)

    def _refresh_slots(self) -> None:
        self.slots_list.clear()
        for slot in self._automation.slots:
            item = QListWidgetItem(self._slot_label(slot))
            item.setData(Qt.ItemDataRole.UserRole, (slot.day, slot.time))
            self.slots_list.addItem(item)

    def _add_slot(self) -> None:
        day = str(self.day_combo.currentData() or "daily")
        time_text = self.time_edit.time().toString("HH:mm")
        if self._automation.add_slot(AutomationSlot(day=day, time=time_text)):
            self._refresh_slots()

    def _remove_slot(self) -> None:
        item = self.slots_list.currentItem()
        if item is None:
            return
        day, time_text = item.data(Qt.ItemDataRole.UserRole)
        self._automation.slots = [s for s in self._automation.slots if not (s.day == day and s.time == time_text)]
        self._refresh_slots()

    # -- Mobile reader tab ------------------------------------------------
    def _build_mobile_reader_tab(self, enabled: bool, port: int, host: str, urls: list[str]) -> QWidget:
        self.mobile_reader_enable = QCheckBox(self.tr("mobile_reader_enable"))
        self.mobile_reader_enable.setChecked(bool(enabled))
        self.mobile_reader_enable.setToolTip(self.tr("mobile_reader_enable_hint"))

        self.mobile_reader_port_spin = QSpinBox()
        self.mobile_reader_port_spin.setRange(1024, 65535)
        self.mobile_reader_port_spin.setValue(max(1024, min(65535, int(port or 8765))))
        self.mobile_reader_port_spin.setToolTip(self.tr("mobile_reader_port_hint"))

        self.mobile_reader_host_edit = QLineEdit(str(host or "0.0.0.0"))
        self.mobile_reader_host_edit.setPlaceholderText("0.0.0.0")
        self.mobile_reader_host_edit.setToolTip(self.tr("mobile_reader_host_hint"))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow(self.tr("mobile_reader_host"), self.mobile_reader_host_edit)
        form.addRow(self.tr("mobile_reader_port"), self.mobile_reader_port_spin)

        intro = QLabel(self.tr("mobile_reader_description"))
        intro.setWordWrap(True)

        security = QLabel(self.tr("mobile_reader_security_hint"))
        security.setWordWrap(True)
        security.setObjectName("SettingsHint")

        hostname_hint = QLabel(self.tr("mobile_reader_hostname_hint"))
        hostname_hint.setWordWrap(True)
        hostname_hint.setObjectName("SettingsHint")

        address_title = QLabel(self.tr("mobile_reader_addresses"))
        address_title.setStyleSheet("font-weight: 600;")

        self.mobile_reader_addresses_label = QLabel()
        self.mobile_reader_addresses_label.setWordWrap(True)
        self.mobile_reader_addresses_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.mobile_reader_addresses_label.setObjectName("SettingsHint")
        self.mobile_reader_addresses_label.setText(
            "\n".join(urls) if urls else self.tr("mobile_reader_no_address")
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(intro)
        layout.addWidget(self.mobile_reader_enable)
        layout.addLayout(form)
        layout.addWidget(address_title)
        layout.addWidget(self.mobile_reader_addresses_label)
        layout.addWidget(hostname_hint)
        layout.addWidget(security)
        layout.addStretch(1)

        tab = QWidget()
        tab.setLayout(layout)
        self._mobile_reader_tab = tab
        return tab

    def mobile_reader_enabled(self) -> bool:
        return self.mobile_reader_enable.isChecked()

    def mobile_reader_port(self) -> int:
        return self.mobile_reader_port_spin.value()

    def mobile_reader_host(self) -> str:
        return self.mobile_reader_host_edit.text().strip() or "0.0.0.0"

    # -- Help / self-update tab -------------------------------------------
    def _build_help_tab(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QGridLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setHorizontalSpacing(14)
        card_layout.setVerticalSpacing(9)

        title = QLabel("MangoDango")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(max(title_font.pointSize() + 4, 14))
        title.setFont(title_font)
        card_layout.addWidget(title, 0, 0, 1, 2)

        rows = (
            ("help_created_by", "Testatost"),
            ("help_license", "MIT"),
            ("help_current_version", __version__),
        )
        for row, (label_key, value) in enumerate(rows, start=1):
            label = QLabel(self.tr(label_key))
            label.setObjectName("Muted")
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card_layout.addWidget(label, row, 0)
            card_layout.addWidget(value_label, row, 1)

        repo_label = QLabel(self.tr("help_repository"))
        repo_label.setObjectName("Muted")
        self.repo_link = QLabel(f'<a href="{REPOSITORY_URL}">{REPOSITORY_URL}</a>')
        self.repo_link.setTextFormat(Qt.TextFormat.RichText)
        self.repo_link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.repo_link.setOpenExternalLinks(False)
        self.repo_link.linkActivated.connect(self._open_help_link)
        card_layout.addWidget(repo_label, 4, 0)
        card_layout.addWidget(self.repo_link, 4, 1)
        card_layout.setColumnStretch(1, 1)

        self.app_update_status = QLabel(self.tr("help_update_hint"))
        self.app_update_status.setObjectName("Muted")
        self.app_update_status.setWordWrap(True)

        self.app_update_progress = QProgressBar()
        self.app_update_progress.setRange(0, 100)
        self.app_update_progress.setValue(0)
        self.app_update_progress.setTextVisible(True)
        self.app_update_progress.setVisible(False)

        self.app_update_button = QPushButton(self.tr("help_update_button"))
        self.app_update_button.clicked.connect(self._start_app_update)

        update_row = QHBoxLayout()
        update_row.addStretch(1)
        update_row.addWidget(self.app_update_button)

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)
        outer.addWidget(card)
        outer.addWidget(self.app_update_status)
        outer.addWidget(self.app_update_progress)
        outer.addLayout(update_row)
        outer.addStretch(1)
        self._help_tab = tab
        return tab

    def _open_help_link(self, url: str) -> None:
        parent = self.parent()
        opener = getattr(parent, "open_external_url", None)
        if callable(opener):
            opener(url)
            return
        QDesktopServices.openUrl(QUrl.fromUserInput(url))

    def _start_app_update(self) -> None:
        if self._app_update_worker is not None and self._app_update_worker.isRunning():
            return
        self.app_update_button.setEnabled(False)
        self.dialog_buttons.setEnabled(False)
        self.app_update_progress.setValue(0)
        self.app_update_progress.setVisible(False)
        self.app_update_status.setText(self.tr("help_update_checking"))

        owner = self.parent() or self
        worker = AppUpdateWorker(owner)
        self._app_update_worker = worker
        worker.status_changed.connect(self._on_app_update_status)
        worker.progress_changed.connect(self._on_app_update_progress)
        worker.no_update.connect(self._on_app_update_no_update)
        worker.failed.connect(self._on_app_update_failed)
        worker.update_ready.connect(self._on_app_update_ready)
        worker.finished.connect(self._on_app_update_worker_finished)
        worker.start()

    def _on_app_update_status(self, status: str) -> None:
        if status == "downloading":
            self.app_update_progress.setVisible(True)
            self.app_update_status.setText(self.tr("help_update_downloading"))
        else:
            self.app_update_status.setText(self.tr("help_update_checking"))

    def _on_app_update_progress(self, value: int) -> None:
        self.app_update_progress.setVisible(True)
        self.app_update_progress.setValue(max(0, min(100, int(value))))

    def _on_app_update_no_update(self, version: str) -> None:
        self.app_update_progress.setVisible(False)
        self.app_update_status.setText(self.tr("help_update_latest", version=version or __version__))

    def _on_app_update_failed(self, error: str) -> None:
        self.app_update_progress.setVisible(False)
        reason = self.tr(error) if str(error).startswith("update_error_") else self.tr("update_error_generic")
        self.app_update_status.setText(self.tr("help_update_failed", error=reason))
        QMessageBox.warning(
            self,
            self.tr("help_update_title"),
            self.tr("help_update_failed", error=reason),
        )

    def _on_app_update_ready(self, release: ReleaseInfo, package_path: str) -> None:
        self._downloaded_update_path = package_path
        self.app_update_progress.setVisible(True)
        self.app_update_progress.setValue(100)
        self.app_update_status.setText(self.tr("help_update_ready", version=release.version))

        if not localized_question(
            self,
            self.tr,
            self.tr("help_update_title"),
            self.tr("help_update_restart_question", version=release.version),
            default_yes=True,
        ):
            cleanup_download(package_path)
            self._downloaded_update_path = ""
            return

        try:
            schedule_update_install(release, package_path)
        except Exception:
            cleanup_download(package_path)
            self._downloaded_update_path = ""
            reason = self.tr("update_error_generic")
            self.app_update_status.setText(self.tr("help_update_install_failed", error=reason))
            QMessageBox.warning(
                self,
                self.tr("help_update_title"),
                self.tr("help_update_install_failed", error=reason),
            )
            return

        cleanup_download(package_path)
        self._downloaded_update_path = ""
        self.app_update_status.setText(self.tr("help_update_restarting", version=release.version))

        # update_ready is emitted from inside AppUpdateWorker.run(). At this
        # point the native QThread may still be running for a few more event-loop
        # turns. Quitting the application immediately can therefore destroy the
        # worker before QThread.finished is emitted and abort the process.
        self._quit_after_update_worker = True
        self.accept()
        worker = self._app_update_worker
        if worker is None or not worker.isRunning():
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(0, app.quit)

    def _on_app_update_worker_finished(self) -> None:
        worker = self.sender()
        if worker is self._app_update_worker:
            self._app_update_worker = None
        self.app_update_button.setEnabled(True)
        self.dialog_buttons.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

        if self._quit_after_update_worker:
            self._quit_after_update_worker = False
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(0, app.quit)

    def reject(self) -> None:
        if self._app_update_worker is not None and self._app_update_worker.isRunning():
            self._app_update_worker.stop()
        if self._downloaded_update_path:
            cleanup_download(self._downloaded_update_path)
            self._downloaded_update_path = ""
        super().reject()

    # -- Combo helpers ----------------------------------------------------
    def _fill_combo(self, combo: QComboBox, values: tuple[str, ...], prefix: str) -> None:
        combo.clear()
        for value in values:
            combo.addItem(self.tr(prefix + value), value)
        combo.setMaxVisibleItems(len(values))
        combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        combo.view().setMinimumHeight((len(values) * 28) + 8)

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    # -- Getters ----------------------------------------------------------
    def selected_settings(self) -> ItemSettings:
        return ItemSettings(
            reading_style=str(self.reading_combo.currentData()),
            output_mode=str(self.output_combo.currentData()),
            image_format=str(self.format_combo.currentData()),
            keep_images=self.keep_images.isChecked(),
            image_threads=self.threads.value(),
            request_delay=self.delay.value(),
        )

    def check_updates_on_startup_enabled(self) -> bool:
        return self.check_updates_on_startup.isChecked()

    def auto_download_updates_enabled(self) -> bool:
        return self.auto_download_updates.isChecked()

    def automation_schedule(self) -> AutomationSchedule:
        schedule = self._automation.clone()
        schedule.enabled = self.automation_enabled.isChecked()
        return schedule

    def selected_output_dir(self) -> str:
        return self.output_dir_edit.text().strip()

    def manga_flags(self) -> list[dict]:
        result: list[dict] = []
        for index in range(self.manga_tree.topLevelItemCount()):
            item = self.manga_tree.topLevelItem(index)
            result.append({
                "path": str(item.data(0, Qt.ItemDataRole.UserRole) or ""),
                "check_updates": item.checkState(1) == Qt.CheckState.Checked,
                "auto_download": item.checkState(2) == Qt.CheckState.Checked,
            })
        return result

    def requested_manga_action(self) -> tuple[str, list[dict]] | None:
        if self._requested_manga_action is None:
            return None
        action, mangas = self._requested_manga_action
        return action, [dict(item) for item in mangas]


class ThemePreview(QFrame):
    def __init__(self, tr, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self.setObjectName("ThemePreview")
        self.setMinimumHeight(72)
        self.sample = QLabel(self.tr("preview_sample"))
        self.sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sample.setObjectName("PreviewSample")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.addStretch(1)
        layout.addWidget(self.sample)
        layout.addStretch(1)

    def set_theme(self, theme: ThemeSettings) -> None:
        c = _colors(theme)
        self.setStyleSheet(f"""
            QFrame#ThemePreview {{
                background: {c['window']};
                border: 1px solid {c['accent']};
                border-radius: 10px;
            }}
            QLabel#PreviewSample {{
                background: {c['panel']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-left: 10px solid {c['accent']};
                border-right: 10px solid {c['button']};
                border-radius: 6px;
                padding: 10px 14px;
            }}
        """)


class PreferencesDialog(QDialog):
    COLOR_FIELDS = (
        ("text", "color_text"),
        ("panel", "color_surface"),
        ("window", "color_background"),
        ("accent", "color_selection"),
        ("border", "color_overlay_border"),
        ("button", "color_overlay_split"),
    )

    def __init__(self, theme: ThemeSettings, tr, custom_themes: list[dict] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self.custom_themes = deepcopy(custom_themes or [])
        self.current_theme = theme.normalized()
        self.selected_template_label = self.tr("preset_" + self.current_theme.preset)
        self.setWindowTitle(self.tr("preferences_title"))
        self.setMinimumWidth(720)
        self.preset_buttons: dict[QPushButton, dict] = {}
        self.color_buttons: dict[str, QPushButton] = {}
        self.color_labels: dict[str, QLabel] = {}

        intro = QLabel(self.tr("appearance_hint"))
        intro.setWordWrap(True)

        preset_label = QLabel(self.tr("template"))
        self.preset_grid = QGridLayout()
        self.preset_grid.setSpacing(0)
        self._build_preset_grid(self.preset_grid)

        custom_label = QLabel(self.tr("custom_colors"))
        custom_frame = QFrame()
        custom_frame.setObjectName("Panel")
        custom_layout = QGridLayout(custom_frame)
        custom_layout.setContentsMargins(10, 10, 10, 10)
        custom_layout.setHorizontalSpacing(12)
        custom_layout.setVerticalSpacing(8)

        for row, (field, key) in enumerate(self.COLOR_FIELDS):
            label = QLabel(self.tr(key))
            value = QLabel()
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            button = QPushButton(self.tr("choose_color"))
            button.clicked.connect(lambda _checked=False, f=field: self.choose_color(f))
            self.color_labels[field] = value
            self.color_buttons[field] = button
            custom_layout.addWidget(label, row, 0)
            custom_layout.addWidget(value, row, 1)
            custom_layout.addWidget(button, row, 2)

        self.preview = ThemePreview(self.tr)
        custom_layout.addWidget(self.preview, 0, 3, len(self.COLOR_FIELDS), 1)
        custom_layout.setColumnStretch(1, 1)
        custom_layout.setColumnStretch(3, 2)

        bottom = QHBoxLayout()
        self.reset_themes_button = QPushButton(self.tr("reset_themes"))
        self.reset_button = QPushButton(self.tr("reset_theme"))
        self.save_template_button = QPushButton(self.tr("save_template"))
        self.save_button = QPushButton(self.tr("ok"))
        self.cancel_button = QPushButton(self.tr("cancel"))
        self.reset_themes_button.clicked.connect(self.reset_custom_themes)
        self.reset_button.clicked.connect(self.reset_current_theme)
        self.save_template_button.clicked.connect(self.save_current_template)
        self.save_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        bottom.addWidget(self.reset_themes_button)
        bottom.addWidget(self.reset_button)
        bottom.addStretch(1)
        bottom.addWidget(self.save_template_button)
        bottom.addWidget(self.save_button)
        bottom.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(intro)
        layout.addWidget(preset_label)
        layout.addLayout(self.preset_grid)
        layout.addWidget(custom_label)
        layout.addWidget(custom_frame)
        layout.addLayout(bottom)
        self._sync_all()

    def _build_preset_grid(self, grid: QGridLayout) -> None:
        names = list(PRESET_ORDER)
        for item in self.custom_themes:
            name = str(item.get("name", "") or "")
            if name:
                names.append(f"custom::{name}")
        columns = 6
        for index, name in enumerate(names):
            row, column = divmod(index, columns)
            if name.startswith("custom::"):
                custom_name = name.split("::", 1)[1]
                theme_data = next((item.get("theme", {}) for item in self.custom_themes if item.get("name") == custom_name), {})
                theme = ThemeSettings.from_mapping(theme_data)
                label = custom_name
                data = {"type": "custom", "name": custom_name, "theme": theme.to_mapping()}
            else:
                theme = preset_theme(name)
                label = self.tr("preset_" + name)
                data = {"type": "preset", "name": name}
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumHeight(56)
            button.clicked.connect(lambda _checked=False, d=data: self.select_template(d))
            self._style_preset_button(button, theme)
            grid.addWidget(button, row, column)
            self.preset_buttons[button] = data

    def _style_preset_button(self, button: QPushButton, theme: ThemeSettings) -> None:
        c = _colors(theme)
        button.setStyleSheet(f"""
            QPushButton {{
                background: {c['panel']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 10px 12px;
            }}
            QPushButton:hover {{
                border: 1px solid {c['accent']};
                background: {c['panel2']};
            }}
            QPushButton:checked {{
                border: 2px solid {c['accent']};
                padding: 9px 11px;
            }}
        """)

    def select_template(self, data: dict) -> None:
        if data.get("type") == "custom":
            name = str(data.get("name", ""))
            self.current_theme = ThemeSettings.from_mapping(data.get("theme"))
            self.selected_template_label = name or self.tr("template")
        else:
            name = str(data.get("name", "midnight"))
            self.current_theme = preset_theme(name)
            self.selected_template_label = self.tr("preset_" + name)
        self._sync_all()

    def choose_color(self, field: str) -> None:
        current = QColor(getattr(self.current_theme, field))
        color = QColorDialog.getColor(current, self, self.tr("choose_color"))
        if not color.isValid():
            return
        setattr(self.current_theme, field, color.name())
        self.current_theme = self.current_theme.normalized()
        self._sync_all()

    def _sync_all(self) -> None:
        self.current_theme = self.current_theme.normalized()
        for button, data in self.preset_buttons.items():
            checked = data.get("type") == "preset" and data.get("name") == self.current_theme.preset
            if data.get("type") == "custom":
                checked = ThemeSettings.from_mapping(data.get("theme")).to_mapping() == self.current_theme.to_mapping()
            button.blockSignals(True)
            button.setChecked(bool(checked))
            button.blockSignals(False)
        for field, _key in self.COLOR_FIELDS:
            value = getattr(self.current_theme, field)
            self.color_labels[field].setText(value)
            self.color_labels[field].setStyleSheet(f"background: {value}; color: {self._readable_text(value)}; border-radius: 5px; padding: 6px;")
            self.color_buttons[field].setStyleSheet(f"background: {value}; color: {self._readable_text(value)};")
        self.preview.set_theme(self.current_theme)

    def _readable_text(self, hex_color: str) -> str:
        color = QColor(hex_color)
        if not color.isValid():
            return "#000000"
        brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
        return "#000000" if brightness > 160 else "#ffffff"

    def save_current_template(self) -> None:
        name, ok = localized_text_input(self, self.tr, self.tr("save_template"), self.tr("template_name"))
        name = name.strip()
        if not ok or not name:
            return
        theme_data = self.current_theme.normalized().to_mapping()
        self.custom_themes = [item for item in self.custom_themes if str(item.get("name", "")) != name]
        self.custom_themes.append({"name": name, "theme": theme_data})
        self.selected_template_label = name
        self._rebuild_buttons()

    def reset_custom_themes(self) -> None:
        self.custom_themes = []
        self._rebuild_buttons()

    def reset_current_theme(self) -> None:
        self.current_theme = preset_theme("original")
        self.selected_template_label = self.tr("preset_original")
        self._sync_all()

    def _rebuild_buttons(self) -> None:
        while self.preset_grid.count():
            item = self.preset_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.preset_buttons.clear()
        self._build_preset_grid(self.preset_grid)
        self._sync_all()

    def selected_theme(self) -> ThemeSettings:
        return self.current_theme.normalized()

    def selected_template_name(self) -> str:
        return self.selected_template_label or self.tr("preset_" + self.current_theme.normalized().preset)

    def selected_custom_themes(self) -> list[dict]:
        return deepcopy(self.custom_themes)


class BusyProgressDialog(QDialog):
    def __init__(self, tr, title_key: str, message_key: str, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self.setWindowTitle(self.tr(title_key))
        self.setModal(False)
        self.setMinimumWidth(420)
        self.message_label = QLabel(self.tr(message_key))
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.cancel_button = QPushButton(self.tr("cancel"))
        layout = QVBoxLayout(self)
        layout.addWidget(self.message_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)

    def set_detail(self, message: str, index: int = 0, total: int = 0) -> None:
        if total > 0:
            self.detail_label.setText(f"{message}\n{index}/{total}")
        else:
            self.detail_label.setText(message)


class UpdateResultsDialog(QDialog):
    def __init__(self, mangas, tr, auto_download_default: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self.mangas = mangas
        self.download_after = auto_download_default
        self.setWindowTitle(self.tr("updates_dialog_title"))
        self.setMinimumSize(760, 460)

        self.info = QLabel(self.tr("updates_dialog_message", count=sum(len(m.chapters) for m in mangas)))
        self.info.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([
            self.tr("columns_name"),
            self.tr("columns_link"),
            self.tr("columns_status"),
        ])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)

        for manga in self.mangas:
            parent_item = QTreeWidgetItem(self.tree)
            parent_item.setText(0, manga.title)
            parent_item.setText(1, manga.url)
            parent_item.setText(2, self.tr("updates_new_chapters", count=len(manga.chapters)))
            parent_item.setCheckState(0, Qt.CheckState.Checked)
            parent_item.setData(0, Qt.ItemDataRole.UserRole, manga.item_id)
            for chapter in manga.chapters:
                child = QTreeWidgetItem(parent_item)
                child.setText(0, chapter.title)
                child.setText(1, chapter.url)
                child.setText(2, self.tr("status_pending"))
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setData(0, Qt.ItemDataRole.UserRole, chapter.item_id)
            parent_item.setExpanded(True)

        self.auto_download = QCheckBox(self.tr("updates_download_after_add"))
        self.auto_download.setChecked(auto_download_default)

        self.add_button = QPushButton(self.tr("updates_add_selected"))
        self.add_download_button = QPushButton(self.tr("updates_add_and_download"))
        self.cancel_button = QPushButton(self.tr("cancel"))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.add_download_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info)
        layout.addWidget(self.tree)
        layout.addWidget(self.auto_download)
        layout.addLayout(buttons)

        self.add_button.clicked.connect(self._accept_add)
        self.add_download_button.clicked.connect(self._accept_download)
        self.cancel_button.clicked.connect(self.reject)

    def _accept_add(self) -> None:
        self.download_after = self.auto_download.isChecked()
        self.accept()

    def _accept_download(self) -> None:
        self.download_after = True
        self.accept()

    def selected_mangas(self):
        selected = []
        for i in range(self.tree.topLevelItemCount()):
            parent_item = self.tree.topLevelItem(i)
            manga = self.mangas[i]
            chapters = []
            for j in range(parent_item.childCount()):
                child_item = parent_item.child(j)
                if child_item.checkState(0) == Qt.CheckState.Checked:
                    chapters.append(manga.chapters[j])
            if chapters:
                cloned = deepcopy(manga)
                cloned.chapters = chapters
                selected.append(cloned)
        return selected



@dataclass
class ReaderPage:
    manga_title: str
    manga_url: str
    chapter_id: str
    chapter_title: str
    page_index: int
    display_name: str
    file_path: Path | None = None
    archive_path: Path | None = None
    archive_member: str = ""


class MangaReaderDialog(QDialog):
    ROLE_PAGE_INDEX = int(Qt.ItemDataRole.UserRole) + 101
    ROLE_IS_CHAPTER = int(Qt.ItemDataRole.UserRole) + 102
    MIN_ZOOM = 0.35
    MAX_ZOOM = 4.0

    def __init__(
        self,
        manga: MangaEntry,
        output_dir: str,
        tr,
        settings=None,
        start_chapter_id: str = "",
        start_chapter_title: str = "",
        start_page_index: int = 0,
        start_global_index: int | None = None,
        start_after: bool = False,
        manga_dir: str | Path | None = None,
        reading_store: ReadingStateStore | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manga = manga
        self.output_dir = output_dir
        self.tr = tr
        self.settings = settings
        self.manga_dir = Path(manga_dir).expanduser() if manga_dir is not None else (Path(output_dir).expanduser() / sanitize_filename(manga.title, "Manga"))
        self.reading_store = reading_store or ReadingStateStore(output_dir)
        self.pages = self._collect_pages()
        self.current_index = self._resolve_start_index(
            start_chapter_id,
            start_chapter_title,
            start_page_index,
            start_global_index,
            start_after,
        )
        self._updating_tree = False
        self._navigation_visible = True
        self._zoom_factor = 1.0
        self._base_pixmap = QPixmap()

        self.setWindowTitle(self.tr("reader_title"))
        self.resize(1200, 900)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(QSize(240, 240))
        self.image_label.setObjectName("ReaderImage")

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.viewport().installEventFilter(self)
        self.image_label.installEventFilter(self)

        self.navigation_tree = QTreeWidget()
        self.navigation_tree.setObjectName("ReaderNavigationTree")
        self.navigation_tree.setHeaderHidden(True)
        self.navigation_tree.setMinimumWidth(230)
        self.navigation_tree.setMaximumWidth(420)
        self.navigation_tree.itemClicked.connect(self.on_navigation_item_clicked)

        self.toggle_navigation_button = QPushButton()
        self.toggle_navigation_button.setObjectName("ReaderToggleButton")
        self.toggle_navigation_button.setMaximumWidth(150)
        self.toggle_navigation_button.clicked.connect(self.toggle_navigation)

        self.display_mode_combo = QComboBox()
        self.display_mode_combo.setMinimumWidth(220)
        self.display_mode_combo.currentIndexChanged.connect(self.on_display_mode_changed)

        self.info_label = QLabel()
        self.info_label.setObjectName("Muted")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.previous_button = QPushButton()
        self.next_button = QPushButton()
        self.zoom_reset_button = QPushButton()
        self.close_button = QPushButton()

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(self.toggle_navigation_button)
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)
        controls.addWidget(self.display_mode_combo)
        controls.addWidget(self.zoom_reset_button)
        controls.addStretch(1)
        controls.addWidget(self.close_button)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)
        splitter.addWidget(self.navigation_tree)
        splitter.addWidget(self.scroll_area)
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 920])
        self.splitter = splitter

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.info_label)
        layout.addLayout(controls)

        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.zoom_reset_button.clicked.connect(self.reset_zoom)
        self.close_button.clicked.connect(self.accept)

        self._fill_display_modes()
        self._fill_navigation_tree()

        for sequence, slot in (
            (QKeySequence.StandardKey.MoveToPreviousChar, self.previous_page),
            (QKeySequence.StandardKey.MoveToNextChar, self.next_page),
            (QKeySequence("A"), self.previous_page),
            (QKeySequence("D"), self.next_page),
            (QKeySequence("Space"), self.next_page),
            (QKeySequence("Backspace"), self.previous_page),
        ):
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(slot)

        self.retranslate_ui()
        self.show_page()
        QTimer.singleShot(0, self.showMaximized)

    def _fill_display_modes(self) -> None:
        current = self.current_display_mode()
        self.display_mode_combo.blockSignals(True)
        self.display_mode_combo.clear()
        for value in ("single", "double", "strip_single", "strip_double"):
            self.display_mode_combo.addItem(self.tr("reader_mode_" + value), value)
        index = self.display_mode_combo.findData(current)
        if index < 0:
            index = 0
        self.display_mode_combo.setCurrentIndex(index)
        self.display_mode_combo.blockSignals(False)

    def current_display_mode(self) -> str:
        return str(self.display_mode_combo.currentData() or "single")

    def retranslate_ui(self) -> None:
        self.toggle_navigation_button.setText(self.tr("reader_hide_navigation") if self._navigation_visible else self.tr("reader_show_navigation"))
        self.previous_button.setText(self.tr("reader_previous"))
        self.next_button.setText(self.tr("reader_next"))
        self.zoom_reset_button.setText(self.tr("reader_zoom_reset", zoom=int(self._zoom_factor * 100)))
        self.close_button.setText(self.tr("reader_close"))
        current = self.current_display_mode()
        self._fill_display_modes()
        index = self.display_mode_combo.findData(current)
        if index >= 0:
            self.display_mode_combo.setCurrentIndex(index)

    def toggle_navigation(self) -> None:
        self._navigation_visible = not self._navigation_visible
        self.navigation_tree.setVisible(self._navigation_visible)
        if self._navigation_visible:
            self.splitter.setSizes([280, max(400, self.width() - 280)])
        self.retranslate_ui()

    def on_display_mode_changed(self) -> None:
        self._zoom_factor = 1.0
        self.show_page()

    def reset_zoom(self) -> None:
        self._zoom_factor = 1.0
        self._apply_zoom()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            if not delta:
                return True

            # Zoom only while Alt or Ctrl is held. Use QApplication.keyboardModifiers()
            # as fallback because some Qt/platform combinations do not report
            # Alt/Ctrl reliably on the wheel event itself.
            modifiers = event.modifiers() | QApplication.keyboardModifiers()
            if modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
                factor = 1.12 if delta > 0 else 1 / 1.12
                self._zoom_factor = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom_factor * factor))
                self._apply_zoom()
                return True

            if self.current_display_mode().startswith("strip"):
                # Let QScrollArea handle normal vertical scrolling in strip views.
                return False

            if delta > 0:
                self.previous_page()
            else:
                self.next_page()
            return True
        return super().eventFilter(watched, event)

    def _collect_pages(self) -> list[ReaderPage]:
        pages: list[ReaderPage] = []
        for chapter in self.manga.chapters:
            paths = chapter_output_paths(self.output_dir, self.manga.title, chapter.title)
            chapter_dir = paths["chapter_dir"]
            chapter_pages: list[ReaderPage] = []

            if chapter_dir.exists() and chapter_dir.is_dir():
                image_files = sorted(
                    [
                        item for item in chapter_dir.iterdir()
                        if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS and item.stat().st_size > 0
                    ],
                    key=lambda item: natural_sort_key(item.name),
                )
                for page_index, image_path in enumerate(image_files):
                    chapter_pages.append(
                        ReaderPage(
                            manga_title=self.manga.title,
                            manga_url=self.manga.url,
                            chapter_id=chapter.item_id,
                            chapter_title=chapter.title,
                            page_index=page_index,
                            display_name=image_path.name,
                            file_path=image_path,
                        )
                    )

            if not chapter_pages and paths["cbz"].exists() and paths["cbz"].stat().st_size > 0:
                try:
                    with zipfile.ZipFile(paths["cbz"], "r") as archive:
                        members = sorted(
                            [
                                member for member in archive.namelist()
                                if Path(member).suffix.lower() in IMAGE_EXTENSIONS
                            ],
                            key=natural_sort_key,
                        )
                    for page_index, member in enumerate(members):
                        chapter_pages.append(
                            ReaderPage(
                                manga_title=self.manga.title,
                                manga_url=self.manga.url,
                                chapter_id=chapter.item_id,
                                chapter_title=chapter.title,
                                page_index=page_index,
                                display_name=Path(member).name,
                                archive_path=paths["cbz"],
                                archive_member=member,
                            )
                        )
                except Exception:
                    pass

            pages.extend(chapter_pages)
        return pages

    def _chapter_key(self, page: ReaderPage) -> tuple[str, str]:
        return (page.chapter_id, page.chapter_title)

    def _chapter_pages(self, page: ReaderPage) -> list[ReaderPage]:
        return [candidate for candidate in self.pages if candidate.chapter_id == page.chapter_id or candidate.chapter_title == page.chapter_title]

    def _resolve_start_index(
        self,
        chapter_id: str,
        chapter_title: str,
        page_index: int,
        start_global_index: int | None,
        start_after: bool,
    ) -> int:
        if not self.pages:
            return 0

        if start_global_index is not None and start_global_index >= 0:
            target_index = min(len(self.pages) - 1, start_global_index)
            if start_after:
                target_index = min(len(self.pages) - 1, target_index + 1)
            return max(0, target_index)

        target_index = 0
        for index, page in enumerate(self.pages):
            if chapter_id and page.chapter_id == chapter_id and page.page_index == page_index:
                target_index = index
                break
            if chapter_title and page.chapter_title == chapter_title and page.page_index == page_index:
                target_index = index
                break
        else:
            for index, page in enumerate(self.pages):
                if chapter_id and page.chapter_id == chapter_id:
                    target_index = index
                    break
                if chapter_title and page.chapter_title == chapter_title:
                    target_index = index
                    break

        if start_after:
            target_index = min(len(self.pages) - 1, target_index + 1)
        return max(0, min(len(self.pages) - 1, target_index))

    def _page_bytes(self, page: ReaderPage) -> bytes:
        if page.file_path:
            return page.file_path.read_bytes()
        if page.archive_path and page.archive_member:
            with zipfile.ZipFile(page.archive_path, "r") as archive:
                return archive.read(page.archive_member)
        return b""

    def _load_pixmap(self, page: ReaderPage) -> QPixmap:
        pixmap = QPixmap()
        pixmap.loadFromData(self._page_bytes(page))
        return pixmap

    def _scale_for_strip(self, pixmap: QPixmap, target_width: int, target_height: int) -> QPixmap:
        if pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
            return pixmap

        # In strip mode, 100 % should not mean "force every page to full width".
        # Each page is fitted into the reader viewport by width AND height. This
        # prevents landscape pages from appearing massively over-zoomed.
        scale = min(max(1, target_width) / pixmap.width(), max(1, target_height) / pixmap.height())
        scale = max(0.01, scale)
        width = max(1, int(pixmap.width() * scale))
        height = max(1, int(pixmap.height() * scale))
        return pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def _compose_horizontal(self, pixmaps: list[QPixmap]) -> QPixmap:
        pixmaps = [p for p in pixmaps if not p.isNull()]
        if not pixmaps:
            return QPixmap()
        height = max(p.height() for p in pixmaps)
        scaled = [p.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation) if p.height() != height else p for p in pixmaps]
        width = sum(p.width() for p in scaled)
        result = QPixmap(width, height)
        result.fill(Qt.GlobalColor.white)
        painter = QPainter(result)
        x = 0
        for pixmap in scaled:
            painter.drawPixmap(x, 0, pixmap)
            x += pixmap.width()
        painter.end()
        return result

    def _compose_vertical(self, rows: list[QPixmap]) -> QPixmap:
        rows = [row for row in rows if not row.isNull()]
        if not rows:
            return QPixmap()
        width = max(row.width() for row in rows)
        height = sum(row.height() for row in rows)
        result = QPixmap(width, height)
        result.fill(Qt.GlobalColor.white)
        painter = QPainter(result)
        y = 0
        for row in rows:
            x = (width - row.width()) // 2
            painter.drawPixmap(x, y, row)
            y += row.height()
        painter.end()
        return result

    def _display_pages_for_current_mode(self) -> list[ReaderPage]:
        if not self.pages:
            return []
        page = self.pages[self.current_index]
        mode = self.current_display_mode()
        if mode == "single":
            return [page]
        if mode == "double":
            result = [page]
            if self.current_index + 1 < len(self.pages):
                next_page = self.pages[self.current_index + 1]
                if next_page.chapter_id == page.chapter_id or next_page.chapter_title == page.chapter_title:
                    result.append(next_page)
            return result
        return self._chapter_pages(page)

    def _build_display_pixmap(self) -> QPixmap:
        display_pages = self._display_pages_for_current_mode()
        if not display_pages:
            return QPixmap()
        mode = self.current_display_mode()
        if mode == "single":
            return self._load_pixmap(display_pages[0])
        if mode == "double":
            # Manga double-page view is right-to-left: the current/first page
            # belongs on the right, so the next page is drawn on the left.
            return self._compose_horizontal([self._load_pixmap(page) for page in reversed(display_pages)])

        viewport = self.scroll_area.viewport().size()
        strip_width = max(240, viewport.width() - 18)
        strip_height = max(240, viewport.height() - 18)

        if mode == "strip_single":
            rows = [self._scale_for_strip(self._load_pixmap(page), strip_width, strip_height) for page in display_pages]
            return self._compose_vertical(rows)
        if mode == "strip_double":
            rows: list[QPixmap] = []
            index = 0
            page_width = max(120, (strip_width - 10) // 2)
            while index < len(display_pages):
                pair = [self._scale_for_strip(self._load_pixmap(display_pages[index]), page_width, strip_height)]
                if index + 1 < len(display_pages):
                    pair.append(self._scale_for_strip(self._load_pixmap(display_pages[index + 1]), page_width, strip_height))
                # Right-to-left manga ordering: first/current page on the right.
                rows.append(self._compose_horizontal(list(reversed(pair))))
                index += 2
            return self._compose_vertical(rows)
        return self._load_pixmap(display_pages[0])

    def _apply_zoom(self) -> None:
        if self._base_pixmap.isNull():
            return
        viewport = self.scroll_area.viewport().size()
        base_w = max(1, self._base_pixmap.width())
        base_h = max(1, self._base_pixmap.height())
        available_w = max(1, viewport.width() - 18)
        available_h = max(1, viewport.height() - 18)
        mode = self.current_display_mode()

        # 100 % means "fit to the reader": a single page or double page fills
        # the available width/height without cropping. Strip pixmaps are already
        # composed from viewport-fitted pages, so avoid a second width fit.
        if mode.startswith("strip"):
            fit = 1.0
        else:
            fit = max(0.01, min(available_w / base_w, available_h / base_h))

        scale = fit * self._zoom_factor
        width = max(1, int(base_w * scale))
        height = max(1, int(base_h * scale))
        pixmap = self._base_pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(pixmap)
        self.image_label.setFixedSize(pixmap.size())
        self.zoom_reset_button.setText(self.tr("reader_zoom_reset", zoom=int(self._zoom_factor * 100)))

    def _fill_navigation_tree(self) -> None:
        self._updating_tree = True
        try:
            self.navigation_tree.clear()
            chapter_items: dict[tuple[str, str], QTreeWidgetItem] = {}
            chapter_counts: dict[tuple[str, str], int] = {}

            for global_index, page in enumerate(self.pages):
                key = self._chapter_key(page)
                parent = chapter_items.get(key)
                if parent is None:
                    parent = QTreeWidgetItem(self.navigation_tree)
                    parent.setText(0, page.chapter_title)
                    parent.setData(0, self.ROLE_PAGE_INDEX, global_index)
                    parent.setData(0, self.ROLE_IS_CHAPTER, True)
                    chapter_items[key] = parent
                    chapter_counts[key] = 0

                chapter_counts[key] += 1
                child = QTreeWidgetItem(parent)
                child.setText(0, self.tr("reader_page_list_item", page=chapter_counts[key]))
                child.setData(0, self.ROLE_PAGE_INDEX, global_index)
                child.setData(0, self.ROLE_IS_CHAPTER, False)

            for item in chapter_items.values():
                item.setExpanded(False)
            self.navigation_tree.resizeColumnToContents(0)
        finally:
            self._updating_tree = False

    def _sync_navigation_tree_to_current_page(self) -> None:
        if self._updating_tree:
            return
        self._updating_tree = True
        try:
            for index in range(self.navigation_tree.topLevelItemCount()):
                parent = self.navigation_tree.topLevelItem(index)
                if not parent:
                    continue
                parent_data = parent.data(0, self.ROLE_PAGE_INDEX)
                parent_page_index = int(parent_data) if parent_data is not None else -1
                first_page = self.pages[parent_page_index] if 0 <= parent_page_index < len(self.pages) else None
                if first_page:
                    current = self.pages[self.current_index]
                    same_chapter = (first_page.chapter_id and first_page.chapter_id == current.chapter_id) or first_page.chapter_title == current.chapter_title
                    if same_chapter and not parent.isExpanded():
                        self.navigation_tree.setCurrentItem(parent)
                        self.navigation_tree.scrollToItem(parent)
                        return
                for child_index in range(parent.childCount()):
                    child = parent.child(child_index)
                    child_data = child.data(0, self.ROLE_PAGE_INDEX)
                    if child_data is not None and int(child_data) == self.current_index:
                        self.navigation_tree.setCurrentItem(child)
                        self.navigation_tree.scrollToItem(child)
                        return
        finally:
            self._updating_tree = False

    def on_navigation_item_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        if self._updating_tree:
            return
        page_data = item.data(0, self.ROLE_PAGE_INDEX)
        page_index = int(page_data) if page_data is not None else -1
        if page_index < 0:
            return

        # Clicking a chapter opens its first page and expands it, so the first
        # pages are immediately selectable. Index 0 is valid and must not be
        # treated as "missing".
        if bool(item.data(0, self.ROLE_IS_CHAPTER)):
            item.setExpanded(True)

        self.current_index = max(0, min(len(self.pages) - 1, page_index))
        self._zoom_factor = 1.0
        self.show_page()

    def show_page(self) -> None:
        if not self.pages:
            self.image_label.setText(self.tr("reader_no_pages"))
            self.info_label.setText("")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.navigation_tree.setEnabled(False)
            return

        self.current_index = max(0, min(len(self.pages) - 1, self.current_index))
        page = self.pages[self.current_index]
        self._base_pixmap = self._build_display_pixmap()
        if self._base_pixmap.isNull():
            self.image_label.clear()
            self.image_label.setText(self.tr("reader_page_load_failed"))
        else:
            self._apply_zoom()

        self.info_label.setText(
            self.tr(
                "reader_page_info",
                manga=page.manga_title,
                chapter=page.chapter_title,
                page=page.page_index + 1,
                pages=self._chapter_page_count(page.chapter_id, page.chapter_title),
                current=self.current_index + 1,
                total=len(self.pages),
            )
        )
        self.previous_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < len(self.pages) - 1)
        self._sync_navigation_tree_to_current_page()
        self._save_position(page)

    def _chapter_page_count(self, chapter_id: str, chapter_title: str) -> int:
        return sum(1 for page in self.pages if page.chapter_id == chapter_id or page.chapter_title == chapter_title)

    def _reader_completed_keys(self, page: ReaderPage) -> list[str]:
        keys: list[str] = []
        title_key = sanitize_filename(page.manga_title, "manga").lower()
        if title_key:
            keys.append(f"reader/completed/title/{title_key}")
        if page.manga_url:
            url_key = sanitize_filename(page.manga_url, "manga").lower()
            if url_key:
                keys.append(f"reader/completed/url/{url_key}")
        return keys

    def _save_position(self, page: ReaderPage) -> None:
        # Mark the manga as read when the reader reaches the final page.
        reached_latest_page = bool(self.pages and self.current_index >= len(self.pages) - 1)

        # Keep the old QSettings position as a backwards-compatible fallback,
        # but do not make shared desktop/mobile progress depend on QSettings.
        if self.settings is not None:
            self.settings.setValue("reader/has_position", "true")
            self.settings.setValue("reader/last_manga_title", page.manga_title)
            self.settings.setValue("reader/last_manga_url", page.manga_url)
            self.settings.setValue("reader/last_chapter_id", page.chapter_id)
            self.settings.setValue("reader/last_chapter_title", page.chapter_title)
            self.settings.setValue("reader/last_page_index", page.page_index)
            self.settings.setValue("reader/last_global_index", self.current_index)
            self.settings.setValue("reader/last_output_dir", self.output_dir)
            if reached_latest_page:
                for key in self._reader_completed_keys(page):
                    self.settings.setValue(key, "true")
            self.settings.sync()

        # Shared progress is stored in the library itself so the desktop and
        # mobile readers always resume from the same chapter and page.
        try:
            self.reading_store.update_progress(
                manga_id=manga_id_for_path(self.manga_dir),
                title=page.manga_title,
                source_url=page.manga_url,
                path=self.manga_dir,
                chapter_id=page.chapter_id,
                chapter_title=page.chapter_title,
                page_index=page.page_index,
                global_index=self.current_index,
                chapter_pages=self._chapter_page_count(page.chapter_id, page.chapter_title),
                total_pages=len(self.pages),
                reader_mode=self.current_display_mode(),
                reached_latest_page=reached_latest_page,
            )
        except Exception:
            # QSettings remains the backwards-compatible fallback even if the
            # library is temporarily read-only.
            pass

    def save_current_position(self) -> None:
        if not self.pages:
            return
        self.current_index = max(0, min(len(self.pages) - 1, self.current_index))
        self._save_position(self.pages[self.current_index])

    def accept(self) -> None:
        self.save_current_position()
        super().accept()

    def reject(self) -> None:
        self.save_current_position()
        super().reject()

    def closeEvent(self, event) -> None:
        self.save_current_position()
        super().closeEvent(event)

    def previous_page(self) -> None:
        if self.current_index > 0:
            step = 2 if self.current_display_mode() == "double" else 1
            self.current_index = max(0, self.current_index - step)
            self.show_page()

    def next_page(self) -> None:
        if self.current_index < len(self.pages) - 1:
            step = 2 if self.current_display_mode() == "double" else 1
            self.current_index = min(len(self.pages) - 1, self.current_index + step)
            self.show_page()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.pages:
            self._base_pixmap = self._build_display_pixmap()
        self._apply_zoom()


