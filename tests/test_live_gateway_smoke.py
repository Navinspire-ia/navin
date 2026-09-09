# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live happy-path smoke against a running gateway (skips when offline).

Run manually or in a deploy check::

    .venv/bin/python -m pytest tests/test_live_gateway_smoke.py -q

Asserts the production surface Cursor-style users hit first:
health, SPA shell, and webui bootstrap - all with tight latency budgets
(regression guard for the event-loop-blocking bug fixed in cli_apps_api).
"""

from __future__ import annotations

import time
import unittest
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8766"
_LATENCY_BUDGET_SECONDS = 3.0


def _get(path: str, timeout: float = 8.0) -> tuple[int, bytes, float]:
    start = time.monotonic()
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as response:
            body = response.read()
            return response.status, body, time.monotonic() - start
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.monotonic() - start


def _get_with_retry(
    path: str,
    *,
    attempts: int = 3,
    retry_delay: float = 2.0,
) -> tuple[int, bytes, float]:
    """GET with short retries on 404.

    A live gateway can transiently return 404 for static assets while
    `npm run build` rewrites navin/web/dist; retrying keeps this smoke
    test focused on real regressions instead of rebuild races.
    """
    status, body, elapsed = _get(path)
    for _ in range(attempts - 1):
        if status != 404:
            break
        time.sleep(retry_delay)
        status, body, elapsed = _get(path)
    return status, body, elapsed


def _gateway_running() -> bool:
    try:
        health_status, health_body, _ = _get("/health", timeout=2.0)
        shell_status, shell_body, _ = _get("/", timeout=2.0)
    except Exception:
        return False
    return (
        health_status == 200
        and b'"ok"' in health_body
        and shell_status == 200
        and b'<div id="root">' in shell_body
    )


@unittest.skipUnless(_gateway_running(), "gateway not running on 127.0.0.1:8766")
class LiveGatewaySmokeTest(unittest.TestCase):
    def test_health_fast(self) -> None:
        status, body, elapsed = _get("/health")
        self.assertEqual(status, 200)
        self.assertIn(b'"ok"', body)
        self.assertLess(elapsed, _LATENCY_BUDGET_SECONDS)

    def test_spa_shell_served(self) -> None:
        status, body, elapsed = _get_with_retry("/")
        self.assertEqual(status, 200)
        self.assertIn(b'<div id="root">', body)
        self.assertIn(b"<title>Navin</title>", body)
        self.assertLess(elapsed, _LATENCY_BUDGET_SECONDS)

    def test_webui_bootstrap(self) -> None:
        status, body, elapsed = _get("/webui/bootstrap")
        self.assertEqual(status, 200)
        self.assertIn(b'"token"', body)
        self.assertIn(b'"ws_url"', body)
        self.assertLess(elapsed, _LATENCY_BUDGET_SECONDS)

    def test_health_survives_burst(self) -> None:
        """Health must stay fast under a short burst (event loop free)."""
        for _ in range(5):
            status, _, elapsed = _get("/health")
            self.assertEqual(status, 200)
            self.assertLess(elapsed, _LATENCY_BUDGET_SECONDS)


if __name__ == "__main__":
    unittest.main()
