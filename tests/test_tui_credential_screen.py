# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Terminal credential card: masked fields, values to the vault, status back."""

from __future__ import annotations

import unittest
from typing import Any

from textual.app import App
from textual.widgets import Button, Input, Static

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

    async def test_connector_card_opens_even_when_the_vault_is_unreadable(self) -> None:
        from unittest.mock import patch

        answers: list[tuple[str, str]] = []

        class Runtime:
            async def answer_credential(self, request_id: str, *, status: str, login: str = "") -> None:
                answers.append((request_id, status))

        class Host(App):
            runtime = Runtime()

        def unreadable(service: str) -> dict[str, Any]:
            raise PermissionError("the connectors index is not readable")

        app = Host()
        with patch("navin.connectors.manager.terminal_form", unreadable):
            async with app.run_test(size=(100, 40)) as pilot:
                handled = await handle_credential_event(
                    app, UiCredentialRequested("r2", "connector", "hubspot", [], "Sync the CRM")
                )
                self.assertTrue(handled)
                await pilot.pause()
                screen = app.screen
                self.assertIsInstance(screen, CredentialScreen)
                # No form to fill without the app's details: only Decline is offered.
                self.assertEqual(len(screen.query("#cred-save")), 0)
                self.assertEqual(len(screen.query("#conn-oauth")), 0)
                await pilot.press("escape")
                await pilot.pause()
        self.assertEqual(answers, [("r2", "declined")])

    async def test_connector_key_is_verified_and_only_the_account_comes_back(self) -> None:
        calls: list[tuple[str, str, dict[str, str]]] = []
        results: list[Any] = []
        form = {
            "service": "zendesk",
            "name": "Zendesk",
            "key_method": {"type": "api_key", "fields": [
                {"key": "apiKey", "label": "API Token", "required": True, "secret": True},
                {"key": "subdomain", "label": "Subdomain", "required": True, "secret": False},
                {"key": "email", "label": "Email", "required": True, "secret": False},
            ]},
            "oauth": {"configured": False, "label": "Zendesk", "needs_fields": True},
        }

        def connect(service: str, method: str, values: dict[str, str]) -> dict[str, Any]:
            calls.append((service, method, dict(values)))
            return {"status": {"connected": True, "account": "Agent Smith"}}

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(
                    CredentialScreen("connector", "zendesk", reason="Read the tickets", connector_form=form, connect=connect),
                    results.append,
                )

        app = Host()
        async with app.run_test(size=(100, 60)) as pilot:
            screen = app.screen
            self.assertTrue(screen.query_one("#conn-apiKey", Input).password)
            self.assertFalse(screen.query_one("#conn-subdomain", Input).password)
            self.assertEqual(len(screen.query("#conn-oauth")), 0)
            screen.query_one("#cred-save", Button).press()
            await pilot.pause()
            self.assertEqual(calls, [])
            self.assertIn("Fill API Token, Subdomain, Email", str(screen.query_one("#cred-error", Static).render()))
            screen.query_one("#conn-apiKey", Input).value = "zd-secret-token"
            screen.query_one("#conn-subdomain", Input).value = "acme"
            screen.query_one("#conn-email", Input).value = "a@b.co"
            screen.query_one("#cred-save", Button).press()
            for _ in range(50):
                await pilot.pause(0.02)
                if results:
                    break
        self.assertEqual(calls, [("zendesk", "api_key", {"apiKey": "zd-secret-token", "subdomain": "acme", "email": "a@b.co"})])
        self.assertEqual(results, [{"status": "connected", "login": "Agent Smith"}])
        self.assertNotIn("zd-secret-token", repr(results))

    async def test_connector_oauth_opens_the_browser_and_waits_for_the_connection(self) -> None:
        opened: list[str] = []
        results: list[Any] = []
        state = {"polls": 0}
        form = {"service": "notion", "name": "Notion", "key_method": None,
                "oauth": {"configured": True, "label": "Notion", "needs_fields": False}}

        def status(service: str) -> tuple[bool, str]:
            state["polls"] += 1
            return (state["polls"] >= 2, "Workspace A")

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(
                    CredentialScreen(
                        "connector", "notion", connector_form=form,
                        oauth_start=lambda service: "https://api.notion.com/v1/oauth/authorize?state=x",
                        oauth_status=status, browser=lambda url: opened.append(url) or True, poll_interval=0.01,
                    ),
                    results.append,
                )

        app = Host()
        async with app.run_test(size=(100, 40)) as pilot:
            app.screen.query_one("#conn-oauth", Button).press()
            for _ in range(50):
                await pilot.pause(0.02)
                if results:
                    break
        self.assertEqual(opened, ["https://api.notion.com/v1/oauth/authorize?state=x"])
        self.assertEqual(results, [{"status": "connected", "login": "Workspace A"}])

    async def test_connector_sign_in_comes_before_the_key_form(self) -> None:
        form = {
            "service": "hubspot",
            "name": "HubSpot",
            "key_method": {"type": "api_key", "fields": [
                {"key": "apiKey", "label": "Private app token", "required": True, "secret": True},
            ]},
            "oauth": {"configured": True, "label": "HubSpot", "needs_fields": False},
        }

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(CredentialScreen("connector", "hubspot", reason="Sync the CRM", connector_form=form))

        app = Host()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            order = [node.id for node in screen.walk_children() if node.id in {"conn-oauth", "conn-apiKey", "cred-save"}]
            self.assertEqual(order, ["conn-oauth", "conn-apiKey", "cred-save"])
            self.assertEqual(screen.query_one("#conn-oauth", Button).variant, "primary")
            self.assertEqual(screen.query_one("#cred-save", Button).variant, "default")
            self.assertIs(app.focused, screen.query_one("#conn-oauth", Button))

    async def test_connector_without_a_ready_oauth_app_keeps_the_key_form_first(self) -> None:
        form = {
            "service": "hubspot",
            "name": "HubSpot",
            "key_method": {"type": "api_key", "fields": [
                {"key": "apiKey", "label": "Private app token", "required": True, "secret": True},
            ]},
            "oauth": {"configured": False, "label": "HubSpot", "needs_fields": False},
        }

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(CredentialScreen("connector", "hubspot", connector_form=form))

        app = Host()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertEqual(len(screen.query("#conn-oauth")), 0)
            self.assertEqual(screen.query_one("#cred-save", Button).variant, "primary")
            self.assertIs(app.focused, screen.query_one("#conn-apiKey", Input))


if __name__ == "__main__":
    unittest.main()
