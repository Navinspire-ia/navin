# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Read the live terminal geometry, including after a window resize."""

import os

from textual.drivers.linux_driver import LinuxDriver


class NavinLinuxDriver(LinuxDriver):
    def _get_terminal_size(self) -> tuple[int, int]:
        # shutil.get_terminal_size prefers exported COLUMNS/LINES, which may
        # still describe the shell before its window was maximized. Ask the
        # actual output/input TTY first; keep Textual's fallback for pipes.
        for descriptor in (self._file.fileno(), self.fileno):
            try:
                size = os.get_terminal_size(descriptor)
            except OSError:
                continue
            if size.columns > 0 and size.lines > 0:
                return size.columns, size.lines
        return super()._get_terminal_size()
