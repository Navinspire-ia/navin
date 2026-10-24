"""Organization tier: managed settings (navin/agent/managed_policy.py) and its wiring."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.agent import managed_policy as mp
from navin.agent import user_hooks as uh
from navin.plugins.manager import PluginManager

EXAMPLE = Path(__file__).resolve().parents[1] / "navin/skills/hook-development/examples/shell-advice.py"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".navin").mkdir(parents=True)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("navin.plugins.manager.plugins_root", lambda: plugins)
    monkeypatch.delenv(mp.ENV_PATH, raising=False)
    # Never read the real machine's policy.
    monkeypatch.setattr(mp, "candidate_paths", _env_only)
    mp._CACHE.update(key=None, policy=mp.NONE)
    monkeypatch.setattr("navin.agent.hookify.enabled", lambda: False, raising=False)
    monkeypatch.setattr("navin.agent.security_guidance.enabled", lambda: False, raising=False)
    monkeypatch.setattr("navin.agent.stop_checks.enabled", lambda: False, raising=False)
    managed = tmp_path / "managed-settings.json"
    return SimpleNamespace(home=home, plugins=plugins, ws=workspace, managed=managed, tmp=tmp_path, mp=monkeypatch)


_REAL_CANDIDATES = mp.candidate_paths


def _env_only():
    return [p for p in _REAL_CANDIDATES() if str(p) == __import__("os").environ.get(mp.ENV_PATH, "")]


def write_policy(env, data, *, raw: str | None = None):
    env.managed.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    env.mp.setenv(mp.ENV_PATH, str(env.managed))
    return mp.load()


def hook_json(command: str, event: str = "PreToolUse", matcher: str = "") -> dict:
    return {event: [{"matcher": matcher, "hooks": [{"type": "command", "command": command}]}]}


def py(code: str) -> str:
    return f'{sys.executable} -c "{code}"'


def test_discovery_order_env_first(monkeypatch, tmp_path):
    monkeypatch.setenv(mp.ENV_PATH, str(tmp_path / "x.json"))
    paths = mp.candidate_paths()
    assert paths[0] == tmp_path / "x.json"
    tail = str(paths[-1]).replace("\\", "/")
    assert tail.endswith("managed-settings.json") and "avin" in tail
    monkeypatch.delenv(mp.ENV_PATH)
    assert mp.candidate_paths()[0] != tmp_path / "x.json"


def test_platform_paths(monkeypatch):
    monkeypatch.delenv(mp.ENV_PATH, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert mp.candidate_paths() == [Path("/etc/navin/managed-settings.json")]
    monkeypatch.setattr(sys, "platform", "darwin")
    assert mp.candidate_paths()[0] == Path("/Library/Application Support/Navin/managed-settings.json")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ProgramData", "D:/PD")
    assert mp.candidate_paths() == [Path("D:/PD") / "Navin" / "managed-settings.json"]


def test_schema_parsing(env):
    policy = write_policy(env, {
        "allowManagedHooksOnly": True, "allowManagedPluginsOnly": "true", "allowedPlugins": ["Kit"],
        "mcpAllowlist": ["kit-api"], "env": {"X_PIN": 1}, "permissions": {"deny": ["Browser"]},
        "disableBypassPermissions": True,
    })
    assert policy.active and not policy.locked_down
    assert policy.allow_managed_hooks_only and policy.allow_managed_plugins_only
    assert policy.allowed_plugins == {"kit"} and policy.mcp_allowlist == {"kit-api"}
    assert policy.env == {"X_PIN": "1"} and policy.disable_bypass_permissions
    assert not policy.tools.is_allowed("browser") and policy.tools.is_allowed("read_file")


def test_mtime_cache(env):
    write_policy(env, {"permissions": {"deny": ["browser"]}})
    first = mp.load()
    assert mp.load() is first
    env.managed.write_text(json.dumps({"permissions": {"deny": ["exec", "browser"]}}), encoding="utf-8")
    import os
    os.utime(env.managed, ns=(1, 10**18))
    assert not mp.load().tools.is_allowed("exec")


@pytest.mark.parametrize("raw", ["{not json", "[]", '{"allowedPlugins": "all"}'])
def test_fail_closed_on_corrupt_file(env, raw):
    policy = write_policy(env, None, raw=raw)
    assert policy.locked_down and policy.allow_managed_hooks_only
    assert mp.tool_denied("exec") and mp.tool_denied("write_file")
    assert mp.tool_denied("read_file") is None
    assert not mp.plugin_allowed("kit") and not mp.mcp_server_allowed("kit-api")
    assert "lockdown" in mp.summary()


def test_no_managed_file_no_change(env):
    assert mp.load() is mp.NONE
    assert mp.summary() == "" and mp.pinned_env() == {}
    assert mp.tool_denied("exec") is None and mp.plugin_allowed("x") and mp.mcp_server_allowed("y")
    assert uh.hooks_summary(env.ws) == ""
    assert uh._sources(env.ws) == []


def test_managed_hook_blocks_even_when_user_allows(env):
    marker = env.tmp / "user_ran"
    allow = py("import json;print(json.dumps({'decision':'approve'}));open(r'%s','w')" % marker)
    (env.home / ".navin" / "hooks.json").write_text(json.dumps(hook_json(allow)), encoding="utf-8")
    deny = py("import sys;sys.stderr.write('org says no');sys.exit(2)")
    write_policy(env, {"hooks": hook_json(deny)})
    sources = uh._sources(env.ws)
    assert sources[0][2].startswith(uh.MANAGED_PREFIX)
    reason = asyncio.run(uh.pre_tool_use_block(env.ws, "s", "exec", {"command": "ls"}))
    assert reason == "org says no"
    assert not marker.exists()  # later hooks never ran
    assert "Organization policy in force" in uh.hooks_summary(env.ws)


def test_allow_managed_hooks_only_drops_other_sources(env):
    (env.home / ".navin" / "hooks.json").write_text(json.dumps(hook_json("echo hi")), encoding="utf-8")
    plugin = env.plugins / "kit"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "hooks" / "hooks.json").write_text(json.dumps(hook_json("echo p")), encoding="utf-8")
    project = uh.project_hooks_file(env.ws)
    project.parent.mkdir()
    project.write_text(json.dumps(hook_json("echo proj")), encoding="utf-8")
    uh.trust(project)
    assert len(uh._sources(env.ws)) == 3
    write_policy(env, {"allowManagedHooksOnly": True})
    sources = uh._sources(env.ws)
    assert len(sources) == 1 and sources[0][2].startswith(uh.MANAGED_PREFIX)


def test_pinned_env_wins_in_hook_process(env):
    out = env.tmp / "seen"
    write_policy(env, {"env": {"HTTPS_PROXY": "http://org:1"}})
    env.mp.setenv("HTTPS_PROXY", "http://user:2")
    cmd = py("import os;open(r'%s','w').write(os.environ['HTTPS_PROXY']+'|'+os.environ['%s'])" % (out, mp.ENV_PATH))
    hook = uh.HookCommand(command=cmd, plugin_root="")
    asyncio.run(uh._run_one(hook, {}, env.ws))
    assert out.read_text() == f"http://org:1|{env.managed}"
    assert mp.apply_env({"HTTPS_PROXY": "mine"})["HTTPS_PROXY"] == "http://org:1"


def test_permissions_deny_refuses_tool(env):
    write_policy(env, {"permissions": {"deny": ["browser"]}})
    reason = asyncio.run(uh.pre_tool_use_block(env.ws, "s", "browser", {}))
    assert reason and "organization" in reason
    assert asyncio.run(uh.pre_tool_use_block(env.ws, "s", "read_file", {})) is None


def _plugin(env, name: str, servers: dict) -> None:
    folder = env.plugins / name
    (folder / "skills" / "s").mkdir(parents=True)
    (folder / "skills" / "s" / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nx\n", encoding="utf-8")
    (folder / "mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_allowed_plugins_filtering(env):
    _plugin(env, "kit", {})
    _plugin(env, "other", {})
    write_policy(env, {"allowManagedPluginsOnly": True, "allowedPlugins": ["kit"]})
    rows = {r["name"]: r for r in PluginManager(env.plugins).list()}
    assert rows["kit"]["enabled"] and not rows["kit"]["blocked_by_policy"]
    assert not rows["other"]["enabled"] and rows["other"]["blocked_by_policy"]


def test_mcp_allowlist_filters_plugin_servers(env, monkeypatch):
    _plugin(env, "kit", {"api": {"command": "a"}, "db": {"command": "b"}})
    config = SimpleNamespace(tools=SimpleNamespace(mcp_servers={}))
    monkeypatch.setattr("navin.config.loader.load_config", lambda: config)
    monkeypatch.setattr("navin.config.loader.save_config", lambda c: None)
    write_policy(env, {"mcpAllowlist": ["kit-api"]})
    PluginManager(env.plugins).sync_mcp_servers()
    assert set(config.tools.mcp_servers) == {"kit-api"}


@pytest.mark.parametrize("command,code", [
    ("grep -r foo .", 2), ("find . -name '*.py'", 2), ("rg foo", 0), ("ls | grep x", 0),
])
def test_shell_advice_example(command, code):
    payload = json.dumps({"tool_name": "exec", "tool_input": {"command": command}})
    proc = subprocess.run([sys.executable, str(EXAMPLE)], input=payload, capture_output=True, text=True)
    assert proc.returncode == code
    if code == 2:
        assert "rg" in proc.stderr


def test_exec_environment_keeps_pinned_variables(env):
    from navin.agent.tools.shell import ExecTool

    write_policy(env, {"env": {"NAVIN_PINNED_X": "pinned"}})
    tool = ExecTool(working_dir=str(env.ws), allowed_env_keys=["NAVIN_PINNED_X"])
    env.mp.setenv("NAVIN_PINNED_X", "user")
    built = tool._build_env()
    assert built["NAVIN_PINNED_X"] == "pinned"


def test_disable_bypass_keeps_confirmations_on(env):
    from navin.agent.approval import ApprovalBroker, ApprovalConfig, ApprovalRequest

    broker = ApprovalBroker(config=ApprovalConfig(enabled=False))
    request = ApprovalRequest(tool="exec", action="Run", reason="r")
    assert asyncio.run(broker.ask(request)).allowed
    write_policy(env, {"disableBypassPermissions": True})
    assert not asyncio.run(broker.ask(request)).allowed


def test_user_configured_mcp_servers_follow_the_allowlist(env):
    from navin.agent.tools import mcp as mcp_tools

    write_policy(env, {"mcpAllowlist": ["docs"]})
    seen = {}

    async def fake_connect(servers, registry):
        seen.update(servers)
        return {}

    env.mp.setattr(mcp_tools, "connect_mcp_servers", fake_connect)
    class State:
        _mcp_servers = {"docs": object(), "shady": object()}
        _mcp_stacks: dict = {}
        _mcp_connecting = False
        _mcp_closing = False

    state = State()
    asyncio.run(mcp_tools.connect_missing_servers(state, None))
    assert set(seen) == {"docs"}
    assert state._mcp_policy_refused == {"shady"}
