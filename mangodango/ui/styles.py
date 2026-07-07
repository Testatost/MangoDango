from __future__ import annotations

from dataclasses import dataclass, asdict

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..constants import DEFAULT_ACCENT, THEME_MODES


THEME_PRESETS: dict[str, dict[str, str]] = {
    "original": {
        "mode": "light", "accent": "#2563eb", "window": "#f7f7f5", "panel": "#efede8",
        "panel2": "#e4e0d6", "input": "#ffffff", "button": "#eee9df", "button_hover": "#e2dbcf",
        "text": "#111827", "muted": "#6b7280", "border": "#c8c1b3", "selection_text": "#ffffff",
        "disabled": "#9ca3af",
    },
    "light": {
        "mode": "light", "accent": "#2f6fed", "window": "#faf8f0", "panel": "#f2eee4",
        "panel2": "#e9e2d5", "input": "#ffffff", "button": "#eee7db", "button_hover": "#e3dacb",
        "text": "#111827", "muted": "#6a6258", "border": "#cfc5b5", "selection_text": "#ffffff",
        "disabled": "#9a9289",
    },
    "midnight": {
        "mode": "dark", "accent": "#7c9cff", "window": "#101827", "panel": "#172033",
        "panel2": "#202a40", "input": "#111b2b", "button": "#22304a", "button_hover": "#2d3d5f",
        "text": "#eaf0ff", "muted": "#aeb9cf", "border": "#3c4964", "selection_text": "#ffffff",
        "disabled": "#79869b",
    },
    "paper": {
        "mode": "light", "accent": "#b45309", "window": "#f7efd4", "panel": "#fff8dd",
        "panel2": "#efe4bf", "input": "#fffbeb", "button": "#f3e6c4", "button_hover": "#ead8aa",
        "text": "#2b2114", "muted": "#7a6948", "border": "#d6c28e", "selection_text": "#ffffff",
        "disabled": "#a08f6a",
    },
    "cyberpunk": {
        "mode": "dark", "accent": "#ff2bd6", "window": "#050414", "panel": "#0e0a25",
        "panel2": "#1a103c", "input": "#0a0620", "button": "#181036", "button_hover": "#27135c",
        "text": "#f8e8ff", "muted": "#bda4d6", "border": "#8a2be2", "selection_text": "#ffffff",
        "disabled": "#7c6b8f",
    },
    "retrowave": {
        "mode": "dark", "accent": "#f973ff", "window": "#1c1136", "panel": "#251547",
        "panel2": "#311c5d", "input": "#170d2c", "button": "#37205f", "button_hover": "#442b78",
        "text": "#fff0fb", "muted": "#d9b6e4", "border": "#7c3aed", "selection_text": "#ffffff",
        "disabled": "#9c80ad",
    },
    "forest": {
        "mode": "dark", "accent": "#84cc16", "window": "#0f1a0f", "panel": "#172617",
        "panel2": "#20341f", "input": "#101f10", "button": "#223621", "button_hover": "#2d482b",
        "text": "#efffe7", "muted": "#b6c9a8", "border": "#45633a", "selection_text": "#102000",
        "disabled": "#839477",
    },
    "ocean": {
        "mode": "dark", "accent": "#2dd4bf", "window": "#102027", "panel": "#152c35",
        "panel2": "#1b3843", "input": "#0e242c", "button": "#1e3a46", "button_hover": "#264b59",
        "text": "#eafffb", "muted": "#9fc7c2", "border": "#37616d", "selection_text": "#04201d",
        "disabled": "#71938f",
    },
    "sakura": {
        "mode": "light", "accent": "#f472b6", "window": "#fff7fb", "panel": "#fff0f7",
        "panel2": "#f9dce9", "input": "#fffafd", "button": "#f5dce9", "button_hover": "#f0c9dc",
        "text": "#3b2332", "muted": "#8c6176", "border": "#e8b7cd", "selection_text": "#ffffff",
        "disabled": "#ae8799",
    },
    "copper": {
        "mode": "dark", "accent": "#fb923c", "window": "#2b211d", "panel": "#392821",
        "panel2": "#473126", "input": "#241a17", "button": "#442f25", "button_hover": "#563a2b",
        "text": "#fff1e7", "muted": "#d8b9a5", "border": "#7a4c35", "selection_text": "#241003",
        "disabled": "#9b7b68",
    },
    "terminal": {
        "mode": "dark", "accent": "#00ff66", "window": "#001006", "panel": "#001a0b",
        "panel2": "#05240f", "input": "#000b04", "button": "#062411", "button_hover": "#0a341a",
        "text": "#9dffb9", "muted": "#66b981", "border": "#00aa44", "selection_text": "#001006",
        "disabled": "#4d805e",
    },
    "organs": {
        "mode": "dark", "accent": "#dc2626", "window": "#250711", "panel": "#310b17",
        "panel2": "#3f0d1d", "input": "#1c050d", "button": "#3d101b", "button_hover": "#551625",
        "text": "#ffe8ee", "muted": "#c994a3", "border": "#7f1d1d", "selection_text": "#ffffff",
        "disabled": "#9a6c76",
    },
    "lavender": {
        "mode": "light", "accent": "#8b5cf6", "window": "#f7f4ff", "panel": "#efebff",
        "panel2": "#e5ddff", "input": "#ffffff", "button": "#e8e0ff", "button_hover": "#ddd1ff",
        "text": "#261b3f", "muted": "#675a80", "border": "#c7b8f4", "selection_text": "#ffffff",
        "disabled": "#9186a7",
    },
    "gpt": {
        "mode": "dark", "accent": "#10a37f", "window": "#202b27", "panel": "#253630",
        "panel2": "#2c4038", "input": "#1b2723", "button": "#2d443a", "button_hover": "#38594c",
        "text": "#f2fff9", "muted": "#b2c9c0", "border": "#45675a", "selection_text": "#06251e",
        "disabled": "#81968d",
    },
    "claude": {
        "mode": "light", "accent": "#d97706", "window": "#f7f1e8", "panel": "#f4eadf",
        "panel2": "#eadbca", "input": "#fffaf4", "button": "#ead8c4", "button_hover": "#dec8b0",
        "text": "#2a2118", "muted": "#756455", "border": "#d3bea8", "selection_text": "#ffffff",
        "disabled": "#9b8b7a",
    },
    "cute": {
        "mode": "light", "accent": "#fb7185", "window": "#fff7fa", "panel": "#fff0f5",
        "panel2": "#ffe1eb", "input": "#ffffff", "button": "#ffe0ea", "button_hover": "#ffd1e0",
        "text": "#40202d", "muted": "#875d6b", "border": "#f6b3c4", "selection_text": "#ffffff",
        "disabled": "#aa8793",
    },
    "hell": {
        "mode": "light", "accent": "#2563eb", "window": "#ffffff", "panel": "#f8f8f8",
        "panel2": "#eeeeee", "input": "#ffffff", "button": "#f2f2f2", "button_hover": "#e7e7e7",
        "text": "#111111", "muted": "#555555", "border": "#c9c9c9", "selection_text": "#ffffff",
        "disabled": "#9a9a9a",
    },
    "dunkel": {
        "mode": "dark", "accent": "#3b82f6", "window": "#222831", "panel": "#2b3038",
        "panel2": "#303844", "input": "#252b35", "button": "#343b46", "button_hover": "#404856",
        "text": "#f3f4f6", "muted": "#b4bac6", "border": "#4b5563", "selection_text": "#ffffff",
        "disabled": "#8b95a3",
    },
}

PRESET_ORDER = (
    "original", "light", "midnight", "paper", "cyberpunk", "retrowave",
    "forest", "ocean", "sakura", "copper", "terminal", "organs",
    "lavender", "gpt", "claude", "cute", "hell", "dunkel",
)


@dataclass
class ThemeSettings:
    mode: str = "dark"
    accent: str = DEFAULT_ACCENT
    preset: str = "midnight"
    window: str = ""
    panel: str = ""
    input: str = ""
    text: str = ""
    button: str = ""
    border: str = ""

    def normalized(self) -> "ThemeSettings":
        preset = self.preset if self.preset in THEME_PRESETS else "midnight"
        preset_values = THEME_PRESETS[preset]
        mode = self.mode if self.mode in THEME_MODES else preset_values.get("mode", "dark")
        return ThemeSettings(
            mode=mode,
            accent=_valid_color(self.accent, preset_values.get("accent", DEFAULT_ACCENT)),
            preset=preset,
            window=_valid_color(self.window, preset_values.get("window", "#1f232a")),
            panel=_valid_color(self.panel, preset_values.get("panel", "#262b34")),
            input=_valid_color(self.input, preset_values.get("input", "#20252d")),
            text=_valid_color(self.text, preset_values.get("text", "#f2f4f8")),
            button=_valid_color(self.button, preset_values.get("button", "#303744")),
            border=_valid_color(self.border, preset_values.get("border", "#49515e")),
        )

    @classmethod
    def from_mapping(cls, data: dict | None) -> "ThemeSettings":
        data = data or {}
        return cls(
            mode=str(data.get("mode", "dark") or "dark"),
            accent=str(data.get("accent", DEFAULT_ACCENT) or DEFAULT_ACCENT),
            preset=str(data.get("preset", "midnight") or "midnight"),
            window=str(data.get("window", "") or ""),
            panel=str(data.get("panel", "") or ""),
            input=str(data.get("input", "") or ""),
            text=str(data.get("text", "") or ""),
            button=str(data.get("button", "") or ""),
            border=str(data.get("border", "") or ""),
        ).normalized()

    def to_mapping(self) -> dict[str, str]:
        return asdict(self.normalized())


def _valid_color(value: str, fallback: str) -> str:
    color = QColor(str(value or ""))
    return color.name() if color.isValid() else fallback


def _tint(hex_color: str, factor: float) -> str:
    color = QColor(hex_color)
    if not color.isValid():
        return hex_color
    r, g, b = color.red(), color.green(), color.blue()
    if factor >= 0:
        r = min(255, int(r + (255 - r) * factor))
        g = min(255, int(g + (255 - g) * factor))
        b = min(255, int(b + (255 - b) * factor))
    else:
        r = max(0, int(r * (1 + factor)))
        g = max(0, int(g * (1 + factor)))
        b = max(0, int(b * (1 + factor)))
    return QColor(r, g, b).name()


def _colors(settings: ThemeSettings) -> dict[str, str]:
    settings = settings.normalized()
    preset = THEME_PRESETS.get(settings.preset, THEME_PRESETS["midnight"]).copy()
    preset.update({
        "mode": settings.mode,
        "accent": settings.accent,
        "window": settings.window,
        "panel": settings.panel,
        "input": settings.input,
        "button": settings.button,
        "text": settings.text,
        "border": settings.border,
    })
    if settings.mode == "light":
        preset.setdefault("panel2", _tint(settings.panel, -0.04))
        preset.setdefault("button_hover", _tint(settings.button, -0.06))
        preset.setdefault("muted", _tint(settings.text, 0.35))
        preset.setdefault("selection_text", "#ffffff")
        preset.setdefault("disabled", _tint(settings.text, 0.55))
    else:
        preset.setdefault("panel2", _tint(settings.panel, 0.08))
        preset.setdefault("button_hover", _tint(settings.button, 0.12))
        preset.setdefault("muted", _tint(settings.text, -0.25))
        preset.setdefault("selection_text", "#ffffff")
        preset.setdefault("disabled", _tint(settings.text, -0.45))
    # Derived soft colors used by the main UI.
    preset["label_bg"] = _tint(preset["panel"], -0.04 if settings.mode == "light" else -0.12)
    preset["soft_border"] = _tint(preset["border"], 0.20 if settings.mode == "dark" else -0.05)
    return preset


def preset_theme(name: str) -> ThemeSettings:
    values = THEME_PRESETS.get(name, THEME_PRESETS["midnight"])
    return ThemeSettings.from_mapping({"preset": name, **values})


def apply_theme(app: QApplication, settings: ThemeSettings) -> None:
    settings = settings.normalized()
    colors = _colors(settings)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(colors["window"]))
    palette.setColor(QPalette.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.Base, QColor(colors["input"]))
    palette.setColor(QPalette.AlternateBase, QColor(colors["panel2"]))
    palette.setColor(QPalette.Text, QColor(colors["text"]))
    palette.setColor(QPalette.Button, QColor(colors["button"]))
    palette.setColor(QPalette.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.Highlight, QColor(colors["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(colors["selection_text"]))
    app.setPalette(palette)
    app.setStyleSheet(build_stylesheet(settings))


def build_stylesheet(settings: ThemeSettings) -> str:
    c = _colors(settings)
    return f"""
        QMainWindow, QDialog, QWidget {{
            background: {c['window']};
            color: {c['text']};
            font-size: 13px;
        }}
        QLabel {{
            background: transparent;
            color: {c['text']};
        }}
        QCheckBox, QRadioButton {{
            background: transparent;
            color: {c['text']};
            spacing: 5px;
        }}
        QLabel#Title {{
            font-size: 22px;
            font-weight: 700;
        }}
        QLabel#Subtitle, QLabel#Muted {{
            color: {c['muted']};
        }}
        QLabel#HeroPanel {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #141414, stop:0.70 {c['panel']});
            color: {c['text']};
            border: 1px solid #e50914;
            border-radius: 14px;
            padding: 14px 18px;
            font-size: 14px;
        }}
        QPushButton#HomeButton {{
            text-align: left;
            padding: 0;
            min-height: 18px;
            border: 0;
            background: transparent;
            color: {c['accent']};
            font-weight: 600;
        }}
        QPushButton#HomeButton:hover {{
            color: {c['text']};
            text-decoration: underline;
        }}
        QFrame#Panel, QGroupBox {{
            background: {c['panel']};
            border: 1px solid {c['border']};
            border-radius: 8px;
        }}
        QGroupBox {{
            margin-top: 7px;
            padding: 8px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
            background: {c['window']};
            color: {c['text']};
            border-radius: 4px;
        }}
        QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTreeWidget {{
            background: {c['input']};
            color: {c['text']};
            border: 1px solid {c['soft_border']};
            border-radius: 6px;
            padding: 5px;
            selection-background-color: {c['accent']};
            selection-color: {c['selection_text']};
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {c['accent']};
        }}
        QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            background: {c['button']};
            border: 0;
            border-left: 1px solid {c['border']};
            width: 22px;
        }}
        QComboBox QAbstractItemView {{
            background: {c['input']};
            color: {c['text']};
            border: 1px solid {c['accent']};
            border-radius: 6px;
            padding: 3px;
            selection-background-color: {c['accent']};
            selection-color: {c['selection_text']};
            outline: 0;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 26px;
            padding: 4px 8px;
            border-radius: 4px;
        }}
        QComboBox#InlineCombo {{
            background: transparent;
            color: {c['text']};
            border: 0;
            border-radius: 5px;
            padding: 2px 24px 2px 4px;
            min-height: 24px;
        }}
        QComboBox#InlineCombo:hover, QComboBox#InlineCombo:focus {{
            background: {c['panel2']};
            border: 1px solid {c['accent']};
            padding: 1px 23px 1px 3px;
        }}
        QComboBox#InlineCombo::drop-down {{
            background: transparent;
            border: 0;
            width: 20px;
        }}
        QTreeWidget {{
            alternate-background-color: {c['panel2']};
            show-decoration-selected: 1;
            outline: 0;
        }}
        QTreeWidget::item {{
            min-height: 30px;
            padding: 2px 4px;
            border: 0;
        }}
        QTreeWidget::item:hover {{
            background: {c['button_hover']};
        }}
        QTreeWidget::item:selected {{
            background: {c['accent']};
            color: {c['selection_text']};
        }}
        QHeaderView::section {{
            background: {c['panel2']};
            color: {c['text']};
            border: 0;
            border-right: 1px solid {c['border']};
            border-bottom: 1px solid {c['border']};
            padding: 6px;
            font-weight: 600;
        }}
        QPushButton, QToolButton {{
            background: {c['button']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 7px 10px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {c['button_hover']};
            border-color: {c['accent']};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background: {c['accent']};
            color: {c['selection_text']};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {c['disabled']};
            background: {c['panel']};
            border-color: {c['border']};
        }}
        QProgressBar {{
            background: {c['input']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            text-align: center;
            min-height: 18px;
            max-height: 20px;
        }}
        QProgressBar::chunk {{
            background: {c['accent']};
            border-radius: 5px;
        }}
        QMenuBar, QMenu {{
            background: {c['panel']};
            color: {c['text']};
            border: 1px solid {c['border']};
        }}
        QMenu::item:selected {{
            background: {c['accent']};
            color: {c['selection_text']};
        }}
        QScrollBar:vertical {{
            background: {c['panel']};
            width: 10px;
            border: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border']};
            border-radius: 5px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {c['accent']};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            border: 0;
        }}
        QSplitter::handle {{
            background: {c['border']};
            margin: 0;
        }}
    """
