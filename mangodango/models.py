from __future__ import annotations

from dataclasses import asdict, dataclass, field
from uuid import uuid4

from .constants import IMAGE_FORMATS, OUTPUT_MODES, READING_STYLES


def _new_id() -> str:
    return uuid4().hex


def safe_choice(value: str, choices: tuple[str, ...], default: str) -> str:
    return value if value in choices else default


@dataclass
class ItemSettings:
    reading_style: str = "long_strip"
    output_mode: str = "images"
    image_format: str = "original"
    keep_images: bool = True
    image_threads: int = 4
    request_delay: float = 1.0

    @classmethod
    def from_dict(cls, data: dict | None) -> "ItemSettings":
        data = data or {}
        return cls(
            reading_style=safe_choice(str(data.get("reading_style", "long_strip")), READING_STYLES, "long_strip"),
            output_mode=safe_choice(str(data.get("output_mode", "images")), OUTPUT_MODES, "images"),
            image_format=safe_choice(str(data.get("image_format", "original")), IMAGE_FORMATS, "original"),
            keep_images=bool(data.get("keep_images", True)),
            image_threads=max(1, min(10, int(data.get("image_threads", 4) or 4))),
            request_delay=max(0.0, min(30.0, float(data.get("request_delay", 1.0) or 0.0))),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def clone(self) -> "ItemSettings":
        return ItemSettings.from_dict(self.to_dict())

    @property
    def create_cbz(self) -> bool:
        return self.output_mode in {"cbz", "images_cbz", "cbz_pdf", "images_cbz_pdf"}

    @property
    def create_pdf(self) -> bool:
        return self.output_mode in {"pdf", "images_pdf", "cbz_pdf", "images_cbz_pdf"}

    @property
    def preserve_images(self) -> bool:
        if self.output_mode == "images":
            return True
        return self.keep_images or self.output_mode.startswith("images")


@dataclass
class ChapterEntry:
    title: str
    url: str
    settings: ItemSettings = field(default_factory=ItemSettings)
    enabled: bool = True
    status: str = "pending"
    progress_text: str = ""
    eta_text: str = ""
    item_id: str = field(default_factory=_new_id)

    @classmethod
    def from_dict(cls, data: dict) -> "ChapterEntry":
        return cls(
            title=str(data.get("title", "")).strip() or "Chapter",
            url=str(data.get("url", "")).strip(),
            settings=ItemSettings.from_dict(data.get("settings")),
            enabled=bool(data.get("enabled", True)),
            status=str(data.get("status", "pending") or "pending"),
            progress_text=str(data.get("progress_text", "") or ""),
            eta_text=str(data.get("eta_text", "") or ""),
            item_id=str(data.get("item_id", "")).strip() or _new_id(),
        )

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "url": self.url,
            "settings": self.settings.to_dict(),
            "enabled": self.enabled,
            "status": self.status,
            "progress_text": self.progress_text,
            "eta_text": self.eta_text,
        }


@dataclass
class MangaEntry:
    title: str
    url: str
    chapters: list[ChapterEntry] = field(default_factory=list)
    settings: ItemSettings = field(default_factory=ItemSettings)
    enabled: bool = True
    status: str = "pending"
    progress_text: str = ""
    eta_text: str = ""
    item_id: str = field(default_factory=_new_id)

    @classmethod
    def from_dict(cls, data: dict) -> "MangaEntry":
        return cls(
            title=str(data.get("title", "")).strip() or "Manga",
            url=str(data.get("url", "")).strip(),
            chapters=[ChapterEntry.from_dict(item) for item in data.get("chapters", [])],
            settings=ItemSettings.from_dict(data.get("settings")),
            enabled=bool(data.get("enabled", True)),
            status=str(data.get("status", "pending") or "pending"),
            progress_text=str(data.get("progress_text", "") or ""),
            eta_text=str(data.get("eta_text", "") or ""),
            item_id=str(data.get("item_id", "")).strip() or _new_id(),
        )

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "url": self.url,
            "settings": self.settings.to_dict(),
            "enabled": self.enabled,
            "status": self.status,
            "progress_text": self.progress_text,
            "eta_text": self.eta_text,
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }

    def merge_chapters(self, chapters: list[ChapterEntry]) -> int:
        seen = {chapter.url for chapter in self.chapters}
        added = 0
        for chapter in chapters:
            if chapter.url in seen:
                continue
            self.chapters.append(chapter)
            seen.add(chapter.url)
            added += 1
        return added
