from __future__ import annotations

import time
from copy import deepcopy

from PySide6.QtCore import QThread, Signal

from . import engine
from .i18n import Translator, tr_message
from .models import ChapterEntry, ItemSettings, MangaEntry
from . import __version__
from .updater import cleanup_download, download_release, fetch_latest_release, is_newer_version
from .scraper import (
    WeebCentralClient,
    chapter_exists_on_disk,
    write_manga_metadata,
)


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"




class AppUpdateWorker(QThread):
    """Check GitHub for a newer release and download it without blocking the UI."""

    status_changed = Signal(str)
    progress_changed = Signal(int)
    update_ready = Signal(object, str)
    no_update = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            self.status_changed.emit("checking")
            release = fetch_latest_release(__version__)
            if self._stop_requested:
                return
            if not is_newer_version(release.version, __version__):
                self.no_update.emit(release.version)
                return

            self.status_changed.emit("downloading")
            package = download_release(
                release,
                progress=self.progress_changed.emit,
                stop=lambda: self._stop_requested,
            )
            if self._stop_requested:
                cleanup_download(package)
                return
            self.update_ready.emit(release, str(package))
        except Exception:
            if not self._stop_requested:
                self.failed.emit("update_error_generic")


class ResolveWorker(QThread):
    log_message = Signal(object)
    progress_message = Signal(str, int, int)
    resolved = Signal(object)
    failed = Signal(str, object)

    def __init__(self, urls: list[str], defaults: ItemSettings, language: str, parent=None) -> None:
        super().__init__(parent)
        self.urls = urls
        self.defaults = defaults
        self.translator = Translator(language)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        client = WeebCentralClient(tr=self.translator.tr, log=self.log_message.emit, stop=lambda: self._stop_requested)
        total = len(self.urls)
        for index, url in enumerate(self.urls, start=1):
            if self._stop_requested:
                break
            self.progress_message.emit(self.translator.tr("collector_current", url=url), index, total)
            self.log_message.emit(tr_message("log_add_url", url=url))
            try:
                manga = client.resolve(url, self.defaults)
                self.resolved.emit(manga)
            except Exception as exc:
                error = str(exc)
                error_value = tr_message(error) if error.startswith("error_") else error
                self.failed.emit(url, tr_message("resolve_failed", error=error_value))


class UpdateCheckWorker(QThread):
    log_message = Signal(object)
    progress_message = Signal(str, int, int)
    updates_found = Signal(object)

    def __init__(
        self,
        output_dir: str,
        defaults: ItemSettings,
        language: str,
        known_mangas: list[MangaEntry] | None = None,
        parent=None,
        candidate_titles: set[str] | None = None,
        include_disabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self.output_dir = output_dir
        self.defaults = defaults
        self.known_mangas = deepcopy(known_mangas or [])
        self.candidate_titles = (
            {str(title).strip().casefold() for title in candidate_titles if str(title).strip()}
            if candidate_titles is not None
            else None
        )
        self.include_disabled = bool(include_disabled)
        self.translator = Translator(language)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _update_candidates(self) -> list[tuple[str, str]]:
        return engine.collect_update_candidates(
            self.output_dir,
            self.known_mangas,
            only_titles=self.candidate_titles,
            include_disabled=self.include_disabled,
        )

    def run(self) -> None:
        client = WeebCentralClient(tr=self.translator.tr, log=self.log_message.emit, stop=lambda: self._stop_requested)
        candidates = engine.collect_update_candidates(
            self.output_dir,
            self.known_mangas,
            client=client,
            log=self.log_message.emit,
            tr=lambda key, **kwargs: tr_message(key, **kwargs),
            only_titles=self.candidate_titles,
            include_disabled=self.include_disabled,
        )
        if not candidates:
            self.log_message.emit(tr_message("updates_no_metadata"))
            self.updates_found.emit([])
            return

        result: list[MangaEntry] = []
        total = len(candidates)

        for index, (title, url) in enumerate(candidates, start=1):
            if self._stop_requested:
                break
            self.progress_message.emit(self.translator.tr("updates_checking_manga", title=title), index, total)
            try:
                manga = engine.find_new_chapters(client, self.output_dir, url, self.defaults, local_title=title)
                if manga is not None:
                    result.append(manga)
                    self.log_message.emit(tr_message("updates_found_for_manga", title=manga.title, count=len(manga.chapters)))
                else:
                    self.log_message.emit(tr_message("updates_none_for_manga", title=title))
            except Exception as exc:
                self.log_message.emit(tr_message("updates_check_failed", title=title, error=exc))

        self.updates_found.emit(result)


class QueueDownloadWorker(QThread):
    log_message = Signal(object)
    chapter_status = Signal(str, str, str)
    manga_status = Signal(str, str)
    chapter_progress = Signal(str, str, str, str)
    manga_progress = Signal(str, str, str)
    global_progress = Signal(float, str)
    finished_signal = Signal(bool, bool)

    def __init__(self, mangas: list[MangaEntry], output_dir: str, language: str, parent=None, skip_done: bool = False) -> None:
        super().__init__(parent)
        self.mangas = deepcopy(mangas)
        self.output_dir = output_dir
        self.translator = Translator(language)
        self.skip_done = skip_done
        self._stop_requested = False
        self._manga_start_times: dict[str, float] = {}
        self._manga_done_counts: dict[str, float] = {}
        self._manga_total_counts: dict[str, int] = {}
        self._chapter_eta_seconds: dict[str, float] = {}
        self._manga_eta_seconds: dict[str, float] = {}
        self._chapter_eta_text: dict[str, str] = {}
        self._manga_eta_text: dict[str, str] = {}

    def stop(self) -> None:
        self._stop_requested = True

    def _enabled_chapters(self) -> list[tuple[MangaEntry, ChapterEntry]]:
        result: list[tuple[MangaEntry, ChapterEntry]] = []
        for manga in self.mangas:
            if not manga.enabled:
                continue
            for chapter in manga.chapters:
                if chapter.enabled:
                    if self.skip_done and chapter.status == "done":
                        continue
                    result.append((manga, chapter))
        return result

    def _prepare_manga_totals(self, tasks: list[tuple[MangaEntry, ChapterEntry]]) -> None:
        self._manga_total_counts.clear()
        self._manga_done_counts.clear()
        self._chapter_eta_seconds.clear()
        self._manga_eta_seconds.clear()
        self._chapter_eta_text.clear()
        self._manga_eta_text.clear()
        for manga, _chapter in tasks:
            self._manga_total_counts[manga.item_id] = self._manga_total_counts.get(manga.item_id, 0) + 1
            self._manga_done_counts.setdefault(manga.item_id, 0.0)

    def _stable_eta(self, cache: dict[str, float], text_cache: dict[str, str], key: str, estimate: float | None) -> str:
        """Return a stable ETA string without flickering to empty between updates."""
        if estimate is None or estimate <= 0 or estimate == float("inf"):
            return text_cache.get(key, "…")

        previous = cache.get(key)
        if previous is None:
            smoothed = estimate
        else:
            # Exponential smoothing avoids jumpy ETA changes when a single page is slow.
            smoothed = (previous * 0.72) + (estimate * 0.28)

        cache[key] = smoothed
        text = _format_eta(smoothed)
        if text:
            text_cache[key] = text
        return text_cache.get(key, text or "…")

    def _emit_download_progress(self, manga: MangaEntry, chapter: ChapterEntry, value: float, text: str, chapter_start: float) -> None:
        # Text from the scraper is usually "Chapter X: 7/19".
        progress_text = ""
        fraction = 0.0
        match = None
        try:
            import re
            match = re.search(r"(\d+)\s*/\s*(\d+)", text)
        except Exception:
            match = None
        if match:
            done = int(match.group(1))
            total = max(1, int(match.group(2)))
            fraction = min(1.0, done / total)
            progress_text = f"{done}/{total}"
        else:
            progress_text = text

        elapsed = max(0.0, time.monotonic() - chapter_start)
        chapter_estimate = (elapsed / fraction) * (1.0 - fraction) if fraction > 0 else None
        chapter_eta = self._stable_eta(self._chapter_eta_seconds, self._chapter_eta_text, chapter.item_id, chapter_estimate)
        self.chapter_progress.emit(manga.item_id, chapter.item_id, progress_text, chapter_eta)

        total_chapters = max(1, self._manga_total_counts.get(manga.item_id, 1))
        done_before = self._manga_done_counts.get(manga.item_id, 0.0)
        manga_fraction = min(1.0, (done_before + fraction) / total_chapters)
        manga_progress = f"{min(total_chapters, int(done_before + fraction))}/{total_chapters}"
        manga_elapsed = max(0.0, time.monotonic() - self._manga_start_times.get(manga.item_id, time.monotonic()))
        manga_estimate = (manga_elapsed / manga_fraction) * (1.0 - manga_fraction) if manga_fraction > 0 else None
        manga_eta = self._stable_eta(self._manga_eta_seconds, self._manga_eta_text, manga.item_id, manga_estimate)
        self.manga_progress.emit(manga.item_id, manga_progress, manga_eta)
        self.global_progress.emit(value, text)

    def run(self) -> None:
        tasks = self._enabled_chapters()
        if not tasks:
            self.log_message.emit(tr_message("queue_empty"))
            self.finished_signal.emit(False, False)
            return

        self._prepare_manga_totals(tasks)
        client = WeebCentralClient(tr=self.translator.tr, log=self.log_message.emit, stop=lambda: self._stop_requested)
        total = len(tasks)
        all_ok = True
        stopped = False
        current_manga_id = ""

        for index, (manga, chapter) in enumerate(tasks, start=1):
            if self._stop_requested:
                stopped = True
                break

            if manga.item_id != current_manga_id:
                current_manga_id = manga.item_id
                self._manga_start_times[manga.item_id] = time.monotonic()
                self.manga_status.emit(manga.item_id, "running")
                self.log_message.emit(tr_message("log_download_manga", title=manga.title))
                try:
                    write_manga_metadata(self.output_dir, manga.title, manga.url)
                except Exception as exc:
                    self.log_message.emit(tr_message("metadata_write_failed", title=manga.title, error=exc))

            if chapter_exists_on_disk(self.output_dir, manga.title, chapter.title):
                chapter.status = "done"
                chapter.progress_text = self.translator.tr("download_present")
                chapter.eta_text = ""
                self.chapter_status.emit(manga.item_id, chapter.item_id, "done")
                self.chapter_progress.emit(manga.item_id, chapter.item_id, chapter.progress_text, "")
                self._manga_done_counts[manga.item_id] = self._manga_done_counts.get(manga.item_id, 0.0) + 1.0
                total_manga = max(1, self._manga_total_counts.get(manga.item_id, 1))
                self.manga_progress.emit(
                    manga.item_id,
                    f"{int(self._manga_done_counts[manga.item_id])}/{total_manga}",
                    self._manga_eta_text.get(manga.item_id, ""),
                )
                self.log_message.emit(tr_message("log_existing_chapter_skipped", manga=manga.title, chapter=chapter.title))
                self._update_manga_status(manga)
                continue

            self.chapter_status.emit(manga.item_id, chapter.item_id, "running")
            chapter_start = time.monotonic()
            try:
                ok = client.download_chapter(
                    manga_title=manga.title,
                    chapter=chapter,
                    output_dir=self.output_dir,
                    chapter_index=index,
                    total_chapters=total,
                    progress=lambda value, text, m=manga, c=chapter, start=chapter_start: self._emit_download_progress(m, c, value, text, start),
                )
                if self._stop_requested:
                    stopped = True
                    chapter.status = "stopped"
                    self.chapter_status.emit(manga.item_id, chapter.item_id, "stopped")
                    break
                if not ok and self._stop_requested:
                    stopped = True
                    chapter.status = "stopped"
                    self.chapter_status.emit(manga.item_id, chapter.item_id, "stopped")
                    break
                chapter.status = "done" if ok else "warning"
                chapter.progress_text = "100%" if ok else chapter.progress_text
                chapter.eta_text = ""
                self.chapter_status.emit(manga.item_id, chapter.item_id, chapter.status)
                self.chapter_progress.emit(manga.item_id, chapter.item_id, chapter.progress_text or "100%", "")
                self._manga_done_counts[manga.item_id] = self._manga_done_counts.get(manga.item_id, 0.0) + 1.0
                total_manga = max(1, self._manga_total_counts.get(manga.item_id, 1))
                manga_eta = "" if int(self._manga_done_counts[manga.item_id]) >= total_manga else self._manga_eta_text.get(manga.item_id, "")
                self.manga_progress.emit(manga.item_id, f"{int(self._manga_done_counts[manga.item_id])}/{total_manga}", manga_eta)
                all_ok = all_ok and ok
            except Exception as exc:
                if self._stop_requested:
                    stopped = True
                    chapter.status = "stopped"
                    chapter.eta_text = ""
                    self.chapter_status.emit(manga.item_id, chapter.item_id, "stopped")
                    self.log_message.emit(tr_message("download_stopped"))
                    break
                all_ok = False
                chapter.status = "failed"
                chapter.eta_text = ""
                self.chapter_status.emit(manga.item_id, chapter.item_id, "failed")
                self.log_message.emit(str(exc))
            self._update_manga_status(manga)

        if stopped:
            for manga, chapter in tasks[index - 1:]:
                if chapter.status == "running":
                    chapter.status = "stopped"
                    self.chapter_status.emit(manga.item_id, chapter.item_id, "stopped")
        self.global_progress.emit(100.0, self.translator.tr("download_stopped" if stopped else "download_finished"))
        self.finished_signal.emit(all_ok and not stopped, stopped)

    def _update_manga_status(self, manga: MangaEntry) -> None:
        statuses = [chapter.status for chapter in manga.chapters if chapter.enabled]
        if not statuses:
            manga.status = "skipped"
            self.manga_status.emit(manga.item_id, manga.status)
            return
        if all(status == "done" for status in statuses):
            manga.status = "done"
        elif any(status == "failed" for status in statuses):
            manga.status = "warning"
        elif any(status == "warning" for status in statuses):
            manga.status = "warning"
        elif any(status == "running" for status in statuses):
            manga.status = "running"
        else:
            manga.status = "pending"
        self.manga_status.emit(manga.item_id, manga.status)
