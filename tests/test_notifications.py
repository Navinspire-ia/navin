# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the notification channel: bus helper, agent tool, websocket frame."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from navin.agent.tools.notify import NotifyTool
from navin.bus.notify import (
    clear_notification_bus,
    notify,
    publish_notification,
    set_notification_bus,
)
from navin.bus.outbound_events import (
    NotificationEvent,
    outbound_event_from_message,
)


class _Queue:
    def __init__(self, *, full: bool = False) -> None:
        self.items: list[Any] = []
        self._full = full

    def put_nowait(self, item: Any) -> None:
        if self._full:
            raise asyncio.QueueFull
        self.items.append(item)


class _Bus:
    def __init__(self, *, full: bool = False) -> None:
        self.outbound = _Queue(full=full)


def _event(bus: _Bus) -> NotificationEvent:
    message = bus.outbound.items[-1]
    event = outbound_event_from_message(message)
    assert isinstance(event, NotificationEvent)
    return event


class PublishNotificationTest(unittest.TestCase):
    def test_queues_an_event_carrying_every_field(self):
        bus = _Bus()
        self.assertTrue(
            publish_notification(
                bus,
                title="Deploy needs approval",
                detail="Waiting since 14:02",
                level="warning",
                source="agent",
                key="deploy",
            )
        )
        event = _event(bus)
        self.assertEqual(event.title, "Deploy needs approval")
        self.assertEqual(event.detail, "Waiting since 14:02")
        self.assertEqual(event.level, "warning")
        self.assertEqual(event.source, "agent")
        self.assertEqual(event.key, "deploy")

    def test_reaches_every_connection_by_default(self):
        bus = _Bus()
        publish_notification(bus, title="x")
        self.assertEqual(bus.outbound.items[-1].chat_id, "*")

    def test_can_be_addressed_to_one_chat(self):
        bus = _Bus()
        publish_notification(bus, title="x", chat_id="chat-1")
        self.assertEqual(bus.outbound.items[-1].chat_id, "chat-1")

    def test_refuses_an_empty_title(self):
        bus = _Bus()
        self.assertFalse(publish_notification(bus, title="   "))
        self.assertEqual(bus.outbound.items, [])

    def test_falls_back_to_info_for_an_unknown_level(self):
        bus = _Bus()
        publish_notification(bus, title="x", level="catastrophe")
        self.assertEqual(_event(bus).level, "info")

    def test_blank_detail_and_key_become_absent(self):
        bus = _Bus()
        publish_notification(bus, title="x", detail="  ", key="  ")
        event = _event(bus)
        self.assertIsNone(event.detail)
        self.assertIsNone(event.key)

    def test_survives_a_missing_bus(self):
        self.assertFalse(publish_notification(None, title="x"))

    def test_reports_failure_when_the_queue_will_not_take_it(self):
        # Best-effort delivery, but the caller is told, so an agent waiting on a
        # human can fall back to asking in its reply.
        self.assertFalse(publish_notification(_Bus(full=True), title="x"))


class ProcessWideBusTest(unittest.TestCase):
    """The sink the scheduler, the indexer and the language servers publish to."""

    def tearDown(self) -> None:
        clear_notification_bus()

    def test_reaches_the_installed_bus(self):
        bus = _Bus()
        set_notification_bus(bus)
        self.assertTrue(notify(title="Scheduled job failed", level="error"))
        self.assertEqual(_event(bus).title, "Scheduled job failed")

    def test_is_a_no_op_before_a_gateway_starts(self):
        # A CLI run has no websocket to speak to. Callers deep in the codebase
        # must not have to know that, so this stays quiet rather than raising.
        clear_notification_bus()
        self.assertFalse(notify(title="x"))

    def test_stops_publishing_once_cleared(self):
        bus = _Bus()
        set_notification_bus(bus)
        clear_notification_bus()
        self.assertFalse(notify(title="x"))
        self.assertEqual(bus.outbound.items, [])

    def test_broadcasts_by_default(self):
        bus = _Bus()
        set_notification_bus(bus)
        notify(title="x")
        self.assertEqual(bus.outbound.items[-1].chat_id, "*")


class CronFailureNotificationTest(unittest.IsolatedAsyncioTestCase):
    """A job that fails while nobody is watching has to say so."""

    def tearDown(self) -> None:
        clear_notification_bus()

    def _job(self):
        from navin.cron.types import CronJob, CronSchedule

        return CronJob(
            id="job-1",
            name="Nightly backup",
            schedule=CronSchedule(kind="every", every_ms=60_000),
        )

    async def _run(self, on_job):
        from navin.cron.service import CronService

        bus = _Bus()
        set_notification_bus(bus)
        service = CronService.__new__(CronService)
        service.on_job = on_job
        service._MAX_RUN_HISTORY = 20
        job = self._job()
        await service._execute_job(job)
        return bus, job

    async def test_reports_a_failure_as_a_warning(self):
        """Warning, never error: display policy bans red banners in front of
        the client; the job state itself still records the error."""

        async def failing(_job):
            raise RuntimeError("disk full")

        bus, job = await self._run(failing)
        event = _event(bus)
        self.assertEqual(event.level, "warning")
        self.assertIn("Nightly backup", event.title)
        self.assertEqual(event.detail, "disk full")
        self.assertEqual(job.state.last_status, "error")

    async def test_keys_by_job_so_a_repeating_failure_stays_one_entry(self):
        async def failing(_job):
            raise RuntimeError("disk full")

        bus, _ = await self._run(failing)
        self.assertEqual(_event(bus).key, "cron:job-1")

    async def test_stays_quiet_when_the_job_succeeds(self):
        async def succeeding(_job):
            return None

        bus, job = await self._run(succeeding)
        self.assertEqual(job.state.last_status, "ok")
        self.assertEqual(bus.outbound.items, [])

    async def test_stays_quiet_when_the_job_is_skipped(self):
        # Overlapping runs skip by design; saying so every time would be noise.
        from navin.cron.service import CronJobSkippedError

        async def skipping(_job):
            raise CronJobSkippedError("already running")

        bus, job = await self._run(skipping)
        self.assertEqual(job.state.last_status, "skipped")
        self.assertEqual(bus.outbound.items, [])


class NotifyToolTest(unittest.TestCase):
    def _run(self, tool: NotifyTool, **kwargs: Any):
        return asyncio.run(tool.execute(**kwargs))

    def test_publishes_with_the_agent_source(self):
        bus = _Bus()
        result = self._run(NotifyTool(bus=bus), title="Need the API key", level="warning")
        self.assertFalse(result.is_error)
        event = _event(bus)
        self.assertEqual(event.source, "agent")
        self.assertEqual(event.title, "Need the API key")

    def test_requires_a_title(self):
        bus = _Bus()
        result = self._run(NotifyTool(bus=bus), title="   ")
        self.assertTrue(result.is_error)
        self.assertIn("title is required", result)
        self.assertEqual(bus.outbound.items, [])

    def test_rejects_an_invented_level(self):
        bus = _Bus()
        result = self._run(NotifyTool(bus=bus), title="x", level="critical")
        self.assertTrue(result.is_error)
        self.assertIn("level must be one of", result)
        self.assertEqual(bus.outbound.items, [])

    def test_truncates_an_overlong_title(self):
        bus = _Bus()
        self._run(NotifyTool(bus=bus), title="w" * 400)
        self.assertLessEqual(len(_event(bus).title), 120)

    def test_tells_the_agent_to_ask_in_its_reply_when_undeliverable(self):
        result = self._run(NotifyTool(bus=_Bus(full=True)), title="x")
        self.assertTrue(result.is_error)
        self.assertIn("ask in your reply", result)

    def test_is_available_to_subagents_too(self):
        self.assertIn("subagent", NotifyTool._scopes)

    def test_description_warns_against_progress_reports(self):
        # The guard against noise is the wording itself, so it is worth pinning:
        # a notification the user did not need teaches them to ignore the rest.
        description = NotifyTool().description.lower()
        self.assertIn("do not", description)
        self.assertIn("progress", description)


class WebsocketFrameTest(unittest.IsolatedAsyncioTestCase):
    """The frame shape the WebUI parses."""

    def _channel(self):
        from navin.channels.websocket import WebSocketChannel

        channel = WebSocketChannel.__new__(WebSocketChannel)
        sent: list[str] = []

        async def _safe_send_to(_connection: Any, raw: str, label: str = "") -> None:
            sent.append(raw)

        channel._safe_send_to = _safe_send_to  # type: ignore[method-assign]
        channel._conn_chats = {"conn-a": set()}  # type: ignore[attr-defined]
        channel._subs = {"chat-1": ["conn-a"]}  # type: ignore[attr-defined]
        return channel, sent

    async def test_frame_carries_the_fields_the_ui_needs(self):
        channel, sent = self._channel()
        await channel.send_notification(
            "chat-1",
            NotificationEvent(
                title="Deploy needs approval",
                level="warning",
                detail="since 14:02",
                key="deploy",
                source="agent",
            ),
        )
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "notification")
        self.assertEqual(body["chat_id"], "chat-1")
        self.assertEqual(body["title"], "Deploy needs approval")
        self.assertEqual(body["level"], "warning")
        self.assertEqual(body["detail"], "since 14:02")
        self.assertEqual(body["key"], "deploy")
        self.assertEqual(body["source"], "agent")

    async def test_star_reaches_every_connection(self):
        channel, sent = self._channel()
        await channel.send_notification("*", NotificationEvent(title="Gateway restarted"))
        self.assertEqual(len(sent), 1)

    async def test_optional_fields_are_omitted_when_empty(self):
        channel, sent = self._channel()
        await channel.send_notification("chat-1", NotificationEvent(title="x"))
        body = json.loads(sent[0])
        self.assertNotIn("detail", body)
        self.assertNotIn("key", body)

    async def test_nothing_is_sent_without_a_title(self):
        channel, sent = self._channel()
        await channel.send_notification("chat-1", NotificationEvent(title="  "))
        self.assertEqual(sent, [])

    async def test_nothing_is_sent_to_a_chat_nobody_watches(self):
        channel, sent = self._channel()
        await channel.send_notification("chat-unknown", NotificationEvent(title="x"))
        self.assertEqual(sent, [])

    async def test_the_dispatcher_routes_a_notification_to_its_own_frame(self):
        # The seam between the two halves: a notification must not fall through
        # to the generic text path and land in the transcript, which is exactly
        # what retry waits used to do - once per countdown tick.
        channel, sent = self._channel()
        bus = _Bus()
        set_notification_bus(bus)
        self.addCleanup(clear_notification_bus)
        notify(title="Scheduled job failed", level="error", chat_id="chat-1")
        await channel.send(bus.outbound.items[-1])
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "notification")
        self.assertEqual(body["title"], "Scheduled job failed")
        self.assertEqual(body["level"], "error")


if __name__ == "__main__":
    unittest.main()
