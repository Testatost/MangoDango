"""Headless MangoDango runner for servers.

Runs update checks (and optional downloads) either once or on the automation
schedule, without opening the Qt GUI. Only ``QtCore`` is imported (for
``QSettings``), so this works on a machine without a display.

Examples
--------
Run a single update-and-download pass now::

    python main.py --once

Run continuously on the configured automation schedule::

    python main.py --server

Watch a specific folder and download new chapters for everything in a saved
queue file::

    python main.py --server --output /srv/manga --queue my_queue.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings

from . import engine
from .automation import AutomationSchedule
from .constants import (
    APP_NAME,
    DEFAULT_OUTPUT_DIR,
    IMAGE_FORMATS,
    ORG_NAME,
    OUTPUT_MODES,
    READING_STYLES,
)
from .i18n import Translator, normalize_language
from .models import ItemSettings, MangaEntry

# Set by the signal handlers so the run loop can exit cleanly on Ctrl+C
# (SIGINT) or `kill <pid>` (SIGTERM).
_STOP_REQUESTED = False


def _request_stop(_signum, _frame) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def _install_signal_handlers() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _request_stop)
        except (ValueError, OSError):
            pass  # not on the main thread / unsupported platform


def _log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def _safe(value: str, choices: tuple[str, ...], default: str) -> str:
    return value if value in choices else default


def load_defaults(settings: QSettings) -> ItemSettings:
    return ItemSettings(
        reading_style=_safe(str(settings.value("defaults/reading_style", "long_strip") or "long_strip"), READING_STYLES, "long_strip"),
        output_mode=_safe(str(settings.value("defaults/output_mode", "images") or "images"), OUTPUT_MODES, "images"),
        image_format=_safe(str(settings.value("defaults/image_format", "original") or "original"), IMAGE_FORMATS, "original"),
        keep_images=str(settings.value("defaults/keep_images", "true")).lower() == "true",
        image_threads=int(settings.value("defaults/image_threads", 4) or 4),
        request_delay=float(settings.value("defaults/request_delay", 1.0) or 1.0),
    )


def load_queue_mangas(path: str | None, tr) -> list[MangaEntry]:
    if not path:
        return []
    queue_path = Path(path)
    if not queue_path.exists():
        _log(tr("server_queue_not_found", path=path))
        return []
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        _log(tr("server_queue_read_failed", path=path))
        return []
    payload = data.get("mangas", []) if isinstance(data, dict) else data
    mangas: list[MangaEntry] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                try:
                    mangas.append(MangaEntry.from_dict(item))
                except Exception:
                    pass
    return mangas


def run_pass(output_dir: str, defaults: ItemSettings, tr, known: list[MangaEntry], download: bool) -> None:
    _log(tr("server_checking_updates"))
    updates = engine.check_for_updates(
        output_dir, defaults, tr, known_mangas=known, log=_log, stop=lambda: _STOP_REQUESTED
    )
    if not updates:
        _log(tr("server_no_updates"))
        return
    if download and not _STOP_REQUESTED:
        downloadable = [m for m in updates if engine.manga_wants_auto_download(output_dir, m.title, m.url)]
        if downloadable:
            total = sum(len(manga.chapters) for manga in downloadable)
            _log(tr("server_downloading", count=total))
            engine.download_mangas(downloadable, output_dir, tr, log=_log, stop=lambda: _STOP_REQUESTED)
    _log(tr("server_run_finished"))


def build_parser(tr) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mangodango", description=tr("server_cli_description"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--server", action="store_true", help=tr("server_cli_server_help"))
    mode.add_argument("--once", action="store_true", help=tr("server_cli_once_help"))
    mode.add_argument("--headless", action="store_true", help=tr("server_cli_headless_help"))
    parser.add_argument("--output", default=None, help=tr("server_cli_output_help"))
    parser.add_argument("--queue", default=None, help=tr("server_cli_queue_help"))
    parser.add_argument("--lang", default=None, help=tr("server_cli_lang_help"))
    parser.add_argument("--interval", type=int, default=60, help=tr("server_cli_interval_help"))
    download = parser.add_mutually_exclusive_group()
    download.add_argument("--download", dest="download", action="store_true", help=tr("server_cli_download_help"))
    download.add_argument("--no-download", dest="download", action="store_false", help=tr("server_cli_no_download_help"))
    parser.set_defaults(download=None)
    return parser


def _requested_language(argv: list[str], saved_language: str) -> str:
    for index, value in enumerate(argv):
        if value.startswith("--lang="):
            return normalize_language(value.split("=", 1)[1])
        if value == "--lang" and index + 1 < len(argv):
            return normalize_language(argv[index + 1])
    return normalize_language(saved_language)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])

    # QCoreApplication (not QApplication) gives us QSettings without needing a
    # display server, which is exactly what a headless machine needs.
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    settings = QSettings(ORG_NAME, APP_NAME)

    language = _requested_language(argv, str(settings.value("ui/language", "en") or "en"))
    tr = Translator(language).tr
    args = build_parser(tr).parse_args(argv)

    output_dir = args.output or str(settings.value("paths/output_dir", "") or "").strip() or DEFAULT_OUTPUT_DIR
    defaults = load_defaults(settings)
    known = load_queue_mangas(args.queue, tr)

    if args.download is None:
        download = str(settings.value("updates/auto_download", "true")).lower() == "true"
    else:
        download = bool(args.download)

    _install_signal_handlers()
    _log(tr("server_started"))
    _log(tr("server_stop_hint", pid=os.getpid()))
    _log(tr("log_default_output", folder=output_dir))

    if args.once:
        run_pass(output_dir, defaults, tr, known, download)
        return 0

    schedule = AutomationSchedule.from_json(str(settings.value("automation/schedule", "") or ""))
    if not schedule.active:
        _log(tr("server_no_schedule"))
        return 1

    last_run = datetime.now()
    next_run = schedule.next_run(last_run)
    if next_run is not None:
        _log(tr("server_next_run", time=next_run.strftime("%Y-%m-%d %H:%M")))

    poll = max(5, int(args.interval))
    while not _STOP_REQUESTED:
        # Sleep in short slices so a stop signal is picked up quickly instead of
        # waiting out the whole poll interval.
        slept = 0.0
        while slept < poll and not _STOP_REQUESTED:
            time.sleep(min(1.0, poll - slept))
            slept += 1.0
        if _STOP_REQUESTED:
            break
        now = datetime.now()
        # Reload the schedule each cycle so edits made in the GUI are picked up
        # without restarting the server.
        settings.sync()
        schedule = AutomationSchedule.from_json(str(settings.value("automation/schedule", "") or ""))
        if not schedule.active:
            continue
        if schedule.due_since(last_run, now):
            last_run = now
            _log(tr("server_run_started", time=now.strftime("%Y-%m-%d %H:%M")))
            run_pass(output_dir, defaults, tr, known, download)
            upcoming = schedule.next_run(now)
            if upcoming is not None:
                _log(tr("server_next_run", time=upcoming.strftime("%Y-%m-%d %H:%M")))

    _log(tr("server_stopped"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
