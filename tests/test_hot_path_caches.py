# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-turn caches must return exactly what the uncached path returns.

Token counts are remembered by content and the config file by version so the
gateway loop stops re-encoding prompts and re-parsing config every turn. A
cache that drifts from the real answer would silently skew the context meter
or keep serving a stale license, so each cache is checked against the slow
path here.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import navin.utils.helpers as helpers
from navin.agent.prompt_profile import SECTION_SEPARATOR, profile_request
from navin.config import loader
from navin.config.loader import (
    invalidate_config_cache,
    load_config,
    load_config_cached,
    save_config,
)
from navin.utils.helpers import (
    clear_token_count_cache,
    estimate_message_tokens,
    estimate_prompt_tokens,
    estimate_text_tokens,
)


def _words(rng: random.Random, n: int) -> str:
    return " ".join(
        "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(1, 9)))
        for _ in range(n)
    )


def _request(seed: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}" if i % 5 else f"mcp_srv_{i}",
                "description": _words(rng, 60),
                "parameters": {
                    "type": "object",
                    "properties": {"arg": {"type": "string", "description": _words(rng, 15)}},
                },
            },
        }
        for i in range(20)
    ]
    system = SECTION_SEPARATOR.join(
        f"# {heading}\n\n{_words(rng, 200)}"
        for heading in ("Who You Are", "Memory", "Skills", "Tool Usage Notes", "Unknown")
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    for i in range(12):
        messages.append({"role": "user", "content": _words(rng, rng.randint(1, 80))})
        messages.append(
            {
                "role": "assistant",
                "content": _words(rng, rng.randint(0, 150)),
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "exec", "arguments": json.dumps({"cmd": _words(rng, 4)})},
                    }
                ],
                "reasoning_content": _words(rng, 20),
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": f"c{i}", "name": "exec", "content": _words(rng, 300)}
        )
    messages.append({"role": "user", "content": [{"type": "text", "text": _words(rng, 30)}, {"type": "image_url", "image_url": {"url": "x"}}]})
    return messages, tools


def _uncached_text_tokens(text: str) -> int:
    return len(helpers._get_token_encoding().encode(text)) if text else 0


class TokenCountCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_token_count_cache()
        self.addCleanup(clear_token_count_cache)

    def test_text_counts_match_tiktoken_cold_and_warm(self) -> None:
        rng = random.Random(7)
        texts = [_words(rng, n) for n in (0, 1, 5, 50, 500, 5000)] + ["héllo ✓ wörld"]
        for text in texts:
            expected = _uncached_text_tokens(text)
            self.assertEqual(estimate_text_tokens(text), expected)
            self.assertEqual(estimate_text_tokens(text), expected)  # warm
            self.assertEqual(estimate_text_tokens("".join(list(text))), expected)  # same content, new object

    def test_message_counts_match_the_uncached_formula(self) -> None:
        messages, _tools = _request(3)
        enc = helpers._get_token_encoding()
        for message in messages:
            parts: list[str] = []
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        if part.get("text"):
                            parts.append(part["text"])
                    else:
                        parts.append(json.dumps(part, ensure_ascii=False))
            for key in ("name", "tool_call_id"):
                if isinstance(message.get(key), str) and message[key]:
                    parts.append(message[key])
            if message.get("tool_calls"):
                parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
            if isinstance(message.get("reasoning_content"), str) and message["reasoning_content"]:
                parts.append(message["reasoning_content"])
            payload = "\n".join(parts)
            expected = max(4, len(enc.encode(payload)) + 4) if payload else 4
            self.assertEqual(estimate_message_tokens(message), expected)
            self.assertEqual(estimate_message_tokens(dict(message)), expected)

    def test_profile_is_identical_cold_warm_and_with_copied_inputs(self) -> None:
        for seed in range(4):
            messages, tools = _request(seed)
            clear_token_count_cache()
            cold = profile_request(messages, tools).as_dict()
            warm = profile_request(messages, tools).as_dict()
            copied = profile_request(
                [dict(m) for m in messages], [json.loads(json.dumps(t)) for t in tools]
            ).as_dict()
            self.assertEqual(cold, warm)
            self.assertEqual(cold, copied)

    def test_profile_tools_match_direct_schema_encoding(self) -> None:
        messages, tools = _request(11)
        sections = profile_request(messages, tools).sections
        builtin = sum(
            _uncached_text_tokens(json.dumps(t, ensure_ascii=False))
            for t in tools
            if not t["function"]["name"].startswith("mcp_")
        )
        mcp = sum(
            _uncached_text_tokens(json.dumps(t, ensure_ascii=False))
            for t in tools
            if t["function"]["name"].startswith("mcp_")
        )
        self.assertEqual(sections["tools"], builtin)
        self.assertEqual(sections["tools.mcp"], mcp)

    def test_prompt_estimate_is_identical_cold_and_warm(self) -> None:
        messages, tools = _request(5)
        clear_token_count_cache()
        cold = estimate_prompt_tokens(messages, tools)
        self.assertEqual(estimate_prompt_tokens(messages, tools), cold)
        self.assertEqual(estimate_prompt_tokens([dict(m) for m in messages], list(tools)), cold)

    def test_cache_stays_bounded(self) -> None:
        with patch.object(helpers, "_TEXT_TOKENS_MAX_CHARS", 10_000):
            for i in range(200):
                estimate_text_tokens(f"{i} " + "word " * 40)
            self.assertLessEqual(helpers._TEXT_TOKENS_CHARS[0], 10_000)
            self.assertEqual(
                helpers._TEXT_TOKENS_CHARS[0], sum(len(t) for t in helpers._TEXT_TOKENS)
            )


class ConfigCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "config.json"
        invalidate_config_cache()
        self.addCleanup(invalidate_config_cache)

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_matches_load_config_and_is_shared_until_the_file_changes(self) -> None:
        self._write({"agents": {"defaults": {"model": "a/one"}}})
        first = load_config_cached(self.path)
        self.assertEqual(
            first.model_dump(mode="json"), load_config(self.path).model_dump(mode="json")
        )
        self.assertIs(load_config_cached(self.path), first)

    def test_an_external_edit_is_picked_up(self) -> None:
        self._write({"agents": {"defaults": {"model": "a/one"}}})
        first = load_config_cached(self.path)
        self._write({"agents": {"defaults": {"model": "b/two"}}})
        second = load_config_cached(self.path)
        self.assertIsNot(second, first)
        self.assertEqual(second.agents.defaults.model, "b/two")

    def test_a_same_size_rewrite_is_picked_up(self) -> None:
        self._write({"agents": {"defaults": {"model": "a/one"}}})
        load_config_cached(self.path)
        size = os.stat(self.path).st_size
        # Past one kernel timestamp tick, like any human or tool edit.
        time.sleep(0.05)
        self._write({"agents": {"defaults": {"model": "a/two"}}})
        self.assertEqual(os.stat(self.path).st_size, size)
        self.assertEqual(load_config_cached(self.path).agents.defaults.model, "a/two")

    def test_save_config_invalidates(self) -> None:
        self._write({"agents": {"defaults": {"model": "a/one"}}})
        first = load_config_cached(self.path)
        edited = load_config(self.path)
        edited.agents.defaults.model = "c/three"
        save_config(edited, self.path)
        self.assertEqual(load_config_cached(self.path).agents.defaults.model, "c/three")
        self.assertEqual(first.agents.defaults.model, "a/one")

    def test_a_missing_file_gives_defaults_then_the_created_file(self) -> None:
        self.assertEqual(
            load_config_cached(self.path).model_dump(mode="json"),
            load_config(self.path).model_dump(mode="json"),
        )
        self._write({"agents": {"defaults": {"model": "d/four"}}})
        self.assertEqual(load_config_cached(self.path).agents.defaults.model, "d/four")

    def test_a_patched_loader_is_not_bypassed(self) -> None:
        self._write({})
        load_config_cached(self.path)
        sentinel = object()
        with patch.object(loader, "load_config", return_value=sentinel):
            self.assertIs(load_config_cached(self.path), sentinel)

    def test_a_cache_hit_does_not_reparse(self) -> None:
        self._write({})
        load_config_cached(self.path)
        with patch.object(loader, "decrypt_config_data", side_effect=AssertionError("re-parsed")):
            started = time.perf_counter()
            for _ in range(100):
                load_config_cached(self.path)
            self.assertLess(time.perf_counter() - started, 0.5)

    def test_the_default_path_is_followed(self) -> None:
        self._write({"agents": {"defaults": {"model": "e/five"}}})
        with patch.object(loader, "get_config_path", return_value=self.path):
            self.assertEqual(load_config_cached().agents.defaults.model, "e/five")


if __name__ == "__main__":
    unittest.main()
