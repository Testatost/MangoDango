<div align="center">
  <img src="logo-small.png" alt="MangoDango Logo" width="260">

  # MangoDango

  **Minimalist PySide6 downloader for WeebCentral**

  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
    <img alt="PySide6" src="https://img.shields.io/badge/PySide6-Qt%20GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white">
    <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=for-the-badge">
    <img alt="License" src="https://img.shields.io/badge/License-MIT-00A36C?style=for-the-badge">
  </p>
</div>

---

## Overview

**MangoDango** is a lightweight desktop downloader for manga chapters from `https://weebcentral.com/`.

The application is built around a clean queue-based workflow: manga are shown as parent entries, while their chapters are displayed underneath as indented child entries. Reading style, storage mode, and image format can be configured globally, per manga, or per individual chapter.

MangoDango only accepts WeebCentral URLs.

---

## Features

- Queue view with manga entries and indented chapter entries
- Support for series URLs and direct chapter URLs
- Individual settings per manga or chapter
- Editable dropdowns directly inside the queue:
  - Reading style
  - Storage mode
  - Image format
- Start, stop, and resume downloads
- Delete selected entries with `Delete` / `Del`
- Undo with `Ctrl+Z`
- Redo with `Ctrl+Y`
- Double-click any manga or chapter to open the source page in the browser
- Save and load queue files
- Progress bar and optional log output
- Automatic system-language detection on first launch
- Separate language files for EU languages
- Appearance customization with themes, custom colors, and saved variants
- Reset function for MangoDango settings and temporary files

---

## Installation

### Requirements

- Python 3.10 or newer
- Internet connection

### Clone the repository

```bash
git clone https://github.com/<your-name>/MangoDango.git
cd MangoDango
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Start the application

```bash
python main.py
```

---

## Usage

1. Paste one or more WeebCentral URLs.
2. Select an output folder, or use the default folder.
3. Configure the global settings for newly added links.
4. Add the links to the queue.
5. Adjust individual manga or chapter settings if needed.
6. Start the download.

Direct chapter links are added as individual chapters. Series links are resolved automatically, and the discovered chapters are displayed underneath the corresponding manga entry.

---

## Supported Settings

### Reading styles

- Long Strip
- Single Page
- Double Page
- Double Page (MangaPlus)

### Storage modes

- Images in folders
- CBZ
- PDF
- Combinations of images, CBZ, and PDF

### Image formats

- Original
- JPG
- PNG
- WebP

---

## Appearance Customization

MangoDango includes a built-in theme system. The appearance dialog allows you to select predefined themes, adjust individual colors, and save your own variants.

Included themes include:

- Original
- Light
- Midnight
- Paper
- Forest
- Ocean
- Sakura
- Terminal
- Lavender
- GPT
- Claude

Custom variants are stored locally and can be selected again later.

---

## Localization

MangoDango detects the system language on first launch and automatically applies the matching interface language when available.

Language files are stored separately from the main UI code so visible interface text does not need to be hardcoded inside the window logic.

```text
mangodango/i18n/languages/
```

Each language has its own Python file, making translations easier to extend, review, and maintain.

## Reset Function

The reset function removes local MangoDango settings and temporary MangoDango files.

Downloaded manga and chapter files are not deleted.

---

## Notes

MangoDango is limited to `https://weebcentral.com/`.

If WeebCentral enables protection mechanisms such as Cloudflare checks, rate limits, or changes to the page structure, downloads may temporarily fail. In that case, the scraper may need to be updated.

Please respect the terms of service of the website and only download content you are allowed to access.

---

## License

This project is released under the MIT License. See the project license file and `THIRD_PARTY_NOTICES.md` for details.
