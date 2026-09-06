"""Keep one OpenSSL in a frozen build: the newest one anybody linked.

Two things in a Navin build talk TLS through OpenSSL: python's own ``_ssl``
module and cryptography's rust binding ``_rust.abi3.so``. On a machine where
they were linked against two different OpenSSL installs - a python.org
interpreter shipping its own 3.0 next to a Homebrew 3.x, say - PyInstaller
collects both libraries under the same basename ``libssl.3.dylib`` and only one
survives. When the older one wins, the app boots but cryptography fails to load
with ``Symbol not found: _SSL_get0_group_name``, which on a user's Mac read as
"websocket channel not available" and a WebUI that never came up.

OpenSSL keeps ABI compatibility within a major series, so the newest copy
satisfies every consumer; the oldest satisfies only its own. This module walks
the Analysis TOC, finds every OpenSSL library each consumer really links, and
rewrites the TOC so the newest copy is the one that ships.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# libssl.3.dylib, libcrypto.so.3, libssl.so.1.1 ... one group per basename, so
# differently-named generations never fight each other.
_OPENSSL_BASENAME = re.compile(r"^lib(ssl|crypto)[-.0-9]*(\.dylib|\.so(\.[0-9]+)*)$")

# Every OpenSSL library embeds its own version banner.
_VERSION_BANNER = re.compile(rb"OpenSSL (\d+)\.(\d+)\.(\d+)")

# The modules whose linkage decides which OpenSSL must ship.
_CONSUMER_PREFIXES = ("_ssl", "_rust")


def openssl_version(path: Path | str) -> tuple[int, int, int]:
    """The version an OpenSSL library says it is, or (0, 0, 0) when unreadable."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return (0, 0, 0)
    best = (0, 0, 0)
    for banner in _VERSION_BANNER.finditer(data):
        found = tuple(int(part) for part in banner.groups())
        if found > best:
            best = found
    return best


def _otool_lines(flag: str, binary: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["otool", flag, str(binary)], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return result.stdout.splitlines()


def _macos_rpaths(binary: Path) -> list[Path]:
    rpaths: list[Path] = []
    lines = _otool_lines("-l", binary)
    for index, line in enumerate(lines):
        if "cmd LC_RPATH" not in line:
            continue
        for follow in lines[index : index + 4]:
            follow = follow.strip()
            if follow.startswith("path "):
                entry = follow[len("path ") :].split(" (offset", 1)[0]
                entry = entry.replace("@loader_path", str(binary.parent))
                rpaths.append(Path(entry))
                break
    return rpaths


def _macos_dependencies(binary: Path) -> list[Path]:
    found: list[Path] = []
    rpaths: list[Path] | None = None
    for line in _otool_lines("-L", binary)[1:]:
        name = line.strip().split(" (compatibility", 1)[0]
        if name.startswith("@rpath/"):
            if rpaths is None:
                rpaths = _macos_rpaths(binary)
            for rpath in rpaths:
                candidate = rpath / name[len("@rpath/") :]
                if candidate.is_file():
                    found.append(candidate)
                    break
        elif name.startswith("@loader_path/"):
            candidate = binary.parent / name[len("@loader_path/") :]
            if candidate.is_file():
                found.append(candidate)
        elif name.startswith("/") and Path(name).is_file():
            found.append(Path(name))
    return found


def _linux_dependencies(binary: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["ldd", str(binary)], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[Path] = []
    for line in result.stdout.splitlines():
        if "=>" not in line:
            continue
        target = line.split("=>", 1)[1].strip().split(" ", 1)[0]
        if target.startswith("/") and Path(target).is_file():
            found.append(Path(target))
    return found


def _dependencies(binary: Path) -> list[Path]:
    if sys.platform == "darwin":
        return _macos_dependencies(binary)
    return _linux_dependencies(binary)


def prefer_newest_openssl(binaries, dependencies=_dependencies):
    """Rewrite an Analysis TOC so one OpenSSL - the newest linked - ships.

    ``binaries`` is a sequence of ``(dest, source, typecode)``. Windows never
    hits the collision (the wheels there link statically or ship distinctly
    named DLLs), so the TOC passes through untouched.
    """
    entries = list(binaries)
    if sys.platform == "win32":
        return entries

    candidates: dict[str, set[Path]] = {}
    for dest, source, _kind in entries:
        name = Path(dest).name
        if _OPENSSL_BASENAME.match(name):
            candidates.setdefault(name, set()).add(Path(source))

    for dest, source, _kind in entries:
        if not Path(dest).name.startswith(_CONSUMER_PREFIXES):
            continue
        for linked in dependencies(Path(source)):
            if _OPENSSL_BASENAME.match(linked.name):
                candidates.setdefault(linked.name, set()).add(linked)

    if not candidates:
        return entries

    winners = {
        name: max(paths, key=openssl_version) for name, paths in candidates.items()
    }

    rebuilt: list[tuple[str, str, str]] = []
    replaced: set[str] = set()
    for dest, source, kind in entries:
        name = Path(dest).name
        if name in winners:
            if name in replaced:
                continue
            replaced.add(name)
            rebuilt.append((dest, str(winners[name]), kind))
        else:
            rebuilt.append((dest, source, kind))

    # A library only the consumers revealed was never collected: ship it, or the
    # bundle will not load at all on machines without that OpenSSL.
    for name, path in winners.items():
        if name not in replaced:
            rebuilt.append((name, str(path), "BINARY"))
    return rebuilt
