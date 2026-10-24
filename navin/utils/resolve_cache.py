# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``Path.resolve()`` for workspace roots, remembered for a few seconds.

On Windows, ``resolve()`` opens the path and asks the system for its final
name. For a ``\\\\wsl.localhost`` project that is a round trip through the 9P
bridge: 20 ms when the bridge is idle, seconds when a search or the index
warmer keeps it busy. The desktop log of 2026-10-21 shows the event loop
blocked 161 times inside ``build_workspace_scope``,
``workspace_sandbox_status`` and ``schedule_warm`` doing that for the same
root again and again, on every HTTP route and every message.

Only for roots: a five-second TTL is the same trade-off the project-directory
check in :mod:`navin.security.workspace_access` already makes. Never use it for
the paths that tools read or write, which must be resolved at use time.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

ROOT_RESOLVE_TTL_S = 5.0
_MAX_ENTRIES = 512
_CACHE: dict[str, tuple[float, Path]] = {}


def resolve_root(path: str | Path, *, ttl: float = ROOT_RESOLVE_TTL_S) -> Path:
    """``Path(path).expanduser().resolve(strict=False)``, cached when absolute.

    A relative path depends on the working directory, so it is resolved
    every time.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return candidate.resolve(strict=False)
    key = os.fspath(candidate)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    resolved = candidate.resolve(strict=False)
    if len(_CACHE) >= _MAX_ENTRIES:
        _CACHE.clear()
    _CACHE[key] = (now, resolved)
    # A resolved root resolves to itself: callers that pass the result back
    # (build_workspace_scope does) hit the cache instead of the disk.
    _CACHE.setdefault(os.fspath(resolved), (now, resolved))
    return resolved


def clear_root_cache() -> None:
    _CACHE.clear()
