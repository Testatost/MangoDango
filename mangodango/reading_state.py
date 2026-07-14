"""Shared reading progress storage for desktop and mobile readers.

The state lives inside the active manga library so both the desktop reader and
LAN mobile reader always see the same progress. Writes are atomic and guarded by
per-file locks because the mobile HTTP server can update progress from several
threads while the Qt reader is open.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

STATE_DIR_NAME = ".mangodango"
STATE_FILE_NAME = "reading_state.json"
STATE_VERSION = 1
_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.RLock())


def stable_id(*parts: object) -> str:
    value = "\0".join(str(part) for part in parts)
    return hashlib.blake2b(value.encode("utf-8", errors="surrogatepass"), digest_size=10).hexdigest()


def manga_id_for_path(path: str | Path) -> str:
    return stable_id(Path(path).expanduser().resolve(strict=False))



def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class ReadingStateStore:
    """Persist reading progress for one library root."""

    def __init__(self, library_root: str | Path) -> None:
        self.library_root = Path(library_root).expanduser()
        self.state_path = self.library_root / STATE_DIR_NAME / STATE_FILE_NAME
        self._lock = _lock_for(self.state_path)
        self._cache: dict[str, Any] | None = None
        self._cache_mtime_ns = -1

    def _empty(self) -> dict[str, Any]:
        return {"version": STATE_VERSION, "mangas": {}}

    def _read_unlocked(self, force: bool = False) -> dict[str, Any]:
        try:
            mtime_ns = self.state_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        if not force and self._cache is not None and mtime_ns == self._cache_mtime_ns:
            return self._cache
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            data = raw if isinstance(raw, dict) else self._empty()
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = self._empty()
        mangas = data.get("mangas")
        if not isinstance(mangas, dict):
            mangas = {}
        data = {"version": STATE_VERSION, "mangas": mangas}
        self._cache = data
        self._cache_mtime_ns = mtime_ns
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.state_path)
        try:
            self._cache_mtime_ns = self.state_path.stat().st_mtime_ns
        except OSError:
            self._cache_mtime_ns = -1
        self._cache = data

    def all_records(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            data = self._read_unlocked()
            return {
                str(key): dict(value)
                for key, value in data["mangas"].items()
                if isinstance(value, dict)
            }

    def get(
        self,
        manga_id: str = "",
        *,
        path: str | Path | None = None,
        title: str = "",
        source_url: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            data = self._read_unlocked()
            records = data["mangas"]
            candidate_ids: list[str] = []
            if manga_id:
                candidate_ids.append(str(manga_id))
            if path is not None:
                candidate_ids.append(manga_id_for_path(path))
            for candidate in candidate_ids:
                value = records.get(candidate)
                if isinstance(value, dict):
                    return dict(value)
            title_fold = str(title or "").strip().casefold()
            url = str(source_url or "").strip()
            for value in records.values():
                if not isinstance(value, dict):
                    continue
                if url and str(value.get("source_url") or "").strip() == url:
                    return dict(value)
                if title_fold and str(value.get("title") or "").strip().casefold() == title_fold:
                    return dict(value)
            return None

    def update_progress(
        self,
        *,
        manga_id: str,
        title: str,
        source_url: str = "",
        path: str | Path | None = None,
        chapter_id: str = "",
        chapter_title: str = "",
        page_index: int = 0,
        global_index: int | None = None,
        chapter_pages: int = 0,
        total_pages: int = 0,
        reader_mode: str = "",
        reached_latest_page: bool = False,
        series_complete: bool = False,
        updated_at: float | None = None,
    ) -> dict[str, Any]:
        key = str(manga_id or (manga_id_for_path(path) if path is not None else stable_id(title, source_url)))
        now = float(updated_at if updated_at is not None else time.time())
        with self._lock:
            data = self._read_unlocked(force=True)
            records = data["mangas"]
            existing = records.get(key)
            record = dict(existing) if isinstance(existing, dict) else {}
            # Reading status was intentionally removed. Preserve only progress
            # fields; old ``status`` values in existing files are ignored.
            record.pop("status", None)
            record.update({
                "manga_id": key,
                "title": str(title or ""),
                "source_url": str(source_url or ""),
                "path": str(Path(path).expanduser().resolve(strict=False)) if path is not None else str(record.get("path") or ""),
                "chapter_id": str(chapter_id or ""),
                "chapter_title": str(chapter_title or ""),
                "page_index": max(0, _to_int(page_index)),
                "global_index": None if global_index is None else max(0, _to_int(global_index)),
                "chapter_pages": max(0, _to_int(chapter_pages)),
                "total_pages": max(0, _to_int(total_pages)),
                "reader_mode": str(reader_mode or record.get("reader_mode") or ""),
                "updated_at": now,
            })
            records[key] = record
            self._write_unlocked(data)
            return dict(record)


    def migrate(self, old_manga_id: str, new_manga_id: str, *, title: str = "", path: str | Path | None = None) -> None:
        old_key = str(old_manga_id or "")
        new_key = str(new_manga_id or "")
        if not old_key or not new_key or old_key == new_key:
            return
        with self._lock:
            data = self._read_unlocked(force=True)
            old = data["mangas"].pop(old_key, None)
            if isinstance(old, dict):
                record = dict(old)
                record["manga_id"] = new_key
                if title:
                    record["title"] = title
                if path is not None:
                    record["path"] = str(Path(path).expanduser().resolve(strict=False))
                data["mangas"][new_key] = record
                self._write_unlocked(data)

    def remove(self, manga_id: str) -> None:
        with self._lock:
            data = self._read_unlocked(force=True)
            if data["mangas"].pop(str(manga_id or ""), None) is not None:
                self._write_unlocked(data)

    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        records = list(self.all_records().values())
        records = [record for record in records if _to_float(record.get("updated_at")) > 0]
        records.sort(key=lambda item: -_to_float(item.get("updated_at")))
        return records[: max(0, int(limit))]

    def clear_missing(self, valid_manga_ids: set[str]) -> int:
        with self._lock:
            data = self._read_unlocked(force=True)
            before = len(data["mangas"])
            data["mangas"] = {
                key: value for key, value in data["mangas"].items()
                if key in valid_manga_ids
            }
            removed = before - len(data["mangas"])
            if removed:
                self._write_unlocked(data)
            return removed
