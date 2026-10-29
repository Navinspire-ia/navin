# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Credential card for the terminal: masked fields, token or device code.

The agent asked for a site login, a git account or an app connector
(request_credentials). This modal writes the values straight to the vault,
to Settings > Git & GitHub or to the connector vault (after the app
accepted them), and dismisses with a status only (``saved`` / ``connected``
/ ``declined`` plus a login name), which is all the agent ever receives.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Callable

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from navin.tui.textmarkup import escape

_FIELD_LABELS = {"username": "Username or email", "password": "Password", "totp": "2FA seed (optional)"}
_MASKED = frozenset({"password", "totp"})


def save_site_values(site: str, values: dict[str, str]) -> None:
    """Store non-empty values in the browser credential vault."""
    from navin.browser_credentials import BrowserCredentialStore

    clean = {name: value for name, value in values.items() if value}
    if clean:
        BrowserCredentialStore().set_secrets(site, clean)


def save_forge_token(host: str, token: str) -> None:
    """Store a forge token the same way Settings > Git & GitHub does."""
    from navin.webui import git_accounts
    from navin.webui.settings_api import _apply_forge_host, load_config, save_config

    config = load_config()
    if _apply_forge_host(config, {"forge_token": [token.strip()]}, host):
        save_config(config)
    git_accounts.clear_account_cache()


def start_device_flow(host: str) -> dict[str, Any]:
    from navin.webui import git_accounts

    return git_accounts.start_device_flow(host)


def poll_device_flow(flow_id: str) -> dict[str, Any]:
    from navin.webui import git_accounts

    return git_accounts.poll_device_flow(flow_id)


def connect_connector(service: str, method: str, values: dict[str, str]) -> dict[str, Any]:
    """Verify a connector key with the app and store it, like Settings does."""
    from navin.connectors import manager
    from navin.connectors.store import ConnectorStore

    return manager.connect(ConnectorStore(), service, method, values)


def start_connector_oauth(service: str) -> str:
    """Authorization URL of a connector whose OAuth app is set up."""
    from navin.connectors import oauth
    from navin.connectors.store import ConnectorStore

    return str(oauth.start(ConnectorStore(), service, {})["authorization_url"])


def connector_status(service: str) -> tuple[bool, str]:
    from navin.connectors.manager import is_connected

    return is_connected(service)


def open_browser(url: str) -> bool:
    import webbrowser

    return bool(webbrowser.open(url))


class CredentialScreen(ModalScreen[dict[str, str]]):
    """Dismisses with ``{"status": saved|connected|declined, "login": ...}``."""

    DEFAULT_CSS = """
    CredentialScreen { align: center middle; }
    CredentialScreen > VerticalScroll {
        width: 72;
        max-width: 96%;
        height: auto;
        max-height: 92%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    CredentialScreen .cred-hint { color: $text-muted; margin: 0 0 1 0; }
    CredentialScreen .field-label { margin: 1 0 0 0; }
    CredentialScreen Input { border: none; height: 1; padding: 0 1; background: #2A2A2A; }
    CredentialScreen Input:focus { border: none; background: #2F3A4A; }
    CredentialScreen Horizontal { height: auto; margin: 1 0 0 0; }
    CredentialScreen Button { margin: 0 1 0 0; }
    CredentialScreen #cred-error { color: $error; height: auto; }
    CredentialScreen #cred-device { height: auto; margin: 1 0 0 0; }
    """

    BINDINGS = [Binding("escape", "decline", "Decline")]

    def __init__(
        self,
        kind: str,
        host: str,
        names: list[str] | None = None,
        reason: str = "",
        *,
        save_site: Callable[[str, dict[str, str]], None] = save_site_values,
        save_token: Callable[[str, str], None] = save_forge_token,
        device_start: Callable[[str], dict[str, Any]] = start_device_flow,
        device_poll: Callable[[str], dict[str, Any]] = poll_device_flow,
        connector_form: dict[str, Any] | None = None,
        connect: Callable[[str, str, dict[str, str]], dict[str, Any]] = connect_connector,
        oauth_start: Callable[[str], str] = start_connector_oauth,
        oauth_status: Callable[[str], tuple[bool, str]] = connector_status,
        browser: Callable[[str], bool] = open_browser,
        poll_interval: float = 2.0,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.host = host
        self.names = [n for n in (names or ["username", "password"]) if n in _FIELD_LABELS]
        self.reason = reason
        self._save_site = save_site
        self._save_token = save_token
        self._device_start = device_start
        self._device_poll = device_poll
        self._device_run = 0
        self._form = connector_form or {}
        key_method = self._form.get("key_method") or {}
        self._key_fields: list[dict[str, Any]] = list(key_method.get("fields") or [])
        self._key_type = str(key_method.get("type") or "")
        self._connect = connect
        self._oauth_start = oauth_start
        self._oauth_status = oauth_status
        self._browser = browser
        self._poll_interval = poll_interval

    def _oauth_ready(self) -> bool:
        """The app signs in with OAuth here: its OAuth app is set up and asks for nothing more."""
        oauth = self._form.get("oauth") or {}
        return bool(oauth.get("configured") and not oauth.get("needs_fields"))

    def _compose_connector(self) -> ComposeResult:
        name = str(self._form.get("name") or self.host)
        yield Static(f"Connect {escape(name)}", classes="picker-title", markup=True)
        if self.reason:
            yield Static(escape(self.reason), classes="cred-hint", markup=True)
        yield Static(
            f"Checked with {escape(name)}, then kept in the local vault. The agent never sees the value.",
            classes="cred-hint",
            markup=True,
        )
        oauth = self._form.get("oauth") or {}
        if self._oauth_ready():
            # Sign-in first whenever the app offers it, as in the web form.
            yield Button(f"Sign in with {name}", variant="primary", id="conn-oauth")
            if self._key_fields:
                yield Static("Or connect with an API key:", classes="field-label")
        for field in self._key_fields:
            label = str(field.get("label") or field["key"]) + ("" if field.get("required") else " (optional)")
            yield Static(escape(label), classes="field-label", markup=True)
            yield Input(
                password=bool(field.get("secret")),
                placeholder=str(field.get("placeholder") or ""),
                value="" if field.get("secret") else str(field.get("defaultValue") or ""),
                id=f"conn-{field['key']}",
                compact=True,
            )
        if oauth and not self._oauth_ready():
            yield Static(
                f"To sign in with OAuth, add the {escape(str(oauth.get('label') or name))} OAuth app in "
                "Settings > Channels > Connectors (web or desktop).",
                classes="cred-hint",
                markup=True,
            )
        yield Static("", id="cred-device")
        yield Static("", id="cred-error")
        with Horizontal():
            if self._key_fields:
                yield Button("Connect", variant="default" if self._oauth_ready() else "primary", id="cred-save")
            yield Button("Decline  esc", id="cred-decline")

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            if self.kind == "connector":
                yield from self._compose_connector()
                return
            if self.kind == "site_login":
                yield Static(f"Sign in to {escape(self.host)}", classes="picker-title", markup=True)
            else:
                yield Static(f"Connect {escape(self.host)}", classes="picker-title", markup=True)
            if self.reason:
                yield Static(escape(self.reason), classes="cred-hint", markup=True)
            yield Static(
                "Saved in the local vault. The agent only gets a reference, never the value.",
                classes="cred-hint",
            )
            if self.kind == "site_login":
                for name in self.names:
                    yield Static(_FIELD_LABELS[name], classes="field-label")
                    yield Input(password=name in _MASKED, id=f"cred-{name}", compact=True)
            else:
                yield Static("Access token", classes="field-label")
                yield Input(password=True, id="cred-token", compact=True)
                yield Static("", id="cred-device")
            yield Static("", id="cred-error")
            with Horizontal():
                yield Button("Save" if self.kind == "site_login" else "Connect", variant="primary", id="cred-save")
                if self.kind == "git_host" and self.host == "github.com":
                    yield Button("Sign in with GitHub", id="cred-device-start")
                yield Button("Decline  esc", id="cred-decline")

    def on_mount(self) -> None:
        if self.kind == "connector" and self._oauth_ready():
            self.query_one("#conn-oauth", Button).focus()
            return
        first = self.query("Input").first() if self.query("Input") else None
        if first is not None:
            first.focus()

    def _error(self, text: str) -> None:
        self.query_one("#cred-error", Static).update(escape(text))

    @on(Input.Submitted)
    @on(Button.Pressed, "#cred-save")
    async def _save(self) -> None:
        try:
            if self.kind == "connector":
                await self._save_connector()
                return
            if self.kind == "site_login":
                values = {name: self.query_one(f"#cred-{name}", Input).value for name in self.names}
                required = [n for n in self.names if n != "totp"]
                if any(not values[n].strip() for n in required):
                    self._error("Fill every field before saving.")
                    return
                await asyncio.to_thread(self._save_site, self.host, values)
                for name in self.names:
                    self.query_one(f"#cred-{name}", Input).value = ""
                self.dismiss({"status": "saved", "login": ""})
                return
            token = self.query_one("#cred-token", Input).value.strip()
            if not token:
                self._error("Paste an access token, or sign in with GitHub.")
                return
            await asyncio.to_thread(self._save_token, self.host, token)
            self.query_one("#cred-token", Input).value = ""
            self.dismiss({"status": "connected", "login": ""})
        except Exception as exc:  # noqa: BLE001 - shown in the card, never the value
            self._error(str(getattr(exc, "message", None) or "Could not save. Try again."))

    async def _save_connector(self) -> None:
        if not self._key_fields:
            self._error("This app signs in with OAuth: use the sign-in button.")
            return
        values = {field["key"]: self.query_one(f"#conn-{field['key']}", Input).value for field in self._key_fields}
        missing = [str(field.get("label") or field["key"]) for field in self._key_fields
                   if field.get("required") and not values[field["key"]].strip()]
        if missing:
            self._error("Fill " + ", ".join(missing) + ".")
            return
        self.query_one("#cred-device", Static).update("Checking with the app...")
        try:
            result = await asyncio.to_thread(self._connect, self.host, self._key_type, values)
        finally:
            self.query_one("#cred-device", Static).update("")
        for field in self._key_fields:
            if field.get("secret"):
                self.query_one(f"#conn-{field['key']}", Input).value = ""
        account = str(((result or {}).get("status") or {}).get("account") or "")
        self.dismiss({"status": "connected", "login": account})

    @on(Button.Pressed, "#conn-oauth")
    def _oauth(self) -> None:
        self._device_run += 1
        self._run_oauth(self._device_run)

    @work(exclusive=True, group="cred-device")
    async def _run_oauth(self, run: int) -> None:
        from navin.connectors.catalog import ConnectorError

        status = self.query_one("#cred-device", Static)
        try:
            url = await asyncio.to_thread(self._oauth_start, self.host)
        except (ConnectorError, OSError) as exc:
            self._error(str(getattr(exc, "message", None) or "The sign-in could not start."))
            return
        opened = await asyncio.to_thread(self._browser, url)
        status.update(
            "Finish signing in in your browser. Waiting..."
            if opened
            else f"Open this address to sign in: {escape(url)}"
        )
        deadline = asyncio.get_running_loop().time() + 600
        while run == self._device_run and self.is_attached and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self._poll_interval)
            try:
                linked, account = await asyncio.to_thread(self._oauth_status, self.host)
            except (ConnectorError, OSError) as exc:
                # A locked vault does not end the sign-in: say so and keep waiting.
                status.update(escape(str(getattr(exc, "message", None) or "Waiting for the connection...")))
                continue
            if linked:
                self.dismiss({"status": "connected", "login": account})
                return
        if run == self._device_run and self.is_attached:
            status.update("")
            self._error("The sign-in did not finish in time. Try again.")

    @on(Button.Pressed, "#cred-device-start")
    def _device(self) -> None:
        self._device_run += 1
        self._run_device(self._device_run)

    @work(exclusive=True, group="cred-device")
    async def _run_device(self, run: int) -> None:
        status = self.query_one("#cred-device", Static)
        try:
            flow = await asyncio.to_thread(self._device_start, self.host)
        except Exception as exc:  # noqa: BLE001
            self._error(str(getattr(exc, "message", None) or "GitHub sign-in is not available."))
            return
        status.update(
            f"Enter code [b]{escape(str(flow.get('user_code') or ''))}[/b] at "
            f"{escape(str(flow.get('verification_uri') or 'github.com/login/device'))}. "
            "Waiting for approval..."
        )
        interval = max(1, int(flow.get("interval") or 5))
        while run == self._device_run and self.is_attached:
            await asyncio.sleep(interval)
            try:
                result = await asyncio.to_thread(self._device_poll, str(flow.get("flow_id") or ""))
            except Exception:  # noqa: BLE001
                continue
            state = result.get("status")
            if state == "ok":
                self.dismiss({"status": "connected", "login": str(result.get("login") or "")})
                return
            if state == "slow_down":
                interval += 5
            elif state in {"expired", "denied", "error"}:
                status.update("")
                self._error("GitHub sign-in did not complete. Try again or paste a token.")
                return

    @on(Button.Pressed, "#cred-decline")
    def action_decline(self) -> None:
        self._device_run += 1
        self.dismiss({"status": "declined", "login": ""})


async def handle_credential_event(app: Any, event: Any) -> bool:
    """Open or close the credential modal for a runtime event. True if handled."""
    from navin.tui.runtime import UiCredentialClosed, UiCredentialRequested

    open_screens: dict[str, CredentialScreen] | None = getattr(app, "_credential_screens", None)
    if open_screens is None:
        open_screens = {}
        app._credential_screens = open_screens
    if isinstance(event, UiCredentialRequested):
        request_id = event.request_id
        if request_id in open_screens:
            return True  # already open: shown again when its session is reopened
        form: dict[str, Any] | None = None
        if event.kind == "connector":
            from navin.connectors.catalog import ConnectorError
            from navin.connectors.manager import terminal_form

            try:
                form = await asyncio.to_thread(terminal_form, event.host)
            except (ConnectorError, OSError):
                # Unknown app or unreadable vault: the card still opens and offers Decline.
                form = {"service": event.host, "name": event.host, "key_method": None, "oauth": None}
        screen = CredentialScreen(event.kind, event.host, list(event.names), event.reason, connector_form=form)
        open_screens[request_id] = screen

        async def _answer(result: dict[str, str] | None) -> None:
            if open_screens.pop(request_id, None) is None:
                return  # closed by the engine (timeout, stop): nothing to send
            answer = result or {"status": "declined"}
            await app.runtime.answer_credential(
                request_id, status=answer.get("status", "declined"), login=answer.get("login", "")
            )

        await app.push_screen(screen, _answer)
        return True
    if isinstance(event, UiCredentialClosed):
        screen = open_screens.pop(event.request_id, None)
        if screen is not None and screen.is_attached:
            # Not the top screen any more: leave it, its answer has nowhere to go.
            with contextlib.suppress(Exception):
                screen.dismiss({"status": event.status})
        return True
    return False
