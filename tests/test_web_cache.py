# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""web_search / web_fetch must not re-hit the network for identical calls."""

from __future__ import annotations

import unittest

from navin.agent.tools import web_cache
from navin.agent.tools.base import ToolResult


class WebCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        web_cache.clear()
        self.addCleanup(web_cache.clear)

    def test_a_successful_value_is_returned_within_the_ttl(self) -> None:
        key = {"provider": "brave", "query": "navin", "n": 5}
        web_cache.put("search", key, "hit body")
        self.assertEqual(web_cache.get("search", key), "hit body")

    def test_errors_are_never_stored(self) -> None:
        key = {"url": "https://example.com"}
        web_cache.put("fetch", key, ToolResult.error('{"error": "boom"}'))
        self.assertIsNone(web_cache.get("fetch", key))
        web_cache.put("fetch", key, "Error: network down")
        self.assertIsNone(web_cache.get("fetch", key))

    def test_different_arguments_miss(self) -> None:
        web_cache.put("search", {"query": "a", "n": 5}, "A")
        self.assertIsNone(web_cache.get("search", {"query": "b", "n": 5}))

    def test_clear_empties_the_cache(self) -> None:
        web_cache.put("search", {"query": "x"}, "X")
        web_cache.clear()
        self.assertIsNone(web_cache.get("search", {"query": "x"}))


if __name__ == "__main__":
    unittest.main()
