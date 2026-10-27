# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin App Factory: turn an existing web or full-stack project into native apps.

Two technologies are imposed so the client never picks a framework: Tauri 2 +
Rust for desktop (Windows first, then macOS and Linux) and Capacitor 8 for
mobile (Android first, then iOS). Everything is generated in ``os/<target>``
at the project root; the web project and its backend are never overwritten.

Modules:

- :mod:`.targets` the seven targets, their engine, folder, deliverables and host
- :mod:`.analyze` read the project (frontend, backend, server features, auth)
- :mod:`.compat` compatibility diagnosis and the ordered conversion plan
- :mod:`.toolchain` what the target needs on this machine, with official installs
- :mod:`.scaffold` the non-destructive ``os/<target>`` skeleton and its audit
- :mod:`.deliver` built packages and the honest delivery report
"""

from navin.appfactory.targets import TARGETS, Target, get_target

__all__ = ["TARGETS", "Target", "get_target"]
