# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bulk file work on a WSL project seen from Windows, done in one wsl.exe call.

Through ``\\\\wsl.localhost`` every ``stat`` and every ``open`` is a 9P round
trip. A pass over a few thousand files (the code index, the change watcher)
took minutes that way, while one process inside the distribution answers the
same question in well under a second. Each helper here returns None when it
cannot run, so callers keep their per-file path as the fallback.
"""

from __future__ import annotations

import io
import shlex
import subprocess
import sys
import tarfile
import zlib
from collections.abc import Iterable
from pathlib import Path

from navin.utils import wsl
from navin.utils.proc import no_window_kwargs

_TIMEOUT_S = 120

# `%Y` is exact epoch seconds and `%y` carries the nanoseconds in text: no
# timestamp goes through a float (GNU stat's `%.9Y` loses low bits).
_STAT_SCRIPT = "export LC_ALL=C; xargs -0 -r stat --printf='%Y %y %s %n\\0' --"
# --ignore-failed-read: a file deleted since the listing must not void the rest.
_TAR_SCRIPT = "export LC_ALL=C; tar --null --no-recursion --ignore-failed-read -T - -cf - 2>/dev/null"


def location_for(root: Path | str, *, platform: str | None = None) -> wsl.WslLocation | None:
    """Where ``root`` lives in its distribution, when reached from Windows."""
    if (platform or sys.platform) != "win32":
        return None
    location = wsl.parse_unc(str(root))
    if location is None or not wsl.wsl_executable():
        return None
    return location


def _run(location: wsl.WslLocation, script: str, payload: bytes) -> bytes | None:
    distro = wsl.resolve_distro(location.distro) or location.distro
    try:
        out = subprocess.run(  # noqa: S603
            [*wsl.command_prefix(distro, location.path), "sh", "-c", script],
            input=payload, capture_output=True, timeout=_TIMEOUT_S, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # 1 / 123: some listed file vanished or was unreadable; the rest is good.
    return out.stdout if out.returncode in (0, 1, 2, 123) else None


def _paths_payload(rels: Iterable[str]) -> bytes:
    return b"".join(rel.encode("utf-8", "surrogateescape") + b"\0" for rel in rels)


def parse_stamps(raw: bytes) -> dict[str, tuple[int, int]] | None:
    """``{rel: (mtime_ns, size)}`` from the stat script; None on an old stat."""
    stamps: dict[str, tuple[int, int]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            seconds, _date, clock, _zone, size, name = record.decode("utf-8", "surrogateescape").split(" ", 5)
            _whole, dot, fraction = clock.partition(".")
            if not dot or not fraction.isdecimal() or len(fraction) > 9:
                raise ValueError
            stamps[name.removeprefix("./")] = (
                int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0")), int(size),
            )
        except ValueError:
            return None
    return stamps


def stat_files(root: Path | str, rels: list[str]) -> dict[str, tuple[int, int]] | None:
    """Stamps for project-relative ``rels``, missing files left out."""
    location = location_for(root)
    if location is None:
        return None
    if not rels:
        return {}
    raw = _run(location, _STAT_SCRIPT, _paths_payload(rels))
    return parse_stamps(raw) if raw is not None else None


def read_files(root: Path | str, rels: list[str]) -> dict[str, bytes] | None:
    """Contents of project-relative ``rels`` streamed as one tar archive."""
    location = location_for(root)
    if location is None:
        return None
    if not rels:
        return {}
    raw = _run(location, _TAR_SCRIPT, _paths_payload(rels))
    if raw is None:
        return None
    out: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    out[member.name.removeprefix("./")] = handle.read()
    except (tarfile.TarError, OSError):
        return out or None
    return out


def layout_digest(
    root: Path | str,
    *,
    names: Iterable[str],
    watched_dirs: Iterable[str],
    prune: Iterable[str],
    max_depth: int,
) -> int | None:
    """Digest of a project's layout, read in one call inside the distribution.

    Covers, down to ``max_depth``: every folder by name (one added or removed
    changes it), files matching ``names`` (shell patterns) with their mtime,
    ``watched_dirs`` with their mtime (dependencies installed) and
    ``.navin/commands.json``. ``prune`` folders are not entered. Source edits
    leave it unchanged, which is the point: it says when a project's commands
    may have changed.
    """
    location = location_for(root)
    if location is None:
        return None

    def any_name(items: Iterable[str]) -> str:
        return " -o ".join(f"-name {shlex.quote(item)}" for item in sorted(items))

    watched = sorted(watched_dirs)
    skipped = sorted(set(prune) - set(watched))
    clauses = []
    if watched:
        clauses.append(f"\\( -type d \\( {any_name(watched)} \\) -prune -printf 'W %P %T@\\0' \\)")
    if skipped:
        clauses.append(f"\\( -type d \\( {any_name(skipped)} \\) -prune \\)")
    clauses.append("\\( -type d -printf 'D %P\\0' \\)")
    clauses.append(f"\\( -type f \\( {any_name(names)} \\) -printf 'F %P %T@ %s\\0' \\)")
    script = (
        f"export LC_ALL=C; find . -maxdepth {int(max_depth)} " + " -o ".join(clauses)
        + "; stat --printf='S %n %Y\\0' .navin/commands.json 2>/dev/null; true"
    )
    raw = _run(location, script, b"")
    if raw is None or b"D \0" not in raw:  # the root itself must be listed
        return None
    digest = 0
    for record in raw.split(b"\0"):
        if record:
            digest ^= zlib.crc32(record)
    return digest


def tree_signature(root: Path | str, prune: Iterable[str], *, max_entries: int) -> int | None:
    """Digest of every file's (path, mtime, size), listed inside the distribution.

    Same role as the change watcher's own walk, without one 9P stat per entry.
    """
    location = location_for(root)
    if location is None:
        return None
    names = " -o ".join(f"-name {shlex.quote(name)}" for name in sorted(prune))
    script = (
        "export LC_ALL=C; find . -mindepth 1 "
        + (f"-type d \\( {names} \\) -prune -o " if names else "")
        + f"-type f -printf '%P %T@ %s\\0' | head -z -n {int(max_entries)}"
    )
    raw = _run(location, script, b"")
    if raw is None:
        return None
    digest = 0
    for record in raw.split(b"\0"):
        if record:
            digest ^= zlib.crc32(record)
    return digest
