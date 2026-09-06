"""Restrict-to-workspace Settings must unlock chats that still say restricted."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from navin.agent.tools.filesystem import ReadFileTool, WriteFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.shell import ExecTool, handle_exec_policy_reload
from navin.security.workspace_access import (
    WorkspaceScopeResolver,
    bind_live_restrict_to_workspace,
    bind_workspace_scope,
    build_workspace_scope,
    current_tool_workspace,
    effective_restrict_to_workspace,
    live_restrict_to_workspace_setting,
    reset_live_restrict_to_workspace,
    reset_workspace_scope,
)


def _run(coro):
    result = asyncio.run(coro)
    return str(getattr(result, "content", result))


class _RestrictCase(unittest.TestCase):
    def setUp(self) -> None:
        self._project = tempfile.TemporaryDirectory()
        self._other = tempfile.TemporaryDirectory()
        self.addCleanup(self._project.cleanup)
        self.addCleanup(self._other.cleanup)
        self.project = Path(self._project.name).resolve()
        self.other = Path(self._other.name).resolve()
        self.other_file = self.other / "secret.txt"
        self.other_file.write_text("from-other-workspace\n", encoding="utf-8")
        self.restricted = build_workspace_scope(self.project, "restricted")
        self.full = build_workspace_scope(self.project, "full")

    def _bind(self, scope, live: bool):
        scope_token = bind_workspace_scope(scope)
        live_token = bind_live_restrict_to_workspace(live)
        self.addCleanup(reset_workspace_scope, scope_token)
        self.addCleanup(reset_live_restrict_to_workspace, live_token)


class PolicyMathTests(unittest.TestCase):
    def test_settings_off_unlocks_restricted_chat(self) -> None:
        scope = build_workspace_scope(Path("/tmp/navin-project"), "restricted")
        scope_token = bind_workspace_scope(scope)
        live_token = bind_live_restrict_to_workspace(False)
        try:
            access = current_tool_workspace(
                "/tmp/navin-project",
                restrict_to_workspace=True,
            )
            self.assertFalse(access.restrict_to_workspace)
            self.assertEqual(access.project_path, scope.project_path)
        finally:
            reset_live_restrict_to_workspace(live_token)
            reset_workspace_scope(scope_token)

    def test_settings_on_keeps_restricted_chat_locked(self) -> None:
        scope = build_workspace_scope(Path("/tmp/navin-project"), "restricted")
        scope_token = bind_workspace_scope(scope)
        live_token = bind_live_restrict_to_workspace(True)
        try:
            access = current_tool_workspace(
                "/tmp/navin-project",
                restrict_to_workspace=True,
            )
            self.assertTrue(access.restrict_to_workspace)
        finally:
            reset_live_restrict_to_workspace(live_token)
            reset_workspace_scope(scope_token)

    def test_full_access_chat_unlocks_when_settings_stay_on(self) -> None:
        scope = build_workspace_scope(Path("/tmp/navin-project"), "full")
        scope_token = bind_workspace_scope(scope)
        live_token = bind_live_restrict_to_workspace(True)
        try:
            access = current_tool_workspace(
                "/tmp/navin-project",
                restrict_to_workspace=True,
            )
            self.assertFalse(access.restrict_to_workspace)
        finally:
            reset_live_restrict_to_workspace(live_token)
            reset_workspace_scope(scope_token)

    def test_unbound_live_flag_keeps_session_value(self) -> None:
        self.assertTrue(effective_restrict_to_workspace(True))
        self.assertFalse(effective_restrict_to_workspace(False))

    def test_sandbox_does_not_lock_file_tools_when_settings_are_off(self) -> None:
        """OS sandbox confines exec writes; Settings restrict is the file lock.

        A second silent lock made read_file / edit look blocked with no
        approval card. sandbox_restricts_workspace is ignored on purpose.
        """
        scope = build_workspace_scope(Path("/tmp/navin-project"), "restricted")
        scope_token = bind_workspace_scope(scope)
        live_token = bind_live_restrict_to_workspace(False)
        try:
            access = current_tool_workspace(
                "/tmp/navin-project",
                restrict_to_workspace=True,
                sandbox_restricts_workspace=True,
            )
            self.assertFalse(access.restrict_to_workspace)
        finally:
            reset_live_restrict_to_workspace(live_token)
            reset_workspace_scope(scope_token)

    def test_read_config_false_does_not_use_disk_settings(self) -> None:
        with patch(
            "navin.config.loader.load_config",
            side_effect=AssertionError("must not read config.json"),
        ):
            self.assertIsNone(live_restrict_to_workspace_setting())
            self.assertTrue(effective_restrict_to_workspace(True))

    def test_read_config_uses_saved_toggle(self) -> None:
        fake = SimpleNamespace(tools=SimpleNamespace(restrict_to_workspace=False))
        with patch("navin.config.loader.load_config", return_value=fake):
            self.assertFalse(
                live_restrict_to_workspace_setting(read_config=True),
            )
            self.assertFalse(
                effective_restrict_to_workspace(True, read_config=True),
            )


class FileAndExecUnlockTests(_RestrictCase):
    def test_read_file_reaches_another_workspace_when_settings_off(self) -> None:
        self._bind(self.restricted, live=False)
        tool = ReadFileTool(
            workspace=self.project,
            allowed_dir=self.project,
            restrict_to_workspace=True,
        )
        out = _run(tool.execute(path=str(self.other_file)))
        self.assertIn("from-other-workspace", out)
        self.assertNotIn("Error", out)

    def test_read_file_still_refuses_another_workspace_when_settings_on(self) -> None:
        self._bind(self.restricted, live=True)
        tool = ReadFileTool(
            workspace=self.project,
            allowed_dir=self.project,
            restrict_to_workspace=True,
        )
        out = _run(tool.execute(path=str(self.other_file)))
        self.assertIn("Error", out)
        self.assertNotIn("from-other-workspace", out)

    def test_exec_working_dir_in_other_workspace_when_settings_off(self) -> None:
        self._bind(self.restricted, live=False)
        tool = ExecTool(working_dir=str(self.project), restrict_to_workspace=True)
        out = _run(tool.execute(command="pwd", working_dir=str(self.other)))
        self.assertIn(str(self.other), out)
        self.assertNotIn("outside the configured workspace", out)

    def test_exec_working_dir_refused_when_settings_on(self) -> None:
        self._bind(self.restricted, live=True)
        tool = ExecTool(working_dir=str(self.project), restrict_to_workspace=True)
        out = _run(tool.execute(command="pwd", working_dir=str(self.other)))
        self.assertIn("outside the configured workspace", out)

    def test_write_file_reaches_another_workspace_when_settings_off(self) -> None:
        self._bind(self.restricted, live=False)
        target = self.other / "written.txt"
        tool = WriteFileTool(
            workspace=self.project,
            allowed_dir=self.project,
            restrict_to_workspace=True,
        )
        out = _run(tool.execute(path=str(target), content="cross-workspace-write\n"))
        self.assertNotIn("Error", out)
        self.assertEqual(target.read_text(encoding="utf-8"), "cross-workspace-write\n")

    def test_full_access_chat_reads_other_workspace_with_settings_on(self) -> None:
        self._bind(self.full, live=True)
        tool = ReadFileTool(
            workspace=self.project,
            allowed_dir=self.project,
            restrict_to_workspace=True,
        )
        out = _run(tool.execute(path=str(self.other_file)))
        self.assertIn("from-other-workspace", out)


class ExecPolicyReloadTests(unittest.TestCase):
    def test_reload_updates_loop_resolver_subagents_and_file_tools(self) -> None:
        project = Path("/tmp/navin-project")
        file_tool = SimpleNamespace(_restrict_to_workspace=True)
        exec_tool = ExecTool(working_dir=str(project), restrict_to_workspace=True)
        registry = ToolRegistry()
        registry.register(exec_tool)
        registry._tools["read_file"] = file_tool  # type: ignore[assignment]

        state = SimpleNamespace(
            approvals=None,
            tools_config=SimpleNamespace(approvals=None, restrict_to_workspace=True),
            restrict_to_workspace=True,
            workspace_scopes=WorkspaceScopeResolver(
                default_workspace=project,
                default_restrict_to_workspace=True,
            ),
            subagents=SimpleNamespace(restrict_to_workspace=True),
        )
        fake_config = SimpleNamespace(
            tools=SimpleNamespace(
                approvals=SimpleNamespace(enabled=False, exec_ask="destructive"),
                restrict_to_workspace=False,
                exec=SimpleNamespace(
                    allow_patterns=[],
                    deny_patterns=[],
                    builtin_deny_rules=True,
                ),
            ),
        )
        from navin.bus.events import (
            INBOUND_META_RUNTIME_CONTROL,
            RUNTIME_CONTROL_ACK,
            RUNTIME_CONTROL_EXEC_POLICY_RELOAD,
        )

        async def _go() -> dict:
            ack: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            msg = SimpleNamespace(
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_EXEC_POLICY_RELOAD,
                    RUNTIME_CONTROL_ACK: ack,
                },
            )
            with patch("navin.config.loader.load_config", return_value=fake_config):
                handled = await handle_exec_policy_reload(state, msg, registry)
            self.assertTrue(handled)
            return await ack

        result = asyncio.run(_go())
        self.assertTrue(result.get("ok"))
        self.assertFalse(state.restrict_to_workspace)
        self.assertFalse(state.tools_config.restrict_to_workspace)
        self.assertFalse(state.workspace_scopes.default_restrict_to_workspace)
        self.assertFalse(state.subagents.restrict_to_workspace)
        self.assertFalse(file_tool._restrict_to_workspace)
        self.assertFalse(exec_tool.restrict_to_workspace)


if __name__ == "__main__":
    unittest.main()
