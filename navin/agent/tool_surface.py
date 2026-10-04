# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-mode tool schema denylists and build-turn allowlists to shrink prompts.

Agent still has the full registry for execution when not denied. Review /
Security / Debug / Ask / Plan omit heavy or off-mission tool schemas so each
LLM call does not pay for scrape/montage/mobile/etc.

``/forge`` and ``/cruise`` go further: they publish an allowlist. A denylist
cannot keep up (every new desk tool is ON until someone remembers to deny it).
Measured 2026-09-01: 54 schemas were ~29k tokens before the user message.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

# Tools that are expensive in schema size and rarely needed outside Agent.
_HEAVY_MEDIA_TOOLS: frozenset[str] = frozenset(
    {
        "scrape",
        "montage",
        "mobile",
        "generate_image",
        "generate_video",
        "generate_music",
        "generate_speech",
        "music_generation",
        "speech_generation",
        "image_generation",
        "video_generation",
        "present_artifact",
        "cli_apps",
        "run_cli_app",
    }
)

_BUILD_TOOLS: frozenset[str] = frozenset(
    {
        "apply_patch",
        "write_file",
        "edit_file",
        "start_app",
        "open_preview",
        "preview_server",
    }
)

# Tools whose writes are the point of planning. A Plan turn refuses every
# other mutating call at the runner level (see AgentRunSpec.plan_read_only),
# but filing board tasks, asking the user and tinting the composer are how a
# plan is delivered, not how it is executed.
PLAN_SAFE_WRITE_TOOLS: frozenset[str] = frozenset(
    {"board", "ask_user", "set_composer_mode", "open_file_preview"}
)

# The one folder a Plan turn may write: the plan's own artifacts (the archify
# diagram source and HTML, an optional Markdown plan). The brief asks for a
# checked archify diagram, and refusing every write made Plan retry it forever.
PLAN_ARTIFACT_DIR = ".navin/plans"
_ARCHIFY_PLAN_ACTIONS = frozenset({"validate", "deliver", "brands"})
_SHELL_CONTROL_CHARS = frozenset(";&|<>`$\n\r")


def _inside_plan_dir(raw: Any, base: Path | None, workspace: Path) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        if base is None:
            return False
        candidate = base / candidate
    root = (workspace / PLAN_ARTIFACT_DIR).resolve()
    try:
        candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def plan_artifact_call(name: str, params: Any, workspace: Path | None) -> bool:
    """Whether a Plan-turn call only writes the plan's own artifacts.

    ``write_file`` into ``.navin/plans/``, or ``node .../archify.mjs
    validate|deliver|brands`` whose file arguments all live there. Anything
    else (another path, a shell operator, another program) stays refused.
    """
    if workspace is None or not isinstance(params, dict):
        return False
    workspace = workspace.resolve()
    if name == "write_file":
        return _inside_plan_dir(params.get("path"), workspace, workspace)
    if name != "exec":
        return False
    command = params.get("command") or params.get("cmd")
    if not isinstance(command, str) or any(ch in _SHELL_CONTROL_CHARS for ch in command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) < 3 or PurePosixPath(tokens[0]).name not in {"node", "node.exe"}:
        return False
    if tokens[2] not in _ARCHIFY_PLAN_ACTIONS:
        return False
    working_dir = params.get("working_dir")
    if isinstance(working_dir, str) and working_dir.strip():
        cwd = Path(working_dir.strip()).expanduser()
        if not cwd.is_absolute():
            cwd = workspace / cwd
    else:
        cwd = workspace
    # Only the bundled CLI: a script merely named archify.mjs could have been
    # written into the plan dir a call earlier.
    from navin.agent.skills import BUILTIN_SKILLS_DIR

    script = Path(tokens[1]).expanduser()
    if not script.is_absolute():
        script = cwd / script
    try:
        if script.resolve() != (BUILTIN_SKILLS_DIR / "archify" / "bin" / "archify.mjs").resolve():
            return False
    except OSError:
        return False
    if tokens[2] == "brands":
        return True
    # validate <type> <candidate> [flags] / deliver <type> <candidate> <output>:
    # the files come right after the type and must stay in the plan dir, and
    # so must any other argument naming a file.
    expected = 2 if tokens[2] == "deliver" else 1
    files = tokens[4:4 + expected]
    if len(files) < expected or any(token.startswith("-") for token in files):
        return False
    files += [
        token for token in tokens[4 + expected:]
        if not token.startswith("-") and PurePosixPath(token).suffix
    ]
    return all(_inside_plan_dir(path, cwd, workspace) for path in files)


# Code-build workflows (/forge, /cruise). Everything else (studio desks,
# scrape, MCP, generators other than images) stays out of the
# schema and is refused at execution. An explicit media request unions
# those generators back in (see AgentLoop._allowed_tools).
CODE_INTERACTION_TOOLS: frozenset[str] = frozenset(
    {"preview_server", "browser", "computer", "mobile", "notebook_edit"}
)
CODE_BUILD_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "apply_patch",
        "manage_files",
        "grep",
        "find_files",
        "list_dir",
        "code_index",
        "lsp",
        "metagraph",
        "exec",
        "write_stdin",
        "list_exec_sessions",
        "git",
        "verify",
        "improvement",
        "lint",
        "test_run",
        "code_review",
        "pr_comments",
        "start_app",
        "open_preview",
        "open_file_preview",
        "open_terminal",
        "open_in_editor",
        "board",
        "skill",
        "spawn",
        "ask_user",
        "set_composer_mode",
        "create_goal",
        "update_goal",
        "web_search",
        "web_fetch",
        "generate_image",
        "my",
    }
) | CODE_INTERACTION_TOOLS

# Nested JSON-schema "description" fields on board/git/code_index are essays.
# Truncating them on allowlisted turns is free accuracy: the enum still names
# every action.
_COMPACT_SCHEMA_DESC_CHARS = 220

# composer_mode → extra tool names denied for schema + execution this turn.
_MODE_DENIED: dict[str, frozenset[str]] = {
    "ask": _HEAVY_MEDIA_TOOLS | _BUILD_TOOLS | frozenset({"spawn", "cron"}),
    # Plan is design-only: no edits, no shell, no file management, no
    # scheduling, apart from the plan's own artifacts under .navin/plans/. Read-heavy tools (git queries, code_index, lsp) stay in the
    # schema; the runner refuses any surviving mutating call per-action.
    # spawn stays for the same reason git does: spawn(action="results") only
    # reports how earlier subagents ended, which is exactly the research a plan
    # has to fold in. SpawnTool.call_read_only refuses action="start" per call,
    # because a subagent runs on its own spec and would mutate by proxy.
    # write_file and exec stay in the schema for the plan's own artifacts
    # (plan_artifact_call); the runner refuses every other path or command.
    "plan": _HEAVY_MEDIA_TOOLS
    | frozenset(
        {
            "apply_patch",
            "edit_file",
            "start_app",
            "mobile",
            "montage",
            "write_stdin",
            "manage_files",
            "cron",
        }
    ),
    "review": _HEAVY_MEDIA_TOOLS
    | frozenset(
        {
            "montage",
            "mobile",
            "spawn",
            "generate_image",
            "generate_video",
            "music_generation",
        }
    ),
    "security": _HEAVY_MEDIA_TOOLS
    | frozenset({"montage", "mobile", "music_generation", "generate_music"}),
    "debug": _HEAVY_MEDIA_TOOLS
    | frozenset({"montage", "scrape", "music_generation", "generate_music"}),
    "montage": frozenset({"scrape", "mobile", "security_scan"}),
}


def denied_tools_for_composer_mode(mode: str | None) -> frozenset[str]:
    """Return tool names to omit from schemas (and block) for this UI mode."""
    key = (mode or "").strip().lower()
    if not key or key in {"agent"}:
        return frozenset()
    return _MODE_DENIED.get(key, frozenset())


def tool_name_is_denied(name: str, denied: frozenset[str] | set[str] | None) -> bool:
    """True when *name* is listed or matches a ``mcp_<server>_`` / glob prefix."""
    if not name or not denied:
        return False
    if name in denied:
        return True
    for item in denied:
        if not item:
            continue
        if item.endswith("*") and name.startswith(item[:-1]):
            return True
        if item.startswith("mcp_") and item.endswith("_") and name.startswith(item):
            return True
    return False


def tool_name_is_allowed(
    name: str, allowed: frozenset[str] | set[str] | None
) -> bool:
    """True when there is no allowlist, or *name* is on it (globs supported)."""
    if allowed is None:
        return True
    if not name:
        return False
    if name in allowed:
        return True
    for item in allowed:
        if not item:
            continue
        if item.endswith("*") and name.startswith(item[:-1]):
            return True
    return False


def _schema_tool_name(schema: dict[str, Any] | Any) -> str:
    if not isinstance(schema, dict):
        return ""
    fn = schema.get("function")
    if isinstance(fn, dict):
        raw = fn.get("name")
        if isinstance(raw, str):
            return raw
    raw = schema.get("name")
    return raw if isinstance(raw, str) else ""


def _truncate_schema_descriptions(node: Any, max_chars: int) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if (
                key == "description"
                and isinstance(value, str)
                and len(value) > max_chars
            ):
                cut = value.rfind(" ", 0, max_chars - 3)
                kept = value[: cut if cut > 80 else max_chars].rstrip()
                out[key] = kept + "..."
            else:
                out[key] = _truncate_schema_descriptions(value, max_chars)
        return out
    if isinstance(node, list):
        return [_truncate_schema_descriptions(item, max_chars) for item in node]
    return node


def compact_tool_schema(
    schema: dict[str, Any],
    *,
    max_chars: int = _COMPACT_SCHEMA_DESC_CHARS,
) -> dict[str, Any]:
    """Copy a tool schema with long ``description`` strings shortened."""
    return _truncate_schema_descriptions(deepcopy(schema), max_chars)


def filter_tool_definitions(
    definitions: list[dict[str, Any]],
    denied: frozenset[str] | set[str] | None = None,
    *,
    allowed: frozenset[str] | set[str] | None = None,
    compact: bool = False,
) -> list[dict[str, Any]]:
    """Keep schemas that pass the allowlist (if any) and are not denied."""
    if not denied and allowed is None and not compact:
        return definitions
    out: list[dict[str, Any]] = []
    for schema in definitions:
        name = _schema_tool_name(schema)
        if name and allowed is not None and not tool_name_is_allowed(name, allowed):
            continue
        if name and tool_name_is_denied(name, denied):
            continue
        out.append(compact_tool_schema(schema) if compact else schema)
    return out


class FilteredToolDefinitions:
    """Adapter: same registry, filtered ``get_definitions`` for token estimates.

    ``denied`` / ``allowed`` may be callables so a mid-turn mode switch
    (set_composer_mode) is reflected in the very next definitions read
    instead of the next turn.
    """

    __slots__ = ("_inner", "_denied", "_allowed", "_compact")

    def __init__(
        self,
        inner: Any,
        denied: frozenset[str] | set[str] | Callable[[], frozenset[str]] | None,
        allowed: (
            frozenset[str] | set[str] | Callable[[], frozenset[str] | None] | None
        ) = None,
        compact: bool | Callable[[], bool] = False,
    ) -> None:
        self._inner = inner
        self._denied = denied if callable(denied) else frozenset(denied or ())
        if callable(allowed) or allowed is None:
            self._allowed = allowed
        else:
            self._allowed = frozenset(allowed)
        self._compact = compact

    def get_definitions(self) -> list[dict[str, Any]]:
        denied = self._denied() if callable(self._denied) else self._denied
        allowed = self._allowed() if callable(self._allowed) else self._allowed
        compact = self._compact() if callable(self._compact) else bool(self._compact)
        return filter_tool_definitions(
            self._inner.get_definitions(),
            denied,
            allowed=allowed,
            compact=compact,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
