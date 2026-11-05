# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Full-screen terminal UI for Navin (``navin-cli``).

This package is isolated from the rest of the CLI: nothing in ``navin.cli``
imports it eagerly, and it only *consumes* the public agent engine
(``AgentLoop``, the message bus and its typed outbound events). It never
patches or reconfigures the engine.
"""

from __future__ import annotations

__all__ = ["run_tui"]


def run_tui(**kwargs):  # type: ignore[no-untyped-def]
    """Lazy entry point so importing ``navin.tui`` stays cheap."""
    from navin.tui.terminal import prepare_terminal_env
    from navin.utils.process_isolation import prepare_process

    prepare_terminal_env()  # before Textual reads TEXTUAL_COLOR_SYSTEM
    from navin.tui import resilience

    # Before the UI starts its threads: glibc arenas, and a systemd scope of
    # its own so one out-of-memory kill elsewhere does not stop this CLI.
    prepare_process("cli", report=lambda text: resilience.record("process", text))
    from navin.tui.app import run_tui as _run

    return _run(**kwargs)
