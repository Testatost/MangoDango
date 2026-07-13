<div align="center">
  <img src="splash.png" alt="MangoDango Logo" width="500">

  **Manga archive, manga reader and downloader for WeebCentral**

  <p>
    <img alt="Version" src="https://img.shields.io/badge/Version-1.5.0-F59E0B?style=for-the-badge">
    <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
    <img alt="PySide6" src="https://img.shields.io/badge/PySide6-Qt%20GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white">
    <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows%2011%20%7C%20Fedora%20%7C%20Linux%20Mint-lightgrey?style=for-the-badge">
    <img alt="License" src="https://img.shields.io/badge/License-MIT-00A36C?style=for-the-badge">
  </p>
</div>

---

## Overview

**MangoDango** is a PySide6 desktop application that combines three main functions in one interface:

- a local **manga archive/library** for downloaded series,
- a built-in **manga reader** for locally stored image folders and CBZ archives,
- and a queue-based **manga downloader** for `https://weebcentral.com/`.

The application can resolve complete series URLs or direct chapter URLs, download chapters in several formats, track already downloaded content, check for new chapters, automate update checks, and run without a graphical interface on a server.

MangoDango only accepts WeebCentral manga and chapter URLs.

---

## Main Features

### Manga library

- Automatically scans the configured target folder for downloaded manga.
- Displays manga as cover cards with chapter count and latest chapter information.
- Sorts the library by:
  - Last updated
  - A-Z
  - Favorites
- Open a manga directly in the integrated reader.
- Library actions include:
  - Rename manga
  - Change cover image
  - Mark as read or unread
  - Add or remove favorites
  - Add or remove a manga from automatic update checks
  - Open the original source page
  - Delete a manga and its local files

### Built-in manga reader

- Reads locally stored manga from image folders and CBZ archives.
- Four display modes:
  - Single page
  - Double page
  - Long strip
  - Long strip with double pages
- Chapter and page navigation sidebar.
- Previous/next page controls.
- Zoom support with reset function.
- Remembers the last reading position.
- Can start from:
  - The beginning
  - The latest local chapter
  - The saved reading position, when available
- Automatically saves reading progress when the reader is closed.

### Queue-based downloader

- Queue view with manga entries and indented chapter entries.
- Supports complete series URLs and direct chapter URLs.
- Individual settings per manga or chapter.
- Editable queue settings for:
  - Reading style
  - Storage mode
  - Image format
- Enable or disable complete manga entries or individual chapters.
- Start, stop and resume downloads.
- Detects chapters already present on disk and skips them when appropriate.
- Configurable parallel image downloads.
- Configurable delay between chapter requests.
- Progress, status and ETA columns.
- Optional log/output panel.
- Save and load queue files.
- Search inside the queue.
- Open a manga or chapter source page by double-clicking its queue entry.
- Remove selected entries with `Delete` / `Del`.
- Undo with `Ctrl+Z`.
- Redo with `Ctrl+Y`.

### Update checks and automation

- Check downloaded manga for newly available chapters.
- Optional update check when MangoDango starts.
- Optional automatic download of newly found chapters.
- Per-manga control over:
  - Update checks
  - Automatic downloads
- Dedicated manga list inside the settings dialog.
- Manual **Check now** and **Download now** actions.
- Scheduled automation with multiple day/time entries.
- The same schedule can be used by the GUI and the headless server mode.

### Headless server mode

MangoDango can run without a graphical desktop and use `QCoreApplication` only. This is useful for servers, cron jobs and systemd services.

Supported modes include:

```bash
python main.py --once
```

Run one update/download pass and exit.

```bash
python main.py --server
```

Run continuously and execute update checks according to the automation schedule configured in the GUI.

Useful options:

| Option | Description |
|---|---|
| `--output DIR` | Target folder to scan and manage |
| `--queue FILE` | Include an optional saved queue file |
| `--lang CODE` | Language code for log output, for example `de` or `en` |
| `--interval SECONDS` | Poll interval for the automation schedule; default is 60 seconds |
| `--download` | Download newly found chapters |
| `--no-download` | Only check for updates without downloading |

See [SERVER.md](SERVER.md) for a more detailed server-mode guide and a systemd example.

### Built-in application updater

The **Help** tab in the settings dialog can check the official MangoDango GitHub repository for a newer release.

When a compatible update is available, MangoDango can download it, prepare installation, close the running application and restart after the update process begins.

______

## Installation from source

### Requirements

- Python 3.10 or newer
- Internet connection
- A graphical desktop for GUI mode

### Clone the repository

```bash
git clone https://github.com/Testatost/MangoDango.git
cd MangoDango
```

### Install dependencies

```bash
python -m pip install -r requirements.txt
```

### Start the graphical application

```bash
python main.py
```

The default target folder is:

```text
~/Downloads/MangoDango
```

A different target folder can be selected in the application settings.

____

## Notes and Limitations

MangoDango is designed specifically for `https://weebcentral.com/`.

If WeebCentral changes its page structure, image delivery behavior, rate limits or protection mechanisms such as Cloudflare checks, downloading or update detection may temporarily fail until the scraper is adjusted.

The built-in reader currently expects locally available manga pages from image folders or CBZ archives. PDF files are generated as an export format but are not the reader's primary input format.

Please respect the website's terms of service and only download content you are permitted to access.

---

## License

This project is released under the MIT License.

See the project license file and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for additional information about third-party components.
