# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Resolved prompts must release the transcript for the rest of the turn."""

import asyncio

import pytest
from textual.widgets import Input

from navin.tui.runtime import (
    UiApprovalClosed,
    UiApprovalRequested,
    UiChoiceClosed,
    UiChoiceRequested,
    UiStreamDelta,
    UiStreamEnd,
)
from navin.tui.widgets import ApprovalCard, ChoiceCard
from tests.test_tui_queue import make_app


def approval_request(request_id="permission"):
    return UiApprovalRequested(
        request_id=request_id,
        tool="exec",
        action="Run a shell command that needs your approval",
        reason="It matches a path outside the current project.",
        detail="$ command -v pip3 uv; ls /home/aymen/.navin/bin/ | head",
        consequence="The command runs with your user permissions.",
        scope="exec:workspaceEscape",
        remember_offered=True,
        expires_at_ms=None,
    )


def choice_request(request_id="question"):
    return UiChoiceRequested(
        request_id=request_id,
        question="Comment veux-tu gerer les fichiers archives ?",
        options=[
            {"id": "zip", "label": "Supporter zip"},
            {"id": "all", "label": "Supporter zip + rar/7z"},
        ],
        allow_skip=True,
        recommended_id="all",
        expires_at_ms=None,
    )


async def start_work(app):
    await app.submit_text("Ajouter le support des archives")
    await app.runtime.bus.consume_inbound()
    await app._on_runtime_event(UiStreamDelta("Analyse des fichiers.\n\n" * 25))
    await app._on_runtime_event(UiStreamEnd())
    app.composer.set_text("brouillon conserve")


async def assert_work_visible(app, pilot):
    await app._on_runtime_event(UiStreamDelta("Le travail continue apres la validation."))
    await app._on_runtime_event(UiStreamEnd())
    await pilot.pause()
    await pilot.wait_for_scheduled_animations()
    assert not app.query(ApprovalCard)
    assert not app.query(ChoiceCard)
    assert not app._pending_approvals
    assert not app._pending_choices
    assert not app.composer.shortcut_keys
    assert app.composer.text == "brouillon conserve"
    assert app.focused is app.composer
    assert app.runtime.turn_active
    assert app.transcript.children[-1] is app._current
    assert app._current.text.endswith("Le travail continue apres la validation.")
    assert app.transcript.is_vertical_scroll_end, (
        app.transcript.scroll_y, app.transcript.max_scroll_y, app.transcript.auto_follow,
    )


@pytest.mark.parametrize("decision", ["allow", "always", "deny"])
def test_permission_disappears_after_decision(tmp_path, decision):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await start_work(app)
            await app._on_runtime_event(approval_request())
            await pilot.pause()
            card = app.query_one(ApprovalCard)
            await pilot.click(card.query_one(f"#{decision}"))
            answer = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert answer.metadata["allowed"] is (decision != "deny")
            assert answer.metadata["remember"] is (decision == "always")
            await assert_work_visible(app, pilot)
            # The engine's acknowledgement may arrive after the local decision.
            await app._on_runtime_event(UiApprovalClosed("permission", decision != "deny", ""))
            await app._approval_decided(ApprovalCard.Decided("permission", True, False))
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())


@pytest.mark.parametrize("answer_kind", ["selected", "number", "custom", "skipped"])
def test_question_disappears_after_answer(tmp_path, answer_kind):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 28)) as pilot:
            await start_work(app)
            await app._on_runtime_event(choice_request())
            await pilot.pause()
            card = app.query_one(ChoiceCard)
            if answer_kind == "custom":
                custom = card.query_one(Input)
                custom.focus()
                custom.value = "Seulement les archives locales"
            await pilot.press({"number": "2", "skipped": "escape"}.get(answer_kind, "enter"))
            answer = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert answer.metadata["option_id"] == ("all" if answer_kind in {"selected", "number"} else "")
            assert answer.metadata["custom_text"] == ("Seulement les archives locales" if answer_kind == "custom" else "")
            assert answer.metadata["skipped"] is (answer_kind == "skipped")
            await assert_work_visible(app, pilot)
            await app._on_runtime_event(UiChoiceClosed("question", answer.metadata["option_id"], answer_kind == "skipped"))
            await app._choice_answered(ChoiceCard.Answered("question", "zip", False, ""))
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())


def test_answering_one_prompt_keeps_the_other_pending_prompt_usable(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 40)) as pilot:
            await start_work(app)
            await app._on_runtime_event(choice_request())
            await app._on_runtime_event(approval_request())
            await pilot.pause()
            await pilot.press("2")
            answer = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert answer.metadata["option_id"] == "all"
            assert not app.query(ChoiceCard)
            assert len(app.query(ApprovalCard)) == 1
            assert app.composer.shortcut_keys == {"y", "a", "n"}
            await pilot.click(app.query_one(ApprovalCard).query_one("#always"))
            decision = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert decision.metadata["allowed"]
            assert decision.metadata["remember"]
            await assert_work_visible(app, pilot)
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["approval", "choice"])
@pytest.mark.parametrize("expired", [False, True])
def test_engine_closure_removes_prompt_without_submitting_another_answer(tmp_path, kind, expired):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 28)) as pilot:
            await start_work(app)
            if kind == "approval":
                await app._on_runtime_event(approval_request())
                await pilot.pause()
                app.query_one(ApprovalCard).query_one("#allow").focus()
                closed = UiApprovalClosed("permission", not expired, "expired" if expired else "allowed")
            else:
                await app._on_runtime_event(choice_request())
                await pilot.pause()
                closed = UiChoiceClosed("question", "" if expired else "all", expired)
            await app._on_runtime_event(closed)
            await assert_work_visible(app, pilot)
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())
