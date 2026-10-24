# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool that installs, launches and removes MCP servers at runtime.

The settings screen and ``/pack`` were the only ways to add an MCP server, so
an agent asked to "connect the GitHub MCP" could only edit the config file by
hand and tell the user to restart. This tool writes the same config entries
the settings screen writes (validated by ``navin.webui.mcp_presets_api``), then
hot-reloads the live MCP connections of the running agent loop, in the CLI and
in the desktop gateway alike. A missing launcher (npx, uvx, bunx, docker, deno)
comes back with the install command for this OS instead of a silent failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters

# First npx/uvx run downloads the package: give it time, then let the
# connection finish in the background instead of holding the turn.
_CONNECT_TIMEOUT_S = 120.0
_MAX_LISTED_TOOLS = 15
_MAX_LISTED_PRESETS = 80
_BACKGROUND_RELOADS: set[asyncio.Future[Any]] = set()

_LAUNCHER_RUNTIME = {
    "npx": "node",
    "npm": "node",
    "node": "node",
    "uvx": "uv",
    "uv": "uv",
    "bunx": "bun",
    "bun": "bun",
    "docker": "docker",
    "deno": "deno",
}

_RUNTIME_INSTALL: dict[str, tuple[str, dict[str, str]]] = {
    "node": ("Node.js", {
        "windows": "winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements",
        "macos": "brew install node",
        "linux": (
            "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
            " && bash -c '. \"$HOME/.nvm/nvm.sh\" && nvm install --lts'"
        ),
    }),
    "uv": ("uv", {
        "windows": 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
        "macos": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    }),
    "bun": ("Bun", {
        "windows": 'powershell -c "irm bun.sh/install.ps1 | iex"',
        "macos": "curl -fsSL https://bun.sh/install | bash",
        "linux": "curl -fsSL https://bun.sh/install | bash",
    }),
    "docker": ("Docker", {
        "windows": "winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements",
        "macos": "brew install --cask docker",
        "linux": "curl -fsSL https://get.docker.com | sh",
    }),
    "deno": ("Deno", {
        "windows": 'powershell -c "irm https://deno.land/install.ps1 | iex"',
        "macos": "curl -fsSL https://deno.land/install.sh | sh",
        "linux": "curl -fsSL https://deno.land/install.sh | sh",
    }),
}

_SECRET_FLAG_RE = re.compile(r"(key|token|secret|password|passwd|auth)", re.IGNORECASE)


def platform_key() -> str:
    """``windows``, ``macos`` or ``linux`` for the machine running navin."""
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _launcher_name(command: str) -> str:
    base = command.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".cmd", ".bat", ".exe", ".ps1"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def install_hint(command: str, platform: str | None = None) -> str:
    """The one command that installs the runtime behind an MCP launcher."""
    runtime = _LAUNCHER_RUNTIME.get(_launcher_name(command))
    if runtime is None:
        return f"Install `{command}` so it is on PATH, then retry action=add."
    plat = platform or platform_key()
    label, commands = _RUNTIME_INSTALL[runtime]
    return (
        f"`{command}` comes with {label}. Install it on {plat} with: {commands[plat]} "
        "- then retry action=add (no restart needed)."
    )


def command_available(command: str) -> bool:
    """Whether ``command`` resolves now, including freshly installed user tools."""
    if not command:
        return False
    # A runtime the agent just installed lands in a per-user directory the
    # gateway PATH did not contain at startup: pick it up without a restart.
    from navin.utils.path_env import augment_path_for_user_tools

    augment_path_for_user_tools()
    if shutil.which(command):
        return True
    return os.path.isfile(os.path.expanduser(command))


def _redacted_args(args: list[str]) -> list[str]:
    out: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            out.append("***")
            hide_next = False
            continue
        if arg.startswith("-") and _SECRET_FLAG_RE.search(arg):
            if "=" in arg:
                out.append(arg.split("=", 1)[0] + "=***")
            else:
                out.append(arg)
                hide_next = True
            continue
        out.append(arg)
    return out


def _describe_server(cfg: Any) -> str:
    from navin.agent.tools.mcp import _redact_url

    if cfg.command:
        parts = [cfg.command, *_redacted_args(list(cfg.args))]
        text = "stdio: " + " ".join(parts)
    else:
        text = f"{cfg.type or 'http'}: {_redact_url(cfg.url)}"
    if cfg.env:
        text += f" (env: {', '.join(sorted(cfg.env))})"
    return text


def _save_servers(servers: dict[str, Any]) -> list[str]:
    from navin.config import loader

    config = loader.load_config()
    existed = [name for name in servers if name in config.tools.mcp_servers]
    config.tools.mcp_servers.update(servers)
    loader.save_config(config)
    return existed


def _configured_servers() -> dict[str, Any]:
    from navin.config import loader

    return dict(loader.load_config().tools.mcp_servers)


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if str(k).strip()}


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "add", "preset", "remove", "reload"],
            "description": (
                "list: configured servers and the preset catalog; add: save and connect a server; "
                "preset: enable a catalog preset; remove: disconnect and delete; reload: retry all."
            ),
        },
        "name": {
            "type": "string",
            "description": "Server name (lowercase letters, digits, - or _). For preset: the catalog name.",
        },
        "command": {
            "type": "string",
            "description": "stdio launcher, e.g. npx, uvx, bunx, docker, node, python.",
        },
        "args": {"type": "array", "items": {"type": "string"}, "description": "stdio arguments."},
        "env": {
            "type": "object",
            "description": "Extra environment variables (API keys), added on top of the inherited environment.",
        },
        "cwd": {"type": "string", "description": "stdio working directory (optional)."},
        "url": {"type": "string", "description": "Remote MCP endpoint (streamable HTTP or SSE)."},
        "type": {
            "type": "string",
            "enum": ["stdio", "sse", "streamableHttp"],
            "description": "Transport; inferred from command/url when omitted.",
        },
        "headers": {"type": "object", "description": "HTTP headers for a remote server."},
        "enabled_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only register these tools (default: all).",
        },
        "tool_timeout": {"type": "integer", "minimum": 5, "maximum": 600},
        "config": {
            "type": "string",
            "description": (
                "A JSON snippet as published in MCP READMEs: "
                '{"mcpServers": {"name": {"command": "npx", "args": [...]}}}. Adds every server it holds.'
            ),
        },
        "fields": {
            "type": "object",
            "description": "preset: credential values keyed by the field names action=list shows.",
        },
    },
    "required": ["action"],
})
class McpManageTool(Tool):
    """Install, launch and remove MCP servers of the running agent loop."""

    _plugin_discoverable = False  # Needs the live agent loop; registered by AgentLoop.

    def __init__(self, runtime_state: Any = None, registry: Any = None) -> None:
        self._runtime_state = runtime_state
        self._registry = registry

    def __deepcopy__(self, memo: dict[int, Any]) -> McpManageTool:
        clone = self.__class__.__new__(self.__class__)
        memo[id(self)] = clone
        clone._runtime_state = self._runtime_state
        clone._registry = self._registry
        return clone

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def description(self) -> str:
        return (
            "Install, launch and remove MCP servers live, without restarting navin (CLI and desktop, "
            "Windows, macOS and Linux). action=list shows configured servers (connected or not) and the "
            "preset catalog. action=add saves a server - a stdio command, a remote url, or a README "
            "`mcpServers` JSON in config - and connects it at once; its tools appear as "
            "mcp_<server>_<tool> (find them with tool_search if they are not in your tool list yet). "
            "action=preset enables a catalog preset with its credential fields. action=remove "
            "disconnects and deletes a server. action=reload retries every configured server. "
            "A missing launcher (npx, uvx, bunx, docker, deno) returns the install command for this "
            "OS: run it with exec, then retry add. Only add servers the user asked for: a stdio "
            "server runs as a local process with the user's rights."
        )

    def call_read_only(self, arguments: Any) -> bool:
        return isinstance(arguments, dict) and arguments.get("action") == "list"

    async def execute(self, action: str = "list", **kwargs: Any) -> Any:
        from navin.webui.mcp_presets_api import McpPresetError

        handlers = {
            "list": self._list,
            "add": self._add,
            "preset": self._preset,
            "remove": self._remove,
            "reload": self._reload_all,
        }
        handler = handlers.get(action)
        if handler is None:
            return self.unknown_action(action)
        try:
            return await handler(**kwargs)
        except McpPresetError as exc:
            return ToolResult.error(f"Error: {exc.message}")
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")

    # --- actions ---------------------------------------------------------

    async def _list(self, **_: Any) -> str:
        from navin.webui.mcp_presets_api import MCP_PRESETS

        servers = await asyncio.to_thread(_configured_servers)
        live = set(getattr(self._runtime_state, "_mcp_stacks", {}) or {})
        lines = [f"Configured MCP servers ({len(servers)}):"]
        if not servers:
            lines.append("  (none)")
        for name, cfg in sorted(servers.items()):
            if name in live:
                state = f"connected, {len(self._server_tools(name))} tool(s)"
            else:
                state = "not connected"
            lines.append(f"  {name} - {state} - {_describe_server(cfg)}")

        catalog = [
            preset for preset in MCP_PRESETS
            if getattr(preset, "install_supported", True) and preset.name not in servers
        ][:_MAX_LISTED_PRESETS]
        if catalog:
            lines.append("Presets you can enable with action=preset name=<name>:")
            for preset in catalog:
                needs = [field.name for field in preset.fields if field.required]
                suffix = f" (fields: {', '.join(needs)})" if needs else ""
                lines.append(f"  {preset.name} - {preset.display_name}{suffix}")
        return "\n".join(lines)

    async def _add(
        self,
        name: str = "",
        command: str = "",
        args: list[Any] | None = None,
        env: dict[str, Any] | None = None,
        cwd: str = "",
        url: str = "",
        type: str = "",
        headers: dict[str, Any] | None = None,
        enabled_tools: list[Any] | None = None,
        tool_timeout: int | None = None,
        config: Any = None,
        **_: Any,
    ) -> Any:
        from navin.webui.mcp_presets_api import _import_mcp_servers, _mcp_server_config

        if config:
            raw_json = config if isinstance(config, str) else json.dumps(config)
            servers = _import_mcp_servers(raw_json)
        else:
            if not name.strip():
                return ToolResult.error("Error: name is required (or pass a README mcpServers JSON in config).")
            raw_server: dict[str, Any] = {
                "command": command.strip(),
                "args": [str(item) for item in (args or [])],
                "env": _string_map(env),
                "cwd": cwd,
                "url": url.strip(),
                "headers": _string_map(headers),
            }
            if type:
                raw_server["type"] = type
            if enabled_tools is not None:
                raw_server["enabledTools"] = [str(item) for item in enabled_tools]
            if tool_timeout is not None:
                raw_server["toolTimeout"] = tool_timeout
            server_name, cfg = _mcp_server_config(name.strip(), raw_server)
            servers = {server_name: cfg}

        missing = [
            (server_name, cfg.command)
            for server_name, cfg in servers.items()
            if cfg.command and not await asyncio.to_thread(command_available, cfg.command)
        ]
        if missing:
            lines = [
                f"Error: {server_name} needs `{cmd}`, which is not installed on this machine. "
                + install_hint(cmd)
                for server_name, cmd in missing
            ]
            lines.append("Nothing was saved.")
            return ToolResult.error("\n".join(lines))

        existed = await asyncio.to_thread(_save_servers, servers)
        result = await self._reload()
        head = [
            f"{'Updated' if server_name in existed else 'Added'} MCP server {server_name}."
            for server_name in servers
        ]
        return self._report(head, list(servers), result)

    async def _preset(self, name: str = "", fields: dict[str, Any] | None = None, **_: Any) -> Any:
        from navin.webui.mcp_presets_api import _preset_by_name_optional, mcp_presets_action

        preset_name = name.strip()
        if not preset_name:
            return ToolResult.error("Error: name is required; action=list shows the catalog.")
        preset = _preset_by_name_optional(preset_name)
        if preset is None:
            return ToolResult.error(
                f"Error: unknown preset {preset_name}. Use action=list for the catalog, or action=add for a custom server."
            )
        query: dict[str, list[str]] = {"name": [preset_name]}
        for key, value in _string_map(fields).items():
            query[key] = [value]
        payload = await asyncio.to_thread(mcp_presets_action, "enable", query)
        row = next(
            (item for item in payload.get("presets", []) if item.get("name") == preset_name),
            {},
        )
        status = str(row.get("status") or "")
        if status == "missing_credentials":
            needs = [
                f"{field.name} ({field.label})" for field in preset.fields if field.required
            ]
            return ToolResult.error(
                f"Error: {preset.display_name} is saved but needs credentials: {', '.join(needs)}. "
                f"Ask the user for them, then call action=preset name={preset_name} fields={{...}}."
            )
        cfg = (await asyncio.to_thread(_configured_servers)).get(preset_name)
        if cfg is not None and cfg.command and not await asyncio.to_thread(command_available, cfg.command):
            return ToolResult.error(
                f"Error: {preset.display_name} is saved but `{cfg.command}` is not installed. "
                + install_hint(cfg.command).replace("retry action=add", f"retry action=preset name={preset_name}")
            )
        result = await self._reload()
        return self._report([f"Enabled preset {preset.display_name}."], [preset_name], result)

    async def _remove(self, name: str = "", **_: Any) -> Any:
        from navin.webui.mcp_presets_api import mcp_presets_action

        server_name = name.strip().lower()
        if not server_name:
            return ToolResult.error("Error: name is required.")
        payload = await asyncio.to_thread(mcp_presets_action, "remove", {"name": [server_name]})
        result = await self._reload()
        message = str((payload.get("last_action") or {}).get("message") or f"Removed {server_name}.")
        if result is None:
            return f"{message} Restart navin to drop its live connection."
        still_live = server_name in set(result.get("connected") or [])
        return message + (" It is still connected; restart navin." if still_live else " Disconnected.")

    async def _reload_all(self, **_: Any) -> Any:
        servers = await asyncio.to_thread(_configured_servers)
        result = await self._reload()
        return self._report([], sorted(servers), result)

    # --- helpers ---------------------------------------------------------

    async def _reload(self) -> dict[str, Any] | None:
        state, registry = self._runtime_state, self._registry
        if state is None or registry is None or not hasattr(state, "_mcp_servers"):
            return None
        from navin.agent.tools.mcp import reload_servers

        task = asyncio.ensure_future(reload_servers(state, registry))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=_CONNECT_TIMEOUT_S)
        except asyncio.TimeoutError:
            _BACKGROUND_RELOADS.add(task)
            task.add_done_callback(_BACKGROUND_RELOADS.discard)
            return {"timed_out": True}

    def _server_tools(self, server_name: str) -> list[str]:
        if self._registry is None:
            return []
        from navin.agent.tools.mcp import _tool_prefix

        prefix = _tool_prefix(server_name)
        return sorted(name for name in self._registry.tool_names if name.startswith(prefix))

    def _failure_hint(self, server_name: str) -> str:
        servers = getattr(self._runtime_state, "_mcp_servers", {}) or {}
        cfg = servers.get(server_name)
        log_hint = f"The navin log has the reason under \"MCP server '{server_name}'\"."
        if cfg is None:
            return log_hint
        if cfg.command:
            line = " ".join([cfg.command, *_redacted_args(list(cfg.args))])
            return f"Check that `{line}` starts in a terminal and prints only JSON-RPC on stdout. {log_hint}"
        return (
            "Check that the server is running at its url; a 405 means it is not a streamable HTTP "
            f"endpoint, so retry action=add with type=sse. {log_hint}"
        )

    def _report(self, head: list[str], names: list[str], result: dict[str, Any] | None) -> str:
        lines = list(head)
        if result is None:
            lines.append("Saved. This session has no live MCP runtime: restart navin to connect it.")
            return "\n".join(lines)
        if result.get("timed_out"):
            lines.append(
                f"Still connecting after {int(_CONNECT_TIMEOUT_S)}s (a first npx/uvx run downloads the "
                "package). It keeps going in the background: call action=list in a moment."
            )
            return "\n".join(lines)
        connected = set(result.get("connected") or [])
        for server_name in names:
            if server_name in connected:
                tools = self._server_tools(server_name)
                shown = ", ".join(tools[:_MAX_LISTED_TOOLS])
                more = f" (+{len(tools) - _MAX_LISTED_TOOLS} more)" if len(tools) > _MAX_LISTED_TOOLS else ""
                lines.append(f"{server_name}: connected, {len(tools)} tool(s): {shown}{more}".rstrip(": "))
            else:
                lines.append(
                    f"{server_name}: saved but not connected. {self._failure_hint(server_name)}"
                )
        if not names:
            lines.append(str(result.get("message") or "No MCP server is configured."))
        return "\n".join(lines)
