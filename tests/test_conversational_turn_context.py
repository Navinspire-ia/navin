# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A greeting held back by the intent gate gets no orientation context."""

import asyncio
from types import SimpleNamespace

from navin.agent.loop import AgentLoop
from navin.command.modules import CONVERSATIONAL_TURN_METADATA_KEY
from navin.runtime_context import RuntimeContextBlock


def _blocks_for(metadata: dict) -> list[str]:
    async def board(_request):
        return RuntimeContextBlock(source="board", content="In progress: t-a47c2db3")

    async def git(_request):
        return RuntimeContextBlock(source="git", content="branch prod-v2, 20 unstaged")

    async def project_map(_request):
        return RuntimeContextBlock(source="metagraph", content="navin/ tui/ agent/")

    async def mention(_request):
        return RuntimeContextBlock(source="file_mentions", content="@notes.md attached")

    loop = SimpleNamespace(tools=None, _runtime_context_providers=[board, git, project_map, mention])
    ctx = SimpleNamespace(
        tools=SimpleNamespace(get_runtime_context_providers=lambda: []),
        request_context=SimpleNamespace(),
        msg=SimpleNamespace(metadata=metadata),
    )
    blocks = asyncio.run(AgentLoop._resolve_runtime_context_for_turn(loop, ctx))
    return [block.source for block in blocks]


def test_greeting_turn_gets_no_board_git_or_project_map():
    assert _blocks_for({CONVERSATIONAL_TURN_METADATA_KEY: True}) == ["file_mentions"]


def test_work_turn_keeps_the_full_orientation_context():
    assert _blocks_for({CONVERSATIONAL_TURN_METADATA_KEY: False}) == ["board", "git", "metagraph", "file_mentions"]
    assert _blocks_for({}) == ["board", "git", "metagraph", "file_mentions"]
