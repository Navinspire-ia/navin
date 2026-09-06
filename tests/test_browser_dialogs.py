"""Dialogs and popups must not silently stall or fork the browser session.

An unanswered ``alert()`` or ``confirm()`` blocks every later Playwright action
until it times out, with no explanation the agent can act on; a target=_blank
link opens a page the session never tracked, so every later action kept hitting
the old one. These exercise the handlers wired in ``_wire_events`` against fake
pages, because a real Chromium is not a given in this environment.
"""

from __future__ import annotations

import unittest

from navin.agent.tools.browser import BrowserToolConfig, _BrowserSession


class _FakePage:
    """Records the handlers _wire_events registers, so tests can fire them."""

    def __init__(self, url: str = "https://example.test/") -> None:
        self.url = url
        self.handlers: dict[str, object] = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler


class _FakeDialog:
    def __init__(self, type_: str, message: str, *, broken: bool = False) -> None:
        self.type = type_
        self.message = message
        self.accepted = False
        self.dismissed = False
        self._broken = broken

    async def accept(self) -> None:
        if self._broken:
            raise RuntimeError("dialog already handled")
        self.accepted = True

    async def dismiss(self) -> None:
        if self._broken:
            raise RuntimeError("dialog already handled")
        self.dismissed = True


class DialogHandlingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = _BrowserSession(BrowserToolConfig())
        self.page = _FakePage()
        self.session._wire_events(self.page)
        self.session.page = self.page

    async def _fire(self, dialog: _FakeDialog) -> None:
        await self.page.handlers["dialog"](dialog)

    async def test_a_confirm_is_dismissed_not_accepted(self) -> None:
        # Accepting could commit a destructive action the agent never chose;
        # dismissing is the reversible answer.
        dialog = _FakeDialog("confirm", "Delete everything?")
        await self._fire(dialog)
        self.assertTrue(dialog.dismissed)
        self.assertFalse(dialog.accepted)

    async def test_an_alert_is_dismissed_so_actions_do_not_hang(self) -> None:
        dialog = _FakeDialog("alert", "Saved!")
        await self._fire(dialog)
        self.assertTrue(dialog.dismissed)

    async def test_beforeunload_is_accepted_so_navigation_proceeds(self) -> None:
        # Dismissing beforeunload cancels the navigation the agent just asked
        # for, which is the one dialog where "no" is the blocking answer.
        dialog = _FakeDialog("beforeunload", "")
        await self._fire(dialog)
        self.assertTrue(dialog.accepted)
        self.assertFalse(dialog.dismissed)

    async def test_the_dialog_message_reaches_the_console_log(self) -> None:
        await self._fire(_FakeDialog("confirm", "Delete everything?"))
        entries = list(self.session.console)
        self.assertTrue(any("[dialog]" in e and "Delete everything?" in e for e in entries))

    async def test_a_dialog_playwright_already_closed_does_not_raise(self) -> None:
        # The race is real: the page can dismiss its own dialog before the
        # handler runs. The log line must survive even then.
        await self._fire(_FakeDialog("alert", "gone", broken=True))
        self.assertTrue(any("[dialog]" in e for e in self.session.console))


class PopupHandlingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = _BrowserSession(BrowserToolConfig())
        self.page = _FakePage()
        self.session._wire_events(self.page)
        self.session.page = self.page

    def _open_popup(self) -> _FakePage:
        popup = _FakePage(url="https://example.test/popup")
        self.page.handlers["popup"](popup)
        return popup

    def test_the_session_follows_the_new_page(self) -> None:
        popup = self._open_popup()
        self.assertIs(self.session.page, popup)

    def test_the_switch_is_recorded_in_the_console_log(self) -> None:
        self._open_popup()
        entries = list(self.session.console)
        self.assertTrue(any("[popup]" in e and "example.test/popup" in e for e in entries))

    def test_the_popup_gets_the_same_handlers(self) -> None:
        # A dialog raised by the popup, or a popup it opens in turn, must be
        # covered too, or the original trap reappears one page deeper.
        popup = self._open_popup()
        for event in ("console", "dialog", "popup"):
            self.assertIn(event, popup.handlers)


if __name__ == "__main__":
    unittest.main()
