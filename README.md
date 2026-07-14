<div align="center">
  <img src="splash.png" alt="MangoDango Logo" width="500">

  **Manga archive, desktop reader, mobile reader and downloader for WeebCentral**

  <p>
    <img alt="Version" src="https://img.shields.io/badge/Version-1.6.0-F59E0B?style=for-the-badge">
    <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
    <img alt="PySide6" src="https://img.shields.io/badge/PySide6-Qt%20GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white">
    <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows%2011%20%7C%20Fedora%20%7C%20Linux%20Mint-lightgrey?style=for-the-badge">
    <img alt="License" src="https://img.shields.io/badge/License-MIT-00A36C?style=for-the-badge">
  </p>
</div>

---

## Overview

**MangoDango** is a PySide6 desktop application for building, managing and reading a personal manga collection downloaded from `https://weebcentral.com/`.

It combines four main functions in one application:

- a local **manga library** for downloaded series,
- a built-in **desktop manga reader**,
- a lightweight **mobile reader** for phones and tablets on the same local network,
- and a queue-based **manga downloader** for WeebCentral.

MangoDango can resolve complete series URLs or direct chapter URLs, download chapters in several formats, detect content already present on disk, check selected manga for new chapters, automate update checks and downloads, and run without a graphical interface on a server.

MangoDango only accepts WeebCentral manga and chapter URLs for downloading.

---

## Main Features

### Manga library

- Automatically scans the configured target folder for downloaded manga.
- Displays manga as cover cards with chapter count and latest chapter information.
- Sort the library by:
  - Last updated
  - A-Z
  - Favorites
- Open a manga directly in the integrated reader.
- A check mark appears next to the manga title when the newest locally available chapter has been read completely.
- The completion check is updated automatically when reading progress changes or a newer chapter is added.
- Library actions include:
  - Rename manga
  - Change cover image
  - Add or remove favorites
  - Add or remove a manga from automatic update checks
  - Open the original source page
  - Delete a manga and its local files

### Built-in desktop manga reader

- Reads locally stored manga from image folders and CBZ archives.
- Four display modes:
  - Single page
  - Double page
  - Long strip
  - Long strip with double pages
- Chapter and page navigation sidebar.
- Previous and next page controls.
- Zoom support with reset function.
- Remembers the last reading position.
- Can start from:
  - The beginning
  - The latest local chapter
  - The saved reading position, when available
- Automatically saves reading progress when the reader is closed.
- Shares reading progress with the mobile reader.

### Mobile reader for phones and tablets

MangoDango includes a lightweight web-based reader that can be opened from a phone or tablet connected to the same local network as the computer running MangoDango.

The mobile reader is focused on browsing and reading the existing local collection. It does not include a downloader.

Features include:

- Responsive manga library with cover cards.
- Original MangoDango branding and centered logo.
- The MangoDango logo and name act as a Home button and return to the library overview.
- Manga search and sorting.
- Favorites support.
- A completion check mark appears next to a manga title when the newest locally available chapter has been read completely.
- Chapter list sorted with the newest chapter at the top and the oldest chapter at the bottom.
- Quick reading choices:
  - Read from beginning
  - Continue reading
  - Read latest chapter
- Shared reading progress between desktop and mobile reader.
- Three mobile reading modes:
  - Single page
  - Double page
  - Long strip
- Single-page swipe navigation.
- Vertical and horizontal swipe gestures for changing pages.
- Snapping page transitions in single-page and double-page mode.
- Double-page spreads are handled as one zoomable unit.
- Pinch zoom and panning without scaling the application UI.
- The reader toolbar and page indicator hide automatically after two seconds.
- A short tap on a manga page shows the reader UI again.
- Normal scrolling does not reopen the UI.
- The page indicator is compact and unobtrusive.
- Chapter pages are preloaded before reading to reduce interruptions while scrolling or changing pages.
- The interface language automatically follows the language selected in the desktop application.
- Mobile manga actions include:
  - Rename manga
  - Change cover image
  - Add or remove favorites
  - Enable or disable automatic update checks
  - Open the original source page
  - Delete a manga

### Starting the mobile reader

Open:

```text
Settings → Mobile Reader
```

Enable the server and choose the port. The default port is:

```text
8765
```

MangoDango shows the local address that can be opened on the phone, for example:

```text
http://192.168.178.42:8765
```

The bind address can also be configured. The default is:

```text
0.0.0.0
```

This makes the mobile reader available on all local IPv4 interfaces.

When mDNS is available on the local network, the mobile reader can also be reached through:

```text
http://mangodango.local:8765
```

A public address such as `mangodango.de` cannot be created automatically by the application because it requires a registered domain and DNS configuration.

For security, the mobile reader is intended for private local networks. Do not expose its port directly to the public internet.

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

Run one update/download pass and exit:

```bash
python main.py --once
```

Run continuously and execute update checks according to the configured automation schedule:

```bash
python main.py --server
```

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

The updater distinguishes between operating-system-specific release assets so that the correct package can be selected for:

- Windows 11
- Fedora
- Linux Mint

Recommended release asset names are:

```text
MangoDango
MangoDango_Fedora
MangoDango_Mint
```

When a compatible update is available, MangoDango can download it, prepare installation, close the running application and restart after the update process begins.

### Splash screen

MangoDango displays `splash.png` during startup before the main application window appears.

The splash screen is included in the PyInstaller build configuration for supported desktop platforms.

---

## Localization

MangoDango includes 24 interface languages:

- Bulgarian
- Croatian
- Czech
- Danish
- Dutch
- English
- Estonian
- Finnish
- French
- German
- Greek
- Hungarian
- Irish
- Italian
- Latvian
- Lithuanian
- Maltese
- Polish
- Portuguese
- Romanian
- Slovak
- Slovenian
- Spanish
- Swedish

The mobile reader automatically follows the language selected in the desktop application.

Language files are stored separately under:

```text
mangodango/i18n/languages/
```

---

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

---

## Building standalone executables

MangoDango includes separate PyInstaller specifications for supported desktop platforms.

### Windows 11

```bash
pyinstaller --clean --noconfirm main_windows11.spec
```

### Fedora and Linux Mint

```bash
pyinstaller --clean --noconfirm main_linux.spec
```

The build specifications include the application icons, splash image and packaged MangoDango data files, including the mobile reader's HTML, CSS and JavaScript assets.

---

## Project structure

A simplified overview of the relevant files:

```text
MangoDango/
├── main.py
├── main_windows11.spec
├── main_linux.spec
├── requirements.txt
├── README.md
├── SERVER.md
├── MOBILE_READER.md
├── splash.png
├── logo.png
├── logo-small.png
└── mangodango/
    ├── app.py
    ├── main_window.py
    ├── mobile_server.py
    ├── reading_state.py
    ├── i18n/
    │   └── languages/
    ├── mobile/
    │   └── static/
    │       ├── index.html
    │       ├── app.css
    │       └── app.js
    └── ui/
        └── dialogs.py
```

---

## Notes and limitations

MangoDango is designed specifically for `https://weebcentral.com/`.

If WeebCentral changes its page structure, image delivery behavior, rate limits or protection mechanisms such as Cloudflare checks, downloading or update detection may temporarily fail until the scraper is adjusted.

The built-in readers currently expect locally available manga pages from image folders or CBZ archives. PDF files can be generated as an export format but are not the primary reader input format.

The mobile reader is intended for devices on the same trusted local network. Do not expose it directly to the public internet without additional protection.

Please respect the website's terms of service and only download content you are permitted to access.

---

## License

This project is released under the MIT License.

See the project license file and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for additional information about third-party components.
