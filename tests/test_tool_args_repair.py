"""Repair of near-JSON tool call arguments before execution.

Smaller models routinely emit big write_file/exec payloads with literal
newlines inside JSON strings or a stray unescaped quote. Those must execute
instead of looping on "parameters must be a JSON object, got str". Truncated
payloads and ambiguous multi-field strings must still be rejected.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.registry import ToolRegistry


class _RecordingTool(Tool):
    def __init__(self, name: str, schema: dict[str, Any]):
        self._name = name
        self._schema = schema
        self.received: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "test tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, **params: Any) -> ToolResult:
        self.received = params
        return ToolResult("ok")


def _write_file_tool() -> _RecordingTool:
    return _RecordingTool(
        "write_file",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    )


def _exec_tool() -> _RecordingTool:
    return _RecordingTool(
        "exec",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["command"],
        },
    )


class ToolArgsRepairTest(unittest.TestCase):
    def _run(self, tool: _RecordingTool, params: Any) -> Any:
        registry = ToolRegistry()
        registry.register(tool)
        return asyncio.run(registry.execute(tool.name, params))

    def test_literal_newlines_in_json_string_are_repaired(self) -> None:
        tool = _write_file_tool()
        raw = '{"path": "report.html", "content": "<html>\n<body>\ntext\t.\n</body>\n</html>"}'
        result = self._run(tool, raw)
        self.assertFalse(getattr(result, "is_error", False), str(result))
        assert tool.received is not None
        self.assertEqual(tool.received["path"], "report.html")
        self.assertIn("<body>\ntext\t.\n</body>", tool.received["content"])

    def test_unescaped_inner_quotes_are_repaired(self) -> None:
        tool = _exec_tool()
        raw = '{"command": "echo "hello world" && ls"}'
        result = self._run(tool, raw)
        self.assertFalse(getattr(result, "is_error", False), str(result))
        assert tool.received is not None
        self.assertEqual(tool.received["command"], 'echo "hello world" && ls')

    def test_truncated_payload_is_rejected_not_written(self) -> None:
        tool = _write_file_tool()
        raw = '{"path": "report.html", "content": "<html><body>partial conte'
        result = self._run(tool, raw)
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIsNone(tool.received)

    def test_bare_string_maps_to_single_required_string_param(self) -> None:
        tool = _exec_tool()
        result = self._run(tool, "echo test")
        self.assertFalse(getattr(result, "is_error", False), str(result))
        assert tool.received is not None
        self.assertEqual(tool.received["command"], "echo test")

    def test_bare_string_is_rejected_for_multi_required_tools(self) -> None:
        tool = _write_file_tool()
        result = self._run(tool, "some content with no clear field")
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIsNone(tool.received)

    def test_valid_json_still_passes_untouched(self) -> None:
        tool = _write_file_tool()
        raw = '{"path": "a.txt", "content": "line1\\nline2"}'
        result = self._run(tool, raw)
        self.assertFalse(getattr(result, "is_error", False), str(result))
        assert tool.received is not None
        self.assertEqual(tool.received["content"], "line1\nline2")

    def test_rejection_message_names_the_required_parameters(self) -> None:
        tool = _write_file_tool()
        result = self._run(tool, "some content with no clear field")
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIn("Required: path, content", str(result))

    def test_native_array_for_string_param_becomes_valid_json_text(self) -> None:
        # A model passing findings natively (array) instead of a JSON-encoded
        # string must yield parseable JSON, not a Python repr with single quotes.
        import json

        tool = _RecordingTool(
            "code_review",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "findings_json": {"type": "string"},
                },
                "required": ["action"],
            },
        )
        result = self._run(
            tool,
            {"action": "filter", "findings_json": [{"path": "a.py", "line": 3}]},
        )
        self.assertFalse(getattr(result, "is_error", False), str(result))
        assert tool.received is not None
        self.assertEqual(
            json.loads(tool.received["findings_json"]),
            [{"path": "a.py", "line": 3}],
        )


if __name__ == "__main__":
    unittest.main()
