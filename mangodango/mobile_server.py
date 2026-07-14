"""Mobile library and manga reader for MangoDango.

The server intentionally uses only Python's standard library. It exposes the
same downloaded manga library that the desktop reader uses and serves a small,
responsive web application to phones/tablets on the local network.

The web UI has no direct downloader. It can synchronize reading progress and
perform the same explicit manga-management actions as the desktop library.
"""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import shutil
import socket
import struct
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from .i18n import Translator, normalize_language
from .reading_state import ReadingStateStore, manga_id_for_path, stable_id
from .scraper import (
    IMAGE_EXTENSIONS,
    chapter_sort_key,
    natural_sort_key,
    read_manga_metadata_dir,
    sanitize_filename,
    write_manga_metadata_dir,
)

DEFAULT_MOBILE_READER_PORT = 8765
DEFAULT_MOBILE_READER_HOST = "0.0.0.0"
DEFAULT_MOBILE_READER_HOSTNAME = "mangodango.local"


class MobileReaderConfigurationError(ValueError):
    """Configuration error that can be rendered through the active UI language."""

    def __init__(self, translation_key: str, **translation_kwargs) -> None:
        self.translation_key = str(translation_key)
        self.translation_kwargs = dict(translation_kwargs)
        super().__init__(self.translation_key)

_CACHE_TTL_SECONDS = 300.0
_MAX_JSON_BODY_BYTES = 64 * 1024
_MAX_COVER_UPLOAD_BYTES = 25 * 1024 * 1024



def _safe_stat_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _bool_flag(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off", ""}
    return bool(value)


def _is_allowed_client(address: str) -> bool:
    """Allow loopback, link-local and private LAN clients only."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return bool(ip.is_loopback or ip.is_private or ip.is_link_local)
    except ValueError:
        return False


def discover_lan_addresses() -> list[str]:
    """Return likely private IPv4 addresses for the current machine."""
    addresses: set[str] = set()

    # UDP connect does not send payload data; it only asks the OS which local
    # interface would be used for an outbound route.
    for target in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(target)
            candidate = sock.getsockname()[0]
            if _is_allowed_client(candidate) and not ipaddress.ip_address(candidate).is_loopback:
                addresses.add(candidate)
        except OSError:
            pass
        finally:
            sock.close()

    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            candidate = str(item[4][0])
            try:
                ip = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if (ip.is_private or ip.is_link_local) and not ip.is_loopback:
                addresses.add(candidate)
    except OSError:
        pass

    return sorted(addresses, key=lambda value: tuple(int(part) for part in value.split(".")))


def _normalize_bind_host(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or text in {"*", "all"}:
        return DEFAULT_MOBILE_READER_HOST
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError as exc:
        raise MobileReaderConfigurationError("mobile_reader_invalid_host", host=text) from exc
    if isinstance(ip, ipaddress.IPv6Address):
        raise MobileReaderConfigurationError("mobile_reader_ipv4_only")
    return text


def _advertised_addresses(host: str) -> list[str]:
    host = _normalize_bind_host(host)
    if host == "0.0.0.0":
        return discover_lan_addresses()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return []
    if ip.is_unspecified:
        return discover_lan_addresses()
    return [host]


def mobile_reader_urls(
    port: int,
    host: str = DEFAULT_MOBILE_READER_HOST,
    hostname: str | None = None,
) -> list[str]:
    port = max(1, min(65535, int(port)))
    urls = [f"http://{address}:{port}" for address in _advertised_addresses(host)]
    local_name = str(hostname or "").strip().rstrip(".").lower()
    if local_name.endswith(".local") and urls:
        urls.insert(0, f"http://{local_name}:{port}")
    return list(dict.fromkeys(urls))


def _dns_encode_name(name: str) -> bytes:
    labels = [label for label in name.rstrip(".").split(".") if label]
    encoded = bytearray()
    for label in labels:
        raw = label.encode("utf-8")
        if len(raw) > 63:
            raise ValueError("mDNS label too long")
        encoded.append(len(raw))
        encoded.extend(raw)
    encoded.append(0)
    return bytes(encoded)


def _dns_read_name(packet: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    if depth > 12:
        raise ValueError("DNS compression loop")
    labels: list[str] = []
    original_next = offset
    jumped = False
    while True:
        if offset >= len(packet):
            raise ValueError("Truncated DNS name")
        length = packet[offset]
        if length == 0:
            offset += 1
            if not jumped:
                original_next = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("Truncated DNS pointer")
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            suffix, _ = _dns_read_name(packet, pointer, depth + 1)
            labels.extend(label for label in suffix.rstrip(".").split(".") if label)
            if not jumped:
                original_next = offset + 2
                jumped = True
            break
        offset += 1
        end = offset + length
        if end > len(packet):
            raise ValueError("Truncated DNS label")
        labels.append(packet[offset:end].decode("utf-8", errors="ignore"))
        offset = end
        if not jumped:
            original_next = offset
    return ".".join(labels).lower() + ".", original_next


class _MDNSResponder:
    """Tiny best-effort mDNS A-record responder for ``mangodango.local``.

    It deliberately stays optional. If another mDNS daemon owns UDP 5353 or the
    platform rejects multicast membership, the mobile reader keeps working via
    its normal IP URLs.
    """

    _GROUP = "224.0.0.251"
    _PORT = 5353

    def __init__(self, hostname: str, addresses: Iterable[str]) -> None:
        self.hostname = str(hostname or "").strip().rstrip(".").lower()
        self.addresses = [
            value for value in dict.fromkeys(str(item) for item in addresses)
            if value and value != "0.0.0.0"
        ]
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def start(self) -> bool:
        if self.is_running:
            return True
        if not self.hostname.endswith(".local") or not self.addresses:
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind(("", self._PORT))
            membership = socket.inet_aton(self._GROUP) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            return False
        self._socket = sock
        self._thread = threading.Thread(target=self._run, name="MangoDangoMDNS", daemon=True)
        self._thread.start()
        return True

    def _question_matches(self, packet: bytes) -> bool:
        if len(packet) < 12:
            return False
        try:
            _identifier, _flags, qdcount, _ancount, _nscount, _arcount = struct.unpack("!6H", packet[:12])
            offset = 12
            wanted = self.hostname + "."
            for _ in range(qdcount):
                name, offset = _dns_read_name(packet, offset)
                if offset + 4 > len(packet):
                    return False
                qtype, _qclass = struct.unpack("!HH", packet[offset:offset + 4])
                offset += 4
                if name == wanted and qtype in {1, 255}:
                    return True
        except (ValueError, struct.error):
            return False
        return False

    def _response(self) -> bytes:
        name = _dns_encode_name(self.hostname)
        answers = bytearray()
        for address in self.addresses:
            try:
                packed = socket.inet_aton(address)
            except OSError:
                continue
            answers.extend(name)
            answers.extend(struct.pack("!HHIH", 1, 0x8001, 120, len(packed)))
            answers.extend(packed)
        count = len(self.addresses) if answers else 0
        return struct.pack("!6H", 0, 0x8400, 0, count, 0, 0) + bytes(answers)

    def _run(self) -> None:
        sock = self._socket
        if sock is None:
            return
        response = self._response()
        if not response:
            return
        while self._socket is sock:
            try:
                packet, source = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._question_matches(packet):
                continue
            for target in (source, (self._GROUP, self._PORT)):
                try:
                    sock.sendto(response, target)
                except OSError:
                    pass

    def stop(self) -> None:
        sock = self._socket
        thread = self._thread
        self._socket = None
        self._thread = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)


@dataclass(frozen=True)
class PageSource:
    name: str
    file_path: Path | None = None
    archive_path: Path | None = None
    archive_member: str = ""

    @property
    def suffix(self) -> str:
        source = self.file_path.name if self.file_path is not None else self.archive_member
        return Path(source).suffix.lower()

    def read_bytes(self) -> bytes:
        if self.file_path is not None:
            return self.file_path.read_bytes()
        if self.archive_path is not None and self.archive_member:
            with zipfile.ZipFile(self.archive_path, "r") as archive:
                return archive.read(self.archive_member)
        return b""


@dataclass
class ChapterInfo:
    chapter_id: str
    title: str
    pages: list[PageSource] = field(default_factory=list)
    updated_at: float = 0.0

    def public_dict(self) -> dict:
        return {
            "id": self.chapter_id,
            "title": self.title,
            "pages": len(self.pages),
            "updated_at": self.updated_at,
        }


@dataclass
class MangaInfo:
    manga_id: str
    title: str
    path: Path
    chapters: list[ChapterInfo]
    cover: PageSource | None
    favorite: bool = False
    check_updates: bool = True
    auto_download: bool = True
    cover_version: float = 0.0
    updated_at: float = 0.0
    source_url: str = ""

    def public_dict(self, include_chapters: bool = False) -> dict:
        payload = {
            "id": self.manga_id,
            "title": self.title,
            "chapter_count": len(self.chapters),
            "latest_chapter": self.chapters[-1].title if self.chapters else "",
            "favorite": self.favorite,
            "check_updates": self.check_updates,
            "auto_download": self.auto_download,
            "cover_version": self.cover_version,
            "updated_at": self.updated_at,
            "has_cover": self.cover is not None,
            "source_url": self.source_url,
        }
        if include_chapters:
            payload["chapters"] = [chapter.public_dict() for chapter in self.chapters]
        return payload


class LibrarySnapshot:
    def __init__(self, root: Path, mangas: Iterable[MangaInfo]) -> None:
        self.root = root
        self.mangas = list(mangas)
        self.by_id = {manga.manga_id: manga for manga in self.mangas}
        self.created_at = time.monotonic()


def _chapter_from_image_dir(manga_dir: Path, chapter_dir: Path) -> ChapterInfo | None:
    try:
        images = sorted(
            [
                item
                for item in chapter_dir.iterdir()
                if item.is_file()
                and item.suffix.lower() in IMAGE_EXTENSIONS
                and _safe_stat_size(item) > 0
            ],
            key=lambda item: natural_sort_key(item.name),
        )
    except OSError:
        return None
    if not images:
        return None
    title = chapter_dir.name
    chapter_id = stable_id(manga_dir.resolve(), title)
    return ChapterInfo(
        chapter_id=chapter_id,
        title=title,
        pages=[PageSource(name=image.name, file_path=image) for image in images],
        updated_at=max((_safe_mtime(image) for image in images), default=_safe_mtime(chapter_dir)),
    )


def _chapter_from_cbz(manga_dir: Path, archive_path: Path) -> ChapterInfo | None:
    if _safe_stat_size(archive_path) <= 0:
        return None
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = sorted(
                [
                    member
                    for member in archive.namelist()
                    if Path(member).suffix.lower() in IMAGE_EXTENSIONS
                    and not member.endswith("/")
                ],
                key=natural_sort_key,
            )
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return None
    if not members:
        return None
    title = archive_path.stem
    chapter_id = stable_id(manga_dir.resolve(), title)
    return ChapterInfo(
        chapter_id=chapter_id,
        title=title,
        pages=[
            PageSource(name=Path(member).name, archive_path=archive_path, archive_member=member)
            for member in members
        ],
        updated_at=_safe_mtime(archive_path),
    )


def _cover_from_folder(manga_dir: Path) -> PageSource | None:
    for pattern in ("cover.*", "folder.*", "poster.*"):
        try:
            candidates = sorted(manga_dir.glob(pattern), key=lambda item: natural_sort_key(item.name))
        except OSError:
            candidates = []
        for path in candidates:
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and _safe_stat_size(path) > 0:
                return PageSource(name=path.name, file_path=path)
    return None


def _scan_manga(manga_dir: Path) -> MangaInfo | None:
    metadata = read_manga_metadata_dir(manga_dir)
    title = str(metadata.get("title") or manga_dir.name).strip() or manga_dir.name
    source_url = str(metadata.get("url") or "").strip()
    favorite = _bool_flag(metadata.get("favorite"), False)
    check_updates = _bool_flag(metadata.get("check_updates"), True)
    auto_download = _bool_flag(metadata.get("auto_download"), True)

    chapters_by_title: dict[str, ChapterInfo] = {}
    try:
        entries = list(manga_dir.iterdir())
    except OSError:
        return None

    # Keep desktop-reader semantics: image folders win over CBZ archives with the
    # same chapter title.
    for folder in sorted((item for item in entries if item.is_dir()), key=lambda item: chapter_sort_key(item.name)):
        chapter = _chapter_from_image_dir(manga_dir, folder)
        if chapter is not None:
            chapters_by_title[chapter.title] = chapter

    for archive in sorted(
        (item for item in entries if item.is_file() and item.suffix.lower() == ".cbz"),
        key=lambda item: chapter_sort_key(item.name),
    ):
        if archive.stem in chapters_by_title:
            continue
        chapter = _chapter_from_cbz(manga_dir, archive)
        if chapter is not None:
            chapters_by_title[chapter.title] = chapter

    chapters = sorted(chapters_by_title.values(), key=lambda chapter: chapter_sort_key(chapter.title))
    if not chapters:
        return None

    cover = _cover_from_folder(manga_dir)
    if cover is None:
        # The first readable page is a useful fallback for older downloads that
        # have no explicit cover image.
        cover = chapters[0].pages[0] if chapters and chapters[0].pages else None

    raw_updated = metadata.get("updated_at", 0)
    try:
        updated_at = float(raw_updated or 0)
    except (TypeError, ValueError):
        updated_at = 0.0
    if updated_at <= 0:
        updated_at = max((chapter.updated_at for chapter in chapters), default=_safe_mtime(manga_dir))

    return MangaInfo(
        manga_id=manga_id_for_path(manga_dir),
        title=title,
        path=manga_dir,
        chapters=chapters,
        cover=cover,
        favorite=favorite,
        check_updates=check_updates,
        auto_download=auto_download,
        cover_version=_safe_mtime(cover.file_path or cover.archive_path) if cover is not None else 0.0,
        updated_at=updated_at,
        source_url=source_url,
    )


def scan_mobile_library(root: str | Path) -> LibrarySnapshot:
    root_path = Path(root).expanduser()
    mangas: list[MangaInfo] = []
    if root_path.exists() and root_path.is_dir():
        try:
            directories = [item for item in root_path.iterdir() if item.is_dir() and item.name != ".mangodango"]
        except OSError:
            directories = []
        for manga_dir in sorted(directories, key=lambda item: natural_sort_key(item.name)):
            manga = _scan_manga(manga_dir)
            if manga is not None:
                mangas.append(manga)
    mangas.sort(key=lambda manga: (-manga.updated_at, manga.title.casefold()))
    return LibrarySnapshot(root_path, mangas)


def _mobile_logo_path() -> Path | None:
    """Locate the original MangoDango logo in source and PyInstaller builds."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    candidates = [
        Path(__file__).resolve().with_name("logo-small.png"),
        Path(__file__).resolve().parents[1] / "logo-small.png",
    ]
    if frozen_root:
        root = Path(frozen_root)
        candidates = [root / "logo-small.png", root / "mangodango" / "logo-small.png", *candidates]
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def _mobile_static_path(name: str) -> Path | None:
    """Locate a bundled mobile web asset in source and PyInstaller builds."""
    safe_name = str(name or "").replace("\\", "/").lstrip("/")
    if ".." in Path(safe_name).parts:
        return None
    frozen_root = getattr(sys, "_MEIPASS", None)
    candidates = [Path(__file__).resolve().parent / "mobile" / "static" / safe_name]
    if frozen_root:
        root = Path(frozen_root)
        candidates = [
            root / "mangodango" / "mobile" / "static" / safe_name,
            root / "mobile" / "static" / safe_name,
            *candidates,
        ]
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def _mobile_texts(language: str) -> dict[str, str]:
    """Build the web-reader labels from MangoDango's desktop translations."""
    translator = Translator(normalize_language(language))
    tr = translator.tr
    return {
        "library_title": tr("library_view"),
        "library_subtitle": tr("mobile_reader_enable_hint"),
        "search": tr("search"),
        "sort_latest": tr("library_sort_latest"),
        "sort_az": tr("library_sort_az"),
        "sort_favorites": tr("library_sort_favorites"),
        "library_empty": tr("library_empty"),
        "chapter": tr("reader_chapter"),
        "page": tr("reader_page"),
        "start_question": tr("reader_start_text", manga="{manga}"),
        "start_beginning": tr("reader_start_beginning"),
        "start_continue": tr("reader_start_continue"),
        "start_latest": tr("reader_start_latest"),
        "mode_strip": tr("reader_mode_strip_single"),
        "mode_single": tr("reader_mode_single"),
        "mode_double": tr("reader_mode_double"),
        "previous": tr("reader_previous"),
        "next": tr("reader_next"),
        "refreshing": tr("status_resolving"),
        "first_chapter_reached": tr("reader_start_beginning"),
        "last_chapter_reached": tr("reader_start_latest"),
        "menu_rename": tr("menu_rename"),
        "menu_change_cover": tr("menu_change_cover"),
        "menu_favorite": tr("menu_favorite"),
        "menu_unfavorite": tr("menu_unfavorite"),
        "menu_auto_add": tr("menu_auto_add"),
        "menu_auto_remove": tr("menu_auto_remove"),
        "menu_open_source": tr("menu_open_source"),
        "menu_delete": tr("menu_delete"),
        "rename_prompt": tr("rename_prompt"),
        "delete_confirm": tr("delete_confirm", title="{title}"),
        "cancel": tr("cancel"),
        "preloading_pages": tr("mobile_reader_preloading_pages", done="{done}", total="{total}"),
        "pages_ready": tr("mobile_reader_pages_ready"),
        "action_failed": tr("mobile_reader_action_failed", error="{error}"),
    }


class MobileLibraryApplication:
    def __init__(self, library_dir: str | Path, language: str = "en") -> None:
        self._library_dir = Path(library_dir).expanduser()
        self._language = normalize_language(language)
        self._snapshot: LibrarySnapshot | None = None
        self._reading_store = ReadingStateStore(self._library_dir)
        self._lock = threading.RLock()

    @property
    def language(self) -> str:
        with self._lock:
            return self._language

    def set_language(self, language: str) -> None:
        with self._lock:
            self._language = normalize_language(language)

    def mobile_config(self) -> dict[str, object]:
        with self._lock:
            language = self._language
        return {"language": language, "texts": _mobile_texts(language)}

    @property
    def library_dir(self) -> Path:
        with self._lock:
            return self._library_dir

    def set_library_dir(self, library_dir: str | Path) -> None:
        with self._lock:
            new_dir = Path(library_dir).expanduser()
            if new_dir != self._library_dir:
                self._library_dir = new_dir
                self._reading_store = ReadingStateStore(new_dir)
                self._snapshot = None

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None

    def snapshot(self, force: bool = False) -> LibrarySnapshot:
        with self._lock:
            expired = self._snapshot is None or (time.monotonic() - self._snapshot.created_at) > _CACHE_TTL_SECONDS
            if force or expired:
                self._snapshot = scan_mobile_library(self._library_dir)
            return self._snapshot

    def manga(self, manga_id: str) -> MangaInfo | None:
        snapshot = self.snapshot()
        manga = snapshot.by_id.get(manga_id)
        if manga is None:
            manga = self.snapshot(force=True).by_id.get(manga_id)
        return manga

    def chapter(self, manga_id: str, chapter_id: str) -> tuple[MangaInfo, ChapterInfo] | None:
        manga = self.manga(manga_id)
        if manga is None:
            return None
        for chapter in manga.chapters:
            if chapter.chapter_id == chapter_id:
                return manga, chapter
        refreshed = self.snapshot(force=True).by_id.get(manga_id)
        if refreshed is not None:
            for chapter in refreshed.chapters:
                if chapter.chapter_id == chapter_id:
                    return refreshed, chapter
        return None

    def _reading_for(self, manga: MangaInfo) -> dict:
        return self._reading_store.get(
            manga.manga_id, path=manga.path, title=manga.title, source_url=manga.source_url,
        ) or {}

    @staticmethod
    def _latest_chapter_is_read(manga: MangaInfo, reading: dict) -> bool:
        """Return True when the final page of the current newest chapter was read."""
        if not manga.chapters or not reading:
            return False
        latest = manga.chapters[-1]
        saved_id = str(reading.get("chapter_id") or "")
        saved_title = str(reading.get("chapter_title") or "").strip().casefold()
        same_chapter = bool(saved_id and saved_id == latest.chapter_id) or (
            bool(saved_title) and saved_title == latest.title.strip().casefold()
        )
        if not same_chapter or not latest.pages:
            return False
        try:
            raw_page_index = reading.get("page_index", -1)
            page_index = int(raw_page_index if raw_page_index is not None else -1)
        except (TypeError, ValueError):
            return False
        return page_index >= len(latest.pages) - 1

    def public_manga(self, manga: MangaInfo, include_chapters: bool = False) -> dict:
        payload = manga.public_dict(include_chapters=include_chapters)
        reading = self._reading_for(manga)
        payload["reading"] = reading
        payload["latest_read"] = self._latest_chapter_is_read(manga, reading)
        payload["last_read_at"] = float(reading.get("updated_at", 0) or 0)
        return payload

    def library_payload(self) -> dict:
        snapshot = self.snapshot()
        library = [self.public_manga(manga) for manga in snapshot.mangas]
        return {"library": library, "count": len(library)}

    def reading_progress(self, manga_id: str) -> dict | None:
        manga = self.manga(manga_id)
        if manga is None:
            return None
        return self._reading_store.get(manga.manga_id, path=manga.path, title=manga.title, source_url=manga.source_url)

    def save_reading_progress(self, manga_id: str, payload: dict) -> dict:
        manga = self.manga(manga_id)
        if manga is None:
            raise FileNotFoundError("Manga not found")
        chapter_id = str(payload.get("chapter_id") or "")
        chapter_title = str(payload.get("chapter_title") or "")
        chapter = None
        for item in manga.chapters:
            if (chapter_id and item.chapter_id == chapter_id) or (chapter_title and item.title == chapter_title):
                chapter = item
                break
        if chapter is None:
            raise FileNotFoundError("Chapter not found")
        page_index = max(0, min(len(chapter.pages) - 1, int(payload.get("page_index", 0) or 0)))
        reached_latest = bool(manga.chapters and chapter is manga.chapters[-1] and page_index >= len(chapter.pages) - 1)
        record = self._reading_store.update_progress(
            manga_id=manga.manga_id,
            title=manga.title,
            source_url=manga.source_url,
            path=manga.path,
            chapter_id=chapter.chapter_id,
            chapter_title=chapter.title,
            page_index=page_index,
            global_index=None,
            chapter_pages=len(chapter.pages),
            total_pages=sum(len(item.pages) for item in manga.chapters),
            reader_mode=str(payload.get("reader_mode") or ""),
            reached_latest_page=reached_latest,
        )
        return record


    def _metadata_for(self, manga: MangaInfo) -> dict:
        data = read_manga_metadata_dir(manga.path)
        data.setdefault("app", "MangoDango")
        data.setdefault("title", manga.title)
        if manga.source_url:
            data.setdefault("url", manga.source_url)
        return data

    def _find_after_refresh(self, *, path: Path | None = None, title: str = "", source_url: str = "") -> MangaInfo | None:
        snapshot = self.snapshot(force=True)
        if path is not None:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            for manga in snapshot.mangas:
                try:
                    if manga.path.resolve() == resolved:
                        return manga
                except OSError:
                    if manga.path == path:
                        return manga
        for manga in snapshot.mangas:
            if source_url and manga.source_url == source_url:
                return manga
            if title and manga.title == title:
                return manga
        return None

    def apply_manga_action(self, manga_id: str, action: str, payload: dict | None = None) -> MangaInfo | None:
        payload = payload or {}
        with self._lock:
            manga = self.manga(manga_id)
            if manga is None:
                raise FileNotFoundError("Manga not found")
            action = str(action or "").strip().lower()
            metadata = self._metadata_for(manga)
            old_manga_id = manga.manga_id
            target_path = manga.path
            target_title = manga.title

            if action == "rename":
                new_title = str(payload.get("title") or "").strip()
                if not new_title:
                    raise ValueError("A new title is required")
                new_folder = sanitize_filename(new_title, new_title)
                new_path = manga.path.parent / new_folder
                if new_path != manga.path and new_path.exists():
                    raise FileExistsError("A manga folder with that name already exists")
                if new_path != manga.path:
                    shutil.move(str(manga.path), str(new_path))
                target_path = new_path
                target_title = new_title
                metadata["title"] = new_title
                write_manga_metadata_dir(target_path, metadata)
            elif action == "favorite":
                metadata["favorite"] = bool(payload.get("value"))
                write_manga_metadata_dir(manga.path, metadata)
            elif action == "auto":
                value = bool(payload.get("value"))
                metadata["check_updates"] = value
                metadata["auto_download"] = value
                write_manga_metadata_dir(manga.path, metadata)
            elif action == "delete":
                shutil.rmtree(manga.path)
                self._reading_store.remove(manga.manga_id)
                self._snapshot = None
                return None
            else:
                raise ValueError(f"Unsupported action: {action}")

            self._snapshot = None
            refreshed = self._find_after_refresh(path=target_path, title=target_title, source_url=manga.source_url)
            if refreshed is not None and refreshed.manga_id != old_manga_id:
                self._reading_store.migrate(old_manga_id, refreshed.manga_id, title=refreshed.title, path=refreshed.path)
            return refreshed

    def set_cover(self, manga_id: str, body: bytes, content_type: str) -> MangaInfo:
        with self._lock:
            manga = self.manga(manga_id)
            if manga is None:
                raise FileNotFoundError("Manga not found")
            if not body:
                raise ValueError("Cover image is empty")
            mime = str(content_type or "").split(";", 1)[0].strip().lower()
            suffix_by_mime = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
                "image/avif": ".avif",
            }
            suffix = suffix_by_mime.get(mime)
            if suffix is None:
                raise ValueError("Unsupported cover image format")
            for existing in manga.path.glob("cover.*"):
                try:
                    existing.unlink()
                except OSError:
                    pass
            target = manga.path / f"cover{suffix}"
            target.write_bytes(body)
            metadata = self._metadata_for(manga)
            metadata["cover"] = target.name
            write_manga_metadata_dir(manga.path, metadata)
            self._snapshot = None
            refreshed = self._find_after_refresh(path=manga.path, title=manga.title, source_url=manga.source_url)
            if refreshed is None:
                raise RuntimeError("Could not refresh manga after cover update")
            return refreshed


class _MobileHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, application: MobileLibraryApplication):
        self.application = application
        super().__init__(server_address, handler_class)


class MobileRequestHandler(BaseHTTPRequestHandler):
    server_version = "MangoDangoMobile/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> MobileLibraryApplication:
        return self.server.application  # type: ignore[attr-defined]

    def _tr(self, key: str, **kwargs) -> str:
        return Translator(self.app.language).tr(key, **kwargs)

    def log_message(self, _format: str, *args) -> None:
        # Keep the desktop log clean; callers can use health/status methods for
        # diagnostics instead of printing every page request.
        return

    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'",
        )

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status.value)
        self._common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status=status)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _read_body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > limit:
            raise ValueError("Request body is too large")
        return self.rfile.read(length) if length else b""

    def _read_json_body(self) -> dict:
        body = self._read_body(_MAX_JSON_BODY_BYTES)
        if not body:
            return {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _mobile_write_allowed(self) -> bool:
        return self.headers.get("X-MangoDango-Request", "") == "1"

    def _send_page_source(self, source: PageSource, cache_control: str = "no-store") -> None:
        try:
            body = source.read_bytes()
        except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
            self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
            return
        if not body:
            self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_internal"))
            return
        content_type = mimetypes.guess_type("page" + source.suffix)[0] or "application/octet-stream"
        self._send_bytes(body, content_type, cache_control=cache_control)

    def _send_static_asset(self, name: str, *, cache_control: str = "public, max-age=3600") -> bool:
        path = _mobile_static_path(name)
        if path is None:
            return False
        try:
            body = path.read_bytes()
        except OSError:
            return False
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() in {".html", ".css", ".js"}:
            content_type += "; charset=utf-8"
        self._send_bytes(body, content_type, cache_control=cache_control)
        return True

    def _route(self) -> None:
        if not _is_allowed_client(str(self.client_address[0])):
            self._send_error_json(HTTPStatus.FORBIDDEN, self._tr("mobile_error_local_only"))
            return

        path = unquote(urlsplit(self.path).path)
        if path in {"/", "/index.html"}:
            if not self._send_static_asset("index.html", cache_control="no-store"):
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_internal"))
            return
        if path == "/assets/app.css":
            if not self._send_static_asset("app.css", cache_control="no-cache"):
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_internal"))
            return
        if path == "/assets/app.js":
            if not self._send_static_asset("app.js", cache_control="no-cache"):
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_internal"))
            return
        if path == "/health":
            snapshot = self.app.snapshot()
            self._send_json({"ok": True, "mangas": len(snapshot.mangas), "language": self.app.language})
            return
        if path == "/api/config":
            self._send_json(self.app.mobile_config())
            return
        if path == "/assets/logo-small.png":
            logo_path = _mobile_logo_path()
            if logo_path is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            try:
                body = logo_path.read_bytes()
            except OSError:
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            self._send_bytes(body, "image/png", cache_control="public, max-age=86400")
            return
        if path == "/api/library":
            self._send_json(self.app.library_payload())
            return

        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["api", "manga"]:
            manga = self.app.manga(parts[2])
            if manga is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            self._send_json(self.app.public_manga(manga, include_chapters=True))
            return
        if len(parts) == 3 and parts[:2] == ["api", "progress"]:
            manga = self.app.manga(parts[2])
            if manga is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            self._send_json({"progress": self.app.reading_progress(parts[2])})
            return
        if len(parts) == 3 and parts[:2] == ["api", "cover"]:
            manga = self.app.manga(parts[2])
            if manga is None or manga.cover is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            self._send_page_source(manga.cover)
            return
        if len(parts) == 6 and parts[:2] == ["api", "page"]:
            manga_id, chapter_id, raw_index = parts[2], parts[3], parts[4]
            # The sixth segment is a harmless display filename used only to make
            # browser debugging/caching friendlier. It is never interpreted as a path.
            try:
                page_index = int(raw_index)
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, self._tr("mobile_error_invalid_request"))
                return
            pair = self.app.chapter(manga_id, chapter_id)
            if pair is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            _manga, chapter = pair
            if page_index < 0 or page_index >= len(chapter.pages):
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            self._send_page_source(chapter.pages[page_index])
            return

        # Also accept the compact URL without a display filename.
        if len(parts) == 5 and parts[:2] == ["api", "page"]:
            manga_id, chapter_id, raw_index = parts[2], parts[3], parts[4]
            try:
                page_index = int(raw_index)
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, self._tr("mobile_error_invalid_request"))
                return
            pair = self.app.chapter(manga_id, chapter_id)
            if pair is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            _manga, chapter = pair
            if page_index < 0 or page_index >= len(chapter.pages):
                self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
                return
            self._send_page_source(chapter.pages[page_index])
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT.value)
            self._common_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))

    def _route_post(self) -> None:
        if not _is_allowed_client(str(self.client_address[0])):
            self._send_error_json(HTTPStatus.FORBIDDEN, self._tr("mobile_error_local_only"))
            return
        if not self._mobile_write_allowed():
            self._send_error_json(HTTPStatus.FORBIDDEN, self._tr("mobile_error_write_rejected"))
            return

        path = unquote(urlsplit(self.path).path)
        parts = [part for part in path.split("/") if part]
        try:
            if len(parts) == 3 and parts[:2] == ["api", "progress"]:
                payload = self._read_json_body()
                record = self.app.save_reading_progress(parts[2], payload)
                self._send_json({"ok": True, "progress": record})
                return
            if len(parts) == 4 and parts[:2] == ["api", "manga"] and parts[3] == "action":
                manga_id = parts[2]
                payload = self._read_json_body()
                action = str(payload.get("action") or "")
                manga = self.app.apply_manga_action(manga_id, action, payload)
                if manga is None:
                    self._send_json({"ok": True, "deleted": True})
                else:
                    self._send_json({"ok": True, "manga": self.app.public_manga(manga, include_chapters=True)})
                return
            if len(parts) == 4 and parts[:2] == ["api", "manga"] and parts[3] == "cover":
                manga_id = parts[2]
                body = self._read_body(_MAX_COVER_UPLOAD_BYTES)
                manga = self.app.set_cover(manga_id, body, self.headers.get("Content-Type", ""))
                self._send_json({"ok": True, "manga": self.app.public_manga(manga, include_chapters=True)})
                return
        except FileNotFoundError:
            self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))
            return
        except FileExistsError:
            self._send_error_json(HTTPStatus.CONFLICT, self._tr("mobile_error_conflict"))
            return
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, self._tr("mobile_error_invalid_request"))
            return
        except (OSError, RuntimeError):
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, self._tr("mobile_error_internal"))
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, self._tr("mobile_error_not_found"))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._route()

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._route()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._route_post()


class MobileLibraryServer:
    """Small lifecycle wrapper around the LAN web server."""

    def __init__(
        self,
        library_dir: str | Path,
        port: int = DEFAULT_MOBILE_READER_PORT,
        host: str = DEFAULT_MOBILE_READER_HOST,
        language: str = "en",
        hostname: str = DEFAULT_MOBILE_READER_HOSTNAME,
    ) -> None:
        self.host = _normalize_bind_host(host)
        self.port = int(port)
        self.hostname = str(hostname or "").strip().rstrip(".").lower()
        self.application = MobileLibraryApplication(library_dir, language=language)
        self._server: _MobileHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._mdns: _MDNSResponder | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._server is not None and self._thread is not None and self._thread.is_alive())

    def set_library_dir(self, library_dir: str | Path) -> None:
        self.application.set_library_dir(library_dir)

    def set_language(self, language: str) -> None:
        self.application.set_language(language)

    def invalidate(self) -> None:
        self.application.invalidate()

    def urls(self) -> list[str]:
        advertised_name = self.hostname if self._mdns is not None and self._mdns.is_running else None
        return mobile_reader_urls(self.port, host=self.host, hostname=advertised_name)

    def start(self) -> list[str]:
        if self.is_running:
            return self.urls()
        self._server = _MobileHTTPServer((self.host, self.port), MobileRequestHandler, self.application)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="MangoDangoMobileReader",
            daemon=True,
        )
        self._thread.start()

        addresses = _advertised_addresses(self.host)
        mdns = _MDNSResponder(self.hostname, addresses)
        if mdns.start():
            self._mdns = mdns
        else:
            self._mdns = None
        return self.urls()

    def stop(self) -> None:
        mdns = self._mdns
        self._mdns = None
        if mdns is not None:
            mdns.stop()

        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)


# Mobile web assets live in mangodango/mobile/static/.
