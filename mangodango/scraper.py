from __future__ import annotations

import html as html_module
import re
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .constants import IMAGE_FORMATS, READING_STYLES
from .models import ChapterEntry, ItemSettings, MangaEntry

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PLACEHOLDER_MARKERS = (
    "broken_image", "favicon", "logo", "avatar", "placeholder", "failed", "missing",
    "error", "static/images", "assets/images", "data:image/", "base64,",
)
MIN_PAGE_WIDTH = 300
MIN_PAGE_HEIGHT = 300

LogCallback = Callable[[str], None]
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
    match = re.search(r"(?:chapter\s*)?(\d+(?:\.\d+)?)", str(name).lower())
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


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
            self.log_callback(self.tr(key_or_message, **kwargs))
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
        for element in reversed(elements):
            href = element.get("href")
            if not href:
                continue
            chapter_url = urljoin(self.base_url, str(href))
            parsed = urlparse(chapter_url)
            if parsed.netloc.lower() != "weebcentral.com" or chapter_url in seen:
                continue
            seen.add(chapter_url)
            label = element.select_one("span.flex > span") or element
            name = sanitize_filename(label.get_text(" ", strip=True), f"Chapter {len(chapters) + 1}")
            chapters.append(ChapterEntry(title=name, url=chapter_url))
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
        configured = configured if configured in READING_STYLES else "long_strip"
        result: list[str] = []
        for style in (configured, "long_strip", "single_page", "double_page", "double_page_mangaplus"):
            if style not in result:
                result.append(style)
        return result

    def image_page_url(self, chapter_url: str, reading_style: str) -> str:
        return f"{chapter_url.rstrip('/')}/images?reading_style={reading_style}"

    def chapter_images(self, chapter_url: str, reading_style: str) -> tuple[str, list[str]]:
        best_url = self.image_page_url(chapter_url, reading_style)
        best: list[str] = []
        for style in self.reading_style_candidates(reading_style):
            try:
                page_url = self.image_page_url(chapter_url, style)
                self.log("log_image_page", url=page_url)
                urls = self.extract_image_urls(self.fetch_html(page_url), page_url)
            except Exception:
                continue
            if len(urls) > len(best):
                best_url, best = page_url, urls
            if len(urls) >= 3:
                if style != reading_style:
                    self.log("log_style_fallback", style=style)
                return page_url, urls
            self.log("log_style_low_count", count=len(urls), style=style)
        if not best:
            self.log("log_try_chapter_page")
            return chapter_url, self.extract_image_urls(self.fetch_html(chapter_url), chapter_url)
        return best_url, best

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
            try:
                response = self.session.get(url, headers=headers, timeout=45, allow_redirects=True)
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
        with ThreadPoolExecutor(max_workers=max(1, settings.image_threads)) as executor:
            futures = {
                executor.submit(self.download_image, image_url, chapter_dir / f"{page_index:04d}.tmp", page_url, settings.image_format): page_index
                for page_index, image_url in enumerate(image_urls, start=1)
            }
            for future in as_completed(futures):
                if self.stop_callback():
                    return False
                try:
                    future.result()
                    completed += 1
                except Exception as exc:
                    failed += 1
                    self.log("log_image_failed", number=f"{futures[future]:04d}", error=exc)
                overall = ((chapter_index - 1) + ((completed + failed) / max(1, total_images))) / max(1, total_chapters) * 100
                progress(overall, f"{chapter.title}: {completed + failed}/{total_images}")
        if completed == 0:
            raise RuntimeError(self.tr("error_no_saved_image"))
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
