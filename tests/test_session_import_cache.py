# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An import must not leave the cached session list stale.

The sidebar refreshes right after an import; a cached ``/api/sessions``
answer (stale-while-revalidate) used to hide the new sessions until a manual
reload.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from navin.webui.route_cache import CoalescingCache
from navin.webui.ws_http import GatewayHTTPHandler


class _FakeHandler:
    _dispatch_misc_routes = GatewayHTTPHandler._dispatch_misc_routes

    def __init__(self) -> None:
        self.route_cache = CoalescingCache()
        self.calls: list[str] = []

    async def _handle_webui_session_import(self, connection: Any, request: Any, action: str) -> str:
        self.calls.append(f"import:{action}")
        return "imported"

    async def _handle_webui_session_bundle(self, connection: Any, request: Any, action: str) -> str:
        self.calls.append(f"bundle:{action}")
        return "bundle"


async def _cached(handler: _FakeHandler) -> None:
    async def stale() -> dict[str, list[str]]:
        return {"sessions": ["old"]}

    await handler.route_cache.get("webui:sessions", stale, ttl=60)


class SessionImportCacheTest(unittest.TestCase):
    def _run(self, path: str) -> tuple[_FakeHandler, Any]:
        handler = _FakeHandler()

        async def scenario() -> Any:
            await _cached(handler)
            return await handler._dispatch_misc_routes(None, SimpleNamespace(path=path), path)

        return handler, asyncio.run(scenario())

    def _assert_list_dropped(self, handler: _FakeHandler) -> None:
        async def fresh() -> dict[str, list[str]]:
            return {"sessions": ["old", "new"]}

        payload = asyncio.run(handler.route_cache.get("webui:sessions", fresh, ttl=60))
        self.assertEqual(payload, {"sessions": ["old", "new"]})

    def test_import_drops_cached_list(self) -> None:
        handler, response = self._run("/api/webui/sessions/import")
        self.assertEqual(response, "imported")
        self.assertEqual(handler.calls, ["import:import"])
        self._assert_list_dropped(handler)

    def test_bundle_open_and_upload_drop_cached_list(self) -> None:
        for action in ("open", "upload"):
            handler, response = self._run(f"/api/webui/sessions/bundle/{action}")
            self.assertEqual(response, "bundle")
            self.assertEqual(handler.calls, [f"bundle:{action}"])
            self._assert_list_dropped(handler)


if __name__ == "__main__":
    unittest.main()
