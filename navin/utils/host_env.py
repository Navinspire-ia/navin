# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Environment for host binaries (git, npm) spawned from the frozen sidecar.

The packaged Linux desktop build (deb, rpm, pacman) prepends
``/usr/lib/Navin/navin-dist/_internal`` to ``LD_LIBRARY_PATH``. A host
``git-remote-https`` started with that env loads the bundled ``libssl.so.3``
(OpenSSL 3.0) while the host ``libcurl`` needs ``OPENSSL_3.2.0`` / ``3.5.0``,
and every https clone aborts. Host tools must resolve host libraries.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

_LOADER_KEYS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")
_BUNDLE_MARKER = "navin-dist"


def _is_bundle_dir(part: str) -> bool:
    if _BUNDLE_MARKER in part:
        return True
    meipass = getattr(sys, "_MEIPASS", None)
    return bool(meipass) and os.path.normpath(part) == os.path.normpath(str(meipass))


def host_tool_env(base: Mapping[str, str] | None = None, **extra: str) -> dict[str, str]:
    """Parent env minus the bundle's loader paths, plus ``extra``.

    Only the bundle entries are dropped: anything else the user put on the
    loader path is kept. A ``<KEY>_ORIG`` value saved by the PyInstaller
    bootloader is restored when nothing else is left.
    """
    env = dict(os.environ if base is None else base)
    for key in _LOADER_KEYS:
        value = env.get(key)
        if not value:
            continue
        parts = [part for part in value.split(os.pathsep) if part]
        kept = [part for part in parts if not _is_bundle_dir(part)]
        if len(kept) == len(parts):
            continue
        original = env.get(f"{key}_ORIG")
        if kept:
            env[key] = os.pathsep.join(kept)
        elif original:
            env[key] = original
        else:
            env.pop(key, None)
    env.update(extra)
    return env


def failure_detail(raw: str | None, limit: int = 400) -> str:
    """Shorten tool output keeping both the first error and the final line.

    A plain ``[-limit:]`` slice cut the first error in half (the dialog then
    started with ``uired by /usr/lib/libcurl.so.4``).
    """
    text = (raw or "").strip()
    if len(text) <= limit:
        return text
    half = max(limit // 2, 1)
    return f"{text[:half]}\n...\n{text[-half:]}"
