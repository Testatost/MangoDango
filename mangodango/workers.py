from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QThread, Signal

from .i18n import Translator
from .models import ItemSettings, MangaEntry
from .scraper import WeebCentralClient


class ResolveWorker(QThread):
    log_message = Signal(str)
    resolved = Signal(object)
    failed = Signal(str, str)
    finished_signal = Signal()

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
        for url in self.urls:
            if self._stop_requested:
                break
            self.log_message.emit(self.translator.tr("log_add_url", url=url))
            try:
                manga = client.resolve(url, self.defaults)
                self.resolved.emit(manga)
            except Exception as exc:
                error = str(exc)
                if error.startswith("error_"):
                    error = self.translator.tr(error)
                self.failed.emit(url, self.translator.tr("resolve_failed", error=error))
        self.finished_signal.emit()


class QueueDownloadWorker(QThread):
    log_message = Signal(str)
    chapter_status = Signal(str, str, str)
    manga_status = Signal(str, str)
    global_progress = Signal(float, str)
    finished_signal = Signal(bool, bool)

    def __init__(self, mangas: list[MangaEntry], output_dir: str, language: str, parent=None, skip_done: bool = False) -> None:
        super().__init__(parent)
        self.mangas = deepcopy(mangas)
        self.output_dir = output_dir
        self.translator = Translator(language)
        self.skip_done = skip_done
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _enabled_chapters(self) -> list[tuple[MangaEntry, object]]:
        result = []
        for manga in self.mangas:
            if not manga.enabled:
                continue
            for chapter in manga.chapters:
                if chapter.enabled:
                    if self.skip_done and chapter.status == "done":
                        continue
                    result.append((manga, chapter))
        return result

    def run(self) -> None:
        tasks = self._enabled_chapters()
        if not tasks:
            self.log_message.emit(self.translator.tr("queue_empty"))
            self.finished_signal.emit(False, False)
            return
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
                self.manga_status.emit(manga.item_id, "running")
                self.log_message.emit(self.translator.tr("log_download_manga", title=manga.title))
            self.chapter_status.emit(manga.item_id, chapter.item_id, "running")
            try:
                ok = client.download_chapter(
                    manga_title=manga.title,
                    chapter=chapter,
                    output_dir=self.output_dir,
                    chapter_index=index,
                    total_chapters=total,
                    progress=lambda value, text: self.global_progress.emit(value, text),
                )
                if self._stop_requested:
                    stopped = True
                    chapter.status = "stopped"
                    self.chapter_status.emit(manga.item_id, chapter.item_id, "stopped")
                    break
                chapter.status = "done" if ok else "warning"
                self.chapter_status.emit(manga.item_id, chapter.item_id, chapter.status)
                all_ok = all_ok and ok
            except Exception as exc:
                all_ok = False
                chapter.status = "failed"
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
