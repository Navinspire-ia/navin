# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""request_credentials: a secure card, refs back, never a secret."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from navin.agent import credentials as cred
from navin.agent.credentials import (
    CredentialAnswer,
    CredentialBroker,
    CredentialRequest,
    bind_credential_broker,
    handle_credential_answer,
    request_credentials,
    reset_credential_broker,
)
from navin.agent.tools.base import ToolResult
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.agent.tools.credentials import RequestCredentialsTool
from navin.bus.events import INBOUND_META_RUNTIME_CONTROL, RUNTIME_CONTROL_CREDENTIAL_ANSWER

SECRET = "hunter2-very-secret"


class _Vault:
    def __init__(self, refs: set[str] | None = None) -> None:
        self.refs = set(refs or ())

    def has(self, ref: str) -> bool:
        return ref in self.refs


class _Base(unittest.IsolatedAsyncioTestCase):
    metadata: dict[str, Any] = {}
    channel = "websocket"

    def setUp(self) -> None:
        self.published: list[tuple[str, CredentialRequest, dict[str, Any]]] = []
        self.closed: list[tuple[str, CredentialAnswer]] = []
        self.vault = _Vault()
        self.git_connected: tuple[bool, str] = (False, "")
        self._ctx = bind_request_context(
            RequestContext(
                channel=self.channel,
                chat_id="c1",
                session_key=f"{self.channel}:c1",
                metadata=dict(self.metadata),
            )
        )
        self._patches = [
            patch.object(cred, "_site_has", lambda ref: self.vault.has(ref)),
            patch.object(cred, "_git_host_status", lambda host: self.git_connected),
        ]
        for p in self._patches:
            p.start()
        self.broker = self._broker()
        self._bind = bind_credential_broker(self.broker)

    def tearDown(self) -> None:
        reset_credential_broker(self._bind)
        for p in self._patches:
            p.stop()
        reset_request_context(self._ctx)

    def _broker(self, **kwargs: Any) -> CredentialBroker:
        async def publish(request_id: str, request: CredentialRequest, route: dict[str, Any]) -> None:
            self.published.append((request_id, request, route))

        async def close(request_id: str, answer: CredentialAnswer, route: dict[str, Any]) -> None:
            self.closed.append((request_id, answer))

        return CredentialBroker(publish=publish, close=close, **kwargs)

    async def _answer_when_open(self, status: str, *, login: str = "", save: set[str] | None = None) -> None:
        for _ in range(200):
            if self.published:
                break
            await asyncio.sleep(0.001)
        request_id = self.published[-1][0]
        if save:
            self.vault.refs |= save
        self.broker.resolve(request_id, status=status, login=login)


class BrokerTest(_Base):
    async def test_event_is_published_and_saved_refs_come_back(self) -> None:
        req = CredentialRequest("site_login", "app.example.com", ("username", "password"), "Log in to export")
        refs = {"site:app.example.com/username", "site:app.example.com/password"}
        answer, _ = await asyncio.gather(
            request_credentials(req), self._answer_when_open("saved", save=refs)
        )
        self.assertEqual(answer.status, "saved")
        _rid, published, route = self.published[0]
        self.assertEqual(published.kind, "site_login")
        self.assertEqual(published.reason, "Log in to export")
        self.assertIn("expires_at_ms", route)
        text = answer.as_text()
        self.assertIn("site:app.example.com/username", text)
        self.assertIn("site:app.example.com/password", text)
        self.assertNotIn(SECRET, text)
        self.assertEqual(self.closed[0][1].status, "saved")

    async def test_ui_claim_without_vault_values_is_not_saved(self) -> None:
        req = CredentialRequest("site_login", "app.example.com", ("password",), "why")
        answer, _ = await asyncio.gather(request_credentials(req), self._answer_when_open("saved"))
        self.assertEqual(answer.status, "not_saved")
        self.assertFalse(answer.ok)

    async def test_decline(self) -> None:
        req = CredentialRequest("git_host", "github.com", (), "push the branch")
        answer, _ = await asyncio.gather(request_credentials(req), self._answer_when_open("declined"))
        self.assertEqual(answer.status, "declined")
        self.assertIn("declined", answer.as_text())

    async def test_timeout(self) -> None:
        self.broker._timeout_s = 0.01
        answer = await request_credentials(CredentialRequest("git_host", "github.com", (), "why"))
        self.assertEqual(answer.status, "timed_out")
        self.assertEqual(self.closed[0][1].status, "timed_out")

    async def test_git_connected_reports_the_login(self) -> None:
        self.git_connected = (True, "octocat")
        answer, _ = await asyncio.gather(
            request_credentials(CredentialRequest("git_host", "github.com", (), "why")),
            self._answer_when_open("connected", login="ignored"),
        )
        self.assertTrue(answer.as_text().startswith("Connected github.com as octocat."))

    async def test_asked_once_per_turn_per_host(self) -> None:
        req = CredentialRequest("git_host", "github.com", (), "why")
        first, _ = await asyncio.gather(request_credentials(req), self._answer_when_open("declined"))
        second = await request_credentials(req)
        self.assertEqual(len(self.published), 1)
        self.assertEqual(second.status, "declined")
        self.assertTrue(second.repeated)
        self.assertEqual(first.status, "declined")

    async def test_answer_rides_the_runtime_control_route(self) -> None:
        task = asyncio.create_task(
            request_credentials(CredentialRequest("git_host", "github.com", (), "why"))
        )
        while not self.published:
            await asyncio.sleep(0.001)
        msg = SimpleNamespace(
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CREDENTIAL_ANSWER,
                "request_id": self.published[0][0],
                "status": "declined",
            }
        )
        self.assertTrue(await handle_credential_answer(SimpleNamespace(credentials=self.broker), msg, None))
        self.assertEqual((await task).status, "declined")

    async def test_unknown_ui_status_is_a_decline(self) -> None:
        req = CredentialRequest("git_host", "github.com", (), "why")
        answer, _ = await asyncio.gather(request_credentials(req), self._answer_when_open("hacked"))
        self.assertEqual(answer.status, "declined")

    async def test_tool_returns_refs_only(self) -> None:
        refs = {"site:app.example.com/username", "site:app.example.com/password"}
        result, _ = await asyncio.gather(
            RequestCredentialsTool().execute(
                kind="site_login", host="https://App.Example.com/login", reason="Export the report"
            ),
            self._answer_when_open("saved", save=refs),
        )
        self.assertIn("The user saved site:app.example.com/username", str(result))
        self.assertEqual(self.published[0][1].host, "app.example.com")

    async def test_tool_validates_input(self) -> None:
        tool = RequestCredentialsTool()
        self.assertTrue((await tool.execute(kind="password", host="x.com", reason="r")).is_error)
        self.assertTrue((await tool.execute(kind="site_login", host="", reason="r")).is_error)
        self.assertTrue((await tool.execute(kind="git_host", host="github.com", reason="")).is_error)
        self.assertEqual(self.published, [])


class UnattendedTest(_Base):
    async def test_no_broker_bound(self) -> None:
        reset_credential_broker(self._bind)
        self._bind = bind_credential_broker(None)
        answer = await request_credentials(CredentialRequest("git_host", "github.com", (), "why"))
        self.assertEqual(answer.status, "unattended")
        self.assertIn("Settings > Git & GitHub", answer.as_text())

    async def test_cron_and_subagent_turns_never_ask(self) -> None:
        for metadata in ({"subagent": True}, {"heartbeat": True}, {"_cron_trigger": {"job_id": "j"}}):
            token = bind_request_context(
                RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1", metadata=metadata)
            )
            try:
                self.assertFalse(cred.credentials_interactive())
                answer = await request_credentials(CredentialRequest("git_host", "github.com", (), "why"))
            finally:
                reset_request_context(token)
            self.assertEqual(answer.status, "unattended", metadata)
        self.assertEqual(self.published, [])


class NonAnsweringChannelTest(_Base):
    channel = "telegram"

    async def test_channel_without_cards_is_unattended(self) -> None:
        answer = await request_credentials(CredentialRequest("git_host", "github.com", (), "why"))
        self.assertEqual(answer.status, "unattended")
        self.assertEqual(self.published, [])


class BrowserAutoTriggerTest(_Base):
    async def test_missing_ref_asks_then_continues(self) -> None:
        from navin.agent.tools.browser import _ask_for_missing_refs

        refs = ["site:app.example.com/username", "site:app.example.com/password"]
        result, _ = await asyncio.gather(
            _ask_for_missing_refs(self.vault, refs, "app.example.com"),
            self._answer_when_open("saved", save=set(refs)),
        )
        self.assertIsNone(result)
        self.assertEqual(self.published[0][1].names, ("username", "password"))
        self.assertTrue(self.published[0][1].auto)

    async def test_saved_refs_never_ask(self) -> None:
        from navin.agent.tools.browser import _ask_for_missing_refs

        self.vault.refs = {"site:app.example.com/password"}
        self.assertIsNone(await _ask_for_missing_refs(self.vault, ["site:app.example.com/password"], ""))
        self.assertEqual(self.published, [])

    async def test_decline_returns_hint_and_does_not_ask_twice(self) -> None:
        from navin.agent.tools.browser import _ask_for_missing_refs

        refs = ["site:app.example.com/password"]
        result, _ = await asyncio.gather(
            _ask_for_missing_refs(self.vault, refs, ""), self._answer_when_open("declined")
        )
        self.assertTrue(result.is_error)
        self.assertIn("declined", str(result))
        again = await _ask_for_missing_refs(self.vault, refs, "")
        self.assertTrue(again.is_error)
        self.assertEqual(len(self.published), 1)

    async def test_unattended_returns_settings_hint(self) -> None:
        from navin.agent.tools.browser import _ask_for_missing_refs

        token = bind_request_context(
            RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1", metadata={"subagent": True})
        )
        try:
            result = await _ask_for_missing_refs(self.vault, ["site:app.example.com/password"], "")
        finally:
            reset_request_context(token)
        self.assertIn("Settings > Sites & logins", str(result))
        self.assertNotIn("request_credentials", str(result))
        self.assertEqual(self.published, [])


_AUTH_FAIL = ToolResult.error(
    "Error: remote: Invalid username or password.\n"
    "fatal: Authentication failed for 'https://github.com/acme/app.git/'"
)


class GitAutoTriggerTest(_Base):
    def _tool(self, results: list[Any]) -> Any:
        from navin.agent.tools.git import GitTool

        tool = GitTool.__new__(GitTool)
        tool._forge_tokens = {}
        calls: list[dict[str, Any]] = []

        async def fake_bound(**call: Any) -> Any:
            calls.append(call)
            return results.pop(0)

        tool._execute_bound = fake_bound  # type: ignore[method-assign]
        tool.calls = calls  # type: ignore[attr-defined]
        return tool

    async def test_auth_failure_asks_and_retries_once(self) -> None:
        self.git_connected = (True, "octocat")
        tool = self._tool([_AUTH_FAIL, "pushed main"])
        with patch("navin.agent.tools.git._reload_forge_tokens", return_value={"github.com": "t"}):
            result, _ = await asyncio.gather(
                tool.execute(action="push"), self._answer_when_open("connected")
            )
        self.assertEqual(str(result), "pushed main")
        self.assertEqual(len(tool.calls), 2)
        self.assertEqual(self.published[0][1].host, "github.com")
        self.assertEqual(tool._forge_tokens, {"github.com": "t"})

    async def test_decline_keeps_the_error_and_no_retry(self) -> None:
        tool = self._tool([_AUTH_FAIL])
        result, _ = await asyncio.gather(tool.execute(action="fetch"), self._answer_when_open("declined"))
        self.assertTrue(result.is_error)
        self.assertIn("declined", str(result))
        self.assertEqual(len(tool.calls), 1)

    async def test_success_and_local_actions_never_ask(self) -> None:
        tool = self._tool(["ok", _AUTH_FAIL])
        self.assertEqual(str(await tool.execute(action="push")), "ok")
        await tool.execute(action="status")
        self.assertEqual(self.published, [])

    async def test_unattended_returns_hint_without_asking(self) -> None:
        token = bind_request_context(
            RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1", metadata={"_cron_trigger": {"job_id": "j"}})
        )
        try:
            tool = self._tool([_AUTH_FAIL])
            result = await tool.execute(action="push")
        finally:
            reset_request_context(token)
        self.assertTrue(result.is_error)
        self.assertEqual(self.published, [])
        self.assertEqual(len(tool.calls), 1)


class ForgeAutoTriggerTest(_Base):
    async def test_missing_token_asks_then_resolves(self) -> None:
        from navin.agent.tools.forge import ForgeTool
        from navin.webui.forge_api import ForgeError

        tool = ForgeTool.__new__(ForgeTool)
        self.git_connected = (True, "octocat")
        exc = ForgeError("No API token for github.com.", status=401, code="forge_token_missing", host="github.com")
        with patch.object(ForgeTool, "_resolve", lambda self, root: ("remote", "tok")):
            (remote, token, error), _ = await asyncio.gather(
                tool._after_auth_error(exc, Path(".")), self._answer_when_open("connected")
            )
        self.assertIsNone(error)
        self.assertEqual((remote, token), ("remote", "tok"))

    async def test_other_errors_do_not_ask(self) -> None:
        from navin.agent.tools.forge import ForgeTool
        from navin.webui.forge_api import ForgeError

        tool = ForgeTool.__new__(ForgeTool)
        exc = ForgeError("not found", code="forge_repo_not_found", host="github.com")
        _r, _t, error = await tool._after_auth_error(exc, Path("."))
        self.assertTrue(error.is_error)
        self.assertEqual(self.published, [])


class WebsocketContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_answer_frame_drops_everything_but_status_and_login(self) -> None:
        from navin.channels.websocket import WebSocketChannel

        published: list[Any] = []

        async def publish_inbound(msg: Any) -> None:
            published.append(msg)

        async def send_event(*_: Any, **__: Any) -> None:
            raise AssertionError("valid frame rejected")

        fake = SimpleNamespace(bus=SimpleNamespace(publish_inbound=publish_inbound), _send_event=send_event)
        await WebSocketChannel._dispatch_credential_answer(
            fake,  # type: ignore[arg-type]
            "conn",
            {
                "type": "credential_answer",
                "request_id": "r1",
                "status": "saved",
                "password": SECRET,
                "values": {"password": SECRET},
            },
        )
        meta = published[0].metadata
        self.assertEqual(meta["status"], "saved")
        self.assertNotIn(SECRET, json.dumps({k: v for k, v in meta.items() if isinstance(v, str)}))
        self.assertNotIn(SECRET, published[0].content)

    async def test_request_frame_shape(self) -> None:
        from navin.bus.outbound_events import CredentialRequestedEvent
        from navin.channels.websocket import WebSocketChannel

        sent: list[str] = []

        async def safe_send(connection: Any, raw: str, label: str = "") -> None:
            sent.append(raw)

        fake = SimpleNamespace(_subs={"c1": {"conn"}}, _safe_send_to=safe_send)
        await WebSocketChannel.send_credential_request(
            fake,  # type: ignore[arg-type]
            "c1",
            CredentialRequestedEvent(
                request_id="r1", kind="site_login", host="app.example.com", names=["password"], reason="why"
            ),
        )
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "credential_request")
        self.assertEqual(body["names"], ["password"])


if __name__ == "__main__":
    unittest.main()
