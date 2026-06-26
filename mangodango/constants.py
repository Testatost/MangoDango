from pathlib import Path

APP_NAME = "MangoDango"
ORG_NAME = "LocalTools"
DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads" / APP_NAME)

READING_STYLES = ("long_strip", "single_page", "double_page", "double_page_mangaplus")
OUTPUT_MODES = ("images", "cbz", "pdf", "images_cbz", "images_pdf", "cbz_pdf", "images_cbz_pdf")
IMAGE_FORMATS = ("original", "jpg", "png", "webp")
THEME_MODES = ("dark", "light")
DEFAULT_ACCENT = "#7c9cff"
QUEUE_FILE_VERSION = 1
