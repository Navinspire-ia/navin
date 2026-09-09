# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Multitask: dispatch queued composer prompts to parallel subagents.

Covers the runtime-control handler that the ws_http endpoint reaches through
the bus: argument plumbing into SubagentManager.spawn, the per-session
concurrency ceiling, and the guarantee that the ack future is always answered.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from navin.agent.runner import AgentRunResult
from navin.agent.subagent import (
    SubagentManager,
    handle_multitask_spawn,
    request_multitask_spawn,
    spawn_was_accepted,
)
from navin.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_MULTITASK_SPAWN,
)


class _FakeManager(SubagentManager):
    """SubagentManager whose spawn records its arguments and does no work."""

    def __init__(self, running: int = 0, limit: int = 20, reply: str | None = None) -> None:
        # No super().__init__: the handler only touches the members below.
        self.spawn_calls: list[dict[str, Any]] = []
        self._running = running
        self.max_concurrent_subagents = limit
        self._reply = reply or "Subagent [x] started (id: abc)."

    def get_running_count_by_session(self, session_key: str) -> int:
        return self._running

    async def spawn(self, **kwargs: Any) -> str:  # type: ignore[override]
        replayed = self.replay_client_spawn(
            kwargs.get("session_key"), kwargs.get("client_key")
        )
        if replayed:
            return replayed
        self.spawn_calls.append(kwargs)
        return self._reply


def _control_message(
    prompt: str,
    session_key: str,
    ack: asyncio.Future,
    client_key: str = "",
) -> Any:
    return SimpleNamespace(
        metadata={
            INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_MULTITASK_SPAWN,
            RUNTIME_CONTROL_ACK: ack,
            "session_key": session_key,
            "prompt": prompt,
            "label": "",
            "client_key": client_key,
        }
    )


def _state(manager: _FakeManager) -> Any:
    scope = SimpleNamespace(project_path="/tmp/proj")
    session = SimpleNamespace(metadata={})
    return SimpleNamespace(
        subagents=manager,
        sessions=SimpleNamespace(get_or_create=lambda key: session),
        workspace_scopes=SimpleNamespace(for_turn=lambda **kwargs: scope),
        llm_runtime=lambda: SimpleNamespace(model="test-model"),
    )


class MultitaskSpawnHttpRouteTest(unittest.IsolatedAsyncioTestCase):
    """The webui endpoint that carries the prompt in chunked base64 headers.

    Regression: file_body_from_headers returns decoded text (str). The route
    used to call raw.decode("utf-8") on it, so every Multitask click died in
    an AttributeError and the UI showed a generic gateway 500 banner.
    """

    def _handler(self) -> Any:
        from navin.webui.ws_http import GatewayHTTPHandler

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True
        handler.bus = object()
        return handler

    def _request(
        self,
        prompt: str = "corrige le bug",
        label: str = "fix",
        client_key: str = "qp-1",
    ) -> Any:
        import base64
        import json

        encoded = base64.b64encode(
            json.dumps({
                "prompt": prompt,
                "label": label,
                "client_key": client_key,
            }).encode("utf-8")
        ).decode("ascii")
        return SimpleNamespace(headers={"x-navin-file-body-0": encoded})

    async def test_route_parses_chunked_header_body(self) -> None:
        from unittest.mock import patch

        handler = self._handler()
        request = self._request()

        seen: dict[str, Any] = {}

        async def fake_spawn(
            bus: Any,
            key: str,
            prompt: str,
            *,
            label: str = "",
            client_key: str = "",
        ) -> dict:
            seen.update({
                "key": key,
                "prompt": prompt,
                "label": label,
                "client_key": client_key,
            })
            return {"ok": True, "detail": "Subagent started."}

        with patch("navin.agent.subagent.request_multitask_spawn", fake_spawn):
            response = await handler._handle_multitask_spawn(
                request, "websocket%3A12f50393-abc"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["key"], "websocket:12f50393-abc")
        self.assertEqual(seen["prompt"], "corrige le bug")
        self.assertEqual(seen["label"], "fix")
        self.assertEqual(seen["client_key"], "qp-1")

    async def test_route_returns_409_when_the_loop_refuses(self) -> None:
        from unittest.mock import patch

        async def fake_spawn(
            bus: Any, key: str, prompt: str, *, label: str = "", client_key: str = ""
        ) -> dict:
            return {"ok": False, "error": "Cannot spawn subagent: queue is full"}

        with patch("navin.agent.subagent.request_multitask_spawn", fake_spawn):
            response = await self._handler()._handle_multitask_spawn(
                self._request(), "websocket%3A12f50393-abc"
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"Cannot spawn", response.body)

    async def test_route_returns_504_when_the_loop_times_out(self) -> None:
        from unittest.mock import patch

        async def fake_spawn(
            bus: Any, key: str, prompt: str, *, label: str = "", client_key: str = ""
        ) -> dict:
            return {
                "ok": False,
                "error": "the agent loop did not answer in time",
                "timed_out": True,
            }

        with patch("navin.agent.subagent.request_multitask_spawn", fake_spawn):
            response = await self._handler()._handle_multitask_spawn(
                self._request(), "websocket%3A12f50393-abc"
            )
        self.assertEqual(response.status_code, 504)
        self.assertIn(b"did not answer in time", response.body)

    async def test_route_cuts_a_long_client_key(self) -> None:
        from unittest.mock import patch

        seen: dict[str, Any] = {}

        async def fake_spawn(
            bus: Any, key: str, prompt: str, *, label: str = "", client_key: str = ""
        ) -> dict:
            seen["client_key"] = client_key
            return {"ok": True, "detail": "Subagent started."}

        with patch("navin.agent.subagent.request_multitask_spawn", fake_spawn):
            response = await self._handler()._handle_multitask_spawn(
                self._request(client_key="k" * 200),
                "websocket%3A12f50393-abc",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["client_key"], "k" * 80)


class MultitaskSpawnRequestTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_marks_the_spawn_as_already_on_the_bus(self) -> None:
        class _SilentBus:
            async def publish_inbound(self, msg: Any) -> None:
                return None

        result = await request_multitask_spawn(
            _SilentBus(), "websocket:chat-42", "go", timeout=0.05
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertIn("did not answer in time", result["error"])

    async def test_empty_prompt_does_not_publish(self) -> None:
        class _CountingBus:
            def __init__(self) -> None:
                self.published = 0

            async def publish_inbound(self, msg: Any) -> None:
                self.published += 1

        bus = _CountingBus()
        result = await request_multitask_spawn(bus, "websocket:chat-42", "   ")
        self.assertFalse(result["ok"])
        self.assertEqual(bus.published, 0)

    async def test_client_key_is_published_on_the_control(self) -> None:
        published: dict[str, Any] = {}

        class _AckBus:
            async def publish_inbound(self, msg: Any) -> None:
                published.update(msg.metadata)
                ack = msg.metadata[RUNTIME_CONTROL_ACK]
                ack.set_result({"ok": True, "detail": "started"})

        result = await request_multitask_spawn(
            _AckBus(), "websocket:chat-42", "go", client_key="qp-1"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(published["client_key"], "qp-1")

    async def test_a_long_client_key_is_cut_to_eighty_chars(self) -> None:
        published: dict[str, Any] = {}

        class _AckBus:
            async def publish_inbound(self, msg: Any) -> None:
                published.update(msg.metadata)
                ack = msg.metadata[RUNTIME_CONTROL_ACK]
                ack.set_result({"ok": True})

        result = await request_multitask_spawn(
            _AckBus(),
            "websocket:chat-42",
            "go",
            client_key="k" * 200,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(published["client_key"], "k" * 80)


class MultitaskSpawnHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_ignores_other_control_messages(self) -> None:
        msg = SimpleNamespace(metadata={INBOUND_META_RUNTIME_CONTROL: "something_else"})
        handled = await handle_multitask_spawn(_state(_FakeManager()), msg, None)
        self.assertFalse(handled)

    async def test_spawns_with_session_routing(self) -> None:
        manager = _FakeManager()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("fix the login bug", "websocket:chat-42", ack)

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertTrue(result["ok"])
        self.assertEqual(len(manager.spawn_calls), 1)
        call = manager.spawn_calls[0]
        self.assertEqual(call["task"], "fix the login bug")
        self.assertEqual(call["origin_channel"], "websocket")
        self.assertEqual(call["origin_chat_id"], "chat-42")
        self.assertEqual(call["session_key"], "websocket:chat-42")
        self.assertIsNotNone(call["workspace_scope"])
        self.assertIsNotNone(call["runtime"])
        # Cursor-style isolation: each Multitask prompt runs in its own git
        # worktree; the manager falls back to the shared tree without git.
        self.assertTrue(call["isolate"])
        self.assertIsNone(call.get("client_key"))

    async def test_a_full_session_is_handed_over_not_refused(self) -> None:
        """Past the limit the manager queues, so the handler must still spawn.

        Refusing here meant a Multitask prompt sent to a busy conversation was
        simply lost, with nothing to retry against.
        """
        manager = _FakeManager(running=3, limit=3)
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("another task", "websocket:chat-42", ack)

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertTrue(result["ok"])
        self.assertEqual(len(manager.spawn_calls), 1)

    async def test_missing_prompt_answers_instead_of_hanging(self) -> None:
        manager = _FakeManager()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("   ", "websocket:chat-42", ack)

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertFalse(result["ok"])
        self.assertEqual(manager.spawn_calls, [])

    async def test_a_queue_wall_is_a_refusal_not_a_success(self) -> None:
        """The manager returns a 'Cannot spawn' string; Multitask must not
        answer ok and drop the composer prompt.
        """
        manager = _FakeManager(
            reply=(
                "Cannot spawn subagent: 3 are running for this conversation "
                "and 1000 more are already queued. Their results arrive on "
                "their own, so wait for them instead of spawning more."
            )
        )
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("one too many", "websocket:chat-42", ack)

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertFalse(result["ok"])
        self.assertIn("Cannot spawn", result["error"])
        self.assertEqual(len(manager.spawn_calls), 1)

    async def test_a_queued_spawn_is_still_success(self) -> None:
        manager = _FakeManager(reply="Subagent [x] queued (id: abc), position 1")
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("later", "websocket:chat-42", ack)

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertTrue(result["ok"])
        self.assertIn("queued", result["detail"])

    async def test_spawn_was_accepted_matches_the_manager_contract(self) -> None:
        self.assertTrue(spawn_was_accepted("Subagent [x] started (id: abc)."))
        self.assertTrue(spawn_was_accepted("Subagent [x] queued (id: abc), position 1"))
        self.assertFalse(spawn_was_accepted("Cannot spawn subagent: queue is full"))
        self.assertFalse(spawn_was_accepted(None))  # type: ignore[arg-type]

    async def test_spawn_failure_still_answers_the_ack(self) -> None:
        manager = _FakeManager()

        async def boom(**kwargs: Any) -> str:
            raise RuntimeError("provider exploded")

        manager.spawn = boom  # type: ignore[method-assign]
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("do things", "websocket:chat-42", ack)

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertFalse(result["ok"])
        self.assertIn("provider exploded", result["error"])

    async def test_client_key_is_handed_to_spawn(self) -> None:
        manager = _FakeManager()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("fix login", "websocket:chat-42", ack, client_key="qp-1")

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertTrue(result["ok"])
        self.assertEqual(manager.spawn_calls[0]["client_key"], "qp-1")

    async def test_the_same_client_key_does_not_spawn_twice(self) -> None:
        manager = _FakeManager()
        manager._client_keys = {}
        manager._task_client_slots = {}
        manager._client_key_details = {}
        manager._task_statuses = {"old": object()}
        manager.bind_client_spawn(
            "websocket:chat-42",
            "qp-1",
            "old",
            "Subagent [x] started (id: old).",
        )
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = _control_message("fix login", "websocket:chat-42", ack, client_key="qp-1")

        handled = await handle_multitask_spawn(_state(manager), msg, None)

        self.assertTrue(handled)
        result = await ack
        self.assertTrue(result["ok"])
        self.assertEqual(result["detail"], "Subagent [x] started (id: old).")
        self.assertEqual(manager.spawn_calls, [])


class MultitaskClientKeyTest(unittest.TestCase):
    def test_bind_replay_and_forget(self) -> None:
        manager = object.__new__(SubagentManager)
        manager._client_keys = {}
        manager._task_client_slots = {}
        manager._client_key_details = {}
        manager._task_statuses = {"t1": object()}
        manager.bind_client_spawn(
            "websocket:a", "qp-1", "t1", "Subagent [x] started (id: t1)."
        )
        self.assertEqual(
            manager.replay_client_spawn("websocket:a", "qp-1"),
            "Subagent [x] started (id: t1).",
        )
        self.assertIsNone(manager.replay_client_spawn("websocket:a", "other"))
        manager.forget_client_spawn("t1")
        self.assertIsNone(manager.replay_client_spawn("websocket:a", "qp-1"))

    def test_stale_key_after_task_gone_is_forgotten(self) -> None:
        manager = object.__new__(SubagentManager)
        manager._client_keys = {}
        manager._task_client_slots = {}
        manager._client_key_details = {}
        manager._task_statuses = {}
        manager.bind_client_spawn(
            "websocket:a", "qp-1", "gone", "Subagent [x] started (id: gone)."
        )
        self.assertIsNone(manager.replay_client_spawn("websocket:a", "qp-1"))

    def test_the_same_key_on_another_session_is_a_different_slot(self) -> None:
        manager = object.__new__(SubagentManager)
        manager._client_keys = {}
        manager._task_client_slots = {}
        manager._client_key_details = {}
        manager._task_statuses = {"t1": object(), "t2": object()}
        manager.bind_client_spawn("websocket:a", "qp-1", "t1", "first")
        manager.bind_client_spawn("websocket:b", "qp-1", "t2", "second")
        self.assertEqual(manager.replay_client_spawn("websocket:a", "qp-1"), "first")
        self.assertEqual(manager.replay_client_spawn("websocket:b", "qp-1"), "second")


class _GatedRunner:
    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = 0

    async def run(self, spec) -> AgentRunResult:
        self.started += 1
        await self.gate.wait()
        return AgentRunResult(final_content="done", messages=[])


class LiveClientKeySpawnTest(unittest.IsolatedAsyncioTestCase):
    """Idempotence on a real SubagentManager, not a fake that only records calls."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runner = _GatedRunner()
        self.manager = SubagentManager(
            workspace=Path(self._tmp.name),
            bus=SimpleNamespace(
                publish_inbound=self._noop_publish,
                outbound=SimpleNamespace(put_nowait=lambda msg: None),
            ),
            max_tool_result_chars=4000,
        )
        self.manager.runner = self.runner
        self.manager.max_concurrent_subagents = 1

    async def _noop_publish(self, msg: Any) -> None:
        return None

    async def asyncTearDown(self) -> None:
        watchdog = self.manager._watchdog_task
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        self.manager._queued.clear()
        leftover = list(self.manager._running_tasks.values())
        for task in leftover:
            task.cancel()
        if leftover:
            await asyncio.gather(*leftover, return_exceptions=True)

    async def _spawn(self, task: str, client_key: str, session_key: str = "websocket:chat") -> str:
        return await self.manager.spawn(
            task=task,
            runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
            origin_channel="websocket",
            origin_chat_id="chat",
            session_key=session_key,
            client_key=client_key,
        )

    async def _wait_started(self, expected: int) -> None:
        deadline = asyncio.get_running_loop().time() + 15.0
        while self.runner.started < expected:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"only {self.runner.started}/{expected} subagent(s) started")
            await asyncio.sleep(0.01)

    async def test_a_retry_with_the_same_key_does_not_start_a_second_agent(self) -> None:
        first = await self._spawn("fix login", "qp-1")
        self.assertTrue(spawn_was_accepted(first))
        await self._wait_started(1)
        second = await self._spawn("fix login", "qp-1")
        self.assertEqual(second, first)
        self.assertEqual(self.runner.started, 1)
        self.assertEqual(self.manager.get_running_count_by_session("websocket:chat"), 1)

    async def test_a_queued_retry_replays_instead_of_stacking(self) -> None:
        first = await self._spawn("one", "qp-a")
        self.assertIn("started", first)
        await self._wait_started(1)
        queued = await self._spawn("two", "qp-b")
        self.assertIn("queued", queued)
        replayed = await self._spawn("two again", "qp-b")
        self.assertEqual(replayed, queued)
        self.assertEqual(self.manager.queued_count("websocket:chat"), 1)

    async def test_cancel_forgets_the_key_so_a_later_retry_can_start(self) -> None:
        first = await self._spawn("fix login", "qp-1")
        self.assertTrue(spawn_was_accepted(first))
        await self._wait_started(1)
        await self.manager.cancel_by_session("websocket:chat")
        deadline = asyncio.get_running_loop().time() + 5.0
        while self.manager.replay_client_spawn("websocket:chat", "qp-1"):
            if asyncio.get_running_loop().time() > deadline:
                self.fail("client key still bound after cancel")
            await asyncio.sleep(0.01)
        retry = await self._spawn("fix login", "qp-1")
        self.assertTrue(spawn_was_accepted(retry))
        self.assertNotEqual(retry, first)
        await self._wait_started(2)


if __name__ == "__main__":
    unittest.main()
