"""Ask mode: read-only workflow metadata + hard tool gate in the runner."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.loop import AgentLoop
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.command.builtin import (
    _ASK_CLARIFY_CLAUSE,
    _ASK_UNCLEAR_FOCUS_CLAUSE,
    _CODE_VERIFY_CLAUSE,
    _READ_ONLY_WORKFLOWS,
    _UNCLEAR_FOCUS_CLAUSE,
    _WORKFLOW_BRIEFS,
    _workflow_handler,
    ask_focus_is_specific,
)
from navin.command.modules import READ_ONLY_TOOLS_METADATA_KEY
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime


class AskWorkflowBriefTest(unittest.TestCase):
    def _brief(self, command: str = "/ask") -> tuple[str, dict]:
        msg = SimpleNamespace(
            content="",
            metadata={},
            channel="cli",
            chat_id="test",
        )
        ctx = SimpleNamespace(
            args="explain AuthService",
            raw=f"{command} explain AuthService",
            msg=msg,
            loop=None,
        )
        asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
        return msg.content, dict(msg.metadata)

    def test_ask_is_registered_read_only_workflow(self) -> None:
        self.assertIn("/ask", _WORKFLOW_BRIEFS)
        self.assertEqual(_READ_ONLY_WORKFLOWS, frozenset({"/ask"}))

    def test_ask_brief_stamps_read_only_metadata_and_composer_mode(self) -> None:
        content, meta = self._brief()
        self.assertTrue(meta.get(READ_ONLY_TOOLS_METADATA_KEY))
        self.assertEqual(meta.get("composer_mode"), "ask")
        self.assertEqual(meta.get("original_command"), "/ask")
        self.assertIn("READ-ONLY", content)
        self.assertIn("CLARIFY FIRST", content)
        self.assertIn("Project linkage:", content)
        self.assertNotIn(_CODE_VERIFY_CLAUSE, content)

    def test_forge_is_not_read_only(self) -> None:
        _content, meta = self._brief("/forge")
        self.assertFalse(meta.get(READ_ONLY_TOOLS_METADATA_KEY))

    def test_loop_reads_metadata_flag(self) -> None:
        self.assertTrue(
            AgentLoop._read_only_tools(None, {READ_ONLY_TOOLS_METADATA_KEY: True})
        )
        self.assertFalse(AgentLoop._read_only_tools(None, {}))
        self.assertFalse(AgentLoop._read_only_tools(None, None))

    def test_ask_focus_specificity(self) -> None:
        self.assertFalse(ask_focus_is_specific(""))
        self.assertFalse(ask_focus_is_specific("salut"))
        self.assertFalse(ask_focus_is_specific("aide moi"))
        self.assertFalse(ask_focus_is_specific("regarde mon projet"))
        self.assertTrue(ask_focus_is_specific("explique AuthService"))
        self.assertTrue(ask_focus_is_specific("où est géré le login ?"))
        self.assertTrue(ask_focus_is_specific("why does tests/test_auth.py fail"))

    def _ask_with(self, args: str, *, metadata: dict | None = None) -> tuple[str, dict]:
        msg = SimpleNamespace(
            content="",
            metadata=dict(metadata or {}),
            channel="websocket",
            chat_id="test",
        )
        ctx = SimpleNamespace(
            args=args,
            raw=f"/ask {args}".strip(),
            msg=msg,
            loop=None,
        )
        asyncio.run(_workflow_handler("/ask")(ctx))  # type: ignore[arg-type]
        return msg.content, dict(msg.metadata)

    def test_ask_greeting_uses_ask_unclear_gate(self) -> None:
        from navin.command.modules import PRELOAD_SKILLS_METADATA_KEY

        content, meta = self._ask_with("salut")
        self.assertIn(_ASK_UNCLEAR_FOCUS_CLAUSE, content)
        self.assertNotIn(_UNCLEAR_FOCUS_CLAUSE, content)
        self.assertEqual(meta.get(PRELOAD_SKILLS_METADATA_KEY), [])

    def test_ask_vague_with_linked_project_still_clarifies(self) -> None:
        content, meta = self._ask_with(
            "améliore mon projet",
            metadata={
                "workspace_scope": {
                    "project_path": "/tmp/demo-app",
                    "project_name": "demo-app",
                    "access_mode": "project",
                }
            },
        )
        self.assertIn(_ASK_CLARIFY_CLAUSE, content)
        self.assertIn("Project linkage: linked (demo-app)", content)
        self.assertIn("améliore mon projet", content)
        # Skills stay off until the ask is concrete.
        from navin.command.modules import PRELOAD_SKILLS_METADATA_KEY

        self.assertEqual(meta.get(PRELOAD_SKILLS_METADATA_KEY), [])

    def test_ask_no_project_vague_blocks_tools_path(self) -> None:
        content, _meta = self._ask_with("aide moi un peu")
        self.assertIn("Project linkage: no linked project", content)
        self.assertIn(_ASK_CLARIFY_CLAUSE, content)
        self.assertNotIn("Skills for this mission", content)


class _FakeProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


class _ReadTool(Tool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "read"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        return f"ok:{kwargs.get('path')}"


class _WriteTool(Tool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "write"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        return "written"


def _runtime(provider: Any) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )


class AskReadOnlyGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_tools_are_blocked_and_never_execute(self) -> None:
        writer = _WriteTool()
        reader = _ReadTool()
        provider = _FakeProvider(
            [
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="w1",
                            name="write_file",
                            arguments={"path": "a.py", "content": "x = 1\n"},
                        )
                    ],
                ),
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="r1",
                            name="read_file",
                            arguments={"path": "a.py"},
                        )
                    ],
                ),
                LLMResponse(
                    content="AuthService validates tokens.",
                    finish_reason="stop",
                ),
            ]
        )
        tools = ToolRegistry()
        tools.register(writer)
        tools.register(reader)
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "/ask explain"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=6,
                max_tool_result_chars=4000,
                hook=AgentHook(),
                read_only_tools=True,
            )
        )
        self.assertEqual(writer.calls, 0)
        self.assertEqual(reader.calls, 1)
        self.assertIn("read_file", result.tools_used)
        self.assertNotIn("write_file", result.tools_used)
        blocked_events = [
            e for e in result.tool_events if e.get("detail") == "blocked by read-only turn"
        ]
        self.assertEqual(len(blocked_events), 1)
        blocked_text = " ".join(
            str(m.get("content", ""))
            for m in result.messages
            if m.get("role") == "tool"
        )
        self.assertIn("read-only", blocked_text.lower())
        self.assertIn("ask", blocked_text.lower())

    async def test_agent_mode_still_allows_writes(self) -> None:
        writer = _WriteTool()
        provider = _FakeProvider(
            [
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="w1",
                            name="write_file",
                            arguments={"path": "a.py", "content": "x = 1\n"},
                        )
                    ],
                ),
                LLMResponse(content="done", finish_reason="stop"),
            ]
        )
        tools = ToolRegistry()
        tools.register(writer)
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "/forge write"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=4,
                max_tool_result_chars=4000,
                hook=AgentHook(),
                read_only_tools=False,
            )
        )
        self.assertEqual(writer.calls, 1)
        self.assertIn("write_file", result.tools_used)


if __name__ == "__main__":
    unittest.main()
