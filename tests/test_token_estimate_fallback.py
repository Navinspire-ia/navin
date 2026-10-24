"""Regression tests: token estimation must survive a broken tiktoken install.

The PyInstaller bundle can ship without tiktoken's encoding plugins
(tiktoken_ext.openai_public), which makes get_encoding() raise. Budgeting
callers (auto-compact, context status) need a number, not 0.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from navin.utils.helpers import (
    estimate_message_tokens,
    estimate_prompt_tokens,
    estimate_prompt_tokens_chain,
)

_MESSAGES = [
    {"role": "user", "content": "hello world, this is a probe message"},
    {"role": "assistant", "content": "a reply with some content to estimate"},
]

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
]


def _broken_encoding():
    def _raise(name: str):
        raise ValueError(
            f"Unknown encoding {name}.\nPlugins found: []"
        )

    return _raise


class TiktokenUnavailableFallbackTest(unittest.TestCase):
    """estimate_prompt_tokens falls back instead of returning 0."""

    def setUp(self) -> None:
        import navin.utils.helpers as helpers

        helpers._get_token_encoding.cache_clear()
        patcher = patch.object(
            helpers.tiktoken, "get_encoding", side_effect=_broken_encoding()
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(helpers._get_token_encoding.cache_clear)

    def test_a_broken_encoding_still_yields_a_positive_estimate(self) -> None:
        estimated = estimate_prompt_tokens(_MESSAGES)
        self.assertGreater(estimated, 0)

    def test_the_fallback_matches_the_message_estimator_convention(self) -> None:
        estimated = estimate_prompt_tokens(_MESSAGES)
        expected = (
            sum(estimate_message_tokens(msg) for msg in _MESSAGES)
            + len(_MESSAGES) * 4
        )
        self.assertEqual(estimated, expected)

    def test_the_fallback_counts_tools_too(self) -> None:
        with_tools = estimate_prompt_tokens(_MESSAGES, _TOOLS)
        without = estimate_prompt_tokens(_MESSAGES)
        self.assertGreater(with_tools, without)
        self.assertGreaterEqual(with_tools - without, 8)

    def test_the_chain_returns_a_usable_estimate_without_a_provider_counter(self) -> None:
        tokens, source = estimate_prompt_tokens_chain(None, "test-model", _MESSAGES, _TOOLS)
        self.assertGreater(tokens, 0)
        self.assertEqual(source, "tiktoken")

    def test_a_provider_counter_still_wins_over_the_fallback(self) -> None:
        class _Provider:
            @staticmethod
            def estimate_prompt_tokens(messages, tools, model):
                return 42, "provider_counter"

        tokens, source = estimate_prompt_tokens_chain(
            _Provider(), "test-model", _MESSAGES, _TOOLS
        )
        self.assertEqual((tokens, source), (42, "provider_counter"))


def _tiktoken_usable() -> bool:
    try:
        import tiktoken

        tiktoken.get_encoding("cl100k_base")
        return True
    except Exception:
        return False


@unittest.skipUnless(
    _tiktoken_usable(),
    "this runtime ships tiktoken without its encoding plugins (known 2.0.4 bundle defect)",
)
class HealthyTiktokenPathTest(unittest.TestCase):
    """The fallback must not hijack the path when tiktoken works."""

    def test_a_working_encoding_is_used_not_the_fallback(self) -> None:
        import tiktoken

        messages = [
            {"role": "user", "content": "hello world, this is a probe message"},
        ]
        estimated = estimate_prompt_tokens(messages)
        enc = tiktoken.get_encoding("cl100k_base")
        expected = len(enc.encode(messages[0]["content"])) + 4
        self.assertEqual(estimated, expected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def test_long_parts_are_encoded_once_across_steps(monkeypatch):
    """The history is re-estimated every step: only new long texts get encoded."""
    from navin.utils import helpers

    enc = helpers._get_token_encoding()
    encoded: list[int] = []

    class Counting:
        def encode(self, text, *args, **kwargs):
            encoded.append(len(text))
            return enc.encode(text, *args, **kwargs)

    counting = Counting()
    monkeypatch.setattr(helpers, "_get_token_encoding", lambda: counting)
    long_text = "result line\n" * 400
    history = [
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "exec", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": long_text},
    ]
    first = helpers.estimate_prompt_tokens(history)
    encoded.clear()
    # Next step: the same messages, shallow-copied, plus a new short one.
    again = helpers.estimate_prompt_tokens([dict(m) for m in history] + [{"role": "user", "content": "next"}])
    assert again > first
    assert all(n < len(long_text) for n in encoded)  # the long result was not encoded again
    # Close to the exact joined count.
    exact = len(enc.encode("\n".join(["build it", helpers._tool_calls_text(history[1]["tool_calls"]), "c1", long_text]))) + 3 * 4
    assert abs(first - exact) <= 3
