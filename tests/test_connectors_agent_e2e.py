# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The agent connects an app it needs, then uses it: journal and CLI widget.

User ask: when the agent thinks a task needs an app, it opens that app's
connection card itself, in the web / desktop journal and in the CLI widget,
then calls the app. These tests drive a real AgentLoop (scripted model, real
bus, real tools and credential broker) end to end:

1. the model calls a tool, the broker publishes the connector card;
2. the card stores the key the way each UI does (the connectors HTTP action of
   the web form, the terminal form of the CLI), checked with the app first;
3. only a status travels back, and the engine checks the vault itself;
4. the next call reaches the app with the key injected by Navin, never shown
   to the model nor sent back to the chat.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from navin.agent.loop import AgentLoop
from navin.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_CREDENTIAL_ANSWER,
    InboundMessage,
)
from navin.bus.outbound_events import CredentialClosedEvent, CredentialRequestedEvent
from navin.bus.queue import MessageBus
from navin.config.loader import get_config_path, set_config_path
from navin.config.schema import Config
from navin.connectors import manager, runtime
from navin.connectors import store as store_module
from navin.connectors.store import ConnectorStore
from navin.providers.base import LLMProvider, LLMResponse, ToolCallRequest

SECRET = "tvly-SECRET-e2e-0001"
FOUND = "Navin agent, the first result"


class FakeVault:
    """No OS keyring in tests: the store keeps secrets in its encrypted file."""

    def ready(self) -> bool:
        return False

    def get(self, account: str) -> dict[str, Any]:
        return {}

    def put(self, account: str, tokens: dict[str, Any]) -> None:
        raise AssertionError("the keyring is off in this test")

    def delete(self, account: str) -> None:
        return None


class TavilyApp:
    """httpx.MockTransport handler standing in for api.tavily.com."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "api.tavily.com":
            return httpx.Response(404, json={"error": f"unexpected host {request.url.host}"})
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return httpx.Response(401, json={"detail": {"error": "Unauthorized: missing or invalid API key."}})
        if request.url.path == "/usage":
            return httpx.Response(200, json={"key": {"usage": 3, "limit": 1000}})
        if request.url.path == "/search" and request.method == "POST":
            asked = json.loads(request.content or b"{}").get("query")
            return httpx.Response(200, json={"query": asked, "results": [{"title": FOUND, "url": "https://navin.example"}]})
        return httpx.Response(404, json={"error": "unknown path"})

    def searches(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path == "/search"]


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__()
        self.responses = deque(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "test-connectors-e2e"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(list(kwargs.get("messages") or []))
        if not self.responses:
            return LLMResponse(content="(no more scripted answers)")
        return self.responses.popleft()

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat(**kwargs)


def _tool_call(call_id: str, name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)],
    )


def _tool_results(messages: list[dict[str, Any]]) -> list[str]:
    return [str(message.get("content") or "") for message in messages if message.get("role") == "tool"]


async def _wait_for(predicate: Any, what: str, timeout: float = 30.0) -> None:
    try:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.02)
    except TimeoutError as exc:
        raise AssertionError(f"timed out waiting for {what}") from exc


@pytest.fixture()
def tavily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ConnectorStore, TavilyApp]:
    """An isolated config, a connector store without keyring, and a fake Tavily."""
    old = get_config_path()
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    set_config_path(config)
    store = ConnectorStore(tmp_path / "connectors", vault=FakeVault(), config_path=config)
    from navin.webui import connectors_api

    for module in (store_module, runtime, manager, connectors_api):
        monkeypatch.setattr(module, "ConnectorStore", lambda *args, **kwargs: store)
    app = TavilyApp()
    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(app))
    runtime.clear_token_cache()
    try:
        with patch("navin.agent.skills._home_skill_dirs", return_value=[]):
            yield store, app
    finally:
        set_config_path(old)


def _assert_never_leaked(provider: ScriptedProvider, *texts: str) -> None:
    sent = json.dumps(provider.calls, ensure_ascii=False, default=str)
    assert SECRET not in sent, "the key reached the model"
    for text in texts:
        assert SECRET not in text, "the key reached the chat"


def test_the_journal_card_connects_the_app_then_the_agent_calls_it(
    tmp_path: Path, tavily: tuple[ConnectorStore, TavilyApp]
) -> None:
    """Web and desktop journal: request_credentials kind=connector, then connectors request."""
    store, app = tavily
    from navin.webui.connectors_api import handle_connectors_action

    async def run() -> None:
        provider = ScriptedProvider([
            _tool_call("c1", "request_credentials", kind="connector", host="Tavily",
                       reason="Chercher sur le web avec Tavily"),
            _tool_call("c2", "connectors", action="request", service="tavily", method="POST",
                       path="/search", body={"query": "navin"}),
            LLMResponse(content=f"Done: {FOUND}."),
        ])
        workspace = tmp_path / "ws"
        workspace.mkdir()
        loop = AgentLoop(provider=provider, workspace=workspace, bus=MessageBus())
        loop._mcp_servers = {}
        outbound: list[Any] = []

        async def collect() -> None:
            while True:
                outbound.append(await loop.bus.consume_outbound())

        def events(kind: type) -> list[Any]:
            return [message.event for message in outbound if isinstance(getattr(message, "event", None), kind)]

        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(collect())
        try:
            await loop.bus.publish_inbound(InboundMessage(
                channel="websocket", sender_id="user", chat_id="c1",
                content="Cherche navin sur le web", metadata={"_wants_stream": False},
            ))
            await _wait_for(lambda: events(CredentialRequestedEvent), "the connector card")
            [card] = events(CredentialRequestedEvent)
            assert (card.kind, card.host, card.names) == ("connector", "tavily", [])
            assert card.reason == "Chercher sur le web avec Tavily"
            # No answer yet: the tool waits, the agent does not go on alone.
            await asyncio.sleep(0.2)
            assert len(provider.calls) == 1

            # The card's form saves through the connectors API (checked with the app first)...
            saved = await asyncio.to_thread(
                handle_connectors_action, "connect", {},
                {"service": "tavily", "method": "api_key", "values": {"apiKey": SECRET}}, store=store,
            )
            assert saved["status"]["connected"]
            # ... and only a status travels back on the socket, as WebSocketChannel sends it.
            await loop.bus.publish_inbound(InboundMessage(
                channel="system", sender_id="webui-credentials", chat_id="runtime",
                content=RUNTIME_CONTROL_CREDENTIAL_ANSWER,
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CREDENTIAL_ANSWER,
                    "request_id": card.request_id, "status": "connected", "login": "",
                },
            ))
            await _wait_for(lambda: events(CredentialClosedEvent), "the card to close")
            await _wait_for(
                lambda: any(f"Done: {FOUND}" in str(message.content) for message in outbound if message.chat_id == "c1"),
                "the final answer",
            )
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

        [closed] = events(CredentialClosedEvent)
        assert (closed.request_id, closed.status, closed.host) == (card.request_id, "connected", "tavily")
        assert len(provider.calls) == 3
        assert any("The user connected tavily" in text and "connectors action=request" in text
                   for text in _tool_results(provider.calls[1]))
        assert any(FOUND in text for text in _tool_results(provider.calls[2]))
        [search] = app.searches()
        assert search.method == "POST" and json.loads(search.content)["query"] == "navin"
        _assert_never_leaked(provider, *(str(message.content) for message in outbound))

    asyncio.run(run())


def test_the_journal_card_declined_lets_the_agent_go_on_without_the_app(
    tmp_path: Path, tavily: tuple[ConnectorStore, TavilyApp]
) -> None:
    """A declined card ends the wait; the agent is told not to ask again in this turn."""
    _store, app = tavily

    async def run() -> None:
        provider = ScriptedProvider([
            _tool_call("c1", "connectors", action="request", service="tavily", method="POST",
                       path="/search", body={"query": "navin"}, reason="Chercher navin avec Tavily"),
            _tool_call("c2", "connectors", action="request", service="tavily", method="POST",
                       path="/search", body={"query": "navin"}),
            LLMResponse(content="Tavily is not connected, I searched without it."),
        ])
        workspace = tmp_path / "ws"
        workspace.mkdir()
        loop = AgentLoop(provider=provider, workspace=workspace, bus=MessageBus())
        loop._mcp_servers = {}
        outbound: list[Any] = []

        async def collect() -> None:
            while True:
                outbound.append(await loop.bus.consume_outbound())

        def cards() -> list[Any]:
            return [message.event for message in outbound if isinstance(getattr(message, "event", None), CredentialRequestedEvent)]

        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(collect())
        try:
            await loop.bus.publish_inbound(InboundMessage(
                channel="websocket", sender_id="user", chat_id="c2",
                content="Cherche navin avec tavily", metadata={"_wants_stream": False},
            ))
            await _wait_for(cards, "the connector card")
            [card] = cards()
            assert (card.kind, card.host, card.reason) == ("connector", "tavily", "Chercher navin avec Tavily")
            await loop.bus.publish_inbound(InboundMessage(
                channel="system", sender_id="webui-credentials", chat_id="runtime",
                content=RUNTIME_CONTROL_CREDENTIAL_ANSWER,
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CREDENTIAL_ANSWER,
                    "request_id": card.request_id, "status": "declined", "login": "",
                },
            ))
            await _wait_for(
                lambda: any("searched without it" in str(message.content) for message in outbound if message.chat_id == "c2"),
                "the final answer",
            )
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

        first, second = _tool_results(provider.calls[1])[-1], _tool_results(provider.calls[2])[-1]
        assert first.startswith("Error:") and "declined" in first and "Do not ask again" in first
        # The same app in the same turn: no second card, the first answer comes back.
        assert len(cards()) == 1
        assert second.startswith("Error:") and "Already asked in this turn" in second
        assert app.searches() == []

    asyncio.run(run())


# -- CLI widget -----------------------------------------------------------------------


class NoopHook:
    """Stand-in for the usage and cron hooks."""

    accounts_for_usage = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def before_run(self, context: Any) -> None:
        pass

    async def after_run(self, context: Any) -> None:
        pass

    async def on_error(self, context: Any) -> None:
        pass

    async def on_finally(self, context: Any) -> None:
        pass


def test_the_cli_widget_connects_the_app_the_agent_asked_for_then_it_is_called(
    tmp_path: Path, tavily: tuple[ConnectorStore, TavilyApp]
) -> None:
    """CLI: the agent calls an app that is not connected; the terminal card opens on its own."""
    from textual.widgets import Button, Input

    from navin.providers.factory import ProviderSnapshot
    from navin.tui.app import NavinApp
    from navin.tui.credential_screen import CredentialScreen
    from navin.tui.prefs import TuiPrefs

    store, app_http = tavily

    async def run() -> None:
        provider = ScriptedProvider([
            _tool_call("c1", "connectors", action="request", service="tavily", method="POST",
                       path="/search", body={"query": "navin"}, reason="Chercher navin avec Tavily"),
            LLMResponse(content=f"From the terminal: {FOUND}."),
        ])
        config = Config.model_validate({
            "agents": {"defaults": {"workspace": str(tmp_path), "provider": "openai", "model": "test-connectors-e2e"}},
            "providers": {"openai": {"apiKey": "sk-test"}},
        })
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        tui = NavinApp(config, prefs=prefs)

        def fake_snapshot(*args: Any, **kwargs: Any) -> ProviderSnapshot:
            return ProviderSnapshot(provider, "test-connectors-e2e", 128_000, ("test-connectors-e2e",))

        with (
            patch("navin.providers.factory.build_provider_snapshot_allowing_unconfigured", fake_snapshot),
            patch("navin.providers.factory.load_provider_snapshot_allowing_unconfigured", fake_snapshot),
            patch("navin.agent.loop.AgentLoop._connect_mcp", new_callable=AsyncMock),
            patch("navin.optional_live.live_modules_available", return_value=False),
            patch("navin.tui.app.live_modules_available", return_value=False),
            patch("navin.cron.service.CronService"),
            patch("navin.cron.spend.CronSpendHook", NoopHook),
            patch("navin.webui.token_usage.TokenUsageHook", NoopHook),
            patch("navin.update.notice.update_notice", return_value=""),
        ):
            try:
                async with tui.run_test(size=(110, 40)) as pilot:
                    tui._engine_ready = True
                    await tui._render_history()
                    await tui.submit_text("Cherche navin avec tavily")

                    def card_ready() -> bool:
                        # The screen is pushed first and composed on the next frames.
                        return isinstance(tui.screen, CredentialScreen) and len(tui.screen.query("#conn-apiKey")) > 0

                    await _wait_for(card_ready, "the terminal card and its key field")
                    screen = tui.screen
                    assert (screen.kind, screen.host, screen.reason) == ("connector", "tavily", "Chercher navin avec Tavily")
                    key = screen.query_one("#conn-apiKey", Input)
                    assert key.password, "the key field must be masked"
                    key.value = SECRET
                    screen.query_one("#cred-save", Button).press()
                    await _wait_for(lambda: len(provider.calls) >= 2, "the call after the connection")
                    await _wait_for(lambda: not tui.runtime.turn_active, "the end of the turn")
                    await pilot.pause()
                    assert not isinstance(tui.screen, CredentialScreen)
            finally:
                if tui.runtime.agent_loop is not None:
                    tui.runtime.agent_loop.stop()
                for name in ("_loop_task", "_consumer_task"):
                    running = getattr(tui.runtime, name)
                    if running is not None:
                        running.cancel()
                await asyncio.gather(
                    tui.runtime._loop_task or asyncio.sleep(0),
                    tui.runtime._consumer_task or asyncio.sleep(0),
                    return_exceptions=True,
                )
                await tui.runtime.close()

        assert store.status("tavily")["connected"]
        assert any(FOUND in text for text in _tool_results(provider.calls[1]))
        [search] = app_http.searches()
        assert json.loads(search.content)["query"] == "navin"
        _assert_never_leaked(provider)

    asyncio.run(run())
