# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The OS temp dir is scratch space for every tool, not only for exec."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from navin.agent.tools.filesystem import ReadFileTool, WriteFileTool
from navin.agent.tools.sandbox import writable_host_paths


@pytest.mark.scratch_dirs_open
def test_file_tools_reach_the_temp_dir_with_restrict_to_workspace_on(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("scratch")
        target = Path(handle.name)
    try:
        reader = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        assert reader._resolve_read(str(target)) is not None
        writer = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        out = Path(tempfile.gettempdir()) / "navin-scratch-test.txt"
        result = asyncio.run(writer.execute(path=str(out), content="ok"))
        assert "rror" not in str(result)[:20]
        assert out.read_text() == "ok"
        out.unlink()
    finally:
        target.unlink(missing_ok=True)


def test_the_sandbox_lets_commands_write_the_temp_dir(tmp_path: Path) -> None:
    paths = {str(Path(p).resolve()) for p in writable_host_paths(str(tmp_path))}
    assert str(Path(tempfile.gettempdir()).resolve()) in paths
