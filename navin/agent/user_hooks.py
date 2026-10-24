# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""User lifecycle hooks: shell commands run on agent events.

Sources, all merged (every matching hook runs, in parallel):

- ``~/.navin/hooks.json``                     personal
- ``<plugin>/hooks/hooks.json`` (+ manifest)  enabled plugins
- ``<workspace>/.navin/hooks.json``           project, only once trusted

Format::

    {"hooks": {"PreToolUse": [{"matcher": "exec|write_file",
                               "hooks": [{"type": "command",
                                          "command": "bash x.sh",
                                          "timeout": 30}]}]}}

Each command gets the event as JSON on stdin and ``NAVIN_PROJECT_DIR`` /
``NAVIN_PLUGIN_ROOT`` in its environment. Exit 0 = success (stdout may hold
a JSON decision), exit 2 = block (stderr is the reason fed back to the
model), anything else = non-blocking error.

Trust a project file: ``python -m navin.agent.user_hooks trust <workspace>``.

Hookify rules (``.navin/hookify.*.local.md``, see :mod:`navin.agent.hookify`)
are evaluated natively on the same events, after the shell hooks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils.proc import no_window_kwargs

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop",
          "SessionEnd", "PreCompact", "Notification", "PermissionRequest")
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse"})
DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 600
_MAX_OUTPUT = 8000

# Common names from other agent harnesses, so shared hook files keep working.
TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "exec": ("Bash",),
    "write_file": ("Write",),
    "edit_file": ("Edit", "MultiEdit"),
    "apply_patch": ("Edit", "MultiEdit"),
    "read_file": ("Read",),
    "grep": ("Grep",),
    "find_files": ("Glob",),
    "list_dir": ("LS",),
    "web_fetch": ("WebFetch",),
    "web_search": ("WebSearch",),
    "spawn": ("Task", "Agent"),
}


@dataclass
class HookCommand:
    command: str
    timeout: float = DEFAULT_TIMEOUT_S
    matcher: str = ""
    plugin_root: str = ""
    source: str = ""
    kind: str = "command"  # "command" or "prompt" (the text is then a model prompt)
    model: str = ""


@dataclass
class HookOutcome:
    blocked: bool = False
    reasons: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    system_messages: list[str] = field(default_factory=list)
    ran: int = 0

    @property
    def reason(self) -> str:
        return "\n".join(r for r in self.reasons if r).strip()

    @property
    def additional_context(self) -> str:
        return "\n".join(c for c in self.context if c).strip()


Notify = Callable[[str], Awaitable[None]]


async def _deliver(notify: Notify | None, outcome: HookOutcome) -> None:
    """Hand each ``systemMessage`` to the UI; a failing sink never breaks the turn."""
    if notify is None:
        return
    for text in outcome.system_messages:
        try:
            await notify(text)
        except Exception:  # noqa: BLE001
            logger.debug("Hook systemMessage delivery failed", exc_info=True)


async def announce_hook_messages(
    on_progress: Callable[..., Awaitable[None]] | None, messages: list[str], turn_id: str | None = None,
) -> None:
    """Show each hook ``systemMessage`` as one activity row (user only, not the model)."""
    if on_progress is None:
        return
    from navin.utils.progress_events import invoke_on_progress, on_progress_accepts_tool_events

    if not on_progress_accepts_tool_events(on_progress):
        return
    for text in messages:
        text = (text or "").strip()
        if not text:
            continue
        _NOTICE_IDS[0] += 1
        try:
            await invoke_on_progress(
                on_progress, "", tool_hint=False,
                tool_events=[{
                    "version": 1, "phase": "end",
                    "call_id": f"hook-{turn_id or 'turn'}-{_NOTICE_IDS[0]}",
                    "name": "hook", "arguments": {"label": "hook message"}, "result": text,
                    "error": None, "files": [], "embeds": [],
                }],
            )
        except Exception:  # noqa: BLE001
            logger.debug("Hook message row failed", exc_info=True)


_NOTICE_IDS = [0]


def _home() -> Path:
    return Path.home() / ".navin"


def _trust_file() -> Path:
    return _home() / "trusted_hooks.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_trust() -> dict[str, str]:
    try:
        data = json.loads(_trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def project_hooks_file(workspace: Path) -> Path:
    return Path(workspace) / ".navin" / "hooks.json"


def is_trusted(path: Path) -> bool:
    """A project hooks file runs only when its exact content was approved."""
    try:
        return _load_trust().get(str(path.resolve())) == _digest(path)
    except OSError:
        return False


def trust(path: Path) -> None:
    data = _load_trust()
    data[str(path.resolve())] = _digest(path)
    target = _trust_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _event_table(data: Any) -> dict[str, Any]:
    if isinstance(data, dict) and isinstance(data.get("hooks"), dict):
        data = data["hooks"]
    return data if isinstance(data, dict) else {}


def _parse(data: Any, event: str, *, plugin_root: str, source: str) -> list[HookCommand]:
    out: list[HookCommand] = []
    entries = _event_table(data).get(event)
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            continue
        matcher = entry.get("matcher") if isinstance(entry.get("matcher"), str) else ""
        for hook in entry["hooks"]:
            kind = hook.get("type", "command") if isinstance(hook, dict) else ""
            if kind not in ("command", "prompt"):
                continue
            command = hook.get("prompt") if kind == "prompt" else hook.get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            try:
                timeout = float(hook.get("timeout") or DEFAULT_TIMEOUT_S)
            except (TypeError, ValueError):
                timeout = DEFAULT_TIMEOUT_S
            if plugin_root:
                command = command.replace("${NAVIN_PLUGIN_ROOT}", plugin_root)
            out.append(HookCommand(
                command=command,
                timeout=max(1.0, min(timeout, MAX_TIMEOUT_S)),
                matcher=matcher,
                plugin_root=plugin_root,
                source=source,
                kind=kind,
                model=str(hook.get("model") or "") if kind == "prompt" else "",
            ))
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring hooks file {}: {}", path, exc)
        return None


def _plugin_sources() -> list[tuple[Any, str, str]]:
    try:
        from navin.plugins.manager import PluginManager, plugins_root

        root = plugins_root()
        if not root.is_dir():
            return []
        rows = PluginManager(root).list()
    except Exception:  # noqa: BLE001 - a broken plugin never breaks a turn
        return []
    sources: list[tuple[Any, str, str]] = []
    for row in rows:
        if not row.get("enabled") or not row.get("hooks", True):
            continue
        plugin_dir = root / row["name"]
        default = plugin_dir / "hooks" / "hooks.json"
        if default.is_file():
            sources.append((_read_json(default), str(plugin_dir), str(default)))
        manifest = _read_json(plugin_dir / "plugin.json") if (plugin_dir / "plugin.json").is_file() else None
        custom = manifest.get("hooks") if isinstance(manifest, dict) else None
        if isinstance(custom, dict):
            sources.append((custom, str(plugin_dir), f"{plugin_dir}/plugin.json"))
        elif isinstance(custom, str) and ".." not in Path(custom).parts and not custom.startswith("/"):
            path = plugin_dir / custom
            if path.is_file() and path.resolve() != default.resolve():
                sources.append((_read_json(path), str(plugin_dir), str(path)))
    return sources


MANAGED_PREFIX = "managed:"


def _sources(workspace: Path | None) -> list[tuple[Any, str, str]]:
    """(data, plugin_root, label) of every trusted hooks file, read once."""
    sources: list[tuple[Any, str, str]] = []
    from navin.agent import managed_policy

    policy = managed_policy.load()
    if policy.active:
        # Organization hooks: always first, no trust step, never filtered.
        sources.append(({"hooks": policy.hooks}, "", MANAGED_PREFIX + policy.path))
        if policy.allow_managed_hooks_only:
            return sources
    personal = _home() / "hooks.json"
    if personal.is_file():
        sources.append((_read_json(personal), "", str(personal)))
    sources.extend(_plugin_sources())
    if workspace is not None:
        project = project_hooks_file(Path(workspace))
        if project.is_file():
            if is_trusted(project):
                sources.append((_read_json(project), "", str(project)))
            else:
                logger.debug("Project hooks {} not trusted yet; skipped", project)
    return sources


def load_hooks(
    event: str, workspace: Path | None, *, sources: list[tuple[Any, str, str]] | None = None
) -> list[HookCommand]:
    """Every configured hook command for *event*, from all trusted sources."""
    if event not in EVENTS:
        return []
    if sources is None:
        sources = _sources(workspace)
    commands: list[HookCommand] = []
    for data, root, source in sources:
        if data is not None:
            commands.extend(_parse(data, event, plugin_root=root, source=source))
    return commands


def matches(matcher: str, tool_name: str) -> bool:
    if matcher in ("", "*"):
        return True
    names = (tool_name, *TOOL_ALIASES.get(tool_name, ()))
    try:
        pattern = re.compile(matcher)
    except re.error:
        return False
    return any(pattern.fullmatch(name) for name in names)


async def _run_one(hook: HookCommand, payload: dict[str, Any], workspace: Path | None) -> tuple[int, str, str]:
    if hook.kind == "prompt":
        from navin.agent.prompt_hooks import evaluate

        return await evaluate(
            hook.command, str(payload.get("hook_event_name") or ""), payload,
            timeout=hook.timeout, model=hook.model or None,
        )
    env = dict(os.environ)
    cwd = str(workspace) if workspace is not None and Path(workspace).is_dir() else None
    env["NAVIN_PROJECT_DIR"] = cwd or os.getcwd()
    if hook.plugin_root:
        env["NAVIN_PLUGIN_ROOT"] = hook.plugin_root
    from navin.agent.managed_policy import apply_env

    apply_env(env)  # organization-pinned variables win over everything above
    try:
        proc = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            **no_window_kwargs(),
        )
    except OSError as exc:
        return 1, "", f"cannot start hook: {exc}"
    data = json.dumps(payload, default=str).encode("utf-8")
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), timeout=hook.timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, "", f"hook timed out after {hook.timeout:.0f}s"
    return (
        proc.returncode or 0,
        out.decode("utf-8", "replace")[:_MAX_OUTPUT],
        err.decode("utf-8", "replace")[:_MAX_OUTPUT],
    )


def _apply(outcome: HookOutcome, event: str, code: int, out: str, err: str, source: str) -> None:
    outcome.ran += 1
    if code == 2:
        outcome.blocked = True
        outcome.reasons.append(err.strip() or out.strip() or "blocked by hook")
        return
    if code != 0:
        logger.warning("Hook {} for {} failed ({}): {}", source, event, code, err.strip()[:300])
        return
    text = out.strip()
    decision: dict[str, Any] | None = None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            decision = parsed if isinstance(parsed, dict) else None
        except ValueError:
            decision = None
    if decision is None:
        # Plain stdout is context for prompt-level events, ignored elsewhere.
        if text and event in ("SessionStart", "UserPromptSubmit"):
            outcome.context.append(text)
        return
    specific = decision.get("hookSpecificOutput") if isinstance(decision.get("hookSpecificOutput"), dict) else {}
    verdict = str(decision.get("decision") or specific.get("permissionDecision") or "").lower()
    reason = str(decision.get("reason") or specific.get("permissionDecisionReason") or decision.get("stopReason") or "")
    if verdict in ("block", "deny") or decision.get("continue") is False:
        outcome.blocked = True
        outcome.reasons.append(reason or "blocked by hook")
    elif reason and event in ("PostToolUse",):
        outcome.reasons.append(reason)
    extra = specific.get("additionalContext") or decision.get("additionalContext")
    if isinstance(extra, str) and extra.strip():
        outcome.context.append(extra.strip())
    if isinstance(decision.get("systemMessage"), str) and decision["systemMessage"].strip():
        outcome.system_messages.append(decision["systemMessage"].strip())


def _apply_hookify(
    outcome: HookOutcome, event: str, body: dict[str, Any], workspace: Path | None,
    transcript: list[Any] | None,
) -> None:
    """Fold matching hookify rules into *outcome* (warn = notice, block = refuse)."""
    from navin.agent import hookify

    if not hookify.enabled():
        return
    payload = body
    if event in ("Stop", "SubagentStop"):
        payload = {**body, "transcript": hookify.render_transcript(transcript)}
    verdict = hookify.evaluate(event, payload, workspace)
    if not verdict.blocking and not verdict.warning:
        return
    outcome.ran += 1
    warning = verdict.warning_message
    if event == "PreToolUse":
        if verdict.blocked:
            # The refusal (tool row and model) carries the rule message.
            outcome.blocked = True
            outcome.reasons.append(verdict.message)
        elif warning:
            outcome.system_messages.append(warning)
        return
    if event == "PostToolUse":
        # The model gets the warning with the tool result. The user already saw
        # it before the call, except for rules that only match the output.
        before = hookify.evaluate(
            "PreToolUse", {k: v for k, v in body.items() if k != "tool_response"}, workspace,
        )
        seen = {r.name for r in [*before.blocking, *before.warning]}
        fresh = [r for r in [*verdict.blocking, *verdict.warning] if r.name not in seen]
        outcome.context.append("\n\n".join(r.text for r in [*verdict.blocking, *verdict.warning]))
        if fresh:
            outcome.system_messages.append("\n\n".join(r.text for r in fresh))
        return
    if event == "UserPromptSubmit":
        if verdict.blocked:
            outcome.blocked = True
            outcome.reasons.append(verdict.message)
        elif warning:
            outcome.system_messages.append(warning)
            outcome.context.append(warning)
        return
    # Stop / SubagentStop: block = keep working, and say why to the user too.
    if verdict.blocked:
        outcome.blocked = True
        outcome.reasons.append(verdict.message)
        outcome.system_messages.append(verdict.message)
    elif warning:
        outcome.system_messages.append(warning)


def _apply_nested_instructions(
    outcome: HookOutcome, event: str, body: dict[str, Any], workspace: Path | None,
) -> None:
    """Attach AGENTS.md files of subdirectories the agent reads or edits, once per session."""
    if event != "PostToolUse":
        return
    from navin.agent import nested_instructions as ni

    text, labels = ni.attach(
        str(body.get("tool_name") or ""), body.get("tool_input"), workspace,
        str(body.get("session_id") or "default"),
    )
    if text:
        outcome.ran += 1
        outcome.context.append(text)
    if labels:
        outcome.system_messages.append(ni.notice(labels))


async def _apply_security_guidance(
    outcome: HookOutcome, event: str, body: dict[str, Any], workspace: Path | None,
) -> None:
    """Pattern reminders after edits; opt-in AI review at Stop and after commits."""
    from navin.agent import security_guidance as sg

    if not sg.enabled():
        return
    session = str(body.get("session_id") or "default")
    tool_input = body.get("tool_input") if isinstance(body.get("tool_input"), dict) else {}
    if event == "PostToolUse":
        tool_name = str(body.get("tool_name") or "")
        warn = sg.edit_warnings(tool_name, tool_input, workspace, session)
        if warn.context:
            outcome.ran += 1
            outcome.context.append(warn.context)
            outcome.system_messages.append(warn.notice)
        context, notice = await sg.commit_review(
            tool_name, tool_input, str(body.get("tool_response") or ""), workspace, session,
        )
        if context:
            outcome.ran += 1
            outcome.context.append(context)
            outcome.system_messages.append(notice)
        return
    if event == "Stop" and not outcome.blocked:
        reason, notice = await sg.stop_review(
            workspace, session, stop_hook_active=bool(body.get("stop_hook_active")),
        )
        if reason:
            outcome.ran += 1
            outcome.blocked = True
            outcome.reasons.append(reason)
            outcome.system_messages.append(notice)


async def _apply_stop_checks(
    outcome: HookOutcome, event: str, body: dict[str, Any], workspace: Path | None,
) -> None:
    """Remember edited files; at Stop, lint and test them (opt-in, see stop_checks)."""
    from navin.agent import stop_checks

    if not stop_checks.enabled():
        return
    session = str(body.get("session_id") or "default")
    if event == "PostToolUse":
        tool_input = body.get("tool_input") if isinstance(body.get("tool_input"), dict) else {}
        stop_checks.record_edit(str(body.get("tool_name") or ""), tool_input, workspace, session)
        return
    if event in ("Stop", "SubagentStop") and not outcome.blocked:
        reason, notice = await stop_checks.stop_checks(workspace, session)
        if notice:
            outcome.system_messages.append(notice)
        if reason:
            outcome.ran += 1
            outcome.blocked = True
            outcome.reasons.append(reason)


def _apply_ralph_loop(
    outcome: HookOutcome, event: str, body: dict[str, Any], workspace: Path | None,
) -> None:
    """Main-agent Stop only: feed the /ralph-loop prompt back (see ralph_loop)."""
    if event != "Stop" or outcome.blocked or workspace is None:
        return
    from navin.agent import ralph_loop

    reason, notice = ralph_loop.stop_reason(
        workspace, str(body.get("session_id") or ""), str(body.get("last_assistant_message") or ""),
    )
    if notice:
        outcome.system_messages.append(notice)
    if reason:
        outcome.ran += 1
        outcome.blocked = True
        outcome.reasons.append(reason)


async def run_hooks(
    event: str,
    payload: dict[str, Any],
    workspace: Path | None,
    *,
    tool_name: str | None = None,
    transcript: list[Any] | None = None,
) -> HookOutcome:
    """Run every hook configured for *event*, then hookify rules; never raises."""
    outcome = HookOutcome()
    body = {
        "hook_event_name": event,
        "cwd": str(workspace) if workspace is not None else os.getcwd(),
        **payload,
    }
    try:
        hooks = load_hooks(event, workspace)
    except Exception:  # noqa: BLE001
        logger.exception("Loading {} hooks failed", event)
        hooks = []
    if event in TOOL_EVENTS and tool_name is not None:
        hooks = [h for h in hooks if matches(h.matcher, tool_name)]
    managed = [h for h in hooks if h.source.startswith(MANAGED_PREFIX)]
    if managed:
        # Organization hooks run first; their PreToolUse refusal is final.
        hooks = [h for h in hooks if not h.source.startswith(MANAGED_PREFIX)]
        done = await asyncio.gather(*(_run_one(h, body, workspace) for h in managed), return_exceptions=True)
        for hook, result in zip(managed, done):
            if isinstance(result, BaseException):
                logger.warning("Managed hook {} crashed: {}", hook.source, result)
                continue
            _apply(outcome, event, *result, hook.source)
        if outcome.blocked and event == "PreToolUse":
            return outcome
    if hooks:
        results = await asyncio.gather(
            *(_run_one(h, body, workspace) for h in hooks), return_exceptions=True
        )
        for hook, result in zip(hooks, results):
            if isinstance(result, BaseException):
                logger.warning("Hook {} crashed: {}", hook.source, result)
                continue
            _apply(outcome, event, *result, hook.source)
    try:
        _apply_hookify(outcome, event, body, workspace, transcript)
    except Exception:  # noqa: BLE001 - a broken rule never breaks a turn
        logger.exception("Hookify rules failed on {}", event)
    try:
        # Before the security review: a red test run ends the AI review early.
        await _apply_stop_checks(outcome, event, body, workspace)
    except Exception:  # noqa: BLE001 - checks are best effort
        logger.exception("End-of-turn checks failed on {}", event)
    try:
        await _apply_security_guidance(outcome, event, body, workspace)
    except Exception:  # noqa: BLE001 - guidance is best effort
        logger.exception("Security guidance failed on {}", event)
    try:
        if event == "PostToolUse":
            # Disk lookups (slow on a WSL project seen from Windows): off the
            # event loop, which also streams the replies and runs the subagents.
            await asyncio.to_thread(_apply_nested_instructions, outcome, event, body, workspace)
    except Exception:  # noqa: BLE001 - instructions are best effort
        logger.exception("Nested instructions failed on {}", event)
    try:
        # Last: a red check or a review finding is fixed before the next round.
        _apply_ralph_loop(outcome, event, body, workspace)
    except Exception:  # noqa: BLE001 - the loop never breaks a turn
        logger.exception("Ralph loop failed on {}", event)
    return outcome


# -- call sites ---------------------------------------------------------------

async def pre_tool_use_block(
    workspace: Path | None, session_key: str | None, tool_name: str, params: Any,
    *, notify: Notify | None = None,
) -> str | None:
    """Reason to refuse this tool call, or None to let it run."""
    from navin.agent.managed_policy import tool_denied

    denied = tool_denied(tool_name)
    if denied:
        return denied
    outcome = await run_hooks(
        "PreToolUse",
        {"session_id": session_key or "", "tool_name": tool_name, "tool_input": params if isinstance(params, dict) else {}},
        workspace,
        tool_name=tool_name,
    )
    await _deliver(notify, outcome)
    return (outcome.reason or "blocked by hook") if outcome.blocked else None


async def post_tool_use_feedback(
    workspace: Path | None, session_key: str | None, tool_name: str, params: Any, result: Any,
    *, notify: Notify | None = None,
) -> str | None:
    """Text appended to the tool result for the model, or None."""
    outcome = await run_hooks(
        "PostToolUse",
        {
            "session_id": session_key or "",
            "tool_name": tool_name,
            "tool_input": params if isinstance(params, dict) else {},
            "tool_response": str(result)[:_MAX_OUTPUT],
        },
        workspace,
        tool_name=tool_name,
    )
    await _deliver(notify, outcome)
    parts = [*outcome.reasons, *outcome.context]
    if not parts:
        return None
    return "[PostToolUse hook] " + "\n".join(p for p in parts if p)


async def stop_hook_reason(
    workspace: Path | None, session_key: str | None, final_content: str | None,
    *, stop_hook_active: bool, subagent: bool = False, notify: Notify | None = None,
    transcript: list[Any] | None = None,
) -> str | None:
    """Reason to keep working instead of ending the turn, or None.

    *transcript* (the turn's messages) feeds hookify ``transcript`` conditions.
    """
    outcome = await run_hooks(
        "SubagentStop" if subagent else "Stop",
        {
            "session_id": session_key or "",
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": (final_content or "")[:_MAX_OUTPUT],
        },
        workspace,
        transcript=transcript,
    )
    await _deliver(notify, outcome)
    return (outcome.reason or "A Stop hook asked to continue.") if outcome.blocked else None


async def prompt_hooks(
    workspace: Path | None, session_key: str | None, prompt: str, *, new_session: bool
) -> HookOutcome:
    """SessionStart (first message only) then UserPromptSubmit."""
    merged = HookOutcome()
    if new_session:
        start = await run_hooks("SessionStart", {"session_id": session_key or "", "source": "startup"}, workspace)
        merged.context.extend(start.context)
        merged.system_messages.extend(start.system_messages)
        merged.ran += start.ran
    submit = await run_hooks("UserPromptSubmit", {"session_id": session_key or "", "prompt": prompt}, workspace)
    merged.blocked = submit.blocked
    merged.reasons.extend(submit.reasons)
    merged.context.extend(submit.context)
    merged.system_messages.extend(submit.system_messages)
    merged.ran += submit.ran
    return merged


def hooks_summary(workspace: Path | None) -> str:
    """One line per event with active hooks, for the system prompt ('' if none)."""
    lines = []
    try:
        sources = _sources(workspace)
    except Exception:  # noqa: BLE001
        sources = []
    for event in EVENTS:
        count = len(load_hooks(event, workspace, sources=sources))
        if count:
            lines.append(f"- {event}: {count} hook(s)")
    try:
        from navin.agent.managed_policy import summary as managed_summary

        managed_line = managed_summary()
    except Exception:  # noqa: BLE001
        managed_line = ""
    if managed_line:
        lines.append(managed_line)
    try:
        from navin.agent.hookify import summary as hookify_summary

        rules_line = hookify_summary(workspace)
    except Exception:  # noqa: BLE001
        rules_line = ""
    if rules_line:
        lines.append(rules_line)
    try:
        from navin.agent.security_guidance import summary as security_summary

        security_line = security_summary(workspace)
    except Exception:  # noqa: BLE001
        security_line = ""
    if security_line:
        lines.append(security_line)
    try:
        from navin.agent.stop_checks import summary as checks_summary

        checks_line = checks_summary()
    except Exception:  # noqa: BLE001
        checks_line = ""
    if checks_line:
        lines.append(checks_line)
    try:
        from navin.agent.ralph_loop import summary as ralph_summary

        ralph_line = ralph_summary(workspace)
    except Exception:  # noqa: BLE001
        ralph_line = ""
    if ralph_line:
        lines.append(ralph_line)
    if workspace is not None:
        project = project_hooks_file(Path(workspace))
        if project.is_file() and not is_trusted(project):
            lines.append(
                f"- {project} exists but is not trusted, so it does not run "
                "(the user trusts it with `python -m navin.agent.user_hooks trust <workspace>`)"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "trust":
        path = project_hooks_file(Path(args[1]).expanduser())
        if not path.is_file():
            print(f"no hooks file at {path}", file=sys.stderr)
            return 1
        trust(path)
        print(f"trusted: {path}")
        return 0
    if len(args) == 2 and args[0] == "list":
        print(hooks_summary(Path(args[1]).expanduser()) or "no hooks")
        return 0
    print("usage: python -m navin.agent.user_hooks trust|list <workspace>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
