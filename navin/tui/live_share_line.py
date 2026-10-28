"""Persistent live share line: the link with copy and stop controls next to it.

Shown under the transcript while the CLI chat is shared through the relay,
so the link never has to be fished out of a passing note.
"""

from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Static

from navin.tui.textmarkup import escape

ELLIPSIS = "\u2026"
# "● live ", " copy ", " stop " and the paddings: what the line needs besides the link and the guest count.
CHROME_WIDTH = 20


def fit_link(link: str, width: int) -> str:
    """Shorten ``link`` to ``width`` cells: head (scheme, host, /s/<id>...) and tail kept, ellipsis in the middle."""
    link = link or ""
    if width <= 0:
        return ""
    if len(link) <= width:
        return link
    if width <= 1:
        return ELLIPSIS
    tail = min(8, max(1, (width - 1) // 3))
    head = width - 1 - tail
    return f"{link[:head]}{ELLIPSIS}{link[-tail:]}"


class _Control(Static):
    """One clickable word (``copy``, ``stop``) running an app action, like DockHint."""

    DEFAULT_CSS = """
    _Control {
        width: auto;
        height: 1;
        padding: 0 1 0 0;
        color: $text-muted 80%;
    }
    _Control:hover { color: $foreground; text-style: underline; }
    """

    def __init__(self, label: str, action: str, *, id: str | None = None) -> None:
        super().__init__(escape(label), markup=True, id=id)
        self.action = action

    def on_click(self) -> None:
        self.app.call_later(self.app.run_action, self.action)


class _Link(Static):
    """The shortened link itself; clicking it copies the full link."""

    DEFAULT_CSS = """
    _Link {
        width: auto;
        height: 1;
        padding: 0 1 0 0;
        color: $primary;
    }
    _Link:hover { text-style: underline; }
    """

    def on_click(self) -> None:
        self.app.call_later(self.app.run_action, "copy_live_link")


class LiveShareLine(Horizontal):
    """``● live <link> copy stop 1 guest``, hidden when nothing is shared."""

    DEFAULT_CSS = """
    LiveShareLine {
        height: 0;
        min-height: 0;
        margin: 0;
        padding: 0 1;
        background: $background;
        display: none;
    }
    LiveShareLine.-visible {
        display: block;
        height: 1;
        min-height: 1;
    }
    LiveShareLine > .live-share-dot { width: auto; height: 1; padding: 0 1 0 0; color: $success; }
    LiveShareLine > .live-share-guests { width: auto; height: 1; color: $text-muted; }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.link: str | None = None
        self.guests = 0
        self._shown = ""
        self._guests_shown = ""

    def compose(self):
        yield Static("[b]\u25cf[/b] live", markup=True, classes="live-share-dot", id="live-share-dot")
        yield _Link("", markup=False, id="live-share-link")
        yield _Control("copy", "copy_live_link", id="live-share-copy")
        yield _Control("stop", "stop_live_share", id="live-share-stop")
        yield Static("", markup=False, classes="live-share-guests", id="live-share-guests")

    @property
    def active(self) -> bool:
        return bool(self.link)

    def set_share(self, link: str | None, guests: int = 0) -> None:
        """Paint the line for ``link`` (``None`` hides it); no repaint when nothing changed."""
        guests = max(0, int(guests or 0))
        if link == self.link and guests == self.guests and bool(link) == self.has_class("-visible"):
            return
        self.link, self.guests = link or None, guests
        if not link:
            self.display = False
            self.remove_class("-visible")
            self._shown = ""
            return
        self.display = True
        self.add_class("-visible")
        self._paint()

    def guest_label(self) -> str:
        if self.guests <= 0:
            return ""
        return "1 guest" if self.guests == 1 else f"{self.guests} guests"

    def _link_width(self) -> int:
        width = self.size.width if self.size.width > 0 else 80
        return max(12, width - CHROME_WIDTH - len(self.guest_label()))

    def _paint(self) -> None:
        if not self.link or not self.is_mounted:
            return
        shown = fit_link(self.link, self._link_width())
        if shown != self._shown:
            self._shown = shown
            self.query_one("#live-share-link", _Link).update(shown)
        guests = self.guest_label()
        if guests != self._guests_shown:
            self._guests_shown = guests
            self.query_one("#live-share-guests", Static).update(guests)

    def on_mount(self) -> None:
        if self.link:
            self._paint()

    def on_resize(self) -> None:
        if self.link:
            self._paint()
