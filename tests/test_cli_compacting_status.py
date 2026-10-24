# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The interactive CLI says "Compacting context…" beside Working.

Summarizing a full context takes model calls; a bare spinner during that wait
reads as a frozen turn, so the compaction notice reaches the status line.
"""

from __future__ import annotations

import asyncio

from navin.bus.events import OUTBOUND_META_AGENT_UI
from navin.bus.outbound_events import ProgressEvent, outbound_message_for_event
from navin.cli.commands import _maybe_print_interactive_progress
from navin.utils.task_progress import build_agent_ui_blob


class _Renderer:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def set_status(self, text: str) -> None:
        self.statuses.append(text)


def _progress(data: dict) -> object:
    return outbound_message_for_event(
        channel="cli",
        chat_id="direct",
        event=ProgressEvent(content=""),
        content="",
        metadata={OUTBOUND_META_AGENT_UI: build_agent_ui_blob(data)},
    )


def test_compacting_notice_shows_beside_working() -> None:
    renderer = _Renderer()
    msg = _progress({"label": "Compacting context…", "indeterminate": True, "phase": "compacting"})
    handled = asyncio.run(_maybe_print_interactive_progress(msg, None, None, renderer))
    assert handled is True
    assert renderer.statuses == ["Working… Compacting context…"]


def test_other_task_progress_label_replaces_status() -> None:
    renderer = _Renderer()
    msg = _progress({"label": "Downloading…", "indeterminate": True})
    asyncio.run(_maybe_print_interactive_progress(msg, None, None, renderer))
    assert renderer.statuses == ["Downloading…"]
