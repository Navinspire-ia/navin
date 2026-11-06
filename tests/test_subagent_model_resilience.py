# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Subagents survive a model that stops answering, and never run for hours.

On 2026-11-06 subagents of a CLI session ended on "Sorry, I encountered an
error calling the AI model." after one silent minute, others ran for three
hours without a report, and the only trace was the generic sentence. Covers:

- a transient model error resumes the run with the work already done, and a
  refusal does not;
- the final message names a model that stopped answering;
- the model-error log holds the failure, without secrets;
- the wrap-up note is injected once, the time limit stops the run with its
  partial work and the reason;
- a model taking over runs at its deepest effort (a flagship peer at the
  turn's), an effort picked in the chat is kept by it, and a subagent goes
  down the same ladder as the main agent;
- the default team is at high.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from navin.agent import subagent as subagent_module
from navin.agent.runner import (
    _PERSISTED_MODEL_ERROR_PLACEHOLDER,
    AgentRunResult,
    without_model_error_placeholder,
)
from navin.agent.subagent import SubagentManager, SubagentStatus, _humanize_error
from navin.providers import model_error_log
from navin.providers.base import LLMResponse
from navin.providers.user_facing_errors import content_allows_model_switch, user_facing_llm_error


def _timeout_response() -> LLMResponse:
    return LLMResponse(
        content="Error calling LLM: timed out after 300s",
        finish_reason="error",
        error_kind="timeout",
    )


def _timeout_result(messages: list[dict]) -> AgentRunResult:
    return AgentRunResult(
        final_content=user_facing_llm_error("Error calling LLM: timed out after 300s"),
        messages=[*messages, {"role": "assistant", "content": _PERSISTED_MODEL_ERROR_PLACEHOLDER}],
        stop_reason="error",
        error="The model provider stopped answering",
        retryable_error=True,
    )


class _Bus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish_inbound(self, msg) -> None:
        self.published.append(msg)


class _Runtime:
    model = "anthropic/claude-opus-5.5"

    def with_generation_overrides(self, **_):
        return self


class _ResumingRunner:
    """Fails with a model timeout *failures* times, then finishes."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.specs: list = []

    async def run(self, spec) -> AgentRunResult:
        self.specs.append(spec)
        messages = list(spec.initial_messages)
        if len(self.specs) <= self.failures:
            work = {"role": "assistant", "content": f"step {len(self.specs)} done"}
            return _timeout_result([*messages, work])
        return AgentRunResult(final_content="all done", messages=messages)


class _RefusingRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, spec) -> AgentRunResult:
        self.calls += 1
        return AgentRunResult(
            final_content="Authentication with the model provider failed.",
            messages=list(spec.initial_messages),
            stop_reason="error",
            error="Authentication with the model provider failed.",
            retryable_error=False,
        )


class _StuckRunner:
    """Never answers until released: what a silent model looks like from outside."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.injections: list[list[dict]] = []
        self.spec = None

    async def run(self, spec) -> AgentRunResult:
        self.spec = spec
        await self.release.wait()
        return AgentRunResult(final_content="finished", messages=list(spec.initial_messages))


class _ManagerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = _Bus()
        self.manager = SubagentManager(
            workspace=Path(self._tmp.name),
            bus=self.bus,
            max_tool_result_chars=4000,
        )

    async def _spawn(self, runner, label: str = "worker") -> str:
        self.manager.runner = runner
        reply = await self.manager.spawn(
            task=f"do {label}",
            label=label,
            runtime=_Runtime(),
            origin_channel="websocket",
            origin_chat_id="chat-1",
            session_key="websocket:chat-1",
        )
        self.assertIn("started", reply)
        return reply

    async def _wait_done(self, timeout: float = 15.0) -> None:
        # A finished run is forgotten by the manager and announced on the bus:
        # the announcement is what the parent waits for, so it is what we wait for.
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.bus.published and self.manager.get_running_count() == 0:
                return
            await asyncio.sleep(0.01)
        self.fail("subagent did not finish in time")

    def _announcements(self) -> list[str]:
        return [str(getattr(msg, "content", "")) for msg in self.bus.published]


class ModelErrorResumeTest(_ManagerCase):
    async def test_a_model_timeout_resumes_the_run_with_the_work_already_done(self) -> None:
        runner = _ResumingRunner(failures=1)
        with mock.patch.object(subagent_module, "_MODEL_RESUME_DELAYS_S", (0.0, 0.0, 0.0)):
            await self._spawn(runner)
            await self._wait_done()

        self.assertEqual(len(runner.specs), 2)
        resumed_messages = runner.specs[1].initial_messages
        # The resumed run continues the same conversation, work included...
        self.assertIn({"role": "assistant", "content": "step 1 done"}, resumed_messages)
        # ...without the error placeholder the failed run left at its end.
        self.assertNotIn(_PERSISTED_MODEL_ERROR_PLACEHOLDER, json.dumps(resumed_messages))
        announcements = self._announcements()
        self.assertTrue(any("all done" in text for text in announcements), announcements)
        self.assertFalse(any("encountered an error" in text for text in announcements), announcements)

    async def test_resumes_stop_after_the_limit_and_the_failure_names_the_silent_model(self) -> None:
        runner = _ResumingRunner(failures=10)
        with (
            mock.patch.object(subagent_module, "_MODEL_RESUME_DELAYS_S", (0.0, 0.0, 0.0)),
            mock.patch.dict("os.environ", {"NAVIN_SUBAGENT_MODEL_RESUMES": "2"}),
        ):
            await self._spawn(runner)
            await self._wait_done()

        self.assertEqual(len(runner.specs), 3)  # first run + 2 resumes
        failure = next(text for text in self._announcements() if "stopped answering" in text)
        self.assertIn("resumed this subagent 2 time(s)", failure)
        self.assertNotIn("Sorry, I encountered an error calling the AI model", failure)

    async def test_a_refusal_is_not_resumed(self) -> None:
        runner = _RefusingRunner()
        with mock.patch.object(subagent_module, "_MODEL_RESUME_DELAYS_S", (0.0, 0.0, 0.0)):
            await self._spawn(runner)
            await self._wait_done()

        self.assertEqual(runner.calls, 1)
        self.assertTrue(any("Authentication" in text for text in self._announcements()))

    async def test_resuming_can_be_turned_off(self) -> None:
        runner = _ResumingRunner(failures=1)
        with (
            mock.patch.object(subagent_module, "_MODEL_RESUME_DELAYS_S", (0.0,)),
            mock.patch.dict("os.environ", {"NAVIN_SUBAGENT_MODEL_RESUMES": "0"}),
        ):
            await self._spawn(runner)
            await self._wait_done()

        self.assertEqual(len(runner.specs), 1)

    def test_placeholder_is_removed_once_whatever_the_tail(self) -> None:
        tail = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]
        placeholder = {"role": "assistant", "content": _PERSISTED_MODEL_ERROR_PLACEHOLDER}
        self.assertEqual(without_model_error_placeholder([*tail, placeholder]), tail)
        self.assertEqual(without_model_error_placeholder(tail), tail)
        self.assertEqual(without_model_error_placeholder([]), [])


class TimeLimitTest(_ManagerCase):
    async def test_the_wrapup_note_is_injected_once_after_the_delay(self) -> None:
        runner = _StuckRunner()
        with mock.patch.dict("os.environ", {"NAVIN_SUBAGENT_WRAPUP_MIN": "0.0005", "NAVIN_SUBAGENT_MAX_MIN": "0"}):
            await self._spawn(runner)
            deadline = asyncio.get_running_loop().time() + 10.0
            while runner.spec is None and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            self.assertIsNotNone(runner.spec)
            await asyncio.sleep(0.1)  # past the 30 ms wrap-up delay
            first = await runner.spec.injection_callback(limit=8, wait=False)
            second = await runner.spec.injection_callback(limit=8, wait=False)
            runner.release.set()
            await self._wait_done()

        self.assertEqual(len(first), 1)
        self.assertIn("write your final report now", first[0]["content"])
        self.assertEqual(second, [])

    async def test_the_time_limit_stops_the_run_and_names_the_reason(self) -> None:
        runner = _StuckRunner()
        with (
            mock.patch.dict("os.environ", {"NAVIN_SUBAGENT_MAX_MIN": "0.001", "NAVIN_SUBAGENT_WRAPUP_MIN": "0"}),
            mock.patch.object(subagent_module, "_HEARTBEAT_S", 0.02),
        ):
            await self._spawn(runner)
            await self._wait_done()

        statuses = list(self.manager._task_statuses.values())
        announcements = self._announcements()
        stopped = next((text for text in announcements if "time limit" in text), None)
        self.assertIsNotNone(stopped, announcements)
        self.assertIn("partial work is kept", stopped)
        self.assertFalse(runner.release.is_set())
        if statuses:
            self.assertEqual(statuses[0].error_kind, "limit")

    def test_time_waiting_for_the_users_approval_does_not_count(self) -> None:
        import time

        class _Task:
            def __init__(self) -> None:
                self.cancelled_with: list[str] = []

            def done(self) -> bool:
                return False

            def cancel(self, msg: str) -> None:
                self.cancelled_with.append(msg)

        manager = SubagentManager.__new__(SubagentManager)
        task = _Task()
        manager._running_tasks = {"t": task}
        manager._time_limited = {}
        manager._publish_progress = lambda *args, **kwargs: None
        status = SubagentStatus(task_id="t", label="L", task_description="d", started_at=0.0, phase="awaiting_tools")
        now = time.monotonic()
        status.run_started_at = now - 100 * 60
        # 50 of those 100 minutes were spent waiting for the user's approval.
        waiter = manager._approval_waiter(status)
        with mock.patch.object(subagent_module.time, "monotonic", side_effect=[now - 70 * 60, now - 20 * 60]):
            waiter(True)
            self.assertEqual(status.phase, "awaiting_approval")
            waiter(False)
        self.assertAlmostEqual(status.paused_s, 50 * 60, delta=1)
        manager._enforce_time_limit(status, now)
        self.assertEqual(task.cancelled_with, [])  # 50 min of run time, under 90
        status.paused_s = 0.0
        manager._enforce_time_limit(status, now)
        self.assertEqual(task.cancelled_with, ["time limit of 90 min reached"])
        manager._enforce_time_limit(status, now)
        self.assertEqual(len(task.cancelled_with), 1)  # stopped once

    def test_time_limit_reads_as_its_own_reason(self) -> None:
        self.assertEqual(
            _humanize_error("time limit of 90 min reached", "limit"), ("time_limit", "Reached its time limit"),
        )
        self.assertEqual(_humanize_error("anything", "limit"), ("limit", "Reached its step limit"))

    def test_the_card_shows_how_long_the_model_has_been_silent(self) -> None:
        status = SubagentStatus(
            task_id="t", label="L", task_description="d", started_at=0.0,
            phase="awaiting_model",
        )
        import time

        status.phase_since = time.monotonic() - 3 * 60 - 5
        self.assertEqual(SubagentManager._status_line(status), "Waiting for the model (3 min)")
        status.phase_since = time.monotonic()
        self.assertEqual(SubagentManager._status_line(status), "Waiting for model response...")
        status.waiting_note = "Model not responding, resuming in 15s (1/3)"
        self.assertEqual(SubagentManager._status_line(status), status.waiting_note)

    def test_limits_follow_the_environment(self) -> None:
        with mock.patch.dict("os.environ", {"NAVIN_SUBAGENT_MAX_MIN": "0"}):
            self.assertIsNone(subagent_module._limit_seconds("NAVIN_SUBAGENT_MAX_MIN", 90.0))
        with mock.patch.dict("os.environ", {"NAVIN_SUBAGENT_MAX_MIN": "30"}):
            self.assertEqual(subagent_module._limit_seconds("NAVIN_SUBAGENT_MAX_MIN", 90.0), 1800.0)
        with mock.patch.dict("os.environ", {"NAVIN_SUBAGENT_MAX_MIN": "soon"}):
            self.assertEqual(subagent_module._limit_seconds("NAVIN_SUBAGENT_MAX_MIN", 90.0), 5400.0)
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("NAVIN_SUBAGENT_MAX_MIN", None)
            self.assertEqual(subagent_module._limit_seconds("NAVIN_SUBAGENT_MAX_MIN", 90.0), 5400.0)


class UserFacingTimeoutTest(unittest.TestCase):
    def test_a_timeout_is_named_and_still_allows_a_model_switch(self) -> None:
        for raw in (
            "Error calling LLM: timed out after 300s",
            "Error calling LLM: request timed out after 120s",
            "Error calling LLM: stream stalled",
            "httpx.ReadTimeout: The read operation timed out",
        ):
            text = user_facing_llm_error(raw)
            self.assertIn("stopped answering", text, raw)
            self.assertNotIn("Sorry, I encountered", text)
            self.assertTrue(content_allows_model_switch(text), raw)

    def test_a_timeout_setting_mentioned_in_prose_is_not_a_timeout(self) -> None:
        self.assertNotIn("stopped answering", user_facing_llm_error("Error: invalid api key for timeout tests"))


class ModelErrorLogTest(unittest.TestCase):
    @staticmethod
    def _run_and_read_log(response: LLMResponse) -> tuple[AgentRunResult, list[dict]]:
        """One runner step on a model that answers ``response``, and the log it leaves."""
        from navin.agent.hook import AgentHook
        from navin.agent.runner import AgentRunner, AgentRunSpec
        from navin.agent.tools.registry import ToolRegistry
        from navin.providers.base import GenerationSettings
        from navin.utils.llm_runtime import LLMRuntime

        class _FailingProvider:
            async def chat_with_retry(self, **kwargs) -> LLMResponse:
                return response

            async def chat_stream_with_retry(self, **kwargs) -> LLMResponse:
                return response

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-errors.jsonl"
            with mock.patch.object(model_error_log, "log_path", lambda: path):
                result = asyncio.run(AgentRunner().run(AgentRunSpec(
                    initial_messages=[{"role": "user", "content": "go"}],
                    tools=ToolRegistry(),
                    runtime=LLMRuntime(
                        provider=_FailingProvider(),
                        model="anthropic/claude-opus-5.5",
                        generation=GenerationSettings(),
                        context_window_tokens=128_000,
                    ),
                    max_iterations=4,
                    max_tool_result_chars=4000,
                    hook=AgentHook(),
                    session_key="cli:42",
                )))
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return result, rows

    def test_a_runner_step_ending_on_a_model_error_is_logged(self) -> None:
        # A model that never answers in time, as the gateway reports it.
        result, rows = self._run_and_read_log(_timeout_response())

        self.assertEqual(result.stop_reason, "error")
        self.assertTrue(result.retryable_error)
        self.assertIn("stopped answering", result.final_content or "")
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0]["session"], rows[0]["model"], rows[0]["error_kind"], rows[0]["retryable"]),
            ("cli:42", "anthropic/claude-opus-5.5", "timeout", True),
        )

    def test_a_refusal_shown_as_the_generic_sentence_is_logged_with_the_provider_words(self) -> None:
        # What the gateway sends for a refused request: a dict body, which the
        # chat can only show as the generic sentence (seen 2026-11-06 13:59).
        said = (
            "litellm.BadRequestError: BedrockException - The model returned the following errors: "
            "messages.13.content.36: Invalid signature in thinking block."
        )
        result, rows = self._run_and_read_log(LLMResponse(
            content=user_facing_llm_error("Error: {'message': '" + said + "', 'code': 400}"),
            finish_reason="error",
            error_status_code=400,
            error_detail=said,
            error_provider="Navin",
        ))

        self.assertIn("Sorry, I encountered an error calling the AI model.", result.final_content or "")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"], rows[0]["retryable"], rows[0]["provider"]), (400, False, "Navin"))
        self.assertIn("Invalid signature in thinking block", rows[0]["detail"])
        self.assertNotIn("Sorry, I encountered", rows[0]["detail"])

    def test_failures_are_logged_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-errors.jsonl"
            with mock.patch.object(model_error_log, "log_path", lambda: path):
                ok = model_error_log.record_model_error({
                    "session": "cli:1",
                    "model": "anthropic/claude-opus-5.5",
                    "error_kind": "timeout",
                    "status": None,
                    "retryable": True,
                    "detail": "401 for key sk-abcdefghijklmnop1234 Bearer eyJhbGciOiJIUzI1NiJ9.payload api_key=supersecretvalue",
                })
                self.assertTrue(ok)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row["session"], row["error_kind"], row["retryable"]), ("cli:1", "timeout", True))
            self.assertNotIn("status", row)
            self.assertNotIn("abcdefghijklmnop1234", row["detail"])
            self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", row["detail"])
            self.assertNotIn("supersecretvalue", row["detail"])
            self.assertIn("sk-***", row["detail"])
            self.assertIn("Bearer ***", row["detail"])

    def test_an_unwritable_log_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "a-file"
            blocked.write_text("x", encoding="utf-8")
            with mock.patch.object(model_error_log, "log_path", lambda: blocked / "model-errors.jsonl"):
                self.assertFalse(model_error_log.record_model_error({"model": "m", "detail": "d"}))


class RelayEffortTest(unittest.TestCase):
    def test_a_model_taking_over_runs_at_its_deepest_effort_and_a_flagship_peer_at_the_turns(self) -> None:
        from navin.providers.relay_ladder import relay_candidates

        candidates = relay_candidates(
            "anthropic/claude-opus-5.5",
            [
                ("claude-fable-5-1", "anthropic/claude-fable-5.1"),
                ("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5"),
                ("gpt-6-astra", "openai/gpt-6-astra"),
            ],
        )
        # The user's order and efforts (2026-11-06): Fable at the turn's
        # effort, Astra at xhigh, Sonnet at max.
        self.assertEqual(
            [(candidate.model, candidate.reasoning_effort) for candidate in candidates],
            [
                ("anthropic/claude-fable-5.1", None),
                ("openai/gpt-6-astra", "xhigh"),
                ("anthropic/claude-sonnet-5.5", "max"),
            ],
        )

    def test_an_effort_picked_in_the_chat_is_kept_by_the_model_taking_over(self) -> None:
        from navin.config.schema import ModelPresetConfig
        from navin.providers.fallback_provider import FallbackProvider
        from navin.providers.relay_ladder import chat_chosen_effort

        class _Provider:
            def __init__(self, response: LLMResponse) -> None:
                self._response = response
                self.efforts: list = []

            def get_default_model(self) -> str:
                return "stub"

            async def chat(self, **kwargs) -> LLMResponse:
                self.efforts.append(kwargs.get("reasoning_effort"))
                return self._response

        primary = _Provider(LLMResponse(content="Error: 503 service unavailable", finish_reason="error", error_status_code=503))
        substitute = _Provider(LLMResponse(content="hello", finish_reason="stop"))
        chain = FallbackProvider(
            primary=primary,
            fallback_presets=[ModelPresetConfig(model="anthropic/claude-sonnet-5.5", provider="navin", reasoning_effort="high")],
            provider_factory=lambda preset: substitute,
            sticky_retries=0,
        )

        async def _run(chosen: bool):
            with chat_chosen_effort(chosen):
                return await chain.chat(model="anthropic/claude-opus-5.5", messages=[], reasoning_effort="xhigh")

        asyncio.run(_run(True))
        self.assertEqual(substitute.efforts[-1], "xhigh")  # the user's pick stays
        asyncio.run(_run(False))
        self.assertEqual(substitute.efforts[-1], "high")  # the preset's default applies

    def test_default_team_runs_at_high(self) -> None:
        from navin.collab.team import DEFAULT_MEMBER_MODELS

        self.assertEqual({effort for _preset, effort in DEFAULT_MEMBER_MODELS.values()}, {"high"})

    def test_a_saved_team_at_the_old_max_moves_to_high_but_a_picked_effort_stays(self) -> None:
        from navin.config.schema import CollabConfig

        saved = CollabConfig.model_validate({
            "toolsVersion": 6,
            "members": [
                {"id": "michel", "name": "Michel", "role": "architect", "modelPreset": "gpt-6-astra", "reasoningEffort": "max"},
                {"id": "mark", "name": "Mark", "role": "developer", "modelPreset": "claude-opus-5-5", "reasoningEffort": "xhigh"},
                {"id": "salma", "name": "Salma", "role": "tester", "modelPreset": "gpt-6-1-sol", "reasoningEffort": "low"},
                {"id": "lea", "name": "Lea", "role": "seo", "modelPreset": "kimi-k3", "reasoningEffort": "max"},
            ],
        })
        by_id = {member.id: member for member in saved.members}
        self.assertEqual(by_id["michel"].reasoning_effort, "high")
        self.assertEqual(by_id["mark"].reasoning_effort, "high")
        self.assertEqual(by_id["salma"].reasoning_effort, "low")  # the user's pick
        self.assertEqual(by_id["lea"].reasoning_effort, "max")  # not on its built-in model
        self.assertEqual(saved.tools_version, 7)

    def test_the_chat_pick_travels_with_the_runtime(self) -> None:
        from navin.providers.base import GenerationSettings
        from navin.utils.llm_runtime import LLMRuntime

        runtime = LLMRuntime(provider=mock.Mock(), model="m", generation=GenerationSettings(), context_window_tokens=1000)
        self.assertFalse(runtime.reasoning_effort_chosen)
        picked = runtime.with_generation_overrides(reasoning_effort="xhigh", reasoning_effort_chosen=True)
        self.assertTrue(picked.reasoning_effort_chosen)
        self.assertEqual(picked.generation.reasoning_effort, "xhigh")
        # A later override of the effort alone keeps the mark.
        self.assertTrue(picked.with_generation_overrides(reasoning_effort="low").reasoning_effort_chosen)


@pytest.mark.parametrize("provider", ["navin", "bedrock", "litellm"])
@pytest.mark.parametrize("named_agent", [False, True])
def test_a_subagent_goes_down_the_same_ladder_as_the_main_agent(tmp_path, monkeypatch, named_agent, provider):
    """A spawned agent, on the parent's model or on its own, gets the factory's chain."""
    from navin.agent.model_presets import make_preset_snapshot_loader
    from navin.agent.model_runtime import ModelRuntimeResolver
    from navin.agent.tools.context import RequestContext, request_context
    from navin.agent.tools.spawn import SpawnTool
    from navin.config.schema import Config, ModelPresetConfig, ProviderConfig
    from navin.providers.base import GenerationSettings
    from navin.providers.factory import build_provider_snapshot
    from navin.providers.fallback_provider import FallbackProvider
    from navin.utils.llm_runtime import runtime_from_provider_snapshot
    from tests.test_subagent_delivery_guards import _manager, _stop

    config = Config()
    config.providers.navin.api_key = "test-key"
    if provider == "litellm":
        config.providers.litellm = ProviderConfig(api_base="https://gateway.example/v1")
    models = {
        "parent": "anthropic/claude-opus-5.5",
        "specialist": "anthropic/claude-fable-5.1",
        "second": "openai/gpt-6-astra",
        "third": "anthropic/claude-sonnet-5.5",
    }
    config.model_presets = {name: ModelPresetConfig(model=model, provider=provider) for name, model in models.items()}
    monkeypatch.setattr("navin.providers.factory._preset_is_usable", lambda *_: True)
    calls: list[tuple[str, str | None]] = []

    class Provider:
        generation = GenerationSettings()

        def get_default_model(self):
            return models["parent"]

        async def chat(self, **kwargs):
            calls.append((kwargs["model"], kwargs.get("reasoning_effort")))
            if kwargs["model"] != models["third"]:
                # A definitive refusal: the chain moves on without a retry.
                return LLMResponse(content="unavailable", finish_reason="error", error_status_code=404)
            return LLMResponse(content="delegated work completed")

    monkeypatch.setattr("navin.providers.factory._make_provider_core", lambda *a, **kw: Provider())
    # Refusals are remembered process-wide: start clean, leak nothing.
    monkeypatch.setattr("navin.providers.fallback_provider._REFUSED_UNTIL", {})
    parent = runtime_from_provider_snapshot(build_provider_snapshot(config, preset_name="parent"))
    resolver = ModelRuntimeResolver(
        parent, model_presets=config.model_presets,
        preset_snapshot_loader=make_preset_snapshot_loader(config, None),
    )
    seen = []

    class Runner:
        async def run(self, spec):
            seen.append(spec.runtime)
            response = await spec.runtime.provider.chat(
                messages=[{"role": "user", "content": "Work"}],
                model=spec.runtime.model,
                temperature=spec.runtime.generation.temperature,
            )
            assert response.content == "delegated work completed"
            return AgentRunResult(final_content=response.content, messages=[])

    async def run():
        manager = _manager(tmp_path, monkeypatch, runner=Runner())
        tool = SpawnTool(manager, resolve_model_preset=resolver.resolve_preset)
        monkeypatch.setattr(tool, "_agent_model_preset", lambda _: "specialist")
        try:
            with request_context(RequestContext(channel="cli", chat_id="one", session_key="cli:one", runtime=parent)):
                result = await tool.execute(
                    task="Work", agent="specialist" if named_agent else None, temperature=0.2,
                )
            assert "started" in result
            await asyncio.gather(*list(manager._running_tasks.values()))
        finally:
            await _stop(manager, "cli:one")

    asyncio.run(run())
    assert len(seen) == 1
    runtime = seen[0]
    assert isinstance(runtime.provider, FallbackProvider)
    assert runtime.generation.temperature == 0.2
    assert resolver.current() is parent  # resolving a worker never changes the parent's pick
    if not named_agent:
        assert runtime.provider is parent.provider
    primary, peer = ("specialist", "parent") if named_agent else ("parent", "specialist")
    # The flagship peer at the step's effort, then Astra at xhigh and Sonnet at max.
    assert calls == [
        (models[primary], None),
        (models[peer], None),
        (models["second"], "xhigh"),
        (models["third"], "max"),
    ]


if __name__ == "__main__":
    unittest.main()
