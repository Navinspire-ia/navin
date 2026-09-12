# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Secure, signed updates for frozen Navin binaries: desktop app and CLI."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

from navin import __version__
from navin.bus.notify import notify
from navin.config.loader import load_config
from navin.process_runtime import child_environment
from navin.utils.proc import detached_no_window_kwargs, no_window_kwargs

_CACHE_TTL_S = 6 * 60 * 60
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_STATE_LOCK = threading.Lock()
_OPERATION_LOCK = threading.Lock()
_DOWNLOAD_WORKER: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "state": "idle",
    "progress": 0,
    "downloadedBytes": 0,
    "totalBytes": 0,
}
_CACHE: tuple[float, dict[str, Any] | None] = (0.0, None)


class UpdateError(RuntimeError):
    """A safe, user-facing update failure."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _set_state(**values: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(values)


def _set_checked_release(info: dict[str, Any]) -> None:
    with _STATE_LOCK:
        if _STATE.get("state") in {"downloading", "installing", "restarting"}:
            return
        if _STATE.get("state") == "ready" and _STATE.get("update") == info:
            return
        _STATE.update(
            state="available" if info.get("available") else "idle",
            update=info,
            error=None,
            path=None,
            progress=0,
        )


def _update_failure(exc: Exception) -> UpdateError:
    error = exc if isinstance(exc, UpdateError) else UpdateError(
        f"Could not complete the update: {exc}", status=503,
    )
    _set_state(state="error", error=str(error))
    return error


def _run_update_operation(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    if not _OPERATION_LOCK.acquire(blocking=False):
        raise UpdateError("An update is already in progress", status=409)
    try:
        return action()
    except (UpdateError, OSError, subprocess.SubprocessError) as exc:
        error = _update_failure(exc)
        if error is exc:
            raise
        raise error from exc
    finally:
        _OPERATION_LOCK.release()


_INSTALL_KINDS = frozenset(
    {
        "cli",
        "windows-setup",
        "windows-portable",
        "linux",
        "linux-appimage",
        "linux-package",
        "macos",
        "macos-app",
    }
)

# Installs owned by something else: a distribution package manager put these
# files there, and replacing them behind its back leaves a machine whose
# package database disagrees with its disk.
_MANAGED_KINDS = frozenset({"linux-package"})

# The directory PyInstaller builds and every install ships: the CLI archives
# (curl https://navin.live/install), the desktop bundles, all of them.
_CLI_TREE_NAME = "navin-dist"

# The window executable the desktop installers put next to the sidecar tree.
# Its presence means the setup, DMG or AppImage updater owns these files.
_DESKTOP_MARKERS = ("Navin.exe", "navin-desktop.exe", "navin-desktop", "Navin")


def _cli_binary_name() -> str:
    return "navin.exe" if sys.platform == "win32" else "navin"


def _cli_tree() -> Path | None:
    """The ``navin-dist/`` directory this CLI runs from, when it is ours to replace.

    The one-line installer unpacks the CLI archive somewhere the user owns
    (``~/.local/share/navin/pkg``, ``%LOCALAPPDATA%\\Navin\\cli``) and points
    ``navin`` / ``navin-cli`` wrappers at ``navin-dist/navin``. That whole
    directory is the unit of update: swap it, and the wrappers keep working.

    The same tree inside a desktop install is not ours: the setup, the DMG or
    the AppImage replaces it as part of the application, and a tree the user
    cannot write to belongs to whoever installed it.
    """
    if not getattr(sys, "frozen", False):
        return None
    try:
        executable = Path(sys.executable).resolve()
    except OSError:
        return None
    tree = executable.parent
    if tree.name != _CLI_TREE_NAME or executable.name.lower() != _cli_binary_name():
        return None
    if _app_bundle_path() is not None:
        return None
    parent = tree.parent
    # Tauri installs the tree right next to the window executable; older
    # layouts had it one level down under binaries/ or resources/. Not
    # further: %LOCALAPPDATA%\Navin\cli\navin-dist is the CLI's own home even
    # when the desktop app is installed in %LOCALAPPDATA%\Navin as well.
    homes = [parent]
    if parent.name.lower() in {"binaries", "resources"}:
        homes.append(parent.parent)
    for home in homes:
        if any((home / marker).is_file() for marker in _DESKTOP_MARKERS):
            return None
    if not os.access(parent, os.W_OK):
        return None
    return tree


def _install_kind() -> str:
    # The desktop shell knows what the user actually installed, which this
    # process cannot see: its own path is a sidecar inside the app. It states
    # the kind when it spawns the gateway.
    explicit = os.environ.get("NAVIN_INSTALL_KIND", "").strip().lower()
    if explicit in _INSTALL_KINDS:
        return explicit
    # The AppImage runtime sets this even when an older shell forgot to name
    # the install. Without it a frozen sidecar reports kind "linux", looks up
    # linux-x64 in the manifest, finds nothing, and the toast never appears.
    if os.environ.get("APPIMAGE", "").strip():
        return "linux-appimage"
    if not getattr(sys, "frozen", False):
        return "source"
    if _cli_tree() is not None:
        return "cli"
    stem = Path(sys.executable).stem.lower()
    if sys.platform == "win32":
        return "windows-portable" if "portable" in stem else "windows-setup"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        # The two macOS shapes are updated differently: the DMG installs an
        # application bundle, while the bare binary is replaced in place exactly
        # like the Linux one. The sidecar lives in Contents/Resources/navin-dist
        # inside the app, so detect the bundle by walking parents, not by the
        # Contents/MacOS marker.
        return "macos-app" if _app_bundle_path() is not None else "macos"
    return "unsupported"


def _app_bundle_path() -> Path | None:
    """The ``.app`` directory this process runs from, if any."""
    announced = _desktop_app()
    if announced is not None and announced.suffix == ".app":
        return announced
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _strip_windows_extended_prefix(text: str) -> str:
    """Drop the ``\\\\?\\`` prefix Windows adds so paths can exceed MAX_PATH.

    .NET Framework ``Path.GetFullPath`` treats ``?`` as an illegal character,
    which is the dialog users actually saw: "Caractères non conformes dans le
    chemin d'accès." Tauri ``canonicalize`` and Python ``Path.resolve`` both
    emit that prefix on Windows.
    """
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    if text.startswith("//?/UNC/"):
        return "//" + text[8:]
    if text.startswith("//?/"):
        return text[4:]
    return text


def _os_path(path: Path | str) -> str:
    """A path the platform updater (C# helper, hdiutil, cp) can open."""
    return _strip_windows_extended_prefix(os.fspath(path))


def _desktop_app() -> Path | None:
    """The installed application the desktop shell announced, if any.

    This is what an update replaces: the .AppImage the user launched, Navin.exe,
    the .app bundle. The sidecar's own path is inside it, often on a read-only
    mount, and replacing that would update nothing the user can see.
    """
    for raw in (
        os.environ.get("NAVIN_DESKTOP_APP", ""),
        os.environ.get("APPIMAGE", ""),
    ):
        cleaned = _strip_windows_extended_prefix(raw.strip())
        if not cleaned:
            continue
        path = Path(cleaned)
        if path.exists():
            return path
    return None


def _desktop_pid() -> int:
    """PID of the window to close before its files can be replaced, or 0."""
    raw = os.environ.get("NAVIN_DESKTOP_PID", "").strip()
    try:
        pid = int(raw)
    except ValueError:
        return 0
    return pid if pid > 1 else 0


def _arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in {"arm64", "aarch64"} else "x64"


def _cli_os() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _platform_key(kind: str) -> str:
    # The desktop kinds already name their OS; the CLI archive is the same
    # shape everywhere, so its key carries the OS: cli-linux-x64, cli-macos-arm64,
    # cli-windows-x64. These are the names the publisher signs.
    if kind == "cli":
        return f"cli-{_cli_os()}-{_arch()}"
    return f"{kind}-{_arch()}"


def _platform_keys(kind: str) -> tuple[str, ...]:
    """Native artifact first. Apple Silicon falls back to Intel if arm64 is missing."""
    preferred = _platform_key(kind)
    apple = kind.startswith("macos") or preferred.startswith("cli-macos")
    if apple and preferred.endswith("-arm64"):
        return (preferred, preferred[: -len("-arm64")] + "-x64")
    return (preferred,)


def _dev_overrides_allowed() -> bool:
    """Whether the dev-only env overrides are honored.

    NAVIN_UPDATE_PUBLIC_KEY and NAVIN_UPDATE_ALLOW_HTTP exist to test the
    update pipeline from a source checkout against a local server. In a
    packaged build they would let anything able to set an environment
    variable swap the signature key or downgrade to plain HTTP - an update
    hijack. So packaged builds ignore them unconditionally.
    """
    return not getattr(sys, "frozen", False)


def _public_key() -> Ed25519PublicKey:
    raw = os.environ.get("NAVIN_UPDATE_PUBLIC_KEY", "").strip() if _dev_overrides_allowed() else ""
    if not raw:
        key_path = Path(__file__).with_name("public_key.txt")
        if key_path.exists():
            raw = key_path.read_text(encoding="ascii").strip()
    if not raw:
        raise UpdateError("Update signing key is not configured", status=503)
    try:
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) != 32:
            raise ValueError("wrong key length")
        return Ed25519PublicKey.from_public_bytes(decoded)
    except (ValueError, TypeError) as exc:
        raise UpdateError("Update signing key is invalid", status=503) from exc


def _update_config() -> tuple[str, str, str]:
    config = load_config().updates
    bundled_url_path = Path(__file__).with_name("default_base_url.txt")
    bundled_url = (
        bundled_url_path.read_text(encoding="utf-8").strip()
        if bundled_url_path.exists()
        else ""
    )
    base_url = (
        os.environ.get("NAVIN_UPDATE_BASE_URL", "").strip()
        or config.base_url.strip()
        or bundled_url
    ).rstrip("/")
    return base_url, config.channel, config.skipped_version.strip()


def _validate_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    allow_http = _dev_overrides_allowed() and os.environ.get("NAVIN_UPDATE_ALLOW_HTTP") == "1"
    if parsed.scheme != "https" and not (
        allow_http and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        raise UpdateError("Update server must use HTTPS", status=503)
    if not parsed.hostname or parsed.username or parsed.password:
        raise UpdateError("Update server URL is invalid", status=503)


def _artifact_url(base_url: str, raw_url: str) -> str:
    url = urljoin(f"{base_url}/", raw_url)
    base = urlparse(base_url)
    parsed = urlparse(url)
    if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
        raise UpdateError("Update artifact points to an untrusted host")
    return url


def _fetch_manifest(base_url: str, channel: str) -> dict[str, Any]:
    _validate_base_url(base_url)
    manifest_url = f"{base_url}/{channel}/manifest.json"
    signature_url = f"{base_url}/{channel}/manifest.sig"
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            manifest_response = client.get(manifest_url)
            manifest_response.raise_for_status()
            signature_response = client.get(signature_url)
            signature_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpdateError("Could not reach the update server", status=503) from exc

    raw = manifest_response.content
    if len(raw) > 1024 * 1024:
        raise UpdateError("Update manifest is unexpectedly large")
    try:
        signature = base64.b64decode(signature_response.text.strip(), validate=True)
        _public_key().verify(signature, raw)
    except Exception as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("Update manifest signature verification failed", status=503) from exc
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("Update manifest is malformed", status=503) from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise UpdateError("Unsupported update manifest format", status=503)
    if payload.get("channel") != channel:
        raise UpdateError("Update manifest channel mismatch", status=503)
    return payload


def _release_info(
    manifest: dict[str, Any],
    *,
    base_url: str,
    skipped_version: str,
) -> dict[str, Any] | None:
    latest = str(manifest.get("version", "")).strip()
    try:
        current_version = Version(__version__)
        latest_version = Version(latest)
    except InvalidVersion as exc:
        raise UpdateError("Update manifest contains an invalid version", status=503) from exc
    if latest_version <= current_version or latest == skipped_version:
        return None
    rollout = int(manifest.get("rollout", 100))
    if rollout < 0 or rollout > 100:
        raise UpdateError("Update manifest rollout is invalid", status=503)
    identity_path = Path.home() / ".navin" / "update-id"
    try:
        if identity_path.exists():
            identity = identity_path.read_text(encoding="ascii").strip()
        else:
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            identity = str(uuid.uuid4())
            identity_path.write_text(identity + "\n", encoding="ascii")
    except OSError:
        identity = platform.node() or "navin"
    bucket = int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16) % 100
    if bucket >= rollout:
        return None

    minimum = str(manifest.get("minimumVersion", "")).strip()
    try:
        mandatory = bool(minimum and current_version < Version(minimum))
    except InvalidVersion as exc:
        raise UpdateError("Update manifest minimum version is invalid", status=503) from exc
    kind = _install_kind()
    if kind == "unsupported":
        return None
    if kind == "source" or kind in _MANAGED_KINDS:
        # Tell the user a release exists. Do not offer a button that cannot
        # replace a git tree or a package-manager install.
        # There is a newer version and the user deserves to hear about it, but
        # these files belong to the package manager. Naming where the update
        # comes from is better than a button that cannot honour its promise.
        return {
            "currentVersion": __version__,
            "latestVersion": latest,
            "available": True,
            "supported": False,
            "installKind": kind,
            "notes": str(manifest.get("notes", "")),
            "mandatory": mandatory,
            "reason": (
                "This session is a source checkout. Upgrade the packaged CLI with: navin update"
                if kind == "source"
                else "Navin was installed from a system package. Update it with your package manager."
            ),
        }
    artifacts = manifest.get("artifacts") or {}
    artifact = None
    for key in _platform_keys(kind):
        candidate = artifacts.get(key)
        if isinstance(candidate, dict):
            artifact = candidate
            break
    if not isinstance(artifact, dict):
        # A newer version on another OS (Windows 1.0.1 while macOS is still
        # 1.0.0) must not raise a toast on machines that have no binary yet.
        return None
    raw_url = str(artifact.get("url", ""))

    size = int(artifact.get("size", 0))
    digest = str(artifact.get("sha256", "")).lower()
    if size <= 0 or size > _MAX_ARTIFACT_BYTES:
        raise UpdateError("Update artifact size is invalid", status=503)
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise UpdateError("Update artifact checksum is invalid", status=503)
    return {
        "currentVersion": __version__,
        "latestVersion": latest,
        "available": True,
        "supported": True,
        "installKind": kind,
        "notes": str(manifest.get("notes", "")),
        "mandatory": mandatory,
        "artifact": {
            "url": _artifact_url(base_url, raw_url),
            "size": size,
            "sha256": digest,
        },
    }


def check_for_update(*, force: bool = False) -> dict[str, Any]:
    """Fetch and verify the signed manifest, returning updater state."""
    global _CACHE
    base_url, channel, skipped_version = _update_config()
    kind = _install_kind()
    if not base_url:
        result = {
            "currentVersion": __version__,
            "available": False,
            "configured": False,
            "installKind": kind,
            "channel": channel,
        }
        _set_checked_release(result)
        return result

    now = time.monotonic()
    cached_at, cached = _CACHE
    if not force and cached is not None and now - cached_at < _CACHE_TTL_S:
        return cached
    try:
        manifest = _fetch_manifest(base_url, channel)
        release = _release_info(manifest, base_url=base_url, skipped_version=skipped_version)
        result = release or {
            "currentVersion": __version__,
            "available": False,
            "configured": True,
            "installKind": kind,
        }
        result["configured"] = True
        result["channel"] = channel
        _CACHE = (now, result)
        _set_checked_release(result)
        return result
    except UpdateError as exc:
        with _STATE_LOCK:
            if _STATE.get("state") not in {"downloading", "ready", "installing", "restarting"}:
                _STATE.update(state="error", error=str(exc))
        raise


def updates_configured() -> bool:
    """Return whether this build has a usable update server URL."""
    return bool(_update_config()[0])


def _download_path(version: str, kind: str) -> Path:
    if kind == "cli":
        suffix = ".zip" if sys.platform == "win32" else ".tar.gz"
    elif kind.startswith("windows"):
        suffix = ".exe"
    elif kind == "macos-app":
        # hdiutil refuses to attach an image whose name does not end in .dmg.
        suffix = ".dmg"
    elif kind == "linux-appimage":
        suffix = ".AppImage"
    else:
        suffix = ""
    root = Path.home() / ".navin" / "updates" / version
    root.mkdir(parents=True, exist_ok=True)
    return root / f"navin-update-{kind}{suffix}"


def download_update() -> dict[str, Any]:
    """Download the currently available update and verify it byte-for-byte."""
    return _run_update_operation(_download_update)


def start_update_download() -> dict[str, Any]:
    """Start one download and return immediately so the UI can poll progress."""
    global _DOWNLOAD_WORKER
    if not _OPERATION_LOCK.acquire(blocking=False):
        return update_status()
    with _STATE_LOCK:
        ready = _STATE.get("state") == "ready" and bool(_STATE.get("path"))
        restarting = _STATE.get("state") == "restarting"
    if restarting or (ready and Path(str(_STATE["path"])).is_file()):
        _OPERATION_LOCK.release()
        return update_status()
    _set_state(
        state="downloading", progress=0, downloadedBytes=0, totalBytes=0,
        path=None, error=None,
    )

    def run() -> None:
        try:
            _download_update()
        except Exception as exc:  # noqa: BLE001 - every worker failure must reach the UI
            _update_failure(exc)
        finally:
            _OPERATION_LOCK.release()

    _DOWNLOAD_WORKER = threading.Thread(target=run, name="navin-update-download", daemon=True)
    try:
        _DOWNLOAD_WORKER.start()
    except RuntimeError as exc:
        _OPERATION_LOCK.release()
        raise _update_failure(exc) from exc
    return update_status()


def _download_update() -> dict[str, Any]:
    info = check_for_update()
    if not info.get("available"):
        raise UpdateError("No update is available", status=409)
    if not info.get("supported"):
        raise UpdateError("This installation cannot be updated automatically", status=409)
    artifact = info["artifact"]
    destination = _download_path(info["latestVersion"], info["installKind"])
    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    downloaded = 0
    expected = int(artifact["size"])
    _set_state(
        state="downloading",
        progress=0,
        downloadedBytes=0,
        totalBytes=expected,
        error=None,
        update=info,
        path=None,
    )
    try:
        with httpx.stream(
            "GET",
            artifact["url"],
            timeout=httpx.Timeout(30.0, read=120.0),
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > expected or downloaded > _MAX_ARTIFACT_BYTES:
                        raise UpdateError("Update download exceeded the signed size")
                    stream.write(chunk)
                    digest.update(chunk)
                    _set_state(
                        progress=min(99, int(downloaded * 100 / expected)),
                        downloadedBytes=downloaded,
                    )
        if downloaded != expected:
            raise UpdateError("Update download size does not match the signed manifest")
        if digest.hexdigest() != artifact["sha256"]:
            raise UpdateError("Update checksum verification failed")
        os.replace(partial, destination)
        if info["installKind"] in {"linux", "linux-appimage", "macos"}:
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
        _set_state(state="ready", progress=100, downloadedBytes=downloaded, path=str(destination))
        return {**info, "state": "ready", "path": str(destination)}
    except (httpx.HTTPError, OSError) as exc:
        partial.unlink(missing_ok=True)
        message = str(exc) if isinstance(exc, UpdateError) else "Update download failed"
        _set_state(state="error", error=message)
        raise UpdateError(message, status=503) from exc
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise


# Nothing may touch the application's files while it is still running: a
# process cannot replace the image it executes from. The gateway stops itself a
# moment after answering the request that started this, so it is waited on; the
# desktop window has no reason to close on its own, so it is asked, then told.
# $1 is the gateway, $2 the window (0 when Navin runs without one).
_WAIT_FOR_APP = (
    "gone() { i=0; "
    'while kill -0 "$1" 2>/dev/null && [ "$i" -lt "$2" ]; do sleep 1; i=$((i+1)); done; '
    'if kill -0 "$1" 2>/dev/null; then kill -9 "$1" 2>/dev/null; sleep 1; fi; }; '
    'gone "$1" 120; '
    'if [ "$2" != "0" ]; then kill "$2" 2>/dev/null; gone "$2" 15; fi; '
)

# $3 disk image, $4 the .app to replace, $5 where the old one waits.
# Mount at a known empty directory instead of parsing ``hdiutil attach``
# columns: a volume name with spaces, extra tabs, or a localized "Volumes"
# line used to leave $mount empty and the install died with no UI.
_MACOS_INSTALL_SCRIPT = (
    _WAIT_FOR_APP + 'target="$4"; abort() { open -a "$target" >/dev/null 2>&1; exit 1; }; '
    'mnt=$(mktemp -d "${TMPDIR:-/tmp}/navin-dmg.XXXXXX") || abort; '
    'unmount() { hdiutil detach "$mnt" -force >/dev/null 2>&1; rm -rf "$mnt"; }; '
    'hdiutil attach "$3" -mountpoint "$mnt" -readonly -nobrowse -noautoopen '
    ">/dev/null || { rm -rf \"$mnt\"; abort; }; "
    'src=""; for app in "$mnt"/*.app "$mnt"/*/*.app; do '
    'if [ -d "$app" ]; then src="$app"; break; fi; done; '
    '[ -d "$src" ] || { unmount; abort; }; '
    # ditto is what Finder uses: it preserves the signature and the resource
    # forks. Finish copying before moving the current application aside.
    'rm -rf "$4.new" || { unmount; abort; }; '
    'ditto "$src" "$4.new" || { unmount; rm -rf "$4.new"; abort; }; '
    'unmount; xattr -dr com.apple.quarantine "$4.new" >/dev/null 2>&1; '
    'rm -rf "$5" && mv "$4" "$5" || { rm -rf "$4.new"; abort; }; '
    'if mv "$4.new" "$4"; then '
    'if open -a "$4"; then rm -rf "$5"; '
    'else rm -rf "$4"; mv "$5" "$4"; open -a "$4"; exit 1; fi; '
    'else mv "$5" "$4"; open -a "$4"; exit 1; fi'
)

# A single-file install: the AppImage the user launched, or a bare frozen
# binary. The running app holds the old inode open, so the file can be swapped
# under it. $3 the downloaded file, $4 the one in place, $5 the backup.
_FILE_SWAP_INSTALL_SCRIPT = (
    _WAIT_FOR_APP
    # Staged next to the target so the rename is atomic and never crosses a
    # filesystem, then swapped, so a failure mid-copy leaves the old one intact.
    + 'cp -f "$3" "$4.new" && chmod +x "$4.new" && mv -f "$4" "$5" '
    '&& mv -f "$4.new" "$4" || { rm -f "$4.new"; '
    '[ -f "$4" ] || mv -f "$5" "$4"; exit 1; }; '
    '"$4" >/dev/null 2>&1 & newpid=$!; sleep 20; '
    # An app that dies in its first twenty seconds is a broken update, and the
    # user is left with nothing. Put the version that worked back.
    'if kill -0 "$newpid" 2>/dev/null; then rm -f "$5"; '
    'else rm -f "$4"; mv -f "$5" "$4"; "$4" >/dev/null 2>&1 & fi'
)


def _updater_environment() -> dict[str, str]:
    # The new application outlives the old frozen process and must load its
    # own libraries instead of inheriting the old bundle's runtime directory.
    return {**child_environment(), "PYINSTALLER_RESET_ENVIRONMENT": "1"}


def _install_posix_app(
    script: str,
    *,
    artifact: Path,
    target: Path,
    pid: int,
    desktop_pid: int,
) -> None:
    """Hand the swap to a detached shell that outlives this process."""
    if not os.access(target.parent, os.W_OK):
        raise UpdateError(
            f"Navin cannot write to {target.parent}; move it somewhere you own, "
            "or install the new version by hand",
            status=409,
        )
    backup = target.with_name(f"{target.name}.old")
    subprocess.Popen(
        [
            "/bin/sh",
            "-c",
            script,
            "navin-updater",
            str(pid),
            str(desktop_pid),
            _os_path(artifact),
            _os_path(target),
            _os_path(backup),
        ],
        env=_updater_environment(),
        close_fds=True,
        **detached_no_window_kwargs(),
    )


def _install_macos_app(image: Path, *, pid: int, desktop_pid: int) -> None:
    """Replace the running ``Navin.app`` with the one inside a downloaded DMG."""
    app = _app_bundle_path()
    if app is None:
        raise UpdateError("Navin is not running from an application bundle", status=409)
    if not os.access(app.parent, os.W_OK):
        raise UpdateError(
            f"Navin cannot write to {app.parent}; drag the new disk image over it instead",
            status=409,
        )
    _install_posix_app(
        _MACOS_INSTALL_SCRIPT,
        artifact=image,
        target=app,
        pid=pid,
        desktop_pid=desktop_pid,
    )


def _install_appimage(image: Path, *, pid: int, desktop_pid: int) -> None:
    """Swap the ``.AppImage`` the user launched for the downloaded one."""
    target = _desktop_app()
    if target is None:
        raise UpdateError(
            "Navin could not find the AppImage it is running from", status=409
        )
    _install_posix_app(
        _FILE_SWAP_INSTALL_SCRIPT,
        artifact=image,
        target=target,
        pid=pid,
        desktop_pid=desktop_pid,
    )


# ---------------------------------------------------------------------------
# CLI install: a navin-dist/ directory the one-line installer unpacked.
# ---------------------------------------------------------------------------


def _safe_member_path(root: Path, name: str) -> Path:
    """Where an archive member lands, refusing anything that escapes ``root``."""
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise UpdateError("The update archive contains an unsafe path")
    return candidate


def _extract_archive(archive: Path, destination: Path) -> None:
    """Unpack the CLI archive (tar.gz or zip) under ``destination``.

    Both readers check every member stays inside ``destination``: the manifest
    signature proves who built the archive, not that a tar entry named
    ``../../.bashrc`` is harmless.
    """
    root = destination.resolve()
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    _safe_member_path(root, member.filename)
                bundle.extractall(root)
            return
        with tarfile.open(archive, "r:*") as bundle:
            for member in bundle.getmembers():
                _safe_member_path(root, member.name)
                if member.issym():
                    # Symlink targets are relative to the link's own directory.
                    link_target = (root / member.name).parent / member.linkname
                    _safe_member_path(root, os.path.relpath(link_target.resolve(), root))
                elif member.islnk():
                    # Hard link targets are archive-relative names.
                    _safe_member_path(root, member.linkname)
            if hasattr(tarfile, "data_filter"):
                bundle.extractall(root, filter="data")
            else:  # pragma: no cover - Python < 3.12
                bundle.extractall(root)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        raise UpdateError("The update archive could not be unpacked") from exc


def _extracted_tree(scratch: Path) -> Path:
    """The ``navin-dist/`` directory inside an unpacked CLI archive."""
    direct = scratch / _CLI_TREE_NAME
    if (direct / _cli_binary_name()).is_file():
        return direct
    for candidate in scratch.rglob(_cli_binary_name()):
        if candidate.parent.name == _CLI_TREE_NAME:
            return candidate.parent
    raise UpdateError(f"The update archive has no {_CLI_TREE_NAME}/{_cli_binary_name()}")


def _probe_binary(binary: Path, version: str) -> None:
    """Run the freshly unpacked ``navin --version`` before it replaces anything.

    A tree that does not start is caught here, while the working install is
    still untouched, instead of after a swap that would need undoing.
    """
    try:
        result = subprocess.run(  # noqa: S603
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=_updater_environment(),
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"The downloaded Navin {version} does not start") from exc
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or version not in output:
        raise UpdateError(f"The downloaded Navin {version} does not start")


def _stage_cli_tree(archive: Path, tree: Path, version: str) -> Path:
    """Unpack next to the install and prove the new tree runs. Returns ``navin-dist.new``."""
    parent = tree.parent
    if not os.access(parent, os.W_OK):
        raise UpdateError(
            f"Navin cannot write to {parent}; re-run the installer instead",
            status=409,
        )
    staged = parent / f"{tree.name}.new"
    scratch = parent / f".{tree.name}-update-{version}"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        scratch.mkdir(parents=True)
        _extract_archive(archive, scratch)
        extracted = _extracted_tree(scratch)
        binary = extracted / _cli_binary_name()
        if sys.platform != "win32":
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        _probe_binary(binary, version)
        os.replace(extracted, staged)
    except OSError as exc:
        raise UpdateError("Could not prepare the update next to the installation") from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return staged


def _swap_cli_tree(tree: Path, staged: Path) -> None:
    """Put ``navin-dist.new`` in place of ``navin-dist`` (POSIX, in-process).

    A running process keeps its mapped files alive through the rename and the
    delete, so this is safe to do from the very tree being replaced. The
    caller must be about to exit or restart: anything the frozen runtime
    still has to load lazily would now come from the new tree.
    """
    backup = tree.with_name(f"{tree.name}.old")
    shutil.rmtree(backup, ignore_errors=True)
    try:
        os.rename(tree, backup)
    except OSError as exc:
        shutil.rmtree(staged, ignore_errors=True)
        raise UpdateError("Could not move the current installation aside") from exc
    try:
        os.rename(staged, tree)
    except OSError as exc:
        os.rename(backup, tree)
        shutil.rmtree(staged, ignore_errors=True)
        raise UpdateError("Could not put the new version in place; the current one was kept") from exc
    shutil.rmtree(backup, ignore_errors=True)


# $1 the process to wait for, $2 unused (0), then the command to run in its place.
_RELAUNCH_SCRIPT = _WAIT_FOR_APP + 'shift 2; exec "$@"'


def _relaunch_after_exit(argv: list[str], pid: int) -> None:
    """Start ``argv`` once process ``pid`` is gone (POSIX)."""
    subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", _RELAUNCH_SCRIPT, "navin-updater", str(pid), "0", *argv],
        env=_updater_environment(),
        close_fds=True,
        **detached_no_window_kwargs(),
    )


def _windows_helper() -> Path:
    executable = Path(sys.executable).resolve()
    helper = executable.with_name("NavinUpdater.exe")
    if not helper.is_file():
        helper = Path(getattr(sys, "_MEIPASS", executable.parent)) / "NavinUpdater.exe"
    if not helper.is_file():
        raise UpdateError("Navin update helper is missing", status=503)
    return helper


def _stage_windows_helper(version: str) -> Path:
    """The running helper must not lock files that the installer replaces."""
    helper = _windows_helper()
    outside = Path.home() / ".navin" / "updates" / version / "NavinUpdater.exe"
    outside.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(helper, outside)
    return outside


def _other_processes_in_tree(tree: Path, pid: int) -> list[int]:
    """PIDs of other ``navin.exe`` running from ``tree`` (Windows).

    Windows refuses to rename a directory while any file in it is open, and a
    running executable is exactly that. Finding them before the hand-off gives
    the user a message with PIDs instead of a helper that fails after the fact.
    """
    if sys.platform != "win32":
        return []
    script = (
        "Get-Process -Name navin -ErrorAction SilentlyContinue | "
        "ForEach-Object { try { '{0}|{1}' -f $_.Id, $_.Path } catch { } }"
    )
    try:
        result = subprocess.run(  # noqa: S603
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    root = os.path.normcase(_os_path(tree)).rstrip("\\") + "\\"
    others: list[int] = []
    for line in result.stdout.splitlines():
        raw_pid, _, raw_path = line.strip().partition("|")
        if not raw_pid.isdigit() or int(raw_pid) == pid or not raw_path:
            continue
        if os.path.normcase(_strip_windows_extended_prefix(raw_path)).startswith(root):
            others.append(int(raw_pid))
    return others


def _launch_windows_tree_updater(
    staged: Path,
    tree: Path,
    *,
    version: str,
    pid: int,
    restart: list[str] | None,
) -> None:
    """Hand the directory swap to NavinUpdater.exe, run from outside the tree.

    The helper cannot live inside what it renames: its own image would hold
    the directory open. A copy under ~/.navin/updates does the work once this
    process has exited.
    """
    outside = _stage_windows_helper(version)
    command = [
        _os_path(outside),
        "--mode",
        "tree",
        "--wait-pid",
        str(pid),
        "--artifact",
        _os_path(staged),
        "--target",
        _os_path(tree),
    ]
    if restart:
        command += ["--restart", _os_path(restart[0])]
        if len(restart) > 1:
            command += ["--restart-args", subprocess.list2cmdline(restart[1:])]
    subprocess.Popen(  # noqa: S603
        command, cwd=_os_path(outside.parent), env=_updater_environment(),
        close_fds=True, **_updater_spawn_kwargs(),
    )


def _install_cli(archive: Path, *, version: str, pid: int, relaunch: bool) -> dict[str, Any]:
    """Replace the CLI's ``navin-dist/`` with the one in the downloaded archive.

    POSIX swaps the directory right away and reports ``deferred: False``;
    Windows hands the swap to the helper, which waits for this process to exit,
    and reports ``deferred: True``. With ``relaunch`` the same command line comes
    back up on the new version once this process is gone.
    """
    tree = _cli_tree()
    if tree is None:
        raise UpdateError("Navin is not running from a navin-dist folder it can replace", status=409)
    staged = _stage_cli_tree(archive, tree, version)
    restart = [str(tree / _cli_binary_name()), *sys.argv[1:]] if relaunch else None
    if sys.platform == "win32":
        others = _other_processes_in_tree(tree, pid)
        if others:
            shutil.rmtree(staged, ignore_errors=True)
            listed = ", ".join(str(other) for other in others)
            raise UpdateError(
                f"Close the other Navin processes first (PID {listed}), then run the update again",
                status=409,
            )
        _launch_windows_tree_updater(staged, tree, version=version, pid=pid, restart=restart)
        return {"state": "pending", "deferred": True, "tree": str(tree)}
    _swap_cli_tree(tree, staged)
    if restart:
        _relaunch_after_exit(restart, pid)
    return {"state": "installed", "deferred": False, "tree": str(tree)}


def apply_cli_update() -> dict[str, Any]:
    """Download (if needed) and install the available update for ``navin update``.

    Unlike :func:`install_update`, nothing here stops the process or announces
    a restart: the command that called this prints the outcome and exits.
    """
    return _run_update_operation(_apply_cli_update)


def _apply_cli_update() -> dict[str, Any]:
    with _STATE_LOCK:
        path = str(_STATE.get("path", ""))
        info = dict(_STATE.get("update") or {})
    if not path or not Path(path).is_file():
        downloaded = _download_update()
        path = downloaded["path"]
        info = downloaded
    kind = str(info.get("installKind") or _install_kind())
    if kind != "cli":
        raise UpdateError("This installation is not a CLI install; use the desktop app to update", status=409)
    version = str(info.get("latestVersion") or "").strip()
    _set_state(state="installing", progress=100)
    try:
        result = _install_cli(Path(path), version=version, pid=os.getpid(), relaunch=False)
    except UpdateError as exc:
        _set_state(state="error", error=str(exc))
        raise
    if result["deferred"]:
        _record_pending_install(version)
    _set_state(state="installed", update={**info, "state": "installed"})
    with_version = {**result, "latestVersion": version, "currentVersion": __version__}
    Path(path).unlink(missing_ok=True)
    return with_version


def _pending_path() -> Path:
    return Path.home() / ".navin" / "update-pending"


# Long enough to cover a machine that is put to sleep mid-update, short enough
# that a failure nobody noticed is not reported as news days later.
_PENDING_TTL_S = 6 * 60 * 60


def _record_pending_install(version: str) -> None:
    """Remember what is being installed so the next start can confirm it.

    Restarting is the moment the promise made to the user comes due. Without a
    note left on disk, the new version comes up looking exactly like the old
    one, and a silent success is indistinguishable from a silent failure.
    """
    if not version:
        return
    path = _pending_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": version, "from": __version__, "at": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        # A confirmation is a courtesy; failing to note it must not stop the
        # update the user asked for.
        pass


def consume_completed_update() -> dict[str, Any] | None:
    """Report once on how the last install turned out, then forget it."""
    path = _pending_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path.unlink(missing_ok=True)
    if not isinstance(raw, dict):
        return None
    target = str(raw.get("version") or "").strip()
    try:
        stale = time.time() - float(raw.get("at") or 0) > _PENDING_TTL_S
    except (TypeError, ValueError):
        stale = True
    if not target or stale:
        return None
    if __version__ == target:
        return {"version": target, "ok": True}
    # The version that came back up is not the one that was installed: either
    # the installer never ran, or the rollback put the working copy back.
    return {"version": target, "ok": False, "currentVersion": __version__}


def _schedule_shutdown() -> None:
    def stop() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    timer = threading.Timer(1.5, stop)
    timer.daemon = True
    timer.start()


def _updater_spawn_kwargs() -> dict[str, Any]:
    """Spawn flags for the Windows update helper.

    Detached and windowless like any other background child, plus permission
    to leave the job object: the gateway lives in a job owned by the desktop
    window, and when that window exits the job dies - taking the helper with
    it mid-install unless it broke away first.
    """
    kwargs = detached_no_window_kwargs()
    if sys.platform == "win32":
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | breakaway
    return kwargs


def install_update() -> dict[str, Any]:
    """Launch the platform updater, then stop Navin after the HTTP response."""
    return _run_update_operation(_install_update)


def _install_update() -> dict[str, Any]:
    with _STATE_LOCK:
        path = str(_STATE.get("path", ""))
        info = dict(_STATE.get("update") or {})
        if _STATE.get("state") == "restarting":
            return {"state": "restarting", "latestVersion": info.get("latestVersion")}
    if not path or not Path(path).is_file():
        downloaded = _download_update()
        path = downloaded["path"]
        info = downloaded
    kind = str(info.get("installKind") or _install_kind())
    if kind in _MANAGED_KINDS:
        raise UpdateError(
            "Navin was installed from a system package. Update it with your package manager.",
            status=409,
        )
    executable = Path(sys.executable).resolve()
    pid = os.getpid()
    desktop_pid = _desktop_pid()
    _set_state(state="installing", progress=100)

    if kind.startswith("windows"):
        helper = _stage_windows_helper(str(info.get("latestVersion") or ""))
        mode = "installer" if kind == "windows-setup" else "portable"
        # What comes back up is the window the user closed, not the sidecar
        # behind it: relaunching the gateway alone would leave them with a
        # process and no Navin.
        relaunch = _desktop_app() or executable
        waits = str(pid) if not desktop_pid else f"{pid},{desktop_pid}"
        command = [
            _os_path(helper),
            "--mode",
            mode,
            "--wait-pid",
            waits,
            "--artifact",
            _os_path(path),
            "--target",
            _os_path(_desktop_app() or executable),
            "--restart",
            _os_path(relaunch),
        ]
        subprocess.Popen(
            command, cwd=_os_path(helper.parent), env=_updater_environment(),
            close_fds=True, **_updater_spawn_kwargs(),
        )
    elif kind == "macos-app":
        _install_macos_app(Path(path), pid=pid, desktop_pid=desktop_pid)
    elif kind == "linux-appimage":
        _install_appimage(Path(path), pid=pid, desktop_pid=desktop_pid)
    elif kind in {"linux", "macos"}:
        _install_posix_app(
            _FILE_SWAP_INSTALL_SCRIPT,
            artifact=Path(path),
            target=executable,
            pid=pid,
            desktop_pid=desktop_pid,
        )
    elif kind == "cli":
        # The gateway of a CLI install (navin webui / navin gateway) asked from
        # its browser tab: swap the tree and bring the same command line back.
        try:
            _install_cli(
                Path(path),
                version=str(info.get("latestVersion") or "").strip(),
                pid=pid,
                relaunch=True,
            )
        except UpdateError as exc:
            _set_state(state="error", error=str(exc))
            raise
    else:
        raise UpdateError("This installation cannot be updated automatically", status=409)

    version = str(info.get("latestVersion") or "").strip()
    _record_pending_install(version)
    _set_state(state="restarting", error=None)
    # The tab that asked for this gets a reply; every other open window just
    # loses its connection in a second and a half with no idea why.
    notify(
        title=f"Navin is restarting to install {version}" if version else "Navin is restarting to update",
        detail="The connection will drop briefly.",
        level="warning",
        key="update:install",
    )
    _schedule_shutdown()
    return {"state": "restarting", "latestVersion": info.get("latestVersion")}


def update_status() -> dict[str, Any]:
    """Return a snapshot of download/install progress."""
    with _STATE_LOCK:
        state = dict(_STATE)
    state["currentVersion"] = __version__
    state["installKind"] = _install_kind()
    return state
