from __future__ import annotations

import asyncio

from textual.widgets import Button, Static

from navin.board import session_focus
from navin.board.store import ProjectBoardStore
from navin.tui.session_overview import (
    SessionOverviewScreen,
    collect_prompt_history,
    collect_session_plan,
)
from tests.test_tui_queue import make_app


def test_saved_history_does_not_include_injected_or_hidden_messages():
    rows = collect_prompt_history([
        {"role": "user", "content": "Mon vrai message", "timestamp": "2026-11-07T10:00:00"},
        {"role": "assistant", "content": "Réponse"},
        {"role": "user", "content": "injection", "injected_event": "task"},
        {"role": "user", "content": "hidden", "_hidden_history": True},
        {"role": "user", "content": "à faire"},
    ])
    assert [row["text"] for row in rows] == ["Mon vrai message", "à faire"]
    assert [row["answered"] for row in rows] == ["1", ""]


def test_plan_only_includes_this_conversation_and_has_error_tab(tmp_path, monkeypatch):
    monkeypatch.setattr(session_focus, "_store_path", lambda: tmp_path / "focus.json")
    session_focus.clear()
    board = ProjectBoardStore(tmp_path)
    a = board.create_task(title="Tâche liée", actor="test", actor_type="agent", status="fix")
    board.create_task(title="Tâche autre conversation", actor="test", actor_type="agent", status="blocked")
    session_focus.remember("cli:direct", [a["id"]])
    assert [row["title"] for row in collect_session_plan(tmp_path, "cli:direct")] == ["Tâche liée"]
    assert collect_session_plan(tmp_path, "cli:other") == []

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 34)) as pilot:
            screen = SessionOverviewScreen("plan", tasks=collect_session_plan(tmp_path, "cli:direct"))
            app.push_screen(screen)
            await pilot.pause()
            await pilot.click(screen.query_one("#overview-errors", Button))
            await pilot.pause()
            text = str(screen.query_one("#overview-text", Static).content)
            assert "Tâche liée" in text and "Tâche autre conversation" not in text
            assert "À relancer" in text
            await pilot.press("escape")
    try:
        asyncio.run(run())
    finally:
        session_focus.clear()


def test_history_pages_display_only_twenty_prompts(tmp_path):
    async def run():
        app = make_app(tmp_path)
        prompts = [{"date": "07/11/2026", "text": f"question-{i}", "answered": ""} for i in range(45)]
        async with app.run_test(size=(110, 34)) as pilot:
            screen = SessionOverviewScreen("history", prompts=prompts)
            app.push_screen(screen)
            await pilot.pause()
            text = str(screen.query_one("#overview-text", Static).content)
            assert "question-44" in text and "question-24" not in text
            await pilot.click(screen.query_one("#overview-next", Button))
            await pilot.pause()
            text = str(screen.query_one("#overview-text", Static).content)
            assert "question-24" in text and "question-44" not in text
    asyncio.run(run())
