# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""S5.2 - the transfer campaign: an isolated job that runs the secret suites
against the real model with the real tools, and lets the protocol answer.

Isolation, item after item:

* a fresh throwaway folder with the item's fixture files; the real
  filesystem and search tools plus the fixture web and desk tools; no shell;
* the configured provider (the LLM is the reasoning rail; without it S5 is
  lost by design) behind one plain system message: **no skill, no playbook,
  no recall, no steer, no policy or world hook**;
* a budget: tool calls, wall clock, tokens. Past any of them the item fails;
* the S4 trajectory files are counted before and after: the campaign never
  writes a training line.

Scoring: pass rate per family against the family's bar; one family under
its bar (or collapsed, or too small) stops the campaign there. Families are
never averaged. The record is replayable: same items, same seeds.

The campaign runs in a child process (``navin.transfer.campaign_job``) from
the panel and the CLI; tests run it inline with a scripted runtime.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.hook import AgentHook, AgentHookContext
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tool_surface import denied_tools_for_composer_mode
from navin.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.search import FindFilesTool, GrepTool
from navin.evals.agent_loop import AgentLoopCase, _check_case
from navin.policy.sandbox_tools import FixtureBrowserTool, FixtureDeskTool
from navin.providers.base import ToolCallRequest
from navin.transfer.journal import append_campaign, journal, now_stamp, read_campaigns
from navin.transfer.prereqs import prereqs
from navin.transfer.protocol import (
    DEFAULT_BUDGET,
    FAMILY_IDS,
    PROTOCOL_VERSION,
    SYSTEM_PROMPT,
    ItemBudget,
    bar_for,
    family_verdict,
)
from navin.transfer.settings import read_settings
from navin.transfer.suites import (
    SuitesError,
    SuitesLock,
    SuitesTamperedError,
    TransferItem,
    check_outside,
    contamination,
    load_suites,
    suites_dir_for,
    verify,
)
from navin.utils.llm_runtime import LLMRuntime

HUMAN = "human"
_CHILD_GRACE_S = 120.0

RuntimeFactory = Callable[[TransferItem], LLMRuntime]
"""Builds the runtime (provider + model) an item runs with. The default is the configured provider."""


class _CallCounter(AgentHook):
    """Count tool calls; nothing else. The runner enforces the ceiling."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def after_execute_tool(self, context: AgentHookContext, tool_call: ToolCallRequest, tool: Any, params: Any, result: Any) -> None:
        self.calls += 1

    async def on_execute_tool_error(self, context: AgentHookContext, tool_call: ToolCallRequest, tool: Any, params: Any, error: Any) -> None:
        self.calls += 1


@dataclass(slots=True)
class ItemResult:
    id: str
    family: str
    seed: int
    passed: bool
    missing: list[str]
    tool_calls: int
    tokens: int
    duration_ms: int
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "seed": self.seed,
            "passed": self.passed,
            "missing": list(self.missing)[:8],
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
        }


@dataclass(slots=True)
class FamilyScore:
    family: str
    items: int
    passed: int
    bar: float
    verdict: str
    results: list[ItemResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float | None:
        return (self.passed / self.items) if self.items else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "items": self.items,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "bar": self.bar,
            "verdict": self.verdict,
            "results": [r.as_dict() for r in self.results],
        }


def campaign_registry(workspace: Path) -> ToolRegistry:
    """Real file and search tools confined to the sandbox (restrict on, no
    approver around, so outside is a refusal), the fixture web and desk, no
    shell, no recall, no policy_next, no world_predict."""
    registry = ToolRegistry()
    confined = {"workspace": workspace, "allowed_dir": workspace, "restrict_to_workspace": True}
    for tool in (
        ReadFileTool(**confined),
        WriteFileTool(**confined),
        EditFileTool(**confined),
        ListDirTool(**confined),
        GrepTool(**confined),
        FindFilesTool(**confined),
        FixtureBrowserTool(workspace=workspace),
        FixtureDeskTool(workspace=workspace),
    ):
        registry.register(tool)
    return registry


def default_runtime_factory() -> RuntimeFactory:
    """The configured provider, loaded once, temperature as configured."""
    from navin.providers.factory import load_provider_snapshot
    from navin.utils.llm_runtime import runtime_from_provider_snapshot

    snapshot = load_provider_snapshot()
    runtime = runtime_from_provider_snapshot(snapshot)

    def factory(item: TransferItem) -> LLMRuntime:
        return runtime

    return factory


def _run_sync(coro: Any, timeout_s: float) -> Any:
    """Run a coroutine under a wall clock from sync code, whatever the thread."""

    async def bounded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout_s)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bounded())
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(bounded())
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            box["error"] = exc

    worker = threading.Thread(target=_target, name="navin-transfer-item", daemon=True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def run_item(item: TransferItem, runtime: LLMRuntime, *, budget: ItemBudget = DEFAULT_BUDGET) -> ItemResult:
    """One item in a fresh sandbox: no skill, no recall, no steer, a budget."""
    started = time.monotonic()
    counter = _CallCounter()
    tmp = tempfile.mkdtemp(prefix="navin-transfer-")
    workspace = Path(tmp)
    reason: str | None = None
    missing: list[str] = []
    tokens = 0
    try:
        for rel, content in item.workspace.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        mode = item.composer_mode
        spec = AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": item.prompt},
            ],
            tools=campaign_registry(workspace),
            runtime=runtime,
            max_iterations=budget.max_tool_calls + 1,
            max_tool_result_chars=16_000,
            hook=counter,
            composer_mode=mode,
            plan_read_only=(mode == "plan"),
            read_only_tools=(mode == "ask"),
            denied_tools=denied_tools_for_composer_mode(mode),
            workspace=workspace,
        )
        try:
            result = _run_sync(AgentRunner().run(spec), budget.timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            result = None
            reason = f"budget: wall clock {budget.timeout_s:.0f}s"
        if result is not None:
            usage = result.usage if isinstance(result.usage, dict) else {}
            tokens = int(usage.get("total_tokens") or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)) or 0)
            if counter.calls > budget.max_tool_calls or result.stop_reason == "max_iterations":
                reason = f"budget: {counter.calls} tool calls (max {budget.max_tool_calls})"
            elif tokens > budget.max_tokens:
                reason = f"budget: {tokens} tokens (max {budget.max_tokens})"
            elif result.error:
                reason = f"run error: {str(result.error)[:160]}"
            else:
                loop_case = AgentLoopCase(
                    id=item.id,
                    prompt=item.prompt,
                    category=item.family,
                    composer_mode=mode,
                    workspace_files=item.workspace,
                    script=(),
                    expect=item.expect,
                )
                missing = _check_case(loop_case, result, workspace)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = reason is None and not missing
    return ItemResult(
        id=item.id,
        family=item.family,
        seed=item.seed,
        passed=passed,
        missing=missing if reason is None else [reason],
        tool_calls=counter.calls,
        tokens=tokens,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason=reason,
    )


def _s4_footprint(workspace: Path) -> dict[str, int]:
    """How many S4 training lines and cases exist: must not move during a campaign."""
    try:
        from navin.policy.journal import count_steps
        from navin.policy.paths import cases_path

        cases = 0
        path = cases_path(workspace)
        if path.is_file():
            cases = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        return {"steps": count_steps(workspace), "cases": cases}
    except Exception:  # noqa: BLE001 - S4 absent is fine
        return {"steps": 0, "cases": 0}


def campaign_id(now: float, suites_version: str) -> str:
    return hashlib.sha1(f"{now}|{suites_version}".encode()).hexdigest()[:8]


def _void(workspace: Path, *, actor: str, reason: str, status: str = "void") -> dict[str, Any]:
    journal(workspace, "campaign_refused", reason=reason[:500], actor=actor)
    return {"status": status, "verdict": None, "reason": reason}


def run_campaign(
    workspace: Path | str,
    *,
    actor: str = HUMAN,
    runtime_factory: RuntimeFactory | None = None,
    budget: ItemBudget = DEFAULT_BUDGET,
    replay_of: str | None = None,
    only_items: dict[str, list[str]] | None = None,
    on_item: Callable[[ItemResult], None] | None = None,
) -> dict[str, Any]:
    """Run the protocol once. Returns the campaign record (also appended to
    ``campaigns.jsonl``) or a refusal ``{"status": "skipped"|"refused"|"void"}``.

    Refused when the flag is off, when a prerequisite is missing, when the
    suites are misplaced or not frozen. Void when the suites changed since
    the lock or leaked into a skill, an episode or the S4 battery.
    """
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "verdict": None, "reason": "transfer protocol is off for this project"}
    if actor != HUMAN:
        return _void(workspace, actor=actor, reason="a campaign is started by a human", status="refused")
    gates = prereqs(workspace)
    if not gates.ok:
        return _void(workspace, actor=actor, reason="prerequisites not met: " + "; ".join(gates.reasons), status="refused")
    suites_dir = suites_dir_for(workspace)
    try:
        check_outside(suites_dir, workspace)
        lock: SuitesLock = verify(suites_dir)
        suites = load_suites(suites_dir)
    except SuitesTamperedError as exc:
        return _void(workspace, actor=actor, reason=str(exc))
    except SuitesError as exc:
        return _void(workspace, actor=actor, reason=str(exc), status="refused")
    every_item = [item for items in suites.values() for item in items]
    leaks = contamination(workspace, every_item)
    if leaks:
        return _void(workspace, actor=actor, reason=f"secret items leaked into this project: {leaks[:3]}")
    if runtime_factory is None:
        try:
            runtime_factory = default_runtime_factory()
        except Exception as exc:  # noqa: BLE001 - no provider = no reasoning rail
            return _void(workspace, actor=actor, reason=f"no configured provider: {exc}", status="refused")

    started = time.time()
    cid = campaign_id(started, lock.version)
    before = _s4_footprint(workspace)
    journal(workspace, "campaign_started", campaign=cid, suites_version=lock.version, actor=actor, replay_of=replay_of)
    scores: dict[str, FamilyScore] = {}
    stopped_at: str | None = None
    for family in FAMILY_IDS:
        items = suites.get(family, [])
        if only_items is not None:
            if family not in only_items:
                break  # a replay runs what the original ran, nothing more
            wanted = set(only_items[family])
            items = [i for i in items if i.id in wanted]
        results: list[ItemResult] = []
        for item in items:
            try:
                outcome = run_item(item, runtime_factory(item), budget=budget)
            except Exception as exc:  # noqa: BLE001 - a crashed item is a failed item, never a retry
                logger.warning("transfer item {} crashed: {}", item.id, exc)
                outcome = ItemResult(item.id, family, item.seed, False, [f"crash: {str(exc)[:120]}"], 0, 0, 0, reason="crash")
            results.append(outcome)
            if on_item is not None:
                on_item(outcome)
        passed = sum(1 for r in results if r.passed)
        bar = bar_for(family, lock.junior_baseline)
        rate = (passed / len(results)) if results else None
        verdict = family_verdict(rate, items=len(results), bar=bar)
        scores[family] = FamilyScore(family=family, items=len(results), passed=passed, bar=bar, verdict=verdict, results=results)
        if verdict != "pass":
            stopped_at = family
            break
    after = _s4_footprint(workspace)
    # Pass needs all four families scored and passed; a partial replay is never a pass.
    overall = "pass" if stopped_at is None and len(scores) == len(FAMILY_IDS) else "fail"
    record = {
        "id": cid,
        "ts": now_stamp(started),
        "protocol": PROTOCOL_VERSION,
        "suites_version": lock.version,
        "actor": actor,
        "replay_of": replay_of,
        "budget": budget.as_dict(),
        "families": {fam: scores[fam].as_dict() for fam in scores},
        "not_run": [fam for fam in FAMILY_IDS if fam not in scores],
        "stopped_at": stopped_at,
        "verdict": overall,
        "isolation": {
            "skills": False,
            "recall": False,
            "steer": False,
            "hooks": False,
            "s4_before": before,
            "s4_after": after,
            "s4_untouched": before == after,
        },
        "duration_ms": int((time.time() - started) * 1000),
    }
    append_campaign(workspace, record)
    journal(workspace, "campaign_scored", campaign=cid, verdict=overall, stopped_at=stopped_at, actor=actor)
    return {"status": "scored", **record}


def replay(workspace: Path | str, campaign: str, *, actor: str = HUMAN, runtime_factory: RuntimeFactory | None = None) -> dict[str, Any]:
    """Same items, same seeds, same suites version; a new record that points to the old one."""
    workspace = Path(workspace)
    previous = next((c for c in read_campaigns(workspace, limit=200) if c.get("id") == campaign), None)
    if previous is None:
        return {"status": "not_found", "verdict": None, "reason": f"no campaign {campaign}"}
    suites_dir = suites_dir_for(workspace)
    try:
        lock = verify(suites_dir)
    except (SuitesError, SuitesTamperedError) as exc:
        return _void(workspace, actor=actor, reason=str(exc))
    if lock.version != previous.get("suites_version"):
        return _void(workspace, actor=actor, reason=f"suites version {lock.version} differs from the campaign's {previous.get('suites_version')}")
    only = {fam: [r["id"] for r in data.get("results", [])] for fam, data in (previous.get("families") or {}).items()}
    budget_data = previous.get("budget") or {}
    budget = ItemBudget(
        max_tool_calls=int(budget_data.get("max_tool_calls", DEFAULT_BUDGET.max_tool_calls)),
        timeout_s=float(budget_data.get("timeout_s", DEFAULT_BUDGET.timeout_s)),
        max_tokens=int(budget_data.get("max_tokens", DEFAULT_BUDGET.max_tokens)),
    )
    return run_campaign(workspace, actor=actor, runtime_factory=runtime_factory, budget=budget, replay_of=campaign, only_items=only)


def spawn_campaign(workspace: Path | str, *, actor: str = HUMAN, replay_of: str | None = None, timeout_s: float = 3 * 3600.0) -> dict[str, Any]:
    """Run the campaign in a child process and return its result dict.

    The child never inherits the gateway environment; a dead or silent child
    is reported as ``status: error`` and nothing is recorded as a score.
    """
    workspace = Path(workspace)
    command = [sys.executable, "-m", "navin.transfer.campaign_job", "--workspace", str(workspace), "--actor", actor]
    if replay_of:
        command += ["--replay", replay_of]
    env = {k: v for k, v in os.environ.items() if not k.startswith("NAVIN_GATEWAY")}
    env["NAVIN_TRANSFER_CHILD"] = "1"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s + _CHILD_GRACE_S,
            env=env,
            cwd=str(workspace) if workspace.is_dir() else None,
            check=False,
        )
    except subprocess.TimeoutExpired:
        reason = f"campaign child killed after {timeout_s + _CHILD_GRACE_S:.0f}s"
        journal(workspace, "campaign_failed", reason=reason, actor=actor)
        return {"status": "error", "verdict": None, "reason": reason}
    except OSError as exc:
        reason = f"campaign child could not start: {exc}"
        journal(workspace, "campaign_failed", reason=reason, actor=actor)
        return {"status": "error", "verdict": None, "reason": reason}
    line = ""
    for candidate in reversed(completed.stdout.splitlines()):
        if candidate.strip().startswith("{"):
            line = candidate
            break
    if completed.returncode != 0 or not line:
        tail = (completed.stderr or "").strip().splitlines()[-3:]
        reason = f"campaign child exited {completed.returncode}" + (": " + " | ".join(tail) if tail else "")
        journal(workspace, "campaign_failed", reason=reason[:500], actor=actor)
        return {"status": "error", "verdict": None, "reason": reason[:500]}
    try:
        result = json.loads(line)
    except json.JSONDecodeError:
        return {"status": "error", "verdict": None, "reason": "campaign child answered something that is not JSON"}
    if not isinstance(result, dict):
        return {"status": "error", "verdict": None, "reason": "campaign child answered a non-object"}
    result.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
    result["process"] = "child"
    return result
