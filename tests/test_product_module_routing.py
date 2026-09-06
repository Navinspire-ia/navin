"""Server-side dev routing: chat turn -> Code module, all the way to the shell."""

from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path

from navin.bus.outbound_events import ProductModuleRequestedEvent
from navin.command.modules import PRODUCT_MODULE_METADATA_KEY

WEBUI = Path(__file__).resolve().parents[1] / "webui" / "src"
APP = WEBUI / "App.tsx"
CLIENT = WEBUI / "lib" / "navin-client.ts"
WEBSOCKET = Path(__file__).resolve().parents[1] / "navin" / "channels" / "websocket.py"


class _FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []


class _Channel:
    """Minimal stand-in exercising the real send_product_module_request body."""

    def __init__(self, connection: _FakeConnection, chat_id: str) -> None:
        self._subs = {chat_id: [connection]}
        self._connection = connection

    async def _safe_send_to(self, connection, raw, label=""):  # noqa: ANN001
        connection.sent.append(raw)

    send_product_module_request = None  # bound below


def _bound_sender():
    from navin.channels.websocket import WebSocketChannel

    return WebSocketChannel.send_product_module_request


class WebsocketTransportTest(unittest.TestCase):
    def _send(self, module: str, chat_id: str = "chat-1"):
        conn = _FakeConnection()
        channel = _Channel(conn, chat_id)
        asyncio.run(
            _bound_sender()(channel, chat_id, ProductModuleRequestedEvent(module=module))
        )
        return conn.sent

    def test_a_known_module_reaches_the_client(self) -> None:
        sent = self._send("code")
        self.assertEqual(len(sent), 1)
        self.assertIn('"event": "product_module_request"', sent[0])
        self.assertIn('"module": "code"', sent[0])

    def test_the_dev_view_alias_is_normalized_to_its_module(self) -> None:
        sent = self._send("dev")
        self.assertIn('"module": "code"', sent[0])

    def test_an_unknown_module_is_dropped(self) -> None:
        self.assertEqual(self._send("banana"), [])
        self.assertEqual(self._send(""), [])

    def test_no_subscriber_is_not_an_error(self) -> None:
        conn = _FakeConnection()
        channel = _Channel(conn, "chat-1")
        asyncio.run(
            _bound_sender()(channel, "other-chat", ProductModuleRequestedEvent(module="code"))
        )
        self.assertEqual(conn.sent, [])


class ComposerModeTransportTest(unittest.TestCase):
    """The montage mode used to be dropped in transit; it must reach the client."""

    def _send(self, mode: str):
        from navin.bus.outbound_events import ComposerModeRequestedEvent
        from navin.channels.websocket import WebSocketChannel

        conn = _FakeConnection()
        channel = _Channel(conn, "chat-1")
        asyncio.run(
            WebSocketChannel.send_composer_mode_request(
                channel, "chat-1", ComposerModeRequestedEvent(mode=mode)
            )
        )
        return conn.sent

    def test_montage_is_forwarded(self) -> None:
        sent = self._send("montage")
        self.assertEqual(len(sent), 1, "the /montage brief could not tint the composer")
        self.assertIn('"mode": "montage"', sent[0])

    def test_ask_is_forwarded(self) -> None:
        self.assertEqual(len(self._send("ask")), 1)

    def test_a_bogus_mode_is_still_refused(self) -> None:
        self.assertEqual(self._send("banana"), [])


class LoopEmissionTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, text, *, channel="websocket", metadata=None, original=...):
        from navin.agent.loop import AgentLoop, TurnContext, TurnState
        from navin.bus.events import InboundMessage

        published: list = []

        class _Bus:
            async def publish_outbound(self, message):
                published.append(message)

        msg = InboundMessage(
            channel=channel,
            chat_id="chat-1",
            sender_id="u",
            content=text,
            metadata=dict(metadata or {}),
        )
        ctx = TurnContext(
            msg=msg,
            session_key="websocket:chat-1",
            state=TurnState.RESTORE,
            turn_id="t1",
            runtime=None,
            original_user_text=text if original is ... else original,
        )
        loop = object.__new__(AgentLoop)
        loop.bus = _Bus()
        await AgentLoop._maybe_request_product_module(loop, ctx)
        return published, ctx

    async def test_a_dev_request_does_not_yank_tchat_onto_code(self) -> None:
        """New chat is Tchat. A coding verb is not a reason to leave it."""
        published, ctx = await self._run("corrige le bug du login")
        self.assertEqual(published, [])
        self.assertNotIn(PRODUCT_MODULE_METADATA_KEY, ctx.msg.metadata)

    async def test_a_plain_question_changes_nothing(self) -> None:
        published, ctx = await self._run("bonjour, tu vas bien ?")
        self.assertEqual(published, [])
        self.assertNotIn(PRODUCT_MODULE_METADATA_KEY, ctx.msg.metadata)

    async def test_ask_mode_never_moves_the_user(self) -> None:
        published, _ = await self._run(
            "corrige le bug du login", metadata={"composer_mode": "ask"}
        )
        self.assertEqual(published, [])

    async def test_montage_keeps_its_desk(self) -> None:
        published, _ = await self._run(
            "corrige le bug du login", metadata={"composer_mode": "montage"}
        )
        self.assertEqual(published, [])

    async def test_an_already_scoped_shell_is_left_alone(self) -> None:
        published, _ = await self._run(
            "corrige le bug du login",
            metadata={PRODUCT_MODULE_METADATA_KEY: "code"},
        )
        self.assertEqual(published, [])

    async def test_other_channels_are_untouched(self) -> None:
        for channel in ("telegram", "cli", "slack"):
            published, _ = await self._run("corrige le bug du login", channel=channel)
            self.assertEqual(published, [], channel)

    async def test_an_internal_continuation_does_not_re_navigate(self) -> None:
        # Mid-turn continuations carry no user text and must not bounce the view.
        published, _ = await self._run("corrige le bug", original=None)
        self.assertEqual(published, [])


class ShellWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = APP.read_text(encoding="utf-8")
        self.client = CLIENT.read_text(encoding="utf-8")

    def test_the_client_parses_and_validates_the_frame(self) -> None:
        self.assertIn('parsed.event === "product_module_request"', self.client)
        self.assertIn("onProductModuleRequest", self.client)
        self.assertIn("PRODUCT_MODULES as readonly string[]", self.client)

    def test_the_shell_subscribes_and_navigates(self) -> None:
        match = re.search(
            r"client\.onProductModuleRequest\(\(request\) => \{(.*?)\n    \}\);",
            self.app,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "App does not react to the module request")
        body = match.group(1)
        self.assertIn("VIEW_BY_PRODUCT_MODULE[request.module]", body)
        self.assertIn(
            "if (isWorkbenchView(view)) return;",
            body,
            "a deliberately opened workbench must not be replaced",
        )
        self.assertIn(
            'if (view === "chat") return;',
            body,
            "Tchat / new chat must stay in Tchat",
        )

    def test_the_code_module_maps_to_the_dev_view(self) -> None:
        self.assertRegex(self.app, r"VIEW_BY_PRODUCT_MODULE[^=]*=\s*\{[^}]*code: \"dev\"")


class WebsocketFilterTest(unittest.TestCase):
    def test_the_composer_mode_allowlist_lists_montage(self) -> None:
        source = WEBSOCKET.read_text(encoding="utf-8")
        match = re.search(r"if mode not in \{([^}]*)\}", source)
        self.assertIsNotNone(match)
        self.assertIn("montage", match.group(1))


if __name__ == "__main__":
    unittest.main()
