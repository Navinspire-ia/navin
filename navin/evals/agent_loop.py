# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live agent-loop evals: run the real AgentRunner with real tools.

The keyword eval (``navin.evals.runner``) scores canned text from a mock
model. This backend executes the actual agent loop against a fixture
workspace, so the release gate asserts what the product really does:

* which tool calls succeeded (``tool_events`` with status ``ok``),
* which calls the mode policy refused (Plan and Ask enforcement),
* what actually landed on disk in the workspace,
* how the turn stopped (``stop_reason``) and what the final answer said.

The model side stays deterministic offline: each case carries a ``script``
of the tool calls a competent model would make, replayed by a scripted
provider. Loop, policy, tool execution and filesystem effects are all real,
which is exactly the part the keyword eval could not certify.

Usage (CLI)::

    python -m navin.evals.agent_loop navin/evals/datasets/code_agent_live_v1.jsonl \
        --scoreboard --gate

JSONL case schema::

    {"id": "bugfix-live-01", "category": "bugfix",
     "prompt": "Fix the off-by-one in calc.py",
     "composer_mode": "agent",
     "workspace": {"calc.py": "..."},
     "script": [
        {"tool": "read_file", "args": {"path": "calc.py"}},
        {"tool": "edit_file", "args": {"path": "calc.py",
                                       "old_text": "...", "new_text": "..."}},
        {"final": "Fixed."}
     ],
     "expect": {"tools_ok": ["read_file", "edit_file"],
                "tools_blocked": ["write_file"],
                "files_contain": {"calc.py": ["return sum(xs)"]},
                "files_absent": ["notes.md"],
                "no_writes": false,
                "stop_reason": "completed",
                "final_contains": ["Fixed"]}}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tool_surface import denied_tools_for_composer_mode
from navin.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.search import FindFilesTool, GrepTool
from navin.evals.runner import (
    EvalResult,
    _print_report,
    _print_scoreboard,
    release_gate,
    scoreboard,
)
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime

_MUTATING_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "manage_files"})


@dataclass(frozen=True)
class AgentLoopCase:
    id: str
    prompt: str
    category: str | None = None
    composer_mode: str = "agent"
    workspace_files: Mapping[str, str] = field(default_factory=dict)
    script: tuple[Mapping[str, Any], ...] = ()
    expect: Mapping[str, Any] = field(default_factory=dict)


class ScriptedProvider:
    """Replay a fixed sequence of model turns, one tool call per turn.

    A final step (``{"final": "..."}``) stops the loop; if the script runs
    out the provider keeps answering the last stop response so the runner
    always terminates.
    """

    def __init__(self, script: Sequence[Mapping[str, Any]]) -> None:
        self._responses = [_response_for_step(step, idx) for idx, step in enumerate(script)]
        if not self._responses or self._responses[-1].finish_reason != "stop":
            self._responses.append(LLMResponse(content="done", finish_reason="stop"))
        self.calls = 0

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


def _response_for_step(step: Mapping[str, Any], idx: int) -> LLMResponse:
    if "final" in step:
        return LLMResponse(content=str(step["final"]), finish_reason="stop")
    tool = step.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError(f"script step {idx}: needs 'tool' or 'final'")
    args = step.get("args") or {}
    if not isinstance(args, Mapping):
        raise ValueError(f"script step {idx}: 'args' must be an object")
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[
            ToolCallRequest(id=f"call-{idx}", name=tool.strip(), arguments=dict(args))
        ],
    )


def _registry(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(workspace=workspace),
        WriteFileTool(workspace=workspace),
        EditFileTool(workspace=workspace),
        ListDirTool(workspace=workspace),
        GrepTool(workspace=workspace),
        FindFilesTool(workspace=workspace),
    ):
        registry.register(tool)
    return registry


def _spec_for_case(case: AgentLoopCase, workspace: Path) -> AgentRunSpec:
    mode = case.composer_mode.strip().lower() or "agent"
    runtime = LLMRuntime(
        provider=ScriptedProvider(case.script),
        model="eval-scripted",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": case.prompt}],
        tools=_registry(workspace),
        runtime=runtime,
        max_iterations=max(len(case.script) + 4, 8),
        max_tool_result_chars=16_000,
        hook=AgentHook(),
        composer_mode=mode,
        plan_read_only=(mode == "plan"),
        read_only_tools=(mode == "ask"),
        denied_tools=denied_tools_for_composer_mode(mode),
    )


def load_agent_dataset(path: Path) -> list[AgentLoopCase]:
    cases: list[AgentLoopCase] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        data = json.loads(line)
        prompt = data.get("prompt") or data.get("input")
        if not prompt:
            raise ValueError(f"{path}:{line_no}: missing prompt")
        script = data.get("script")
        if not isinstance(script, list) or not script:
            raise ValueError(f"{path}:{line_no}: missing script")
        workspace = data.get("workspace") or {}
        if not isinstance(workspace, Mapping):
            raise ValueError(f"{path}:{line_no}: workspace must be an object")
        expect = data.get("expect") or {}
        if not isinstance(expect, Mapping):
            raise ValueError(f"{path}:{line_no}: expect must be an object")
        cases.append(
            AgentLoopCase(
                id=str(data.get("id") or f"case-{line_no}"),
                prompt=str(prompt),
                category=str(data["category"]) if data.get("category") else None,
                composer_mode=str(data.get("composer_mode") or "agent"),
                workspace_files={str(k): str(v) for k, v in workspace.items()},
                script=tuple(script),
                expect=dict(expect),
            )
        )
    return cases


def _check_case(case: AgentLoopCase, result: Any, workspace: Path) -> list[str]:
    missing: list[str] = []
    expect = case.expect
    events = list(result.tool_events)

    for name in expect.get("tools_ok") or []:
        ok = any(e.get("name") == name and e.get("status") == "ok" for e in events)
        if not ok:
            missing.append(f"tool_ok:{name}")

    for name in expect.get("tools_blocked") or []:
        blocked = any(
            e.get("name") == name and "blocked" in str(e.get("detail") or "")
            for e in events
        )
        if not blocked:
            missing.append(f"tool_blocked:{name}")

    if expect.get("no_writes"):
        wrote = [
            e.get("name")
            for e in events
            if e.get("name") in _MUTATING_TOOLS and e.get("status") == "ok"
        ]
        if wrote:
            missing.append(f"no_writes violated by {wrote}")

    for rel, needles in (expect.get("files_contain") or {}).items():
        target = workspace / str(rel)
        if not target.is_file():
            missing.append(f"file_missing:{rel}")
            continue
        body = target.read_text(encoding="utf-8")
        for needle in needles if isinstance(needles, list) else [needles]:
            if str(needle) not in body:
                missing.append(f"file:{rel} missing {needle!r}")

    for rel in expect.get("files_absent") or []:
        if (workspace / str(rel)).exists():
            missing.append(f"file_should_be_absent:{rel}")

    wanted_stop = expect.get("stop_reason")
    if wanted_stop and result.stop_reason != wanted_stop:
        missing.append(f"stop_reason:{result.stop_reason!r} != {wanted_stop!r}")

    final = str(result.final_content or "")
    for needle in expect.get("final_contains") or []:
        if str(needle) not in final:
            missing.append(f"final missing {needle!r}")

    return missing


def run_agent_case(case: AgentLoopCase) -> EvalResult:
    with tempfile.TemporaryDirectory(prefix="navin-eval-") as tmp:
        workspace = Path(tmp)
        for rel, content in case.workspace_files.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        result = asyncio.run(AgentRunner().run(_spec_for_case(case, workspace)))
        missing = _check_case(case, result, workspace)
        return EvalResult(
            case_id=case.id,
            passed=not missing,
            output=str(result.final_content or ""),
            missing=missing,
            category=case.category,
        )


def run_agent_dataset(dataset_path: Path | str) -> list[EvalResult]:
    cases = load_agent_dataset(Path(dataset_path))
    return [run_agent_case(case) for case in cases]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Navin live agent-loop evals")
    parser.add_argument(
        "dataset",
        nargs="?",
        default=str(
            Path(__file__).resolve().parent / "datasets" / "code_agent_live_v1.jsonl"
        ),
        help="Path to a JSONL dataset",
    )
    parser.add_argument("--scoreboard", action="store_true", help="Print per-category rates")
    parser.add_argument("--gate", action="store_true", help="Fail below the release bar")
    parser.add_argument("--min-overall", type=float, default=1.0)
    parser.add_argument("--min-category", type=float, default=1.0)
    args = parser.parse_args(argv)

    results = run_agent_dataset(args.dataset)
    failed = _print_report(results)
    total = len(results)
    print(f"{total - failed}/{total} passed")
    board = scoreboard(results)
    if args.scoreboard or args.gate:
        _print_scoreboard(board)
    if args.gate:
        gate = release_gate(
            board,
            min_overall=args.min_overall,
            min_per_category=args.min_category,
        )
        if not gate.ok:
            for reason in gate.reasons:
                print(f"GATE FAIL: {reason}")
            return 1
        print("GATE PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
