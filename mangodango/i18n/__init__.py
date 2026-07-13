from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from string import Formatter
from typing import Any

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


@dataclass(frozen=True)
class TranslatableText:
    """Translation key plus arguments that can be rendered again after a language change."""

    key: str
    kwargs: dict[str, Any] = field(default_factory=dict)


def tr_message(key: str, **kwargs: Any) -> TranslatableText:
    return TranslatableText(str(key), dict(kwargs))




@lru_cache(maxsize=None)
def _translation_matchers(language: str) -> tuple[dict[str, str], tuple[tuple[str, re.Pattern[str]], ...]]:
    table = LANGUAGES.get(normalize_language(language), LANGUAGES["en"])
    exact: dict[str, str] = {}
    patterns: list[tuple[str, re.Pattern[str]]] = []
    formatter = Formatter()
    for key, template in table.items():
        if not isinstance(template, str):
            continue
        parts = list(formatter.parse(template))
        fields = [field for _literal, field, _spec, _conversion in parts if field]
        if not fields:
            exact.setdefault(template, key)
            continue
        regex_parts: list[str] = []
        seen: set[str] = set()
        valid = True
        for literal, field, _spec, _conversion in parts:
            regex_parts.append(re.escape(literal))
            if not field:
                continue
            if not str(field).isidentifier():
                valid = False
                break
            if field in seen:
                regex_parts.append(f"(?P={field})")
            else:
                regex_parts.append(f"(?P<{field}>.*?)")
                seen.add(field)
        if valid:
            try:
                patterns.append((key, re.compile("^" + "".join(regex_parts) + "$", re.DOTALL)))
            except re.error:
                pass
    return exact, tuple(patterns)


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

    def render(self, value: Any) -> str:
        if isinstance(value, TranslatableText):
            return self.tr(value.key, **value.kwargs)
        return str(value)

    def identify(self, value: Any) -> TranslatableText | None:
        """Recover a translation key from text rendered in the active language.

        This lets the GUI keep a language-neutral log history even when older
        call sites still pass ``tr(...)`` results instead of ``tr_message(...)``.
        """
        if isinstance(value, TranslatableText):
            return value
        text = str(value)
        exact, patterns = _translation_matchers(self.language)
        key = exact.get(text)
        if key is not None:
            return tr_message(key)
        for candidate, pattern in patterns:
            match = pattern.fullmatch(text)
            if match is not None:
                return tr_message(candidate, **match.groupdict())
        return None

    def tr(self, key: str, **kwargs) -> str:
        table = LANGUAGES.get(self.language, LANGUAGES["en"])
        fallback = LANGUAGES["en"]
        text = table.get(key, fallback.get(key, key))
        if kwargs:
            rendered = {name: self.render(value) for name, value in kwargs.items()}
            try:
                return text.format(**rendered)
            except Exception:
                return text
        return text
