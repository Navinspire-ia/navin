# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A subagent's question reaches the user who spawned it, in the CLI and the desktop.

A subagent runs in a background task. When it was spawned outside a turn (a
Multitask prompt, a resume after a restart) no choice broker was bound in its
context, so ``ask_user`` got the unattended refusal: the question ended up as
plain text in the result, vanished from the chat and the journal, and the run
read as "the subagent did nothing". The manager now carries the runtime's own
broker as the fallback, and the card is named after the subagent so the user
knows who is asking. The broker's timeout then picks the recommended option,
like for the main agent.
"""

import asyncio
from unittest.mock import Mock

from navin.agent import choice
from navin.agent.choice import (
    ChoiceAnswer,
    ChoiceOption,
    ChoiceRequest,
    SubagentChoiceBroker,
    request_choice,
)
from navin.agent.runner import AgentRunResult
from tests.test_subagent_delivery_guards import _manager, _stop

QUESTION = ChoiceRequest(
    question="Which module reads the invoices?",
    options=(
        ChoiceOption(id="a", label="Invoice import", recommended=True),
        ChoiceOption(id="b", label="PDF reader"),
    ),
)


class _Parent:
    """Stands for the runtime's ChoiceBroker: records what it is asked."""

    def __init__(self, option_id: str = "b", label: str = "PDF reader") -> None:
        self.asked: list[ChoiceRequest] = []
        self._answer = ChoiceAnswer(option_id=option_id, label=label)

    async def ask(self, request: ChoiceRequest) -> ChoiceAnswer:
        self.asked.append(request)
        return self._answer

    def open_requests(self, session_key=None):
        return []

    def cancel_session(self, session_key, reason):
        return 0


class _AskingRunner:
    """The subagent's only step is to ask the user."""

    def __init__(self) -> None:
        self.answers: list[ChoiceAnswer] = []

    async def run(self, spec):
        self.answers.append(await request_choice(QUESTION))
        return AgentRunResult(final_content="done", messages=[])


async def _spawn_and_finish(manager, **kwargs) -> None:
    detail = await manager.spawn(task="Work", runtime=Mock(), session_key="cli:one", **kwargs)
    assert "started" in detail, detail
    await asyncio.gather(*list(manager._running_tasks.values()))
    await _stop(manager, "cli:one")


def test_question_spawned_outside_a_turn_reaches_the_runtime_broker(tmp_path, monkeypatch):
    parent = _Parent()
    runner = _AskingRunner()

    async def run():
        assert choice.current_choice_broker() is None, "this spawn happens outside any turn"
        manager = _manager(tmp_path, monkeypatch, runner=runner, choice_broker=parent)
        await _spawn_and_finish(manager, label="worker")

    asyncio.run(run())
    assert len(parent.asked) == 1
    asked = parent.asked[0]
    # The card names the subagent, keeps its options and lands on the parent's chat.
    assert asked.question == "[worker] Which module reads the invoices?"
    assert asked.options == QUESTION.options
    assert asked.recommended.id == "a"
    # The user's answer, not the fallback, is what the subagent continues with.
    assert runner.answers == [ChoiceAnswer(option_id="b", label="PDF reader")]
    assert not runner.answers[0].unattended


def test_turn_broker_wins_over_the_runtime_fallback(tmp_path, monkeypatch):
    turn_broker = _Parent(option_id="a", label="Invoice import")
    fallback = _Parent()
    runner = _AskingRunner()

    async def run():
        manager = _manager(tmp_path, monkeypatch, runner=runner, choice_broker=fallback)
        token = choice.bind_choice_broker(turn_broker)
        try:
            await _spawn_and_finish(manager, label="worker")
        finally:
            choice.reset_choice_broker(token)

    asyncio.run(run())
    assert len(turn_broker.asked) == 1 and fallback.asked == []
    assert runner.answers[0].option_id == "a"


def test_without_any_broker_the_subagent_is_told_nobody_can_answer(tmp_path, monkeypatch):
    runner = _AskingRunner()

    async def run():
        manager = _manager(tmp_path, monkeypatch, runner=runner)
        await _spawn_and_finish(manager, label="worker")

    asyncio.run(run())
    answer = runner.answers[0]
    assert answer.unattended is True
    # The refusal restates the question so the subagent reports it instead of guessing.
    assert "Which module reads the invoices?" in answer.reason
    assert "a) Invoice import (recommended)" in answer.reason


def test_subagent_broker_does_not_double_the_label():
    parent = _Parent()
    broker = SubagentChoiceBroker(parent, label="worker")
    already = ChoiceRequest(question="[worker] Keep?", options=QUESTION.options)

    async def run():
        await broker.ask(QUESTION)
        await broker.ask(already)

    asyncio.run(run())
    assert [r.question for r in parent.asked] == [
        "[worker] Which module reads the invoices?",
        "[worker] Keep?",
    ]
    assert broker.open_requests("cli:one") == []
    assert broker.cancel_session("cli:one", "stopped") == 0
