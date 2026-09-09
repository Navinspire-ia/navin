# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Parent turn policy must follow delegated work into subagents.

Delegation was the policy escape hatch: a Build parent with a verify gate
or a module denylist could spawn a subagent that had neither. The chain
under test: the runner binds the spec's inheritable policy for the turn,
spawn() captures it at delegation time, and the subagent's own AgentRunSpec
carries the verify gate and the locked denials.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.run_policy import (
    TurnPolicy,
    bind_turn_policy,
    current_turn_policy,
    reset_turn_policy,
)
from navin.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from navin.agent.subagent import SubagentManager
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.bus.queue import MessageBus
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class _ProbeTool(Tool):
    """Records the turn policy visible while a tool executes."""

    def __init__(self) -> None:
        self.seen: list[TurnPolicy | None] = []

    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "records the bound policy"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        self.seen.append(current_turn_policy())
        return "ok"


class _OneToolProvider:
    """First call asks for the probe tool, second call finishes."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None, model=None, max_tokens=4096,
                   temperature=0.7, reasoning_effort=None, tool_choice=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="c1", name="probe", arguments={})],
            )
        return LLMResponse(content="done")

    async def chat_with_retry(self, **kwargs: Any):
        allowed = {
            "messages", "tools", "model", "max_tokens", "temperature",
            "reasoning_effort", "tool_choice",
        }
        return await self.chat(**{k: v for k, v in kwargs.items() if k in allowed})


def _runtime(provider: Any) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(temperature=0.2, max_tokens=512),
        context_window_tokens=100_000,
    )


class RunnerBindsPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_tools_see_the_spec_policy_during_the_turn(self) -> None:
        probe = _ProbeTool()
        registry = ToolRegistry()
        registry.register(probe)
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=registry,
            runtime=_runtime(_OneToolProvider()),
            max_iterations=4,
            max_tool_result_chars=10_000,
            requires_verify_before_done=True,
            locked_denied_tools=frozenset({"exec", "cron"}),
            allowed_tools=frozenset({"read_file", "verify", "probe"}),
        )
        await AgentRunner().run(spec)
        self.assertEqual(1, len(probe.seen))
        policy = probe.seen[0]
        assert policy is not None
        self.assertTrue(policy.requires_verify_before_done)
        self.assertEqual(frozenset({"exec", "cron"}), policy.locked_denied_tools)
        self.assertEqual(frozenset({"read_file", "verify", "probe"}), policy.allowed_tools)

    async def test_policy_is_reset_after_the_turn(self) -> None:
        registry = ToolRegistry()
        registry.register(_ProbeTool())
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=registry,
            runtime=_runtime(_OneToolProvider()),
            max_iterations=4,
            max_tool_result_chars=10_000,
            requires_verify_before_done=True,
        )
        await AgentRunner().run(spec)
        self.assertIsNone(current_turn_policy())


class SpawnCapturesPolicyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-subpol-")
        self.addCleanup(self.tmp.cleanup)
        self.manager = SubagentManager(
            workspace=Path(self.tmp.name),
            bus=MessageBus(),
            max_tool_result_chars=10_000,
        )

    async def test_spawn_captures_the_bound_policy(self) -> None:
        captured: dict[str, Any] = {}

        async def _fake_run(*args: Any, **kwargs: Any) -> None:
            # _run_subagent(self, task_id, task, label, origin, status,
            #               runtime, ..., inherited_policy) - policy is last.
            captured["policy"] = args[-1] if args else kwargs.get("inherited_policy")

        self.manager._run_subagent = _fake_run  # type: ignore[method-assign]
        bound = TurnPolicy(
            requires_verify_before_done=True,
            locked_denied_tools=frozenset({"montage"}),
        )
        token = bind_turn_policy(bound)
        try:
            await self.manager.spawn(
                "audit the code",
                runtime=_runtime(_OneToolProvider()),
                session_key="websocket:pol-test",
            )
        finally:
            reset_turn_policy(token)
        # Let the fire-and-forget task run.
        import asyncio

        await asyncio.sleep(0.05)
        self.assertEqual(bound, captured.get("policy"))

    async def test_without_a_bound_policy_the_default_travels(self) -> None:
        captured: dict[str, Any] = {}

        async def _fake_run(*args: Any, **kwargs: Any) -> None:
            captured["policy"] = args[-1] if args else kwargs.get("inherited_policy")

        self.manager._run_subagent = _fake_run  # type: ignore[method-assign]
        await self.manager.spawn(
            "task", runtime=_runtime(_OneToolProvider()), session_key="websocket:x"
        )
        import asyncio

        await asyncio.sleep(0.05)
        policy = captured.get("policy")
        assert isinstance(policy, TurnPolicy)
        self.assertFalse(policy.requires_verify_before_done)
        self.assertEqual(frozenset(), policy.locked_denied_tools)


class SubagentSpecCarriesPolicyTest(unittest.IsolatedAsyncioTestCase):
    """The end of the chain: the child AgentRunSpec enforces what traveled."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="navin-subpol-")
        self.addCleanup(self.tmp.cleanup)
        self.manager = SubagentManager(
            workspace=Path(self.tmp.name),
            bus=MessageBus(),
            max_tool_result_chars=10_000,
        )

    async def test_child_spec_carries_verify_gate_and_denials(self) -> None:
        specs: list[AgentRunSpec] = []

        async def _capture_run(spec: AgentRunSpec) -> AgentRunResult:
            specs.append(spec)
            return AgentRunResult(final_content="done", messages=[])

        self.manager.runner.run = _capture_run  # type: ignore[method-assign]
        from navin.agent.subagent import SubagentStatus

        status = SubagentStatus(
            task_id="t1",
            label="lab",
            task_description="task",
            started_at=0.0,
        )
        await self.manager._run_subagent(
            "t1",
            "do the delegated task",
            "lab",
            {"channel": "cli", "chat_id": "direct", "session_key": "cli:direct"},
            status,
            _runtime(_OneToolProvider()),
            inherited_policy=TurnPolicy(
                requires_verify_before_done=True,
                locked_denied_tools=frozenset({"exec", "montage"}),
                allowed_tools=frozenset({"read_file", "apply_patch"}),
            ),
        )
        self.assertEqual(1, len(specs))
        spec = specs[0]
        self.assertTrue(spec.requires_verify_before_done)
        self.assertIn("exec", spec.locked_denied_tools)
        self.assertIn("montage", spec.locked_denied_tools)
        # Runtime denial, not only the lock: the tool schema and the runner
        # gate both consult denied_tools.
        self.assertIn("exec", spec.denied_tools)
        self.assertEqual(frozenset({"read_file", "apply_patch"}), spec.allowed_tools)


if __name__ == "__main__":
    unittest.main()
