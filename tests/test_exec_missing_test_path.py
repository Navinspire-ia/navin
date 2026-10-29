# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A pytest command naming a file that does not exist is refused before it runs.

Seen in a session: ``python -m pytest`` on 21 test files, one of them a guessed
name (``tests/test_metagraph_tool.py`` next to the real ``test_metagraph_graph.py``).
pytest answered "file or directory not found" and ran none of the other 20; the
agent only saw the raw error. The refusal names the closest real files instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from navin.agent.tools import shell
from navin.agent.tools.shell import ExecTool


def _project(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("test_metagraph_graph.py", "test_metagraph_debounce.py", "test_montage_tool.py", "test_board.py"):
        (tests / name).write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    return tmp_path


def test_missing_pytest_paths_are_found_whatever_the_launcher(tmp_path) -> None:
    root = _project(tmp_path)
    cwd = str(root)
    for command in (
        "python -m pytest -q tests/test_board.py tests/test_metagraph_tool.py",
        ".venv/bin/python -m pytest -q -p no:cacheprovider tests/test_metagraph_tool.py::TestX::test_y",
        "pytest tests/test_metagraph_tool.py -k graph",
        "uv run pytest -x tests/test_metagraph_tool.py",
        "timeout 600 python3 -B -m pytest tests/test_metagraph_tool.py 2>&1 | tail -3",
        "cd pkg && python -m pytest ../tests/test_metagraph_tool.py",
    ):
        missing = shell._missing_pytest_paths(command, cwd)
        assert [head for head, _ in missing] == [
            "../tests/test_metagraph_tool.py" if command.startswith("cd pkg") else "tests/test_metagraph_tool.py"
        ], command


def test_option_values_filters_node_ids_and_real_paths_are_not_reported(tmp_path) -> None:
    root = _project(tmp_path)
    cwd = str(root)
    for command in (
        "python -m pytest -q tests/test_board.py",
        "python -m pytest -q tests/test_board.py::test_ok",
        "python -m pytest -k test_metagraph_tool tests",
        "python -m pytest -m 'not slow' --maxfail 1 --tb short tests/",
        "python -m pytest --ignore tests/test_board.py tests > out.log 2>&1",
        "python -m pytest -p no:cacheprovider --junitxml report.xml tests",
        "python -m pytest -q tests/test_*.py",
        "python -m pytest -q $TARGET",
        "python -m pytest -o addopts='' tests",
        "python -m pytest -q TestBoard.test_ok",
        "python -m pytest --co -q",
        "python script.py tests/missing.py",
        "ls tests/test_metagraph_tool.py",
    ):
        assert shell._missing_pytest_paths(command, cwd) == [], command


def test_exec_refuses_before_spawning_and_names_the_closest_files(tmp_path) -> None:
    root = _project(tmp_path)
    tool = ExecTool(timeout=30, working_dir=str(root))
    result = asyncio.run(tool.execute(
        command="python -m pytest -q tests/test_board.py tests/test_metagraph_tool.py", working_dir=str(root),
    ))
    assert result.is_error
    text = str(result)
    assert "would stop before running a single test" in text
    assert "tests/test_metagraph_tool.py does not exist" in text
    # The two files sharing the distinctive word come first; the one sharing
    # only the generic ``tool`` comes after them.
    similar = text.split("Existing tests with a similar name: ", 1)[1].split(". Run one of those", 1)[0]
    assert similar == "tests/test_metagraph_graph.py, tests/test_metagraph_debounce.py, tests/test_montage_tool.py"
    assert "test_board.py," not in text  # not similar: not proposed
    assert 'find_files glob="tests/test_metagraph*"' in text
    assert "Exit code" not in text  # nothing was spawned


def test_exec_still_runs_pytest_on_real_files(tmp_path) -> None:
    root = _project(tmp_path)
    tool = ExecTool(timeout=60, working_dir=str(root))
    result = asyncio.run(tool.execute(
        command="python -m pytest -q -p no:cacheprovider tests/test_board.py", working_dir=str(root),
    ))
    text = str(result)
    assert "1 passed" in text and "Exit code: 0" in text
