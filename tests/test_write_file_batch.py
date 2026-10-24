# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""write_file writes several files in one call (one model round trip)."""

from __future__ import annotations

import asyncio

from navin.agent.code_validation import edit_paths
from navin.agent.tools.filesystem import WriteFileTool


def test_several_files_in_one_call(tmp_path):
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = asyncio.run(tool.execute(files=[
        {"path": "src/App.tsx", "content": "export const App = () => null;\n"},
        {"path": "src/App.test.tsx", "content": "test('x', () => {});\n"},
    ]))
    assert (tmp_path / "src/App.tsx").read_text() == "export const App = () => null;\n"
    assert (tmp_path / "src/App.test.tsx").exists()
    assert "App.tsx" in str(result) and "App.test.tsx" in str(result)


def test_one_bad_item_does_not_lose_the_others(tmp_path):
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = asyncio.run(tool.execute(files=[
        {"path": "ok.txt", "content": "fine"},
        {"path": "../outside.txt", "content": "nope"},
    ]))
    assert (tmp_path / "ok.txt").read_text() == "fine"
    assert "Error" in str(result)


def test_single_file_mode_is_unchanged(tmp_path):
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
    asyncio.run(tool.execute(path="a.py", content="x = 1\n"))
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_batched_writes_count_as_code_changes():
    assert edit_paths({"files": [{"path": "src/a.ts", "content": ""}, {"path": "b.py", "content": ""}]}) == {"src/a.ts", "b.py"}


def test_verify_runs_in_the_same_call(tmp_path, monkeypatch):
    from navin.agent.tools import quality

    seen = {}

    async def fake_execute(self, action, paths=None, **kwargs):
        seen["action"], seen["paths"] = action, paths
        return "PASS - no lint errors"

    monkeypatch.setattr(quality.VerifyTool, "execute", fake_execute)
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = asyncio.run(tool.execute(files=[{"path": "src/a.ts", "content": "export {}\n"}], verify=True))
    assert seen == {"action": "check", "paths": ["src/a.ts"]}
    assert "Verify:" in str(result) and "PASS" in str(result)


def test_whole_file_write_refuses_a_file_changed_since_it_was_read(tmp_path):
    from navin.agent.tools.file_state import FileStates
    from navin.agent.tools.filesystem import ReadFileTool

    states = FileStates()
    reader = ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path, file_states=states)
    writer = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path, file_states=states)
    target = tmp_path / "routes.py"
    target.write_text("A = 1\n")
    asyncio.run(reader.execute(path="routes.py"))
    # A parallel subagent edits the file after this agent read it.
    target.write_text("A = 1\nB = 2\n")

    refused = asyncio.run(writer.execute(path="routes.py", content="A = 3\n"))
    assert "refused to overwrite" in str(refused)
    assert target.read_text() == "A = 1\nB = 2\n"
    batch = asyncio.run(writer.execute(files=[
        {"path": "routes.py", "content": "A = 3\n"},
        {"path": "new.py", "content": "x = 1\n"},
    ]))
    assert "refused to overwrite" in str(batch)
    assert target.read_text() == "A = 1\nB = 2\n"
    assert (tmp_path / "new.py").read_text() == "x = 1\n"

    # A fresh read lifts the refusal, and the agent's own write is the new baseline.
    asyncio.run(reader.execute(path="routes.py"))
    asyncio.run(writer.execute(path="routes.py", content="A = 3\n"))
    assert target.read_text() == "A = 3\n"
    asyncio.run(writer.execute(path="routes.py", content="A = 4\n"))
    assert target.read_text() == "A = 4\n"


def test_a_file_never_read_is_still_written_with_its_warning(tmp_path):
    target = tmp_path / "legacy.py"
    target.write_text("old = True\n")
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)

    result = asyncio.run(tool.execute(path="legacy.py", content="new = True\n"))
    assert "refused" not in str(result)
    assert target.read_text() == "new = True\n"
