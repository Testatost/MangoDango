"""GUI-free orchestration shared by the Qt workers and the headless server.

Keeping the update-check candidate collection and a plain synchronous download
loop here means the desktop app and the server binary run the exact same logic.
Nothing in this module imports Qt widgets, so it is safe to use on a headless
machine without a display.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .models import ChapterEntry, ItemSettings, MangaEntry
from .scraper import (
    WeebCentralClient,
    chapter_exists_on_disk,
    read_manga_metadata,
    read_manga_metadata_dir,
    write_manga_metadata_dir,
    sanitize_filename,
    write_manga_metadata,
)

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[float, str], None]
StopCallback = Callable[[], bool]


def _noop_log(_message: str) -> None:
    return None


def _flag(value, default: bool = True) -> bool:
    """Interpret a stored flag; a missing flag defaults to ``default`` so that
    older downloads keep their previous (check-everything) behaviour."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return bool(value)


def manga_wants_update_check(output_dir: str | Path, title: str) -> bool:
    return _flag(read_manga_metadata(output_dir, title).get("check_updates"), True)


def manga_wants_auto_download(output_dir: str | Path, title: str, url: str = "") -> bool:
    data = read_manga_metadata(output_dir, title)
    return _flag(data.get("auto_download"), True)


def metadata_files(output_dir: str | Path) -> list[Path]:
    base = Path(output_dir)
    if not base.exists():
        return []
    return sorted(item for item in base.rglob(".mangodango.json") if item.is_file())


def collect_update_candidates(
    output_dir: str | Path,
    known_mangas: list[MangaEntry] | None = None,
    client: "WeebCentralClient | None" = None,
    log: LogCallback | None = None,
    tr=None,
    only_titles: set[str] | None = None,
    include_disabled: bool = False,
) -> list[tuple[str, str]]:
    """Return ``(title, url)`` pairs to check for new chapters.

    Candidate sources, in order:
      1. ``.mangodango.json`` metadata whose ``check_updates`` flag is enabled.
      2. Queue entries whose folder exists on disk.
      3. Downloaded folders that have chapters but no usable metadata URL — the
         series URL is recovered from the source by searching for the folder
         name, and the metadata is backfilled so the next run is instant.
    """
    log = log or _noop_log
    tr = tr or (lambda key, **kwargs: key)
    selected_titles = (
        {str(title).strip().casefold() for title in only_titles if str(title).strip()}
        if only_titles is not None
        else None
    )

    def title_is_selected(title: str) -> bool:
        return selected_titles is None or title.casefold() in selected_titles

    base = Path(output_dir)
    folder_dirs = [item for item in base.iterdir() if item.is_dir()] if base.exists() else []
    folder_names = {item.name for item in folder_dirs}
    candidates: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    covered_dirs: set[str] = set()

    for path in metadata_files(base):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            title = str(data.get("title", "") or path.parent.name)
            url = str(data.get("url", "") or "").strip()
        except Exception:
            continue
        covered_dirs.add(str(path.parent))
        if not title_is_selected(title):
            continue
        if not include_disabled and not _flag(data.get("check_updates"), True):
            continue
        if url and url not in seen_urls:
            seen_urls.add(url)
            candidates.append((title, url))

    for manga in known_mangas or []:
        if not title_is_selected(manga.title):
            continue
        if not manga.url or manga.url in seen_urls:
            continue
        sanitized = sanitize_filename(manga.title, manga.title)
        if not folder_names or sanitized in folder_names or manga.title in folder_names:
            if not include_disabled and not manga_wants_update_check(base, manga.title):
                continue
            seen_urls.add(manga.url)
            candidates.append((manga.title, manga.url))
            matched = base / sanitized
            covered_dirs.add(str(matched if matched.exists() else base / manga.title))

    # Folders that have downloaded chapters but no usable URL yet.
    if client is not None:
        for manga_dir in folder_dirs:
            if str(manga_dir) in covered_dirs:
                continue
            if not _dir_has_chapters(manga_dir):
                continue
            data = read_manga_metadata_dir(manga_dir)
            title = str(data.get("title") or manga_dir.name)
            if not title_is_selected(title):
                continue
            if not include_disabled and not _flag(data.get("check_updates"), True):
                continue
            log(tr("updates_recovering", title=title))
            try:
                url = client.recover_series_url(title)
            except Exception:
                url = ""
            if not url or url in seen_urls:
                continue
            # Backfill metadata so future checks skip the search step.
            data.setdefault("app", "MangoDango")
            data["title"] = title
            data["url"] = url
            data.setdefault("check_updates", True)
            data.setdefault("auto_download", True)
            try:
                write_manga_metadata_dir(manga_dir, data)
            except Exception:
                pass
            seen_urls.add(url)
            candidates.append((title, url))

    return candidates


def _dir_has_chapters(manga_dir: Path) -> bool:
    try:
        for item in manga_dir.iterdir():
            if item.is_dir():
                return True
            if item.is_file() and item.suffix.lower() in {".cbz", ".pdf"}:
                return True
    except Exception:
        pass
    return False



def find_new_chapters(
    client: WeebCentralClient,
    output_dir: str | Path,
    url: str,
    defaults: ItemSettings,
    local_title: str = "",
) -> MangaEntry | None:
    """Resolve ``url`` and return a MangaEntry containing only new chapters.

    Returns ``None`` when the manga has no chapters missing on disk.
    """
    manga = client.resolve(url, defaults)
    # Keep the local library name as the storage identity. The title returned by
    # WeebCentral may differ after the user renamed the manga, and using the
    # remote title here would make every existing chapter appear missing.
    if local_title:
        manga.title = str(local_title).strip() or manga.title
    new_chapters: list[ChapterEntry] = []
    for chapter in manga.chapters:
        if not chapter_exists_on_disk(output_dir, manga.title, chapter.title):
            chapter.enabled = True
            chapter.status = "pending"
            new_chapters.append(chapter)
    if not new_chapters:
        return None
    manga.chapters = new_chapters
    manga.status = "ready"
    manga.enabled = True
    return manga


def check_for_updates(
    output_dir: str | Path,
    defaults: ItemSettings,
    tr,
    known_mangas: list[MangaEntry] | None = None,
    log: LogCallback | None = None,
    stop: StopCallback | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[MangaEntry]:
    """Synchronously check every candidate and return mangas with new chapters."""
    log = log or _noop_log
    stop = stop or (lambda: False)
    client = WeebCentralClient(tr=tr, log=log, stop=stop)
    candidates = collect_update_candidates(output_dir, known_mangas, client=client, log=log, tr=tr)
    if not candidates:
        log(tr("updates_no_metadata"))
        return []

    result: list[MangaEntry] = []
    total = len(candidates)
    for index, (title, url) in enumerate(candidates, start=1):
        if stop():
            break
        if progress:
            progress(tr("updates_checking_manga", title=title), index, total)
        try:
            manga = find_new_chapters(client, output_dir, url, defaults, local_title=title)
            if manga is not None:
                result.append(manga)
                log(tr("updates_found_for_manga", title=manga.title, count=len(manga.chapters)))
            else:
                log(tr("updates_none_for_manga", title=title))
        except Exception as exc:
            log(tr("updates_check_failed", title=title, error=exc))
    return result


def download_mangas(
    mangas: list[MangaEntry],
    output_dir: str | Path,
    tr,
    log: LogCallback | None = None,
    stop: StopCallback | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[bool, bool]:
    """Plain synchronous download of every enabled chapter.

    Returns ``(all_ok, stopped)``. Used by the headless server; the GUI keeps
    its own richer worker so it can report per-row ETA and status.
    """
    log = log or _noop_log
    stop = stop or (lambda: False)
    progress = progress or (lambda value, text: None)

    tasks: list[tuple[MangaEntry, ChapterEntry]] = []
    for manga in mangas:
        if not manga.enabled:
            continue
        for chapter in manga.chapters:
            if chapter.enabled and chapter.status != "done":
                tasks.append((manga, chapter))

    if not tasks:
        log(tr("log_no_active_chapters"))
        return True, False

    client = WeebCentralClient(tr=tr, log=log, stop=stop)
    total = len(tasks)
    all_ok = True
    stopped = False
    current_manga_id = ""

    for index, (manga, chapter) in enumerate(tasks, start=1):
        if stop():
            stopped = True
            break
        if manga.item_id != current_manga_id:
            current_manga_id = manga.item_id
            log(tr("log_download_manga", title=manga.title))
            try:
                write_manga_metadata(output_dir, manga.title, manga.url)
            except Exception as exc:
                log(tr("metadata_write_failed", title=manga.title, error=exc))

        if chapter_exists_on_disk(output_dir, manga.title, chapter.title):
            chapter.status = "done"
            log(tr("log_existing_chapter_skipped", manga=manga.title, chapter=chapter.title))
            continue

        try:
            ok = client.download_chapter(
                manga_title=manga.title,
                chapter=chapter,
                output_dir=str(output_dir),
                chapter_index=index,
                total_chapters=total,
                progress=progress,
            )
            if stop():
                stopped = True
                chapter.status = "stopped"
                break
            chapter.status = "done" if ok else "warning"
            all_ok = all_ok and ok
        except Exception as exc:
            if stop():
                stopped = True
                chapter.status = "stopped"
                break
            all_ok = False
            chapter.status = "failed"
            log(str(exc))

    return all_ok and not stopped, stopped
