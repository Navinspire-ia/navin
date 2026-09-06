"""The approval gate: what happens when a tool stops to ask.

The interesting cases are the ones where nobody answers. A missing answer must
read as a refusal on every path - no gate, no channel, a timeout, a stopped turn
- because the alternative is an agent that treats silence as consent.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from navin.agent.approval import (
    NO_APPROVER,
    ApprovalBroker,
    ApprovalConfig,
    ApprovalDecision,
    ApprovalRequest,
    UnattendedGate,
    bind_approval_gate,
    handle_approval_decision,
    request_approval,
    reset_approval_gate,
)
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context


def _request(**kwargs: Any) -> ApprovalRequest:
    fields: dict[str, Any] = {
        "tool": "exec",
        "action": "Run something dangerous",
        "reason": "It matches a rule",
    }
    fields.update(kwargs)
    return ApprovalRequest(**fields)


class _BrokerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.published: list[tuple[str, ApprovalRequest, dict[str, Any]]] = []
        self.closed: list[tuple[str, ApprovalDecision]] = []
        self._ctx_token = bind_request_context(RequestContext(
            channel="websocket",
            chat_id="c1",
            session_key="websocket:c1",
        ))

    def tearDown(self) -> None:
        reset_request_context(self._ctx_token)

    def _broker(self, **config: Any) -> ApprovalBroker:
        async def publish(request_id: str, request: ApprovalRequest, route: dict[str, Any]) -> None:
            self.published.append((request_id, request, route))

        async def close(request_id: str, decision: ApprovalDecision, route: dict[str, Any]) -> None:
            self.closed.append((request_id, decision))

        # enabled=True unless a test says otherwise: the gate ships off, and
        # these tests are about how it behaves once an operator turns it on.
        return ApprovalBroker(
            publish=publish,
            close=close,
            config=ApprovalConfig(**{"enabled": True, **config}),
        )

    async def _answer(
        self,
        broker: ApprovalBroker,
        request: ApprovalRequest,
        *,
        allowed: bool,
        remember: bool = False,
    ) -> ApprovalDecision:
        """Ask, then answer from outside, the way the websocket would."""
        task = asyncio.ensure_future(broker.ask(request))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertTrue(self.published, "nothing was published to the user")
        request_id = self.published[-1][0]
        self.assertTrue(broker.resolve(request_id, allowed=allowed, remember=remember))
        return await task


class AnsweringTest(_BrokerTest):
    async def test_an_allow_lets_the_tool_through(self) -> None:
        decision = await self._answer(self._broker(), _request(), allowed=True)
        self.assertTrue(decision.allowed)

    async def test_a_refusal_carries_a_reason_for_the_model(self) -> None:
        decision = await self._answer(self._broker(), _request(), allowed=False)
        self.assertFalse(decision.allowed)
        self.assertIn("refused", decision.reason)

    async def test_the_question_reaches_the_chat_that_asked(self) -> None:
        await self._answer(self._broker(), _request(), allowed=True)
        _request_id, _req, route = self.published[-1]
        self.assertEqual(route["channel"], "websocket")
        self.assertEqual(route["chat_id"], "c1")

    async def test_the_card_is_closed_however_it_ended(self) -> None:
        broker = self._broker()
        await self._answer(broker, _request(), allowed=False)
        self.assertEqual(len(self.closed), 1)
        self.assertFalse(self.closed[0][1].allowed)

    async def test_a_second_answer_finds_no_waiter(self) -> None:
        broker = self._broker()
        await self._answer(broker, _request(), allowed=True)
        request_id = self.published[-1][0]
        self.assertFalse(broker.resolve(request_id, allowed=True))

    async def test_an_answer_to_an_unknown_request_is_not_an_error(self) -> None:
        self.assertFalse(self._broker().resolve("nope", allowed=True))


class AnswererBindingTest(_BrokerTest):
    """An answer only counts when it comes from a subscriber of the asking chat.

    The card is shown to the chat's subscribers; an answer from a connection
    that never saw it is a replayed request id from another conversation.
    """

    async def _ask(self, broker: ApprovalBroker) -> tuple[asyncio.Task, str]:
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        return task, self.published[-1][0]

    async def test_a_subscriber_of_the_asking_chat_may_answer(self) -> None:
        broker = self._broker()
        task, request_id = await self._ask(broker)
        self.assertTrue(broker.resolve(
            request_id, allowed=True, answerer_chats=["c1", "other"],
        ))
        self.assertTrue((await task).allowed)

    async def test_a_stranger_connection_cannot_answer(self) -> None:
        broker = self._broker()
        task, request_id = await self._ask(broker)
        self.assertFalse(broker.resolve(
            request_id, allowed=True, answerer_chats=["someone-elses-chat"],
        ))
        # The request is still open for the real chat to answer.
        self.assertTrue(broker.resolve(
            request_id, allowed=False, answerer_chats=["c1"],
        ))
        self.assertFalse((await task).allowed)

    async def test_no_chat_list_keeps_the_historical_trust(self) -> None:
        broker = self._broker()
        task, request_id = await self._ask(broker)
        self.assertTrue(broker.resolve(request_id, allowed=True))
        self.assertTrue((await task).allowed)

    async def test_the_runtime_control_handler_forwards_the_chats(self) -> None:
        broker = self._broker()
        task, request_id = await self._ask(broker)

        class _State:
            approvals = broker

        class _Msg:
            metadata = {
                "_runtime_control": "approval_decision",
                "request_id": request_id,
                "allowed": True,
                "answerer_chats": ["not-the-asking-chat"],
            }

        handled = await handle_approval_decision(_State(), _Msg(), None)
        self.assertTrue(handled)
        # Refused delivery: the waiter is still pending.
        self.assertFalse(task.done())
        self.assertTrue(broker.resolve(request_id, allowed=False, answerer_chats=["c1"]))
        await task


class RememberTest(_BrokerTest):
    async def test_always_allow_covers_the_same_scope_again(self) -> None:
        broker = self._broker()
        first = await self._answer(
            broker, _request(scope="exec:recursiveDelete"), allowed=True, remember=True
        )
        self.assertTrue(first.allowed)
        self.published.clear()

        second = await broker.ask(_request(scope="exec:recursiveDelete"))
        self.assertTrue(second.allowed)
        self.assertTrue(second.remembered)
        self.assertFalse(self.published, "the user was asked twice for one consent")

    async def test_another_scope_is_still_asked(self) -> None:
        broker = self._broker()
        await self._answer(
            broker, _request(scope="exec:recursiveDelete"), allowed=True, remember=True
        )
        self.published.clear()
        decision = await self._answer(
            broker, _request(scope="git:push-force"), allowed=False
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(self.published)

    async def test_a_scopeless_request_is_never_remembered(self) -> None:
        broker = self._broker()
        await self._answer(broker, _request(), allowed=True, remember=True)
        self.published.clear()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertTrue(self.published, "an unscoped allow was reused")
        broker.resolve(self.published[-1][0], allowed=False)
        await task

    async def test_remember_off_does_not_carry_consent_over(self) -> None:
        broker = self._broker(remember=False)
        await self._answer(
            broker, _request(scope="exec:recursiveDelete"), allowed=True, remember=True
        )
        self.published.clear()
        task = asyncio.ensure_future(broker.ask(_request(scope="exec:recursiveDelete")))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertTrue(self.published)
        broker.resolve(self.published[-1][0], allowed=False)
        await task

    async def test_forgetting_a_session_drops_its_allowances(self) -> None:
        broker = self._broker()
        await self._answer(
            broker, _request(scope="exec:recursiveDelete"), allowed=True, remember=True
        )
        broker.forget("websocket:c1")
        self.published.clear()
        task = asyncio.ensure_future(broker.ask(_request(scope="exec:recursiveDelete")))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertTrue(self.published)
        broker.resolve(self.published[-1][0], allowed=False)
        await task


class _ImpatientBroker(ApprovalBroker):
    """The configured minimum is 10 seconds, which is not a unit test."""

    def _timeout_s(self) -> float:
        return 0.05


class SilenceTest(_BrokerTest):
    def _impatient(self) -> ApprovalBroker:
        async def publish(request_id: str, request: ApprovalRequest, route: dict[str, Any]) -> None:
            self.published.append((request_id, request, route))

        return _ImpatientBroker(publish=publish, config=ApprovalConfig(enabled=True))

    async def test_no_answer_in_time_is_a_refusal(self) -> None:
        decision = await self._impatient().ask(_request())
        self.assertFalse(decision.allowed)
        self.assertIn("did not answer", decision.reason)

    async def test_a_timed_out_request_stops_being_open(self) -> None:
        broker = self._impatient()
        await broker.ask(_request())
        self.assertEqual(broker.open_requests(), [])

    async def test_disabled_in_config_allows_without_asking(self) -> None:
        """Turning the gate off removes the stop; it does not make it permanent.

        Reading "disabled" as "nobody can be asked, so refuse" meant the one
        setting whose point is to stop interrupting the agent was also the only
        way to block it for good, with no way to answer.
        """
        broker = self._broker(enabled=False)
        decision = await broker.ask(_request())
        self.assertTrue(decision.allowed)
        self.assertFalse(self.published)

    async def test_the_gate_is_off_until_an_operator_asks_for_it(self) -> None:
        broker = ApprovalBroker(publish=None, config=ApprovalConfig())
        self.assertTrue((await broker.ask(_request())).allowed)

    async def test_a_channel_that_cannot_ask_refuses(self) -> None:
        reset_request_context(self._ctx_token)
        self._ctx_token = bind_request_context(RequestContext(
            channel="", chat_id="", session_key="cli:local"
        ))
        decision = await self._broker().ask(_request())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, NO_APPROVER)

    async def test_the_cli_is_unattended_rather_than_kept_waiting(self) -> None:
        """A channel that cannot render the card must not be published to."""
        reset_request_context(self._ctx_token)
        self._ctx_token = bind_request_context(RequestContext(
            channel="cli", chat_id="local", session_key="cli:local"
        ))
        decision = await self._broker().ask(_request())
        self.assertFalse(decision.allowed)
        self.assertFalse(self.published, "the CLI was asked a question it cannot show")


class UnattendedDefaultTest(_BrokerTest):
    """Adding a question must not take an operation away from a headless run."""

    async def test_an_operation_that_used_to_proceed_still_does(self) -> None:
        reset_request_context(self._ctx_token)
        self._ctx_token = bind_request_context(RequestContext(
            channel="cli", chat_id="local", session_key="cli:local"
        ))
        decision = await self._broker().ask(_request(allow_when_unattended=True))
        self.assertTrue(decision.allowed)

    async def test_an_operation_that_used_to_be_refused_still_is(self) -> None:
        reset_request_context(self._ctx_token)
        self._ctx_token = bind_request_context(RequestContext(
            channel="cli", chat_id="local", session_key="cli:local"
        ))
        decision = await self._broker().ask(_request(allow_when_unattended=False))
        self.assertFalse(decision.allowed)

    async def test_no_gate_at_all_follows_the_same_rule(self) -> None:
        permissive = await request_approval(_request(allow_when_unattended=True))
        strict = await request_approval(_request())
        self.assertTrue(permissive.allowed)
        self.assertFalse(strict.allowed)

    async def test_a_subagent_gate_follows_it_too(self) -> None:
        token = bind_approval_gate(UnattendedGate("a background subagent cannot ask"))
        try:
            permissive = await request_approval(_request(allow_when_unattended=True))
            strict = await request_approval(_request())
        finally:
            reset_approval_gate(token)
        self.assertTrue(permissive.allowed)
        self.assertIn("subagent", permissive.reason)
        self.assertFalse(strict.allowed)

    async def test_the_user_still_decides_when_they_can_be_asked(self) -> None:
        """Being permissive when unattended does not mean permissive on refusal."""
        decision = await self._answer(
            self._broker(), _request(allow_when_unattended=True), allowed=False
        )
        self.assertFalse(decision.allowed)

    async def test_a_timeout_is_a_refusal_even_for_a_permissive_request(self) -> None:
        """The card was shown and ignored, which is not the unattended case."""

        class Impatient(ApprovalBroker):
            def _timeout_s(self) -> float:
                return 0.05

        async def publish(request_id: str, request: ApprovalRequest, route: dict[str, Any]) -> None:
            self.published.append((request_id, request, route))

        decision = await Impatient(publish=publish, config=ApprovalConfig(enabled=True)).ask(
            _request(allow_when_unattended=True)
        )
        self.assertFalse(decision.allowed)

    async def test_a_publisher_that_fails_refuses_rather_than_waits(self) -> None:
        async def boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("socket gone")

        broker = ApprovalBroker(publish=boom, config=ApprovalConfig(enabled=True))
        decision = await broker.ask(_request())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, NO_APPROVER)
        self.assertEqual(broker.open_requests(), [])

    async def test_a_stopped_turn_closes_the_card_and_propagates(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(broker.open_requests(), [])
        self.assertEqual(len(self.closed), 1)
        self.assertIn("stopped", self.closed[0][1].reason)

    async def test_cancelling_a_session_refuses_what_is_open(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertEqual(broker.cancel_session("websocket:c1", "the turn was stopped"), 1)
        decision = await task
        self.assertFalse(decision.allowed)
        self.assertIn("stopped", decision.reason)

    async def test_cancelling_another_session_leaves_this_one_alone(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertEqual(broker.cancel_session("websocket:other", "stopped"), 0)
        broker.resolve(self.published[-1][0], allowed=True)
        self.assertTrue((await task).allowed)


class OpenRequestsTest(_BrokerTest):
    async def test_a_reloaded_browser_can_see_what_is_pending(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request(detail="rm -rf build")))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        open_now = broker.open_requests("websocket:c1")
        self.assertEqual(len(open_now), 1)
        self.assertEqual(open_now[0]["detail"], "rm -rf build")
        self.assertIsNotNone(open_now[0]["expires_at_ms"])
        broker.resolve(self.published[-1][0], allowed=True)
        await task

    async def test_another_session_sees_nothing(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        self.assertEqual(broker.open_requests("websocket:other"), [])
        broker.resolve(self.published[-1][0], allowed=True)
        await task


class GateBindingTest(_BrokerTest):
    async def test_no_gate_bound_means_refused_with_an_explanation(self) -> None:
        decision = await request_approval(_request())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, NO_APPROVER)

    async def test_a_bound_gate_is_used(self) -> None:
        broker = self._broker()
        token = bind_approval_gate(broker)
        try:
            task = asyncio.ensure_future(request_approval(_request()))
            for _ in range(100):
                if self.published:
                    break
                await asyncio.sleep(0)
            broker.resolve(self.published[-1][0], allowed=True)
            self.assertTrue((await task).allowed)
        finally:
            reset_approval_gate(token)

    async def test_an_unattended_gate_names_its_context(self) -> None:
        token = bind_approval_gate(UnattendedGate("a background subagent cannot ask"))
        try:
            decision = await request_approval(_request())
        finally:
            reset_approval_gate(token)
        self.assertFalse(decision.allowed)
        self.assertIn("subagent", decision.reason)

    async def test_a_gate_that_raises_refuses_instead_of_crashing_the_turn(self) -> None:
        class Broken:
            async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
                raise RuntimeError("nope")

        token = bind_approval_gate(Broken())
        try:
            decision = await request_approval(_request())
        finally:
            reset_approval_gate(token)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, NO_APPROVER)

    async def test_a_cancelled_gate_does_not_become_a_refusal(self) -> None:
        class Slow:
            async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
                await asyncio.sleep(10)
                raise AssertionError("unreachable")

        token = bind_approval_gate(Slow())
        try:
            task = asyncio.ensure_future(request_approval(_request()))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            reset_approval_gate(token)


class RuntimeControlTest(_BrokerTest):
    """The answer arrives as a runtime-control message, not a user message."""

    class _Msg:
        def __init__(self, metadata: dict[str, Any]) -> None:
            self.metadata = metadata

    def _control(self, **fields: Any) -> "RuntimeControlTest._Msg":
        from navin.bus.events import (
            INBOUND_META_RUNTIME_CONTROL,
            RUNTIME_CONTROL_APPROVAL_DECISION,
        )

        metadata = {INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_APPROVAL_DECISION}
        metadata.update(fields)
        return self._Msg(metadata)

    class _State:
        def __init__(self, broker: ApprovalBroker) -> None:
            self.approvals = broker

    async def test_a_decision_reaches_the_waiting_tool(self) -> None:
        broker = self._broker()
        task = asyncio.ensure_future(broker.ask(_request()))
        for _ in range(100):
            if self.published:
                break
            await asyncio.sleep(0)
        handled = await handle_approval_decision(
            self._State(broker),
            self._control(request_id=self.published[-1][0], allowed=True),
            None,
        )
        self.assertTrue(handled)
        self.assertTrue((await task).allowed)

    async def test_another_control_message_is_left_to_its_handler(self) -> None:
        handled = await handle_approval_decision(
            self._State(self._broker()),
            self._Msg({"_runtime_control": "mcp_reload"}),
            None,
        )
        self.assertFalse(handled)

    async def test_a_late_answer_is_swallowed_not_passed_on(self) -> None:
        handled = await handle_approval_decision(
            self._State(self._broker()),
            self._control(request_id="gone", allowed=True),
            None,
        )
        self.assertTrue(handled, "a late answer must not fall through to MCP")

    async def test_a_malformed_answer_does_not_raise(self) -> None:
        self.assertTrue(await handle_approval_decision(
            self._State(self._broker()), self._control(allowed=True), None
        ))

    async def test_a_state_without_a_broker_is_tolerated(self) -> None:
        self.assertTrue(await handle_approval_decision(
            object(), self._control(request_id="x", allowed=True), None
        ))


if __name__ == "__main__":
    unittest.main()
