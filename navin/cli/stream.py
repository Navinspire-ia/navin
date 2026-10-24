# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Streaming renderer for CLI output.

Uses Rich Live with ``transient=True`` for in-place markdown updates during
streaming.  After the live display stops, a final clean render is printed
so the content persists on screen.  ``transient=True`` ensures the live
area is erased before ``stop()`` returns, avoiding the duplication bug
that plagued earlier approaches.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager, nullcontext

from rich.cells import cell_len, set_cell_size
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.text import Text

from navin.cli.markdown import ResponseMarkdown


def _clear_current_line(console: Console) -> None:
    """Erase a transient status line before printing persistent output."""
    file = console.file
    isatty = getattr(file, "isatty", lambda: False)
    if not isatty():
        return
    file.write("\r\x1b[2K")
    file.flush()


def _make_console() -> Console:
    """Create a Console that emits plain text when stdout is not a TTY.

    Rich's spinner, Live render, and cursor-visibility escape codes all
    key off ``Console.is_terminal``. Forcing ``force_terminal=True`` overrode
    the ``isatty()`` check and caused control sequences (``\\x1b[?25l``,
    braille spinner frames) to pollute programmatic consumers such as
    ``docker exec -i`` or pipes, even with ``NO_COLOR`` or ``TERM=dumb``.
    Deferring to ``isatty()`` keeps Rich output in interactive terminals
    and plain text everywhere else (#3265).
    """
    return Console(file=sys.stdout, force_terminal=sys.stdout.isatty())


class ThinkingSpinner:
    """One live status line: what the agent is doing now, with pause support."""

    def __init__(self, console: Console | None = None, bot_name: str = "navin", text: str = ""):
        c = console or _make_console()
        self._console = c
        self.text = text or f"{bot_name} is working..."
        self._spinner = c.status(f"[dim]{escape(self.text)}[/dim]", spinner="dots")
        self._active = False

    def update(self, text: str) -> None:
        """Show the current activity ("Running pytest", "Working…")."""
        text = " ".join((text or "").split())
        if not text or text == self.text:
            return
        if cell_len(text) > 96:
            # Cut by terminal cells, not characters: wide glyphs count twice.
            text = set_cell_size(text, 95).rstrip() + "…"
        self.text = text
        self._spinner.update(f"[dim]{escape(text)}[/dim]")

    def __enter__(self):
        self._spinner.start()
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        self._spinner.stop()
        _clear_current_line(self._console)
        return False

    def pause(self):
        """Context manager: temporarily stop spinner for clean output."""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            if self._spinner and self._active:
                self._spinner.stop()
                _clear_current_line(self._console)
            try:
                yield
            finally:
                if self._spinner and self._active:
                    self._spinner.start()

        return _ctx()


class StreamRenderer:
    """Streaming renderer with Rich Live for in-place updates.

    During streaming: updates content in-place via Rich Live.
    On end: stops Live (transient=True erases it), then prints final render.

    Flow per round:
      spinner -> first delta -> header + Live updates ->
      on_end -> stop Live + final render
    """

    def __init__(
        self,
        render_markdown: bool = True,
        show_spinner: bool = True,
        bot_name: str = "navin",
        bot_icon: str = "",
    ):
        self._md = render_markdown
        self._show_spinner = show_spinner
        self._bot_name = bot_name
        self._bot_icon = bot_icon
        self._buf = ""
        self.streamed = False
        self._console = _make_console()
        from navin.cli.activity import ActivityPrinter

        self.activity = ActivityPrinter(
            self._console, on_status=self.set_status, on_step_end=self.mark_llm_start,
        )
        self._live: Live | None = None
        self._spinner: ThinkingSpinner | None = None
        self._header_printed = False
        self._status_text = ""
        self._closed = False
        # When the model call in flight started: the turn start, the end of
        # the previous round, or the last tool that finished. A thought is
        # timed from there, so a reasoning block delivered in one piece does
        # not read as "1s".
        self.llm_started = time.monotonic()
        # Consecutive thoughts with nothing printed between them share one
        # "Thought for Ns" line, printed just before the next output.
        self._pending_thought: float | None = None
        self._start_spinner()

    def _renderable(self):
        """Create a renderable from the current buffer."""
        if self._md and self._buf:
            return ResponseMarkdown(self._buf)
        return Text(self._buf or "")

    def _render_str(self) -> str:
        """Render current buffer to a plain string via Rich."""
        with self._console.capture() as cap:
            self._console.print(self._renderable())
        return cap.get()

    def _start_spinner(self) -> None:
        if self._show_spinner and self._spinner is None:
            self._spinner = ThinkingSpinner(bot_name=self._bot_name, text=self._status_text)
            self._spinner.__enter__()

    def mark_llm_start(self) -> None:
        """A new model call starts now (a round ended or a tool finished)."""
        self.llm_started = time.monotonic()

    def note_thought(self, seconds: float) -> None:
        """Hold a finished thought; the next printed output shows it once."""
        self._pending_thought = (self._pending_thought or 0.0) + max(0.0, seconds)
        # The next thought of the same call is timed from here.
        self.mark_llm_start()

    def flush_thought(self) -> None:
        """Print the held ``Thought for Ns`` line (spinner already paused)."""
        seconds = self._pending_thought
        if seconds is None:
            return
        self._pending_thought = None
        self._console.print(
            f"✻ Thought for {max(1, round(seconds))}s", style="dim italic", markup=False, highlight=False,
        )

    def set_status(self, text: str) -> None:
        """Keep the single live status on the current step."""
        self._status_text = text
        if self._spinner is not None:
            self._spinner.update(text)

    def resume_spinner(self) -> None:
        """Bring the live status back after trace lines while the turn runs."""
        if not self._closed and self._live is None and not self._buf.strip():
            self._start_spinner()

    def _stop_spinner(self) -> None:
        if self._spinner:
            self._spinner.__exit__(None, None, None)
            self._spinner = None

    @property
    def console(self) -> Console:
        """Expose the Live's console so external print functions can use it."""
        return self._console

    @property
    def header_printed(self) -> bool:
        """Whether this turn has already opened the assistant output block."""
        return self._header_printed

    def ensure_header(self) -> None:
        """Stop transient status and print the assistant header once."""
        # A turn can print trace rows before the final answer, then restart the
        # spinner while tools run. The next answer delta still needs to stop
        # that spinner even though the header was already printed.
        self._stop_spinner()
        if self._header_printed:
            return
        self._console.print()
        header = f"{self._bot_icon} {self._bot_name}" if self._bot_icon else self._bot_name
        self._console.print(f"[cyan]{header}[/cyan]")
        self._header_printed = True

    def begin_output(self) -> None:
        """Before any persistent line: header once, then a held thought."""
        self.ensure_header()
        self.flush_thought()

    def pause_spinner(self):
        """Context manager: temporarily stop transient output for clean trace lines."""
        @contextmanager
        def _pause():
            live_was_active = self._live is not None
            if self._live:
                # Trace/reasoning can arrive after answer streaming has started.
                # Stop the transient Live view first so it does not leak a raw
                # partial markdown frame before the trace line.
                self._live.stop()
                self._live = None
            with self._spinner.pause() if self._spinner else nullcontext():
                yield
            # If more answer deltas arrive after the trace, on_delta() will
            # create a fresh Live using the existing buffer. If no deltas arrive,
            # on_end() prints the final buffered answer once.
            if live_was_active:
                return

        return _pause()

    async def on_delta(self, delta: str) -> None:
        self.activity.break_group()
        self.streamed = True
        self._buf += delta
        if self._live is None:
            if not self._buf.strip():
                return
            self.begin_output()
            self._live = Live(
                self._renderable(),
                console=self._console,
                auto_refresh=False,
                transient=True,
            )
            self._live.start()
        else:
            self._live.update(self._renderable())
        self._live.refresh()

    async def on_end(self, *, resuming: bool = False, discarded: bool = False) -> None:
        if discarded:
            # A mid-stream retry restarts the answer: the transient Live view
            # vanishes on stop(), so the partial text is never printed.
            if self._live:
                self._live.stop()
                self._live = None
            self._buf = ""
            self.set_status("Working…")
            self._start_spinner()
            return
        if self._live:
            # Double-refresh to sync _shape before stop() calls refresh().
            self._live.refresh()
            self._live.update(self._renderable())
            self._live.refresh()
            self._live.stop()
            self._live = None
        self._stop_spinner()
        if self._buf.strip():
            # Print final rendered content (persists after Live is gone).
            self.flush_thought()
            out = sys.stdout
            out.write(self._render_str())
            out.flush()
        if resuming:
            # Tools run next, then a new model call: one calm status until
            # a step names itself, never a fresh "navin is working...".
            self._buf = ""
            self.mark_llm_start()
            self.set_status("Working…")
            self._start_spinner()
        else:
            self._closed = True
            if self._pending_thought is not None:
                self.begin_output()

    def stop_for_input(self) -> None:
        """Stop spinner before user input to avoid prompt_toolkit conflicts."""
        self._closed = True
        self._stop_spinner()

    def pause(self):
        """Context manager: pause spinner for external output. No-op once streaming has started."""
        if self._spinner:
            return self._spinner.pause()
        return nullcontext()

    async def close(self) -> None:
        """Stop spinner/live without rendering a final streamed round."""
        self._closed = True
        if self._live:
            self._live.stop()
            self._live = None
        self._stop_spinner()
        if self._pending_thought is not None:
            # The answer is printed next, under the same header.
            self.begin_output()
