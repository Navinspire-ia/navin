# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A slow result/review disk must not stall terminal input during fan-in."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from navin.agent.subagent import SubagentManager
from navin.agent.tools.spawn import SpawnTool
from navin.bus.queue import MessageBus
from navin.tui.screens import PickerScreen, PickItem
from tests.test_subagent_concurrency import _Bus, _manager, _WritingRunner
from tests.test_tui_queue import make_app


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


def test_two_hundred_results_keep_input_live_and_survive_reload(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
        app = make_app(tmp_path)
        bus = MessageBus()
        manager = SubagentManager(workspace=tmp_path, bus=bus, max_tool_result_chars=4000)
        persist = manager._persist_outcomes
        entered, release = threading.Event(), threading.Event()
        ui_thread = threading.get_ident()
        stored_batches = []

        def slow_persist(*args):
            assert threading.get_ident() != ui_thread, "fsync is running on the input loop"
            stored_batches.append(len(args[1]))
            entered.set()
            assert release.wait(5), "UI could not release the disk write"
            return persist(*args)

        monkeypatch.setattr(manager, "_persist_outcomes", slow_persist)
        monkeypatch.setattr("navin.agent.tools.spawn.current_request_context", lambda: SimpleNamespace(
            runtime=Mock(), channel="cli", chat_id="direct", session_key="cli:direct",
        ))
        async with app.run_test(size=(100, 32)) as pilot:
            tasks = [asyncio.create_task(manager._announce_result(
                str(index), f"Worker {index}", "Audit one file", f"Verified {index}",
                {"channel": "cli", "chat_id": "direct"}, "ok",
            )) for index in range(200)]
            listing = None
            try:
                await wait_until(entered.is_set)
                assert bus.inbound_size == 0, "Results must be saved before announcing them"
                listing = asyncio.create_task(SpawnTool(manager).execute(action="results"))
                await pilot.press("f", "l", "u", "i", "d", "e")
                assert app.composer.text == "fluide"
                assert not listing.done(), "The gated disk write should still hold the result lock"
                await app.push_screen(PickerScreen("Sessions", [PickItem("other", "Other session")]))
                await pilot.press("escape")
                assert len(app.screen_stack) == 1
                assert not any(task.done() for task in tasks)
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(*tasks), 30)
                if listing is not None:
                    await listing
            assert bus.inbound_size == 200
            assert stored_batches == [200], "A simultaneous wave should share one durable write"
            reborn = SubagentManager(workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=4000)
            restored = reborn.recent_outcomes("cli:direct")
            assert {outcome.task_id for outcome in restored} == {str(i) for i in range(200)}
            assert all(outcome.summary == f"Verified {outcome.task_id}" for outcome in restored)

    asyncio.run(run())


def test_wave_larger_than_retention_saves_each_result_before_announcing(tmp_path, monkeypatch):
    monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
    monkeypatch.setattr("navin.agent.subagent._MAX_OUTCOME_HISTORY", 2)
    announced = []

    class DurableBus:
        async def publish_inbound(self, message):
            reborn = SubagentManager(workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=4000)
            saved = reborn.recent_outcomes(message.session_key_override)
            task_id = message.metadata["subagent_task_id"]
            assert task_id in {outcome.task_id for outcome in saved}
            announced.append(task_id)

    async def run():
        manager = SubagentManager(workspace=tmp_path, bus=DurableBus(), max_tool_result_chars=4000)
        async with asyncio.timeout(5):
            await asyncio.gather(*(manager._announce_result(
                str(index), f"Worker {index}", "Audit one file", f"Verified {index}",
                {"channel": "cli", "chat_id": "direct"}, "ok",
            ) for index in range(5)))
        assert announced == [str(index) for index in range(5)]
        assert [outcome.task_id for outcome in manager.recent_outcomes("cli:direct")] == ["4", "3"]

    asyncio.run(run())


def test_subagent_review_write_keeps_input_live(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
        entered, release = threading.Event(), threading.Event()
        recorded = []
        ui_thread = threading.get_ident()

        def record_edits(key, files):
            assert threading.get_ident() != ui_thread
            entered.set()
            assert release.wait(5)
            recorded.append((key, files))

        bus = _Bus()
        target = tmp_path / "edited.txt"
        target.write_text("original")
        manager = _manager(tmp_path, bus, _WritingRunner(target), record_edits=record_edits)
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await manager.spawn(task="Edit one file", runtime=Mock(), session_key="cli:direct")
            tasks = list(manager._running_tasks.values())
            try:
                await wait_until(entered.is_set)
                await pilot.press("o", "k")
                assert app.composer.text == "ok"
                assert not bus.published
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(*tasks), 10)
                manager._watchdog_task.cancel()
                await asyncio.gather(manager._watchdog_task, return_exceptions=True)
            assert recorded == [("cli:direct", {str(target): b"original"})]
            assert len(bus.published) == 1

    asyncio.run(run())
