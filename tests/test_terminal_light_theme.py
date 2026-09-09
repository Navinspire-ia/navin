# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Light-mode terminal chrome must not keep xterm's hardcoded black viewport.

xterm.css sets .xterm-viewport { background-color: #000 } on macOS so the
scrollbar track is opaque. Dark mode hides that. Light mode showed a black
slab plus WebKit's native I-beam caret on the IME textarea.
"""

from __future__ import annotations

import unittest
from pathlib import Path

DEV = Path(__file__).resolve().parents[1] / "webui" / "src" / "components" / "dev"


class TerminalLightThemeTest(unittest.TestCase):
    def test_css_overrides_the_black_viewport_and_hides_the_native_caret(self) -> None:
        css = (DEV / "terminal.css").read_text(encoding="utf-8")
        self.assertIn(".navin-terminal .xterm-viewport", css)
        self.assertIn("--navin-terminal-bg", css)
        self.assertIn("caret-color: transparent", css)

    def test_both_terminal_hosts_paint_the_surface_from_the_theme(self) -> None:
        for name in ("DevTerminal.tsx", "AgentExecTerminal.tsx"):
            with self.subTest(name=name):
                source = (DEV / name).read_text(encoding="utf-8")
                self.assertIn("terminalSurfaceStyle(isDark)", source)
                self.assertIn("paintTerminalViewport", source)
