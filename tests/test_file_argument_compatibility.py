# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Model argument variants must reach the same checked, atomic file tools."""

import asyncio
import json

import pytest

from navin.agent.runner import AgentRunner
from navin.agent.tools.apply_patch import ApplyPatchTool
from navin.agent.tools.file_manage import ManageFilesTool
from navin.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import LLMResponse
from tests.test_tool_parameter_recovery import _call, _Provider, _spec, _tools


def registry(root):
    tools = ToolRegistry()
    for cls in (ApplyPatchTool, EditFileTool, WriteFileTool, ReadFileTool, ManageFilesTool):
        tools.register(cls(workspace=root, allowed_dir=root, lint_after_edit=False))
    return tools


EDIT = {"file_path": "a.txt", "old_string": "old", "new_string": "new"}


@pytest.mark.parametrize("arguments", [
    EDIT, [EDIT], {"edits": EDIT}, {"edits": json.dumps([EDIT])},
    {"arguments": json.dumps({"edits": [EDIT]})},
    "```json\n" + json.dumps({"edits": [EDIT]}) + "\n```",
    json.dumps(json.dumps({"edits": [EDIT]})),
])
def test_equivalent_patch_shapes_write_exact_content(tmp_path, arguments):
    target = tmp_path / "a.txt"
    target.write_bytes(b"old\r\nuntouched\r\n")
    result = asyncio.run(registry(tmp_path).execute("apply_patch", arguments))
    assert not getattr(result, "is_error", False), result
    assert target.read_bytes() == b"new\r\nuntouched\r\n"


PATCH = "*** Begin Patch\n*** Update File: a.txt\n@@\n-old\n+new\n*** Add File: b.txt\n+created\n*** End Patch"


@pytest.mark.parametrize("arguments", [PATCH, {"patch": PATCH}, {"input": PATCH}, {"patch_text": PATCH}])
@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_text_patch_uses_atomic_file_edits(tmp_path, arguments, ending):
    (tmp_path / "a.txt").write_bytes(b"old" + ending)
    result = asyncio.run(registry(tmp_path).execute("apply_patch", arguments))
    assert not getattr(result, "is_error", False), result
    assert (tmp_path / "a.txt").read_bytes() == b"new" + ending
    assert (tmp_path / "b.txt").read_bytes() == b"created\n"


@pytest.mark.parametrize("arguments", [
    {"patch": PATCH.removesuffix("*** End Patch")},
    {"edits": [{**EDIT, "path": "other.txt"}]},
    {"edits": [{**EDIT, "action": "replace", "replace_all": True}]},
    {"edits": [{"path": "a.txt", "action": "replace", "old_text": "old"}]},
    {"patch": PATCH.replace("*** Add File: b.txt", "*** Delete File: b.txt")},
])
def test_ambiguous_or_incomplete_changes_leave_every_file_intact(tmp_path, arguments):
    (tmp_path / "a.txt").write_text("old\n")
    result = asyncio.run(registry(tmp_path).execute("apply_patch", arguments))
    assert result.is_error
    assert (tmp_path / "a.txt").read_text() == "old\n"
    assert not (tmp_path / "b.txt").exists()


def test_add_file_header_never_appends_to_existing_file(tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    (tmp_path / "b.txt").write_text("keep\n")
    result = asyncio.run(registry(tmp_path).execute("apply_patch", PATCH))
    assert result.is_error
    assert (tmp_path / "a.txt").read_text() == "old\n"
    assert (tmp_path / "b.txt").read_text() == "keep\n"


def test_patch_preview_and_workspace_bounds_still_apply(tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    tools = registry(tmp_path)
    result = asyncio.run(tools.execute("apply_patch", {"patch": PATCH, "dry_run": True}))
    assert not getattr(result, "is_error", False), result
    assert (tmp_path / "a.txt").read_text() == "old\n"
    assert not (tmp_path / "b.txt").exists()
    result = asyncio.run(tools.execute("apply_patch", {"edits": [{"path": "../forbidden.txt", "action": "add", "new_text": "x"}]}))
    assert result.is_error
    assert not (tmp_path.parent / "forbidden.txt").exists()


def test_read_write_edit_move_copy_delete_with_path_aliases(tmp_path):
    async def run():
        tools = registry(tmp_path)
        calls = [
            ("write_file", {"file_path": "a.txt", "content": "old\n"}),
            ("read_file", {"file_path": "a.txt"}),
            ("edit_file", EDIT),
            ("manage_files", {"action": "copy", "source": "a.txt", "dest": "b.txt"}),
            ("manage_files", {"action": "rename", "source_path": "b.txt", "destination_path": "c.txt"}),
            ("manage_files", {"action": "delete", "file_path": "a.txt"}),
        ]
        for name, params in calls:
            result = await tools.execute(name, params)
            assert not getattr(result, "is_error", False), result
        assert not (tmp_path / "a.txt").exists()
        assert not (tmp_path / "b.txt").exists()
        assert (tmp_path / "c.txt").read_text() == "new\n"
    asyncio.run(run())


def test_three_bad_patch_calls_recover_with_another_file_tool(tmp_path):
    provider = _Provider(
        *(_tools(_call("apply_patch")) for _ in range(3)),
        _tools(_call(path="recovered.txt", content="done\n")),
        LLMResponse(content="Completed."),
    )
    spec = _spec(tmp_path, provider)
    spec.tools = registry(tmp_path)
    result = asyncio.run(AgentRunner().run(spec))
    assert result.stop_reason == "completed", result.error
    assert (tmp_path / "recovered.txt").read_text() == "done\n"
    assert "apply_patch" not in {ToolRegistry._schema_name(tool) for tool in provider.requests[3]["tools"]}
    assert "File operation recovery" in str(provider.requests[3]["messages"])


def test_file_fallback_remains_bounded_and_preserves_policy(tmp_path):
    provider = _Provider(_tools(_call("apply_patch")))
    spec = _spec(tmp_path, provider)
    spec.tools = registry(tmp_path)
    result = asyncio.run(AgentRunner().run(spec))
    assert result.stop_reason == "tool_error"
    assert len(provider.requests) == 6
    assert not result.tools_used
    assert not spec.denied_tools
