# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The engine stays fluid with thousands of tasks and thousands of subagents.

Two registries grow with the user's ambition: the project board and the
subagent manager. Both are read on the event loop by the chat UI (snapshots,
``next`` queues, side panels) while agents keep writing to them. These tests
fill them to the thousands and measure what one call costs on the loop, so a
quadratic walk or a full-file rewrite per read shows up here, not as a frozen
Desktop at the worst time.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from navin.agent.tools.board import BoardTool
from navin.board.store import _MAX_TASKS, ProjectBoardStore

TASKS = 2000
AGENTS = 1500
# One call on the loop. Generous for CI boxes, far below anything a user feels.
LOOP_BUDGET_S = 0.25
# One board operation in its worker thread: the agent waits for it, the UI
# does not, but a plan of thousands of steps must still feel instant.
OP_BUDGET_S = 0.5


def _fill_board(project: Path, count: int) -> ProjectBoardStore:
    """A board of ``count`` tasks, written the way the store writes it.

    One real create gives the shape; the rest is replicated and written in one
    go, because filling through ``create_task`` costs a whole-file rewrite per
    task and would make this test about the fill, not about reading at scale.
    """
    store = ProjectBoardStore(project)
    store.create_task(
        title="task 0", actor="agent", actor_type="agent", description="x" * 200,
        priority="low", labels=["stress"],
    )
    model = store.read_tasks()[0]
    tasks = [model]
    for i in range(1, count):
        task = dict(model)
        task["id"] = f"t-{i:08x}"
        task["title"] = f"task {i}"
        task["priority"] = "high" if i % 3 else "low"
        tasks.append(task)
    store._write_tasks(tasks)
    return store


class TestBoardAtScale:
    def test_thousands_of_tasks_read_and_update_in_bounded_time(self, tmp_path: Path) -> None:
        assert TASKS <= _MAX_TASKS
        store = _fill_board(tmp_path, TASKS)
        tasks = store.read_tasks()
        assert len(tasks) == TASKS

        # Filing one more step on a board this size is one read and one write.
        started = time.perf_counter()
        store.create_task(title="one more", actor="agent", actor_type="agent")
        create_s = time.perf_counter() - started
        assert create_s < OP_BUDGET_S, f"one create took {create_s:.2f}s with {TASKS} tasks"

        started = time.perf_counter()
        payload = store.payload(activity_limit=50)
        read_s = time.perf_counter() - started
        assert len(payload["tasks"]) == TASKS + 1
        assert read_s < OP_BUDGET_S, f"board payload took {read_s:.2f}s for {TASKS} tasks"

        target = tasks[TASKS // 2]["id"]
        started = time.perf_counter()
        store.update_task(
            target, actor="agent", actor_type="agent",
            fields={"status": "in_progress"},
        )
        update_s = time.perf_counter() - started
        assert update_s < OP_BUDGET_S, f"one update took {update_s:.2f}s with {TASKS} tasks"

    def test_the_board_tool_keeps_the_loop_free_while_the_store_is_busy(self, tmp_path: Path) -> None:
        """The tool is what agents call: its disk work must not stall the loop."""
        _fill_board(tmp_path, 600)
        tool = BoardTool(workspace=tmp_path)

        async def scenario() -> tuple[float, str]:
            gaps: list[float] = []
            stop = asyncio.Event()

            async def ticker() -> None:
                last = time.perf_counter()
                while not stop.is_set():
                    await asyncio.sleep(0.01)
                    now = time.perf_counter()
                    gaps.append(now - last)
                    last = now

            ticker_task = asyncio.create_task(ticker())
            await asyncio.sleep(0.03)
            outs = []
            for _ in range(5):
                outs.append(str(await tool.execute(action="next")))
                outs.append(str(await tool.execute(action="list", filter_status="planned")))
            stop.set()
            await ticker_task
            return max(gaps), outs[0]

        longest, first = asyncio.run(scenario())
        assert "task" in first.lower()
        assert longest < LOOP_BUDGET_S, f"event loop stalled {longest:.2f}s during board calls"


class TestSubagentsAtScale:
    @pytest.fixture
    def manager(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import navin.agent.subagent as subagent_mod

        monkeypatch.setattr(subagent_mod, "_outcomes_dir", lambda: tmp_path / "outcomes")
        manager = subagent_mod.SubagentManager.__new__(subagent_mod.SubagentManager)
        # Only the registries under test are initialised: no engine, no bus.
        manager._journal_instance = None
        manager._task_statuses = {}
        return manager

    def test_listing_thousands_of_agents_is_linear_and_bounded(self, manager) -> None:
        journal = manager.journal
        started = time.perf_counter()
        for i in range(AGENTS):
            done = i % 4 == 0
            journal.create(
                "cli:direct", f"a{i}",
                {"task_id": f"a{i}", "label": f"agent {i}", "done": done,
                 "phase": "error" if done else "working", "error": "boom" if done else None},
                {"task": f"work item {i}", "workspace": "/w", "isolate": False},
            )
        create_s = time.perf_counter() - started
        assert create_s / AGENTS < 0.01, f"creating one agent record costs {create_s / AGENTS * 1000:.1f} ms"

        started = time.perf_counter()
        page = manager.list_agents("cli:direct", offset=0, limit=200, workspaces={"/w"})
        first_page_s = time.perf_counter() - started
        assert len(page) == 200
        assert first_page_s < LOOP_BUDGET_S, f"first page took {first_page_s:.2f}s for {AGENTS} agents"

        started = time.perf_counter()
        everything = manager.list_agents("cli:direct", offset=0, limit=1000)
        all_s = time.perf_counter() - started
        assert len(everything) == 1000
        assert all_s < LOOP_BUDGET_S, f"a 1000-row page took {all_s:.2f}s for {AGENTS} agents"
        failed = [row for row in everything if row.get("phase") == "error"]
        assert len(failed) == 250
        assert all(row["requires_relaunch"] for row in failed)

        # Pagination keeps the far end as cheap as the first page: no offset walk
        # over a growing list in Python, SQLite does it with the index.
        started = time.perf_counter()
        tail = manager.list_agents("cli:direct", offset=AGENTS - 50, limit=200)
        tail_s = time.perf_counter() - started
        assert len(tail) == 50
        assert tail_s < LOOP_BUDGET_S, f"last page took {tail_s:.2f}s"
