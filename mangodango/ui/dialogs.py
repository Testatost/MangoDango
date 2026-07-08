from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import zipfile

from PySide6.QtCore import Qt, QSize, QTimer, QEvent
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QDoubleSpinBox,
    QVBoxLayout,
)

from ..constants import IMAGE_FORMATS, OUTPUT_MODES, READING_STYLES
from ..models import ChapterEntry, ItemSettings, MangaEntry
from .styles import PRESET_ORDER, THEME_PRESETS, ThemeSettings, preset_theme, _colors
from ..scraper import IMAGE_EXTENSIONS, chapter_output_paths, natural_sort_key, sanitize_filename


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
        name, ok = QInputDialog.getText(self, self.tr("save_template"), self.tr("template_name"))
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manga = manga
        self.output_dir = output_dir
        self.tr = tr
        self.settings = settings
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
        if self.settings is None:
            return
        self.settings.setValue("reader/has_position", "true")
        self.settings.setValue("reader/last_manga_title", page.manga_title)
        self.settings.setValue("reader/last_manga_url", page.manga_url)
        self.settings.setValue("reader/last_chapter_id", page.chapter_id)
        self.settings.setValue("reader/last_chapter_title", page.chapter_title)
        self.settings.setValue("reader/last_page_index", page.page_index)
        self.settings.setValue("reader/last_global_index", self.current_index)
        self.settings.setValue("reader/last_output_dir", self.output_dir)

        # Mark the manga as read when the reader reaches the final page.
        if self.pages and self.current_index >= len(self.pages) - 1:
            for key in self._reader_completed_keys(page):
                self.settings.setValue(key, "true")

        self.settings.sync()

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


