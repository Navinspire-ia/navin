# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Prompt caching is the difference between paying full price and a tenth of it.

The whole layer had no tests, which is a poor bargain for something that is
invisible when it breaks: a misplaced breakpoint costs money silently, and an
ineligible one costs the entire request. These lock in the two limits Anthropic
enforces - at most four breakpoints, and none on a block that cannot carry one -
and the placement that makes the cache actually get read.
"""

import unittest

from navin.providers.anthropic_provider import AnthropicProvider

# Anthropic rejects a fifth breakpoint outright.
MAX_BREAKPOINTS = 4


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": "does a thing",
        "input_schema": {"type": "object", "properties": {}},
    }


def _count(system, messages, tools) -> int:
    """Every cache_control marker the request would carry."""
    total = 0
    if isinstance(system, list):
        total += sum(1 for block in system if "cache_control" in block)
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            total += sum(
                1 for block in content
                if isinstance(block, dict) and "cache_control" in block
            )
    for tool in tools or ():
        if "cache_control" in tool:
            total += 1
    return total


def _apply(messages, tools=None, system="You are navin."):
    return AnthropicProvider._apply_cache_control(system, messages, tools)


def _conversation(turns: int = 2) -> list[dict]:
    """A request as navin actually sends one: it ends on the incoming user turn.

    That trailing message is the one carrying this turn's runtime context, so the
    shape matters for every placement assertion below.
    """
    messages: list[dict] = []
    for index in range(turns):
        messages.append({"role": "user", "content": f"question {index}"})
        messages.append({"role": "assistant", "content": f"answer {index}"})
    messages.append({"role": "user", "content": "current question + git state + board"})
    return messages


def _marked_blocks(message: dict) -> list[dict]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and "cache_control" in b]


class BreakpointBudgetTest(unittest.TestCase):
    """Four is a hard ceiling, and navin already sits on it."""

    def test_a_bare_conversation_stays_within_budget(self) -> None:
        system, messages, tools = _apply(_conversation())
        self.assertLessEqual(_count(system, messages, tools), MAX_BREAKPOINTS)

    def test_builtin_tools_alone_stay_within_budget(self) -> None:
        offered = [_tool(f"t{i}") for i in range(20)]
        system, messages, tools = _apply(_conversation(), offered)
        self.assertLessEqual(_count(system, messages, tools), MAX_BREAKPOINTS)

    def test_mcp_tools_fill_the_budget_exactly(self) -> None:
        """The builtin/MCP boundary earns a second tool breakpoint.

        This is the configuration that leaves no slack: anything that adds a
        fifth breakpoint later turns every request into a 400.
        """
        offered = [_tool("read_file"), _tool("exec"), _tool("mcp_srv_a"), _tool("mcp_srv_b")]
        system, messages, tools = _apply(_conversation(), offered)
        self.assertEqual(_count(system, messages, tools), MAX_BREAKPOINTS)

    def test_a_long_conversation_with_mcp_stays_within_budget(self) -> None:
        offered = [_tool("exec"), *(_tool(f"mcp_srv_{i}") for i in range(30))]
        system, messages, tools = _apply(_conversation(turns=40), offered)
        self.assertLessEqual(_count(system, messages, tools), MAX_BREAKPOINTS)


class SystemBreakpointTest(unittest.TestCase):
    def test_the_system_prompt_is_cached_on_its_own(self) -> None:
        """A separate breakpoint is what lets the system cache outlive a turn."""
        system, _, _ = _apply(_conversation())
        self.assertIsInstance(system, list)
        self.assertEqual(len(system), 1)
        self.assertIn("cache_control", system[0])
        self.assertEqual(system[0]["text"], "You are navin.")

    def test_an_empty_system_prompt_earns_no_breakpoint(self) -> None:
        system, _, _ = _apply(_conversation(), system="")
        self.assertEqual(_count(system, [], None), 0)

    def test_a_block_list_system_prompt_is_marked_at_the_end(self) -> None:
        blocks = [{"type": "text", "text": "rules"}, {"type": "text", "text": "context"}]
        system, _, _ = _apply(_conversation(), system=blocks)
        self.assertNotIn("cache_control", system[0])
        self.assertIn("cache_control", system[1])


class MessagePlacementTest(unittest.TestCase):
    """The breakpoint must sit behind the content that changes every turn."""

    def test_the_breakpoint_lands_on_the_second_to_last_message(self) -> None:
        """The last user message carries the per-turn runtime context.

        navin appends git state, the board digest and file mentions to it, so a
        breakpoint there would write a fresh entry every turn and never read one.
        Marking the message before it keeps the growing prefix cacheable.
        """
        _, messages, _ = _apply(_conversation())
        self.assertTrue(_marked_blocks(messages[-2]))
        self.assertFalse(_marked_blocks(messages[-1]))

    def test_a_short_conversation_earns_no_message_breakpoint(self) -> None:
        """Below three messages there is no stable prefix worth an entry."""
        messages = [{"role": "user", "content": "hi"}]
        _, out, _ = _apply(messages)
        self.assertEqual(sum(len(_marked_blocks(m)) for m in out), 0)

    def test_a_string_content_message_becomes_a_marked_text_block(self) -> None:
        _, messages, _ = _apply(_conversation())
        blocks = messages[-2]["content"]
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[0]["text"], "answer 1")
        self.assertIn("cache_control", blocks[0])

    def test_the_turn_carrying_runtime_context_is_never_marked(self) -> None:
        """The regression that would silently cost money on every single turn."""
        _, messages, _ = _apply(_conversation(turns=8))
        self.assertEqual(messages[-1]["role"], "user")
        self.assertNotIn("cache_control", str(messages[-1]["content"]))

    def test_exactly_one_message_breakpoint_is_ever_placed(self) -> None:
        _, messages, _ = _apply(_conversation(turns=25))
        self.assertEqual(sum(len(_marked_blocks(m)) for m in messages), 1)


class BlockEligibilityTest(unittest.TestCase):
    """A breakpoint on an ineligible block fails the request, not just the cache."""

    def test_a_thinking_only_turn_is_never_marked(self) -> None:
        """A turn that thought and then produced nothing ends on thinking.

        Anthropic refuses cache_control there, so the old tail-marking rule
        turned this rare turn into a 400 for the whole request.
        """
        messages = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "hmm", "signature": "s"}],
            },
            {"role": "user", "content": "q2"},
        ]
        _, out, _ = _apply(messages)
        for block in out[-2]["content"]:
            self.assertNotIn("cache_control", block)

    def test_a_thinking_block_before_text_does_not_move_the_breakpoint(self) -> None:
        messages = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm", "signature": "s"},
                    {"type": "text", "text": "answer"},
                ],
            },
            {"role": "user", "content": "q2"},
        ]
        _, out, _ = _apply(messages)
        blocks = out[-2]["content"]
        self.assertNotIn("cache_control", blocks[0])
        self.assertIn("cache_control", blocks[1])

    def test_the_breakpoint_walks_back_past_a_trailing_thinking_block(self) -> None:
        """Interleaved thinking can land after the text it followed."""
        messages = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "partial"},
                    {"type": "thinking", "thinking": "more", "signature": "s"},
                ],
            },
            {"role": "user", "content": "q2"},
        ]
        _, out, _ = _apply(messages)
        blocks = out[-2]["content"]
        self.assertIn("cache_control", blocks[0])
        self.assertNotIn("cache_control", blocks[1])

    def test_a_redacted_thinking_block_is_refused_too(self) -> None:
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": [{"type": "redacted_thinking", "data": "x"}]},
            {"role": "user", "content": "q2"},
        ]
        _, out, _ = _apply(messages)
        self.assertFalse(_marked_blocks(out[-2]))

    def test_an_empty_text_block_is_skipped(self) -> None:
        """Marking a block the API discards throws the breakpoint away."""
        messages = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "real"}, {"type": "text", "text": ""}],
            },
            {"role": "user", "content": "q2"},
        ]
        _, out, _ = _apply(messages)
        blocks = out[-2]["content"]
        self.assertIn("cache_control", blocks[0])
        self.assertNotIn("cache_control", blocks[1])

    def test_a_tool_use_block_is_eligible(self) -> None:
        """The common in-loop shape: the marked turn ends on a tool call."""
        messages = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "exec", "input": {}}],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        ]
        _, out, _ = _apply(messages)
        self.assertTrue(_marked_blocks(out[-2]))

    def test_an_empty_content_list_earns_no_breakpoint(self) -> None:
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": []},
            {"role": "user", "content": "q2"},
        ]
        _, out, _ = _apply(messages)
        self.assertFalse(_marked_blocks(out[-2]))


class PurityTest(unittest.TestCase):
    """Marking must not mutate the caller's history."""

    def test_the_input_messages_are_left_untouched(self) -> None:
        messages = _conversation()
        original = [dict(m) for m in messages]
        _apply(messages)
        self.assertEqual(messages, original)

    def test_the_input_tools_are_left_untouched(self) -> None:
        offered = [_tool("exec"), _tool("mcp_srv_a")]
        _apply(_conversation(), offered)
        for tool in offered:
            self.assertNotIn("cache_control", tool)


if __name__ == "__main__":
    unittest.main()
