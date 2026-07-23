"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from navin import __version__
from navin.agent.goal_permission import goal_mutation_permission
from navin.bus.events import OutboundMessage
from navin.command.router import CommandContext, CommandRouter
from navin.utils.helpers import build_status_content
from navin.utils.restart import set_restart_notice_to_env
from navin.utils.workspace_prompts import initialize_workspace_prompt

# WebUI protocol contract for how a slash command participates in turn state:
# - side_channel: returns control text without starting or ending an agent turn.
# - finalize_active_turn: side-channel command that also closes the active UI turn.
# - stop_active_turn: cancels the active turn; WebUI may intercept exact submits.
# - agent_turn: always enters the normal agent path.
# - agent_turn_with_args: no args is side-channel usage; args enter the agent path.
CommandLifecycle = Literal[
    "side_channel",
    "finalize_active_turn",
    "stop_active_turn",
    "agent_turn",
    "agent_turn_with_args",
]


@dataclass(frozen=True)
class BuiltinCommandSpec:
    command: str
    title: str
    description: str
    icon: str
    arg_hint: str = ""
    lifecycle: CommandLifecycle = "side_channel"
    accepts_args: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "command": self.command,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "arg_hint": self.arg_hint,
            "lifecycle": self.lifecycle,
            "accepts_args": self.accepts_args,
        }


BUILTIN_COMMAND_SPECS: tuple[BuiltinCommandSpec, ...] = (
    BuiltinCommandSpec(
        "/new",
        "New chat",
        "Reset this chat and start a fresh conversation.",
        "square-pen",
        lifecycle="finalize_active_turn",
    ),
    BuiltinCommandSpec(
        "/stop",
        "Stop current task",
        "Cancel the active agent turn for this chat.",
        "square",
        lifecycle="stop_active_turn",
    ),
    BuiltinCommandSpec(
        "/restart",
        "Restart navin",
        "Restart the bot process.",
        "rotate-cw",
    ),
    BuiltinCommandSpec(
        "/status",
        "Show status",
        "Display runtime, provider, and channel status.",
        "activity",
    ),
    BuiltinCommandSpec(
        "/model",
        "Switch model preset",
        "Show or switch the active model preset.",
        "brain",
        "[preset]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/history",
        "Show conversation history",
        "Print the last N persisted conversation messages.",
        "history",
        "[n]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/goal",
        "Start long-running goal",
        "Tell the agent to treat the request as a long-running goal.",
        "activity",
        "<goal>",
        lifecycle="agent_turn_with_args",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/trigger",
        "Create named local trigger",
        "Create a named CLI trigger bound to this chat session.",
        "zap",
        "<name>",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/dream",
        "Run Dream",
        "Manually trigger memory consolidation.",
        "sparkles",
    ),
    BuiltinCommandSpec(
        "/dream-log",
        "Show Dream log",
        "Show what the last Dream consolidation changed.",
        "book-open",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/dream-restore",
        "Restore memory",
        "Revert memory to a previous Dream snapshot.",
        "undo-2",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/dream-prompt",
        "Dream memory",
        "Tell Dream how to organize this workspace's memory.",
        "file-text",
        "[init]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/evaluator-prompt",
        "Heartbeat evaluator",
        "Customize the heartbeat notification gate prompt for this workspace.",
        "file-text",
        "[init]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/skill",
        "List skills",
        "List all enabled skills available to the agent.",
        "wrench",
    ),
    BuiltinCommandSpec(
        "/help",
        "Show help",
        "List available slash commands.",
        "circle-help",
    ),
    BuiltinCommandSpec(
        "/pairing",
        "Manage pairing",
        "List, approve, deny or revoke pairing requests.",
        "shield",
        "[list|approve <code>|deny <code>|revoke <user_id>]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/blueprint",
        "Plan mode",
        "Design an implementation plan before writing any code.",
        "map",
        "[task]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/forge",
        "Build mode",
        "Full agent build mode: plan, code, run, test, and iterate autonomously.",
        "hammer",
        "[task]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/atlas",
        "Project atlas",
        "Build or refresh .metadata: role of every file, dependencies, metagraph.",
        "waypoints",
        "[init|refresh|query <question>]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/inspect",
        "Code review",
        "Review recent changes or a target for bugs, regressions, and cleanups.",
        "search-code",
        "[path|diff|scope]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/fortify",
        "Security review",
        "Audit the project for security weaknesses and hardening opportunities.",
        "shield-check",
        "[path|scope]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/probe",
        "Vulnerability scan",
        "Hunt for vulnerabilities: injections, secrets, dependencies, CVEs.",
        "bug",
        "[path|scope]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/turbo",
        "Performance audit",
        "Profile and optimize: hot paths, queries, bundle size, memory.",
        "gauge",
        "[path|scope]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/pulse",
        "Metrics & analytics",
        "Measure project health: KPIs, code metrics, coverage, instrumentation.",
        "bar-chart-3",
        "[focus]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/studio",
        "Document studio",
        "Create polished PowerPoint, Word, PDF or Excel documents from a brief.",
        "file-text",
        "[format + brief]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/campaign",
        "Marketing studio",
        "Generate campaigns: ad copy, social posts, product images, video scripts.",
        "megaphone",
        "[brief]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/team",
        "Team studio",
        "Design orgs and roles, staff them, run OKRs, spawn virtual AI teams.",
        "users",
        "[mission|org brief]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/leads",
        "Leads & sales studio",
        "Find companies, people and jobs; qualify, enrich and build outreach.",
        "target",
        "[icp|company|brief]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/seo",
        "SEO studio",
        "Full SEO: audits, keyword research, optimized content, competitors.",
        "trending-up",
        "[url|topic]",
        lifecycle="agent_turn",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/checkpoint",
        "Checkpoints & rewind",
        "Auto-saved before each prompt. Rewind conversation, code, or both.",
        "flag",
        "[save [note]|list|restore <name> [all|chat|code]|delete <name>]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/pilot",
        "Model per task",
        "Route to the model preset mapped to a task: search, plan, review, security, dev…",
        "route",
        "[task]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/pack",
        "Plugin packs",
        "Install, list, enable/disable or remove plugin packs (skills + MCP servers).",
        "package",
        "[list|install <git-url|path>|enable <name>|disable <name>|remove <name>]",
        accepts_args=True,
    ),
)


def builtin_command_palette() -> list[dict[str, str | bool]]:
    """Return structured command metadata for UI command palettes."""
    return [spec.as_dict() for spec in BUILTIN_COMMAND_SPECS]


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(ctx.key)
    # Also drain pending queue to prevent mid-turn injection deadlock
    pending = loop._pending_queues.pop(ctx.key, None)
    if pending is not None:
        while not pending.empty():
            try:
                pending.get_nowait()
                total += 1
            except Exception:
                break
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process."""
    msg = ctx.msg
    set_restart_notice_to_env(
        channel=msg.channel,
        chat_id=msg.chat_id,
        metadata=dict(msg.metadata or {}),
    )

    async def _do_restart():
        await asyncio.sleep(1)
        argv = [sys.executable, "-m", "navin"] + sys.argv[1:]
        mode = getattr(ctx.loop, "restart_mode", "auto") or "auto"
        if mode == "auto":
            mode = "spawn" if sys.platform == "win32" else "exec"
        if mode == "exec":
            os.execv(sys.executable, argv)
            return
        if mode == "spawn":
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(argv, **kwargs)
        os._exit(0)

    asyncio.create_task(_do_restart())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    runtime = ctx.runtime or loop.llm_runtime()
    ctx_est = 0
    with suppress(Exception):
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(
            session,
            runtime=runtime,
        )
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)

    # Fetch web search provider usage (best-effort, never blocks the response)
    search_usage_text: str | None = None
    # Never let usage fetch break /status
    with suppress(Exception):
        from navin.utils.searchusage import fetch_search_usage
        web_cfg = getattr(loop, "web_config", None)
        search_cfg = getattr(web_cfg, "search", None) if web_cfg else None
        if search_cfg is not None:
            provider = getattr(search_cfg, "provider", "duckduckgo")
            api_key = getattr(search_cfg, "api_key", "") or None
            usage = await fetch_search_usage(provider=provider, api_key=api_key)
            search_usage_text = usage.format()
    active_tasks = loop._active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    with suppress(Exception):
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=runtime.model,
            start_time=loop._start_time, last_usage=loop._last_usage,
            context_window_tokens=runtime.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            search_usage_text=search_usage_text,
            active_task_count=task_count,
            max_completion_tokens=runtime.generation.max_tokens,
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Stop active task and start a fresh session."""
    loop = ctx.loop
    await loop._cancel_active_tasks(ctx.key)
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        runtime = ctx.runtime or loop.llm_runtime()
        loop._schedule_background(
            loop.consolidator.archive(
                snapshot,
                runtime=runtime,
                session_key=ctx.key,
            )
        )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        metadata=dict(ctx.msg.metadata or {})
    )


def _format_preset_names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names) if names else "(none configured)"


def _model_preset_names(loop) -> list[str]:
    names = set(loop.model_presets)
    names.add("default")
    return ["default", *sorted(name for name in names if name != "default")]


def _active_model_preset_name(loop) -> str:
    return loop.model_preset or "default"


def _command_error_message(exc: Exception) -> str:
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


def _model_command_status(loop) -> str:
    names = _model_preset_names(loop)
    active = _active_model_preset_name(loop)
    return "\n".join([
        "## Model",
        f"- Current model: `{loop.model}`",
        f"- Current preset: `{active}`",
        f"- Available presets: {_format_preset_names(names)}",
    ])


async def cmd_model(ctx: CommandContext) -> OutboundMessage:
    """Show or switch model presets."""
    loop = ctx.loop
    args = ctx.args.strip()
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    if not args:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=_model_command_status(loop),
            metadata=metadata,
        )

    parts = args.split()
    if len(parts) != 1:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: `/model [preset]`",
            metadata=metadata,
        )

    name = parts[0]
    try:
        runtime = loop.set_model_preset(name)
    except (KeyError, ValueError) as exc:
        names = _model_preset_names(loop)
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                f"Could not switch model preset: {_command_error_message(exc)}\n\n"
                f"Available presets: {_format_preset_names(names)}"
            ),
            metadata=metadata,
        )

    max_tokens = runtime.generation.max_tokens
    lines = [
        f"Switched model preset to `{runtime.model_preset}`.",
        f"- Model: `{runtime.model}`",
        f"- Context window: {runtime.context_window_tokens}",
    ]
    if max_tokens is not None:
        lines.append(f"- Max output tokens: {max_tokens}")
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="\n".join(lines),
        metadata=metadata,
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run."""
    import time

    loop = ctx.loop
    msg = ctx.msg

    async def _run_dream():
        async def _silent(*_args, **_kwargs):
            pass

        from navin.agent.memory import MemoryStore

        dream_session_key = MemoryStore.dream_session_key
        build_dream_commit_message = MemoryStore.build_dream_commit_message
        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = loop.context.memory
        content = ""
        resp = None
        diff_body = ""
        t0 = time.monotonic()
        try:
            result = store.build_dream_prompt()
            if result is None:
                await loop.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=_format_dream_no_input_message(),
                    metadata={"render_as": "text"},
                ))
                return
            prompt, last_cursor = result
            key = dream_session_key()
            resp = await loop.process_direct(
                prompt,
                session_key=key,
                ephemeral=True,
                tools=store.build_dream_tools(),
                on_progress=_silent,
            )
            elapsed = time.monotonic() - t0
            # Ground truth: the real file delta, not the LLM's self-report.
            diff_body = store.dream_content_diff()
            productive = bool(diff_body) or (
                not store.git.is_initialized()
                and MemoryStore.dream_run_completed(resp)
            )
            if productive:
                store.set_last_dream_cursor(last_cursor)
                content = f"Dream completed in {elapsed:.1f}s."
            elif MemoryStore.dream_run_completed(resp):
                content = f"Dream completed in {elapsed:.1f}s; no memory changes."
            else:
                content = (
                    f"Dream did not complete after {elapsed:.1f}s; "
                    "memory cursor was not advanced."
                )
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream failed after {elapsed:.1f}s: {e}"
        finally:
            from navin.webui.token_usage import record_response_token_usage

            record_response_token_usage(
                resp,
                source="dream",
                timezone_name=getattr(loop.context, "timezone", None),
            )
            if store.git.is_initialized():
                commit_msg = build_dream_commit_message("dream: manual run", diff_body)
                sha = store.git.auto_commit(commit_msg)
                if sha:
                    content += f" (commit {sha})"
            store.compact_history()
            prune_dream_sessions(loop.sessions.sessions_dir)
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


async def cmd_dream_prompt(ctx: CommandContext) -> OutboundMessage:
    """Show or set up the workspace Dream memory instructions."""
    store = ctx.loop.context.memory
    path = store.dream_prompt_file
    display_path = path.relative_to(store.workspace).as_posix()
    args = ctx.args.strip().lower()

    if args == "init":
        if not initialize_workspace_prompt(path, store.default_dream_prompt()):
            content = (
                f"Dream memory instructions already exist at `{display_path}`.\n\n"
                "Edit that file, or delete/empty it to return to navin's default."
            )
        else:
            content = (
                f"Created Dream memory instructions at `{display_path}`.\n\n"
                "Edit that file to teach Dream how to organize memory. "
                "This fully replaces navin's default Dream guide for this workspace. "
                "Delete or empty it to return to navin's default."
            )
    elif args:
        content = "Usage: /dream-prompt [init]"
    elif store.has_dream_prompt_override():
        content = (
            "Dream memory instructions: custom for this workspace\n\n"
            f"- Path: `{display_path}`\n"
            "- Delete or empty this file to return to navin's default."
        )
    else:
        content = (
            "Dream memory instructions: navin default\n\n"
            f"- Editable file: `{display_path}`\n"
            "- Run `/dream-prompt init` to create an editable copy."
        )

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_evaluator_prompt(ctx: CommandContext) -> OutboundMessage:
    """Show or set up the workspace heartbeat evaluator prompt."""
    from navin.utils.evaluator import (
        default_evaluator_prompt,
        evaluator_prompt_file,
        has_evaluator_prompt_override,
    )

    workspace = ctx.loop.context.memory.workspace
    path = evaluator_prompt_file(workspace)
    display_path = path.relative_to(workspace).as_posix()
    args = ctx.args.strip().lower()

    if args == "init":
        if not initialize_workspace_prompt(path, default_evaluator_prompt()):
            content = (
                f"Heartbeat evaluator prompt already exists at `{display_path}`.\n\n"
                "Edit that file, or delete/empty it to return to navin's default."
            )
        else:
            content = (
                f"Created heartbeat evaluator prompt at `{display_path}`.\n\n"
                "Edit that file to control when the heartbeat notification gate speaks. "
                "It must still instruct the model to call the `evaluate_notification` tool, "
                "otherwise the gate fails closed and stays silent. "
                "Delete or empty it to return to navin's default."
            )
    elif args:
        content = "Usage: /evaluator-prompt [init]"
    elif has_evaluator_prompt_override(workspace):
        content = (
            "Heartbeat evaluator prompt: custom for this workspace\n\n"
            f"- Path: `{display_path}`\n"
            "- Delete or empty this file to return to navin's default."
        )
    else:
        content = (
            "Heartbeat evaluator prompt: navin default\n\n"
            f"- Editable file: `{display_path}`\n"
            "- Run `/evaluator-prompt init` to create an editable copy."
        )

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _format_dream_no_input_message() -> str:
    return "\n".join([
        "Dream has no conversation history to process yet.",
        "",
        "Dream reads new entries from `memory/history.jsonl` after the current Dream cursor.",
        (
            "Short chats only reach that file after token compaction or idle auto-compact, "
            "so a fresh or short WebUI chat may leave Dream with no input."
        ),
        "",
        "Next steps:",
        "- Enable `agents.defaults.idleCompactAfterMinutes` so completed chats become Dream input automatically.",
        "- Compact the current chat into memory once that manual action is available.",
        "- If you expected history to exist, check whether `memory/history.jsonl` has new entries after the Dream cursor.",
        "- Use `/dream-prompt` to see or change how Dream organizes memory.",
    ])


def _extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _format_changed_files(diff: str) -> str:
    files = _extract_changed_files(diff)
    if not files:
        return "No tracked memory files changed."
    return ", ".join(f"`{path}`" for path in files)


_DREAM_COMMIT_PREFIX = "dream:"


def _format_dream_log_content(commit, diff: str, *, requested_sha: str | None = None) -> str:
    files_line = _format_changed_files(diff)
    lines = [
        "## Dream Update",
        "",
        "Here is the selected Dream memory change." if requested_sha else "Here is the latest Dream memory change.",
        "",
        f"- Commit: `{commit.sha}`",
        f"- Time: {commit.timestamp}",
        f"- Changed files: {files_line}",
    ]
    if diff:
        lines.extend([
            "",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff.rstrip(),
            "```",
        ])
    else:
        lines.extend([
            "",
            "Dream recorded this version, but there is no file diff to display.",
        ])
    return "\n".join(lines)


def _format_dream_restore_list(commits: list) -> str:
    lines = [
        "## Dream Restore",
        "",
        "Choose a Dream memory version to restore. Latest first:",
        "",
    ]
    for c in commits:
        lines.append(f"- `{c.sha}` {c.timestamp} - {c.message.splitlines()[0]}")
    lines.extend([
        "",
        "Preview a version with `/dream-log <sha>` before restoring it.",
        "Restore a version with `/dream-restore <sha>`.",
    ])
    return "\n".join(lines)


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show what the last Dream changed.

    Default: diff of the latest Dream commit versus its parent.
    With /dream-log <sha>: diff of that specific commit.
    """
    store = ctx.loop.consolidator.store
    git = store.git

    if not git.is_initialized():
        if store.get_last_dream_cursor() == 0:
            msg = (
                "Dream has not run yet. Run `/dream`, or wait for the next scheduled Dream cycle.\n\n"
                "Use `/dream-prompt` to see or change how Dream organizes memory."
            )
        else:
            msg = "Dream history is not available because memory versioning is not initialized."
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=msg, metadata={"render_as": "text"},
        )

    args = ctx.args.strip()

    if args:
        # Show diff of a specific commit
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        if not result:
            content = (
                f"Couldn't find Dream change `{sha}`.\n\n"
                "Use `/dream-restore` to list recent versions, "
                "or `/dream-log` to inspect the latest one."
            )
        else:
            commit, diff = result
            content = _format_dream_log_content(commit, diff, requested_sha=sha)
    else:
        # Default: show the latest Dream commit's diff
        commits = git.log(max_entries=1, message_prefix=_DREAM_COMMIT_PREFIX)
        result = (
            git.show_commit_diff(
                commits[0].sha,
                max_entries=1,
                message_prefix=_DREAM_COMMIT_PREFIX,
            )
            if commits else None
        )
        if result:
            commit, diff = result
            content = _format_dream_log_content(commit, diff)
        else:
            content = (
                "Dream memory has no saved versions yet.\n\n"
                "Use `/dream-prompt` to see or change how Dream organizes memory."
            )

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """Restore memory files from a previous dream commit.

    Usage:
        /dream-restore          — list recent commits
        /dream-restore <sha>    — revert a specific commit
    """
    store = ctx.loop.consolidator.store
    git = store.git
    if not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="Dream history is not available because memory versioning is not initialized.",
        )

    args = ctx.args.strip()
    if not args:
        # Show recent Dream commits for the user to pick
        commits = git.log(max_entries=10, message_prefix=_DREAM_COMMIT_PREFIX)
        if not commits:
            content = "Dream memory has no saved versions to restore yet."
        else:
            content = _format_dream_restore_list(commits)
    else:
        sha = args.split()[0]
        result = git.show_commit_diff(sha, message_prefix=_DREAM_COMMIT_PREFIX)
        if not result:
            content = (
                f"Couldn't restore Dream change `{sha}`.\n\n"
                "Only Dream memory versions can be restored. "
                "Use `/dream-restore` to list recent versions."
            )
        else:
            changed_files = _format_changed_files(result[1])
            new_sha = git.revert(sha, message_prefix=_DREAM_COMMIT_PREFIX)
            if new_sha:
                content = (
                    f"Restored Dream memory to the state before `{sha}`.\n\n"
                    f"- New safety commit: `{new_sha}`\n"
                    f"- Restored files: {changed_files}\n\n"
                    f"Use `/dream-log {new_sha}` to inspect the restore diff."
                )
            else:
                content = (
                    f"Couldn't restore Dream change `{sha}`.\n\n"
                    "It may be the first saved version with no earlier state to restore."
                )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


_HISTORY_DEFAULT_COUNT = 10
_HISTORY_MAX_COUNT = 50
_HISTORY_MAX_CONTENT_CHARS = 200


def _format_history_message(msg: dict) -> str | None:
    """Format a single history message for display. Returns None to skip."""
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = " ".join(parts)
    content = str(content).strip()
    if not content:
        return None
    if len(content) > _HISTORY_MAX_CONTENT_CHARS:
        content = content[:_HISTORY_MAX_CONTENT_CHARS] + "…"
    label = "👤 You" if role == "user" else "🤖 Bot"
    return f"{label}: {content}"


async def cmd_history(ctx: CommandContext) -> OutboundMessage:
    """Show the last N messages of the current session (default 10, max 50).

    Usage: /history [count]
    """
    count = _HISTORY_DEFAULT_COUNT
    if ctx.args.strip():
        try:
            count = max(1, min(int(ctx.args.strip()), _HISTORY_MAX_COUNT))
        except ValueError:
            return OutboundMessage(
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                content="Usage: /history [count] — e.g. /history 5 (default: 10, max: 50)",
                metadata=dict(ctx.msg.metadata or {}),
            )

    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    history = session.get_history(max_messages=0, include_runtime_context=False)
    visible = [_format_history_message(m) for m in history]
    visible = [m for m in visible if m is not None]
    recent = visible[-count:]

    if not recent:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="No conversation history yet.",
            metadata=dict(ctx.msg.metadata or {}),
        )

    header = f"Last {len(recent)} message(s):\n"
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=header + "\n".join(recent),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_goal(ctx: CommandContext) -> OutboundMessage | None:
    """Mark this turn as an explicit sustained-goal request."""
    goal = ctx.args.strip()
    if not goal:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: /goal <long-running task description>",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    if ctx.session is None:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                "A task is already running for this chat. "
                "Use `/stop` first, then send `/goal <long-running task description>` again."
            ),
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    if not ctx.is_user_turn:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Goal mode can only be started by a user `/goal <task>` command.",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    ctx.turn_scopes.append(goal_mutation_permission(True))
    ctx.msg.metadata = {
        **dict(ctx.msg.metadata or {}),
        "original_command": "/goal",
        "original_content": ctx.raw,
        "goal_requested": True,
        "goal_started_at": time.time(),
    }
    ctx.msg.content = ctx.raw
    return None


async def cmd_pairing(ctx: CommandContext) -> OutboundMessage:
    """List, approve, deny or revoke pairing requests."""
    from navin.pairing import PAIRING_COMMAND_META_KEY, handle_pairing_command

    reply = handle_pairing_command(ctx.msg.channel, ctx.args)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=reply,
        metadata={PAIRING_COMMAND_META_KEY: True},
    )


async def cmd_skill(ctx: CommandContext) -> OutboundMessage:
    """List all enabled skills (name and description only)."""
    loop = ctx.loop
    skills = loop.context.skills.list_skills(filter_unavailable=False)
    if not skills:
        content = "No skills available."
    else:
        lines = [f"Available skills ({len(skills)}):", ""]
        for entry in skills:
            desc = loop.context.skills._get_skill_description(entry["name"])
            lines.append(f"- **{entry['name']}** — {desc}")
        content = "\n".join(lines)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=dict(ctx.msg.metadata or {}),
    )


async def cmd_trigger(ctx: CommandContext) -> OutboundMessage:
    """Create a local trigger bound to the current session."""
    name = ctx.args.strip()
    if not name:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                "Usage: /trigger <name>\n\n"
                "Create a named local trigger bound to this chat session."
            ),
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    from navin.triggers.local_store import LocalTriggerStore

    loop = ctx.loop
    workspace = getattr(loop, "workspace", None)
    if workspace is None:
        workspace = getattr(getattr(loop, "context", None), "workspace", None)
    if workspace is None:
        raise RuntimeError("workspace unavailable for trigger creation")

    store = getattr(loop, "local_trigger_store", None)
    if store is None:
        store = LocalTriggerStore(workspace)

    from navin.session.keys import UNIFIED_SESSION_KEY

    session_key = (
        ctx.msg.session_key
        if ctx.key == UNIFIED_SESSION_KEY
        else ctx.key
    )
    trigger = store.create(
        name=name,
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        session_key=session_key,
        sender_id="trigger",
        origin_metadata=dict(ctx.msg.metadata or {}),
    )
    command = f'navin trigger {trigger.id} "message"'
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=(
            f"Trigger created: {trigger.name}\n"
            f"ID: {trigger.id}\n\n"
            f"Command:\n{command}"
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )

async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    lines = ["navin commands:"]
    for spec in BUILTIN_COMMAND_SPECS:
        command = spec.command
        if spec.arg_hint:
            command = f"{command} {spec.arg_hint}"
        lines.append(f"{command} — {spec.description}")
    return "\n".join(lines)


# --- Workflow commands -------------------------------------------------------
# Each command rewrites the inbound message into a structured mission brief and
# returns None so the normal agent turn (loop, memory, skills, tools) handles it.

_WORKFLOW_BRIEFS: dict[str, tuple[str, str, str]] = {
    # command: (mode title, skills to load, mission brief)
    "/blueprint": (
        "Plan mode",
        "task-planner, adaptive-reasoning, context-compressor",
        "Do NOT write or edit any code yet. Explore the project, then produce an "
        "implementation plan: goal, constraints, ordered steps with file-level detail, "
        "risks, test strategy, and open questions. End by asking for approval to build. "
        "Recommend running /checkpoint save before executing the plan.",
    ),
    "/forge": (
        "Build mode",
        "fullstack-dev, task-planner, code-reviewer, test-generator, project-metadata",
        "Full build mode: plan briefly, then implement end to end. Write code, run it, "
        "execute tests, fix failures, and iterate until the result works. Use the shell "
        "and file tools directly. Save a checkpoint (/checkpoint save) before large or "
        "destructive changes, and summarize what was built and verified at the end. "
        "If the project has a .metadata/index.json, consult it to locate files and keep "
        "it updated for every file you create, move, or repurpose.",
    ),
    "/atlas": (
        "Project atlas",
        "project-metadata, task-planner, context-compressor",
        "Build or refresh the project knowledge base in .metadata/ following the "
        "project-metadata skill exactly. Start with the metagraph tool "
        "(action=overview) to see kinds, hubs, and whether .metadata exists. For "
        "'init' or 'refresh': walk the project, write .metadata/index.json (one entry "
        "per significant file: kind, role, depends_on, tags) and "
        ".metadata/ARCHITECTURE.md, then report coverage. For 'query <question>': use "
        "the metagraph tool (action=find / action=file) to identify the exact files "
        "and their dependency chains — only fall back to grep if the graph lacks the "
        "answer. Keep the index truthful: update it whenever you create, move, or "
        "significantly change files.",
    ),
    "/inspect": (
        "Code review",
        "code-reviewer, critic-reviewer",
        "Act as a rigorous code reviewer. Determine the scope (recent changes, the given "
        "path, or the whole project), then report findings ordered by severity: bugs, "
        "behavioral regressions, security issues, missing tests, then maintainability. "
        "Cite files and lines. Do not modify code unless explicitly asked to fix.",
    ),
    "/fortify": (
        "Security review",
        "security-auditor, permission-guard, secrets-manager",
        "Run a security audit: authentication and authorization flows, input validation, "
        "injection surfaces, secrets handling, unsafe defaults, dependency risks, and "
        "exposure of internal services. Rate each finding (critical/high/medium/low) with "
        "a concrete remediation. Read-only: propose fixes, do not apply them unasked.",
    ),
    "/probe": (
        "Vulnerability scan",
        "vulnerability-scanner, prompt-injection-defender",
        "Hunt for exploitable weaknesses: OWASP Top 10 patterns, hardcoded secrets and "
        "tokens, vulnerable dependency versions (check lockfiles), SSRF/path traversal, "
        "deserialization, and prompt-injection surfaces. For each finding give proof of "
        "location, impact, and the minimal patch. Read-only unless asked to fix.",
    ),
    "/turbo": (
        "Performance audit",
        "performance-auditor",
        "Audit performance: hot paths, N+1 queries, blocking I/O in async code, missing "
        "caches, oversized bundles or images, memory growth, and slow startup. Measure "
        "before recommending (run profilers, timers, or EXPLAIN where possible) and "
        "quantify expected gains. Propose the top optimizations by impact/effort ratio.",
    ),
    "/pulse": (
        "Metrics & analytics",
        "metrics-analyst, kpi-reporter, quality-gate",
        "Assess project health with numbers: code size and complexity, dependency count "
        "and freshness, lint findings, test coverage if available, TODO/FIXME debt, and "
        "any product KPIs reachable from configured tools. Produce a scored dashboard "
        "with trends and the three highest-leverage improvements.",
    ),
    "/studio": (
        "Document studio",
        "pptx-generator, docx-generator, pdf-generator, spreadsheet-analyst, "
        "presentation-designer, professional-writer, document-templates",
        "Produce a polished, ready-to-share document. Clarify (or infer) the format "
        "(PowerPoint, Word, PDF, or Excel), audience, and goal, then design the "
        "structure before writing: sections, narrative arc, one idea per slide/page. "
        "Generate the actual file in the workspace with python-pptx / python-docx / "
        "openpyxl / reportlab-style tooling. For the look: if the request names a "
        "visual theme, apply its exact spec from the document-templates skill; if the "
        "user provides a template file, open it and build inside it (template wins). "
        "Fill it with concrete researched content — never lorem ipsum — and finish by "
        "reporting the file path plus a short outline of what was created.",
    ),
    "/campaign": (
        "Marketing studio",
        "campaign-manager, ad-creative-generator, social-media-manager, copywriting-agent, "
        "image-generation, video-generation, brand-voice-manager, customer-persona-builder",
        "Act as a full-stack marketing studio. From the brief, define the target persona "
        "and key message, then produce the requested deliverables end to end: ad copy and "
        "hooks, social posts per platform (with hashtags and posting schedule), product "
        "visuals or ad images via the image generation tools, video ad scripts with "
        "scene-by-scene breakdowns (and generated video when tools allow), landing page "
        "copy, and email sequences. Save every asset as a file in the workspace, keep a "
        "consistent brand voice, and end with a summary table of deliverables and paths.",
    ),
    "/team": (
        "Team studio",
        "org-designer, virtual-team-builder, multi-agent-orchestration, task-planner, "
        "recruitment-agent, job-description-writer, candidate-screening, kpi-reporter, "
        "human-approval",
        "Act as a chief of staff and organization designer. Depending on the request: "
        "design the organization (structure, role sheets with owned outcomes and KPIs, "
        "RACI, decision rights, rituals, sequenced hiring plan with triggers), staff it "
        "(job descriptions, screening grids, interview kits, onboarding plans), run it "
        "(cascade OKRs, staff projects, prepare ops reviews), and grow it (skills matrix, "
        "feedback cycles, career paths). When a mission benefits from parallel expertise, "
        "compose a virtual AI team: define 2-5 roles with one deliverable each, spawn "
        "them as subagents with complete briefs, orchestrate handoffs, verify outputs, "
        "and merge the result yourself. Save every artifact as files under org/ or team/ "
        "in the workspace. Structure follows strategy: always start from the goal.",
    ),
    "/leads": (
        "Leads & sales studio",
        "lead-prospector, buying-signals, lead-generation, lead-qualification, "
        "account-research, entity-research, outreach-sequencer, cold-email-writer, "
        "customer-persona-builder, pipeline-analyst",
        "Act as an elite B2B prospecting and sales desk. Depending on the request: define "
        "or refine the ICP, hunt companies and decision-makers from open web sources "
        "(directories, registries, team pages, press, job boards), detect buying signals "
        "(funding, hiring, tech changes, leadership moves), enrich and score every lead, "
        "and build outreach (emails, LinkedIn scripts, call plans, cadences). Every datum "
        "must carry its source URL — never invent contacts. Deliver lists as CSV or "
        "markdown tables saved in the workspace, ranked by fit and signal strength, with "
        "a suggested opening angle per lead and the top accounts to contact first.",
    ),
    "/seo": (
        "SEO studio",
        "seo-technical-auditor, keyword-research, on-page-seo-optimizer, seo-content-writer, "
        "backlink-strategy, competitor-seo-analysis, local-seo, geo-ai-search-optimizer",
        "Act as a complete SEO agency. Depending on the request: run technical audits "
        "(crawlability, Core Web Vitals hints, structured data, meta/canonical issues), "
        "do keyword research with intent mapping and difficulty estimates, write "
        "SEO-optimized content briefs or full articles (title, H-structure, entities, "
        "internal links, FAQ + schema.org markup), analyze competitors, and plan "
        "backlinks. Ground claims by fetching the actual pages when a URL is given. "
        "Deliver actionable output ordered by impact, saved as files when substantial.",
    ),
}


def _workflow_handler(command: str):
    title, skills, brief = _WORKFLOW_BRIEFS[command]

    async def handler(ctx: CommandContext) -> OutboundMessage | None:
        focus = ctx.args.strip()
        parts = [
            f"[{title}] ({command})",
            f"Load and follow these skills if available: {skills}.",
            brief,
        ]
        if focus:
            parts.append(f"Focus / target given by the user: {focus}")
        else:
            parts.append("No explicit target given: infer the most useful scope yourself.")
        ctx.msg.metadata = {
            **dict(ctx.msg.metadata or {}),
            "original_command": command,
            "original_content": ctx.raw,
        }
        ctx.msg.content = "\n".join(parts)
        return None

    return handler


def _checkpoint_store(loop):
    from navin.agent.checkpoints import CheckpointStore

    store = getattr(loop, "checkpoints", None)
    if store is not None:
        return store
    workspace = getattr(loop, "workspace", None)
    if workspace is None:
        workspace = getattr(getattr(loop, "context", None), "workspace", None)
    if workspace is None:
        raise ValueError("no workspace configured for checkpoints")
    return CheckpointStore(workspace)


async def cmd_checkpoint(ctx: CommandContext) -> OutboundMessage:
    """Save / list / restore / delete session state snapshots."""
    from navin.agent.checkpoints import CheckpointError

    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    def reply(content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=content, metadata=metadata,
        )

    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    try:
        store = _checkpoint_store(loop)
    except ValueError as exc:
        return reply(f"Checkpoints unavailable: {exc}")

    args = ctx.args.strip()
    action, _, rest = args.partition(" ")
    action = action.lower()
    rest = rest.strip()

    try:
        if action in ("", "list"):
            rows = store.list(ctx.key)
            if not rows:
                return reply(
                    "No checkpoints for this chat yet.\n"
                    "A checkpoint is saved automatically before each of your prompts; "
                    "use `/checkpoint save [note]` to add one manually."
                )
            lines = [f"Checkpoints ({len(rows)}, newest first):", ""]
            for row in rows[:20]:
                kind = "auto" if row.get("auto") else "manual"
                label = row.get("note") or row.get("prompt") or ""
                if len(label) > 60:
                    label = label[:57] + "..."
                suffix = f" — {label}" if label else ""
                files = f", {row['file_count']} file(s)" if row.get("file_count") else ""
                lines.append(
                    f"- `{row['name']}` ({kind}) · "
                    f"{row['message_count']} messages{files}{suffix}"
                )
            if len(rows) > 20:
                lines.append(f"- … {len(rows) - 20} more")
            lines.append("")
            lines.append(
                "Rewind with `/checkpoint restore <name> [all|chat|code]` — "
                "`chat` rewinds the conversation only, `code` reverts the files "
                "the agent edited, `all` (default) does both."
            )
            return reply("\n".join(lines))
        if action == "save":
            meta = store.save(session, note=rest)
            return reply(
                f"Checkpoint saved: `{meta['name']}` "
                f"({meta['message_count']} messages)."
            )
        if action == "restore":
            parts = rest.split()
            if not parts:
                return reply("Usage: `/checkpoint restore <name> [all|chat|code]`")
            name = parts[0]
            mode = parts[1].lower() if len(parts) > 1 else "all"
            if mode not in ("all", "chat", "code"):
                return reply("Usage: `/checkpoint restore <name> [all|chat|code]`")
            results = []
            if mode in ("all", "code"):
                restored, deleted = store.restore_files(ctx.key, name)
                summary = f"{restored} file(s) restored"
                if deleted:
                    summary += f", {deleted} deleted"
                results.append(f"Code: {summary}.")
            if mode in ("all", "chat"):
                count = store.restore_chat(session, name)
                loop.sessions.save(session)
                loop.sessions.invalidate(session.key)
                results.append(f"Conversation: rewound to {count} messages.")
            return reply(f"Rewound to `{name}`.\n" + "\n".join(f"- {r}" for r in results))
        if action == "delete":
            if not rest:
                return reply("Usage: `/checkpoint delete <name>`")
            store.delete(ctx.key, rest)
            return reply(f"Checkpoint deleted: `{rest}`.")
    except CheckpointError as exc:
        return reply(f"Checkpoint error: {exc}")
    return reply(
        "Usage: `/checkpoint [save [note]|list|restore <name> [all|chat|code]|delete <name>]`\n\n"
        "Checkpoints are saved automatically before each of your prompts and capture "
        "the conversation plus the pre-edit content of every file the agent modifies. "
        "They complement git, they do not replace it."
    )


_PILOT_TASKS = ("search", "plan", "review", "security", "dev", "fast", "deep", "docs")


async def cmd_pilot(ctx: CommandContext) -> OutboundMessage:
    """Switch to the model preset mapped to a task type (search, plan, review…)."""
    loop = ctx.loop
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    def reply(content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=content, metadata=metadata,
        )

    presets = _model_preset_names(loop)
    task = ctx.args.strip().lower()
    if not task:
        mapped = [name for name in _PILOT_TASKS if name in presets]
        lines = [
            "## Model routing",
            f"- Current preset: `{_active_model_preset_name(loop)}` (model `{loop.model}`)",
            f"- Task presets configured: {_format_preset_names(mapped)}",
            f"- All presets: {_format_preset_names(presets)}",
            "",
            "Use `/pilot <task>` to switch (e.g. `/pilot review`). Define presets named "
            f"{', '.join(_PILOT_TASKS)} in Settings → Models to route each task "
            "to its own model.",
        ]
        return reply("\n".join(lines))

    if task not in presets:
        return reply(
            f"No preset named `{task}`.\n\n"
            f"Available presets: {_format_preset_names(presets)}\n"
            f"Create a preset named `{task}` in Settings → Models to enable this route."
        )
    try:
        runtime = loop.set_model_preset(task)
    except (KeyError, ValueError) as exc:
        return reply(f"Could not switch: {_command_error_message(exc)}")
    return reply(
        f"Routed to preset `{runtime.model_preset}` for `{task}` tasks.\n"
        f"- Model: `{runtime.model}`\n"
        f"- Context window: {runtime.context_window_tokens}"
    )


async def cmd_pack(ctx: CommandContext) -> OutboundMessage:
    """Manage plugin packs: bundles of skills + MCP servers."""
    from navin.plugins import PluginError, PluginManager

    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    def reply(content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=content, metadata=metadata,
        )

    manager = PluginManager()
    args = ctx.args.strip()
    action, _, rest = args.partition(" ")
    action = action.lower()
    rest = rest.strip()

    try:
        if action in ("", "list"):
            rows = manager.list()
            if not rows:
                return reply(
                    "No plugin packs installed.\n"
                    "Install one with `/pack install <git-url|/local/path>`. "
                    "A pack bundles skills (`skills/<name>/SKILL.md`) and MCP servers "
                    "(`mcp.json`, commands may use npx/uvx/docker)."
                )
            lines = [f"Plugin packs ({len(rows)}):", ""]
            for row in rows:
                state = "enabled" if row["enabled"] else "disabled"
                parts = []
                if row["skills"]:
                    parts.append(f"{len(row['skills'])} skill(s)")
                if row["mcp_servers"]:
                    parts.append(f"{len(row['mcp_servers'])} MCP server(s)")
                desc = f" — {row['description']}" if row["description"] else ""
                lines.append(
                    f"- `{row['name']}` ({state}) · {', '.join(parts) or 'empty'}{desc}"
                )
            return reply("\n".join(lines))
        if action == "install":
            if not rest:
                return reply("Usage: `/pack install <git-url|/local/path>`")
            if rest.startswith(("https://", "git@", "ssh://")):
                plugin = manager.install_from_git(rest)
            else:
                plugin = manager.install_from_path(rest)
            manager.sync_mcp_servers()
            return reply(
                f"Pack installed: `{plugin['name']}`\n"
                f"- Skills: {', '.join(plugin['skills']) or 'none'}\n"
                f"- MCP servers: {', '.join(plugin['mcp_servers']) or 'none'}\n\n"
                "Skills are available immediately; MCP servers connect on the next turn."
            )
        if action in ("enable", "disable"):
            if not rest:
                return reply(f"Usage: `/pack {action} <name>`")
            plugin = manager.set_enabled(rest, action == "enable")
            manager.sync_mcp_servers()
            return reply(f"Pack `{plugin['name']}` {action}d.")
        if action in ("remove", "uninstall"):
            if not rest:
                return reply("Usage: `/pack remove <name>`")
            manager.uninstall(rest)
            manager.sync_mcp_servers()
            return reply(f"Pack removed: `{rest}`.")
    except PluginError as exc:
        return reply(f"Pack error: {exc}")
    return reply(
        "Usage: `/pack [list|install <git-url|path>|enable <name>|disable <name>|remove <name>]`\n\n"
        "A pack is a directory bundling skills and MCP servers, installed under "
        "`~/.navin/plugins/`. Skills hot-reload instantly; MCP servers are merged "
        "into the tools config with a `<pack>-` prefix."
    )


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/model", cmd_model)
    router.prefix("/model ", cmd_model)
    router.exact("/history", cmd_history)
    router.prefix("/history ", cmd_history)
    router.exact("/goal", cmd_goal)
    router.prefix("/goal ", cmd_goal)
    router.exact("/trigger", cmd_trigger)
    router.prefix("/trigger ", cmd_trigger)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.exact("/dream-prompt", cmd_dream_prompt)
    router.prefix("/dream-prompt ", cmd_dream_prompt)
    router.exact("/evaluator-prompt", cmd_evaluator_prompt)
    router.prefix("/evaluator-prompt ", cmd_evaluator_prompt)
    router.exact("/skill", cmd_skill)
    router.exact("/help", cmd_help)
    router.exact("/pairing", cmd_pairing)
    router.prefix("/pairing ", cmd_pairing)
    for workflow_cmd in _WORKFLOW_BRIEFS:
        handler = _workflow_handler(workflow_cmd)
        router.exact(workflow_cmd, handler)
        router.prefix(f"{workflow_cmd} ", handler)
    router.exact("/checkpoint", cmd_checkpoint)
    router.prefix("/checkpoint ", cmd_checkpoint)
    router.exact("/pilot", cmd_pilot)
    router.prefix("/pilot ", cmd_pilot)
    router.exact("/pack", cmd_pack)
    router.prefix("/pack ", cmd_pack)
