"""WebUI routes that touch the disk must not stall the shared engine loop."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from navin.webui import settings_routes
from navin.webui.settings_routes import WebUISettingsRouter


def _router() -> WebUISettingsRouter:
    return WebUISettingsRouter(
        bus=SimpleNamespace(),
        logger=SimpleNamespace(),
        check_api_token=lambda _request: True,
        parse_query=lambda path: {"code": ["ABC"]} if "code=" in path else {},
        json_response=lambda payload: ("json", payload),
        error_response=lambda status, message: ("error", status, message),
        runtime_surface="desktop",
        runtime_capabilities={},
    )


async def _longest_gap(coro: Any) -> tuple[Any, float]:
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    try:
        result = await coro
    finally:
        stop.set()
        await task
    return result, max(gaps)


def _slow_pending() -> list[dict[str, Any]]:
    time.sleep(0.5)
    return [{"code": "ABC", "channel": "telegram", "sender_id": "42", "created_at": time.time()}]


@pytest.mark.asyncio
async def test_pairing_list_runs_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_routes, "list_pending", _slow_pending)
    router = _router()
    request = SimpleNamespace(path="/api/settings/pairing", headers={})
    result, gap = await _longest_gap(router.dispatch(None, request, "/api/settings/pairing"))
    assert gap < 0.25
    assert result[0] == "json"
    assert [row["code"] for row in result[1]["requests"]] == ["ABC"]
    assert result[1]["requests"][0]["sender_id"] == "42"


@pytest.mark.asyncio
async def test_pairing_deny_runs_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_deny(code: str) -> bool:
        time.sleep(0.5)
        return code == "ABC"

    monkeypatch.setattr(settings_routes, "deny_code", slow_deny)
    monkeypatch.setattr(settings_routes, "list_pending", lambda: [])
    router = _router()
    request = SimpleNamespace(path="/api/settings/pairing/deny?code=ABC", headers={})
    result, gap = await _longest_gap(router.dispatch(None, request, "/api/settings/pairing/deny"))
    assert gap < 0.25
    assert result[0] == "json"
    assert result[1]["last_action"]["action"] == "deny"
