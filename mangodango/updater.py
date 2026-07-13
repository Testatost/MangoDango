"""Self-update support for MangoDango.

The updater talks only to the fixed public GitHub repository configured below.
In source mode it replaces the application source files after MangoDango exits.
In frozen/PyInstaller mode it prefers a platform-specific release asset and
replaces the running executable after the process has stopped.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import requests

from . import __version__

REPOSITORY_OWNER = "Testatost"
REPOSITORY_NAME = "MangoDango"
REPOSITORY_FULL_NAME = f"{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
REPOSITORY_URL = f"https://github.com/{REPOSITORY_FULL_NAME}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY_FULL_NAME}/releases/latest"

ProgressCallback = Callable[[int], None]
StopCallback = Callable[[], bool]


class UpdateError(RuntimeError):
    """Language-neutral updater failure identified by a stable error code."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "unexpected")
        super().__init__(self.code)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    name: str
    html_url: str
    download_url: str
    asset_name: str
    source_archive: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ReleaseInfo":
        return cls(
            version=str(data.get("version", "")),
            tag_name=str(data.get("tag_name", "")),
            name=str(data.get("name", "")),
            html_url=str(data.get("html_url", "")),
            download_url=str(data.get("download_url", "")),
            asset_name=str(data.get("asset_name", "")),
            source_archive=bool(data.get("source_archive", False)),
        )


def _version_parts(value: str) -> tuple[tuple[int, ...], bool]:
    text = str(value or "").strip().lstrip("vV")
    prerelease = bool(
        re.search(r"(?:^|[.\-_])(a|alpha|b|beta|rc|pre|preview|dev)\d*", text, re.IGNORECASE)
        or re.search(r"(?:a|alpha|b|beta|rc|pre|preview|dev)\d*$", text, re.IGNORECASE)
    )
    core = re.split(r"[-+]", text, maxsplit=1)[0]
    core = re.sub(r"(?:a|alpha|b|beta|rc|pre|preview|dev)\d*$", "", core, flags=re.IGNORECASE)
    numbers = tuple(int(part) for part in re.findall(r"\d+", core)[:6]) or (0,)
    return numbers, prerelease


def is_newer_version(remote: str, current: str = __version__) -> bool:
    """Compare common semantic-style version strings without extra dependencies."""
    remote_numbers, remote_pre = _version_parts(remote)
    current_numbers, current_pre = _version_parts(current)
    width = max(len(remote_numbers), len(current_numbers))
    remote_numbers = remote_numbers + (0,) * (width - len(remote_numbers))
    current_numbers = current_numbers + (0,) * (width - len(current_numbers))
    if remote_numbers != current_numbers:
        return remote_numbers > current_numbers
    # For identical numeric versions, a stable release is newer than a prerelease.
    return current_pre and not remote_pre


def _asset_score(name: str) -> int:
    lower = name.lower()
    if any(token in lower for token in ("sha256", "checksum", ".sig", ".asc", "symbols", "debug")):
        return -10_000
    if "source" in lower or re.search(r"(?:^|[-_.])src(?:[-_.]|$)", lower):
        return -500

    machine = platform.machine().lower()
    architecture_tokens = {
        "x86_64": ("x86_64", "amd64", "x64"),
        "amd64": ("x86_64", "amd64", "x64"),
        "aarch64": ("aarch64", "arm64"),
        "arm64": ("aarch64", "arm64"),
    }.get(machine, (machine,) if machine else ())

    if sys.platform == "win32":
        wanted = ("windows", "win64", "win32", "win")
        foreign = ("linux", "appimage", "macos", "darwin", "osx")
        extension_score = 80 if lower.endswith(".exe") else 45 if lower.endswith(".zip") else 0
    elif sys.platform == "darwin":
        wanted = ("macos", "darwin", "osx", "mac")
        foreign = ("windows", "win64", "win32", "linux", "appimage")
        extension_score = 55 if lower.endswith(".zip") else 35 if lower.endswith((".tar.gz", ".tgz")) else 0
    else:
        wanted = ("linux", "appimage")
        foreign = ("windows", "win64", "win32", "macos", "darwin", "osx")
        extension_score = 80 if lower.endswith(".appimage") else 45 if lower.endswith(".zip") else 35 if lower.endswith((".tar.gz", ".tgz")) else 0

    score = extension_score
    if any(token in lower for token in wanted):
        score += 60
    if any(token in lower for token in foreign):
        score -= 120
    if architecture_tokens and any(token in lower for token in architecture_tokens):
        score += 20
    if "mangodango" in lower:
        score += 10
    return score


def _select_compatible_asset(assets: list[dict]) -> dict | None:
    scored: list[tuple[int, dict]] = []
    for asset in assets:
        name = str(asset.get("name", "") or "")
        url = str(asset.get("browser_download_url", "") or "")
        if not name or not url:
            continue
        scored.append((_asset_score(name), asset))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored or scored[0][0] <= 0:
        return None
    return scored[0][1]


def fetch_latest_release(current_version: str = __version__, timeout: float = 30.0) -> ReleaseInfo:
    """Fetch the newest stable GitHub release and choose a downloadable payload."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"MangoDango/{current_version}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = requests.get(LATEST_RELEASE_API, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise UpdateError("github_unreachable") from exc

    if response.status_code == 404:
        raise UpdateError("no_release")
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise UpdateError("http_status") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise UpdateError("invalid_response") from exc

    tag_name = str(data.get("tag_name", "") or "").strip()
    version = tag_name.lstrip("vV") or str(data.get("name", "") or "").strip().lstrip("vV")
    if not version:
        raise UpdateError("missing_version")

    # Source mode can update reliably from GitHub's release source archive. A
    # frozen build needs a platform-specific binary asset instead.
    if not getattr(sys, "frozen", False):
        download_url = str(data.get("zipball_url", "") or "").strip()
        if not download_url:
            raise UpdateError("no_source_archive")
        asset_name = f"MangoDango-{tag_name or version}-source.zip"
        source_archive = True
    else:
        asset = _select_compatible_asset(list(data.get("assets") or []))
        if asset is None:
            raise UpdateError("no_compatible_package")
        download_url = str(asset.get("browser_download_url", "") or "").strip()
        asset_name = str(asset.get("name", "") or "MangoDango-update.bin")
        source_archive = False

    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        name=str(data.get("name", "") or tag_name or version),
        html_url=str(data.get("html_url", "") or REPOSITORY_URL),
        download_url=download_url,
        asset_name=asset_name,
        source_archive=source_archive,
    )


def download_release(
    release: ReleaseInfo,
    progress: ProgressCallback | None = None,
    stop: StopCallback | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download a release payload to a dedicated temporary directory."""
    progress = progress or (lambda _value: None)
    stop = stop or (lambda: False)
    download_dir = Path(tempfile.mkdtemp(prefix="mangodango-update-download-"))
    safe_name = Path(release.asset_name).name or "MangoDango-update.bin"
    target = download_dir / safe_name
    headers = {"User-Agent": f"MangoDango/{__version__}"}

    try:
        with requests.get(release.download_url, headers=headers, stream=True, timeout=timeout, allow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", "0") or 0)
            received = 0
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if stop():
                        raise UpdateError("download_cancelled")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    received += len(chunk)
                    if total > 0:
                        progress(max(0, min(100, int(received * 100 / total))))
            if target.stat().st_size <= 0:
                raise UpdateError("empty_package")
            progress(100)
            return target
    except Exception:
        shutil.rmtree(download_dir, ignore_errors=True)
        raise


def cleanup_download(path: str | Path) -> None:
    """Remove a downloaded update and its dedicated temporary directory."""
    try:
        path = Path(path)
        parent = path.parent
        if parent.name.startswith("mangodango-update-download-"):
            shutil.rmtree(parent, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except Exception:
        pass


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            member_path = (destination / member.filename).resolve()
            if member_path != destination and destination not in member_path.parents:
                raise UpdateError("unsafe_archive")
        archive.extractall(destination)


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            member_path = (destination / member.name).resolve()
            if member_path != destination and destination not in member_path.parents:
                raise UpdateError("unsafe_archive")
        archive.extractall(destination)


def _extract_archive(archive_path: Path, destination: Path) -> None:
    lower = archive_path.name.lower()
    if lower.endswith(".zip"):
        _safe_extract_zip(archive_path, destination)
    elif lower.endswith((".tar.gz", ".tgz", ".tar")):
        _safe_extract_tar(archive_path, destination)
    else:
        raise UpdateError("unsupported_archive")


def _find_source_root(extracted: Path) -> Path:
    candidates = [extracted]
    candidates.extend(path for path in extracted.rglob("main.py") if path.is_file())
    for candidate in candidates:
        root = candidate if candidate.is_dir() else candidate.parent
        if (root / "main.py").is_file() and (root / "mangodango").is_dir():
            return root
    raise UpdateError("incomplete_installation")


def _detached_popen(command: list[str], **kwargs) -> subprocess.Popen:
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        return subprocess.Popen(command, creationflags=flags, close_fds=True, **kwargs)
    return subprocess.Popen(command, start_new_session=True, close_fds=True, **kwargs)


def _source_helper_code() -> str:
    return r'''from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

pid = int(sys.argv[1])
target = Path(sys.argv[2]).resolve()
payload = Path(sys.argv[3]).resolve()
restart_command = json.loads(sys.argv[4])
work_dir = Path(sys.argv[5]).resolve()
backup = work_dir / "backup"


def process_alive(value: int) -> bool:
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


for _ in range(1200):
    if not process_alive(pid):
        break
    time.sleep(0.25)
else:
    raise SystemExit("MANGODANGO_UPDATE_TIMEOUT")

managed = [entry.name for entry in payload.iterdir()]
backup.mkdir(parents=True, exist_ok=True)
try:
    for name in managed:
        source = payload / name
        destination = target / name
        saved = backup / name
        if destination.exists() or destination.is_symlink():
            shutil.move(str(destination), str(saved))
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

    subprocess.Popen(restart_command, cwd=str(target), start_new_session=(os.name != "nt"), close_fds=True)
    shutil.rmtree(work_dir, ignore_errors=True)
except Exception:
    for name in managed:
        destination = target / name
        saved = backup / name
        try:
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination, ignore_errors=True)
            elif destination.exists() or destination.is_symlink():
                destination.unlink(missing_ok=True)
            if saved.exists() or saved.is_symlink():
                shutil.move(str(saved), str(destination))
        except Exception:
            pass
    raise
'''


def _schedule_source_install(package_path: Path, release: ReleaseInfo) -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="mangodango-update-stage-"))
    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        _extract_archive(package_path, extract_dir)
        source_root = _find_source_root(extract_dir)
        payload = work_dir / "payload"
        shutil.copytree(source_root, payload)

        target_root = Path(__file__).resolve().parents[1]
        if not (target_root / "main.py").is_file() or not (target_root / "mangodango").is_dir():
            raise UpdateError("source_install_unknown")
        if not os.access(target_root, os.W_OK):
            raise UpdateError("install_dir_unwritable")
        helper = work_dir / "install_update.py"
        helper.write_text(_source_helper_code(), encoding="utf-8")
        restart_command = [sys.executable, str(target_root / "main.py")]
        _detached_popen([
            sys.executable,
            str(helper),
            str(os.getpid()),
            str(target_root),
            str(payload),
            json.dumps(restart_command),
            str(work_dir),
        ], cwd=str(target_root))
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def _find_frozen_payload(package_path: Path, target_executable: Path, work_dir: Path) -> Path:
    lower = package_path.name.lower()
    if lower.endswith((".zip", ".tar.gz", ".tgz", ".tar")):
        extracted = work_dir / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        _extract_archive(package_path, extracted)
        files = [path for path in extracted.rglob("*") if path.is_file()]
        if not files:
            raise UpdateError("no_program_file")

        exact = [path for path in files if path.name.casefold() == target_executable.name.casefold()]
        if exact:
            return max(exact, key=lambda path: path.stat().st_size)
        if sys.platform == "win32":
            matching = [path for path in files if path.suffix.lower() == ".exe"]
        elif sys.platform.startswith("linux"):
            matching = [path for path in files if path.name.lower().endswith(".appimage") or os.access(path, os.X_OK)]
        else:
            matching = [path for path in files if os.access(path, os.X_OK)]
        if matching:
            return max(matching, key=lambda path: path.stat().st_size)
        raise UpdateError("no_matching_executable")
    return package_path


def _schedule_frozen_install(package_path: Path, release: ReleaseInfo) -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="mangodango-update-stage-"))
    try:
        target_text = os.environ.get("APPIMAGE", "") if sys.platform.startswith("linux") else ""
        target = Path(target_text or sys.executable).resolve()
        payload_source = _find_frozen_payload(package_path, target, work_dir)
        payload = work_dir / target.name
        shutil.copy2(payload_source, payload)
        if os.name != "nt":
            payload.chmod(payload.stat().st_mode | 0o111)

        if os.name == "nt":
            script = work_dir / "install_update.ps1"
            script.write_text(
                "\n".join([
                    "$ErrorActionPreference = 'Stop'",
                    f"$pidToWait = {os.getpid()}",
                    f"$source = {json.dumps(str(payload))}",
                    f"$target = {json.dumps(str(target))}",
                    "while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 300 }",
                    "Copy-Item -LiteralPath $source -Destination $target -Force",
                    "Start-Process -FilePath $target",
                    f"Remove-Item -LiteralPath {json.dumps(str(work_dir))} -Recurse -Force -ErrorAction SilentlyContinue",
                ]),
                encoding="utf-8",
            )
            _detached_popen([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)
            ])
        else:
            script = work_dir / "install_update.sh"
            quoted_payload = shlex.quote(str(payload))
            quoted_target = shlex.quote(str(target))
            quoted_work = shlex.quote(str(work_dir))
            script.write_text(
                "#!/bin/sh\n"
                f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done\n"
                f"cp -f {quoted_payload} {quoted_target}\n"
                f"chmod +x {quoted_target}\n"
                f"nohup {quoted_target} >/dev/null 2>&1 &\n"
                f"rm -rf {quoted_work}\n",
                encoding="utf-8",
            )
            script.chmod(0o700)
            _detached_popen(["/bin/sh", str(script)])
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def schedule_update_install(release: ReleaseInfo, package_path: str | Path) -> None:
    """Stage an update and launch an external helper that installs after exit."""
    package_path = Path(package_path)
    if not package_path.exists() or package_path.stat().st_size <= 0:
        raise UpdateError("package_missing")
    if getattr(sys, "frozen", False):
        _schedule_frozen_install(package_path, release)
    else:
        _schedule_source_install(package_path, release)
