# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A missing user permission must not turn into an alternate-method retry loop."""

from unittest.mock import AsyncMock

import pytest

from navin.agent.tools.base import ToolResult
from navin.agent.tools.computer import ComputerTool
from navin.agent.tools.registry import ToolRegistry, tool_error_hint


@pytest.mark.asyncio
async def test_registry_keeps_permission_guidance_and_does_not_duplicate_it():
    result = ToolResult.error("Computer permission required", recovery_hint="Guide the user and wait for consent.")
    tool = ComputerTool()
    tool.execute = AsyncMock(return_value=result)
    registry = ToolRegistry()
    registry.register(tool)
    wrapped = await registry.execute("computer", {"action": "screen"})
    assert wrapped.is_error
    assert wrapped.recovery_hint == result.recovery_hint
    assert "try a different approach" not in wrapped
    assert wrapped.count("Guide the user") == 1
    # The runner can receive a result already wrapped by the registry.
    assert tool_error_hint(wrapped) == ""


def test_ordinary_errors_keep_the_default_recovery_advice():
    result = ToolResult.error("temporary failure")
    assert tool_error_hint(result) == "\n\n[Analyze the error above and try a different approach.]"
