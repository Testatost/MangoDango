from __future__ import annotations

import html as html_module
import json
import re
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .constants import IMAGE_FORMATS, READING_STYLES
from .i18n import tr_message
from .models import ChapterEntry, ItemSettings, MangaEntry

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
CHAPTER_METADATA_FILE = ".mangodango_chapter.json"
MIN_COMPLETE_IMAGE_COUNT = 3
PLACEHOLDER_MARKERS = (
    "broken_image", "favicon", "logo", "avatar", "placeholder", "failed", "missing",
    "error", "static/images", "assets/images", "data:image/", "base64,",
)
MIN_PAGE_WIDTH = 300
MIN_PAGE_HEIGHT = 300

LogCallback = Callable[[object], None]
ProgressCallback = Callable[[float, str], None]
StopCallback = Callable[[], bool]
TranslateCallback = Callable[..., str]


def identity_tr(key: str, **kwargs) -> str:
    text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def normalize_weebcentral_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        raise ValueError("error_empty_url")
    if not re.match(r"^https?://", value, flags=re.IGNORECASE):
        value = "https://" + value.lstrip("/")
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if host == "www.weebcentral.com":
        parsed = parsed._replace(netloc="weebcentral.com")
        host = "weebcentral.com"
    if host != "weebcentral.com":
        raise ValueError("error_invalid_host")
    if parsed.scheme.lower() != "https":
        parsed = parsed._replace(scheme="https")
    if not parsed.path or parsed.path == "/":
        raise ValueError("error_invalid_url")
    return parsed.geturl()


def sanitize_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r"[\\/*?:\"<>|]", "_", str(value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or fallback


def natural_sort_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def chapter_number(name: str) -> float | None:
    """Return the most likely reading-order number from a chapter label.

    Prefer explicit chapter-like prefixes before falling back to the first
    numeric value. This avoids treating a volume number as the chapter number in
    labels such as ``Vol. 42 - Chapter 386``.
    """
    text = str(name or "").lower()
    patterns = (
        r"\b(?:chapter|chap(?:ter)?|ch\.?)\s*#?\s*(\d+(?:\.\d+)?)",
        r"\b(?:prologue|prolog|prelude|episode|ep\.?|part|act)\s*#?\s*(\d+(?:\.\d+)?)",
    )
    match = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            break
    if match is None:
        match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def chapter_sort_key(value: str) -> tuple[object, ...]:
    """Natural reading-order key for mixed chapter naming schemes.

    A plain lexical/natural sort puts every ``Chapter ...`` folder before every
    ``Prologue ...`` folder. For series such as Berserk this makes the reader jump
    to the prologues only after the numbered chapters. Prologues/preludes are
    therefore grouped before regular chapters, while epilogues/afterwords are
    grouped after them. Numbers and the natural label order remain tie-breakers.
    """
    text = str(value or "").strip()
    lower = text.casefold()
    if re.search(r"\b(?:prologue|prolog|prelude|pilot)\b", lower):
        phase = 0
    elif re.search(r"\b(?:epilogue|epilog|afterword)\b", lower):
        phase = 2
    else:
        phase = 1

    number = chapter_number(text)
    return (
        phase,
        0 if number is not None else 1,
        number if number is not None else float("inf"),
        natural_sort_key(text),
    )


def strip_weebcentral_suffix(value: str) -> str:
    text = re.sub(r"\s*[_|\-]\s*Weeb\s*Central.*$", "", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"\s*[_|\-]\s*WeebCentral.*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def split_chapter_page_title(value: str) -> tuple[str, str]:
    title = strip_weebcentral_suffix(value)
    parts = [part.strip() for part in re.split(r"\s+[_|]\s+", title) if part.strip()]
    if len(parts) >= 2 and parts[0].lower().startswith("chapter"):
        return parts[1], parts[0]
    match = re.match(r"^(Chapter\s+\d+(?:\.\d+)?[^_|-]*)\s*[-|_]\s*(.+)$", title, flags=re.IGNORECASE)
    if match:
        return match.group(2).strip(), match.group(1).strip()
    return title or "Manga", title or "Chapter"


def chapter_output_paths(output_dir: str | Path, manga_title: str, chapter_title: str) -> dict[str, Path]:
    manga_dir = Path(output_dir) / sanitize_filename(manga_title, "Manga")
    chapter_name = sanitize_filename(chapter_title, "Chapter")
    chapter_dir = manga_dir / chapter_name
    return {
        "manga_dir": manga_dir,
        "chapter_dir": chapter_dir,
        "cbz": manga_dir / f"{chapter_name}.cbz",
        "pdf": manga_dir / f"{chapter_name}.pdf",
        "metadata": manga_dir / ".mangodango.json",
        "chapter_metadata": chapter_dir / CHAPTER_METADATA_FILE,
    }


def chapter_image_files(chapter_dir: Path) -> list[Path]:
    if not chapter_dir.exists() or not chapter_dir.is_dir():
        return []
    return sorted(
        [
            item for item in chapter_dir.iterdir()
            if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS and item.stat().st_size > 0
        ],
        key=lambda item: natural_sort_key(item.name),
    )


def chapter_has_partial_files(chapter_dir: Path) -> bool:
    if not chapter_dir.exists() or not chapter_dir.is_dir():
        return False
    return any(item.is_file() and item.suffix.lower() in {".tmp", ".part", ".download"} for item in chapter_dir.iterdir())


def chapter_exists_on_disk(output_dir: str | Path, manga_title: str, chapter_title: str) -> bool:
    paths = chapter_output_paths(output_dir, manga_title, chapter_title)

    # Archive output is atomic enough to be treated as complete.
    if paths["cbz"].exists() and paths["cbz"].stat().st_size > 0:
        return True
    if paths["pdf"].exists() and paths["pdf"].stat().st_size > 0:
        return True

    chapter_dir = paths["chapter_dir"]
    if not chapter_dir.exists() or not chapter_dir.is_dir():
        return False

    # Newer MangoDango versions write a completion marker after a chapter
    # finishes successfully. This avoids treating a failed partial folder as done.
    marker = paths["chapter_metadata"]
    if marker.exists() and marker.stat().st_size > 0:
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if data.get("complete") is True:
                return True
        except Exception:
            pass

    # Backward compatibility for chapters downloaded before completion markers
    # existed. A folder with only one or two images is often a failed/partial
    # download, so do not treat it as complete unless an archive/marker exists.
    image_files = chapter_image_files(chapter_dir)
    if chapter_has_partial_files(chapter_dir):
        return False
    return len(image_files) >= MIN_COMPLETE_IMAGE_COUNT


def write_chapter_metadata(output_dir: str | Path, manga_title: str, chapter_title: str, chapter_url: str, image_count: int) -> None:
    paths = chapter_output_paths(output_dir, manga_title, chapter_title)
    chapter_dir = paths["chapter_dir"]
    chapter_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "app": "MangoDango",
        "title": chapter_title,
        "url": chapter_url,
        "complete": True,
        "image_count": int(image_count),
    }
    paths["chapter_metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def read_manga_metadata_dir(manga_dir: str | Path) -> dict:
    path = Path(manga_dir) / ".mangodango.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_manga_metadata_dir(manga_dir: str | Path, data: dict) -> None:
    manga_dir = Path(manga_dir)
    manga_dir.mkdir(parents=True, exist_ok=True)
    (manga_dir / ".mangodango.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_manga_metadata(output_dir: str | Path, manga_title: str) -> dict:
    return read_manga_metadata_dir(chapter_output_paths(output_dir, manga_title, "Chapter")["manga_dir"])


def merge_manga_metadata(output_dir: str | Path, manga_title: str, **fields) -> dict:
    """Update selected metadata fields without clobbering the rest."""
    manga_dir = chapter_output_paths(output_dir, manga_title, "Chapter")["manga_dir"]
    data = read_manga_metadata_dir(manga_dir)
    data.setdefault("app", "MangoDango")
    data.setdefault("title", manga_title)
    for key, value in fields.items():
        data[key] = value
    write_manga_metadata_dir(manga_dir, data)
    return data


def write_manga_metadata(output_dir: str | Path, manga_title: str, manga_url: str) -> None:
    paths = chapter_output_paths(output_dir, manga_title, "Chapter")
    manga_dir = paths["manga_dir"]
    # Preserve any per-manga flags (favorite, check_updates, auto_download, cover)
    # that a re-download must not overwrite.
    metadata = read_manga_metadata_dir(manga_dir)
    metadata.update({
        "app": "MangoDango",
        "title": manga_title,
        "url": manga_url,
        "updated_at": int(time.time()),
    })
    write_manga_metadata_dir(manga_dir, metadata)


def decode_url_candidate(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().strip('"\'')
    if not text:
        return None
    text = html_module.unescape(text)
    text = text.replace("\\/", "/")
    text = text.replace("\\u002F", "/").replace("\\u002f", "/")
    text = text.replace("&amp;", "&")
    return text.strip()


def parse_srcset(value: str | None) -> list[str]:
    decoded = decode_url_candidate(value)
    if not decoded:
        return []
    result: list[str] = []
    for part in decoded.split(","):
        candidate = part.strip().split()[0] if part.strip() else ""
        if candidate:
            result.append(candidate)
    return result


class WeebCentralClient:
    def __init__(
        self,
        tr: TranslateCallback = identity_tr,
        log: LogCallback | None = None,
        stop: StopCallback | None = None,
    ) -> None:
        self.base_url = "https://weebcentral.com"
        self.tr = tr
        self.log_callback = log or (lambda message: None)
        self.stop_callback = stop or (lambda: False)
        self.session = requests.Session()
        self.session.headers.update(self.default_headers())
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    @staticmethod
    def default_headers() -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8,cs;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }

    def log(self, key_or_message: str, **kwargs) -> None:
        if key_or_message.startswith(("log_", "error_", "status_")):
            self.log_callback(tr_message(key_or_message, **kwargs))
        else:
            self.log_callback(key_or_message)

    def image_headers(self, referer: str) -> dict[str, str]:
        return {
            "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8,cs;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer,
            "Origin": self.base_url,
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    def fetch_html(self, url: str) -> str:
        response = self.session.get(url, timeout=30, allow_redirects=True)
        text = response.text or ""
        if response.status_code in {403, 503} and (
            "cloudflare" in text.lower() or "just a moment" in text.lower()
        ):
            raise RuntimeError("error_cloudflare")
        response.raise_for_status()
        if response.encoding is None:
            response.encoding = response.apparent_encoding
        return response.text

    def page_title(self, soup: BeautifulSoup) -> str:
        for selector in ("title", "h1"):
            element = soup.select_one(selector)
            if element and element.get_text(strip=True):
                return strip_weebcentral_suffix(element.get_text(" ", strip=True))
        return ""

    def manga_title(self, soup: BeautifulSoup) -> str:
        for selector in ("section[x-data] > section:nth-of-type(2) h1", "h1", "title"):
            element = soup.select_one(selector)
            if element and element.get_text(strip=True):
                return sanitize_filename(strip_weebcentral_suffix(element.get_text(" ", strip=True)), "Manga")
        return "Manga"

    def is_direct_chapter_url(self, url: str) -> bool:
        parts = [part for part in urlparse(url).path.split("/") if part]
        return len(parts) >= 2 and parts[0] in {"chapter", "chapters"}

    def chapter_list_url(self, url: str) -> str:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "series":
            return f"{self.base_url}/series/{parts[1]}/full-chapter-list"
        return urljoin(url.rstrip("/") + "/", "full-chapter-list")

    def get_chapters(self, url: str) -> list[ChapterEntry]:
        soup = BeautifulSoup(self.fetch_html(self.chapter_list_url(url)), "html.parser")
        elements = soup.select("div[x-data] > a[href]") or soup.select("a[href*='/chapters/'], a[href*='/chapter/']")
        chapters: list[ChapterEntry] = []
        seen: set[str] = set()
        for element in elements:
            href = element.get("href")
            if not href:
                continue
            chapter_url = urljoin(self.base_url, str(href))
            parsed = urlparse(chapter_url)
            if parsed.netloc.lower() != "weebcentral.com" or chapter_url in seen:
                continue
            seen.add(chapter_url)
            label = element.select_one("span.flex > span") or element
            name = sanitize_filename(
                label.get_text(" ", strip=True),
                self.tr("chapter_fallback", number=len(chapters) + 1),
            )
            chapters.append(ChapterEntry(title=name, url=chapter_url))

        # WeebCentral's chapter list order can differ between pages and over time.
        # Always download from the lowest chapter number upward so a full manga starts
        # with Chapter 1 instead of the newest/recent chapter.
        chapters.sort(key=lambda chapter: chapter_sort_key(chapter.title))
        return chapters

    def cover_url(self, soup: BeautifulSoup) -> str | None:
        for selector in ("img[alt$='cover']", "img[alt*='cover' i]", "img[src*='cover' i]", "img[data-src*='cover' i]"):
            image = soup.select_one(selector)
            if not image:
                continue
            src = image.get("data-src") or image.get("data-lazy-src") or image.get("src")
            if src:
                return urljoin(self.base_url, decode_url_candidate(str(src)) or str(src))
        return None

    def download_cover(self, soup: BeautifulSoup, manga_dir: Path, referer: str) -> None:
        url = self.cover_url(soup)
        if not url:
            self.log("log_cover_missing")
            return
        try:
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                ext = ".jpg"
            target = manga_dir / f"cover{ext}"
            if target.exists() and target.stat().st_size > 0:
                return
            response = self.session.get(url, headers=self.image_headers(referer), timeout=30)
            response.raise_for_status()
            target.write_bytes(response.content)
            self.log("log_cover_saved")
        except Exception:
            return

    def search_series(self, query: str) -> list[tuple[str, str]]:
        """Search weebcentral for a series by name.

        Returns a list of ``(title, series_url)`` matches (best first). Used to
        recover a manga's URL when its metadata file is missing.
        """
        query = (query or "").strip()
        if not query:
            return []
        results: list[tuple[str, str]] = []
        seen: set[str] = set()
        urls = [
            f"{self.base_url}/search/data?author=&text={quote_plus(query)}&sort=Best+Match&order=Descending&official=Any&anime=Any&adult=Any&display_mode=Full+Display",
            f"{self.base_url}/search?text={quote_plus(query)}",
        ]
        for search_url in urls:
            try:
                soup = BeautifulSoup(self.fetch_html(search_url), "html.parser")
            except Exception:
                continue
            for anchor in soup.select("a[href*='/series/']"):
                href = anchor.get("href")
                if not href:
                    continue
                series_url = urljoin(self.base_url, str(href))
                parts = [p for p in urlparse(series_url).path.split("/") if p]
                if len(parts) < 2 or parts[0] != "series":
                    continue
                canonical = f"{self.base_url}/series/{parts[1]}"
                if canonical in seen:
                    continue
                seen.add(canonical)
                label = anchor.get_text(" ", strip=True) or parts[-1]
                results.append((strip_weebcentral_suffix(label), canonical))
            if results:
                break
        return results

    def recover_series_url(self, title: str) -> str:
        """Best-effort URL recovery for a downloaded manga folder.

        Returns the series URL of the closest name match, or an empty string.
        """
        import difflib
        wanted = sanitize_filename(title, "").lower()
        best_url = ""
        best_score = 0.0
        for label, url in self.search_series(title):
            candidate = sanitize_filename(label, "").lower()
            score = difflib.SequenceMatcher(None, wanted, candidate).ratio()
            if candidate == wanted:
                return url
            if score > best_score:
                best_score, best_url = score, url
        return best_url if best_score >= 0.6 else ""

    def resolve(self, raw_url: str, defaults: ItemSettings) -> MangaEntry:
        url = normalize_weebcentral_url(raw_url)
        soup = BeautifulSoup(self.fetch_html(url), "html.parser")
        if self.is_direct_chapter_url(url):
            manga_name, chapter_name = split_chapter_page_title(self.page_title(soup))
            chapter = ChapterEntry(title=sanitize_filename(chapter_name, "Chapter"), url=url, settings=defaults.clone())
            return MangaEntry(title=sanitize_filename(manga_name, "Manga"), url=url, chapters=[chapter], settings=defaults.clone())
        title = self.manga_title(soup)
        chapters = self.get_chapters(url)
        if not chapters:
            raise RuntimeError("error_no_chapters")
        for chapter in chapters:
            chapter.settings = defaults.clone()
        return MangaEntry(title=title, url=url, chapters=chapters, settings=defaults.clone())

    def element_looks_like_placeholder(self, element) -> bool:
        values: list[str] = []
        for attr in ("alt", "title", "class", "id", "aria-label", "src", "data-src", "data-url"):
            value = element.get(attr)
            if value:
                if isinstance(value, list):
                    values.extend(str(item) for item in value)
                else:
                    values.append(str(value))
        joined = " ".join(values).lower()
        return any(marker in joined for marker in PLACEHOLDER_MARKERS + ("weebcentral", "weeb central", "weeb-central"))

    def accepts_image_candidate(self, url: str) -> bool:
        lower = url.lower()
        if not lower.startswith(("http://", "https://", "/")):
            return False
        if any(marker in lower for marker in PLACEHOLDER_MARKERS):
            return False
        parsed = urlparse(urljoin(self.base_url, url))
        name = Path(parsed.path.lower()).name
        if name in {"logo.png", "logo.svg", "failed.png", "missing.png", "placeholder.png", "favicon.ico"}:
            return False
        suffix = Path(parsed.path).suffix.lower()
        return suffix in IMAGE_EXTENSIONS or "manga" in lower or "compsci" in lower or "uploads" in lower

    def normalize_image_url(self, candidate: str, base_url: str) -> str | None:
        decoded = decode_url_candidate(candidate)
        if not decoded or not self.accepts_image_candidate(decoded):
            return None
        absolute = urljoin(base_url, decoded)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return absolute

    def candidate_from_element(self, element, base_url: str) -> str | None:
        for attr in ("data-src", "data-lazy-src", "data-original", "data-url", "data-full", "data-image", "x-bind:src", ":src", "src"):
            value = element.get(attr)
            if not value:
                continue
            if attr in {"src", ":src", "x-bind:src"} and self.element_looks_like_placeholder(element):
                continue
            url = self.normalize_image_url(str(value), base_url)
            if url:
                return url
        for attr in ("data-srcset", "srcset"):
            if attr == "srcset" and self.element_looks_like_placeholder(element):
                continue
            for candidate in reversed(parse_srcset(element.get(attr))):
                url = self.normalize_image_url(candidate, base_url)
                if url:
                    return url
        return None

    def extract_image_urls(self, raw_html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(raw_html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()

        def add(url: str | None) -> None:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

        for picture in soup.find_all("picture"):
            selected: str | None = None
            for source in reversed(picture.find_all("source")):
                selected = self.candidate_from_element(source, base_url)
                if selected:
                    break
            if not selected:
                img = picture.find("img")
                if img:
                    selected = self.candidate_from_element(img, base_url)
            add(selected)
        for img in soup.find_all("img"):
            if img.find_parent("picture") is None:
                add(self.candidate_from_element(img, base_url))
        for source in soup.find_all("source"):
            if source.find_parent("picture") is None:
                add(self.candidate_from_element(source, base_url))
        decoded_html = decode_url_candidate(raw_html) or raw_html
        decoded_html = html_module.unescape(decoded_html).replace("\\/", "/")
        pattern = re.compile(r"https?://[^'\"\s<>\\]+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^'\"\s<>\\]*)?", re.IGNORECASE)
        for match in pattern.findall(decoded_html):
            add(self.normalize_image_url(match, base_url))
        return urls

    def reading_style_candidates(self, configured: str) -> list[str]:
        # For downloads, long_strip is the most reliable source for the complete
        # per-page image list. Some other styles can return only two combined preview
        # images. Keep the configured style in the list, but choose the source with
        # the highest real page count instead of assuming the first endpoint is best.
        ordered = ["long_strip"]
        if configured in READING_STYLES and configured not in ordered:
            ordered.append(configured)
        for style in READING_STYLES:
            if style not in ordered:
                ordered.append(style)
        return ordered

    def image_page_url(self, chapter_url: str, reading_style: str | None = None) -> str:
        base = f"{chapter_url.rstrip('/')}/images"
        if reading_style:
            return f"{base}?reading_style={reading_style}"
        return base

    def chapter_images(self, chapter_url: str, reading_style: str) -> tuple[str, list[str]]:
        best_url = ""
        best_label = ""
        best: list[str] = []

        candidates: list[tuple[str, str]] = []
        for style in self.reading_style_candidates(reading_style):
            candidates.append((style, self.image_page_url(chapter_url, style)))
        candidates.append(("default", self.image_page_url(chapter_url, None)))

        for label, page_url in candidates:
            if self.stop_callback():
                break
            self.log("log_image_page", url=page_url)
            try:
                urls = self.extract_image_urls(self.fetch_html(page_url), page_url)
            except Exception:
                urls = []
            if len(urls) > len(best):
                best_url = page_url
                best_label = label
                best = urls

        if best:
            self.log("log_image_source_selected", style=best_label, count=len(best))
            return best_url, best

        self.log("log_try_chapter_page")
        return chapter_url, self.extract_image_urls(self.fetch_html(chapter_url), chapter_url)

    def image_extension(self, url: str, content_type: str | None = None) -> str:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return suffix
        mapping = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
        if content_type:
            return mapping.get(content_type.split(";")[0].lower(), ".jpg")
        return ".jpg"

    def sorted_images(self, folder: Path) -> list[Path]:
        return sorted([item for item in folder.iterdir() if item.suffix.lower() in IMAGE_EXTENSIONS], key=lambda item: natural_sort_key(item.name))

    def existing_page_file(self, temporary_path: Path) -> Path | None:
        for ext in IMAGE_EXTENSIONS:
            candidate = temporary_path.with_suffix(ext)
            if not candidate.exists() or candidate.stat().st_size <= 0:
                continue
            try:
                with Image.open(candidate) as image:
                    width, height = image.size
                if width >= MIN_PAGE_WIDTH and height >= MIN_PAGE_HEIGHT:
                    return candidate
                candidate.unlink(missing_ok=True)
            except Exception:
                candidate.unlink(missing_ok=True)
        return None

    def convert_image_format(self, path: Path, image_format: str) -> Path:
        if image_format == "original" or image_format not in IMAGE_FORMATS:
            return path
        target_ext = ".jpg" if image_format == "jpg" else f".{image_format}"
        if path.suffix.lower() == target_ext:
            return path
        target = path.with_suffix(target_ext)
        with Image.open(path) as image:
            save_image = image.convert("RGB") if image_format == "jpg" and image.mode != "RGB" else image
            save_kwargs = {"quality": 95} if image_format in {"jpg", "webp"} else {}
            save_image.save(target, format=image_format.upper() if image_format != "jpg" else "JPEG", **save_kwargs)
        path.unlink(missing_ok=True)
        return target

    def download_image(self, url: str, temporary_path: Path, referer: str, image_format: str) -> Path:
        existing = self.existing_page_file(temporary_path)
        if existing:
            return self.convert_image_format(existing, image_format)
        headers = self.image_headers(referer)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            if self.stop_callback():
                raise RuntimeError(self.tr("error_download_stopped"))
            try:
                response = self.session.get(url, headers=headers, timeout=20, allow_redirects=True)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                    time.sleep(1.2 * attempt)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if content_type and not content_type.lower().startswith("image/"):
                    raise RuntimeError(self.tr("error_not_image", content_type=content_type))
                if not response.content:
                    raise RuntimeError(self.tr("error_empty_image"))
                with Image.open(BytesIO(response.content)) as image:
                    width, height = image.size
                    if width < MIN_PAGE_WIDTH or height < MIN_PAGE_HEIGHT:
                        raise RuntimeError(self.tr("error_tiny_image", width=width, height=height))
                    image.verify()
                ext = self.image_extension(url, content_type)
                final_path = temporary_path.with_suffix(ext)
                part_path = temporary_path.with_suffix(".part")
                part_path.write_bytes(response.content)
                part_path.replace(final_path)
                return self.convert_image_format(final_path, image_format)
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(1.2 * attempt)
        raise RuntimeError(str(last_error) if last_error else self.tr("error_no_saved_image"))

    def create_cbz(self, chapter_dir: Path, chapter_name: str, manga_dir: Path) -> Path:
        target = manga_dir / f"{sanitize_filename(chapter_name)}.cbz"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for image in self.sorted_images(chapter_dir):
                archive.write(image, image.name)
        return target

    def create_pdf(self, chapter_dir: Path, chapter_name: str, manga_dir: Path) -> Path:
        images = self.sorted_images(chapter_dir)
        if not images:
            raise RuntimeError(self.tr("error_pdf_no_images"))
        pil_images: list[Image.Image] = []
        try:
            for image_path in images:
                image = Image.open(image_path)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                pil_images.append(image)
            target = manga_dir / f"{sanitize_filename(chapter_name)}.pdf"
            pil_images[0].save(target, "PDF", resolution=100.0, save_all=True, append_images=pil_images[1:])
            return target
        finally:
            for image in pil_images:
                image.close()

    def download_chapter(
        self,
        manga_title: str,
        chapter: ChapterEntry,
        output_dir: str,
        chapter_index: int,
        total_chapters: int,
        progress: ProgressCallback | None = None,
    ) -> bool:
        progress = progress or (lambda value, text: None)
        settings = chapter.settings
        manga_dir = Path(output_dir) / sanitize_filename(manga_title, "Manga")
        chapter_dir = manga_dir / sanitize_filename(chapter.title, "Chapter")
        manga_dir.mkdir(parents=True, exist_ok=True)
        chapter_dir.mkdir(parents=True, exist_ok=True)
        self.log("log_download_chapter", title=chapter.title)
        page_url, image_urls = self.chapter_images(chapter.url, settings.reading_style)
        if not image_urls:
            self.log("log_no_images", title=chapter.title)
            return False
        self.log("log_images_found", count=len(image_urls))
        completed = 0
        failed = 0
        total_images = len(image_urls)
        executor = ThreadPoolExecutor(max_workers=max(1, settings.image_threads))
        futures = {}
        stop_requested = False
        try:
            for page_index, image_url in enumerate(image_urls, start=1):
                if self.stop_callback():
                    stop_requested = True
                    break
                future = executor.submit(
                    self.download_image,
                    image_url,
                    chapter_dir / f"{page_index:04d}.tmp",
                    page_url,
                    settings.image_format,
                )
                futures[future] = page_index

            for future in as_completed(futures):
                if self.stop_callback():
                    stop_requested = True
                    for pending in futures:
                        pending.cancel()
                    # Do not return from inside the futures loop. Let the finally
                    # block run, wait for active downloads to leave file I/O safely,
                    # then return after shutdown.
                    continue
                try:
                    future.result()
                    completed += 1
                except Exception as exc:
                    failed += 1
                    self.log("log_image_failed", number=f"{futures[future]:04d}", error=exc)
                overall = ((chapter_index - 1) + ((completed + failed) / max(1, total_images))) / max(1, total_chapters) * 100
                progress(overall, f"{chapter.title}: {completed + failed}/{total_images}")
        finally:
            # Keep shutdown synchronous. Returning while worker threads are still
            # writing files caused unstable behavior when Stop was pressed.
            executor.shutdown(wait=True, cancel_futures=True)
        if stop_requested or self.stop_callback():
            self.log("log_download_stopped_after_active")
            return False
        if completed == 0:
            raise RuntimeError(self.tr("error_no_saved_image"))
        write_chapter_metadata(output_dir, manga_title, chapter.title, chapter.url, completed)
        if settings.create_cbz:
            target = self.create_cbz(chapter_dir, chapter.title, manga_dir)
            self.log("log_cbz_saved", path=target)
        if settings.create_pdf:
            target = self.create_pdf(chapter_dir, chapter.title, manga_dir)
            self.log("log_pdf_saved", path=target)
        if not settings.preserve_images:
            shutil.rmtree(chapter_dir, ignore_errors=True)
            self.log("log_images_removed", title=chapter.title, path=chapter_dir)
        if settings.request_delay > 0:
            time.sleep(settings.request_delay)
        return failed == 0
