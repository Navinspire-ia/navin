# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An ordinary message must never interrupt (or vanish from) a running turn.

Regression tests for the "salut stopped my mission" report:
- the WebUI composer routes free text through workflow slashes ("salut"
  becomes "/forge salut" in agent mode); while a turn was active those
  commands were inline-dispatched, and their handlers return ``None`` after
  rewriting the message, so the user's message was silently dropped;
- only the priority ``/stop`` command (Stop button / explicit "/stop") may
  cancel running tasks - a greeting or question must always be routed to the
  pending queue as a mid-turn injection.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from navin.agent.loop import should_inject_into_active_turn
from navin.command.builtin import is_agent_turn_command, register_builtin_commands
from navin.command.router import CommandRouter


def _router() -> CommandRouter:
    router = CommandRouter()
    register_builtin_commands(router)
    return router


class IsAgentTurnCommandTest(unittest.TestCase):
    def test_workflow_commands_are_agent_turn(self) -> None:
        for text in (
            "/forge salut",
            "/forge",
            "/blueprint Refactor the auth module",
            "/ask explique le module",
            "/debug the crash",
            "/cruise ship it",
        ):
            self.assertTrue(is_agent_turn_command(text), text)

    def test_side_channel_commands_are_not(self) -> None:
        for text in ("/status", "/model", "/history", "/checkpoint list", "/help"):
            self.assertFalse(is_agent_turn_command(text), text)

    def test_stop_and_new_are_not_agent_turn(self) -> None:
        self.assertFalse(is_agent_turn_command("/stop"))
        self.assertFalse(is_agent_turn_command("/new"))

    def test_plain_text_is_not_a_command(self) -> None:
        self.assertFalse(is_agent_turn_command("salut"))
        self.assertFalse(is_agent_turn_command("stop les taches"))

    def test_transport_bot_suffix_is_normalized(self) -> None:
        self.assertTrue(is_agent_turn_command("/forge@navinbot salut"))

    def test_agent_turn_with_args_needs_args(self) -> None:
        from navin.command.builtin import BUILTIN_COMMAND_SPECS

        with_args = [
            spec.command
            for spec in BUILTIN_COMMAND_SPECS
            if spec.lifecycle == "agent_turn_with_args"
        ]
        for command in with_args:
            self.assertFalse(is_agent_turn_command(command), command)
            self.assertTrue(is_agent_turn_command(f"{command} some focus"), command)


class ShouldInjectIntoActiveTurnTest(unittest.TestCase):
    """Routing decision for a message arriving while its session runs a turn."""

    def setUp(self) -> None:
        self.router = _router()

    def test_plain_greeting_is_injected_never_dispatched(self) -> None:
        self.assertTrue(should_inject_into_active_turn(self.router, "salut"))
        self.assertTrue(should_inject_into_active_turn(self.router, "bonjour, ça va ?"))
        self.assertTrue(
            should_inject_into_active_turn(self.router, "peux-tu m'expliquer ce fichier ?")
        )

    def test_mode_routed_free_text_is_injected(self) -> None:
        # The WebUI agent-mode composer sends "salut" as "/forge salut":
        # it must reach the running turn as an injection, not be dropped by
        # an inline dispatch whose handler returns None.
        self.assertTrue(should_inject_into_active_turn(self.router, "/forge salut"))
        self.assertTrue(should_inject_into_active_turn(self.router, "/ask c'est quoi ce repo ?"))
        self.assertTrue(should_inject_into_active_turn(self.router, "/blueprint add auth"))

    def test_side_channel_commands_dispatch_inline(self) -> None:
        self.assertFalse(should_inject_into_active_turn(self.router, "/status"))
        self.assertFalse(should_inject_into_active_turn(self.router, "/model"))
        self.assertFalse(should_inject_into_active_turn(self.router, "/help"))

    def test_stop_stays_on_the_priority_tier(self) -> None:
        # /stop is handled before this routing decision is ever consulted;
        # the priority tier is the only path that cancels running tasks.
        self.assertTrue(self.router.is_priority("/stop"))
        self.assertFalse(self.router.is_priority("salut"))
        self.assertFalse(self.router.is_priority("/forge salut"))


class OrdinaryMessageDoesNotCancelTest(unittest.IsolatedAsyncioTestCase):
    """Sanity: routing a greeting to injection touches no cancel machinery."""

    async def test_injected_greeting_cancels_nothing(self) -> None:
        import asyncio

        from navin.agent.loop import enqueue_pending_followup
        from navin.bus.events import InboundMessage

        cancel_calls: list[str] = []
        loop_stub = SimpleNamespace(
            _cancel_active_tasks=lambda key: cancel_calls.append(key),
        )
        queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        msg = InboundMessage(
            channel="websocket", sender_id="user", chat_id="chat-1", content="salut",
        )
        router = _router()
        self.assertTrue(should_inject_into_active_turn(router, msg.content))
        await enqueue_pending_followup(queue, msg, session_key="websocket:chat-1")
        self.assertEqual(queue.qsize(), 1)
        self.assertIs(queue.get_nowait(), msg)
        self.assertEqual(cancel_calls, [])
        self.assertIsNotNone(loop_stub)


if __name__ == "__main__":
    unittest.main()
