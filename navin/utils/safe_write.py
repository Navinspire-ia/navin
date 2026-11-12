# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Write a checked file in place without following a link swapped in after the check.

The file tools resolve a path, check it against the project boundary, then write. Between the two, a command
running in the same folder can replace the file (or one of its folders) with a link to a file outside the
project: a plain ``Path.write_bytes`` would follow it (the TOCTOU and symlink-write class of 2026 coding-agent
advisories). Here the final name is opened with ``O_NOFOLLOW``, and before anything is truncated or written the
opened file is asked where it really is: if that is not the path that was checked, nothing is written.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path


class PathChangedError(PermissionError):
    """The checked path now leads somewhere else; nothing was written."""


def _opened_path(fd: int) -> str | None:
    """Where the open file really is, or None when the system cannot tell."""
    if sys.platform.startswith("linux"):
        return os.readlink(f"/proc/self/fd/{fd}")
    if sys.platform == "darwin":
        import fcntl

        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        return os.fsdecode(raw.split(b"\0", 1)[0])
    return None


def write_bytes_no_follow(path: Path | str, data: bytes) -> None:
    """Replace the content of ``path`` (an already resolved, checked path) with ``data``, in place.

    Raises :class:`PathChangedError` when the final name is a link or when the opened file is not ``path``.
    A file this call created at the wrong place is removed again, so a refused write leaves nothing behind.
    """
    target = os.path.abspath(os.fspath(path))
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        # Windows has no O_NOFOLLOW, and creating a link there needs a privilege the agent does not hold.
        Path(target).write_bytes(data)
        return
    existed = os.path.lexists(target)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | nofollow | getattr(os, "O_CLOEXEC", 0), 0o666)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PathChangedError(f"{target} is a symbolic link: it was not written") from exc
        raise
    try:
        opened = _opened_path(fd)
        if opened is not None and opened != target:
            if not existed:
                os.unlink(opened)
            raise PathChangedError(
                f"{target} changed while it was being written (it now leads to {opened}): nothing was written"
            )
        os.ftruncate(fd, 0)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)
