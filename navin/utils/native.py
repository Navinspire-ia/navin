# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Access point for the optional native (Rust) extension ``navin_core``.

The extension accelerates hot paths - content search, batch file reads, tree
hashing, process management, git plumbing, PTY sessions, and Android mobile
preview (``MobilePreviewSession``) - but every caller keeps a pure-Python
fallback, so navin runs unchanged when it is absent (a source checkout without
``make native``, or a platform without a prebuilt wheel). Import errors are
swallowed here once, and callers ask :func:`native` for the module or ``None``.

Set ``NAVIN_DISABLE_NATIVE=1`` to force the pure-Python paths (used by the
parity tests and as an escape hatch if a native path ever misbehaves).
"""

from __future__ import annotations

import os
from typing import Any

_module: Any | None = None
_loaded = False


def native() -> Any | None:
    """Return the ``navin_core`` module, or ``None`` if unavailable/disabled."""
    global _module, _loaded
    if _loaded:
        return _module
    _loaded = True
    if os.environ.get("NAVIN_DISABLE_NATIVE") == "1":
        _module = None
        return None
    try:
        import navin_core  # type: ignore
    except ImportError:
        _module = None
    else:
        _module = navin_core
    return _module


def has_native() -> bool:
    return native() is not None


def native_version() -> str | None:
    module = native()
    return getattr(module, "__version__", None) if module is not None else None
