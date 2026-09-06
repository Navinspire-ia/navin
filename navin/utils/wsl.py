"""Reaching a WSL distribution from a Windows host.

Windows publishes the filesystem of every installed distribution through a
network redirector, at ``\\\\wsl.localhost\\<distro>`` and at the older
``\\\\wsl$\\<distro>``. Opening a project that lives inside a distribution is
therefore only a matter of pointing at the right path - Python reads and writes
it like any other directory.

Running that project is a different matter. A command started on the Windows
side sees Windows paths and Windows binaries: the project's ``node_modules``
holds Linux builds, its virtualenv points at ``/usr/bin/python3``, and its
``make`` does not exist. Commands belonging to such a project have to be handed
to ``wsl.exe`` instead, with the path translated back to the form the
distribution knows itself by. Both halves live here.

Nothing in this module needs a Windows host to be *tested*: parsing and
translation are string work, and the parts that shell out are separated from the
parts that do not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from navin.utils.proc import no_window_kwargs

# Both spellings of the redirector. "wsl$" is the original and still resolves;
# "wsl.localhost" is what current Windows shows in Explorer.
_UNC_PREFIXES = ("\\\\wsl.localhost\\", "\\\\wsl$\\")
# Explorer and some shells hand paths over with forward slashes.
_UNC_PREFIXES_SLASH = tuple(p.replace("\\", "/") for p in _UNC_PREFIXES)

_DISTROS_TTL_S = 300.0
_distros_cache: list[str] | None = None
_distros_cached_at = 0.0

_HOME_TTL_S = 300.0
# distro -> (monotonic timestamp, home or None)
_home_cache: dict[str, tuple[float, str | None]] = {}


@dataclass(frozen=True)
class WslLocation:
    """A path inside a distribution, and the distribution it belongs to."""

    distro: str
    path: PurePosixPath

    @property
    def posix(self) -> str:
        return str(self.path)

    def unc(self) -> str:
        """The Windows path that reaches this location."""
        return to_unc(self.distro, self.path)


def parse_unc(text: str) -> WslLocation | None:
    """Split a ``\\\\wsl.localhost\\Ubuntu\\home\\me`` path into distro and path.

    Returns ``None`` for anything that is not a WSL redirector path, including
    ordinary UNC shares - a file server called ``wsl`` is somebody's network
    drive, not a distribution.
    """
    if not text:
        return None
    raw = text.strip().strip('"')
    normalised = raw.replace("/", "\\")
    for prefix in _UNC_PREFIXES:
        if normalised.lower().startswith(prefix.lower()):
            remainder = normalised[len(prefix) :]
            break
    else:
        return None
    parts = [segment for segment in remainder.split("\\") if segment]
    if not parts:
        return None
    distro, *rest = parts
    return WslLocation(distro=distro, path=PurePosixPath("/", *rest))


def to_unc(distro: str, path: str | PurePosixPath) -> str:
    """The Windows path for ``path`` inside ``distro``."""
    posix = PurePosixPath(path)
    tail = "\\".join(part for part in posix.parts if part != "/")
    base = f"\\\\wsl.localhost\\{distro}"
    return f"{base}\\{tail}" if tail else base


def drive_to_mount(text: str) -> str | None:
    """Translate ``C:\\Users\\me`` to ``/mnt/c/Users/me`` for ``wsl.exe --cd``.

    Returns None for anything that is not a drive-letter path - a UNC path is
    already inside a distribution and handled elsewhere, and a bare relative
    path has no drive to mount.
    """
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text.strip().strip('"'))
    if not match:
        return None
    drive, rest = match.group(1).lower(), match.group(2).replace("\\", "/").rstrip("/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def is_unc(text: str) -> bool:
    """True for a path that lives inside a distribution."""
    return parse_unc(text) is not None


def looks_like_unc(text: str) -> bool:
    """True for any UNC path, WSL or not.

    Used where the question is "would this survive path validation", which is
    about the shape of the path rather than what is behind it.
    """
    if not text:
        return False
    candidate = text.strip()
    return candidate.startswith("\\\\") or candidate.startswith("//")


def is_wsl_guest() -> bool:
    """True when this process is itself running inside WSL.

    The mirror image of the rest of this module: here the Linux side is home and
    Windows is the other side of the boundary.
    """
    if sys.platform == "win32":
        return False
    for source in ("/proc/sys/kernel/osrelease", "/proc/version"):
        try:
            with open(source, encoding="utf-8", errors="ignore") as handle:
                text = handle.read().lower()
        except OSError:
            continue
        if "microsoft" in text or "wsl" in text:
            return True
    return False


def wsl_executable() -> str | None:
    return shutil.which("wsl") or shutil.which("wsl.exe")


def distributions(*, refresh: bool = False) -> list[str]:
    """Installed distributions, newest answer cached.

    ``wsl.exe -l -q`` costs the full timeout when the service is cold, and the
    callers are on the event loop, so this is read rarely and remembered.
    """
    global _distros_cache, _distros_cached_at
    now = time.monotonic()
    if not refresh and _distros_cache is not None and now - _distros_cached_at < _DISTROS_TTL_S:
        return list(_distros_cache)
    names = _read_distributions()
    _distros_cache = names
    _distros_cached_at = now
    return list(names)


def _read_distributions() -> list[str]:
    wsl = wsl_executable()
    if not wsl:
        return []
    try:
        out = subprocess.run(  # noqa: S603
            [wsl, "-l", "-q"],
            capture_output=True,
            timeout=10,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_distribution_list(out.stdout)


def parse_distribution_list(raw: bytes) -> list[str]:
    """Read the output of ``wsl -l -q``.

    It is UTF-16LE, and every name carries a stray NUL when the console is not
    a terminal, which is exactly the case here.
    """
    text = ""
    for encoding in ("utf-16-le", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    names = [line.strip().strip("\x00").strip() for line in text.splitlines()]
    return [name for name in names if name]


def resolve_distro(name: str | None) -> str | None:
    """Match a distribution name case-insensitively against the installed ones.

    Returns the installed spelling, so a path typed as ``\\\\wsl$\\ubuntu`` reaches
    the distribution registered as ``Ubuntu``.
    """
    if not name:
        return None
    for installed in distributions():
        if installed.lower() == name.lower():
            return installed
    return None


def distro_home(distro: str, *, refresh: bool = False) -> str | None:
    """``$HOME`` inside *distro*, spelled the way the distribution spells it.

    Windows has no idea where a distribution puts a user's home, and the answer
    is not derivable from the project path: a project under ``/srv`` or
    ``/mnt/d`` says nothing about the account running it. Anything that needs to
    store per-user state on the Linux side has to ask.

    Cached like :func:`distributions`: the call costs a ``wsl.exe`` round trip,
    and a home directory does not move while the app is running. Failures are
    cached too, so a stopped distribution does not cost a timeout per git
    command.
    """
    now = time.monotonic()
    if not refresh:
        cached = _home_cache.get(distro)
        if cached is not None and now - cached[0] < _HOME_TTL_S:
            return cached[1]
    home = _read_distro_home(distro)
    _home_cache[distro] = (now, home)
    return home


def _read_distro_home(distro: str) -> str | None:
    wsl = wsl_executable()
    if not wsl:
        return None
    try:
        out = subprocess.run(  # noqa: S603
            [wsl, "-d", distro, "--", "sh", "-c", 'printf %s "$HOME"'],
            capture_output=True,
            timeout=20,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    home = out.stdout.decode("utf-8", errors="replace").strip().strip("\x00").strip()
    # A relative or empty answer means the shell never expanded it.
    return home if home.startswith("/") else None


def command_prefix(distro: str, cwd: str | PurePosixPath | None = None) -> list[str]:
    """The argv prefix that runs a command inside ``distro``.

    ``--cd`` is what puts the command in the project rather than in the user's
    home; without it a build runs in the wrong directory, or in ``/mnt/c`` when
    the caller passed a Windows path through.
    """
    wsl = wsl_executable() or "wsl.exe"
    argv = [wsl, "-d", distro]
    if cwd is not None:
        argv += ["--cd", str(PurePosixPath(cwd))]
    argv.append("--")
    return argv


# --- Opening things on the Windows side, from inside a distribution ----------
#
# A URL opened with the Linux machinery (xdg-open, gio) goes nowhere on a stock
# WSL: there is no Linux browser and no portal, so gio answers "Operation not
# supported" while the user waits in front of a Windows screen. The browser that
# can actually show the page lives on the other side of the boundary, and
# Windows executables are directly runnable from here through interop.

# Chromium-style browsers, best first, at their usual install spots. Edge ships
# with Windows itself (under Program Files (x86) even on x64), so on almost
# every machine at least one of these resolves.
_HOST_BROWSER_TAILS = (
    "Google/Chrome/Application/chrome.exe",
    "Microsoft/Edge/Application/msedge.exe",
    "BraveSoftware/Brave-Browser/Application/brave.exe",
)


def windows_env(name: str) -> str | None:
    """Read one environment variable from the Windows host.

    WSL does not mirror the Windows environment, so anything below a per-user
    path (``%LOCALAPPDATA%``, per-user browser installs) has to be asked for.
    Returns None when the variable is unset or the host cannot be reached.
    """
    cmd = shutil.which("cmd.exe") or "/mnt/c/Windows/System32/cmd.exe"
    if not os.path.isfile(cmd):
        return None
    try:
        out = subprocess.run(  # noqa: S603
            [cmd, "/d", "/c", f"echo %{name}%"],
            capture_output=True,
            timeout=10,
            check=False,
            # cmd.exe warns and falls back to C:\Windows when started from a
            # UNC (i.e. WSL) working directory; starting it from /mnt/c avoids
            # the noise.
            cwd="/mnt/c" if os.path.isdir("/mnt/c") else None,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.decode("utf-8", errors="replace").strip()
    # cmd echoes the pattern back verbatim when the variable does not exist.
    if not value or value == f"%{name}%":
        return None
    return value


def host_browser_candidates(local_app_data: str | None = None) -> list[str]:
    """Where a Windows Chromium browser would be, as paths this side can test.

    ``local_app_data`` is the Windows form of ``%LOCALAPPDATA%`` when known,
    covering per-user installs (Chrome's default since 2021 for non-admin
    users). Ordered best browser first, machine-wide install before per-user.
    """
    roots = ["/mnt/c/Program Files", "/mnt/c/Program Files (x86)"]
    if local_app_data:
        converted = windows_path_to_wsl(local_app_data)
        if converted:
            roots.append(converted)
    return [f"{root}/{tail}" for tail in _HOST_BROWSER_TAILS for root in roots]


def find_host_browser(local_app_data: str | None = None) -> str | None:
    """A Windows Chromium browser reachable from this distribution, or None."""
    for candidate in host_browser_candidates(local_app_data):
        if os.path.isfile(candidate):
            return candidate
    return None


def host_open_commands(url: str) -> list[list[str]]:
    """Ways to open ``url`` in the Windows default browser, best first.

    ``wslview`` (from wslu) exists for exactly this and is tried first.
    PowerShell's ``Start-Process`` is the reliable built-in fallback; the URL
    goes in single quotes because it carries ``&`` between query parameters,
    which PowerShell would otherwise read as a statement separator. Plain
    ``explorer.exe`` closes the list: it hands the URL to ShellExecute without
    a shell parse, but reports failure through its exit code even on success,
    which is why it is last and judged only on having started.
    """
    return [
        ["wslview", url],
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Start-Process '{url}'",
        ],
        ["explorer.exe", url],
    ]


def open_url_on_host(url: str) -> bool:
    """Open ``url`` in the Windows default browser. True when something took it."""
    for argv in host_open_commands(url):
        exe = shutil.which(argv[0])
        if not exe:
            continue
        try:
            proc = subprocess.run(  # noqa: S603
                [exe, *argv[1:]],
                capture_output=True,
                timeout=15,
                check=False,
                cwd="/mnt/c" if os.path.isdir("/mnt/c") else None,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        # explorer.exe answers 1 for a URL it opened perfectly well.
        if proc.returncode == 0 or argv[0] == "explorer.exe":
            return True
    return False


_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$", re.DOTALL)


def windows_path_to_wsl(text: str) -> str | None:
    """Translate ``C:\\Users\\me`` into ``/mnt/c/Users/me``.

    For handing a Windows path to a command running inside a distribution. WSL
    paths are returned as themselves; anything else returns ``None`` rather than
    a guess.
    """
    location = parse_unc(text)
    if location is not None:
        return location.posix
    match = _DRIVE_RE.match(text.strip())
    if not match:
        return None
    drive, tail = match.groups()
    body = tail.replace("\\", "/").strip("/")
    return f"/mnt/{drive.lower()}/{body}" if body else f"/mnt/{drive.lower()}"
