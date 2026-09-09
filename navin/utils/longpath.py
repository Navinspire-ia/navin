# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Reach files whose path is longer than Windows' default limit.

Win32 rejects paths over 260 characters with a bare "cannot find the path
specified", which reads as a missing file and sends the agent looking for a typo
that is not there. The limit is lifted by the `\\\\?\\` prefix, which tells Win32
to skip the legacy parsing, and it works whether or not the machine has long
paths enabled in the registry.

The prefix is only applied at the moment of touching the filesystem, and only
when the path is actually long. Everywhere else the plain path is kept, because
the prefix breaks `relative_to` comparisons and would leak into paths shown to
the model.
"""

from __future__ import annotations

import os
from pathlib import Path

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"

# Win32 caps a path at 260 including the terminating NUL, and some calls append
# to a directory before checking. Applying the prefix a little early costs
# nothing and covers directories that are about to grow a filename.
_THRESHOLD = 240


def is_windows() -> bool:
    return os.name == "nt"


def io_path(path: Path | str) -> Path:
    """Return the form of *path* to hand to a filesystem call.

    Returns *path* unchanged on POSIX, and on Windows whenever the path is short
    enough that the prefix would be pointless.
    """
    if not is_windows():
        return Path(path)
    text = os.fspath(path)
    if text.startswith(_PREFIX):
        return Path(text)
    if len(text) < _THRESHOLD:
        return Path(text)
    # The prefix disables normalisation, so the path has to be absolute and free
    # of `.`, `..` and forward slashes before it is applied.
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        return Path(_UNC_PREFIX + absolute[2:])
    return Path(_PREFIX + absolute)


def display(path: Path | str) -> str:
    """Strip the prefix back off for anything the user or model reads."""
    text = os.fspath(path)
    if text.startswith(_UNC_PREFIX):
        return "\\\\" + text[len(_UNC_PREFIX) :]
    if text.startswith(_PREFIX):
        return text[len(_PREFIX) :]
    return text
