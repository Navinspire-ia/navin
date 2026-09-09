# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A subagent's tool set must be narrowed by scope, never by a dropped config.

Media generation used to vanish inside subagents for two independent reasons:
the tools only declared the ``core`` scope, and the subagent config was rebuilt
from a handful of forwarded fields, so everything else silently fell back to its
schema default. That second failure also worked the other way round: a browser
disabled by the operator came back enabled for every subagent.
"""

import tempfile
import unittest
from pathlib import Path

from navin.agent.subagent import SubagentManager
from navin.agent.tools.context import ToolContext
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.registry import ToolRegistry
from navin.config.schema import ToolsConfig


def _loaded(config: ToolsConfig, scope: str) -> set[str]:
    ctx = ToolContext(config=config, workspace=str(Path.cwd()))
    return set(ToolLoader().load(ctx, ToolRegistry(), scope=scope))


def _manager(config: ToolsConfig, workspace: Path, **kwargs) -> SubagentManager:
    return SubagentManager(
        workspace=workspace,
        bus=object(),
        max_tool_result_chars=4000,
        tools_config=config,
        **kwargs,
    )


class MediaToolScopeTest(unittest.TestCase):
    def test_media_tools_reach_the_subagent_scope(self) -> None:
        config = ToolsConfig()
        config.image_generation.enabled = True
        config.video_generation.enabled = True
        self.assertLessEqual(
            {"generate_image", "generate_video"},
            _loaded(config, "subagent"),
        )

    def test_disabled_media_tools_stay_out(self) -> None:
        loaded = _loaded(ToolsConfig(), "subagent")
        self.assertNotIn("generate_image", loaded)
        self.assertNotIn("generate_video", loaded)

    def test_parent_only_tools_stay_out_of_the_subagent_scope(self) -> None:
        """Spawn, message and db_query remain deliberately parent-only."""
        loaded = _loaded(ToolsConfig(), "subagent")
        for name in ("spawn", "message", "db_query"):
            self.assertNotIn(name, loaded)


class SubagentConfigInheritanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_disabled_capabilities_are_not_revived(self) -> None:
        config = ToolsConfig()
        config.browser.enabled = False
        config.cli_apps.enable = False
        registry = _manager(config, self.workspace)._build_tools()
        self.assertIsNone(registry.get("browser"))
        self.assertIsNone(registry.get("run_cli_app"))

    def test_media_generation_is_registered_with_its_provider_configs(self) -> None:
        config = ToolsConfig()
        config.image_generation.enabled = True
        manager = _manager(
            config,
            self.workspace,
            image_generation_provider_configs={"openrouter": "provider-config"},
        )
        tool = manager._build_tools().get("generate_image")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.provider_configs, {"openrouter": "provider-config"})

    def test_restrict_to_workspace_still_overrides_the_inherited_value(self) -> None:
        config = ToolsConfig()
        config.restrict_to_workspace = False
        manager = _manager(config, self.workspace)
        manager.restrict_to_workspace = True
        self.assertTrue(manager._subagent_tools_config().restrict_to_workspace)
        self.assertFalse(config.restrict_to_workspace)


if __name__ == "__main__":
    unittest.main()
