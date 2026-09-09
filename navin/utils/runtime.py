"""Runtime-specific helper functions and constants."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils.helpers import stringify_text_blocks

_MAX_REPEAT_EXTERNAL_LOOKUPS = 2

# Third same-target workspace violation in a turn escalates to "stop retrying".
_MAX_REPEAT_WORKSPACE_VIOLATIONS = 2

# With soft tool errors (fail_on_tool_error=False) the model self-heals by
# retrying; this bounds how often the *same exact call* may fail before the
# error message escalates to "change approach".
_MAX_IDENTICAL_TOOL_FAILURES = 2
# After this many identical failures, treat the call as a hard stop so a stuck
# model cannot burn the whole turn repeating the same broken arguments.
_HARD_STOP_IDENTICAL_TOOL_FAILURES = 5
# Successful read-only calls are also bounded. Context clearing blanks old
# results and tells the model it can re-run the tool; thinking models then
# spend the whole step budget re-reading the same file (35x observed).
_MAX_IDENTICAL_READONLY_CALLS = 2
_READONLY_SPIN_TOOLS = frozenset(
    {
        "read_file",
        "grep",
        "list_dir",
        "find_files",
        "code_index",
    }
)
_MUTATING_RESET_TOOLS = frozenset(
    {
        "apply_patch",
        "edit_file",
        "write_file",
        "manage_files",
    }
)

EMPTY_FINAL_RESPONSE_MESSAGE = (
    "I completed the tool steps but couldn't produce a final answer. "
    "Please try again or narrow the task."
)

FINALIZATION_RETRY_PROMPT = (
    "Please provide your response to the user based on the conversation above."
)

BUDGET_EXHAUSTED_FINALIZATION_PROMPT = (
    "The tool-call budget for this turn is exhausted. Based only on the "
    "conversation and tool results above, provide a concise final response to "
    "the user. Do not call or request tools. Do not claim the task is complete "
    "unless the evidence above clearly shows it is complete. Structure the "
    "reply as: (1) what was done - concrete files, commands, and board steps; "
    "(2) what remains; (3) the best next step (e.g. ask the user to reply "
    "'continue'). Keep it scannable and specific - no vague 'I worked on it'."
)

LENGTH_RECOVERY_PROMPT = (
    "Output limit reached. Continue exactly where you left off "
    "- no recap, no apology. Break remaining work into smaller steps if needed."
)

SUSTAINED_GOAL_CONTINUE_PROMPT = (
    "You have an active sustained goal. Please continue working toward the "
    "objective using your tools, or call update_goal with action='complete' "
    "if the work is truly finished."
)

DELIVERY_CONTINUE_PROMPT = (
    "You ended the turn without calling any tools, so no deliverable was "
    "created. This workflow requires tool execution. Continue now: follow the "
    "Active Skills, call the tools needed to produce the real workspace file "
    "(and any required images), and only then reply with the file path and a "
    "short outline. Do not describe what you plan to do - do it."
)

VERIFY_BEFORE_DONE_CONTINUE_PROMPT = (
    "You edited code but have not verified the result yet. Before claiming "
    "Build/Debug work is done: re-run the failing repro if this is a debug "
    "turn, then run `verify action=check` (or `lint` and `test_run` if verify "
    "is unavailable), attach evidence, and close with a short summary. Do not "
    "narrate success without those checks."
)

VERIFY_FAILED_CONTINUE_PROMPT = (
    "Verification failed (lint errors and/or failing tests). You may not claim "
    "the Build/Debug turn is done while verify is red. Run "
    "`verify action=fix` (or targeted lint/test fixes), re-run "
    "`verify action=check`, and only then summarize with green evidence. "
    "Keep the patch minimal and in-scope."
)

NO_PROGRESS_CONTINUE_PROMPT = (
    "You have been searching the codebase without making an edit or running "
    "a check. Stop exploring. Either apply the fix now or answer the user "
    "from evidence already in this turn. Do not call read_file, grep, "
    "list_dir, or find_files again unless you need one new path."
)

NO_PROGRESS_STOP_FALLBACK = (
    "I searched without making progress. Here is what I know so far; "
    "reply to continue with a narrower target."
)


def build_delivery_continue_message(custom: str | None = None) -> dict[str, str]:
    """Prompt the model to resume when a delivery workflow produced no tools."""
    return {"role": "user", "content": custom or DELIVERY_CONTINUE_PROMPT}


def build_verify_before_done_message(custom: str | None = None) -> dict[str, str]:
    """Prompt the model to run verify/lint/tests before closing a Build turn."""
    return {"role": "user", "content": custom or VERIFY_BEFORE_DONE_CONTINUE_PROMPT}


def build_verify_failed_message(
    custom: str | None = None,
    *,
    last_summary: str | None = None,
) -> dict[str, str]:
    """Prompt the model to fix a red verify before closing a Build turn.

    ``last_summary`` is the recorded verify/lint/test output (already
    truncated). Without it the model only hears "verify is red" and
    re-guesses which tests failed.
    """
    body = custom or VERIFY_FAILED_CONTINUE_PROMPT
    detail = (last_summary or "").strip()
    if detail:
        body = f"{body}\n\nLast verification output:\n{detail[:800]}"
    return {"role": "user", "content": body}


def build_no_progress_continue_message(custom: str | None = None) -> dict[str, str]:
    """Nudge once when a turn has only been searching."""
    return {"role": "user", "content": custom or NO_PROGRESS_CONTINUE_PROMPT}


def empty_tool_result_message(tool_name: str) -> str:
    """Short prompt-safe marker for tools that completed without visible output."""
    return f"({tool_name} completed with no output)"


def ensure_nonempty_tool_result(tool_name: str, content: Any) -> Any:
    """Replace semantically empty tool results with a short marker string."""
    if content is None:
        return empty_tool_result_message(tool_name)
    if isinstance(content, str) and not content.strip():
        return empty_tool_result_message(tool_name)
    if isinstance(content, list):
        if not content:
            return empty_tool_result_message(tool_name)
        text_payload = stringify_text_blocks(content)
        if text_payload is not None and not text_payload.strip():
            return empty_tool_result_message(tool_name)
    return content


def is_blank_text(content: str | None) -> bool:
    """True when *content* is missing or only whitespace."""
    return content is None or not content.strip()


def build_finalization_retry_message() -> dict[str, str]:
    """A short no-tools-allowed prompt for final answer recovery."""
    return {"role": "user", "content": FINALIZATION_RETRY_PROMPT}


def build_budget_exhausted_finalization_message() -> dict[str, str]:
    """Prompt the model for a no-tools final response after budget exhaustion."""
    return {"role": "user", "content": BUDGET_EXHAUSTED_FINALIZATION_PROMPT}


def build_length_recovery_message() -> dict[str, str]:
    """Prompt the model to continue after hitting output token limit."""
    return {"role": "user", "content": LENGTH_RECOVERY_PROMPT}


def build_goal_continue_message(custom: str | None = None) -> dict[str, str]:
    """Prompt the model to continue when a sustained goal is still active."""
    return {"role": "user", "content": custom or SUSTAINED_GOAL_CONTINUE_PROMPT}


def _tool_failure_signature(tool_name: str, arguments: Any) -> str:
    try:
        payload = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(arguments)
    return f"{tool_name}:{payload}"


def repeated_tool_failure_hint(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Escalating hint once the same exact tool call has failed repeatedly.

    Soft tool errors keep the run alive so the model can self-correct, but an
    unbounded retry loop burns iterations on a call that will never succeed.
    Counting (tool, arguments) failures per turn keeps legitimate retries
    with adjusted arguments free, while the verbatim repeat gets told to stop.
    """
    signature = _tool_failure_signature(tool_name, arguments)
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count < _MAX_IDENTICAL_TOOL_FAILURES:
        return None
    logger.warning(
        "Tool {} failed {} times with identical arguments; escalating hint",
        tool_name,
        count,
    )
    if count >= _HARD_STOP_IDENTICAL_TOOL_FAILURES:
        return (
            f"\n\n[This exact {tool_name} call failed {count} times with identical "
            "arguments. Further identical calls are blocked for this turn only - "
            "change the arguments, use a different tool, or continue without it. "
            "The agent run is NOT stopped.]"
        )
    return (
        f"\n\n[This exact {tool_name} call has now failed {count} times. "
        "Do not repeat it verbatim: change the arguments, use a different "
        "tool, or report the blocker in your final answer.]"
    )


def repeated_tool_failure_is_hard_stop(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> bool:
    """True when the latest identical failure should abort further retries."""
    signature = _tool_failure_signature(tool_name, arguments)
    return seen_counts.get(signature, 0) >= _HARD_STOP_IDENTICAL_TOOL_FAILURES


def reset_tool_failure_count(tool_name: str, arguments: Any, seen_counts: dict[str, int]) -> None:
    """A successful retry ends the consecutive failure streak for this call."""
    seen_counts.pop(_tool_failure_signature(tool_name, arguments), None)


def reset_readonly_spin_counts(seen_counts: dict[str, int]) -> None:
    """Drop read-only signatures after a successful edit so a re-read is allowed."""
    stale = [
        key
        for key in seen_counts
        if key.split(":", 1)[0] in _READONLY_SPIN_TOOLS
    ]
    for key in stale:
        del seen_counts[key]


def repeated_readonly_tool_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Block identical successful reads after a small per-turn budget.

    Failures already escalate via ``repeated_tool_failure_hint``. This catches
    the other loop: the same ``read_file`` / ``grep`` succeeding over and over
    because the previous result was blanked from context.
    """
    if tool_name not in _READONLY_SPIN_TOOLS:
        return None
    signature = _tool_failure_signature(tool_name, arguments)
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_IDENTICAL_READONLY_CALLS:
        return None
    logger.warning(
        "Blocking repeated identical {} call after {} successes this turn",
        tool_name,
        count,
    )
    return (
        f"Error: this exact {tool_name} call already ran "
        f"{_MAX_IDENTICAL_READONLY_CALLS} times this turn with identical "
        "arguments. The result is already in the conversation (or was "
        "cleared after you saw it). Do not repeat it. Change the path or "
        "pattern, edit the file, or answer from what you already have."
    )


def external_lookup_signature(tool_name: str, arguments: Any) -> str | None:
    """Stable signature for repeated external lookups we want to throttle."""
    if not isinstance(arguments, dict):
        return None
    if tool_name == "web_fetch":
        url = str(arguments.get("url") or "").strip()
        if url:
            return f"web_fetch:{url.lower()}"
    if tool_name == "web_search":
        query = str(arguments.get("query") or arguments.get("search_term") or "").strip()
        if query:
            return f"web_search:{query.lower()}"
    return None


def repeated_external_lookup_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Block repeated external lookups after a small retry budget."""
    signature = external_lookup_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_EXTERNAL_LOOKUPS:
        return None
    logger.warning(
        "Blocking repeated external lookup {} on attempt {}",
        signature[:160],
        count,
    )
    return (
        "Error: repeated external lookup blocked. "
        "Use the results you already have to answer, or try a meaningfully different source."
    )


# Workspace-boundary violations are soft errors, with per-target throttling.

_OUTSIDE_PATH_PATTERN = re.compile(r"(?:^|[\s|>'\"])((?:/[^\s\"'>;|<]+)|(?:~[^\s\"'>;|<]+))")


def workspace_violation_signature(
    tool_name: str,
    arguments: Any,
) -> str | None:
    """Return a stable cross-tool signature for the outside-workspace target."""
    if not isinstance(arguments, dict):
        return None
    for key in ("path", "file_path", "target", "source", "destination"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return _normalize_violation_target(val.strip())

    if tool_name in {"exec", "shell"}:
        cmd = str(arguments.get("command") or "").strip()
        if cmd:
            match = _OUTSIDE_PATH_PATTERN.search(cmd)
            if match:
                return _normalize_violation_target(match.group(1))
        cwd = str(arguments.get("working_dir") or "").strip()
        if cwd:
            return _normalize_violation_target(cwd)

    return None


def _normalize_violation_target(raw: str) -> str:
    """Normalize *raw* path so that equivalent spellings collide on the same key."""
    try:
        normalized = Path(raw).expanduser().resolve().as_posix()
    except Exception:
        normalized = raw.replace("\\", "/")
    return f"violation:{normalized}".lower()


def repeated_workspace_violation_error(
    tool_name: str,
    arguments: Any,
    seen_counts: dict[str, int],
) -> str | None:
    """Return an escalated error after repeated bypass attempts."""
    signature = workspace_violation_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_WORKSPACE_VIOLATIONS:
        return None
    logger.warning(
        "Escalating repeated workspace bypass attempt {} (attempt {})",
        signature[:160],
        count,
    )
    target = signature.split("violation:", 1)[1] if "violation:" in signature else signature
    return (
        "Error: refusing repeated workspace-bypass attempts.\n"
        f"You have tried to access '{target}' (or an equivalent path) "
        f"{count} times in this turn. This is a hard policy boundary -- "
        "switching tools, shell tricks, working_dir overrides, symlinks, "
        "or base64 piping will NOT change the answer. Stop retrying. "
        "If the user genuinely needs this resource, tell them you cannot "
        "access it and ask how they want to proceed (e.g. copy the file "
        "into the workspace, or disable restrict_to_workspace for this run)."
    )
