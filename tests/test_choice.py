# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The agent stops on a real fork and waits for a pick."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from navin.agent.choice import (
    CUSTOM_TEXT_MAX,
    OTHER_OPTION_ID,
    ChoiceAnswer,
    ChoiceBroker,
    ChoiceOption,
    ChoiceRequest,
    bind_choice_broker,
    handle_choice_answer,
    request_choice,
    reset_choice_broker,
)
from navin.agent.tools.ask_user import AskUserTool, _parse_options
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.bus.events import INBOUND_META_RUNTIME_CONTROL, RUNTIME_CONTROL_CHOICE_ANSWER


def _request(**kwargs: Any) -> ChoiceRequest:
    options = kwargs.pop(
        "options",
        (
            ChoiceOption("a", "Full phase", "3 to 4 days", recommended=True),
            ChoiceOption("b", "Profiler only", "See real data first"),
        ),
    )
    return ChoiceRequest(question="Where do we start?", options=options, **kwargs)


class _BrokerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.published: list[tuple[str, ChoiceRequest, dict[str, Any]]] = []
        self.closed: list[tuple[str, ChoiceAnswer]] = []
        self._ctx_token = bind_request_context(
            RequestContext(
                channel="websocket",
                chat_id="c1",
                session_key="websocket:c1",
            )
        )

    def tearDown(self) -> None:
        reset_request_context(self._ctx_token)

    def _broker(self, **kwargs: Any) -> ChoiceBroker:
        async def publish(request_id: str, request: ChoiceRequest, route: dict[str, Any]) -> None:
            self.published.append((request_id, request, route))

        async def close(request_id: str, answer: ChoiceAnswer, route: dict[str, Any]) -> None:
            self.closed.append((request_id, answer))

        return ChoiceBroker(publish=publish, close=close, timeout_s=2, **kwargs)

    async def _answer(
        self,
        broker: ChoiceBroker,
        request: ChoiceRequest,
        *,
        option_id: str = "",
        skipped: bool = False,
    ) -> ChoiceAnswer:
        task = asyncio.ensure_future(broker.ask(request))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        request_id = self.published[0][0]
        self.assertTrue(broker.resolve(request_id, option_id=option_id, skipped=skipped))
        return await task

    async def test_a_pick_reaches_the_waiter(self) -> None:
        broker = self._broker()
        answer = await self._answer(broker, _request(), option_id="b")
        self.assertEqual(answer.option_id, "b")
        self.assertEqual(answer.label, "Profiler only")
        self.assertFalse(answer.skipped)
        self.assertIn("Profiler only", answer.as_text())

    async def test_skip_takes_the_recommended_path(self) -> None:
        broker = self._broker()
        answer = await self._answer(broker, _request(), skipped=True)
        self.assertEqual(answer.option_id, "a")
        self.assertTrue(answer.skipped)
        self.assertIn("recommended", answer.as_text().lower())

    async def test_no_card_channel_does_not_pick_for_the_user(self) -> None:
        reset_request_context(self._ctx_token)
        self._ctx_token = bind_request_context(
            RequestContext(channel="telegram", chat_id="t1", session_key="telegram:t1")
        )
        broker = self._broker()
        answer = await broker.ask(_request())
        self.assertTrue(answer.unattended)
        self.assertIn("recommended", answer.as_text())
        self.assertIn("Full phase", answer.as_text())
        self.assertIn("own answer", answer.as_text().lower())
        self.assertEqual(self.published, [])

    async def test_request_choice_uses_the_bound_broker(self) -> None:
        broker = self._broker()
        token = bind_choice_broker(broker)
        try:
            task = asyncio.ensure_future(request_choice(_request()))
            for _ in range(100):
                if self.published:
                    break
                await asyncio.sleep(0)
            broker.resolve(self.published[0][0], option_id="a")
            answer = await task
            self.assertEqual(answer.option_id, "a")
        finally:
            reset_choice_broker(token)

    async def test_runtime_control_delivers_the_pick(self) -> None:
        broker = self._broker()

        class State:
            choices = broker

        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)

        class Msg:
            metadata = {
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CHOICE_ANSWER,
                "request_id": self.published[0][0],
                "option_id": "b",
                "skipped": False,
            }

        self.assertTrue(await handle_choice_answer(State(), Msg(), None))
        answer = await task
        self.assertEqual(answer.option_id, "b")

    async def test_stop_closes_the_card(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.closed), 1)
        self.assertEqual(broker.open_requests("websocket:c1"), [])

    async def test_unknown_option_leaves_the_waiter(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertFalse(broker.resolve(self.published[0][0], option_id="nope"))
        self.assertFalse(task.done())
        self.assertTrue(broker.resolve(self.published[0][0], option_id="a"))
        self.assertEqual((await task).option_id, "a")

    async def test_a_typed_answer_reaches_the_waiter(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertTrue(
            broker.resolve(
                self.published[0][0],
                custom_text="  NodePort on 30080, no Ingress  ",
            )
        )
        answer = await task
        self.assertTrue(answer.custom)
        self.assertEqual(answer.option_id, OTHER_OPTION_ID)
        self.assertEqual(answer.detail, "NodePort on 30080, no Ingress")
        self.assertIn("did not pick a listed option", answer.as_text())
        self.assertIn("NodePort on 30080", answer.as_text())

    async def test_whitespace_custom_text_does_not_count_as_an_answer(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertFalse(broker.resolve(self.published[0][0], custom_text="  \n\t  "))
        self.assertFalse(broker.resolve(self.published[0][0], option_id=OTHER_OPTION_ID))
        self.assertFalse(task.done())
        self.assertTrue(broker.resolve(self.published[0][0], option_id="a"))
        self.assertEqual((await task).option_id, "a")

    async def test_custom_text_is_capped(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertTrue(broker.resolve(self.published[0][0], custom_text="x" * (CUSTOM_TEXT_MAX + 50)))
        answer = await task
        self.assertEqual(len(answer.detail), CUSTOM_TEXT_MAX)

    async def test_skip_wins_over_a_typed_answer(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertTrue(
            broker.resolve(
                self.published[0][0],
                skipped=True,
                custom_text="ignore this",
            )
        )
        answer = await task
        self.assertTrue(answer.skipped)
        self.assertFalse(answer.custom)
        self.assertEqual(answer.option_id, "a")

    async def test_runtime_control_delivers_a_typed_answer(self) -> None:
        broker = self._broker()

        class State:
            choices = broker

        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)

        class Msg:
            metadata = {
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CHOICE_ANSWER,
                "request_id": self.published[0][0],
                "option_id": "__other__",
                "skipped": False,
                "custom_text": "Use the existing Istio Gateway in istio-system",
            }

        self.assertTrue(await handle_choice_answer(State(), Msg(), None))
        answer = await task
        self.assertTrue(answer.custom)
        self.assertIn("istio-system", answer.as_text())


class ParseOptionsTest(unittest.TestCase):
    def test_two_options_keep_the_recommended_flag(self) -> None:
        options, error = _parse_options(
            [
                {"id": "a", "label": "Full", "recommended": True},
                {"id": "b", "label": "Fast"},
            ]
        )
        self.assertEqual(error, "")
        self.assertTrue(options[0].recommended)
        self.assertFalse(options[1].recommended)

    def test_missing_reco_defaults_to_the_first(self) -> None:
        options, error = _parse_options(
            [{"id": "a", "label": "One"}, {"id": "b", "label": "Two"}]
        )
        self.assertEqual(error, "")
        self.assertTrue(options[0].recommended)

    def test_one_option_is_rejected(self) -> None:
        options, error = _parse_options([{"id": "a", "label": "Only"}])
        self.assertEqual(options, [])
        self.assertIn("2 to 4", error)

    def test_reserved_other_id_is_rejected(self) -> None:
        options, error = _parse_options(
            [
                {"id": "__other__", "label": "Mine"},
                {"id": "b", "label": "Theirs"},
            ]
        )
        self.assertEqual(options, [])
        self.assertIn("unique short id", error)

    def test_description_tells_the_model_the_user_can_type(self) -> None:
        text = AskUserTool().description.lower()
        self.assertIn("own answer", text)
        self.assertIn("follow that text", text)


class AskUserToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_bad_question_fails_without_asking(self) -> None:
        result = await AskUserTool().execute(
            question="",
            options=[{"id": "a", "label": "One"}, {"id": "b", "label": "Two"}],
        )
        self.assertIn("question", str(result).lower())


if __name__ == "__main__":
    unittest.main()
