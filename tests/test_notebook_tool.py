"""Jupyter notebooks are read as cells and edited as cells."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import EditFileTool, ReadFileTool
from navin.agent.tools.notebook import NotebookEditTool, render_notebook

_PNG = "iVBORw0KGgo" * 400


def _notebook() -> dict:
    return {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Analysis\n", "Some text."]},
            {
                "cell_type": "code",
                "execution_count": 3,
                "metadata": {},
                "source": ["import pandas as pd\n", "df = pd.read_csv('x.csv')\n", "df.head()"],
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": ["loaded 10 rows\n"]},
                    {
                        "output_type": "execute_result",
                        "execution_count": 3,
                        "data": {"text/plain": ["   a  b\n", "0  1  2"], "text/html": ["<table>...</table>"]},
                        "metadata": {},
                    },
                    {"output_type": "display_data", "data": {"image/png": _PNG}, "metadata": {}},
                    {"output_type": "error", "ename": "NameError", "evalue": "name 'x' is not defined", "traceback": ["..."]},
                ],
            },
        ],
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


class _NotebookCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / "nb.ipynb"
        self.path.write_text(json.dumps(_notebook(), indent=1), encoding="utf-8")
        self.states = FileStates()

    def _tool(self, cls):
        return cls(workspace=self.root, allowed_dir=self.root, file_states=self.states)

    def _run(self, coro):
        return asyncio.run(coro)

    def _cells(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))["cells"]


class RenderTest(_NotebookCase):
    def test_cells_are_numbered_and_outputs_summarised(self) -> None:
        out = render_notebook(self.path.read_text(encoding="utf-8"))
        self.assertIn("Notebook: 2 cells (kernel: python3)", out)
        self.assertIn("[cell 1] markdown\n# Analysis\nSome text.", out)
        self.assertIn("[cell 2] code (in [3])\nimport pandas as pd", out)
        self.assertIn("--- output: stdout\nloaded 10 rows", out)
        self.assertIn("--- output: execute_result (text/plain)\n   a  b\n0  1  2", out)
        self.assertIn("--- output: display_data (image/png omitted)", out)
        self.assertIn("--- output: error NameError: name 'x' is not defined", out)
        self.assertNotIn(_PNG[:40], out)

    def test_read_file_shows_the_rendering_not_the_json(self) -> None:
        out = self._run(self._tool(ReadFileTool).execute(path="nb.ipynb"))
        self.assertIn("[cell 2] code", out)
        self.assertNotIn('"cell_type"', out)
        self.assertIn("notebook_edit", out)

    def test_something_that_is_not_a_notebook_is_left_alone(self) -> None:
        self.assertIsNone(render_notebook("not json"))
        self.assertIsNone(render_notebook(json.dumps({"a": 1})))


class EditTest(_NotebookCase):
    def test_replace_clears_stale_outputs_and_keeps_the_type(self) -> None:
        out = self._run(self._tool(NotebookEditTool).execute(
            path="nb.ipynb", cell=2, source="print('hi')\n",
        ))
        self.assertIn("Replaced cell 2 (code)", out)
        cell = self._cells()[1]
        self.assertEqual("".join(cell["source"]), "print('hi')\n")
        self.assertEqual(cell["outputs"], [])
        self.assertIsNone(cell["execution_count"])
        self.assertEqual(cell["cell_type"], "code")

    def test_insert_after_and_before(self) -> None:
        tool = self._tool(NotebookEditTool)
        self._run(tool.execute(path="nb.ipynb", cell=1, action="insert_after", source="x = 1", cell_type="code"))
        self._run(tool.execute(path="nb.ipynb", cell=1, action="insert_before", source="## Intro", cell_type="markdown"))
        cells = self._cells()
        self.assertEqual([c["cell_type"] for c in cells], ["markdown", "markdown", "code", "code"])
        self.assertEqual("".join(cells[0]["source"]), "## Intro")
        self.assertEqual("".join(cells[2]["source"]), "x = 1")

    def test_insert_after_zero_appends_at_the_top(self) -> None:
        self._run(self._tool(NotebookEditTool).execute(
            path="nb.ipynb", cell=0, action="insert_after", source="first", cell_type="markdown",
        ))
        self.assertEqual("".join(self._cells()[0]["source"]), "first")

    def test_delete(self) -> None:
        out = self._run(self._tool(NotebookEditTool).execute(path="nb.ipynb", cell=1, action="delete"))
        self.assertIn("Deleted markdown cell 1", out)
        self.assertEqual(len(self._cells()), 1)

    def test_out_of_range_and_missing_source_are_errors(self) -> None:
        tool = self._tool(NotebookEditTool)
        self.assertIn("does not exist", str(self._run(tool.execute(path="nb.ipynb", cell=9, source="x"))))
        self.assertIn("source is required", str(self._run(tool.execute(path="nb.ipynb", cell=1))))
        self.assertIn("action must be", str(self._run(tool.execute(path="nb.ipynb", cell=1, action="swap"))))

    def test_the_file_stays_a_notebook_jupyter_can_open(self) -> None:
        self._run(self._tool(NotebookEditTool).execute(path="nb.ipynb", cell=2, source="y = 2"))
        nb = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(nb["nbformat"], 4)
        self.assertEqual(nb["metadata"]["kernelspec"]["name"], "python3")
        # Sources keep Jupyter's list-of-lines layout.
        self.assertIsInstance(nb["cells"][1]["source"], list)

    def test_edit_file_points_at_the_cell_tool(self) -> None:
        out = self._run(self._tool(EditFileTool).execute(
            path="nb.ipynb", old_text="import pandas", new_text="import polars",
        ))
        self.assertIn("notebook_edit", str(out))
        self.assertIn("import pandas", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
