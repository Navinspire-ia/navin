"""One MCP server must not be able to crowd out the rest of the tool catalogue.

navin registered every capability a server declared, so a large connector
silently pushed the catalogue past the size where a model still picks the right
tool. These cover the budget that stops it, and the notice that tells the
operator what to do about it, because a cap that trims in silence trades one
invisible failure for another.
"""

import unittest

from navin.agent.tools.mcp import _BUDGET_NOTICES, _register_within_budget
from navin.agent.tools.mcp_budget import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TOOLS,
    ToolCandidate,
    apply_budget,
)
from navin.agent.tools.registry import ToolRegistry


def _candidates(count: int, *, description: str = "does a thing") -> list[ToolCandidate]:
    return [
        ToolCandidate(
            name=f"mcp_srv_tool_{index:03d}",
            description=description,
            schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        for index in range(count)
    ]


class BudgetTest(unittest.TestCase):
    def test_a_small_server_passes_untouched(self) -> None:
        verdict = apply_budget("srv", _candidates(5))
        self.assertEqual(len(verdict.accepted), 5)
        self.assertFalse(verdict.overflowed)
        self.assertEqual(verdict.notice, "")

    def test_a_flood_of_tools_is_capped(self) -> None:
        verdict = apply_budget("srv", _candidates(200))
        self.assertEqual(len(verdict.accepted), DEFAULT_MAX_TOOLS)
        self.assertEqual(len(verdict.dropped), 200 - DEFAULT_MAX_TOOLS)
        self.assertTrue(verdict.overflowed)

    def test_the_accepted_set_is_a_stable_prefix(self) -> None:
        """Prompt caching only survives a reconnect if the prefix is stable."""
        offered = _candidates(200)
        first = apply_budget("srv", offered)
        second = apply_budget("srv", list(offered))
        self.assertEqual(
            [c.name for c in first.accepted],
            [c.name for c in second.accepted],
        )
        self.assertEqual(
            [c.name for c in first.accepted],
            [c.name for c in offered[:DEFAULT_MAX_TOOLS]],
        )

    def test_a_few_enormous_tools_hit_the_token_cap_first(self) -> None:
        """Ten tools is fine; ten tools of prose is not."""
        bloated = _candidates(10, description="lorem ipsum dolor sit amet " * 400)
        verdict = apply_budget("srv", bloated)
        self.assertLess(len(verdict.accepted), 10)
        self.assertLessEqual(verdict.tokens, DEFAULT_MAX_TOKENS)
        self.assertIn("tokens of definitions", verdict.notice)

    def test_nothing_slips_in_behind_a_refusal(self) -> None:
        """A tiny tool after the cap must not jump the queue."""
        offered = _candidates(3, description="x" * 60_000)
        offered.append(ToolCandidate(name="mcp_srv_tiny", description="x", schema=None))
        verdict = apply_budget("srv", offered)
        self.assertNotIn("mcp_srv_tiny", [c.name for c in verdict.accepted])

    def test_an_operator_can_raise_the_cap(self) -> None:
        verdict = apply_budget("srv", _candidates(60), max_tools=100)
        self.assertEqual(len(verdict.accepted), 60)
        self.assertFalse(verdict.overflowed)

    def test_an_operator_can_remove_the_cap(self) -> None:
        verdict = apply_budget("srv", _candidates(500), max_tools=-1, max_tokens=-1)
        self.assertEqual(len(verdict.accepted), 500)
        self.assertFalse(verdict.overflowed)

    def test_an_empty_catalogue_is_not_an_overflow(self) -> None:
        verdict = apply_budget("srv", [])
        self.assertEqual(verdict.accepted, ())
        self.assertFalse(verdict.overflowed)
        self.assertEqual(verdict.tokens, 0)

    def test_an_unserialisable_schema_still_gets_a_price(self) -> None:
        """A schema navin cannot render must not crash the budget."""
        candidate = ToolCandidate(name="t", description="d", schema=object())
        self.assertGreater(candidate.tokens, 0)


class NoticeTest(unittest.TestCase):
    def test_the_notice_names_the_server_and_the_remedy(self) -> None:
        notice = apply_budget("github", _candidates(200)).notice
        self.assertIn("github", notice)
        self.assertIn("200", notice)
        self.assertIn("enabledTools", notice)

    def test_the_notice_says_how_many_were_kept_and_skipped(self) -> None:
        notice = apply_budget("srv", _candidates(50)).notice
        self.assertIn(str(DEFAULT_MAX_TOOLS), notice)
        self.assertIn(str(50 - DEFAULT_MAX_TOOLS), notice)


class _FakeTool:
    """A registrable stand-in: the registry only reads these three fields."""

    _plugin_discoverable = False

    def __init__(self, name: str, description: str = "d") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}


class _Cfg:
    def __init__(self, max_tools: int = 0, max_tool_tokens: int = 0) -> None:
        self.max_tools = max_tools
        self.max_tool_tokens = max_tool_tokens


class RegistrationTest(unittest.TestCase):
    def setUp(self) -> None:
        _BUDGET_NOTICES.clear()
        self.addCleanup(_BUDGET_NOTICES.clear)

    def _register(self, count: int, cfg: _Cfg | None = None) -> tuple[int, ToolRegistry]:
        registry = ToolRegistry()
        offered = [_FakeTool(f"mcp_srv_t{i:03d}") for i in range(count)]
        kept = _register_within_budget("srv", cfg or _Cfg(), offered, registry)
        return kept, registry

    def test_only_the_budgeted_tools_reach_the_registry(self) -> None:
        kept, registry = self._register(200)
        self.assertEqual(kept, DEFAULT_MAX_TOOLS)
        self.assertEqual(len(registry.tool_names), DEFAULT_MAX_TOOLS)

    def test_a_modest_server_registers_in_full(self) -> None:
        kept, registry = self._register(12)
        self.assertEqual(kept, 12)
        self.assertEqual(len(registry.tool_names), 12)

    def test_an_overflow_leaves_a_notice_for_the_operator(self) -> None:
        self._register(200)
        self.assertIn("srv", _BUDGET_NOTICES)
        self.assertIn("enabledTools", _BUDGET_NOTICES["srv"])

    def test_a_reconnect_within_budget_clears_the_stale_notice(self) -> None:
        self._register(200)
        self._register(5)
        self.assertNotIn("srv", _BUDGET_NOTICES)


class _ToolDef:
    def __init__(self, name: str, description: str, schema: dict) -> None:
        self.name = name
        self.description = description
        self.inputSchema = schema


class DescriptionCapTest(unittest.TestCase):
    """A server's prose is paid on every model call; the contract is in the first 2 KB."""

    def _wrap(self, description: str, schema: dict | None = None):
        from navin.agent.tools.mcp import MCPToolWrapper

        return MCPToolWrapper(
            session=None,
            server_name="srv",
            tool_def=_ToolDef("search", description, schema or {"type": "object", "properties": {}}),
        )

    def test_a_short_description_is_kept_whole(self) -> None:
        tool = self._wrap("Search the docs.")
        self.assertIn("Search the docs.", tool.description)
        self.assertNotIn("(truncated)", tool.description)

    def test_an_essay_is_cut_at_the_cap(self) -> None:
        from navin.agent.tools.mcp import MCP_DESCRIPTION_MAX_CHARS

        tool = self._wrap("contract first. " + "filler " * 2000)
        self.assertLessEqual(len(tool.description), MCP_DESCRIPTION_MAX_CHARS)
        self.assertTrue(tool.description.startswith("contract first."))
        self.assertTrue(tool.description.endswith("(truncated)"))

    def test_parameter_descriptions_are_capped_too(self) -> None:
        from navin.agent.tools.mcp import MCP_PARAM_DESCRIPTION_MAX_CHARS

        schema = {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "x" * 5000},
                "opts": {
                    "type": "object",
                    "properties": {"deep": {"type": "string", "description": "y" * 5000}},
                },
            },
            "required": ["q"],
        }
        tool = self._wrap("ok", schema)
        props = tool.parameters["properties"]
        self.assertLessEqual(len(props["q"]["description"]), MCP_PARAM_DESCRIPTION_MAX_CHARS)
        self.assertLessEqual(
            len(props["opts"]["properties"]["deep"]["description"]), MCP_PARAM_DESCRIPTION_MAX_CHARS,
        )
        self.assertEqual(tool.parameters["required"], ["q"])


if __name__ == "__main__":
    unittest.main()
