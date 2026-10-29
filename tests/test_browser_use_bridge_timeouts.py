# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""browser-use actions and session start are bounded: a page that never settles
must not pin the agent turn (and the shared gateway loop) forever."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest import mock

from navin.agent.tools import browser_use_bridge as bridge


class _Registry:
    def __init__(self, hang: bool) -> None:
        self.registry = types.SimpleNamespace(actions={"navigate": object(), "go_back": object()})
        self._hang = hang

    async def execute_action(self, *, action_name, params, browser_session):
        if self._hang:
            await asyncio.sleep(3600)
        return types.SimpleNamespace(error=None, extracted_content=f"did {action_name}", long_term_memory=None)


class _Tools:
    def __init__(self, hang: bool) -> None:
        self.registry = _Registry(hang)


class BrowserUseActionTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_hung_action_is_cut_with_a_readable_error(self) -> None:
        with mock.patch.object(bridge, "ACTION_TIMEOUT_S", 0.05):
            with self.assertRaises(RuntimeError) as caught:
                await bridge.run_browser_use_action(object(), _Tools(hang=True), "back", {})
        self.assertIn("go_back", str(caught.exception))
        self.assertIn("did not finish", str(caught.exception))

    async def test_fast_action_output_is_unchanged(self) -> None:
        result = await bridge.run_browser_use_action(object(), _Tools(hang=False), "navigate", {"url": "x"})
        self.assertEqual(result, "did navigate")


class BrowserUseStartTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_start_that_never_returns_is_bounded(self) -> None:
        class FakeSession:
            def __init__(self, browser_profile) -> None:
                self.profile = browser_profile

            async def start(self) -> None:
                await asyncio.sleep(3600)

        fake_pkg = types.ModuleType("browser_use")
        fake_pkg.BrowserSession = FakeSession
        fake_browser = types.ModuleType("browser_use.browser")
        fake_profile = types.ModuleType("browser_use.browser.profile")
        fake_profile.BrowserProfile = lambda **kw: kw
        modules = {
            "browser_use": fake_pkg,
            "browser_use.browser": fake_browser,
            "browser_use.browser.profile": fake_profile,
        }
        with mock.patch.dict(sys.modules, modules), mock.patch.object(bridge, "START_TIMEOUT_S", 0.05):
            with self.assertRaises(RuntimeError) as caught:
                await bridge.create_browser_use_session(executable_path="/bin/true")
        self.assertIn("did not start", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
