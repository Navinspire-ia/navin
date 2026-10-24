# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The user sees the call being written, not a frozen screen."""

from __future__ import annotations

import asyncio

from navin.agent.runner import _ToolDraftStatus


class _Hook:
    def __init__(self) -> None:
        self.labels: list[str] = []

    async def emit_live_status(self, label: str) -> None:
        self.labels.append(label)


def test_status_follows_the_streamed_tool_call():
    async def run() -> list[str]:
        hook = _Hook()
        status = _ToolDraftStatus(hook)
        status._MIN_INTERVAL_S = 0
        await status.waiting("models/gemini-3.8-flash")
        await status.feed({"index": 0, "name": "write_file", "arguments_delta": '{"files":[{"path":"src/App.tsx","content":"' + "x" * 1500})
        await status.feed({"index": 0, "arguments_delta": '"},{"path":"src/Header.tsx","content":"y'})
        await status.feed({"index": 1, "name": "exec", "arguments_delta": '{"command":"npm install zod'})
        return hook.labels

    labels = asyncio.run(run())
    assert labels[0] == "gemini-3.8-flash is working…"
    assert labels[1].startswith("Writing src/App.tsx… 1.5k chars")
    assert labels[2].startswith("Writing src/Header.tsx (+1 files)")
    assert labels[3] == "Preparing command: npm install zod…"


def test_updates_are_throttled():
    async def run() -> int:
        hook = _Hook()
        status = _ToolDraftStatus(hook)
        await status.waiting("m")
        for _ in range(50):
            await status.feed({"index": 0, "name": "write_file", "arguments_delta": "x" * 10})
        return len(hook.labels)

    assert asyncio.run(run()) <= 3
