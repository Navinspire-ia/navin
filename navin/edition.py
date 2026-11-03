# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Edition of this build: ``standard`` (Navin) or ``sm`` (Navin SM).

Navin SM is the build an organization distributes to its staff with its own
model gateway (``navin.organization``). The organization decides which
version runs: Navin SM never replaces itself with a public release.

``scripts/set-edition.sh`` rewrites the EDITION line below before an edition
is packaged, and the copy the desktop shell reads at build time
(``desktop/src-tauri/edition.txt``, see ``desktop/src-tauri/build.rs``).
The committed tree is always ``standard``: only the Navin SM build stamps ``sm``.
"""

from __future__ import annotations

EDITION = "standard"

STANDARD = "standard"
SM = "sm"


def current() -> str:
    return EDITION


def self_updates() -> bool:
    """False for Navin SM: its versions come from the organization, not the update server."""
    return current() != SM
