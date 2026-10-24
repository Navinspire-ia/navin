# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Terminal credential card: masked fields, values to the vault, status back."""

from __future__ import annotations

import unittest
from typing import Any

from textual.app import App
from textual.widgets import Input

from navin.tui.credential_screen import CredentialScreen, handle_credential_event
from navin.tui.runtime import UiCredentialClosed, UiCredentialRequested


class CredentialScreenTest(unittest.IsolatedAsyncioTestCase):
    async def test_site_login_saves_to_vault_and_returns_status_only(self) -> None:
        saved: list[tuple[str, dict[str, str]]] = []
        results: list[Any] = []

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(
                    CredentialScreen(
                        "site_login",
                        "app.example.com",
                        ["username", "password"],
                        "Log in to export the report",
                        save_site=lambda site, values: saved.append((site, dict(values))),
                    ),
                    results.append,
                )

        app = Host()
        async with app.run_test(size=(90, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CredentialScreen)
            password = screen.query_one("#cred-password", Input)
            self.assertTrue(password.password)
            self.assertFalse(screen.query_one("#cred-username", Input).password)
            screen.query_one("#cred-username", Input).value = "me@example.com"
            password.value = "s3cret"
            await pilot.click("#cred-save")
            await pilot.pause()
        self.assertEqual(saved, [("app.example.com", {"username": "me@example.com", "password": "s3cret"})])
        self.assertEqual(results, [{"status": "saved", "login": ""}])
        self.assertNotIn("s3cret", repr(results))

    async def test_escape_declines(self) -> None:
        results: list[Any] = []

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(
                    CredentialScreen("git_host", "github.com", reason="Push the branch"), results.append
                )

        app = Host()
        async with app.run_test(size=(90, 30)) as pilot:
            self.assertTrue(app.screen.query_one("#cred-token", Input).password)
            await pilot.press("escape")
            await pilot.pause()
        self.assertEqual(results, [{"status": "declined", "login": ""}])

    async def test_token_saved_then_connected(self) -> None:
        tokens: list[tuple[str, str]] = []
        results: list[Any] = []

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(
                    CredentialScreen(
                        "git_host", "gitlab.example.com", save_token=lambda h, t: tokens.append((h, t))
                    ),
                    results.append,
                )

        app = Host()
        async with app.run_test(size=(90, 30)) as pilot:
            app.screen.query_one("#cred-token", Input).value = "glpat-xyz"
            await pilot.click("#cred-save")
            await pilot.pause()
        self.assertEqual(tokens, [("gitlab.example.com", "glpat-xyz")])
        self.assertEqual(results, [{"status": "connected", "login": ""}])

    async def test_event_bridge_answers_runtime_once(self) -> None:
        answers: list[tuple[str, str]] = []

        class Runtime:
            async def answer_credential(self, request_id: str, *, status: str, login: str = "") -> None:
                answers.append((request_id, status))

        class Host(App):
            runtime = Runtime()

        app = Host()
        async with app.run_test(size=(90, 30)) as pilot:
            handled = await handle_credential_event(
                app, UiCredentialRequested("r1", "git_host", "github.com", [], "why")
            )
            self.assertTrue(handled)
            await pilot.pause()
            self.assertIsInstance(app.screen, CredentialScreen)
            await pilot.press("escape")
            await pilot.pause()
            # A close from the engine after the answer sends nothing more.
            await handle_credential_event(app, UiCredentialClosed("r1", "declined"))
            await pilot.pause()
        self.assertEqual(answers, [("r1", "declined")])


if __name__ == "__main__":
    unittest.main()
