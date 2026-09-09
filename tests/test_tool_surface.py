# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Composer-mode tool schema filtering."""

from __future__ import annotations

import unittest

from navin.agent.tool_surface import (
    CODE_BUILD_ALLOWED_TOOLS,
    PLAN_SAFE_WRITE_TOOLS,
    FilteredToolDefinitions,
    compact_tool_schema,
    denied_tools_for_composer_mode,
    filter_tool_definitions,
    tool_name_is_allowed,
)


class ToolSurfaceTest(unittest.TestCase):
    def test_agent_mode_denies_nothing(self) -> None:
        self.assertEqual(denied_tools_for_composer_mode("agent"), frozenset())
        self.assertEqual(denied_tools_for_composer_mode(None), frozenset())

    def test_review_denies_heavy_media(self) -> None:
        denied = denied_tools_for_composer_mode("review")
        self.assertIn("scrape", denied)
        self.assertIn("montage", denied)
        self.assertIn("mobile", denied)

    def test_plan_denies_every_mutation_channel(self) -> None:
        """Plan is design-only: edits, shell, file management and scheduling
        are all out of the schema, not just write_file."""
        denied = denied_tools_for_composer_mode("plan")
        for name in (
            "apply_patch",
            "write_file",
            "edit_file",
            "exec",
            "write_stdin",
            "manage_files",
            "cron",
            "start_app",
        ):
            self.assertIn(name, denied, name)

    def test_plan_keeps_spawn_for_its_read_action(self) -> None:
        """spawn multiplexes a read and a write behind one name, like git.

        Denying the whole tool also denied spawn(action="results"), a pure
        report on subagents that already finished - the research a planning
        turn has to fold in. The schema keeps it; the runner refuses
        action="start" per call (SpawnTool.call_read_only)."""
        self.assertNotIn("spawn", denied_tools_for_composer_mode("plan"))

    def test_spawn_start_is_not_read_only_but_results_is(self) -> None:
        from navin.agent.tools.spawn import SpawnTool

        tool = SpawnTool(manager=object())
        self.assertTrue(tool.call_read_only({"action": "results"}))
        self.assertFalse(tool.call_read_only({"action": "start", "task": "edit foo"}))
        # 'start' is the documented default, so an omitted action must not read
        # as harmless.
        self.assertFalse(tool.call_read_only({"task": "edit foo"}))
        self.assertFalse(tool.call_read_only(None))

    def test_plan_keeps_the_read_and_planning_surfaces(self) -> None:
        """git/board/code_index stay in the schema; the runner judges their
        calls per action so status/list still work while commit refuses."""
        denied = denied_tools_for_composer_mode("plan")
        for name in ("git", "board", "code_index", "read_file", "grep", "lsp"):
            self.assertNotIn(name, denied, name)

    def test_plan_safe_write_tools_are_the_planning_surfaces(self) -> None:
        self.assertEqual(
            PLAN_SAFE_WRITE_TOOLS,
            frozenset({"board", "ask_user", "set_composer_mode"}),
        )

    def test_filtered_definitions_accept_a_live_callable(self) -> None:
        """A mid-turn mode switch must reach the next definitions read."""

        class _Registry:
            def get_definitions(self) -> list[dict]:
                return [{"name": "read_file"}, {"name": "exec"}]

        denied: set[str] = {"exec"}
        filtered = FilteredToolDefinitions(_Registry(), lambda: frozenset(denied))
        self.assertEqual(
            [d["name"] for d in filtered.get_definitions()], ["read_file"]
        )
        denied.clear()
        self.assertEqual(
            [d["name"] for d in filtered.get_definitions()],
            ["read_file", "exec"],
        )

    def test_filter_drops_denied_schemas(self) -> None:
        defs = [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "scrape"}},
            {"name": "grep"},
        ]
        kept = filter_tool_definitions(defs, frozenset({"scrape"}))
        names = []
        for d in kept:
            fn = d.get("function") if isinstance(d, dict) else None
            if isinstance(fn, dict):
                names.append(fn.get("name"))
            else:
                names.append(d.get("name"))
        self.assertEqual(names, ["read_file", "grep"])

    def test_filter_keeps_only_the_allowlist(self) -> None:
        defs = [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "tenders"}},
            {"name": "mcp_debug_foo"},
            {"name": "generate_image"},
        ]
        kept = filter_tool_definitions(
            defs, allowed=CODE_BUILD_ALLOWED_TOOLS
        )
        names = []
        for item in kept:
            fn = item.get("function") if isinstance(item, dict) else None
            if isinstance(fn, dict):
                names.append(fn.get("name"))
            else:
                names.append(item.get("name"))
        self.assertEqual(names, ["read_file", "generate_image"])

    def test_mcp_is_not_on_the_code_build_allowlist(self) -> None:
        self.assertFalse(tool_name_is_allowed("mcp_github_list", CODE_BUILD_ALLOWED_TOOLS))
        self.assertTrue(tool_name_is_allowed("verify", CODE_BUILD_ALLOWED_TOOLS))
        self.assertTrue(tool_name_is_allowed("anything", None))

    def test_compact_shortens_long_descriptions(self) -> None:
        schema = {
            "type": "function",
            "function": {
                "name": "board",
                "description": "word " * 200,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "another " * 80,
                        }
                    },
                },
            },
        }
        compact = compact_tool_schema(schema)
        desc = compact["function"]["description"]
        self.assertLess(len(desc), len(schema["function"]["description"]))
        self.assertTrue(desc.endswith("..."))
        action_desc = compact["function"]["parameters"]["properties"]["action"][
            "description"
        ]
        self.assertLess(
            len(action_desc),
            len(schema["function"]["parameters"]["properties"]["action"]["description"]),
        )


if __name__ == "__main__":
    unittest.main()
