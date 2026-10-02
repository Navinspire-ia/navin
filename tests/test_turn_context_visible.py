# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Matching skills reach the model unprompted, and the user sees what context
the turn started from instead of concluding it was never used."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from navin.agent.loop import announce_runtime_context
from navin.agent.tools.skill_catalog import SKILL_SUGGESTION_HEADER, SkillCatalogTool
from navin.runtime_context import RuntimeContextBlock


def test_small_talk_gets_no_skill_and_a_task_gets_its_skill() -> None:
    tool = SkillCatalogTool(workspace=Path.cwd())
    rows = {"name": "pdf-generator", "description": "Generate PDF documents", "available": True}
    tool._suggest_for_request = lambda text: [rows] if "pdf" in text else []  # type: ignore[method-assign]
    chat = asyncio.run(tool._provide_skill_suggestions(SimpleNamespace(original_user_text="salut ca va bien ?")))
    task = asyncio.run(tool._provide_skill_suggestions(SimpleNamespace(original_user_text="make a pdf report of sales")))
    assert chat is None
    assert task is not None and task.content.startswith(f"{SKILL_SUGGESTION_HEADER} pdf-generator.")


def test_the_turn_announces_its_context_once() -> None:
    sent: list[list[dict]] = []

    async def on_progress(text: str, *, tool_hint: bool = False, tool_events: Any = None, **_: Any) -> None:
        sent.append(tool_events)

    blocks = [
        RuntimeContextBlock(source="metagraph", content="repo map"),
        RuntimeContextBlock(source="skills", content=f"{SKILL_SUGGESTION_HEADER} style-editor.\n- style-editor: x"),
        RuntimeContextBlock(source="world_predict", content="ignored"),
    ]
    asyncio.run(announce_runtime_context(on_progress, blocks, "t1"))
    assert len(sent) == 1
    event = sent[0][0]
    assert event["name"] == "context" and event["call_id"] == "context-t1"
    assert event["result"] == "Context loaded - project map; skills: style-editor"


def test_nothing_is_announced_without_context() -> None:
    sent: list = []

    async def on_progress(*_a: Any, **_k: Any) -> None:
        sent.append(1)

    asyncio.run(announce_runtime_context(on_progress, [], "t1"))
    assert sent == []
