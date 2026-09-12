# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared execution loop for tool-using agents."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import shlex
import time
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navin.agent.code_validation import CodeValidationState, edit_paths, workspace_code_snapshot
from navin.agent.context_governance import (
    ContextGovernanceConfig,
    ContextGovernor,
)
from navin.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from navin.agent.prompt_profile import log_request_profile
from navin.agent.reasoning_router import route_reasoning_effort
from navin.agent.run_policy import TurnPolicy, bind_turn_policy, reset_turn_policy
from navin.agent.scope_anchor import (
    calls_touch_targets,
    is_orientation_batch,
    scope_drift_message,
)
from navin.agent.tools.base import ToolResult
from navin.agent.tools.registry import ToolRegistry, is_tool_error_result, tool_error_hint
from navin.agent.turn_timing import (
    PHASE_CONTEXT,
    PHASE_MODEL,
    PHASE_TOOLS,
    TurnTiming,
    measure,
)
from navin.config.schema import ToolResultClearing
from navin.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from navin.providers.session_affinity import session_affinity
from navin.session.history_visibility import is_hidden_history_message
from navin.utils.helpers import (
    IncrementalThinkExtractor,
    build_assistant_message,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    extract_reasoning,
    strip_reasoning_tags,
    strip_think,
)
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.prompt_templates import render_template
from navin.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    NO_PROGRESS_STOP_FALLBACK,
    build_budget_exhausted_finalization_message,
    build_delivery_continue_message,
    build_finalization_retry_message,
    build_goal_continue_message,
    build_length_recovery_message,
    build_no_progress_continue_message,
    build_no_progress_finalization_message,
    build_verify_before_done_message,
    build_verify_failed_message,
    is_blank_text,
    repeated_external_lookup_error,
    repeated_readonly_tool_error,
    repeated_tool_failure_hint,
    repeated_tool_failure_is_hard_stop,
    repeated_workspace_violation_error,
    reset_readonly_spin_counts,
    reset_tool_failure_count,
)

_VERIFY_TOOL_NAMES = frozenset({"verify", "lint", "test_run"})
# Only nudge verify after real edits - pure investigation must not be blocked.
_EDIT_TOOL_NAMES = frozenset(
    {"apply_patch", "edit_file", "write_file", "manage_files"}
)
PROGRESS_TOOL_NAMES = _EDIT_TOOL_NAMES | {
    "exec",
    "test_run",
    "verify",
    "lint",
    "start_app",
    "open_preview",
    "write_stdin",
}
_PROGRESS_TOOL_NAMES = PROGRESS_TOOL_NAMES
# Only repeated calls already refused by a loop guard count as a stall.
# Successful reads of different files are progress, including long audits.
_REPEATED_CALL_BLOCKS = frozenset({
    "repeated identical tool call blocked",
    "repeated identical read blocked",
    "repeated external lookup blocked",
})
_NO_PROGRESS_NUDGE = 5
_NO_PROGRESS_STOP = 8
# Look-around batches (list/find/grep/read) touching none of the targets the
# user named before the turn is told, once, where the request pointed. Two
# batches is a repo listing plus a README, i.e. exactly the drift.
_SCOPE_DRIFT_NUDGE = 2
# Only repeated final answers without new validation or repair are capped.
# Actual edit/check cycles can continue until the accepted task is complete.
_MAX_VERIFY_FAIL_NUDGES = 2


# Programs that are a test run by themselves, and programs that are one only
# with the right sub-command. Matched on the program token of each simple
# command, so ``pip install pytest`` and ``echo jest`` are not runs.
_TEST_PROGRAMS = frozenset({
    "pytest", "py.test", "vitest", "jest", "mocha", "phpunit", "rspec", "tox",
    "nox", "ctest", "ava", "tap", "karma", "cypress", "behave", "busted",
})
_TEST_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "cargo": frozenset({"test", "nextest"}),
    "go": frozenset({"test"}),
    "dotnet": frozenset({"test"}),
    "make": frozenset({"test", "check", "tests"}),
    "mix": frozenset({"test"}),
    "swift": frozenset({"test"}),
    "flutter": frozenset({"test"}),
    "dart": frozenset({"test"}),
    "deno": frozenset({"test"}),
    "playwright": frozenset({"test"}),
    "mvn": frozenset({"test", "verify"}),
    "gradle": frozenset({"test", "check"}),
    "gradlew": frozenset({"test", "check"}),
    "stack": frozenset({"test"}),
    "cabal": frozenset({"test"}),
    "sbt": frozenset({"test"}),
    "lein": frozenset({"test"}),
    "zig": frozenset({"test"}),
    "nx": frozenset({"test"}),
    "turbo": frozenset({"test"}),
    "ng": frozenset({"test"}),
    "npm": frozenset({"test", "t"}),
    "pnpm": frozenset({"test", "t"}),
    "yarn": frozenset({"test"}),
    "bun": frozenset({"test"}),
}
_RUN_WRAPPERS = frozenset({
    "uv", "poetry", "pipenv", "hatch", "pdm", "rye", "npx", "bunx", "pnpx",
    "bundle", "time", "sudo", "env", "nice", "xvfb-run", "timeout", "nix-shell",
})
_EXIT_CODE_RE = re.compile(r"Exit code: (-?\d+)")


def _is_test_command(command: str) -> bool:
    """Can a successful exit prove this command actually ran tests?"""
    # Shell fallbacks, later commands and pipes can hide a failing test exit.
    if re.search(r"\|\||(?<!&);|(?<!\|)\|(?!\|)", command):
        return False
    if re.search(r"(?:^|\s)--(?:collect-only|listTests|list-tests|list|help|version|dry-run)(?:\s|$)", command):
        return False
    command = command.replace("\\", "/")
    for simple in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command):
        try:
            tokens = shlex.split(simple)
        except ValueError:
            continue
        while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
            tokens.pop(0)  # FOO=bar prefixes
        while tokens and os.path.basename(tokens[0]) in _RUN_WRAPPERS:
            tokens.pop(0)
            # ``timeout -k 5 600 pytest``, ``nice -n 10 pytest``: flags and
            # bare durations belong to the wrapper, not to the program.
            while tokens and (tokens[0].startswith("-") or re.fullmatch(r"\d+[smhd]?", tokens[0])):
                tokens.pop(0)
            if tokens and tokens[0] in {"run", "exec"}:
                tokens.pop(0)
        if not tokens:
            continue
        program = os.path.basename(tokens[0].strip("\"'")).lower()
        if program.endswith(".exe"):
            program = program[:-4]
        args = tokens[1:]
        if program in _TEST_PROGRAMS:
            return True
        if re.fullmatch(r"python[0-9.]*|pypy[0-9.]*|py", program):
            while args and (args[0] in {"-B", "-u", "-E", "-s", "-S", "-I", "-O", "-OO"} or re.fullmatch(r"-3(?:\.\d+)?", args[0])):
                args.pop(0)
            if len(args) >= 2 and args[0] == "-m" and args[1] in {"pytest", "unittest", "nose2", "behave"}:
                return True
            continue
        if program == "node" and "--test" in args:
            return True
        if program in {"npm", "pnpm", "yarn", "bun"} and len(args) >= 2 and args[0] == "run":
            if args[1].split(":")[0] in {"test", "tests", "e2e"} or args[1].startswith("test"):
                return True
            continue
        expected = _TEST_SUBCOMMANDS.get(program)
        if expected and args and args[0] in expected:
            if program == "cargo" and args[0] == "nextest" and args[1:2] != ["run"]:
                continue
            return True
    return False


def _exec_event_fields(params: Any, result: Any) -> dict[str, str]:
    """What a tool event needs to say about an ``exec`` beyond its first line."""
    command = ""
    if isinstance(params, dict):
        command = str(params.get("command") or params.get("cmd") or "")
    codes = _EXIT_CODE_RE.findall(str(result or ""))
    return {"command": command[:300], "exit_code": codes[-1] if codes else ""}


def _tests_passed_via_exec(tool_events: list[dict[str, str]]) -> bool:
    """True when a test runner ran through ``exec`` and exited 0 after the last edit.

    ``pytest`` under ``exec`` is the same evidence ``test_run`` produces. Asking
    the model for ``verify`` on top used to cost two more model calls on
    every small change: one to read the nudge, one to run a tool it had
    effectively already run.
    """
    verified = False
    for event in tool_events:
        name = str(event.get("name") or "")
        if name in _EDIT_TOOL_NAMES:
            verified = False
            continue
        if name != "exec" or event.get("status") != "ok":
            continue
        if not _is_test_command(str(event.get("command") or "")):
            continue
        verified = event.get("exit_code") == "0"
    return verified


def _last_verify_failed(tool_events: list[dict[str, str]]) -> bool:
    """True when the most recent verify/lint/test_run event looks red."""
    for event in reversed(tool_events):
        name = str(event.get("name") or "")
        if name not in _VERIFY_TOOL_NAMES:
            continue
        if event.get("status") == "error":
            return True
        detail = str(event.get("detail") or "")
        upper = detail.upper()
        if "FAIL" in upper or "VERDICT_TEST_FAILURES" in upper or "VERDICT_LINT_ERRORS" in upper:
            return True
        if "PASS" in upper or "CLEAN" in upper or "OK" in upper:
            return False
        return False
    return False


def _verify_failure_summary(
    spec: "AgentRunSpec",
    tool_events: list[dict[str, str]],
) -> str:
    """What the model should see about the red verify, not just that it is red."""
    if spec.workspace is not None:
        try:
            from navin.quality.verification_log import last_verification_summary

            digest = last_verification_summary(spec.workspace)
            if digest:
                return digest
        except Exception:
            pass
    for event in reversed(tool_events):
        name = str(event.get("name") or "")
        if name not in _VERIFY_TOOL_NAMES:
            continue
        detail = str(event.get("detail") or "").strip()
        if detail:
            return f"{name}: {detail}"
    return ""


GoalContinueMessage = str | Callable[[], str | None]

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
# Sentinel consommé par le WebUI (QuotaLimitCard) - ne pas traduire / reformuler.
_QUOTA_LIMIT_SENTINEL = "__NAVIN_QUOTA_LIMIT__"
_PROVIDER_CREDIT_SENTINEL = "__NAVIN_PROVIDER_CREDIT__"
# Line prefix the WebUI reads to quote the provider verbatim - keep in sync
# with parseProviderCreditMessage in QuotaLimitCard.tsx.
_PROVIDER_MESSAGE_PREFIX = "Provider message: "
_PROVIDER_CREDIT_HINT = (
    "This model runs on your own API key. Top up at the provider, or pick another model."
)
_PROVIDER_CREDIT_ERROR_MESSAGE = (
    f"{_PROVIDER_CREDIT_SENTINEL}\n"
    "The provider refused the call: the key is out of credit.\n"
    f"{_PROVIDER_CREDIT_HINT}"
)
_MANAGED_QUOTA_ERROR_MESSAGE = (
    f"{_QUOTA_LIMIT_SENTINEL}\n"
    "Your plan's monthly model quota is used up. It resets at the start of your next "
    "billing period. To keep working now, upgrade your plan in Settings, Account, or "
    "add your own provider key."
)
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"


def _provider_label(runtime: LLMRuntime | None, response: LLMResponse | None) -> str:
    """Display name of the provider that refused the call, "" when unknown."""
    label = str(getattr(response, "error_provider", "") or "").strip()
    if label:
        return label
    provider = getattr(runtime, "provider", None)
    # Failover wrappers keep the chosen model's provider on ``_primary``.
    for _ in range(3):
        if provider is None:
            break
        spec = getattr(provider, "_spec", None)
        for attr in ("display_name", "name"):
            name = getattr(spec, attr, None)
            if isinstance(name, str) and name.strip():
                return name.strip()
        provider = getattr(provider, "_primary", None)
    return ""


def _provider_credit_error_message(
    runtime: LLMRuntime | None, response: LLMResponse | None
) -> str:
    """Refusal on the user's own key: who refused, their words, what to do.

    The generic "out of credit" sentence hid which provider said what. Quoting
    the provider ("Insufficient balance or no resource package. Please
    recharge.") tells the user where to top up without guessing.
    """
    from navin.providers.user_facing_errors import provider_error_detail

    provider = _provider_label(runtime, response) or "The provider"
    model = str(getattr(runtime, "model", "") or "").strip()
    detail = str(getattr(response, "error_detail", "") or "").strip()
    if not detail:
        detail = provider_error_detail(getattr(response, "content", None)) or ""
    target = f" for {model}" if model else ""
    lines = [
        _PROVIDER_CREDIT_SENTINEL,
        f"{provider} refused the call{target}: the key is out of credit.",
    ]
    if detail:
        lines.append(f"{_PROVIDER_MESSAGE_PREFIX}{detail}")
    lines.append(_PROVIDER_CREDIT_HINT)
    return "\n".join(lines)


def _arrearage_error_message(
    runtime: LLMRuntime | None = None, response: LLMResponse | None = None
) -> str:
    """Plan quota only when THIS turn used the managed key.

    A subscribed account can still send BYOK turns. Those 402s are the
    user's provider credit, not the Navin monthly budget, and the provider's
    own message is what tells the user where to top up.
    """
    try:
        from navin.config.loader import load_config
        from navin.license_sync import request_immediate_sync
        from navin.usage_mode import runtime_uses_managed_key

        config = load_config()
        if runtime is not None and runtime_uses_managed_key(runtime, config):
            request_immediate_sync("managed_quota_message")
            return _MANAGED_QUOTA_ERROR_MESSAGE
    except Exception:
        pass
    try:
        return _provider_credit_error_message(runtime, response)
    except Exception:
        return _PROVIDER_CREDIT_ERROR_MESSAGE


# Defaults for AgentRunSpec.max_empty_retries / max_length_recoveries; a run
# that needs different limits sets them on its spec instead of editing these.
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
_MAX_INVALID_TOOL_ITERATIONS = 3
# Keep aligned with AgentDefaults.max_concurrent_subagents: the parent must be
# able to drain one full wave of completions per injection cycle. Draining less
# than a wave is not lossy (the surplus waits in the queue), but it spends one
# of the _MAX_INJECTION_CYCLES per fraction of a wave, so a fan-out wider than
# this starves the cycles left for genuine follow-up work.
_MAX_INJECTIONS_PER_TURN = 200
_MAX_INJECTION_CYCLES = 5


def _call_read_only(tool: Any, params: Any) -> bool:
    """Whether this specific call is side-effect free (per-action aware)."""
    checker = getattr(tool, "call_read_only", None)
    if callable(checker):
        try:
            return bool(checker(params))
        except Exception:
            return bool(getattr(tool, "read_only", False))
    return bool(getattr(tool, "read_only", False))

@dataclass(slots=True)
class AgentLoopGuard:
    """Loop protection and validation shared by one accepted request's slices."""

    external_lookups: dict[str, int] = field(default_factory=dict)
    workspace_violations: dict[str, int] = field(default_factory=dict)
    tool_failures: dict[str, int] = field(default_factory=dict)
    readonly_calls: dict[str, int] = field(default_factory=dict)
    invalid_tool_iterations: int = 0
    no_progress_streak: int = 0
    no_progress_nudge_count: int = 0
    validation: CodeValidationState = field(default_factory=CodeValidationState)


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for a single agent execution."""

    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    runtime: LLMRuntime
    max_iterations: int
    max_tool_result_chars: int
    hook: AgentHook | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = None
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False
    workspace: Path | None = None
    session_key: str | None = None
    context_block_limit: int | None = None
    tool_result_clearing: ToolResultClearing = field(default_factory=ToolResultClearing)
    provider_retry_mode: str = "standard"
    progress_callback: Any | None = None
    stream_progress_deltas: bool = True
    retry_wait_callback: Any | None = None
    checkpoint_callback: Any | None = None
    injection_callback: Any | None = None
    llm_timeout_s: float | None = None
    # Runner-level wall clock around one tool execution. None reads
    # NAVIN_TOOL_TIMEOUT_S (default 3600s); <= 0 disables. The ceiling is
    # deliberately above every legitimate long wait (write_stdin's 30-min
    # emulator boot, exec polls): it exists to catch tools that hang forever
    # (an MCP server that never answers, a network read without its own
    # timeout), not to police slow-but-alive work. A hit is a soft tool error,
    # never the end of the turn.
    tool_timeout_s: float | None = None
    goal_active_predicate: Callable[[], bool] | None = None
    goal_continue_message: GoalContinueMessage | None = None
    # When True, a first final answer with zero successful tool calls is nudged
    # once so delivery workflows (/studio, /campaign, …) cannot end on a plan.
    requires_tool_delivery: bool = False
    # Explicit workflows require checks after every workspace edit.
    requires_verify_before_done: bool = False
    # CLI, desktop and their delegated work enable this regardless of module.
    # Source edits require tests; config/style edits require appropriate checks.
    validate_code_changes: bool = False
    # Paths, branches, services or files the user named in the request. A
    # turn whose first look-around batches touch none of them is reminded
    # once where to look (see navin.agent.scope_anchor).
    scope_targets: tuple[str, ...] = ()
    # Blank final responses re-asked before giving up on the turn.
    max_empty_retries: int = _MAX_EMPTY_RETRIES
    # finish_reason=length continuations before the output is cut short.
    max_length_recoveries: int = _MAX_LENGTH_RECOVERIES
    # Responses containing only invalid tool arguments get two chances to
    # recover. Stop the run after that so a broken subagent releases its slot.
    max_invalid_tool_iterations: int = _MAX_INVALID_TOOL_ITERATIONS
    # Consecutive attempts to finish with unchanged missing validation. Repair
    # edits reset this guard; exhausting it reports a blocker, never success.
    verify_fail_nudge_limit: int | None = None
    # When True, only read-only tool *calls* may run (Ask mode). Tools that
    # multiplex reads and writes behind one name (git, board) are judged per
    # action via Tool.call_read_only, so git status works while commit refuses.
    read_only_tools: bool = False
    # Plan mode: design-only turns. Read-only calls run freely; the planning
    # surfaces (board, ask_user, set_composer_mode) may write; every other
    # mutating call is refused until the composer switches to Agent.
    plan_read_only: bool = False
    # Composer mode this turn started in. A successful set_composer_mode call
    # updates it (and the flags above) mid-turn, so the documented plan→agent
    # simple-task handoff takes effect immediately instead of next turn.
    composer_mode: str | None = None
    # Kept for metadata/prompt compatibility. No longer hard-blocks tools:
    # Agent modes may use write_file / edit_file / apply_patch freely.
    apply_patch_only: bool = False
    # Verify-red retries so far this turn. Each one raises the routed
    # reasoning effort a step (see navin.agent.reasoning_router): the cheap
    # attempt just failed, so its retry should not think at the same depth.
    effort_escalations: int = 0
    # Tool names refused for this turn (e.g. scrape in Code module).
    denied_tools: frozenset[str] = field(default_factory=frozenset)
    # Module-level denials (product decisions). Kept apart from denied_tools so
    # a mid-turn mode switch can swap mode denials without ever lifting these.
    locked_denied_tools: frozenset[str] = field(default_factory=frozenset)
    # None = no allowlist. A frozenset is the only names whose schemas go to
    # the model (/forge). MCP and every desk tool not listed stay out.
    allowed_tools: frozenset[str] | None = None
    # Generator pinned for the first model call when the request is an
    # unambiguous media ask ("genere une image de ..."). Ignored once any tool
    # has run, and when the build does not ship that tool.
    forced_tool: str | None = None
    finalize_on_max_iterations: bool = True
    # Standalone CLI calls and subagents have no dispatcher to resume a slice.
    # Keep their runner alive, with checkpoints and context compaction, until
    # completion, a real failure, or cancellation. Explicit bounded callers
    # (evals and ephemeral jobs) retain the finite default.
    continue_on_max_iterations: bool = False
    loop_guard: AgentLoopGuard | None = None


@dataclass(slots=True)
class AgentRunResult:
    """Outcome of a shared agent execution."""

    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False
    prompt_profile: dict[str, Any] | None = None
    retryable_error: bool = False
    retry_after_s: float | None = None


class AgentRunner:
    """Run a tool-capable LLM loop without product-layer concerns."""

    def __init__(self) -> None:
        self.context_governor = ContextGovernor()

    @staticmethod
    def _model_tool_definitions(spec: AgentRunSpec) -> list[dict[str, Any]]:
        """Schemas the model actually sees (allowlist + denylist + compact)."""
        from navin.agent.tool_surface import filter_tool_definitions

        return filter_tool_definitions(
            spec.tools.get_definitions(),
            spec.denied_tools,
            allowed=spec.allowed_tools,
            compact=True,
        )

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    item if isinstance(item, dict) else {"type": "text", "text": str(item)}
                    for item in value
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    @classmethod
    def _append_injected_messages(
        cls,
        messages: list[dict[str, Any]],
        injections: list[dict[str, Any]],
    ) -> None:
        """Append injected user messages while preserving role alternation."""
        for injection in injections:
            if (
                messages
                and injection.get("role") == "user"
                and messages[-1].get("role") == "user"
                and not is_hidden_history_message(injection)
                and not is_hidden_history_message(messages[-1])
            ):
                merged = dict(messages[-1])
                merged["content"] = cls._merge_message_content(
                    merged.get("content"),
                    injection.get("content"),
                )
                messages[-1] = merged
                continue
            messages.append(injection)

    async def _try_drain_injections(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        assistant_message: dict[str, Any] | None,
        injection_cycles: int,
        *,
        phase: str = "after error",
        iteration: int | None = None,
        allow_goal_continue: bool = False,
        wait: bool = True,
    ) -> tuple[bool, int]:
        """Drain pending injections. Returns (should_continue, updated_cycles).

        If injections are found and we haven't exceeded _MAX_INJECTION_CYCLES,
        append them to *messages* (and emit a checkpoint if *assistant_message*
        and *iteration* are both provided) and return (True, cycles+1) so the
        caller continues the iteration loop.  Otherwise return (False, cycles).

        ``wait=False`` marks a mid-turn drain: take what is already queued but
        never block on still-running subagents, because the model has its own
        next step to run. The end-of-turn drain keeps waiting so background
        results land in this turn instead of being dispatched separately.
        """
        injections: list[dict[str, Any]] = []
        real_injection = False
        if injection_cycles < _MAX_INJECTION_CYCLES:
            injections = await self._drain_injections(spec, wait=wait)
            real_injection = bool(injections)
        if not injections and allow_goal_continue and assistant_message is not None:
            predicate = spec.goal_active_predicate
            if predicate is not None and predicate():
                injections = [self._build_goal_continue_message(spec)]
        if not injections:
            return False, injection_cycles
        if real_injection:
            injection_cycles += 1
        if assistant_message is not None:
            messages.append(assistant_message)
            if iteration is not None:
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "final_response",
                        "iteration": iteration,
                        "model": spec.runtime.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [],
                    },
                )
        self._append_injected_messages(messages, injections)
        if real_injection:
            logger.info(
                "Injected {} follow-up message(s) {} ({}/{})",
                len(injections), phase, injection_cycles, _MAX_INJECTION_CYCLES,
            )
        else:
            logger.info("Injected sustained-goal continuation {}", phase)
        return True, injection_cycles

    def _build_goal_continue_message(self, spec: AgentRunSpec) -> dict[str, str]:
        custom = spec.goal_continue_message
        if callable(custom):
            try:
                custom = custom()
            except Exception:
                logger.exception("goal_continue_message callback failed")
                custom = None
        return build_goal_continue_message(custom)

    async def _drain_injections(
        self, spec: AgentRunSpec, *, wait: bool = True,
    ) -> list[dict[str, Any]]:
        """Drain pending user messages via the injection callback.

        Returns normalized user messages (capped by
        ``_MAX_INJECTIONS_PER_TURN``), or an empty list when there is
        nothing to inject. A callback that over-delivers past the cap has its
        surplus dropped, and the last surviving message says so, so a background
        result cannot go missing without the model being told.
        """
        if spec.injection_callback is None:
            return []
        try:
            signature = inspect.signature(spec.injection_callback)
            has_var_keyword = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            kwargs: dict[str, Any] = {}
            if "limit" in signature.parameters or has_var_keyword:
                kwargs["limit"] = _MAX_INJECTIONS_PER_TURN
            if "wait" in signature.parameters or has_var_keyword:
                kwargs["wait"] = wait
            items = await spec.injection_callback(**kwargs)
        except Exception:
            logger.exception("injection_callback failed")
            return []
        if not items:
            return []
        injected_messages: list[dict[str, Any]] = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, dict) and item.get("role") == "user" and "content" in item:
                if self._has_injection_content(item.get("content")):
                    injected_messages.append(item)
                continue
            if isinstance(item, dict):
                continue
            content = getattr(item, "content") if hasattr(item, "content") else str(item)
            if self._has_injection_content(content):
                injected_messages.append({"role": "user", "content": content})
        if len(injected_messages) > _MAX_INJECTIONS_PER_TURN:
            dropped = len(injected_messages) - _MAX_INJECTIONS_PER_TURN
            logger.warning(
                "Injection callback returned {} messages, capping to {} ({} dropped)",
                len(injected_messages), _MAX_INJECTIONS_PER_TURN, dropped,
            )
            injected_messages = injected_messages[:_MAX_INJECTIONS_PER_TURN]
            # A result the model never hears about looks like a subagent that
            # vanished, and it will either wait for it or redo the work. Naming
            # the loss costs one line and lets it ask instead.
            injected_messages[-1] = self._with_overflow_note(injected_messages[-1], dropped)
        return injected_messages

    @staticmethod
    def _with_overflow_note(message: dict[str, Any], dropped: int) -> dict[str, Any]:
        """Append a note about results this turn could not carry."""
        note = (
            f"\n\n[{dropped} further background result(s) arrived at the same time "
            f"and did not fit in this turn. Ask for them if you need them.]"
        )
        content = message.get("content")
        if isinstance(content, str):
            return {**message, "content": content + note}
        if isinstance(content, list):
            return {**message, "content": [*content, {"type": "text", "text": note}]}
        return message

    @staticmethod
    def _has_injection_content(content: Any) -> bool:
        if content is None:
            return False
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return bool(content)
        return True

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        hook = spec.hook or AgentHook()
        messages = list(spec.initial_messages)
        # The deepcopies below exist to isolate hook callbacks from the live
        # conversation. A bare AgentHook is a documented no-op: nothing reads
        # the context, so paying several full-history deep copies per run for
        # it is pure overhead that grows with conversation size.
        isolate = type(hook) is not AgentHook

        def _snapshot(source: Any) -> Any:
            return deepcopy(source) if isolate else list(source)

        context = AgentRunHookContext(messages=_snapshot(messages))

        # Published for the whole turn so tools that start background work
        # (spawn) can inherit the inheritable guardrails: the verify gate and
        # the module denylist must follow delegated work, not stop at the
        # parent. contextvars carry the value into tasks created mid-turn.
        policy_token = bind_turn_policy(TurnPolicy(
            requires_verify_before_done=spec.requires_verify_before_done,
            validate_code_changes=spec.validate_code_changes,
            locked_denied_tools=spec.locked_denied_tools,
            allowed_tools=spec.allowed_tools,
        ))
        try:
            await hook.before_run(context)
            result = await self._run_core(spec, hook, messages)
        except asyncio.CancelledError as exc:
            context.messages = _snapshot(messages)
            context.stop_reason = "cancelled"
            context.error = None
            context.exception = exc
            raise
        except Exception as exc:
            context.messages = _snapshot(messages)
            context.stop_reason = "error"
            context.error = f"Error: {type(exc).__name__}: {exc}"
            context.exception = exc
            await hook.on_error(context)
            raise
        else:
            context.messages = _snapshot(result.messages)
            context.final_content = result.final_content
            context.tools_used = list(result.tools_used)
            context.usage = dict(result.usage)
            context.stop_reason = result.stop_reason
            context.error = result.error
            context.tool_events = _snapshot(result.tool_events)
            context.had_injections = result.had_injections
            context.exception = None
            if context.error is not None:
                await hook.on_error(context)
            await hook.after_run(context)
            return result
        finally:
            reset_turn_policy(policy_token)
            context.messages = _snapshot(messages)
            if context.exception is None:
                await hook.on_finally(context)
            else:
                try:
                    await hook.on_finally(context)
                except Exception:
                    logger.exception(
                        "AgentHook.on_finally error after {}",
                        context.stop_reason or "run exception",
                    )

    async def _run_core(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
    ) -> AgentRunResult:
        final_content: str | None = None
        tools_used: list[str] = []
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        peak_prompt_tokens = 0
        peak_request_messages: list[dict[str, Any]] | None = None
        error: str | None = None
        stop_reason = "completed"
        tool_events: list[dict[str, str]] = []
        loop_guard = spec.loop_guard or AgentLoopGuard()
        spec.loop_guard = loop_guard
        validation = loop_guard.validation
        external_lookup_counts = loop_guard.external_lookups
        workspace_violation_counts = loop_guard.workspace_violations
        # Soft-error retry budget: identical failing calls escalate after a few tries.
        tool_failure_counts = loop_guard.tool_failures
        invalid_tool_iterations = loop_guard.invalid_tool_iterations
        # Identical successful reads/greps: thinking models re-fetch blanked results.
        readonly_call_counts = loop_guard.readonly_calls
        empty_content_retries = 0
        length_recovery_count = 0
        had_injections = False
        injection_cycles = 0
        compacted_tool_call_ids: set[str] = set()
        from navin.agent.tool_surface import FilteredToolDefinitions

        # Read denied/allowed through callables: a mid-turn set_composer_mode
        # swap is then visible to the very next definitions read.
        #
        # Schemas are always compacted, not just under an allowlist. Prompt
        # size is wall-clock: a 57k prompt costs ~2.3s per call over a small
        # one even at a 100% cache hit, and every step pays it again. The
        # long tail of a description is prose the model does not need to pick
        # the right tool.
        tools_for_model = FilteredToolDefinitions(
            spec.tools,
            lambda: spec.denied_tools,
            allowed=lambda: spec.allowed_tools,
            compact=True,
        )
        governance_config = ContextGovernanceConfig(
            provider=spec.runtime.provider,
            model=spec.runtime.model,
            tools=tools_for_model,
            workspace=spec.workspace,
            session_key=spec.session_key,
            max_tool_result_chars=spec.max_tool_result_chars,
            context_window_tokens=spec.runtime.context_window_tokens,
            context_block_limit=spec.context_block_limit,
            clearing=spec.tool_result_clearing,
            max_tokens=spec.runtime.generation.max_tokens,
            inflight_start_index=len(spec.initial_messages),
        )

        delivery_nudge_count = 0
        no_progress_streak = loop_guard.no_progress_streak
        no_progress_nudge_count = loop_guard.no_progress_nudge_count
        # Scope anchor: look-around batches that never touch a named target.
        scope_drift_streak = 0
        scope_anchored = not spec.scope_targets
        scope_nudge_count = 0
        timing = TurnTiming(session_key=spec.session_key, model=spec.runtime.model)
        iterations = count() if spec.continue_on_max_iterations else range(spec.max_iterations)
        for iteration in iterations:
            try:
                # Keep the persisted conversation untouched. Context governance
                # may repair or compact historical messages for the model, but
                # those synthetic edits must not shift the append boundary used
                # later when the caller saves only the new turn.
                with measure(timing, PHASE_CONTEXT):
                    messages_for_model = self.context_governor.prepare_for_model(
                        governance_config,
                        messages,
                        compacted_tool_call_ids,
                    )
            except Exception:
                logger.exception(
                    "Context governance failed on turn {} for {}; applying minimal repair",
                    iteration,
                    spec.session_key or "default",
                )
                try:
                    messages_for_model = ContextGovernor.strip_placeholder_assistant_messages(
                        messages
                    )
                    messages_for_model = ContextGovernor.strip_malformed_tool_calls(
                        messages_for_model
                    )
                    messages_for_model = ContextGovernor.drop_orphan_tool_results(
                        messages_for_model
                    )
                    messages_for_model = ContextGovernor.backfill_missing_tool_results(
                        messages_for_model
                    )
                except Exception:
                    messages_for_model = messages
            context = AgentHookContext(
                iteration=iteration,
                messages=messages,
                session_key=spec.session_key,
                model=spec.runtime.model,
            )
            await hook.before_iteration(context)
            with measure(timing, PHASE_MODEL):
                response = await self._request_model(
                    spec,
                    messages_for_model,
                    hook,
                    context,
                    tool_followup=bool(tools_used),
                    empty_retry=empty_content_retries > 0,
                )
            timing.end_iteration()
            context.response = response
            context.tool_calls = list(response.tool_calls)

            reasoning_text, cleaned_content = extract_reasoning(
                response.reasoning_content,
                response.thinking_blocks,
                response.content,
            )
            response.content = cleaned_content
            raw_usage = self._usage_or_estimate(spec, messages_for_model, response)
            timing.add_usage(raw_usage, context.requested_reasoning_effort)
            context.usage = dict(raw_usage)
            if spec.runtime.model:
                context.model = spec.runtime.model
                context.usage["model"] = spec.runtime.model
            self._accumulate_usage(usage, raw_usage)
            prompt_n = int(raw_usage.get("prompt_tokens") or 0)
            if prompt_n > peak_prompt_tokens:
                peak_prompt_tokens = prompt_n
                peak_request_messages = list(messages_for_model)
            if reasoning_text and not context.streamed_reasoning:
                await hook.emit_reasoning(reasoning_text)
                await hook.emit_reasoning_end()
                context.streamed_reasoning = True

            if response.should_execute_tools:
                context.tool_calls = list(response.tool_calls)
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                messages.append(assistant_message)
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "awaiting_tools",
                        "iteration": iteration,
                        "model": spec.runtime.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [tc.to_openai_tool_call() for tc in response.tool_calls],
                    },
                )

                await hook.before_execute_tools(context)

                with measure(timing, PHASE_TOOLS):
                    results, new_events, fatal_error = await self._execute_tools(
                        spec,
                        response.tool_calls,
                        external_lookup_counts,
                        workspace_violation_counts,
                        hook,
                        context,
                        tool_failure_counts=tool_failure_counts,
                        readonly_call_counts=readonly_call_counts,
                        timing=timing,
                    )
                tool_events.extend(new_events)
                tools_used.extend(
                    tool_call.name
                    for tool_call, event in zip(response.tool_calls, new_events)
                    if event.get("status") == "ok"
                )
                context.tool_results = list(results)
                context.tool_events = list(new_events)
                # Count model responses, not individual calls: a parallel
                # batch must still get a chance to recover on the next turn.
                # Ordinary execution failures stay soft, and any other batch
                # ends the streak. Empty arguments remain valid for no-arg tools.
                if new_events and all(
                    event.get("error_kind") == "invalid_parameters" for event in new_events
                ):
                    invalid_tool_iterations += 1
                else:
                    invalid_tool_iterations = 0
                loop_guard.invalid_tool_iterations = invalid_tool_iterations
                if (
                    fatal_error is None
                    and invalid_tool_iterations > 0
                    and invalid_tool_iterations >= spec.max_invalid_tool_iterations
                ):
                    names = ", ".join(dict.fromkeys(call.name for call in response.tool_calls))
                    fatal_error = RuntimeError(
                        f"Stopped after {invalid_tool_iterations} consecutive responses "
                        f"with invalid tool arguments for {names}. No tool from these "
                        "responses was executed."
                    )
                completed_tool_results: list[dict[str, Any]] = []
                for tool_call, result in zip(response.tool_calls, results):
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": self.context_governor.normalize_tool_result(
                            governance_config,
                            tool_call.id,
                            tool_call.name,
                            result,
                        ),
                    }
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)
                if fatal_error is not None:
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    final_content = error
                    stop_reason = "tool_error"
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    should_continue, injection_cycles = await self._try_drain_injections(
                        spec, messages, None, injection_cycles,
                        phase="after tool error",
                        wait=False,
                    )
                    if should_continue:
                        had_injections = True
                        invalid_tool_iterations = 0
                        loop_guard.invalid_tool_iterations = 0
                        error = None
                        final_content = None
                        stop_reason = "completed"
                        continue
                    break
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "tools_completed",
                        "iteration": iteration,
                        "model": spec.runtime.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": completed_tool_results,
                        "pending_tool_calls": [],
                    },
                )
                empty_content_retries = 0
                length_recovery_count = 0
                if new_events and all(
                    event.get("status") == "error"
                    and event.get("detail") in _REPEATED_CALL_BLOCKS
                    for event in new_events
                ):
                    no_progress_streak += 1
                else:
                    no_progress_streak = 0
                loop_guard.no_progress_streak = no_progress_streak
                if no_progress_streak >= _NO_PROGRESS_STOP:
                    logger.warning(
                        "Only blocked duplicate calls for {} iterations in {}; stopping",
                        no_progress_streak,
                        spec.session_key or "default",
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    final_content = await self._try_finalize_after_max_iterations(
                        spec, hook, messages, usage,
                        finalization_message=build_no_progress_finalization_message(),
                    )
                    if is_blank_text(final_content):
                        final_content = NO_PROGRESS_STOP_FALLBACK
                    self._append_final_message(messages, final_content)
                    stop_reason = "no_progress"
                    context.final_content = final_content
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    break
                if (
                    no_progress_streak == _NO_PROGRESS_NUDGE
                    and no_progress_nudge_count < 1
                ):
                    no_progress_nudge_count += 1
                    loop_guard.no_progress_nudge_count = no_progress_nudge_count
                    messages.append(build_no_progress_continue_message())
                if not scope_anchored:
                    if calls_touch_targets(response.tool_calls, spec.scope_targets):
                        scope_anchored = True
                    elif is_orientation_batch(response.tool_calls):
                        scope_drift_streak += 1
                    if (
                        scope_drift_streak >= _SCOPE_DRIFT_NUDGE
                        and scope_nudge_count < 1
                    ):
                        scope_nudge_count += 1
                        logger.info(
                            "Scope drift: {} look-around batches without touching {} for {}",
                            scope_drift_streak, list(spec.scope_targets),
                            spec.session_key or "default",
                        )
                        messages.append(scope_drift_message(spec.scope_targets))
                # Checkpoint 1: drain injections after tools, before next LLM call.
                # Non-blocking: the model has its next step to run; waiting for
                # still-running subagents here parked the whole turn for up to
                # five minutes between two tool batches.
                _drained, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles,
                    phase="after tool execution",
                    wait=False,
                )
                if _drained:
                    had_injections = True
                await hook.after_iteration(context)
                continue

            if response.has_tool_calls:
                logger.warning(
                    "Ignoring tool calls under finish_reason='{}' for {}",
                    response.finish_reason,
                    spec.session_key or "default",
                )

            clean = hook.finalize_content(context, response.content)
            if response.finish_reason != "error" and is_blank_text(clean):
                empty_content_retries += 1
                if empty_content_retries < spec.max_empty_retries:
                    logger.warning(
                        "Empty response on turn {} for {} ({}/{}); retrying",
                        iteration,
                        spec.session_key or "default",
                        empty_content_retries,
                        spec.max_empty_retries,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    await hook.after_iteration(context)
                    continue
                logger.warning(
                    "Empty response on turn {} for {} after {} retries; attempting finalization",
                    iteration,
                    spec.session_key or "default",
                    empty_content_retries,
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=False)
                retry_messages = self._finalization_retry_messages(messages_for_model)
                response = await self._request_finalization_retry(spec, messages_for_model)
                retry_usage = self._usage_or_estimate(spec, retry_messages, response)
                self._accumulate_usage(usage, retry_usage)
                retry_n = int(retry_usage.get("prompt_tokens") or 0)
                if retry_n > peak_prompt_tokens:
                    peak_prompt_tokens = retry_n
                    peak_request_messages = list(retry_messages)
                raw_usage = self._merge_usage(raw_usage, retry_usage)
                context.response = response
                context.usage = dict(raw_usage)
                context.tool_calls = list(response.tool_calls)
                clean = hook.finalize_content(context, response.content)

            if response.finish_reason == "length" and not is_blank_text(clean):
                length_recovery_count += 1
                if length_recovery_count <= spec.max_length_recoveries:
                    logger.info(
                        "Output truncated on turn {} for {} ({}/{}); continuing",
                        iteration,
                        spec.session_key or "default",
                        length_recovery_count,
                        spec.max_length_recoveries,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=True)
                    messages.append(build_assistant_message(
                        clean,
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    ))
                    messages.append(build_length_recovery_message())
                    await hook.after_iteration(context)
                    continue

            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

            # Delivery workflows (/studio, …): a first text-only answer with no
            # successful tools is almost always a plan/promise. Nudge once so
            # the model continues into real tool calls instead of ending the turn.
            if (
                spec.requires_tool_delivery
                and not tools_used
                and delivery_nudge_count < 1
                and response.finish_reason != "error"
                and assistant_message is not None
            ):
                delivery_nudge_count += 1
                logger.info(
                    "Delivery workflow produced no tools on turn {}; nudging once for {}",
                    iteration,
                    spec.session_key or "default",
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)
                messages.append(assistant_message)
                messages.append(build_delivery_continue_message())
                await hook.after_iteration(context)
                continue

            # Validation belongs to the accepted request, not to its last
            # slice. A final narration cannot turn a missing/red check green.
            if spec.workspace and spec.validate_code_changes and validation.workspace_snapshot is not None:
                validation.observe_workspace(await asyncio.to_thread(workspace_code_snapshot, spec.workspace))
            if (
                (spec.requires_verify_before_done or spec.validate_code_changes)
                and validation.pending
                and response.finish_reason != "error"
                and assistant_message is not None
                and not spec.read_only_tools
                and not spec.plan_read_only
            ):
                limit = spec.verify_fail_nudge_limit
                limit = _MAX_VERIFY_FAIL_NUDGES if limit is None else limit
                attempts = validation.nudge()
                if attempts > limit:
                    final_content = (
                        "The changes are saved, but the task is not validated. "
                        "The agent repeatedly tried to finish without resolving these checks.\n\n"
                        + validation.missing()
                    )
                    error = final_content
                    stop_reason = "validation_failed"
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    await hook.after_iteration(context)
                    break
                logger.info(
                    "Validation pending after edits on turn {}; continuing for {}",
                    iteration, spec.session_key or "default",
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)
                messages.append(assistant_message)
                if validation.failed:
                    spec.effort_escalations += 1
                    messages.append(build_verify_failed_message(last_summary=validation.missing()))
                else:
                    nudge = build_verify_before_done_message()
                    nudge["content"] += "\n\n" + validation.missing()
                    messages.append(nudge)
                await hook.after_iteration(context)
                continue

            # Check for mid-turn injections BEFORE signaling stream end.
            # If injections are found we keep the stream alive (resuming=True)
            # so streaming channels don't prematurely finalize the card.
            should_continue, injection_cycles = await self._try_drain_injections(
                spec, messages, assistant_message, injection_cycles,
                phase="after final response",
                iteration=iteration,
                allow_goal_continue=True,
            )
            if should_continue:
                had_injections = True

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                await hook.after_iteration(context)
                continue

            if response.finish_reason == "error":
                from navin.providers.user_facing_errors import (
                    is_quota_error_text,
                    user_facing_llm_error,
                )

                # A provider that only said "insufficient credit" in prose
                # (no 402, no billing token) is the same refusal: it must not
                # read as "your own key" when the managed key was used.
                if LLMProvider.is_arrearage_response(response) or is_quota_error_text(
                    clean
                ):
                    final_content = _arrearage_error_message(spec.runtime, response)
                else:
                    final_content = user_facing_llm_error(
                        clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
                    )
                stop_reason = "error"
                error = final_content
                self._append_model_error_placeholder(messages)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                # Fail fast: the turn is ending on an error, do not hold the
                # error message hostage to a subagent still running.
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles,
                    phase="after LLM error",
                    wait=False,
                )
                if should_continue:
                    had_injections = True
                    continue
                break
            if is_blank_text(clean):
                final_content = EMPTY_FINAL_RESPONSE_MESSAGE
                stop_reason = "empty_final_response"
                error = final_content
                self._append_final_message(messages, final_content)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles,
                    phase="after empty response",
                    wait=False,
                )
                if should_continue:
                    had_injections = True
                    continue
                break

            messages.append(assistant_message or build_assistant_message(
                clean,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            ))
            await self._emit_checkpoint(
                spec,
                {
                    "phase": "final_response",
                    "iteration": iteration,
                    "model": spec.runtime.model,
                    "assistant_message": messages[-1],
                    "completed_tool_results": [],
                    "pending_tool_calls": [],
                },
            )
            final_content = clean
            context.final_content = final_content
            context.stop_reason = stop_reason
            await hook.after_iteration(context)
            break
        else:
            stop_reason = "max_iterations"
            # Drain any remaining injections so they are appended to the
            # conversation history instead of being re-published as
            # independent inbound messages by _dispatch's finally block.
            # We include them before the no-tools finalization pass so the
            # final response can account for every known follow-up.
            drained_after_max_iterations, injection_cycles = await self._try_drain_injections(
                spec, messages, None, injection_cycles,
                phase="after max_iterations",
            )
            if drained_after_max_iterations:
                had_injections = True
            final_content = None
            if spec.finalize_on_max_iterations:
                if validation.pending:
                    final_content = "The task ended before validation was completed.\n\n" + validation.missing()
                    stop_reason = "validation_failed"
                    error = final_content
                else:
                    final_content = await self._try_finalize_after_max_iterations(
                        spec,
                        hook,
                        messages,
                        usage,
                    )
            if final_content is None and spec.finalize_on_max_iterations:
                final_content = self._max_iterations_fallback(spec)
            if final_content:
                self._append_final_message(messages, final_content)

        if peak_prompt_tokens > 0:
            usage["peak_prompt_tokens"] = peak_prompt_tokens
        timing.log()
        prompt_profile = self._prompt_profile_dict(
            spec,
            peak_request_messages if peak_request_messages is not None else messages,
        )
        return AgentRunResult(
            final_content=final_content,
            messages=messages,
            tools_used=tools_used,
            usage=usage,
            stop_reason=stop_reason,
            error=error,
            tool_events=tool_events,
            had_injections=had_injections,
            prompt_profile=prompt_profile,
            retryable_error=(
                stop_reason == "error"
                and not LLMProvider.is_arrearage_response(response)
                and LLMProvider._is_transient_response(response)
            ),
            retry_after_s=(
                LLMProvider._extract_retry_after_from_response(response)
                if stop_reason == "error" else None
            ),
        )

    @staticmethod
    def _prompt_profile_dict(
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Token breakdown of the peak request this turn, for the context meter."""
        try:
            from navin.agent.prompt_profile import profile_request

            tools = AgentRunner._model_tool_definitions(spec)
            return profile_request(messages, tools).as_dict()
        except Exception:
            return None

    def _build_request_kwargs(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        tool_followup: bool = False,
        empty_retry: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "tools": tools,
            "model": spec.runtime.model,
            "retry_mode": spec.provider_retry_mode,
            "on_retry_wait": spec.retry_wait_callback,
        }
        generation = spec.runtime.generation
        kwargs["temperature"] = generation.temperature
        kwargs["max_tokens"] = generation.max_tokens
        kwargs["reasoning_effort"] = route_reasoning_effort(
            generation.reasoning_effort,
            composer_mode=spec.composer_mode,
            escalations=spec.effort_escalations,
            tool_followup=tool_followup,
            empty_retry=empty_retry,
        )
        if spec.forced_tool and tools:
            from navin.agent.media_intent import forced_tool_choice

            choice = forced_tool_choice(spec.forced_tool, tools, messages)
            if choice is not None:
                kwargs["tool_choice"] = choice
        return kwargs

    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        hook: AgentHook,
        context: AgentHookContext,
        *,
        malformed_retry: bool = False,
        tool_followup: bool = False,
        empty_retry: bool = False,
    ):
        # Finite by default to avoid per-session lock starvation when an LLM
        # request hangs (gateway/network stall). NAVIN_LLM_TIMEOUT_S=0 disables.
        timeout_s = self._wall_timeout_s(spec)

        kwargs = self._build_request_kwargs(
            spec,
            messages,
            tools=self._model_tool_definitions(spec),
            tool_followup=tool_followup,
            empty_retry=empty_retry,
        )
        context.requested_reasoning_effort = kwargs.get("reasoning_effort")
        log_request_profile(
            kwargs.get("messages"),
            kwargs.get("tools"),
            model=spec.runtime.model,
            session_key=spec.session_key,
        )
        wants_streaming = hook.wants_streaming()
        wants_progress_streaming = (
            not wants_streaming
            and spec.stream_progress_deltas
            and spec.progress_callback is not None
            and getattr(spec.runtime.provider, "supports_progress_deltas", False) is True
        )

        progress_state: dict[str, bool] | None = None

        if wants_streaming:
            thinking_buf = ""

            async def _stream(delta: str) -> None:
                if delta:
                    context.streamed_content = True
                await hook.on_stream(context, delta)

            async def _thinking(delta: str) -> None:
                nonlocal thinking_buf
                if not delta:
                    return
                prev_clean = strip_reasoning_tags(thinking_buf)
                thinking_buf += delta
                new_clean = strip_reasoning_tags(thinking_buf)
                incremental = new_clean[len(prev_clean):]
                if incremental:
                    context.streamed_reasoning = True
                    await hook.emit_reasoning(incremental)

            async def _stream_recover() -> None:
                await hook.on_stream_end(context, resuming=True)

            coro = spec.runtime.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream,
                on_thinking_delta=_thinking,
                on_stream_recover=_stream_recover,
            )
        elif wants_progress_streaming:
            stream_buf = ""
            think_extractor = IncrementalThinkExtractor()
            progress_state = {"reasoning_open": False}

            async def _stream_progress(delta: str) -> None:
                nonlocal stream_buf
                if not delta:
                    return
                prev_clean = strip_think(stream_buf)
                stream_buf += delta
                new_clean = strip_think(stream_buf)
                incremental = new_clean[len(prev_clean):]

                if await think_extractor.feed(stream_buf, hook.emit_reasoning):
                    context.streamed_reasoning = True
                    progress_state["reasoning_open"] = True

                if incremental:
                    if progress_state["reasoning_open"]:
                        await hook.emit_reasoning_end()
                        progress_state["reasoning_open"] = False
                    context.streamed_content = True
                    await spec.progress_callback(incremental)

            coro = spec.runtime.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream_progress,
            )
        else:
            coro = spec.runtime.provider.chat_with_retry(**kwargs)

        # Streaming requests also have provider-level idle timeouts
        # (NAVIN_STREAM_IDLE_TIMEOUT_S), but a stream that keeps producing
        # very slow deltas can still run forever. Use a more generous wall-clock
        # timeout for streaming while preserving NAVIN_LLM_TIMEOUT_S=0 as an
        # opt-out for all LLM wall-clock timeouts.
        is_streaming_request = wants_streaming or wants_progress_streaming
        outer_timeout_s = (
            max(300.0, timeout_s * 2)
            if is_streaming_request and timeout_s is not None
            else timeout_s
        )
        try:
            # Tag the request with the session key so providers that support
            # cache-affinity routing (OpenRouter session_id, OpenAI
            # prompt_cache_key) keep the whole session on one warm cache.
            with session_affinity(spec.session_key):
                response = (
                    await coro if outer_timeout_s is None
                    else await asyncio.wait_for(coro, timeout=outer_timeout_s)
                )
        except asyncio.TimeoutError:
            if outer_timeout_s is None:
                response = LLMResponse(
                    content="Error calling LLM: stream stalled",
                    finish_reason="error",
                    error_kind="timeout",
                )
            else:
                response = LLMResponse(
                    content=f"Error calling LLM: timed out after {outer_timeout_s:g}s",
                    finish_reason="error",
                    error_kind="timeout",
                )
        if progress_state and progress_state.get("reasoning_open"):
            await hook.emit_reasoning_end()
        dropped, all_dropped, original_finish_reason = (
            self._drop_malformed_tool_calls(response)
        )
        if (
            all_dropped
            and original_finish_reason in ("tool_calls", "function_call")
            and not malformed_retry
        ):
            logger.warning(
                "Retrying LLM request after all {} malformed tool call(s) were dropped",
                dropped,
            )
            retry_messages = self._malformed_tool_call_retry_messages(
                messages, response.content,
            )
            return await self._request_model(
                spec, retry_messages, hook, context,
                malformed_retry=True,
                tool_followup=tool_followup,
                empty_retry=empty_retry,
            )
        if (
            all_dropped
            and original_finish_reason in ("tool_calls", "function_call")
            and malformed_retry
        ):
            logger.warning(
                "Malformed tool calls persisted after retry; falling back to no-tools request",
            )
            fallback_messages = self._malformed_tool_call_retry_messages(
                messages, response.content,
            )
            return await self._request_no_tools(spec, fallback_messages)
        return response

    @staticmethod
    def _drop_malformed_tool_calls(
        response: LLMResponse,
    ) -> tuple[int, bool, str | None]:
        """Strip tool calls whose name is missing/non-string from the response.

        Returns (dropped_count, all_dropped, original_finish_reason).

        A degenerate call (name=None or "") cannot be executed, and if it were
        persisted into the assistant message it would be replayed on every
        subsequent turn, causing upstream validation errors
        (``tool_use.name: Input should be a valid string``) that permanently
        wedge the session. Dropping it here keeps it out of execution, the
        assistant message, and the saved history in one place.
        """
        calls = getattr(response, "tool_calls", None)
        if not calls:
            return (0, False, getattr(response, "finish_reason", None))
        valid = [tc for tc in calls if tc.has_valid_name()]
        if len(valid) == len(calls):
            return (0, False, getattr(response, "finish_reason", None))
        dropped = len(calls) - len(valid)
        original_finish_reason = getattr(response, "finish_reason", None)
        logger.warning(
            "Dropped {} malformed tool call(s) with missing/non-string name "
            "from LLM response (finish_reason={!r})",
            dropped,
            original_finish_reason,
        )
        response.tool_calls = valid
        if not valid:
            response.finish_reason = "stop"
        return (dropped, not valid, original_finish_reason)

    @staticmethod
    def _malformed_tool_call_retry_messages(
        messages: list[dict[str, Any]],
        assistant_text: str | None,
    ) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        note = (
            "The previous model response attempted to call tools, but every tool call "
            "was malformed: the tool_use blocks had missing or non-string tool names. "
            "Do not answer with a promise to use tools. Either call the required tools again "
            "using valid tool names from the provided tool list and JSON object inputs, or give "
            "a final answer only if no tool is required."
        )
        if assistant_text:
            note += (
                f"\n\nPrevious assistant text before the malformed calls:\n"
                f"{assistant_text}"
            )
        retry_messages.append({"role": "user", "content": note})
        return retry_messages

    async def _request_finalization_retry(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ):
        retry_messages = self._finalization_retry_messages(messages)
        return await self._request_no_tools(spec, retry_messages)

    @staticmethod
    def _finalization_retry_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        retry_messages.append(build_finalization_retry_message())
        return retry_messages

    async def _try_finalize_after_max_iterations(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
        usage: dict[str, int],
        *,
        finalization_message: dict[str, str] | None = None,
    ) -> str | None:
        retry_messages = (
            [*messages, finalization_message]
            if finalization_message is not None
            else self._budget_exhausted_finalization_messages(messages)
        )
        try:
            response = await self._request_no_tools(spec, retry_messages)
        except Exception:
            logger.exception(
                "Budget-exhausted finalization failed for {}; using fallback",
                spec.session_key or "default",
            )
            return None

        raw_usage = self._usage_or_estimate(spec, retry_messages, response)
        self._accumulate_usage(usage, raw_usage)
        if response.finish_reason == "error" or response.has_tool_calls:
            logger.warning(
                "Budget-exhausted finalization returned finish_reason='{}' "
                "with {} tool call(s) for {}; using fallback",
                response.finish_reason,
                len(response.tool_calls),
                spec.session_key or "default",
            )
            return None

        context = AgentHookContext(
            iteration=spec.max_iterations,
            messages=messages,
            response=response,
            usage=dict(raw_usage),
            session_key=spec.session_key,
            model=spec.runtime.model,
        )
        clean = hook.finalize_content(context, response.content)
        if is_blank_text(clean):
            return None
        return clean

    async def _request_no_tools(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> LLMResponse:
        kwargs = self._build_request_kwargs(
            spec, messages, tools=None, empty_retry=True,
        )
        # Same wall clock as the main request path: finalization runs when the
        # turn is already at its limit (empty responses, budget exhausted),
        # which is precisely when a hung request must not hold the session
        # lock forever.
        timeout_s = self._wall_timeout_s(spec)
        coro = spec.runtime.provider.chat_with_retry(**kwargs)
        if timeout_s is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "Finalization request timed out after {}s for {}",
                timeout_s,
                spec.session_key or "default",
            )
            return LLMResponse(
                content=f"Error calling LLM: request timed out after {timeout_s:.0f}s",
                finish_reason="error",
                error_kind="timeout",
            )

    @staticmethod
    def _tool_wall_timeout_s(spec: AgentRunSpec, tool: Any) -> float | None:
        """Wall clock for one tool execution, or None when opted out.

        Priority: the tool's own ``wall_timeout_s`` property (a tool that
        knows it waits legitimately for hours can raise or disable its cap),
        then the spec, then NAVIN_TOOL_TIMEOUT_S, then the 1-hour default.
        """
        per_tool = getattr(tool, "wall_timeout_s", None)
        if isinstance(per_tool, (int, float)) and not isinstance(per_tool, bool):
            return None if per_tool <= 0 else float(per_tool)
        timeout_s = spec.tool_timeout_s
        if timeout_s is None:
            raw = os.environ.get("NAVIN_TOOL_TIMEOUT_S", "3600").strip()
            try:
                timeout_s = float(raw)
            except (TypeError, ValueError):
                timeout_s = 3600.0
        return None if timeout_s <= 0 else timeout_s

    @staticmethod
    def _wall_timeout_s(spec: AgentRunSpec) -> float | None:
        """The effective LLM wall clock for this spec, or None when opted out."""
        timeout_s: float | None = spec.llm_timeout_s
        if timeout_s is None:
            raw = os.environ.get("NAVIN_LLM_TIMEOUT_S", "300").strip()
            try:
                timeout_s = float(raw)
            except (TypeError, ValueError):
                timeout_s = 300.0
        if timeout_s is not None and timeout_s <= 0:
            return None
        return timeout_s

    @staticmethod
    def _budget_exhausted_finalization_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        retry_messages.append(build_budget_exhausted_finalization_message())
        return retry_messages

    @staticmethod
    def _max_iterations_fallback(spec: AgentRunSpec) -> str:
        if spec.max_iterations_message:
            return spec.max_iterations_message.format(
                max_iterations=spec.max_iterations,
            )
        return render_template(
            "agent/max_iterations_message.md",
            strip=True,
            max_iterations=spec.max_iterations,
        )

    def _usage_or_estimate(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        response: LLMResponse,
    ) -> dict[str, int]:
        usage = self._usage_dict(response.usage)
        total = self._usage_total(usage)
        if total > 0:
            usage["total_tokens"] = total
            usage.setdefault("provider_tokens", total)
            return usage
        if response.finish_reason == "error":
            return {}
        return self._estimate_response_usage(spec, messages, response)

    def _estimate_response_usage(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        response: LLMResponse,
    ) -> dict[str, int]:
        try:
            tools = self._model_tool_definitions(spec)
        except Exception:
            tools = None
        prompt_tokens, _ = estimate_prompt_tokens_chain(
            spec.runtime.provider,
            spec.runtime.model,
            messages,
            tools,
        )
        assistant_message = build_assistant_message(
            response.content or "",
            tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
            reasoning_content=response.reasoning_content,
            thinking_blocks=response.thinking_blocks,
        )
        completion_tokens = estimate_message_tokens(assistant_message)
        total_tokens = max(0, prompt_tokens) + max(0, completion_tokens)
        if total_tokens <= 0:
            return {}
        return {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": total_tokens,
            "estimated_tokens": total_tokens,
        }

    @staticmethod
    def _usage_dict(usage: dict[str, Any] | None) -> dict[str, int]:
        if not usage:
            return {}
        result: dict[str, int] = {}
        for key, value in usage.items():
            try:
                result[key] = int(value or 0)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _usage_total(usage: dict[str, int]) -> int:
        return max(0, usage.get("total_tokens", 0) or (
            usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        ))

    @staticmethod
    def _accumulate_usage(target: dict[str, int], addition: dict[str, int]) -> None:
        for key, value in addition.items():
            target[key] = target.get(key, 0) + value

    @staticmethod
    def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        merged = dict(left)
        for key, value in right.items():
            merged[key] = merged.get(key, 0) + value
        return merged

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
        hook: AgentHook | None = None,
        context: AgentHookContext | None = None,
        *,
        tool_failure_counts: dict[str, int] | None = None,
        readonly_call_counts: dict[str, int] | None = None,
        timing: TurnTiming | None = None,
    ) -> tuple[list[Any], list[dict[str, str]], BaseException | None]:
        hook = hook or AgentHook()
        context = context or AgentHookContext(iteration=0, messages=[])
        tool_failure_counts = tool_failure_counts if tool_failure_counts is not None else {}
        readonly_call_counts = (
            readonly_call_counts if readonly_call_counts is not None else {}
        )
        batches = self._partition_tool_batches(spec, tool_calls)
        tool_results: list[tuple[Any, dict[str, str], BaseException | None]] = []
        validation = spec.loop_guard.validation if spec.loop_guard is not None else None
        validating = validation is not None and (spec.requires_verify_before_done or spec.validate_code_changes)
        for batch in batches:
            watch_workspace = bool(
                validating and spec.validate_code_changes and spec.workspace
                and any(call.name in {"exec", "write_stdin", "run_cli_app", "spawn", "manage_files", "lsp"} for call in batch)
            )
            if watch_workspace:
                validation.observe_workspace(await asyncio.to_thread(workspace_code_snapshot, spec.workspace))
            execution_revision = validation.revision if validating else None
            if spec.concurrent_tools and len(batch) > 1:
                batch_results = await asyncio.gather(*(
                    self._run_tool(
                        spec,
                        tool_call,
                        external_lookup_counts,
                        workspace_violation_counts,
                        hook,
                        context,
                        tool_failure_counts=tool_failure_counts,
                        readonly_call_counts=readonly_call_counts,
                        timing=timing,
                    )
                    for tool_call in batch
                ))
                tool_results.extend(batch_results)
            else:
                batch_results = []
                for tool_call in batch:
                    result = await self._run_tool(
                        spec,
                        tool_call,
                        external_lookup_counts,
                        workspace_violation_counts,
                        hook,
                        context,
                        tool_failure_counts=tool_failure_counts,
                        readonly_call_counts=readonly_call_counts,
                        timing=timing,
                    )
                    tool_results.append(result)
                    batch_results.append(result)

            if watch_workspace:
                validation.observe_workspace(await asyncio.to_thread(workspace_code_snapshot, spec.workspace))
            if validating:
                for call, (result, event, _) in zip(batch, batch_results):
                    _, params, _ = spec.tools.prepare_call(call.name, call.arguments)
                    if not isinstance(params, dict):
                        continue
                    validation.observe(
                        call.name, params, result, status=event.get("status", "error"),
                        require_verify=spec.requires_verify_before_done,
                        validate_code=spec.validate_code_changes,
                        is_test_command=_is_test_command,
                        execution_revision=execution_revision,
                    )
                    if spec.workspace and event.get("status") == "ok" and call.name in _EDIT_TOOL_NAMES:
                        validation.sync_known_edits(spec.workspace, edit_paths(params))

        results: list[Any] = []
        events: list[dict[str, str]] = []
        fatal_error: BaseException | None = None
        for result, event, error in tool_results:
            results.append(result)
            events.append(event)
            if error is not None and fatal_error is None:
                fatal_error = error
        return results, events, fatal_error

    async def _run_tool(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
        hook: AgentHook | None = None,
        context: AgentHookContext | None = None,
        *,
        tool_failure_counts: dict[str, int] | None = None,
        readonly_call_counts: dict[str, int] | None = None,
        timing: TurnTiming | None = None,
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        """Time one tool call, whichever of the many exits it takes."""
        if timing is None:
            return await self._dispatch_tool_call(
                spec,
                tool_call,
                external_lookup_counts,
                workspace_violation_counts,
                hook,
                context,
                tool_failure_counts=tool_failure_counts,
                readonly_call_counts=readonly_call_counts,
            )
        started = time.perf_counter()
        try:
            return await self._dispatch_tool_call(
                spec,
                tool_call,
                external_lookup_counts,
                workspace_violation_counts,
                hook,
                context,
                tool_failure_counts=tool_failure_counts,
                readonly_call_counts=readonly_call_counts,
            )
        finally:
            timing.add_tool(tool_call.name, time.perf_counter() - started)

    async def _dispatch_tool_call(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
        hook: AgentHook | None = None,
        context: AgentHookContext | None = None,
        *,
        tool_failure_counts: dict[str, int] | None = None,
        readonly_call_counts: dict[str, int] | None = None,
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        hook = hook or AgentHook()
        context = context or AgentHookContext(iteration=0, messages=[])
        tool_failure_counts = tool_failure_counts if tool_failure_counts is not None else {}
        readonly_call_counts = (
            readonly_call_counts if readonly_call_counts is not None else {}
        )
        hint = "\n\n[Analyze the error above and try a different approach.]"
        prepare_call = getattr(spec.tools, "prepare_call", None)
        tool, params, prep_error = None, tool_call.arguments, None
        if callable(prepare_call):
            with suppress(Exception):
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared
        if isinstance(prep_error, ToolResult):
            hint = tool_error_hint(prep_error)
        # Soft throttle only: after enough identical failures this turn, refuse
        # to re-execute the same call. The run-level invalid-argument guard
        # separately stops batches that cannot reach any executable tool.
        if repeated_tool_failure_is_hard_stop(
            tool_call.name, tool_call.arguments, tool_failure_counts
        ):
            blocked = (
                f"Error: blocked repeated identical {tool_call.name} call after "
                "too many identical failures this turn. Change the arguments, "
                "use a different tool, or continue without it."
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated identical tool call blocked",
            }
            if prep_error and tool is not None:
                event["error_kind"] = "invalid_parameters"
            if spec.fail_on_tool_error:
                return blocked + hint, event, RuntimeError(blocked)
            return blocked + hint, event, None
        lookup_error = repeated_external_lookup_error(
            tool_call.name,
            tool_call.arguments,
            external_lookup_counts,
        )
        if lookup_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated external lookup blocked",
            }
            if spec.fail_on_tool_error:
                return lookup_error + hint, event, RuntimeError(lookup_error)
            return lookup_error + hint, event, None
        spin_error = repeated_readonly_tool_error(
            tool_call.name,
            tool_call.arguments,
            readonly_call_counts,
        )
        if spin_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated identical read blocked",
            }
            if spec.fail_on_tool_error:
                return spin_error + hint, event, RuntimeError(spin_error)
            return spin_error + hint, event, None
        if prep_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            if tool is not None:
                event["error_kind"] = "invalid_parameters"
            handled = self._classify_violation(
                raw_text=prep_error,
                soft_payload=prep_error + hint,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return handled
            escalation = repeated_tool_failure_hint(
                tool_call.name, tool_call.arguments, tool_failure_counts
            )
            return prep_error + hint + (escalation or ""), event, (
                RuntimeError(prep_error) if spec.fail_on_tool_error else None
            )
        # A board step or goal must not be closed before the same validation
        # required for the final answer, even if its old task metadata lacked
        # a validation field. Non-code work leaves this gate inactive.
        validation = spec.loop_guard.validation if spec.loop_guard else None
        closing_work = isinstance(params, dict) and (
            (tool_call.name == "board" and params.get("status") == "done")
            or (tool_call.name == "update_goal" and params.get("action") == "complete")
        )
        if closing_work and validation is not None and validation.pending:
            detail = "Validation required before closing this work.\n" + validation.missing()
            limit = spec.verify_fail_nudge_limit
            limit = _MAX_VERIFY_FAIL_NUDGES if limit is None else limit
            fatal = RuntimeError(detail) if validation.nudge() > limit else None
            return ToolResult.error(detail), {
                "name": tool_call.name, "status": "error", "detail": "validation required before completion",
            }, fatal
        if spec.read_only_tools or spec.plan_read_only:
            candidate = tool
            if candidate is None:
                tools_map = getattr(spec.tools, "_tools", None)
                if isinstance(tools_map, dict):
                    candidate = tools_map.get(tool_call.name)
            if (
                spec.read_only_tools
                and candidate is not None
                and not _call_read_only(candidate, params)
            ):
                blocked = (
                    f"Error: tool '{tool_call.name}' is not allowed in Ask "
                    "(read-only) mode. If the user asked you to do this, call "
                    "set_composer_mode(mode='agent') once (it unlocks the tools "
                    "for this turn) and then retry; otherwise answer with a "
                    "read-only tool or action (read_file, grep, code_index, lsp, "
                    "git status/diff/log, board list) and tell the user to "
                    "switch to Agent for changes."
                )
                event = {
                    "name": tool_call.name,
                    "status": "error",
                    "detail": "blocked by read-only turn",
                }
                return blocked + hint, event, None
            if spec.plan_read_only and candidate is not None:
                from navin.agent.tool_surface import PLAN_SAFE_WRITE_TOOLS

                if tool_call.name not in PLAN_SAFE_WRITE_TOOLS and not _call_read_only(
                    candidate, params
                ):
                    blocked = (
                        f"Error: tool '{tool_call.name}' is not allowed in Plan "
                        "mode. Plan designs without mutating: explore with "
                        "read-only tools and file board tasks. For the "
                        "simple-task exception, call "
                        "set_composer_mode(mode='agent') first, then do the work."
                    )
                    event = {
                        "name": tool_call.name,
                        "status": "error",
                        "detail": "blocked by plan mode",
                    }
                    return blocked + hint, event, None
        from navin.agent.tool_surface import tool_name_is_allowed, tool_name_is_denied

        if spec.allowed_tools is not None and not tool_name_is_allowed(
            tool_call.name, spec.allowed_tools
        ):
            blocked = (
                f"Error: tool '{tool_call.name}' is not in this workflow's "
                "tool set. Stay on code tools (read_file, apply_patch, "
                "verify, git, board, exec)."
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "blocked by workflow allowlist",
            }
            return blocked + hint, event, None

        if tool_name_is_denied(tool_call.name, spec.denied_tools) or tool_name_is_denied(
            tool_call.name, spec.locked_denied_tools
        ):
            mcp_locked = tool_call.name.startswith("mcp_") and tool_name_is_denied(
                tool_call.name, spec.locked_denied_tools
            )
            if mcp_locked:
                blocked = (
                    f"Error: tool '{tool_call.name}' is not wired for this studio "
                    "module. Stay on the desk tools for the current view."
                )
                detail = "blocked by module MCP denylist"
            elif tool_call.name in spec.locked_denied_tools or not spec.composer_mode:
                blocked = (
                    f"Error: tool '{tool_call.name}' is not allowed in the Code "
                    "module. Stay on code tools (read_file, apply_patch, verify, "
                    "lsp, code_index, exec)."
                )
                detail = "blocked by code module denylist"
            else:
                blocked = (
                    f"Error: tool '{tool_call.name}' is not available in "
                    f"{spec.composer_mode} mode this turn. Continue with the "
                    "allowed tools, or call set_composer_mode to switch modes "
                    "if the user asked for that kind of work."
                )
                detail = "blocked by mode denylist"
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": detail,
            }
            return blocked + hint, event, None
        await hook.before_execute_tool(context, tool_call, tool, params)
        tool_wall_s = self._tool_wall_timeout_s(spec, tool)
        try:
            if tool is not None:
                exec_coro = tool.execute(**params)
            else:
                exec_coro = spec.tools.execute(tool_call.name, params)
            if tool_wall_s is None:
                result = await exec_coro
            else:
                result = await asyncio.wait_for(exec_coro, timeout=tool_wall_s)
        except asyncio.TimeoutError as exc:
            # Soft error: a hung tool must cost one tool call, not the turn.
            await hook.on_execute_tool_error(context, tool_call, tool, params, exc)
            payload = (
                f"Error: tool '{tool_call.name}' was cancelled after "
                f"{tool_wall_s:.0f}s without returning. It may be hung "
                "(unreachable server, deadlocked process). Try a different "
                "approach, or the same call with smaller scope."
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": f"timed out after {tool_wall_s:.0f}s",
            }
            escalation = repeated_tool_failure_hint(
                tool_call.name, tool_call.arguments, tool_failure_counts
            )
            return payload + hint + (escalation or ""), event, (
                RuntimeError(payload) if spec.fail_on_tool_error else None
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await hook.on_execute_tool_error(context, tool_call, tool, params, exc)
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            payload = f"Error: {type(exc).__name__}: {exc}"
            handled = self._classify_violation(
                raw_text=str(exc),
                # Preserve legacy exception payloads without the retry hint.
                soft_payload=payload,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return handled
            if spec.fail_on_tool_error:
                return payload, event, exc
            # Soft path only: never abort the whole turn for a stuck tool call.
            # Long-running agents (hours) must keep going; the escalating hint /
            # identical-call throttle is enough to break retry loops.
            escalation = repeated_tool_failure_hint(
                tool_call.name, tool_call.arguments, tool_failure_counts
            )
            return payload + (escalation or ""), event, None

        if is_tool_error_result(tool_call.name, result):
            hint = tool_error_hint(result)
            await hook.on_execute_tool_error(context, tool_call, tool, params, result)
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": result.replace("\n", " ").strip()[:120],
            }
            handled = self._classify_violation(
                raw_text=result,
                soft_payload=result + hint,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return handled
            if spec.fail_on_tool_error:
                return result + hint, event, RuntimeError(result)
            escalation = repeated_tool_failure_hint(
                tool_call.name, tool_call.arguments, tool_failure_counts
            )
            return result + hint + (escalation or ""), event, None

        await hook.after_execute_tool(context, tool_call, tool, params, result)

        reset_tool_failure_count(tool_call.name, tool_call.arguments, tool_failure_counts)
        if tool_call.name in _EDIT_TOOL_NAMES:
            reset_readonly_spin_counts(readonly_call_counts)

        if tool_call.name == "set_composer_mode":
            self._apply_composer_mode_switch(spec, params)

        detail = "" if result is None else str(result)
        detail = detail.replace("\n", " ").strip()
        if not detail:
            detail = "(empty)"
        elif len(detail) > 120:
            detail = detail[:120] + "..."
        event = {"name": tool_call.name, "status": "ok", "detail": detail}
        if tool_call.name == "exec":
            event.update(_exec_event_fields(params, result))
        return result, event, None

    @staticmethod
    def _apply_composer_mode_switch(spec: AgentRunSpec, params: Any) -> None:
        """Make a successful set_composer_mode change the turn policy now.

        The Plan brief documents a simple-task handoff: call
        set_composer_mode(mode='agent') and do the work in the same turn.
        Without this, the switch only took effect on the next turn and the
        handoff was words the runner did not honor. The same holds for Ask:
        the tool answers "Composer mode set to Agent", the editor flips to
        Agent, so the turn must really be Agent from that call on. Keeping
        Ask read-only after a successful switch produced a turn where every
        exec / edit came back "not allowed in Ask mode" under an Agent badge,
        which read as a broken product and burned iterations on retries.
        Tightening applies immediately as well (agent→plan, →ask).
        """
        from navin.agent.tool_surface import denied_tools_for_composer_mode

        mode = ""
        if isinstance(params, dict):
            mode = str(params.get("mode") or "").strip().lower()
        if not mode:
            return
        previous = denied_tools_for_composer_mode(spec.composer_mode)
        spec.denied_tools = frozenset(
            (set(spec.denied_tools) - previous)
            | denied_tools_for_composer_mode(mode)
            | spec.locked_denied_tools
        )
        spec.composer_mode = mode
        spec.plan_read_only = mode == "plan"
        spec.read_only_tools = mode == "ask"

    # SSRF is a hard security block at the tool boundary, but the agent turn
    # should recover conversationally instead of aborting the runtime.
    _SSRF_MARKERS: tuple[str, ...] = (
        "internal/private url detected",
        "private/internal address",
        "private address",
    )
    _SSRF_BOUNDARY_NOTE: str = (
        "This is a non-bypassable security boundary. Stop trying to access "
        "private/internal URLs. Do not retry with curl, wget, encoded IPs, "
        "alternate DNS, redirects, proxies, or another tool. Ask the user for "
        "local files, logs, screenshots, or an explicit safe public URL instead. "
        "If the user explicitly trusts this private URL, ask them to whitelist "
        "the exact IP/CIDR via tools.ssrfWhitelist."
    )

    # Non-SSRF boundary markers returned to the LLM as recoverable tool errors.
    _WORKSPACE_VIOLATION_MARKERS: tuple[str, ...] = (
        "outside the configured workspace",
        "outside allowed directory",
        "working_dir is outside",
        "working_dir could not be resolved",
        "path outside working dir",
        "path traversal detected",
    )

    @classmethod
    def _is_ssrf_violation(cls, text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        return any(marker in lowered for marker in cls._SSRF_MARKERS)

    @classmethod
    def _is_workspace_violation(cls, text: str) -> bool:
        """True when *text* looks like any policy boundary rejection."""
        if not text:
            return False
        lowered = text.lower()
        if cls._is_ssrf_violation(lowered):
            return True
        return any(marker in lowered for marker in cls._WORKSPACE_VIOLATION_MARKERS)

    def _classify_violation(
        self,
        *,
        raw_text: str,
        soft_payload: str,
        event: dict[str, str],
        tool_call: ToolCallRequest,
        workspace_violation_counts: dict[str, int],
    ) -> tuple[Any, dict[str, str], BaseException | None] | None:
        """Classify safety-boundary failures, or return ``None`` to pass through."""
        if self._is_ssrf_violation(raw_text):
            logger.warning(
                "Tool {} blocked by SSRF guard; returning non-retryable tool error: {}",
                tool_call.name,
                raw_text.replace("\n", " ").strip()[:200],
            )
            event["detail"] = self._event_detail("ssrf_violation: ", raw_text)
            return self._ssrf_soft_payload(raw_text), event, None

        if self._is_workspace_violation(raw_text):
            escalation = repeated_workspace_violation_error(
                tool_call.name,
                tool_call.arguments,
                workspace_violation_counts,
            )
            event["detail"] = self._event_detail("workspace_violation: ", raw_text)
            if escalation is not None:
                logger.warning(
                    "Tool {} hit workspace boundary repeatedly; escalating hint",
                    tool_call.name,
                )
                event["detail"] = self._event_detail(
                    "workspace_violation_escalated: ",
                    raw_text,
                )
                return escalation, event, None
            return soft_payload, event, None

        return None

    @classmethod
    def _ssrf_soft_payload(cls, raw_text: str) -> str:
        text = raw_text.strip() or "Error: request blocked by SSRF guard"
        return f"{text}\n\n{cls._SSRF_BOUNDARY_NOTE}"

    @staticmethod
    def _event_detail(prefix: str, text: str, limit: int = 160) -> str:
        return (prefix + text.replace("\n", " ").strip())[:limit]

    async def _emit_checkpoint(
        self,
        spec: AgentRunSpec,
        payload: dict[str, Any],
    ) -> None:
        callback = spec.checkpoint_callback
        if callback is not None:
            await callback(payload)

    @staticmethod
    def _append_final_message(messages: list[dict[str, Any]], content: str | None) -> None:
        if not content:
            return
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            if messages[-1].get("content") == content:
                return
            messages[-1] = build_assistant_message(content)
            return
        messages.append(build_assistant_message(content))

    @staticmethod
    def _append_model_error_placeholder(messages: list[dict[str, Any]]) -> None:
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            return
        messages.append(build_assistant_message(_PERSISTED_MODEL_ERROR_PLACEHOLDER))

    def _partition_tool_batches(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
    ) -> list[list[ToolCallRequest]]:
        if not spec.concurrent_tools:
            return [[tool_call] for tool_call in tool_calls]

        batches: list[list[ToolCallRequest]] = []
        current: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            get_tool = getattr(spec.tools, "get", None)
            tool = get_tool(tool_call.name) if callable(get_tool) else None
            can_batch = bool(tool and tool.call_concurrency_safe(tool_call.arguments))
            if can_batch:
                current.append(tool_call)
                continue
            if current:
                batches.append(current)
                current = []
            batches.append([tool_call])
        if current:
            batches.append(current)
        return batches
