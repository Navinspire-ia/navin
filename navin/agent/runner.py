# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared execution loop for tool-using agents."""

from __future__ import annotations

import asyncio
import inspect
import json
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

from navin.agent.code_validation import (
    CodeValidationState,
    edit_paths,
    exec_cannot_edit,
    exec_runs_only_tests,
    moves_worktree,
    workspace_code_snapshot,
)
from navin.agent.context_governance import (
    ContextGovernanceConfig,
    ContextGovernor,
)
from navin.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from navin.agent.integration_check import (
    build_frontend_gap_nudge,
    build_integration_nudge,
    frontend_gap,
    unwired_frontend_files,
)
from navin.agent.prompt_profile import log_request_profile
from navin.agent.reasoning_router import fit_effort_to_output, route_reasoning_effort
from navin.agent.run_policy import TurnPolicy, bind_turn_policy, reset_turn_policy
from navin.agent.scope_anchor import (
    calls_touch_targets,
    exploration_budget_message,
    is_action_call,
    is_orientation_batch,
    scope_drift_message,
)
from navin.agent.token_budget import room_for_output
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
    IGNORED_NUDGES_BEFORE_FINAL,
    NO_PROGRESS_STOP_FALLBACK,
    build_budget_exhausted_finalization_message,
    build_delivery_continue_message,
    build_finalization_retry_message,
    build_goal_continue_message,
    build_ignored_nudge_reminder_message,
    build_ignored_nudges_finalization_message,
    build_length_recovery_message,
    build_no_action_continue_message,
    build_no_progress_continue_message,
    build_no_progress_finalization_message,
    build_remaining_work_continue_message,
    build_strict_finalization_retry_message,
    build_verify_before_done_message,
    build_verify_failed_message,
    build_wrap_up_message,
    is_blank_text,
    lists_remaining_work,
    record_identical_action,
    repeated_external_lookup_error,
    repeated_identical_action_error,
    repeated_readonly_tool_error,
    repeated_rerun_check_error,
    repeated_tool_failure_hint,
    repeated_tool_failure_is_hard_stop,
    repeated_workspace_violation_error,
    request_used_tools,
    reset_readonly_spin_counts,
    reset_tool_failure_count,
    session_empty_final_response_message,
    session_tool_failure_stop_message,
)

_VERIFY_TOOL_NAMES = frozenset({"verify", "lint", "test_run"})
# Only nudge verify after real edits - pure investigation must not be blocked.
_EDIT_TOOL_NAMES = frozenset(
    {"apply_patch", "edit_file", "write_file", "manage_files"}
)
# Tools that can change files the runner does not see as edits: the workspace
# is snapshotted around them (two full listings), except for a command that
# clearly only reads (ls, cat, git status...) or a read-only project action.
_WORKSPACE_EDITING_TOOLS = frozenset({"exec", "write_stdin", "run_cli_app", "spawn", "manage_files", "lsp", "project"})
_READ_ONLY_PROJECT_ACTIONS = frozenset({"detect", "status", "logs"})
# Project actions that check the code: their outputs (build dirs, caches,
# reports) are not edits, and a file another session changes meanwhile is not
# this agent's work.
_CHECK_PROJECT_ACTIONS = frozenset({"build", "test", "e2e", "lint", "typecheck"})


def _runs_only_checks(call: Any) -> bool:
    arguments = call.arguments if isinstance(call.arguments, dict) else {}
    return call.name == "project" and arguments.get("action") in _CHECK_PROJECT_ACTIONS


def _may_edit_workspace(call: Any) -> bool:
    if call.name not in _WORKSPACE_EDITING_TOOLS:
        return False
    arguments = call.arguments if isinstance(call.arguments, dict) else {}
    if call.name == "exec":
        return not exec_cannot_edit(arguments)
    if call.name == "project":
        return arguments.get("action") not in _READ_ONLY_PROJECT_ACTIONS
    return True
PROGRESS_TOOL_NAMES = _EDIT_TOOL_NAMES | {
    "exec",
    "test_run",
    "verify",
    "lint",
    "start_app",
    "project",
    "open_preview",
    "write_stdin",
}
_PROGRESS_TOOL_NAMES = PROGRESS_TOOL_NAMES
# Pushes per turn for an answer that still lists work. Each one needs a new
# edit since the last, so the cap only bounds a very long multi-task request.
# Each answer that still lists work is sent back while the turn keeps acting;
# the progress check stops it sooner when a reminder changed nothing.
_MAX_REMAINING_WORK_NUDGES = 12
# A subagent may own only the screen while the lead or a teammate owns the
# router: it can hand the wiring back instead of editing files it was not given.
_SUBAGENT_WIRING_NOTE = (
    "\nAs a subagent: when the router or menu is outside the files you were given, "
    "do not edit it; list each screen in your report as left for the lead to wire, "
    "with the file that should register it."
)
# A plan of many steps is worked one reminder per step at most; the progress
# check stops it sooner when a reminder changed nothing.
_MAX_PLAN_NUDGES = 30

# Blocked duplicate calls and explicit no-op results count as a stall.
# Successful reads of different files are progress, including long audits.
_REPEATED_CALL_BLOCKS = frozenset({
    "repeated identical tool call blocked",
    "repeated identical read blocked",
    "repeated identical action blocked",
    "repeated external lookup blocked",
    # Same check, nothing changed since: its earlier result still stands.
    "unchanged check not re-run",
    # Policy refusals: a model trying one workaround after another around a
    # mode or opt-in gate made no progress either, and was never stopped.
    "blocked by plan mode",
    "blocked by read-only turn",
    "blocked by workflow allowlist",
    "blocked by mode denylist",
    "blocked by code module denylist",
    "blocked by module MCP denylist",
    "browser not requested",
    "browser tests not requested",
})
_NO_PROGRESS_NUDGE = 5
_REPEAT_NUDGE_KEY = "action:nudge"
_NO_PROGRESS_STOP = 8
# Look-around batches (list/find/grep/read) touching none of the targets the
# user named before the turn is told, once, where the request pointed. Two
# batches is a repo listing plus a README, i.e. exactly the drift.
_SCOPE_DRIFT_NUDGE = 2
# Consecutive look-around batches that search, whatever the request
# named: a turn that only searches for this long is told once to act or answer
# instead of spending its remaining steps (each one a full model round trip).
# Four is enough to locate the code behind a request with the index tools.
_EXPLORATION_NUDGE = 4
# Audit and review turns: reading a lot IS the work, so the bar is wider.
_EXPLORATION_NUDGE_AUDIT = 8
_AUDIT_MODES = frozenset({"review", "security", "reverse"})
_AUDIT_REQUEST = re.compile(
    r"\b(audit\w*|review\w*|revue\w*|relecture\w*|inspect\w*)\b", re.IGNORECASE,
)
_ROOT_PATHS = frozenset({"", ".", "./", "/"})


def _call_args(call: Any) -> dict[str, Any]:
    args = getattr(call, "arguments", None)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def _look_call_key(call: Any) -> str:
    args = _call_args(call)
    try:
        blob = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(args)
    return f"{getattr(call, 'name', '')}:{blob}"


def _is_wide_look_call(call: Any) -> bool:
    """A call that tours the repository instead of reading one precise thing."""
    name = str(getattr(call, "name", "") or "")
    args = _call_args(call)
    path = str(args.get("path") or "").strip()
    if name == "list_dir":
        return bool(args.get("recursive")) or path in _ROOT_PATHS
    if name == "find_files":
        return path in _ROOT_PATHS and not (args.get("query") or args.get("glob"))
    if name in {"metagraph", "code_index"}:
        return str(args.get("action") or "") in {"overview", "hubs", "cluster"}
    if name == "exec":
        # Shell reads cannot be told apart cheaply: they keep counting.
        return True
    return False


def counts_as_exploration(calls: Any, seen: set[str]) -> bool:
    """True when a look-around batch tours the repo rather than reads precisely.

    Distinct targeted lookups (a read_file, a code_index definition, a scoped
    grep) never count; a repo-wide listing or a call repeated from earlier in
    the run does. ``seen`` is updated with this batch's calls.
    """
    calls = list(calls)
    if not is_orientation_batch(calls):
        return False
    wandering = False
    for call in calls:
        key = _look_call_key(call)
        if key in seen or _is_wide_look_call(call):
            wandering = True
        seen.add(key)
    return wandering


def is_audit_run(spec: Any) -> bool:
    """An explicit audit/review turn, where many reads are the expected work."""
    if str(getattr(spec, "composer_mode", "") or "") in _AUDIT_MODES:
        return True
    for message in getattr(spec, "initial_messages", None) or ():
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict)
            )
        if isinstance(content, str) and _AUDIT_REQUEST.search(content):
            return True
    return False


def exploration_threshold(spec: Any) -> int:
    return _EXPLORATION_NUDGE_AUDIT if is_audit_run(spec) else _EXPLORATION_NUDGE
# Look-only steps (reads or searches) after the latest edit came back green.
# The work is done and proven: re-reading it "to double-check" costs a full
# round trip each and the user waits for nothing.
_WRAP_UP_NUDGE = 3
# Only repeated final answers without new validation or repair are capped.
# Actual edit/check cycles can continue until the accepted task is complete.
_MAX_VERIFY_FAIL_NUDGES = 2
# Continuations a user Stop hook may force in one turn before the answer stands.
_MAX_STOP_HOOK_ROUNDS = 3


def _stop_hook_round_cap(spec: Any) -> int:
    """Stop-hook rounds allowed this turn: more while a /ralph-loop runs here."""
    if getattr(spec, "user_hook_scope", None) == "subagent":
        return _MAX_STOP_HOOK_ROUNDS
    try:
        from navin.agent.ralph_loop import round_cap

        return round_cap(spec.workspace, spec.session_key, _MAX_STOP_HOOK_ROUNDS)
    except Exception:  # noqa: BLE001 - never break a turn over the loop
        return _MAX_STOP_HOOK_ROUNDS
_MAX_TOTAL_VALIDATION_NUDGES = 8
# Browser-driven test runs (Playwright, Cypress, Puppeteer, tests/browser,
# e2e) per accepted request, installs of their browsers included. A CLI session
# asked for a sidebar restyle ran one browser script after another, installed
# Chromium twice and rewrote another feature's test; three runs is enough to
# show the change works or to report why it cannot be shown here.
_MAX_BROWSER_TEST_RUNS = 3
# Browser runs are opt-in: none unless the request itself asks for a browser
# check. "Change the sidebar style" never needed Chromium.
_BROWSER_REQUEST_MARKERS = re.compile(
    r"playwright|puppeteer|cypress|selenium|\be2e\b|end[- ]to[- ]end|navigateur|browser"
    r"|screenshot|capture d'?\s?[ée]cran|test(?:s)? visuel|visual test",
    re.IGNORECASE,
)
# The browser tool on the open web is for scraping (a JS-rendered page, a wall
# the user cleared) or for a request that asks for it. Reading docs or
# checking a fact goes through web_search / web_fetch / scrape: a Chromium
# session per question is slow, noisy and was the habit of every turn.
_SCRAPE_REQUEST_MARKERS = re.compile(
    r"scrap|crawl|extra(?:ire|is|ct)|collect|r[ée]cup[ée]r|t[ée]l[ée]charg|download"
    r"|dataset|appels? d'?offres?|tenders?|portail|portal|connecte[- ]toi|log ?in|sign ?in",
    re.IGNORECASE,
)
_BROWSER_TEST_MARKERS = re.compile(
    r"\b(?:playwright|cypress|puppeteer|webdriverio|selenium)\b"
    r"|(?:^|[\s/'\"])tests?/(?:browser|e2e)/|\be2e\b",
    re.IGNORECASE,
)
_BROWSER_TEST_LAUNCHERS = frozenset({
    "python", "python3", "py", "node", "npx", "npm", "pnpm", "yarn", "bun", "bunx",
    "deno", "uv", "pytest", "playwright", "cypress", "make", "esbuild", "cp",
})


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
    if re.search(r"(?:^|\s)--?(?:collect-only|listTests|list-tests|list|help|version|dry-run)(?=[=\s]|$)", command):
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
            from navin.agent.code_validation import python_test_script

            if python_test_script(simple):
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
_MAX_INJECTIONS_PER_TURN = 1000
_MAX_INJECTION_CYCLES = 5
_MAX_GOAL_CONTINUES_PER_RUN = 2
# Hard ceiling, in slices of max_iterations, for runs that never hand back.
_MAX_UNBOUNDED_SLICES = 40
_MIN_UNBOUNDED_STEPS = 1000


def _call_read_only(tool: Any, params: Any) -> bool:
    """Whether this specific call is side-effect free (per-action aware)."""
    checker = getattr(tool, "call_read_only", None)
    if callable(checker):
        try:
            return bool(checker(params))
        except Exception:
            return bool(getattr(tool, "read_only", False))
    return bool(getattr(tool, "read_only", False))

_BROWSER_SCRIPT_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".mts")
_BROWSER_SCRIPT_IMPORT = re.compile(
    rb"(?:import|require|from)\b[^\n]{0,80}\b(?:playwright|puppeteer|selenium|cypress|webdriverio)\b",
    re.IGNORECASE,
)


def _script_drives_a_browser(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return bool(_BROWSER_SCRIPT_IMPORT.search(handle.read(8192)))
    except OSError:
        return False


# Set by navin.command.builtin._workflow_handler before the user's own words.
_WORKFLOW_FOCUS_MARKER = "Focus / target given by the user:"


def _request_asks_for_browser(
    messages: list[dict[str, Any]], pattern: re.Pattern[str] = _BROWSER_REQUEST_MARKERS,
) -> bool:
    """Whether the user's own request (not an injected notice) asks for a
    browser check (or, with another pattern, for scraping)."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict)
            )
        text = str(content or "")
        if text.lstrip().startswith("["):
            # A workflow turn ("[Build mode] (/forge) ...") carries the user's
            # own words after the focus marker; skipping it read the previous
            # message and refused a build or browser the user just asked for.
            if _WORKFLOW_FOCUS_MARKER not in text:
                continue  # "[Background command finished]" and other runtime notes
            text = text.split(_WORKFLOW_FOCUS_MARKER, 1)[1]
        return bool(pattern.search(text))
    return False


def _browser_page_open(session_key: str | None) -> bool:
    """A page is already open for this chat: "continue" after a captcha or a
    login handoff keeps the browser it was using."""
    from navin.agent.tools.browser import browser_session_active
    from navin.agent.tools.context import current_request_session_key

    return browser_session_active(session_key or current_request_session_key() or "default")


def interrupted_work_recap(messages: list[dict[str, Any]], start: int) -> str:
    """What the turn did before it stopped, from its own tool calls.

    Never asks the user to type "continue" or resend: retryable errors are
    resumed automatically by turn recovery.
    """
    edited: list[str] = []
    commands = failures = 0
    for message in messages[start:]:
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name") or call.get("name") or ""
                raw = function.get("arguments", call.get("arguments"))
                try:
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
                except (TypeError, ValueError):
                    args = {}
                if name in _EDIT_TOOL_NAMES:
                    for path in sorted(edit_paths(args)):
                        if path not in edited:
                            edited.append(path)
                elif name == "exec":
                    commands += 1
        elif role == "tool" and message.get("_tool_status") == "error":
            failures += 1
    if not edited and not commands:
        return ""
    lines = ["", "", "**Done before the stop**"]
    if edited:
        shown = ", ".join(f"`{path}`" for path in edited[:8])
        more = f" (+{len(edited) - 8} more)" if len(edited) > 8 else ""
        lines.append(f"- Files changed ({len(edited)}): {shown}{more}")
    if commands:
        failed = f", {failures} tool call(s) failed" if failures else ""
        lines.append(f"- Commands run: {commands}{failed}")
    lines.append("- Changes are saved; nothing done so far is lost.")
    return "\n".join(lines)


_LOCAL_HOSTS = re.compile(
    r"^(?:[a-z][a-z0-9+.-]*://)?(?:localhost|127(?:\.\d+){3}|0\.0\.0\.0|\[::1\])(?:[:/]|$)",
    re.IGNORECASE,
)


def opens_local_app(params: dict[str, Any]) -> bool:
    """The browser tool pointed at the project's own dev server: a browser
    test by another name. Browsing the web stays free."""
    url = str(params.get("url") or "").strip()
    return bool(url) and bool(_LOCAL_HOSTS.match(url))


def _exec_cwd(workspace: Path | None, working_dir: Any) -> Path | None:
    if isinstance(working_dir, str) and working_dir:
        candidate = Path(working_dir).expanduser()
        if candidate.is_absolute():
            return candidate
        return workspace / candidate if workspace is not None else None
    return workspace


def is_browser_test_command(command: str, cwd: Path | None = None) -> bool:
    """True when a shell command launches a browser test or installs its
    browsers. Inspecting (``ls ~/.cache/ms-playwright``, ``grep playwright``)
    is not a run: only simple commands led by a launcher count. A script with
    no telling name (``python3 build/visual-button-check.py``) counts when it
    imports a browser driver; ``cwd`` resolves its path."""
    here = cwd
    for segment in re.split(r"&&|\|\||[;|\n]", command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        env = []
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            env.append(tokens.pop(0))  # FOO=bar prefixes
        if not tokens:
            continue
        program = tokens[0].rsplit("/", 1)[-1].lower()
        if program == "cd" and len(tokens) > 1 and here is not None:
            here = (here / Path(tokens[1]).expanduser()) if not Path(tokens[1]).is_absolute() else Path(tokens[1])
            continue
        if program not in _BROWSER_TEST_LAUNCHERS:
            continue
        if _BROWSER_TEST_MARKERS.search(" ".join(env + tokens)):
            return True
        for token in tokens[1:]:
            if token.lower().endswith(_BROWSER_SCRIPT_SUFFIXES):
                script = Path(token).expanduser()
                if not script.is_absolute():
                    if here is None:
                        continue
                    script = here / script
                if _script_drives_a_browser(script):
                    return True
    return False


@dataclass(slots=True)
class AgentLoopGuard:
    """Loop protection and validation shared by one accepted request's slices."""

    browser_test_runs: int = 0
    browser_tests_requested: bool | None = None
    browser_scraping: bool | None = None

    external_lookups: dict[str, int] = field(default_factory=dict)
    workspace_violations: dict[str, int] = field(default_factory=dict)
    tool_failures: dict[str, int] = field(default_factory=dict)
    readonly_calls: dict[str, int] = field(default_factory=dict)
    invalid_tool_iterations: int = 0
    file_argument_fallback: frozenset[str] = field(default_factory=frozenset)
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
    # "subagent" makes user hooks fire SubagentStop instead of Stop.
    user_hook_scope: str = "main"
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
    # "Keep going" nudges injected for an active goal in this run. Capped: a
    # model that wants to stop (missing key, real blocker) was pushed back in.
    goal_continue_count: int = 0
    goal_continue_message: GoalContinueMessage | None = None
    # When True, a first final answer with zero successful tool calls is nudged
    # once so delivery workflows (/studio, /campaign, …) cannot end on a plan.
    requires_tool_delivery: bool = False
    # Explicit workflows require checks after every workspace edit.
    requires_verify_before_done: bool = False
    # Paths, branches, services or files the user named in the request. A
    # turn whose first look-around batches touch none of them is reminded
    # once where to look (see navin.agent.scope_anchor).
    scope_targets: tuple[str, ...] = ()
    # Words of the request naming a screen, button or dashboard. A build turn
    # that then changes backend files only is sent back once for the frontend
    # side (see navin.agent.integration_check.frontend_gap).
    ui_terms: tuple[str, ...] = ()
    # Separate asks listed in the request; the final answer is checked once
    # for the ones it never mentions.
    explicit_asks: tuple[str, ...] = ()
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
    # Verify-red retries so far this turn. Each one raises the routed
    # reasoning effort a step (see navin.agent.reasoning_router): the cheap
    # attempt just failed, so its retry should not think at the same depth.
    effort_escalations: int = 0
    # Prompt size the provider reported for the previous step of this run.
    last_prompt_tokens: int = 0
    # A greeting or a request too vague to act on: nothing to think through,
    # whatever effort the user set for real work.
    conversational_turn: bool = False
    # Effort for steps that follow tool results (None = configured effort throughout).
    followup_reasoning_effort: str | None = None
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
    # Send core schemas plus the ones this conversation loaded; the rest is
    # listed in tool_search (agent.tool_deferral). Main turns only.
    defer_tools: bool = False
    finalize_on_max_iterations: bool = True
    # Standalone CLI calls and subagents have no dispatcher to resume a slice.
    # Keep their runner alive, with checkpoints and context compaction, until
    # completion, a real failure, or cancellation. Explicit bounded callers
    # (evals and ephemeral jobs) retain the finite default.
    continue_on_max_iterations: bool = False
    loop_guard: AgentLoopGuard | None = None
    # Browser test runs for this request when no shared loop_guard exists.
    browser_test_runs: int = 0
    browser_tests_requested: bool | None = None
    browser_scraping: bool | None = None
    # CLI, desktop and their delegated work enable this regardless of module.
    # Source edits require tests; config/style edits require appropriate checks.
    validate_code_changes: bool = False
    learning_evaluation: bool = False


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

        definitions = filter_tool_definitions(
            spec.tools.get_definitions(),
            spec.denied_tools | (spec.loop_guard.file_argument_fallback if spec.loop_guard else frozenset()),
            allowed=spec.allowed_tools,
            compact=True,
        )
        if not spec.defer_tools:
            return definitions
        from navin.agent.tool_deferral import visible_tool_definitions

        return visible_tool_definitions(
            definitions,
            spec.session_key,
            always=(spec.forced_tool,) if spec.forced_tool else (),
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
            from navin.session.turn_continuation import ends_with_user_question

            if (
                predicate is not None
                and spec.goal_continue_count < _MAX_GOAL_CONTINUES_PER_RUN
                # The model handed control back with a question: deliver it.
                and not ends_with_user_question(str(assistant_message.get("content") or ""))
                and predicate()
            ):
                spec.goal_continue_count += 1
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

    @staticmethod
    def _watched_iterations(
        spec: AgentRunSpec, current_messages: Callable[[], list[dict[str, Any]]],
    ):
        """Unbounded iterations for runs with no dispatcher, minus the spinning.

        Subagents and one-shot CLI calls keep going past ``max_iterations``
        because nothing would resume them. Every ``max_iterations`` steps the
        work is accounted like a continuation slice; when two slices in a row
        change nothing the iterations end, and the normal max-iterations
        finalization writes where things stand. A hard ceiling backs it up.
        """
        from navin.session.turn_continuation import record_slice

        size = max(1, int(spec.max_iterations or 1))
        ceiling = max(size * _MAX_UNBOUNDED_SLICES, _MIN_UNBOUNDED_STEPS)
        state: dict[str, Any] = {}
        slice_start = len(current_messages())
        for iteration in count():
            if iteration >= ceiling:
                logger.warning("Run stopped at the {}-step ceiling", ceiling)
                return
            if iteration and iteration % size == 0:
                messages = current_messages()
                report, stalled = record_slice(state, messages, slice_start)
                slice_start = len(messages)
                if stalled:
                    logger.warning(
                        "Run stopped: {} steps in a row changed nothing ({})",
                        state.get("_continuation_stalled_calls"),
                        "; ".join(report.failures[-2:]) or "no failure, no change",
                    )
                    return
            yield iteration

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
        from navin.improvement import code as code_improvement

        hook = spec.hook or AgentHook()
        messages = list(spec.initial_messages)
        experiment = await code_improvement.begin(spec, messages)
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
            await code_improvement.finish(experiment, spec)
            context.messages = _snapshot(messages)
            context.stop_reason = "cancelled"
            context.error = None
            context.exception = exc
            raise
        except Exception as exc:
            await code_improvement.finish(experiment, spec)
            context.messages = _snapshot(messages)
            context.stop_reason = "error"
            context.error = f"Error: {type(exc).__name__}: {exc}"
            context.exception = exc
            await hook.on_error(context)
            raise
        else:
            await code_improvement.finish(experiment, spec, result)
            code_improvement.remove_guidance(experiment, result.messages)
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
            code_improvement.remove_guidance(experiment, messages)
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
        run_start = len(messages)
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
        if validation.root is None and spec.workspace:
            validation.root = Path(spec.workspace)
        external_lookup_counts = loop_guard.external_lookups
        workspace_violation_counts = loop_guard.workspace_violations
        # Soft-error retry budget: identical failing calls escalate after a few tries.
        tool_failure_counts = loop_guard.tool_failures
        invalid_tool_iterations = loop_guard.invalid_tool_iterations
        # Identical successful reads/greps: thinking models re-fetch blanked results.
        readonly_call_counts = loop_guard.readonly_calls
        empty_content_retries = 0
        length_recovery_count = 0
        # Text of the replies cut by finish_reason=length since the last tool
        # batch: the final answer is all of it, not only the last piece.
        length_parts: list[str] = []
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
            lambda: spec.denied_tools | loop_guard.file_argument_fallback,
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
        stop_hook_rounds = 0
        # The text answer the delivery nudge was sent after, and where the
        # nudge sits: a blank reply to the nudge keeps that answer.
        delivery_nudged_text: str | None = None
        delivery_nudged_streamed = False
        delivery_nudge_index = -1
        # Did the final answer reach a streaming channel as deltas? A
        # streaming channel drops the final message (it was "streamed"), so an
        # answer produced by a non-streamed finalization call must be pushed
        # through the stream or the user never sees it.
        final_streamed = False
        no_progress_streak = loop_guard.no_progress_streak
        no_progress_nudge_count = loop_guard.no_progress_nudge_count
        # Scope anchor: look-around batches that never touch a named target.
        scope_drift_streak = 0
        scope_anchored = not spec.scope_targets
        scope_nudge_count = 0
        look_around_streak = 0
        look_around_nudged = False
        look_around_seen: set[str] = set()
        exploration_nudge_at = exploration_threshold(spec)
        wrap_up_streak = 0
        wrap_up_nudged_revision = -1
        # The reminder (no progress, exploration, scope) still waiting to be
        # acted on, and how many steps in a row ignored it since.
        pending_nudge: str | None = None
        ignored_nudges = 0
        # Did the turn edit, run or delegate anything yet? A build turn that
        # only read and then ends on text ("next message I start writing") is
        # sent back once into the work; so is one that stops on a list of
        # what remains.
        acted = False
        no_action_nudged = False
        remaining_work_nudges = 0
        # One "keep going" nudge per turn at most among wrap-up, delivery and
        # no-action: stacked, they once turned a finished answer into an
        # invented file and a full re-test. The remaining-work push keeps its
        # own rule: only again after real progress, never for the user's steps.
        soft_nudges = 0
        # A request of several tasks ends one task at a time: each answer that
        # still lists work gets a new push, but only after new work (an edit, a
        # command, a delegation), so a model that is stuck or waiting on the
        # user is not pushed in a loop.
        remaining_work_mark: tuple[int, int] | None = None
        action_steps = 0
        # Open board steps of the conversation's plan: each reminder needs
        # progress since the previous one (an edit or a step that moved).
        plan_nudges = 0
        plan_mark: tuple[Any, ...] | None = None
        plan_repeats = 0
        coverage_nudged = False
        # A screen written but imported by nothing passes every test and build,
        # so it is checked once at the final answer (git dates are wall clock).
        integration_nudged = False
        shortcut_nudged = False
        frontend_gap_nudged = False
        turn_started_wall = time.time()
        timing = TurnTiming(session_key=spec.session_key, model=spec.runtime.model)
        iterations = (
            self._watched_iterations(spec, lambda: messages)
            if spec.continue_on_max_iterations
            else range(spec.max_iterations)
        )
        for iteration in iterations:
            try:
                # Keep the persisted conversation untouched. Context governance
                # may repair or compact historical messages for the model, but
                # those synthetic edits must not shift the append boundary used
                # later when the caller saves only the new turn.
                with measure(timing, PHASE_CONTEXT):
                    messages_for_model = await asyncio.to_thread(
                        self.context_governor.prepare_for_model,
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
            timing.add_first_token(context.first_token_s)
            # True once a non-streamed finalization call replaced this answer.
            finalized = False
            context.response = response
            context.tool_calls = list(response.tool_calls)

            raw_content = response.content if isinstance(response.content, str) else ""
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
            if prompt_n > 0:
                spec.last_prompt_tokens = prompt_n
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
                    failed_names = frozenset(call.name for call in response.tool_calls)
                    available = {
                        ToolRegistry._schema_name(definition)
                        for definition in self._model_tool_definitions(spec)
                    }
                    alternatives = (available & {"apply_patch", "edit_file", "write_file"}) - failed_names
                    if (
                        not loop_guard.file_argument_fallback
                        and failed_names <= {"apply_patch", "edit_file", "write_file"}
                        and alternatives and not spec.read_only_tools and not spec.plan_read_only
                    ):
                        # One bounded recovery through the remaining file tools.
                        # Keep the registry and permissions intact; only change
                        # what the model is offered for this accepted request.
                        loop_guard.file_argument_fallback = failed_names
                        invalid_tool_iterations = loop_guard.invalid_tool_iterations = 0
                        results[-1] = str(results[-1]) + (
                            f"\n\nFile operation recovery: {names} could not be called correctly. "
                            f"Continue the task using {', '.join(sorted(alternatives))}. "
                            "Read existing files before editing; use exact old_text/new_text "
                            "replacements, and full content only for new files or intentional "
                            "full rewrites. No rejected call changed a file."
                        )
                    else:
                        fatal_error = RuntimeError(
                            f"Stopped after {invalid_tool_iterations} consecutive responses "
                            f"with invalid tool arguments for {names}. No tool from these "
                            "responses was executed."
                        )
                completed_tool_results: list[dict[str, Any]] = []

                def normalized_results() -> list[Any]:
                    return [
                        self.context_governor.normalize_tool_result(
                            governance_config, tool_call.id, tool_call.name, result,
                        )
                        for tool_call, result in zip(response.tool_calls, results)
                    ]

                # An oversized result is written to disk (then referenced):
                # do that in a worker thread, not on the event loop.
                limit = governance_config.max_tool_result_chars
                if any(
                    isinstance(result, list) or (isinstance(result, str) and len(result) > limit)
                    for result in results
                ):
                    contents = await asyncio.to_thread(normalized_results)
                else:
                    contents = normalized_results()
                for tool_call, content, tool_event in zip(response.tool_calls, contents, new_events):
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": content,
                    }
                    if edits := context.file_edit_events.get(tool_call.id):
                        tool_message["_file_edits"] = edits
                    tool_message["_tool_status"] = tool_event.get("status")
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)
                if fatal_error is not None:
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    # The technical detail goes to the logs (and ``error``);
                    # the user reads one plain sentence in their language.
                    logger.warning(
                        "Turn stopped on a failing tool for {}: {}",
                        spec.session_key or "default",
                        error,
                    )
                    final_content = session_tool_failure_stop_message(spec.session_key)
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
                length_parts.clear()
                if new_events and all(
                    event.get("progress") == "none"
                    or (
                        event.get("status") == "error"
                        and event.get("detail") in _REPEATED_CALL_BLOCKS
                    )
                    for event in new_events
                ):
                    no_progress_streak += 1
                else:
                    no_progress_streak = 0
                loop_guard.no_progress_streak = no_progress_streak
                if no_progress_streak >= _NO_PROGRESS_STOP:
                    logger.warning(
                        "Only blocked or unchanged calls for {} iterations in {}; stopping",
                        no_progress_streak,
                        spec.session_key or "default",
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    if validation.pending:
                        final_content = validation.completion_message(reason="no_progress")
                        error = final_content
                        stop_reason = "validation_failed"
                    else:
                        final_content = await self._try_finalize_after_max_iterations(
                            spec, hook, messages, usage,
                            finalization_message=build_no_progress_finalization_message(),
                        )
                        stop_reason = "no_progress"
                    if is_blank_text(final_content):
                        final_content = NO_PROGRESS_STOP_FALLBACK
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    break
                if pending_nudge is not None:
                    if pending_nudge == "repeat":
                        ignored = any(
                            repeated_identical_action_error(
                                call.name, call.arguments, loop_guard.readonly_calls
                            )
                            for call in response.tool_calls
                        )
                    elif pending_nudge == "no_progress":
                        ignored = no_progress_streak > 0
                    elif pending_nudge == "scope":
                        # The targets are read from the user's prose and can be
                        # wrong; missing one never costs the turn its tools.
                        # Real drift is what the exploration check counts.
                        ignored = False
                    elif pending_nudge == "wrap_up":
                        ignored = is_orientation_batch(response.tool_calls)
                    else:
                        # Targeted, distinct lookups after the reminder are
                        # progress, not ignoring it.
                        ignored = counts_as_exploration(
                            response.tool_calls, set(look_around_seen)
                        )
                    if not ignored:
                        pending_nudge = None
                        ignored_nudges = 0
                    else:
                        ignored_nudges += 1
                        if ignored_nudges >= IGNORED_NUDGES_BEFORE_FINAL:
                            logger.warning(
                                "Reminder ignored {} times in a row ({}) for {}; "
                                "asking for the final answer without tools",
                                ignored_nudges, pending_nudge, spec.session_key or "default",
                            )
                            if hook.wants_streaming():
                                await hook.on_stream_end(context, resuming=False)
                            if validation.pending:
                                final_content = validation.completion_message(reason="no_progress")
                                error = final_content
                                stop_reason = "validation_failed"
                            else:
                                final_content = await self._try_finalize_after_max_iterations(
                                    spec, hook, messages, usage,
                                    finalization_message=build_ignored_nudges_finalization_message(
                                        ignored_nudges
                                    ),
                                )
                                stop_reason = "no_progress"
                            if is_blank_text(final_content):
                                final_content = NO_PROGRESS_STOP_FALLBACK
                            self._append_final_message(messages, final_content)
                            context.final_content = final_content
                            context.error = error
                            context.stop_reason = stop_reason
                            await hook.after_iteration(context)
                            break
                        messages.append(build_ignored_nudge_reminder_message(ignored_nudges))
                if (
                    no_progress_streak == _NO_PROGRESS_NUDGE
                    and no_progress_nudge_count < 1
                ):
                    no_progress_nudge_count += 1
                    loop_guard.no_progress_nudge_count = no_progress_nudge_count
                    messages.append(build_no_progress_continue_message())
                    pending_nudge = "no_progress"
                if loop_guard.readonly_calls.pop(_REPEAT_NUDGE_KEY, 0) and pending_nudge is None:
                    pending_nudge = "repeat"
                if any(is_action_call(call) for call in response.tool_calls):
                    acted = True
                    action_steps += 1
                if is_orientation_batch(response.tool_calls):
                    # Distinct targeted lookups neither count nor reset: only
                    # repo-wide listings and repeated lookups do.
                    if counts_as_exploration(response.tool_calls, look_around_seen):
                        look_around_streak += 1
                    if look_around_streak >= exploration_nudge_at and not look_around_nudged:
                        look_around_nudged = True
                        logger.info(
                            "Exploration check: {} search batches in a row for {}",
                            look_around_streak, spec.session_key or "default",
                        )
                        messages.append(exploration_budget_message(look_around_streak))
                        pending_nudge = "look_around"
                else:
                    look_around_streak = 0
                # Validated work followed by nothing but reading: wrap up.
                if (
                    validation.revision
                    and not validation.pending
                    and is_orientation_batch(response.tool_calls)
                ):
                    wrap_up_streak += 1
                    if (
                        wrap_up_streak >= _WRAP_UP_NUDGE
                        and wrap_up_nudged_revision != validation.revision
                        and pending_nudge is None
                        and soft_nudges < 1
                    ):
                        soft_nudges += 1
                        wrap_up_nudged_revision = validation.revision
                        logger.info(
                            "Wrap-up check: {} look-only steps after green checks for {}",
                            wrap_up_streak, spec.session_key or "default",
                        )
                        messages.append(build_wrap_up_message(wrap_up_streak))
                        pending_nudge = "wrap_up"
                else:
                    wrap_up_streak = 0
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
                        pending_nudge = "scope"
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
                        "Empty response on turn {} for {} ({}/{}, finish_reason={}, effort={}); retrying with less thinking",
                        iteration,
                        spec.session_key or "default",
                        empty_content_retries,
                        spec.max_empty_retries,
                        response.finish_reason,
                        context.requested_reasoning_effort,
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
                finalized = True
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
                if is_blank_text(clean):
                    response, clean = await self._strict_finalization_retry(
                        spec, hook, context, messages_for_model, response, clean,
                    )

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
                    # The cut often falls on a space that cleaning strips
                    # from the seam: keep it so the joined text reads right.
                    length_parts.append(clean + (" " if raw_content[-1:].isspace() else ""))
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
                # A continuation of a request that already ran tools has
                # delivered: nudging it made the agent invent a file nobody asked for.
                and not request_used_tools(messages)
                and delivery_nudge_count < 1
                and soft_nudges < 1
                and response.finish_reason != "error"
                and assistant_message is not None
            ):
                delivery_nudge_count += 1
                soft_nudges += 1
                logger.info(
                    "Delivery workflow produced no tools on turn {}; nudging once for {}",
                    iteration,
                    spec.session_key or "default",
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)
                messages.append(assistant_message)
                messages.append(build_delivery_continue_message())
                delivery_nudged_text = clean
                delivery_nudged_streamed = context.streamed_content
                delivery_nudge_index = len(messages) - 1
                await hook.after_iteration(context)
                continue

            # User Stop / SubagentStop hooks (hooks.json) may refuse the final
            # answer with a reason; the reason becomes the next instruction.
            if (
                assistant_message is not None
                and response.finish_reason != "error"
                and stop_hook_rounds < _stop_hook_round_cap(spec)
            ):
                from navin.agent.user_hooks import stop_hook_reason

                stop_reason_text = await stop_hook_reason(
                    spec.workspace, spec.session_key, clean,
                    stop_hook_active=stop_hook_rounds > 0,
                    subagent=spec.user_hook_scope == "subagent",
                    notify=hook.emit_user_notice,
                    transcript=messages,
                )
                if stop_reason_text:
                    stop_hook_rounds += 1
                    logger.info("Stop hook asked to continue for {}", spec.session_key or "default")
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=True)
                    messages.append(assistant_message)
                    messages.append({"role": "user", "content": f"[Stop hook] {stop_reason_text}"})
                    await hook.after_iteration(context)
                    continue

            # Validation belongs to the accepted request, not to its last
            # slice. A final narration cannot turn a missing/red check green.
            if spec.workspace and spec.validate_code_changes and validation.workspace_snapshot is not None:
                snapshot = await asyncio.to_thread(workspace_code_snapshot, spec.workspace)
                if validation.commands_may_edit:
                    await validation.observe_workspace_async(snapshot, root=spec.workspace)
                else:
                    # Only file tools and checks ran: their edits are already
                    # counted, so other changes belong to a parallel session or
                    # the user. Validating them sent a front-end audit to run
                    # the back end's tests.
                    validation.rebaseline(snapshot, root=spec.workspace)
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
                await validation.warm_related_tests()
                attempts = validation.nudge()
                # Two identical refusals stop a stuck finish. The total cap
                # also stops a loop whose state keeps moving without progress
                # (a test rewriting a tracked file made one run 496 turns).
                if attempts > limit or validation.total_nudges > _MAX_TOTAL_VALIDATION_NUDGES:
                    final_content = validation.completion_message(
                        reason="repeated" if attempts > limit else "no_progress",
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

            # Build turns (/forge, ...): the user accepted the task. Ending on
            # text after reads only, or on a list of the steps still to do,
            # is the turn giving up halfway. One reminder each, never a loop.
            build_turn = (
                spec.requires_verify_before_done
                and response.finish_reason != "error"
                and assistant_message is not None
                and not spec.read_only_tools
                and not spec.plan_read_only
            )
            # Back done, front forgotten: checked on every turn that writes
            # code (CLI, desktop and subagents), not only on workflow commands.
            # Still one reminder each, so a turn never loops on it.
            integration_turn = (
                (spec.requires_verify_before_done or spec.validate_code_changes)
                and response.finish_reason != "error"
                and assistant_message is not None
                and not spec.read_only_tools
                and not spec.plan_read_only
            )
            continue_prompt: dict[str, str] | None = None
            unwired: list[str] = []
            if (
                integration_turn
                and acted
                and not integration_nudged
                and not validation.pending
                and validation.paths
                and spec.workspace
            ):
                unwired = await asyncio.to_thread(
                    unwired_frontend_files, spec.workspace, sorted(validation.paths),
                    since=turn_started_wall,
                )
            backend_only: list[str] = []
            if (
                integration_turn
                and acted
                and not unwired
                and not frontend_gap_nudged
                and spec.ui_terms
                and not validation.pending
                and validation.paths
                and spec.workspace
            ):
                backend_only = await asyncio.to_thread(
                    frontend_gap, spec.workspace, sorted(validation.paths),
                )
            if build_turn and not acted and tools_used and not no_action_nudged and soft_nudges < 1 and (
                spec.requires_tool_delivery or lists_remaining_work(clean)
            ):
                no_action_nudged = True
                soft_nudges += 1
                continue_prompt = build_no_action_continue_message()
            elif unwired:
                integration_nudged = True
                logger.info(
                    "Build turn ended with {} unwired screen(s) on turn {}; continuing once for {}",
                    len(unwired), iteration, spec.session_key or "default",
                )
                continue_prompt = {
                    **build_remaining_work_continue_message(),
                    "content": build_integration_nudge(unwired) + (
                        _SUBAGENT_WIRING_NOTE if spec.user_hook_scope == "subagent" else ""
                    ),
                }
            elif backend_only:
                frontend_gap_nudged = True
                logger.info(
                    "Build turn for a user-facing request changed backend files only on turn {};"
                    " continuing once for {}",
                    iteration, spec.session_key or "default",
                )
                continue_prompt = {
                    **build_remaining_work_continue_message(),
                    "content": build_frontend_gap_nudge(spec.ui_terms, backend_only),
                }
            elif (
                build_turn
                and acted
                and remaining_work_nudges < _MAX_REMAINING_WORK_NUDGES
                and (validation.revision, action_steps) != remaining_work_mark
                and not validation.pending
                and lists_remaining_work(clean)
            ):
                remaining_work_nudges += 1
                remaining_work_mark = (validation.revision, action_steps)
                logger.info(
                    "Build turn answer still lists work on turn {} (reminder {}); continuing for {}",
                    iteration, remaining_work_nudges, spec.session_key or "default",
                )
                continue_prompt = build_remaining_work_continue_message()
            if (
                continue_prompt is None
                and build_turn
                and acted
                and not shortcut_nudged
                and not validation.pending
                and validation.paths
                and spec.workspace
                and spec.user_hook_scope != "subagent"
            ):
                from navin.agent.shortcut_check import build_shortcut_nudge, shortcut_findings

                shortcuts = await asyncio.to_thread(
                    shortcut_findings, spec.workspace, sorted(validation.paths),
                    since=turn_started_wall,
                )
                if shortcuts:
                    shortcut_nudged = True
                    logger.info(
                        "Build turn added {} shortcut(s) on turn {}; continuing once for {}",
                        len(shortcuts), iteration, spec.session_key or "default",
                    )
                    continue_prompt = {
                        **build_remaining_work_continue_message(),
                        "content": build_shortcut_nudge(shortcuts),
                    }
            if (
                continue_prompt is None
                and build_turn
                and plan_nudges < _MAX_PLAN_NUDGES
                and not validation.pending
                and spec.workspace
                and spec.session_key
                and spec.user_hook_scope != "subagent"
            ):
                from navin.board.auto_advance import (
                    asks_user,
                    build_open_plan_message,
                    open_plan_steps,
                    reports_failure,
                )

                if not asks_user(clean) and not reports_failure(clean):
                    steps = await asyncio.to_thread(
                        open_plan_steps, spec.workspace, spec.session_key,
                    )
                    mark = (
                        validation.revision,
                        tuple((step["id"], step["status"]) for step in steps),
                    )
                    # Same open steps after a reminder: the model answered with
                    # text only. Push once more, firmer, then let it through.
                    repeat = bool(steps) and mark == plan_mark and plan_repeats < 1
                    if steps and (mark != plan_mark or repeat):
                        plan_nudges += 1
                        plan_repeats = plan_repeats + 1 if repeat else 0
                        plan_mark = mark
                        logger.info(
                            "Build turn ended with {} open plan step(s) on turn {} for {}",
                            len(steps), iteration, spec.session_key,
                        )
                        continue_prompt = {
                            **build_remaining_work_continue_message(),
                            "content": build_open_plan_message(steps, repeat=repeat),
                        }
            if (
                continue_prompt is None
                and build_turn
                and acted
                and clean
                and not coverage_nudged
                and len(spec.explicit_asks) >= 2
                and not validation.pending
                and spec.user_hook_scope != "subagent"
            ):
                from navin.agent.request_checklist import build_coverage_nudge, missing_asks
                from navin.board.auto_advance import asks_user, reports_failure

                missing = missing_asks(spec.explicit_asks, clean)
                if missing and not asks_user(clean) and not reports_failure(clean):
                    coverage_nudged = True
                    logger.info(
                        "Build turn answer drops {} of {} listed ask(s) on turn {}; continuing once for {}",
                        len(missing), len(spec.explicit_asks), iteration,
                        spec.session_key or "default",
                    )
                    continue_prompt = {
                        **build_remaining_work_continue_message(),
                        "content": build_coverage_nudge(missing),
                    }
            if continue_prompt is not None:
                logger.info(
                    "Build turn ended {} on turn {}; continuing once for {}",
                    "before any edit" if not acted else "with work left",
                    iteration, spec.session_key or "default",
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)
                messages.append(assistant_message)
                messages.append(continue_prompt)
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
                # Ending on an error must not erase what the turn already did:
                # a 16-minute run that died on a provider refusal showed only
                # the refusal, and the user could not tell what was saved.
                final_content += interrupted_work_recap(messages, run_start)
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
            if (
                is_blank_text(clean)
                and delivery_nudged_text
                and len(messages) == delivery_nudge_index + 1
            ):
                # The nudge asked for tools after a text answer and got only
                # blanks back, with nothing done in between: that answer
                # stands. Ending on "couldn't produce a final answer" here
                # threw away a reply the model had already written.
                logger.info(
                    "Blank reply to the delivery nudge on turn {} for {}; keeping the previous answer",
                    iteration,
                    spec.session_key or "default",
                )
                messages.pop()
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
                final_content = delivery_nudged_text
                final_streamed = delivery_nudged_streamed
                context.final_content = final_content
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                break
            if is_blank_text(clean):
                final_content = session_empty_final_response_message(
                    spec.session_key
                ) + interrupted_work_recap(messages, run_start)
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
            # A reply continued after an output-limit cut is delivered whole:
            # non-streaming channels only receive final_content.
            if length_parts:
                seam = " " if raw_content[:1].isspace() and not length_parts[-1].endswith(" ") else ""
                final_content = "".join(length_parts) + seam + clean
            else:
                final_content = clean
            final_streamed = context.streamed_content and not finalized
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
                    final_content = validation.completion_message(reason="ended")
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

        if (
            final_content
            and not final_streamed
            and stop_reason in ("completed", "no_progress")
            and hook.wants_streaming()
        ):
            # The loop marks the final message as already streamed for
            # streaming channels, which then drop it: deliver it as one
            # last stream segment instead.
            tail = AgentHookContext(
                iteration=spec.max_iterations,
                messages=messages,
                session_key=spec.session_key,
                model=spec.runtime.model,
                final_content=final_content,
                stop_reason=stop_reason,
            )
            await hook.on_stream(tail, final_content)
            await hook.on_stream_end(tail, resuming=False)
        if peak_prompt_tokens > 0:
            usage["peak_prompt_tokens"] = peak_prompt_tokens
        timing.log()
        # Token counting walks the whole conversation: 2.3 s measured on the
        # loop for a long subagent run, so it runs in a worker thread.
        prompt_profile = await asyncio.to_thread(
            self._prompt_profile_dict,
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
        kwargs["max_tokens"] = room_for_output(
            generation.max_tokens, spec.runtime.context_window_tokens, spec.last_prompt_tokens,
        )
        effort = route_reasoning_effort(
            generation.reasoning_effort,
            composer_mode=spec.composer_mode,
            escalations=spec.effort_escalations,
            conversational=spec.conversational_turn,
            followup_effort=spec.followup_reasoning_effort,
            tool_followup=tool_followup,
            empty_retry=empty_retry,
        )
        # Only a budget the full window squeezed caps thinking: a configured
        # small max_tokens is the provider's to stretch for thinking.
        if kwargs["max_tokens"] != generation.max_tokens:
            effort = fit_effort_to_output(effort, kwargs["max_tokens"])
        kwargs["reasoning_effort"] = effort
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
        # A premature "done" must not reach CLI/desktop as a visible final
        # while the completion gate is still waiting for tests. Tool-bound
        # commentary is released as soon as the response contains real calls.
        defer_unverified_text = bool(
            spec.loop_guard and spec.loop_guard.validation.pending
            and not spec.read_only_tools and not spec.plan_read_only
        )
        deferred_text: list[str] = []

        request_started = time.perf_counter()

        def _first_token() -> None:
            if context.first_token_s is None:
                context.first_token_s = time.perf_counter() - request_started

        # Something visible must move while the model works: a status line
        # at once, then the call being written as its arguments stream in
        # (a five-file write_file is 10-20s of silence otherwise).
        drafting = _ToolDraftStatus(hook)
        await drafting.waiting(spec.runtime.model)

        async def _tool_delta(delta: dict[str, Any]) -> None:
            _first_token()
            await drafting.feed(delta)

        if wants_streaming:
            thinking_buf = ""
            # Whether the open stream segment already showed answer text.
            segment_state = {"has_text": False}

            async def _stream(delta: str) -> None:
                if delta:
                    _first_token()
                if defer_unverified_text:
                    deferred_text.append(delta)
                    return
                if delta:
                    context.streamed_content = True
                    segment_state["has_text"] = True
                await hook.on_stream(context, delta)

            async def _thinking(delta: str) -> None:
                nonlocal thinking_buf
                if not delta:
                    return
                _first_token()
                prev_clean = strip_reasoning_tags(thinking_buf)
                thinking_buf += delta
                new_clean = strip_reasoning_tags(thinking_buf)
                incremental = new_clean[len(prev_clean):]
                if incremental:
                    context.streamed_reasoning = True
                    await hook.emit_reasoning(incremental)

            async def _stream_recover() -> None:
                deferred_text.clear()
                # The retry streams the answer from the start, so the partial
                # text already on screen is withdrawn instead of left above it.
                context.stream_discarded = segment_state["has_text"]
                segment_state["has_text"] = False
                try:
                    await hook.on_stream_end(context, resuming=True)
                finally:
                    context.stream_discarded = False

            coro = spec.runtime.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream,
                on_thinking_delta=_thinking,
                on_stream_recover=_stream_recover,
                on_tool_call_delta=_tool_delta,
            )
        elif wants_progress_streaming:
            stream_buf = ""
            think_extractor = IncrementalThinkExtractor()
            progress_state = {"reasoning_open": False}

            async def _stream_progress(delta: str) -> None:
                nonlocal stream_buf
                if not delta:
                    return
                _first_token()
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
                    if defer_unverified_text:
                        deferred_text.append(incremental)
                    else:
                        context.streamed_content = True
                        await spec.progress_callback(incremental)

            coro = spec.runtime.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream_progress,
                on_tool_call_delta=_tool_delta,
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
        if deferred_text and response.should_execute_tools:
            context.streamed_content = True
            if wants_streaming:
                await hook.on_stream(context, "".join(deferred_text))
            elif wants_progress_streaming:
                await spec.progress_callback("".join(deferred_text))
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

    async def _strict_finalization_retry(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        context: AgentHookContext,
        messages: list[dict[str, Any]],
        response: LLMResponse,
        clean: str,
    ) -> tuple[LLMResponse, str]:
        """One explicit last attempt before telling the user the reply was empty."""
        logger.warning(
            "Finalization reply was blank for {}; trying the strict prompt",
            spec.session_key or "default",
        )
        try:
            retry = await self._request_no_tools(
                spec, [*messages, build_strict_finalization_retry_message()],
            )
        except Exception:
            logger.exception("Strict finalization retry failed for {}", spec.session_key or "default")
            return response, clean
        retry_clean = hook.finalize_content(context, retry.content)
        if is_blank_text(retry_clean):
            return response, clean
        context.response = retry
        context.tool_calls = []
        return retry, retry_clean

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
        # A subagent hands its parent the list of files it changed, docs and
        # command-made files included, even on a turn that validates nothing:
        # without it the parent took the work for lost and redid it.
        listing_changes = validation is not None and spec.user_hook_scope == "subagent"

        async def run_and_report(tool_call: ToolCallRequest) -> tuple[Any, dict[str, str], BaseException | None]:
            outcome = await self._run_tool(
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
            await hook.on_tool_done(context, tool_call, outcome[0], outcome[1])
            return outcome

        for batch in batches:
            tree_moved = any(
                moves_worktree(call.name, call.arguments if isinstance(call.arguments, dict) else {})
                for call in batch
            )
            watch_workspace = bool(
                validating and spec.validate_code_changes and spec.workspace
                and (tree_moved or any(_may_edit_workspace(call) for call in batch))
            )
            # Files a pure test run rewrites (a generated module, a fixture)
            # are its side effects: counting them as edits made the run's own
            # green result stale and asked for the same tests again, forever.
            tests_only = not tree_moved and all(
                (call.name == "exec" and exec_runs_only_tests(call.arguments, _is_test_command))
                or _runs_only_checks(call)
                for call in batch
            )
            if watch_workspace and not tests_only:
                validation.commands_may_edit = True
            if watch_workspace:
                await validation.observe_workspace_async(
                    await asyncio.to_thread(workspace_code_snapshot, spec.workspace), root=spec.workspace,
                )
            tree_before = None
            if (
                listing_changes and not watch_workspace and spec.workspace
                and not tree_moved and not tests_only
                and any(_may_edit_workspace(call) for call in batch)
            ):
                tree_before = await asyncio.to_thread(workspace_code_snapshot, spec.workspace)
            execution_revision = validation.revision if validating else None
            if spec.concurrent_tools and len(batch) > 1:
                pending_tools = [asyncio.create_task(run_and_report(tool_call)) for tool_call in batch]
                try:
                    batch_results = await asyncio.gather(*pending_tools)
                except BaseException:
                    # A fail-closed hook (for example, a durable journal write)
                    # must not leave sibling operations executing unobserved.
                    for pending_tool in pending_tools:
                        if not pending_tool.done():
                            pending_tool.cancel()
                    await asyncio.gather(*pending_tools, return_exceptions=True)
                    raise
                tool_results.extend(batch_results)
            else:
                batch_results = []
                for tool_call in batch:
                    result = await run_and_report(tool_call)
                    tool_results.append(result)
                    batch_results.append(result)

            if watch_workspace:
                after = await asyncio.to_thread(workspace_code_snapshot, spec.workspace)
                if tree_moved or tests_only:
                    # Checked-out commits are not the agent's edits.
                    validation.rebaseline(after, root=spec.workspace)
                else:
                    await validation.observe_workspace_async(after, root=spec.workspace)
            if tree_before is not None:
                validation.note_tree_changes(
                    tree_before, await asyncio.to_thread(workspace_code_snapshot, spec.workspace),
                )
            if validating or listing_changes:
                for call, (result, event, _) in zip(batch, batch_results):
                    _, params, _ = spec.tools.prepare_call(call.name, call.arguments)
                    if not isinstance(params, dict):
                        continue
                    if listing_changes:
                        validation.note_tool_writes(
                            call.name, params, status=event.get("status", "error"),
                        )
                    if not validating:
                        continue
                    if event.get("detail") == "unchanged check not re-run":
                        # Not a run: the earlier identical result stays recorded.
                        continue
                    validation.observe(
                        call.name, params, result, status=event.get("status", "error"),
                        require_verify=spec.requires_verify_before_done,
                        validate_code=spec.validate_code_changes,
                        is_test_command=_is_test_command,
                        execution_revision=execution_revision,
                    )
                    if spec.workspace and event.get("status") == "ok" and call.name in _EDIT_TOOL_NAMES:
                        # Stats and, on a WSL root, a wsl.exe round trip: off the loop.
                        await asyncio.to_thread(
                            validation.sync_known_edits, spec.workspace, edit_paths(params),
                        )

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
        if tool_call.name in {"scrape", "browser"} and isinstance(params, dict) and not opens_local_app(params):
            budget = spec.loop_guard if spec.loop_guard is not None else spec
            if tool_call.name == "scrape":
                # A scrape that hits a JS shell or a wall escalates to browser.
                budget.browser_scraping = True
            else:
                if budget.browser_tests_requested is None:
                    budget.browser_tests_requested = _request_asks_for_browser(spec.initial_messages)
                if budget.browser_scraping is None:
                    budget.browser_scraping = _request_asks_for_browser(
                        spec.initial_messages, _SCRAPE_REQUEST_MARKERS,
                    )
                if not (
                    budget.browser_tests_requested or budget.browser_scraping
                    or _browser_page_open(spec.session_key)
                ):
                    blocked = (
                        "Error: the browser is off for this request - the user did not ask "
                        "for it and this is not scraping. Use web_search, web_fetch or "
                        "scrape to read the web, and the project's checks for code. If a "
                        "board step asks for this browser check, do not retry: close the "
                        "step with the checks that did run and the note 'browser check not "
                        "requested by the user', then move on."
                    )
                    return blocked, {
                        "name": tool_call.name,
                        "status": "error",
                        "detail": "browser not requested",
                    }, None
        if isinstance(params, dict) and (
            (
                tool_call.name == "exec"
                and is_browser_test_command(
                    str(params.get("command") or params.get("cmd") or ""),
                    _exec_cwd(spec.workspace, params.get("working_dir")),
                )
            )
            or (tool_call.name == "browser" and opens_local_app(params))
        ):
            # One-shot runs have no shared guard; the spec is then the request.
            budget = spec.loop_guard if spec.loop_guard is not None else spec
            if budget.browser_tests_requested is None:
                budget.browser_tests_requested = _request_asks_for_browser(spec.initial_messages)
            if not budget.browser_tests_requested:
                blocked = (
                    "Error: browser tests are off for this request - the user did not ask "
                    "for a browser check. Do not run, install, write or copy browser "
                    "scripts. Validate with the type check, build, lint or unit tests, "
                    "then finish and report. A board step that asks for an end-to-end "
                    "or preview check is closed with that evidence and the note 'browser "
                    "check not requested by the user' - never retried."
                )
                return blocked, {
                    "name": tool_call.name,
                    "status": "error",
                    "detail": "browser tests not requested",
                }, None
            if budget.browser_test_runs >= _MAX_BROWSER_TEST_RUNS:
                blocked = (
                    f"Error: browser test budget used ({_MAX_BROWSER_TEST_RUNS} runs of "
                    "Playwright/Cypress/e2e scripts or browser installs for this request). "
                    "Do not run, install or edit browser tests again. Finish with what "
                    "you have: report the change, the checks that passed and, in one "
                    "line, what the browser runs showed or why they could not run."
                )
                return blocked, {
                    "name": tool_call.name,
                    "status": "error",
                    "detail": "browser test budget exhausted",
                }, None
            budget.browser_test_runs += 1
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
        # A call rejected before execution (invalid JSON / parameters) never
        # read anything: counting it as a read turned broken arguments into
        # "this read already ran" and hid the invalid-arguments guard.
        spin_error = None if prep_error else repeated_readonly_tool_error(
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
        rerun_error = None if prep_error else repeated_rerun_check_error(
            tool_call.name,
            tool_call.arguments,
            readonly_call_counts,
        )
        if rerun_error:
            # Not a failure: the earlier identical run already holds the answer.
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "unchanged check not re-run",
            }
            return rerun_error + hint, event, None
        repeat_error = None if prep_error else repeated_identical_action_error(
            tool_call.name,
            tool_call.arguments,
            readonly_call_counts,
        )
        if repeat_error:
            # A reminder, not a wall: the call still runs (a wait can show the
            # same text twice), the model is told once to act or answer, and
            # the turn must answer without tools if it keeps repeating.
            readonly_call_counts[_REPEAT_NUDGE_KEY] = 1
            repeat_hint = "\n\n[" + repeat_error.removeprefix("Error: ") + "]"
        else:
            repeat_hint = ""
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
            await validation.warm_related_tests()
            detail = (
                "Validation pending: task remains open.\n"
                "This is a validation prerequisite, not a request for user permission.\n"
                + validation.missing()
                + "\nContinue the required validation automatically. Retry closing only after it passes. "
                "If a required command actually needs permission, request approval for that command "
                "and explain why. User approval does not replace passing validation."
            )
            # Missing evidence is a prerequisite, not an execution failure.
            # Counting it in the identical-call throttle would permanently
            # reject a valid retry after the tests pass. The no-progress guard
            # still stops repeated closures that do no validation work.
            return ToolResult.error(detail, recovery_hint=""), {
                "name": tool_call.name, "status": "error", "detail": "validation required before completion",
                "error_kind": "validation_required", "progress": "none",
            }, None
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
                from navin.agent.skills import BUILTIN_SKILLS_DIR
                from navin.agent.tool_surface import (
                    PLAN_ARTIFACT_DIR,
                    PLAN_SAFE_WRITE_TOOLS,
                    plan_artifact_call,
                )

                if (
                    tool_call.name not in PLAN_SAFE_WRITE_TOOLS
                    and not plan_artifact_call(tool_call.name, params, spec.workspace)
                    and not _call_read_only(candidate, params)
                ):
                    archify = (BUILTIN_SKILLS_DIR / "archify" / "bin" / "archify.mjs").resolve()
                    blocked = (
                        f"Error: tool '{tool_call.name}' is not allowed in Plan "
                        "mode. Plan designs without mutating: explore with "
                        "read-only tools and file board tasks. The only writes "
                        f"allowed are the plan's own artifacts: write_file under "
                        f"{PLAN_ARTIFACT_DIR}/ and `node {archify} validate|deliver "
                        f"<type> <files under {PLAN_ARTIFACT_DIR}/>` for the diagram. "
                        "For the simple-task exception, call "
                        "set_composer_mode(mode='agent') first, then do the work. "
                        "Otherwise do not retry or work around it: finish the "
                        "plan with your remarks and let the user pick the next action."
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
        if spec.defer_tools and tool is not None:
            # Called without loading it first: send its schema from now on.
            from navin.agent.tool_deferral import mark_loaded

            mark_loaded(spec.session_key, (tool_call.name,))
        from navin.agent.user_hooks import post_tool_use_feedback, pre_tool_use_block

        hook_block = await pre_tool_use_block(
            spec.workspace, spec.session_key, tool_call.name, params, notify=hook.emit_user_notice,
        )
        if hook_block is not None:
            return (
                f"Error: a PreToolUse hook blocked '{tool_call.name}': {hook_block}",
                {"name": tool_call.name, "status": "error", "detail": "blocked by user hook"},
                None,
            )
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
            from navin.utils.tool_hints import is_validation_pending

            if closing_work and is_validation_pending(tool_call.name, str(result)):
                return result, {
                    "name": tool_call.name, "status": "error", "detail": "validation required before completion",
                    "error_kind": "validation_required", "progress": "none",
                }, None
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
        hook_feedback = await post_tool_use_feedback(
            spec.workspace, spec.session_key, tool_call.name, params, result,
            notify=hook.emit_user_notice,
        )
        if hook_feedback and isinstance(result, str):
            result = f"{result}\n\n{hook_feedback}"

        reset_tool_failure_count(tool_call.name, tool_call.arguments, tool_failure_counts)
        if tool_call.name in _EDIT_TOOL_NAMES:
            reset_readonly_spin_counts(readonly_call_counts)
        else:
            record_identical_action(
                tool_call.name, tool_call.arguments, readonly_call_counts, result
            )

        if tool_call.name == "set_composer_mode":
            self._apply_composer_mode_switch(spec, params)

        detail = "" if result is None else str(result)
        detail = detail.replace("\n", " ").strip()
        if not detail:
            detail = "(empty)"
        elif len(detail) > 120:
            detail = detail[:120] + "..."
        event = {"name": tool_call.name, "status": "ok", "detail": detail}
        if isinstance(result, ToolResult) and result.made_progress is False:
            event["progress"] = "none"
        if tool_call.name == "exec":
            event.update(_exec_event_fields(params, result))
        if repeat_hint and isinstance(result, str):
            result = result + repeat_hint
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
        if mode in {"review", "security", "debug", "reverse"}:
            # The /forge brief sends code reviews, audits and debugging here
            # "with the matching tools". Its build allowlist has neither
            # security_scan nor debug_repair, so every one was refused and the
            # model kept retrying. These modes run on the denylist alone.
            spec.allowed_tools = None

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


_PATH_ARG_RE = re.compile(r'"(?:path|file_path)"\s*:\s*"([^"]{1,200})"')
_COMMAND_ARG_RE = re.compile(r'"(?:command|cmd)"\s*:\s*"((?:[^"\\]|\\.){1,80})')


class _ToolDraftStatus:
    """Turn streamed tool-call arguments into a live "what is being written" line."""

    _MIN_INTERVAL_S = 0.3

    def __init__(self, hook: Any) -> None:
        self._hook = hook
        self._calls: dict[int, dict[str, Any]] = {}
        self._last_emit = 0.0
        self._last_label = ""

    async def waiting(self, model: str | None) -> None:
        name = (model or "the model").rsplit("/", 1)[-1]
        await self._emit(f"{name} is working…", force=True)

    async def feed(self, delta: dict[str, Any]) -> None:
        index = int(delta.get("index") or 0)
        call = self._calls.setdefault(index, {"name": "", "args": ""})
        if delta.get("name"):
            call["name"] = str(delta["name"])
        call["args"] += str(delta.get("arguments_delta") or "")
        await self._emit(self._label(call))

    @staticmethod
    def _label(call: dict[str, Any]) -> str:
        name, args = call["name"] or "a tool", call["args"]
        size = f"{len(args) / 1000:.1f}k chars" if len(args) >= 1000 else f"{len(args)} chars"
        if name in ("write_file", "apply_patch", "edit_file", "notebook_edit"):
            paths = _PATH_ARG_RE.findall(args)
            verb = "Writing" if name == "write_file" else "Editing"
            if paths:
                more = f" (+{len(paths) - 1} files)" if len(paths) > 1 else ""
                return f"{verb} {paths[-1]}{more}… {size}"
            return f"{verb}… {size}"
        if name == "exec":
            match = _COMMAND_ARG_RE.search(args)
            if match:
                return f"Preparing command: {match.group(1)}…"
        return f"Preparing {name}…"

    async def _emit(self, label: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not label or label == self._last_label:
            return
        if not force and now - self._last_emit < self._MIN_INTERVAL_S:
            return
        self._last_emit, self._last_label = now, label
        emit = getattr(self._hook, "emit_live_status", None)
        if emit is not None:
            try:
                await emit(label)
            except Exception:
                logger.debug("live status emit failed", exc_info=True)
