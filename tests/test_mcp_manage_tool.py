# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agents install, launch and remove MCP servers without a restart."""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

from navin.agent.tools import mcp_manage
from navin.agent.tools.mcp_manage import McpManageTool, install_hint
from navin.agent.tools.registry import ToolRegistry
from navin.config.schema import Config


@pytest.fixture
def config_store(monkeypatch):
    store = {"config": Config()}

    def load(*_args, **_kwargs):
        return store["config"].model_copy(deep=True)

    def save(cfg, *_args, **_kwargs):
        store["config"] = cfg.model_copy(deep=True)

    for module in ("navin.config.loader", "navin.webui.mcp_presets_api"):
        monkeypatch.setattr(f"{module}.load_config", load, raising=False)
        monkeypatch.setattr(f"{module}.save_config", save, raising=False)
    return store


class _LoopState:
    """The MCP attributes AgentLoop carries (hashable, like the real loop)."""

    def __init__(self) -> None:
        self._mcp_servers: dict = {}
        self._mcp_stacks: dict = {}
        self._mcp_closing = False
        self._mcp_connecting = False


@pytest.mark.parametrize(
    ("platform", "needle"),
    [("windows", "winget install --id OpenJS.NodeJS.LTS"), ("macos", "brew install node"), ("linux", "nvm install --lts")],
)
def test_install_hint_names_the_runtime_for_each_os(platform, needle):
    hint = install_hint("C:\\Program Files\\nodejs\\npx.cmd", platform)
    assert "Node.js" in hint and needle in hint
    assert "astral.sh/uv/install.ps1" in install_hint("uvx", "windows")
    assert "astral.sh/uv/install.sh" in install_hint("uvx", "linux")
    assert "get.docker.com" in install_hint("docker", "linux")
    assert "on PATH" in install_hint("my-server", "macos")


def test_add_with_missing_launcher_returns_install_command_and_saves_nothing(config_store, monkeypatch):
    monkeypatch.setattr(mcp_manage, "command_available", lambda _cmd: False)
    result = asyncio.run(
        McpManageTool().execute(action="add", name="fetch", command="uvx", args=["mcp-server-fetch"])
    )
    assert result.is_error
    assert "astral.sh/uv" in result and "Nothing was saved" in result
    assert config_store["config"].tools.mcp_servers == {}


def test_add_list_remove_custom_server_without_live_runtime(config_store):
    tool = McpManageTool()
    added = asyncio.run(tool.execute(
        action="add",
        name="Local-Py",
        command=sys.executable,
        args=["server.py", "--api-key", "sk-secret"],
        env={"API_TOKEN": "sk-secret"},
    ))
    assert "Added MCP server local-py" in added
    assert "restart navin" in added
    cfg = config_store["config"].tools.mcp_servers["local-py"]
    assert cfg.type == "stdio" and cfg.env == {"API_TOKEN": "sk-secret"}

    listed = asyncio.run(tool.execute(action="list"))
    assert "local-py - not connected" in listed
    assert "sk-secret" not in listed and "API_TOKEN" in listed

    removed = asyncio.run(tool.execute(action="remove", name="local-py"))
    assert "local-py" not in config_store["config"].tools.mcp_servers
    assert not getattr(removed, "is_error", False)


def test_add_imports_readme_mcp_servers_json(config_store, monkeypatch):
    monkeypatch.setattr(mcp_manage, "command_available", lambda _cmd: True)
    snippet = '{"mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}}}'
    result = asyncio.run(McpManageTool().execute(action="add", config=snippet))
    assert "Added MCP server github" in result
    assert config_store["config"].tools.mcp_servers["github"].command == "npx"


def test_add_launches_a_real_stdio_server_live_and_remove_disconnects_it(config_store, tmp_path):
    pytest.importorskip("mcp.server.fastmcp")
    script = tmp_path / "pong_server.py"
    script.write_text(textwrap.dedent(
        '''
        from mcp.server.fastmcp import FastMCP

        app = FastMCP("pong")

        @app.tool()
        def ping(text: str = "") -> str:
            """Reply pong."""
            return "pong" + text

        if __name__ == "__main__":
            app.run()
        '''
    ))

    async def scenario():
        from navin.agent.tools.mcp import close_mcp_servers

        state, registry = _LoopState(), ToolRegistry()
        tool = McpManageTool(runtime_state=state, registry=registry)
        try:
            added = await tool.execute(action="add", name="pong", command=sys.executable, args=[str(script)])
            assert "pong: connected, 1 tool(s): mcp_pong_ping" in added, added
            assert "pong" in state._mcp_stacks
            reply = await registry.get("mcp_pong_ping").execute(text="!")
            assert "pong!" in reply
            assert "pong - connected, 1 tool(s)" in await tool.execute(action="list")

            removed = await tool.execute(action="remove", name="pong")
            assert "Disconnected" in removed
            assert "pong" not in state._mcp_stacks
            assert registry.get("mcp_pong_ping") is None
        finally:
            await close_mcp_servers(state)

    asyncio.run(scenario())
