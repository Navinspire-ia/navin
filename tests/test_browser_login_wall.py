# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The agent must not sit stuck on a login page like nothing happened.

Reported flow: the agent opens the app under test in the browser, the app
shows a login form, and the agent keeps snapshotting/clicking the same wall
without ever telling the user. The fix: when a snapshot lands on a page
with a visible password field, the snapshot carries a clear directive -
fill the form once if credentials are known, otherwise ask the user to log
in. Hidden password fields (non-login pages) must not trigger it.
"""

from __future__ import annotations

import unittest

from navin.agent.tools.browser import BrowserTool, BrowserToolConfig, _BrowserSession


class _FakeLocator:
    def __init__(self, count: int, visible: bool) -> None:
        self._count = count
        self._visible = visible

    async def count(self) -> int:
        return self._count

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def is_visible(self) -> bool:
        return self._visible


class _FakePage:
    def __init__(self, password_count: int = 0, visible: bool = True) -> None:
        self._password_count = password_count
        self._visible = visible
        self.selector: str | None = None

    def locator(self, selector: str) -> _FakeLocator:
        self.selector = selector
        return _FakeLocator(self._password_count, self._visible)

    async def evaluate(self, _js: str) -> dict:
        return {
            "title": "Sign in - App",
            "url": "http://127.0.0.1:3015/login",
            "elements": [],
            "text": "Sign in to continue",
        }


class LoginWallTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tool = BrowserTool()
        self.session = _BrowserSession(BrowserToolConfig())

    async def test_visible_password_field_flags_a_login_wall(self) -> None:
        page = _FakePage(password_count=1)
        snapshot = await self.tool._snapshot(self.session, page)
        self.assertIn("[login-wall]", snapshot)
        self.assertIn("ask the user to log in", snapshot)
        # The note sits right under Page/URL, before anything else.
        self.assertLess(snapshot.index("[login-wall]"), snapshot.index("Sign in to continue"))
        self.assertEqual(page.selector, 'input[type="password"]')

    async def test_no_password_field_no_note(self) -> None:
        snapshot = await self.tool._snapshot(self.session, _FakePage(password_count=0))
        self.assertNotIn("[login-wall]", snapshot)

    async def test_hidden_password_field_does_not_trigger(self) -> None:
        # e.g. a page that keeps a hidden signup password input in the DOM.
        snapshot = await self.tool._snapshot(
            self.session, _FakePage(password_count=1, visible=False)
        )
        self.assertNotIn("[login-wall]", snapshot)

    async def test_locator_errors_are_swallowed(self) -> None:
        class _BrokenPage(_FakePage):
            def locator(self, _selector: str) -> _FakeLocator:
                raise RuntimeError("not connected")

        snapshot = await self.tool._snapshot(self.session, _BrokenPage())
        self.assertNotIn("[login-wall]", snapshot)


if __name__ == "__main__":
    unittest.main()
