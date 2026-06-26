from __future__ import annotations

import re

from .languages import (
    bg,
    cs,
    da,
    de,
    el,
    en,
    es,
    et,
    fi,
    fr,
    ga,
    hr,
    hu,
    it,
    lt,
    lv,
    mt,
    nl,
    pl,
    pt,
    ro,
    sk,
    sl,
    sv,
)

# Do not discover language modules dynamically with pkgutil here.
# In PyInstaller one-file builds, dynamic package discovery can return an
# empty result because modules are loaded from the bundled archive.
# Explicit imports make the language tables reliable in source and frozen builds.
_LANGUAGE_MODULES = {
    "bg": bg,
    "cs": cs,
    "da": da,
    "de": de,
    "el": el,
    "en": en,
    "es": es,
    "et": et,
    "fi": fi,
    "fr": fr,
    "ga": ga,
    "hr": hr,
    "hu": hu,
    "it": it,
    "lt": lt,
    "lv": lv,
    "mt": mt,
    "nl": nl,
    "pl": pl,
    "pt": pt,
    "ro": ro,
    "sk": sk,
    "sl": sl,
    "sv": sv,
}

LANGUAGES: dict[str, dict[str, str]] = {
    code: table
    for code, module in _LANGUAGE_MODULES.items()
    if isinstance((table := getattr(module, "TEXT", None)), dict)
}

if "en" not in LANGUAGES:
    LANGUAGES["en"] = {}

LANGUAGE_ALIASES = {
    "cz": "cs",
    "gr": "el",
    "dk": "da",
    "ee": "et",
    "se": "sv",
    "si": "sl",
    "gb": "en",
    "ie": "ga",
}
SUPPORTED_LANGUAGES = tuple(sorted(LANGUAGES.keys()))


def normalize_language(code: str | None) -> str:
    value = str(code or "").strip().lower()
    value = re.split(r"[-_]", value)[0]
    value = LANGUAGE_ALIASES.get(value, value)
    return value if value in LANGUAGES else "en"


def language_label(code: str, current_language: str | None = None) -> str:
    normalized = normalize_language(code)
    return LANGUAGES.get(normalized, LANGUAGES["en"]).get("language_label", normalized.upper())


class Translator:
    def __init__(self, language: str = "en") -> None:
        self.language = normalize_language(language)

    def set_language(self, language: str) -> None:
        self.language = normalize_language(language)

    def tr(self, key: str, **kwargs) -> str:
        table = LANGUAGES.get(self.language, LANGUAGES["en"])
        fallback = LANGUAGES["en"]
        text = table.get(key, fallback.get(key, key))
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text
