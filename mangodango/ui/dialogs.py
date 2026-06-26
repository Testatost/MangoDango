from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
)

from ..constants import IMAGE_FORMATS, OUTPUT_MODES, READING_STYLES
from ..models import ItemSettings
from .styles import PRESET_ORDER, THEME_PRESETS, ThemeSettings, preset_theme, _colors


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
