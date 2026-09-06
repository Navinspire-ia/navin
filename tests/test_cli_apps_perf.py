"""Perf regression tests: CLI Apps payload must not block the event loop.

Context: /api/settings/cli-apps built its payload with one shutil.which()
per catalog app directly on the asyncio loop. On WSL2 (Windows PATH mounted
under /mnt/c) this froze the whole gateway for 5-20s per request.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

from navin.apps.cli import service as cli_service
from navin.webui import cli_apps_api


class WhichCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        cli_service._invalidate_which_cache()

    def tearDown(self) -> None:
        cli_service._invalidate_which_cache()

    def test_which_results_are_cached(self) -> None:
        with mock.patch.object(
            cli_service.shutil, "which", return_value="/usr/bin/fake"
        ) as which:
            self.assertTrue(cli_service._which_available("fake-tool"))
            self.assertTrue(cli_service._which_available("fake-tool"))
            self.assertTrue(cli_service._which_available("fake-tool"))
        self.assertEqual(which.call_count, 1)

    def test_invalidate_single_entry(self) -> None:
        with mock.patch.object(
            cli_service.shutil, "which", return_value=None
        ) as which:
            self.assertFalse(cli_service._which_available("gone-tool"))
            cli_service._invalidate_which_cache("gone-tool")
            self.assertFalse(cli_service._which_available("gone-tool"))
        self.assertEqual(which.call_count, 2)

    def test_cache_expires_after_ttl(self) -> None:
        with mock.patch.object(
            cli_service.shutil, "which", return_value=None
        ) as which:
            cli_service._which_available("ttl-tool")
            stamp, value = cli_service._which_cache["ttl-tool"]
            cli_service._which_cache["ttl-tool"] = (
                stamp - cli_service._WHICH_CACHE_TTL_SECONDS - 1,
                value,
            )
            cli_service._which_available("ttl-tool")
        self.assertEqual(which.call_count, 2)


class _SlowManager:
    """Fake manager whose payload building sleeps synchronously."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def payload(self, **_: object) -> dict:
        time.sleep(self.delay)
        return {"apps": [{"name": "demo"}], "installed_count": 0, "catalog_updated_at": None}

    def installed_payload(self) -> dict:
        time.sleep(self.delay)
        return {"apps": [], "installed_count": 0, "catalog_updated_at": None}

    def catalog_cache_fresh(self, **_: object) -> bool:
        return True


class PayloadOffloadTest(unittest.TestCase):
    def test_event_loop_stays_responsive_during_payload(self) -> None:
        """A concurrent coroutine must run while payload builds in a thread."""

        async def scenario() -> float:
            ticks = 0

            async def ticker() -> None:
                nonlocal ticks
                for _ in range(20):
                    ticks += 1
                    await asyncio.sleep(0.01)

            with mock.patch.object(
                cli_apps_api, "_manager", return_value=_SlowManager(0.3)
            ):
                tick_task = asyncio.create_task(ticker())
                payload = await cli_apps_api.cli_apps_payload()
                self.assertEqual(payload["apps"][0]["name"], "demo")
                await tick_task
            return ticks

        ticks = asyncio.run(scenario())
        # If payload blocked the loop, the ticker could not have advanced
        # during the 0.3s sleep; require meaningful concurrent progress.
        self.assertGreaterEqual(ticks, 20)

    def test_installed_only_payload_shape(self) -> None:
        async def scenario() -> dict:
            with mock.patch.object(
                cli_apps_api, "_manager", return_value=_SlowManager(0.0)
            ):
                return await cli_apps_api.cli_apps_payload(installed_only=True)

        payload = asyncio.run(scenario())
        self.assertIn("apps", payload)
        self.assertNotIn("catalog_refresh_pending", payload)


if __name__ == "__main__":
    unittest.main()
