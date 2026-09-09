# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Directory browsing API for the Dev project picker.

Lets the WebUI browse host directories (names only, no file contents) so the
user can pick a project root like VS Code's "Open Folder" dialog. Exposes
quick roots that match the host the gateway runs on: home and filesystem root
everywhere; the Windows user profile and Windows drives inside WSL; every drive
and the installed WSL distributions on Windows; the mounted volumes on macOS;
the mounted drives on Linux. A macOS or Linux host never sees WSL or Windows
entries, and a Windows host never sees a bare "/".
"""

from __future__ import annotations

import getpass
import os
import platform
import re
import string
import time
from pathlib import Path
from typing import Any

from navin.config.paths import is_navin_internal_path
from navin.utils import wsl

MAX_DIR_ENTRIES = 400


class FsBrowseError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def host_environment() -> str:
    """Coarse environment label: wsl | linux | macos | windows."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    if wsl.is_wsl_guest():
        return "wsl"
    return "linux"


def _windows_drive_mounts() -> list[Path]:
    """Windows drives mounted inside WSL (/mnt/c, /mnt/d, …)."""
    mnt = Path("/mnt")
    if not mnt.is_dir():
        return []
    drives: list[Path] = []
    for entry in sorted(mnt.iterdir()):
        if entry.is_dir() and re.fullmatch(r"[a-z]", entry.name):
            drives.append(entry)
    return drives


_WINDOWS_HOME_TTL_S = 300.0
# (monotonic timestamp, /mnt/c/Users/<name> or None)
_windows_home_cache: tuple[float, str | None] | None = None


def _windows_home_mount() -> Path | None:
    """The Windows user profile as seen from inside WSL (``/mnt/c/Users/<name>``).

    Home here is the Linux home, but a WSL user's projects often sit on the
    Windows side (Documents, Desktop, a ``C:\\projects`` folder), and reaching
    them meant walking ``/mnt/c/Users`` by hand. Only the Windows host knows
    which profile is the user's; ``cmd.exe`` answers in a few hundred
    milliseconds, so the answer is remembered rather than asked on every open.
    """
    global _windows_home_cache
    now = time.monotonic()
    if _windows_home_cache is not None and now - _windows_home_cache[0] < _WINDOWS_HOME_TTL_S:
        cached = _windows_home_cache[1]
        return Path(cached) if cached else None
    profile = wsl.windows_env("USERPROFILE")
    converted = wsl.windows_path_to_wsl(profile) if profile else None
    _windows_home_cache = (now, converted)
    return Path(converted) if converted else None


def _has_entries(path: Path) -> bool:
    try:
        return any(path.iterdir())
    except OSError:
        return False


# Where Linux desktops mount removable media (per user) and extra disks.
_LINUX_MEDIA_BASES = (Path("/media"), Path("/run/media"))
_LINUX_MNT = Path("/mnt")
_MACOS_VOLUMES = Path("/Volumes")


def _linux_mounted_drives() -> list[Path]:
    """Extra and removable drives on a Linux host.

    Desktop distributions mount removable media under ``/media/<user>`` or
    ``/run/media/<user>``; permanent extra disks are usually under ``/mnt``.
    Only directories with something in them count: an empty directory under
    ``/mnt`` is a mount point whose drive is not attached, and offering it
    would open an empty folder.
    """
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER", "")
    bases = [_LINUX_MNT]
    if user:
        bases = [*(base / user for base in _LINUX_MEDIA_BASES), *bases]
    drives: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in seen:
                continue
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if not _has_entries(entry):
                continue
            seen.add(entry.name)
            drives.append(entry)
    return drives[:12]


def _macos_volumes() -> list[Path]:
    """Mounted volumes on macOS, minus the boot disk.

    ``/Volumes`` lists the boot volume too, as a symlink to ``/``; that one is
    already offered as the filesystem root and would only show up twice.
    """
    try:
        entries = sorted(_MACOS_VOLUMES.iterdir())
    except OSError:
        return []
    out: list[Path] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_dir() or entry.resolve() == Path("/"):
                continue
        except OSError:
            continue
        out.append(entry)
    return out[:12]


def _windows_drive_letters() -> list[str]:
    """Drive letters that exist on native Windows, without touching the media.

    GetLogicalDrives answers a bitmask (bit 0 = A:), which is what Explorer
    itself uses. Probing ``X:\\`` with is_dir() instead would spin up empty
    optical or card-reader drives and can block for seconds each.
    """
    try:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
    except Exception:
        return []
    return [
        letter
        for index, letter in enumerate(string.ascii_uppercase)
        if mask >> index & 1
    ]


def _wsl_distro_roots() -> list[dict[str, str]]:
    """One root per installed distribution, reached through the redirector.

    Windows publishes each distribution's filesystem at
    ``\\\\wsl.localhost\\<distro>``, so a project living inside one can be opened
    without leaving the Windows host. The home directory of the distribution is
    the useful landing point rather than its root: that is where projects are,
    and ``/`` there is full of directories nobody browses to.

    A distribution that is not running has no redirector entry yet, so the home
    probe fails and the root is offered anyway - opening it is what starts it.
    """
    roots: list[dict[str, str]] = []
    for distro in wsl.distributions():
        base = wsl.to_unc(distro, "/")
        home = wsl.to_unc(distro, "/home")
        target = base
        try:
            if Path(home).is_dir():
                target = home
        except OSError:
            pass
        roots.append({"kind": "wsl", "label": f"WSL: {distro}", "path": target, "name": distro})
    return roots


def fs_roots_payload(*, default_project_path: str | None = None) -> dict[str, Any]:
    """Quick-access roots for the folder picker, tagged by environment.

    ``kind`` says what a root is (home, windows drive, volume...), ``name`` the
    part that varies (drive letter, volume or distribution name) so the UI can
    word the label in the user's language; ``label`` is the English wording.
    """
    env = host_environment()
    roots: list[dict[str, str]] = []

    def add(kind: str, label: str, path: Path, *, name: str | None = None) -> None:
        # A root that cannot be probed is left out rather than raised: an
        # unreachable network drive or a stopped distribution must not take the
        # whole picker down with it.
        try:
            present = path.is_dir()
        except OSError:
            return
        if not present:
            return
        entry = {"kind": kind, "label": label, "path": str(path)}
        if name:
            entry["name"] = name
        roots.append(entry)

    # The internal ~/.navin storage must never be offered as a project root:
    # it belongs to the system, not to the user's projects.
    if default_project_path and not is_navin_internal_path(default_project_path):
        add("workspace", "Projects", Path(default_project_path))
    add("home", "Home", Path.home())
    if env == "wsl":
        windows_home = _windows_home_mount()
        if windows_home is not None:
            add("windows-home", "Windows home", windows_home, name=windows_home.name)
        for drive in _windows_drive_mounts():
            letter = drive.name.upper()
            add("windows", f"Windows ({letter}:)", drive, name=letter)
    if env == "windows":
        # Every drive, not just the one Home lives on: a drive root has no
        # parent, so a user who reaches C:\ can go no further and would never
        # see D: without these entries.
        for letter in _windows_drive_letters():
            add("disk", f"Disk ({letter}:)", Path(f"{letter}:\\"), name=letter)
        roots.extend(_wsl_distro_roots())
    if env == "macos":
        for volume in _macos_volumes():
            add("volume", volume.name, volume, name=volume.name)
    if env == "linux":
        for drive in _linux_mounted_drives():
            add("drive", drive.name, drive, name=drive.name)
    # Windows has no single filesystem root. Path("/") there resolves against
    # whichever drive the process happens to be on and prints as a lone
    # backslash, which is an entry the picker cannot explain and the user cannot
    # use. Drives are listed individually above instead.
    if env != "windows":
        add("system", "/", Path("/"))
    payload: dict[str, Any] = {"environment": env, "roots": roots}
    if env == "wsl":
        # The distribution's name, so the UI can say "WSL: Ubuntu" like
        # Cursor does instead of a bare "WSL".
        distro = os.environ.get("WSL_DISTRO_NAME", "").strip()
        if distro:
            payload["distro"] = distro
    return payload


# Windows refuses these device names as files or folders, in any case mix and
# even with an extension. A folder created from Linux or WSL with one of them
# would be unreachable from the Windows side of the same machine.
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Forbidden on Windows (NTFS and the redirector); / and NUL are forbidden
# everywhere. Enforcing the union keeps a folder usable from every host the
# picker can browse: Linux, WSL, Windows drives, macOS volumes.
_FOLDER_NAME_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def validate_new_folder_name(raw_name: str) -> str:
    """A single folder name (no path) safe on Linux, WSL, Windows and macOS."""
    name = (raw_name or "").strip()
    if not name:
        raise FsBrowseError("missing folder name")
    if len(name) > 128:
        raise FsBrowseError("folder name is too long")
    if name in {".", ".."}:
        raise FsBrowseError("invalid folder name")
    if _FOLDER_NAME_BAD_CHARS.search(name):
        raise FsBrowseError('folder name cannot contain <>:"/\\|?*')
    # Trailing dots and spaces are silently stripped by Windows, creating a
    # folder whose real name differs from what the user typed.
    if name.endswith((".", " ")):
        raise FsBrowseError("folder name cannot end with a dot or a space")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise FsBrowseError("this name is reserved on Windows")
    return name


def fs_mkdir_payload(
    raw_name: str,
    raw_parent: str = "",
    *,
    default_parent: str | None = None,
) -> dict[str, Any]:
    """Create a project folder under *parent* (default: the Projects root).

    Idempotent on purpose: asking for a folder that already exists opens it
    instead of failing, because from the sidebar both clicks mean the same
    thing - "I want to work in this folder".
    """
    name = validate_new_folder_name(raw_name)

    parent_raw = (raw_parent or "").strip() or (default_parent or "").strip()
    if not parent_raw:
        raise FsBrowseError("missing parent folder")
    parent = Path(parent_raw).expanduser()
    if not parent.is_absolute():
        raise FsBrowseError("parent must be an absolute path")
    try:
        parent = parent.resolve()
    except OSError as exc:
        raise FsBrowseError(f"cannot resolve parent: {exc}") from exc
    if is_navin_internal_path(str(parent)):
        raise FsBrowseError("cannot create folders inside Navin's internal storage")

    target = parent / name
    created = not target.exists()
    if not created and not target.is_dir():
        raise FsBrowseError("a file with that name already exists", status=409)
    try:
        # parents=True: on first run the Projects root itself may not exist yet.
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise FsBrowseError(f"permission denied in {parent}", status=403) from exc
    except OSError as exc:
        raise FsBrowseError(f"cannot create folder: {exc}") from exc

    return {"path": str(target), "name": name, "created": created}


def fs_list_payload(raw_path: str, *, show_hidden: bool = False) -> dict[str, Any]:
    """List sub-directories of an absolute path (names only)."""
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise FsBrowseError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise FsBrowseError("path must be absolute")
    try:
        path = path.resolve()
    except OSError as exc:
        raise FsBrowseError(f"cannot resolve path: {exc}") from exc
    if not path.is_dir():
        raise FsBrowseError("not a directory", status=404)

    dirs: list[dict[str, str]] = []
    truncated = False
    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise FsBrowseError("permission denied", status=403) from exc
    except OSError as exc:
        raise FsBrowseError(f"cannot list directory: {exc}") from exc
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if len(dirs) >= MAX_DIR_ENTRIES:
            truncated = True
            break
        dirs.append({"name": entry.name, "path": str(entry)})

    parent = str(path.parent) if path.parent != path else None
    return {
        "path": str(path),
        "parent": parent,
        "directories": dirs,
        "truncated": truncated,
    }
