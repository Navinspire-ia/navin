# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Python had no type checker while TypeScript had one.

The lint table shipped `tsc` for the frontend and, for Python, only ruff and a
syntax check. So the agent could break a signature in its own language and see a
clean lint. These tests cover the two checkers added for it, and above all the
gating: mypy configured through pyproject.toml must not be unleashed on every
repository that happens to have that file.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent.tools.file_state import FileStates
from navin.agent.tools.quality import LintTool
from navin.quality.linters import _parse_json_rows, _requirements_met, linter_table


def _run(coro):
    return asyncio.run(coro)


class GatingTest(unittest.TestCase):
    """A checker with no configuration must stay out of the way."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.table = linter_table()

    def _write(self, name: str, body: str) -> None:
        (self.root / name).write_text(textwrap.dedent(body), encoding="utf-8")

    def _applies(self, linter: str) -> bool:
        return _requirements_met(self.table[linter], self.root)

    def test_a_bare_pyproject_does_not_summon_mypy(self) -> None:
        """Nearly every Python project has this file and most do not want mypy."""
        self._write("pyproject.toml", "[project]\nname = 'x'\n")
        self.assertFalse(self._applies("mypy"))

    def test_a_tool_mypy_section_does(self) -> None:
        self._write("pyproject.toml", "[project]\nname = 'x'\n\n[tool.mypy]\nstrict = true\n")
        self.assertTrue(self._applies("mypy"))

    def test_a_dedicated_ini_does(self) -> None:
        self._write("mypy.ini", "[mypy]\n")
        self.assertTrue(self._applies("mypy"))

    def test_a_setup_cfg_section_does(self) -> None:
        self._write("setup.cfg", "[metadata]\nname = x\n\n[mypy]\n")
        self.assertTrue(self._applies("mypy"))

    def test_an_unconfigured_project_gets_neither_checker(self) -> None:
        self._write("app.py", "x = 1\n")
        self.assertFalse(self._applies("mypy"))
        self.assertFalse(self._applies("pyright"))

    def test_a_pyright_config_file_is_enough(self) -> None:
        self._write("pyrightconfig.json", "{}\n")
        self.assertTrue(self._applies("pyright"))

    def test_a_tool_pyright_section_is_too(self) -> None:
        self._write("pyproject.toml", "[tool.pyright]\ntypeCheckingMode = 'basic'\n")
        self.assertTrue(self._applies("pyright"))

    def test_an_unreadable_config_is_not_a_crash(self) -> None:
        (self.root / "pyproject.toml").write_bytes(b"\xff\xfe\x00binary")
        self.assertFalse(self._applies("mypy"))


class PyrightPositionTest(unittest.TestCase):
    """Pyright counts from zero; everything the agent reads counts from one."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.spec = linter_table()["pyright"]

    def _diagnostics(self, payload: dict):
        return _parse_json_rows(
            self.spec, "pyright", json.dumps(payload), self.root, "app.py"
        )

    @staticmethod
    def _payload(line: int = 4, character: int = 16) -> dict:
        return {
            "generalDiagnostics": [
                {
                    "file": "app.py",
                    "severity": "error",
                    "message": 'Type "int" is not assignable to declared type "str"',
                    "range": {
                        "start": {"line": line, "character": character},
                        "end": {"line": line, "character": character + 11},
                    },
                    "rule": "reportAssignmentType",
                }
            ]
        }

    def test_the_reported_line_is_one_based(self) -> None:
        """Unshifted, every diagnostic points one line above the real problem."""
        diagnostic = self._diagnostics(self._payload())[0]
        self.assertEqual(diagnostic.line, 5)
        self.assertEqual(diagnostic.col, 17)

    def test_the_end_of_the_range_shifts_too(self) -> None:
        diagnostic = self._diagnostics(self._payload())[0]
        self.assertEqual(diagnostic.end_line, 5)
        self.assertEqual(diagnostic.end_col, 28)

    def test_the_first_line_of_a_file_stays_line_one(self) -> None:
        diagnostic = self._diagnostics(self._payload(line=0, character=0))[0]
        self.assertEqual((diagnostic.line, diagnostic.col), (1, 1))

    def test_the_rule_becomes_the_code(self) -> None:
        self.assertEqual(self._diagnostics(self._payload())[0].code, "reportAssignmentType")

    def test_the_file_key_is_honoured(self) -> None:
        """Pyright names the field `file`; the default would have missed it."""
        payload = self._payload()
        payload["generalDiagnostics"][0]["file"] = str(self.root / "pkg" / "mod.py")
        self.assertEqual(self._diagnostics(payload)[0].path, "pkg/mod.py")

    def test_a_one_based_linter_is_left_alone(self) -> None:
        """The shift must be opt-in, or every other JSON linter drifts by one."""
        spec = dict(linter_table()["shellcheck"])
        rows = json.dumps([{"line": 3, "column": 5, "code": 2086, "message": "quote it"}])
        diagnostic = _parse_json_rows(spec, "shellcheck", rows, self.root, "s.sh")[0]
        self.assertEqual((diagnostic.line, diagnostic.col), (3, 5))


class _LiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "app.py").write_text(
            textwrap.dedent("""\
                def somme(a: int, b: int) -> int:
                    return a + b


                resultat: str = somme(1, 2)
            """),
            encoding="utf-8",
        )

    def _project(self) -> str:
        tool = LintTool(
            workspace=str(self.root),
            allowed_dir=str(self.root),
            file_states=FileStates(),
        )
        return _run(tool.execute(action="project"))


class MypyLiveTest(_LiveTest):
    def setUp(self) -> None:
        super().setUp()
        if not shutil.which("mypy"):
            self.skipTest("mypy not installed")
        (self.root / "pyproject.toml").write_text(
            "[tool.mypy]\nwarn_unused_ignores = true\n", encoding="utf-8"
        )

    def test_a_type_error_is_reported_at_the_right_line(self) -> None:
        out = self._project()
        self.assertIn("mypy", out)
        self.assertIn("app.py:5", out)
        self.assertIn("assignment", out)

    def test_a_well_typed_project_is_clean(self) -> None:
        (self.root / "app.py").write_text(
            "def somme(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
        )
        self.assertNotIn("app.py:", self._project())


class PyrightLiveTest(_LiveTest):
    def setUp(self) -> None:
        super().setUp()
        if not shutil.which("pyright"):
            self.skipTest("pyright not installed")
        (self.root / "pyrightconfig.json").write_text(
            '{ "typeCheckingMode": "basic" }\n', encoding="utf-8"
        )

    def test_a_type_error_is_reported_at_the_right_line(self) -> None:
        """The whole point of the shift: pyright would have said line 4."""
        out = self._project()
        self.assertIn("pyright", out)
        self.assertIn("app.py:5", out)
        self.assertNotIn("app.py:4", out)


class TableTest(unittest.TestCase):
    def test_python_has_a_project_scope_checker(self) -> None:
        """The gap that started this: a whole language with no type checking."""
        table = linter_table()
        python_project = [
            name
            for name, spec in table.items()
            if spec.get("language") == "python" and spec.get("scope") == "project"
        ]
        self.assertTrue(python_project)

    def test_every_content_rule_names_a_file_and_a_marker(self) -> None:
        for name, spec in linter_table().items():
            for rule in spec.get("requires_content", []):
                with self.subTest(linter=name):
                    self.assertTrue(rule.get("file"))
                    self.assertTrue(rule.get("contains"))


if __name__ == "__main__":
    unittest.main()
