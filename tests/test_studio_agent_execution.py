# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Real desk entry points must remain usable from an asynchronous chat turn."""

from __future__ import annotations

import asyncio
import threading
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navin.agent.tools.career import CareerTool
from navin.agent.tools.context import RequestContext, current_request_context, request_context
from navin.agent.tools.marketing import MarketingTool
from navin.agent.tools.tenders import TendersTool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_cls", "handler", "action"),
    [
        (CareerTool, "navin.webui.career_api.handle_career_action", "collect"),
        (TendersTool, "navin.webui.tenders_api.handle_tenders_action", "collect"),
        (MarketingTool, "navin.webui.marketing_desk_api.handle_marketing_action", "plan"),
    ],
)
async def test_desk_action_keeps_chat_responsive_and_preserves_request_context(tool_cls, handler, action):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    marker: ContextVar[str] = ContextVar("studio_test_marker", default="missing")
    token = marker.set("same-turn")
    ctx = RequestContext(channel="websocket", chat_id="studio-test", session_key="websocket:studio-test")
    timed_out = False

    def desk_action(*args, **kwargs):
        nonlocal timed_out
        assert current_request_context() is ctx
        assert marker.get() == "same-turn"
        loop.call_soon_threadsafe(started.set)
        timed_out = not release.wait(2)
        return {"ready": True}

    try:
        with request_context(ctx), patch(handler, side_effect=desk_action):
            task = asyncio.create_task(tool_cls().execute(action=action))
            try:
                await asyncio.wait_for(started.wait(), 3)
                assert not timed_out, "The desk blocked the event loop until the worker timed out"
            finally:
                release.set()
            assert await task == {"ready": True}
    finally:
        marker.reset(token)


@pytest.mark.asyncio
async def test_marketing_chat_produce_awaits_the_configured_media_provider():
    row = {"id": "creative-test", "kind": "image", "prompt": "Fictitious product", "status": "brief"}
    store = MagicMock()
    store.load_creatives.return_value = [row]
    store.load_content.return_value = []
    store.load_harvest.return_value = {}
    client = SimpleNamespace(generate=AsyncMock(return_value=SimpleNamespace(images=[])))
    config = SimpleNamespace(tools=SimpleNamespace(image_generation=SimpleNamespace(
        provider="test-provider", model="test-model", default_aspect_ratio="1:1", default_image_size="1K",
    )))
    with (
        patch("navin.webui.marketing_desk_api._store", return_value=store),
        patch("navin.webui.marketing_desk_api.snapshot", return_value={}),
        patch("navin.config.loader.load_config", return_value=config),
        patch("navin.providers.media_credentials.media_credentials_ready", return_value=True),
        patch("navin.providers.image_generation.get_image_gen_provider", return_value=lambda **kwargs: client),
        patch("navin.providers.image_generation.image_gen_provider_configs", return_value={"test-provider": SimpleNamespace(api_key="test")}),
    ):
        result = await MarketingTool().execute(action="produce", id=row["id"])
    client.generate.assert_awaited_once()
    assert result["produce"]["produced"] == 0
    assert result["produce"]["skipped"] == ["image provider returned no stills"]
