# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run one battery case in a sandbox and turn it into trajectory steps (S4.1).

The environment is the real thing: ``AgentRunner`` with the real filesystem
tools (plus the two fixture tools for the browser and desk suites) in a
throwaway folder, driven by the case's scripted tool calls. Nothing here
touches the project's workspace, the gateway or the chat model.

Per step the hook records what was called and what came back; after the run
the eval check (``navin.evals.agent_loop._check_case``) decides the reward
of the whole episode (1.0 passed, 0.0 failed), copied on every step. The
final answer is a step too (``stop`` or ``ask``), so the head can learn when
to stop calling tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.agent.hook import AgentHook, AgentHookContext
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tool_surface import denied_tools_for_composer_mode
from navin.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.search import FindFilesTool, GrepTool
from navin.evals.agent_loop import AgentLoopCase, ScriptedProvider, _check_case
from navin.policy.battery import PolicyCase
from navin.policy.sandbox_tools import FixtureBrowserTool, FixtureDeskTool
from navin.policy.trajectory import StateKey, Step, build_step, final_action, intent_of
from navin.providers.base import GenerationSettings, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime
from navin.world_model.trajectory import HISTORY, MAX_OBS_SCAN_CHARS, observation_text

S3Predictor = Callable[[str, Any], str | None]
"""``(tool_name, arguments) -> observation class`` from the project's world model, or None."""


@dataclass(slots=True)
class _Observed:
    tool: str
    arguments: Any
    result_head: str
    is_error: bool
    is_timeout: bool
    read_only: bool | None


class _StepHook(AgentHook):
    """Record each tool call and its answer, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.observed: list[_Observed] = []

    def _note(self, tool_call: ToolCallRequest, tool: Any, params: Any, outcome: Any, *, is_error: bool) -> None:
        read_only: bool | None = None
        if tool is not None:
            try:
                checker = getattr(tool, "call_read_only", None)
                read_only = bool(checker(params)) if callable(checker) else bool(getattr(tool, "read_only", False))
            except Exception:  # noqa: BLE001 - a tool's opinion must not break the eval
                read_only = None
        arguments = dict(params) if isinstance(params, dict) else tool_call.arguments
        self.observed.append(
            _Observed(
                tool=str(tool_call.name or ""),
                arguments=arguments,
                result_head=observation_text(outcome)[:MAX_OBS_SCAN_CHARS],
                is_error=is_error,
                is_timeout=isinstance(outcome, TimeoutError),
                read_only=read_only,
            )
        )

    async def after_execute_tool(self, context: AgentHookContext, tool_call: ToolCallRequest, tool: Any, params: Any, result: Any) -> None:
        self._note(tool_call, tool, params, result, is_error=False)

    async def on_execute_tool_error(self, context: AgentHookContext, tool_call: ToolCallRequest, tool: Any, params: Any, error: Any) -> None:
        self._note(tool_call, tool, params, error, is_error=True)


@dataclass(slots=True)
class Episode:
    case_id: str
    suite: str
    split: str
    episode_id: str
    passed: bool
    reward: float
    missing: list[str]
    steps: list[Step]
    duration_ms: int
    final: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "suite": self.suite,
            "split": self.split,
            "episode": self.episode_id,
            "passed": self.passed,
            "reward": self.reward,
            "missing": list(self.missing),
            "steps": len(self.steps),
            "actions": [step.action for step in self.steps],
            "duration_ms": self.duration_ms,
        }


def sandbox_registry(workspace: Path) -> ToolRegistry:
    """Real filesystem tools plus the fixture web and desk, bound to the sandbox."""
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(workspace=workspace),
        WriteFileTool(workspace=workspace),
        EditFileTool(workspace=workspace),
        ListDirTool(workspace=workspace),
        GrepTool(workspace=workspace),
        FindFilesTool(workspace=workspace),
        FixtureBrowserTool(workspace=workspace),
        FixtureDeskTool(workspace=workspace),
    ):
        registry.register(tool)
    return registry


def _spec(case: PolicyCase, workspace: Path, hook: AgentHook) -> AgentRunSpec:
    mode = case.composer_mode.strip().lower() or "agent"
    runtime = LLMRuntime(
        provider=ScriptedProvider(case.script),
        model="policy-eval-scripted",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": case.prompt}],
        tools=sandbox_registry(workspace),
        runtime=runtime,
        max_iterations=max(len(case.script) + 4, 8),
        max_tool_result_chars=16_000,
        hook=hook,
        composer_mode=mode,
        plan_read_only=(mode == "plan"),
        read_only_tools=(mode == "ask"),
        denied_tools=denied_tools_for_composer_mode(mode),
    )


def _run_sync(coro: Any) -> Any:
    """Run a coroutine from sync code, even when a loop runs in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            box["error"] = exc

    worker = threading.Thread(target=_target, name="navin-policy-episode", daemon=True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def episode_id_for(battery_version: str, case_id: str, run_id: str) -> str:
    return f"{battery_version}|{case_id}|{run_id}"


def run_episode(
    case: PolicyCase,
    *,
    battery_version: str,
    run_id: str,
    s3_predict: S3Predictor | None = None,
    now: float | None = None,
) -> Episode:
    """Execute one case in a fresh sandbox; return the graded episode."""
    started = time.monotonic()
    hook = _StepHook()
    tmp = tempfile.mkdtemp(prefix="navin-policy-eval-")
    workspace = Path(tmp)
    try:
        for rel, content in case.workspace.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        result = _run_sync(AgentRunner().run(_spec(case, workspace, hook)))
        loop_case = AgentLoopCase(
            id=case.id,
            prompt=case.prompt,
            category=case.suite,
            composer_mode=case.composer_mode,
            workspace_files=case.workspace,
            script=case.script,
            expect=case.expect,
        )
        missing = _check_case(loop_case, result, workspace)
        final = str(getattr(result, "final_content", "") or "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    reward = 0.0 if missing else 1.0
    episode = episode_id_for(battery_version, case.id, run_id)
    intent = intent_of(case.prompt)
    steps: list[Step] = []
    prev: list[str] = []
    for index, seen in enumerate(hook.observed):
        s3: str | None = None
        if s3_predict is not None:
            try:
                s3 = s3_predict(seen.tool, seen.arguments)
            except Exception:  # noqa: BLE001 - the radar is a feature, never a failure
                s3 = None
        outcome: Any = TimeoutError(seen.result_head) if seen.is_timeout else seen.result_head
        step = build_step(
            episode=episode,
            suite=case.suite,
            case=case.id,
            split=case.split,
            step=index,
            intent=intent,
            prev=prev,
            s3=s3,
            action=seen.tool,
            arguments=seen.arguments,
            result=outcome,
            is_error=seen.is_error,
            reward=reward,
            read_only=seen.read_only,
            now=now,
        )
        steps.append(step)
        prev = (prev + [f"{step.action}:{step.obs}"])[-HISTORY:]
    steps.append(
        build_step(
            episode=episode,
            suite=case.suite,
            case=case.id,
            split=case.split,
            step=len(hook.observed),
            intent=intent,
            prev=prev,
            s3=None,
            action=final_action(final),
            arguments=None,
            result=None,
            is_error=False,
            reward=reward,
            terminal=True,
            now=now,
        )
    )
    return Episode(
        case_id=case.id,
        suite=case.suite,
        split=case.split,
        episode_id=episode,
        passed=not missing,
        reward=reward,
        missing=missing,
        steps=steps,
        duration_ms=int((time.monotonic() - started) * 1000),
        final=final[:200],
    )


def new_run_id() -> str:
    return hashlib.sha1(f"{time.time_ns()}".encode()).hexdigest()[:6]


def run_cases(
    cases: Iterable[PolicyCase],
    *,
    battery_version: str,
    s3_predict: S3Predictor | None = None,
    on_episode: Callable[[Episode], None] | None = None,
    run_id: str | None = None,
) -> list[Episode]:
    """Run the cases one after another in fresh sandboxes."""
    run_id = run_id or new_run_id()
    episodes: list[Episode] = []
    for case in cases:
        episode = run_episode(case, battery_version=battery_version, run_id=run_id, s3_predict=s3_predict)
        episodes.append(episode)
        if on_episode is not None:
            on_episode(episode)
    return episodes


def state_of_step(record: dict[str, Any]) -> StateKey:
    return StateKey.from_record(record)


__all__ = [
    "Episode",
    "S3Predictor",
    "episode_id_for",
    "new_run_id",
    "run_cases",
    "run_episode",
    "sandbox_registry",
    "state_of_step",
]
