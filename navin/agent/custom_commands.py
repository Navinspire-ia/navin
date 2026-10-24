# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Markdown slash commands: ``/<name> args`` expands a prompt file.

Search order, first name wins (built-in router commands always win first):

1. ``<workspace>/.navin/commands/**/*.md``   project
2. ``~/.navin/commands/**/*.md``             personal
3. ``<plugin>/commands/**/*.md``             enabled plugins (also ``/<plugin>:<name>``)
4. ``navin/builtin_commands/*.md``           shipped with Navin

Frontmatter: ``description``, ``argument-hint``, ``allowed-tools``,
``disable-model-invocation``. Body expansion: ``$ARGUMENTS``, ``$1``..``$9``,
``${1:-default}``, ``${NAVIN_PLUGIN_ROOT}``, ``@path`` (file content inlined)
and ``!`cmd` `` (shell output inlined, only when allowed-tools grants exec).
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.frontmatter import split_frontmatter
from navin.utils.proc import no_window_kwargs

BUILTIN_COMMANDS_DIR = Path(__file__).resolve().parent.parent / "builtin_commands"
_NAME_RE = re.compile(r"^[A-Za-z0-9][\w.-]*$")
_MAX_FILE_BYTES = 128 * 1024
_MAX_INLINE_CHARS = 20000
_SHELL_TIMEOUT_S = 30
_SHELL_RE = re.compile(r"!`([^`\n]+)`")
_FILE_RE = re.compile(r"(?<![\w@/.`])@((?:\$\{NAVIN_PLUGIN_ROOT\}|[\w.~/-])[\w./~${}-]*)")
_TOOL_ALIASES = {
    "bash": "exec", "read": "read_file", "write": "write_file", "edit": "edit_file",
    "multiedit": "apply_patch", "grep": "grep", "glob": "find_files", "ls": "list_dir",
    "webfetch": "web_fetch", "websearch": "web_search", "task": "spawn", "skill": "skill",
    "todowrite": "board",
}


@dataclass
class CustomCommand:
    name: str
    body: str
    source: str
    scope: str = "project"
    description: str = ""
    argument_hint: str = ""
    allowed_tools: list[str] | None = None
    plugin: str = ""
    plugin_root: str = ""
    namespace: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_tools(value: Any) -> list[str] | None:
    """``"Bash(git:*), Read"`` or a list -> Navin tool names (None = inherit)."""
    if value is None:
        return None
    items = value if isinstance(value, list) else re.split(r",(?![^(]*\))", str(value))
    tools: list[str] = []
    for item in items:
        base = re.sub(r"\(.*\)$", "", str(item).strip()).strip()
        if not base:
            continue
        name = _TOOL_ALIASES.get(base.lower(), base)
        if base.lower() == "edit":
            tools.extend(["edit_file", "apply_patch"])
            continue
        tools.append(name)
    return sorted(set(tools))


def _parse(path: Path, *, scope: str, plugin: str = "", plugin_root: str = "", base: Path | None = None) -> CustomCommand | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    if not _NAME_RE.match(path.stem):
        return None
    meta, body, _error = split_frontmatter(text)
    meta = meta if isinstance(meta, dict) else {}
    if not body.strip():
        return None
    namespace = ""
    if base is not None:
        try:
            parts = path.parent.relative_to(base).parts
            namespace = "/".join(parts)
        except ValueError:
            namespace = ""
    return CustomCommand(
        name=path.stem.lower(),
        body=body.strip(),
        source=str(path),
        scope=scope,
        description=str(meta.get("description") or "").strip(),
        argument_hint=str(meta.get("argument-hint") or "").strip(),
        allowed_tools=normalize_tools(meta.get("allowed-tools")),
        plugin=plugin,
        plugin_root=plugin_root,
        namespace=namespace,
        meta=meta,
    )


def _scan(folder: Path, **kwargs: Any) -> list[CustomCommand]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.rglob("*.md")):
        if path.stem.lower() == "readme" or not path.is_file():
            continue
        cmd = _parse(path, base=folder, **kwargs)
        if cmd is not None:
            out.append(cmd)
    return out


def _plugin_command_dirs() -> list[tuple[str, Path, Path]]:
    try:
        from navin.plugins.manager import plugin_component_dirs, plugins_root

        root = plugins_root()
        return [(name, root / name, folder) for name, folder in plugin_component_dirs("commands")]
    except Exception:  # noqa: BLE001
        return []


def list_custom_commands(workspace: Path | None) -> list[CustomCommand]:
    """All visible commands, first hit wins per name (plugin ones also keep ``plugin:name``)."""
    found: list[CustomCommand] = []
    if workspace is not None:
        found += _scan(Path(workspace) / ".navin" / "commands", scope="project")
    home = Path.home() / ".navin" / "commands"
    if workspace is None or home.resolve() != (Path(workspace) / ".navin" / "commands").resolve():
        found += _scan(home, scope="personal")
    for plugin, plugin_dir, folder in _plugin_command_dirs():
        found += _scan(folder, scope="plugin", plugin=plugin, plugin_root=str(plugin_dir))
    found += _scan(BUILTIN_COMMANDS_DIR, scope="builtin")
    seen: set[str] = set()
    unique: list[CustomCommand] = []
    for cmd in found:
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        unique.append(cmd)
    return unique


def find_custom_command(workspace: Path | None, token: str) -> CustomCommand | None:
    """Resolve ``/name`` or ``/plugin:name`` (case-insensitive)."""
    wanted = token.strip().lstrip("/").lower()
    if not wanted:
        return None
    plugin, _, name = wanted.rpartition(":")
    if plugin:
        for p_name, p_dir, folder in _plugin_command_dirs():
            if p_name.lower() != plugin:
                continue
            for cmd in _scan(folder, scope="plugin", plugin=p_name, plugin_root=str(p_dir)):
                if cmd.name == name:
                    return cmd
        # Builtin plugins ship flat commands: /hookify:list is /hookify-list.
        wanted = f"{plugin}-{name}"
    for cmd in list_custom_commands(workspace):
        if cmd.name == wanted:
            return cmd
    return None


def _positional(args: str) -> list[str]:
    try:
        return shlex.split(args)
    except ValueError:
        return args.split()


def _grants_shell(cmd: CustomCommand) -> bool:
    return cmd.allowed_tools is not None and "exec" in cmd.allowed_tools


_NAVIN_PYTHON_RE = re.compile(r"\$\{NAVIN_PYTHON\}|\$NAVIN_PYTHON\b")


def navin_python_command() -> str:
    """Shell words that run Navin's own interpreter (packaged builds included).

    A packaged build has no python on disk: its executable is navin itself,
    whose ``python`` subcommand behaves like the interpreter.
    """
    import os
    import sys

    exe = sys.executable or "python3"
    parts = [exe, "python"] if getattr(sys, "frozen", False) else [exe]
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return " ".join(shlex.quote(part) for part in parts)


def _run_shell(command: str, cwd: Path | None, env_root: str) -> str:
    import os

    python = navin_python_command()
    command = _NAVIN_PYTHON_RE.sub(lambda _m: python, command)
    env = dict(os.environ)
    env["NAVIN_PYTHON"] = python
    if env_root:
        env["NAVIN_PLUGIN_ROOT"] = env_root
    if cwd is not None:
        env["NAVIN_PROJECT_DIR"] = str(cwd)
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=_SHELL_TIMEOUT_S, cwd=str(cwd) if cwd and cwd.is_dir() else None, env=env,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return f"(command timed out after {_SHELL_TIMEOUT_S}s: {command})"
    except OSError as exc:
        return f"(command failed: {exc})"
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.returncode and proc.stderr else "")
    return output.strip()[:_MAX_INLINE_CHARS] or "(no output)"


def _inline_file(token: str, workspace: Path | None, plugin_root: str) -> str | None:
    raw = token.rstrip(".,;:)")
    if plugin_root:
        raw = raw.replace("${NAVIN_PLUGIN_ROOT}", plugin_root)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if workspace is None:
            return None
        path = Path(workspace) / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    roots = [Path(r).resolve() for r in (str(workspace) if workspace else "", plugin_root) if r]
    if not any(resolved == r or r in resolved.parents for r in roots):
        return None
    if not resolved.is_file():
        return None
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")[:_MAX_INLINE_CHARS]
    except OSError:
        return None
    return f"\n<file path=\"{raw}\">\n{content}\n</file>\n"


def expand_command(cmd: CustomCommand, args: str, workspace: Path | None) -> str:
    """Body of *cmd* with arguments, files and (when allowed) shell output inlined."""
    args = args.strip()
    positional = _positional(args)
    text = cmd.body
    if cmd.plugin_root:
        text = text.replace("${NAVIN_PLUGIN_ROOT}", cmd.plugin_root)

    def default_sub(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return positional[index] if index < len(positional) else match.group(2)

    text = re.sub(r"\$\{([1-9]):-([^}]*)\}", default_sub, text)
    had_arguments = "$ARGUMENTS" in text
    text = text.replace("$ARGUMENTS", args)

    def pos_sub(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return positional[index] if index < len(positional) else ""

    used_positional = bool(re.search(r"\$[1-9](?!\d)", text))
    text = re.sub(r"\$([1-9])(?!\d)", pos_sub, text)

    def file_sub(match: re.Match[str]) -> str:
        inlined = _inline_file(match.group(1), workspace, cmd.plugin_root)
        return inlined if inlined is not None else match.group(0)

    text = _FILE_RE.sub(file_sub, text)
    if _SHELL_RE.search(text):
        if _grants_shell(cmd):
            text = _SHELL_RE.sub(
                lambda m: f"\n```\n{_run_shell(m.group(1), workspace, cmd.plugin_root)}\n```\n", text
            )
        else:
            logger.info("Command {} uses !`...` without allowed-tools exec; left as text", cmd.name)
    if args and not had_arguments and not used_positional:
        text = f"{text}\n\nArguments: {args}"
    return text.strip()


def build_command_turn(cmd: CustomCommand, args: str, workspace: Path | None) -> tuple[str, dict[str, Any]]:
    """Message content + metadata for the agent turn that runs *cmd*."""
    label = f"/{cmd.plugin + ':' if cmd.plugin else ''}{cmd.name}"
    content = (
        f"[Command {label}{' ' + args.strip() if args.strip() else ''}]\n"
        f"{expand_command(cmd, args, workspace)}"
    )
    meta: dict[str, Any] = {"custom_command": label, "custom_command_source": cmd.source}
    if cmd.allowed_tools:
        # Always keep the read/plan basics so the command can orient itself.
        meta["custom_command_allowed_tools"] = sorted(set(cmd.allowed_tools) | {"skill", "read_file"})
    return content, meta


def commands_summary(workspace: Path | None, *, limit: int = 40, include_builtin: bool = True) -> str:
    """Markdown list of custom commands for the system prompt ('' when none).

    ``include_builtin=False`` keeps only the project's and the user's own
    commands: the bundled ones are typed by the user and cost ~2.7k prompt
    characters on every call for an agent that never runs them itself.
    """
    rows = []
    commands = [
        cmd for cmd in list_custom_commands(workspace)
        if include_builtin or cmd.scope != "builtin"
    ]
    for cmd in commands[:limit]:
        label = f"/{cmd.name}"
        hint = f" {cmd.argument_hint}" if cmd.argument_hint else ""
        origin = f" ({cmd.plugin})" if cmd.plugin else (" (built-in)" if cmd.scope == "builtin" else "")
        rows.append(f"- `{label}{hint}`{origin} - {cmd.description or 'no description'}")
    return "\n".join(rows)
