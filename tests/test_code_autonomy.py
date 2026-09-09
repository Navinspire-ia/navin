"""Real regressions in skill routing, instruction scope and tool recovery."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navin.agent.context import ContextBuilder
from navin.agent.loop import AgentLoop
from navin.agent.project_agents import _parse_agent_file, _parse_agent_toml
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.skills import (
    SkillsLoader,
    _home_skill_dirs,
    clear_home_skill_dir_cache,
    clear_skills_index_cache,
)
from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS, CODE_INTERACTION_TOOLS
from navin.agent.tools import browser, computer
from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.filesystem import ListDirTool, ReadFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.skill_catalog import SkillCatalogTool
from navin.providers.base import ToolCallRequest
from navin.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)
from tests.test_computer_tool import FakeBackend


def write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def skill(path: Path, description: str = "Deploy the project") -> Path:
    return write(path / "SKILL.md", f"---\ndescription: {description}\n---\nFollow this project playbook.\n")


@pytest.fixture(autouse=True)
def isolated_catalog():
    with patch("navin.agent.skills._home_skill_dirs", return_value=[]), patch.object(
        SkillsLoader, "_plugin_skill_dirs", return_value=[],
    ):
        clear_skills_index_cache()
        yield
        clear_skills_index_cache()


@pytest.mark.parametrize("harness", [".agents/skills", ".claude/skills", ".omp/agent/skills", ".team/skills"])
def test_catalog_follows_the_active_project_across_harnesses(tmp_path, harness):
    gateway = tmp_path / "gateway"
    project = tmp_path / "project"
    skill(gateway / "skills" / "deploy-app", "Wrong project")
    skill(project / harness / "deploy-app", "Selected project")
    tool = SkillCatalogTool(workspace=gateway, builtin_skills_dir=tmp_path / "none")
    scope = bind_workspace_scope(build_workspace_scope(project, "restricted"))
    try:
        result = asyncio.run(tool.execute(action="find", query="deploy-app"))
        assert "Selected project" in result
        assert "Wrong project" not in result
        result = asyncio.run(tool.execute(action="read", name="deploy-app"))
        assert (project / harness / "deploy-app").as_posix() in result.replace("\\", "/")
        assert not (project / ".navin").exists(), "Reading a skill must not create or migrate folders"
    finally:
        reset_workspace_scope(scope)


def test_skill_discovery_does_not_migrate_a_read_only_legacy_library(tmp_path):
    source = skill(tmp_path / ".navin/skill" / "legacy-playbook")
    loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none")
    assert "legacy-playbook" in loader.build_skills_index()
    assert source.is_file()
    assert not (tmp_path / ".navin/skills").exists()


def test_skill_search_runs_without_blocking_the_event_loop(tmp_path):
    async def check():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        tool = SkillCatalogTool(workspace=tmp_path)

        def slow_search(*_args):
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return "result"

        with patch.object(tool, "_execute", side_effect=slow_search):
            pending = asyncio.create_task(tool.execute(action="list"))
            try:
                await asyncio.wait_for(started.wait(), 1)
                assert not pending.done(), "The event loop must run while the catalog is scanning"
            finally:
                release.set()
                assert await pending == "result"
        assert tool.read_only

    asyncio.run(check())


def test_a_new_harness_and_a_new_skill_are_visible_on_the_same_loader(tmp_path):
    loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none")
    assert loader.build_skills_index() == ""
    skill(tmp_path / ".new-harness/skills/added-later")
    clear_skills_index_cache()
    assert "added-later" in loader.build_skills_index()
    skill(tmp_path / ".new-harness/skills/second-playbook")
    assert "second-playbook" in loader.build_skills_index()


def test_windows_bom_and_malformed_skills_do_not_break_the_catalog(tmp_path):
    skill(tmp_path / "skills/deploy-app", "Déploiement vérifié")
    write(tmp_path / "skills/bom/SKILL.md", '\ufeff---\ndescription: Windows playbook\n---\nUse PowerShell.\n')
    write(tmp_path / "skills/broken/SKILL.md", "---\ndescription: [not, a, string]\nmetadata: {navin: {requires: null}}\n---\nBody.\n")
    write(tmp_path / "skills/odd/SKILL.md", '---\nmetadata: {navin: {requires: {bins: [null, 42], env: "NAVIN_TEST_MISSING_ENV_7C1"}}}\n---\nBody.\n')
    bad = write(tmp_path / "skills/invalid/SKILL.md", "")
    bad.write_bytes(b"\xff\xfe\x80")
    loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none")
    assert "deploy-app" in loader.build_skills_index()
    assert loader.search_skills("deploiement verifie")[0]["name"] == "deploy-app"
    assert loader.get_skill_metadata("bom")["description"] == "Windows playbook"
    assert loader.load_skill("invalid") is None
    assert loader.get_skill_availability("odd") == (False, "ENV: NAVIN_TEST_MISSING_ENV_7C1")


def test_a_dependency_installed_during_the_task_refreshes_the_cached_catalog(tmp_path):
    write(tmp_path / "skills/deploy/SKILL.md", '---\nmetadata: {navin: {requires: {bins: [navin-test-tool-7c1]}}}\n---\nDeploy.\n')
    with patch("navin.agent.skills.time.monotonic", return_value=100.0) as clock, patch(
        "navin.agent.skills.shutil.which", return_value=None,
    ) as which:
        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none")
        assert "Unavailable" in loader.build_skills_index()
        which.return_value = "/new-bin/navin-test-tool-7c1"
        clock.return_value = 103.0
        assert loader.build_skills_index() == "deploy"
        assert loader.get_skill_availability("deploy") == (True, "")


def test_new_home_harnesses_are_discovered_without_restarting(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with patch("pathlib.Path.home", return_value=home):
        clear_home_skill_dir_cache()
        assert home / ".new-tool/skills" not in _home_skill_dirs()
        before = home.stat()
        skill(home / ".new-tool/skills/new-skill")
        os.utime(home, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        assert home / ".new-tool/skills" in _home_skill_dirs()
        clear_home_skill_dir_cache()


def test_quoted_false_does_not_force_load_an_unrelated_skill(tmp_path):
    write(tmp_path / "skills/disabled-default/SKILL.md", '---\nalways: "false"\n---\nDo not preload.\n')
    write(tmp_path / "skills/enabled-default/SKILL.md", '---\nalways: "true"\n---\nPreload.\n')
    assert SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none").get_always_skills() == ["enabled-default"]


def test_explicit_slugs_are_loaded_without_treating_ordinary_words_as_skills(tmp_path):
    for name in ("project-release", "git", "memory", "révision"):
        skill(tmp_path / "skills" / name)
    loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none")
    assert loader.mentioned_skill_names("Use project-release, then $memory and `git`.") == ["project-release", "memory", "git"]
    assert loader.mentioned_skill_names("Fix git history and memory consumption.") == []
    assert loader.mentioned_skill_names("Utilise $révision.") == ["révision"]


def test_disabled_skills_and_path_traversal_cannot_bypass_the_catalog(tmp_path):
    skill(tmp_path / "skills/disabled-playbook")
    write(tmp_path / ".navin/outside.md", "Not a skill")
    loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none", disabled_skills={"disabled-playbook"})
    assert loader.load_skills_for_context(["disabled-playbook"]) == ""
    for name in ("../outside", "..\\outside", "/outside", "C:outside", "bad\0name"):
        assert loader.load_skill(name) is None
    tool = SkillCatalogTool(workspace=tmp_path, disabled_skills={"disabled-playbook"})
    result = asyncio.run(tool.execute(action="read", name="disabled-playbook"))
    assert result.is_error and "disabled" in result


def test_directory_instructions_follow_reads_and_refresh_after_edits(tmp_path):
    write(tmp_path / "src/AGENTS.md", "Use the repository service layer.")
    write(tmp_path / "src/api/AGENTS.md", "Use typed API responses.")
    write(tmp_path / "src/api/CLAUDE.md", "Ignored when AGENTS.md exists.")
    write(tmp_path / "other/AGENTS.md", "Unrelated subtree.")
    write(tmp_path / "src/api/handler.py", "value = 1\n")
    reader = ReadFileTool(workspace=tmp_path)
    first = asyncio.run(reader.execute(path="src/api/handler.py"))
    assert first.index("Use the repository service layer") < first.index("Use typed API responses")
    assert "Unrelated subtree" not in first
    assert "Ignored when" not in first
    write(tmp_path / "src/api/AGENTS.md", "Use the revised API contract.")
    second = asyncio.run(reader.execute(path="src/api/handler.py"))
    assert "unchanged" in second
    assert "revised API contract" in second
    listing = asyncio.run(ListDirTool(workspace=tmp_path).execute(path="src/api"))
    assert "revised API contract" in listing


def test_file_reads_use_one_snapshot_and_detect_changes_with_the_same_timestamp(tmp_path):
    target = write(tmp_path / "module.py", "original = 1\n")
    tool = ReadFileTool(workspace=tmp_path)
    original_read = Path.read_bytes
    reads = []

    def tracked_read(path):
        if path == target:
            reads.append(path)
        return original_read(path)

    with patch.object(Path, "read_bytes", tracked_read):
        assert "original = 1" in asyncio.run(tool.execute(path="module.py"))
    assert len(reads) == 1
    stamp = target.stat()
    target.write_text("modified = 2\n", encoding="utf-8")
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert "modified = 2" in asyncio.run(tool.execute(path="module.py"))
    assert "unchanged" in asyncio.run(tool.execute(path="module.py"))


def test_malformed_agent_files_and_bom_do_not_abort_project_context(tmp_path):
    bad = write(tmp_path / "AGENTS.md", "")
    bad.write_bytes(b"\xff\xfe\x80")
    write(tmp_path / "CLAUDE.md", "Valid fallback instructions.")
    context = ContextBuilder(tmp_path)._load_bootstrap_files()
    assert "Could not read" in context and "Valid fallback" in context
    md = write(tmp_path / "review.md", "\ufeff---\nname: reviewer\n---\nReview the code.")
    toml = write(tmp_path / "review.toml", '\ufeffname = "reviewer"\ndeveloper_instructions = "Review the code."\n')
    assert _parse_agent_file(md).name == "reviewer"
    assert _parse_agent_toml(toml).name == "reviewer"
    md.write_bytes(b"\xff\xfe\x80")
    toml.write_bytes(b"\xff\xfe\x80")
    assert _parse_agent_file(md) is None
    assert _parse_agent_toml(toml) is None


class RecoveryTool(Tool):
    name = "recovery_probe"
    description = "Exercise real runner failure handling."
    parameters = {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}

    def __init__(self):
        self.calls = 0
        self.outcome = "ok"

    async def execute(self, **_kwargs):
        self.calls += 1
        if self.outcome == "hang":
            await asyncio.Event().wait()
        return ToolResult.error("Temporary failure") if self.outcome == "error" else "ok"


def run_spec(tool, **kwargs):
    registry = ToolRegistry()
    registry.register(tool)
    return AgentRunSpec(
        initial_messages=[], tools=registry, runtime=SimpleNamespace(),
        max_iterations=20, max_tool_result_chars=2000, **kwargs,
    )


def test_timed_out_and_invalid_calls_get_a_bounded_retry_budget():
    async def check(outcome, arguments):
        tool = RecoveryTool()
        tool.outcome = outcome
        spec = run_spec(tool, tool_timeout_s=0.01)
        counts = {}
        runner = AgentRunner()
        for _ in range(6):
            result, event, fatal = await runner._dispatch_tool_call(
                spec, ToolCallRequest(id="probe", name=tool.name, arguments=arguments),
                {}, {}, tool_failure_counts=counts,
            )
            assert event["status"] == "error" and fatal is None
        assert "blocked repeated identical" in result
        assert tool.calls == (5 if outcome == "hang" else 0)

    asyncio.run(check("hang", {"target": "one"}))
    asyncio.run(check("ok", {}))


def test_successful_recovery_resets_only_the_matching_failure_streak():
    async def check():
        tool = RecoveryTool()
        spec = run_spec(tool)
        runner = AgentRunner()
        counts = {}
        for outcome in ("error", "error", "ok", "error", "error", "error", "ok"):
            tool.outcome = outcome
            result, _, fatal = await runner._dispatch_tool_call(
                spec, ToolCallRequest(id="probe", name=tool.name, arguments={"target": "one"}),
                {}, {}, tool_failure_counts=counts,
            )
            assert "blocked" not in result and fatal is None
        assert tool.calls == 7 and counts == {}

    asyncio.run(check())


@pytest.mark.parametrize("name", ["browser", "computer", "mobile", "notebook_edit", "preview_server"])
def test_build_workflows_can_execute_their_preview_and_device_tools(name):
    async def check():
        tool = RecoveryTool()
        tool.name = name
        spec = run_spec(tool, allowed_tools=CODE_BUILD_ALLOWED_TOOLS)
        _, event, fatal = await AgentRunner()._dispatch_tool_call(
            spec, ToolCallRequest(id="probe", name=name, arguments={"target": "one"}), {}, {},
        )
        assert tool.calls == 1 and event["status"] == "ok" and fatal is None

    asyncio.run(check())


def test_a_browser_handoff_survives_a_continue_message_in_a_backend_project(tmp_path):
    session = browser._BrowserSession(browser.BrowserToolConfig())
    session.page = SimpleNamespace()
    with patch.dict(browser._SESSIONS, {"websocket:active": session}, clear=True):
        assert "browser" not in AgentLoop._denied_tools(
            None, {"product_module": "code", "session_key": "websocket:active"}, "continue", workspace=tmp_path,
        )
        assert "browser" in AgentLoop._denied_tools(
            None, {"product_module": "code", "session_key": "websocket:other"}, "continue", workspace=tmp_path,
        )


def test_resumed_build_sessions_receive_the_new_tools_without_overriding_custom_scopes():
    old_default = sorted(CODE_BUILD_ALLOWED_TOOLS - CODE_INTERACTION_TOOLS)
    assert AgentLoop._allowed_tools(None, {}, session_metadata={"allowed_tools": old_default}) == CODE_BUILD_ALLOWED_TOOLS
    assert AgentLoop._allowed_tools(None, {"allowed_tools": ["read_file"]}, session_metadata={"allowed_tools": old_default}) == {"read_file"}
    assert AgentLoop._allowed_tools(None, {}, session_metadata={"allowed_tools": ["read_file"]}) == {"read_file"}


@pytest.mark.parametrize("kind", ["browser", "computer"])
@pytest.mark.parametrize("replacement", [False, True])
def test_queued_live_input_is_rejected_after_session_close_or_replacement(kind, replacement):
    async def check():
        key = "websocket:live-race"
        module = browser if kind == "browser" else computer
        if kind == "browser":
            session = browser._BrowserSession(browser.BrowserToolConfig())
            page = MagicMock()
            page.is_closed.return_value = False
            page.keyboard.type = AsyncMock()
            session.page = page
            live_id = session.live_id
        else:
            backend = FakeBackend()
            config = computer.ComputerToolConfig(
                enabled=True, ask="never", live_view=False, audit_log=False, audit_screenshots=False,
            )
            session = computer.ComputerSession(config, backend_factory=lambda: backend, session_key=key)
            session.last_shot = backend.screenshot()
            live_id = f"desktop-{session.id}"
        with patch.dict(module._SESSIONS, {key: session}, clear=True):
            await session.lock.acquire()
            queued = asyncio.create_task(module.dispatch_live_input(key, "text", {"text": "stale input"}, live_id=live_id))
            try:
                await asyncio.sleep(0)
                assert not queued.done()
                if replacement:
                    module._SESSIONS[key] = SimpleNamespace(id="replacement")
                else:
                    session.closed = True
                    module._SESSIONS.pop(key)
            finally:
                session.lock.release()
            with pytest.raises(ValueError, match="closed or replaced"):
                await queued
            if kind == "browser":
                page.keyboard.type.assert_not_called()
            else:
                assert ("type", "stale input") not in backend.calls
                await session.close()

    asyncio.run(check())


@pytest.mark.parametrize("payload", [
    {"x": float("nan"), "y": 1},
    {"x": 1, "y": float("inf")},
    {"x": 1, "y": 1, "width": -100, "height": 100},
])
def test_invalid_browser_coordinates_cannot_turn_into_unintended_corner_clicks(payload):
    session = browser._BrowserSession(browser.BrowserToolConfig())
    page = SimpleNamespace(viewport_size={"width": 1280, "height": 800})
    with pytest.raises(ValueError):
        browser._live_point(session, page, payload)


def test_browser_live_flushes_the_last_frame_when_a_page_stops_changing():
    async def check():
        session = browser._BrowserSession(browser.BrowserToolConfig(live_view_min_frame_ms=100))
        cdp = SimpleNamespace(send=AsyncMock(), detach=AsyncMock())
        session._live_cdp = cdp
        frames = []
        with patch.object(session, "_emit_live", side_effect=lambda phase, **kw: frames.append(kw["data"])):
            try:
                for index, data in enumerate(("initial", "intermediate", "final")):
                    session._on_screencast_frame(cdp, {"sessionId": index, "data": data})
                assert frames == ["initial"]
                await asyncio.sleep(0.15)
                assert frames == ["initial", "final"]
                assert cdp.send.await_count == 3, "Every frame must still be acknowledged"
            finally:
                await session._stop_screencast()

    asyncio.run(check())


def test_browser_live_drops_queued_frames_when_the_stream_closes():
    async def check():
        session = browser._BrowserSession(browser.BrowserToolConfig(live_view_min_frame_ms=100))
        cdp = SimpleNamespace(send=AsyncMock(), detach=AsyncMock())
        session._live_cdp = cdp
        with patch.object(session, "_emit_live") as emit:
            session._on_screencast_frame(cdp, {"sessionId": 1, "data": "initial"})
            session._on_screencast_frame(cdp, {"sessionId": 2, "data": "stale"})
            await session._stop_screencast()
            await asyncio.sleep(0.15)
            assert emit.call_count == 1
            assert session._live_frame_timer is None
            assert session._live_pending_frame is None

    asyncio.run(check())


@pytest.mark.parametrize("target,explicit,expected", [
    ("tests/test_api.py", None, ["pytest"]),
    ("tests\\test_api.py::test_response", None, ["pytest"]),
    ("webui/src/app.test.ts", None, ["vitest"]),
    ("webui/src/app.test.tsx", None, ["vitest"]),
    (None, None, ["pytest", "vitest"]),
    ("tests/test_api.py", ["vitest"], ["vitest"]),
])
def test_verify_targets_the_matching_suite_in_mixed_projects(tmp_path, monkeypatch, target, explicit, expected):
    from navin.quality import testing

    write(tmp_path / "tests/test_api.py", "def test_response(): pass\n")
    write(tmp_path / "webui/package.json", '{"devDependencies":{"vitest":"*"}}')
    calls = []

    def run(name, spec, root, selected):
        calls.append((name, selected))
        return testing.TestOutcome(runner=name, ran=True, passed=1, total=1, exit_code=0)

    monkeypatch.setattr(testing, "_runner_argv", lambda *_: ["available-test-runner"])
    monkeypatch.setattr(testing, "_run_one", run)
    outcomes = testing.run_tests(tmp_path, runners=explicit, target=target)
    assert [name for name, _ in calls] == expected
    assert all(selected == target for _, selected in calls)
    assert all(outcome.ok for outcome in outcomes)
