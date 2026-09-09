# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Format Document backend: formatter routing and stdin formatting.

VS Code and Cursor route Format Document to real formatters (Prettier, ruff,
gofmt, rustfmt) because most language servers do not format at all. These
tests pin the routing table and exercise the one formatter guaranteed in this
environment (ruff ships with navin itself).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import WorkspaceSandboxStatus, WorkspaceScope
from navin.webui.format_api import FormatApiError, format_payload, formatter_argv


def _scope(root: Path) -> WorkspaceScope:
    return WorkspaceScope(
        project_path=root,
        access_mode="restricted",
        restrict_to_workspace=True,
        sandbox_status=WorkspaceSandboxStatus(
            restrict_to_workspace=True,
            workspace_root=str(root),
            level="workspace",
            enforced=True,
            provider="none",
            provider_label="None",
            summary="test",
        ),
    )


class FormatterRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_python_routes_to_ruff(self) -> None:
        picked = formatter_argv("src/app.py", self.root)
        assert picked is not None
        argv, tool = picked
        self.assertEqual(tool, "ruff")
        self.assertIn("format", argv)
        self.assertIn("--stdin-filename", argv)
        self.assertEqual(argv[-1], "-")

    def test_go_and_rust_route_to_their_own_formatters(self) -> None:
        if shutil.which("gofmt"):
            picked = formatter_argv("main.go", self.root)
            assert picked is not None
            self.assertEqual(picked[1], "gofmt")
        if shutil.which("rustfmt"):
            picked = formatter_argv("lib.rs", self.root)
            assert picked is not None
            self.assertEqual(picked[1], "rustfmt")

    def test_project_prettier_wins_over_global(self) -> None:
        local = self.root / "node_modules" / ".bin"
        local.mkdir(parents=True)
        (local / "prettier").write_text("#!/bin/sh\ncat\n")
        picked = formatter_argv("src/App.tsx", self.root)
        assert picked is not None
        argv, tool = picked
        self.assertEqual(tool, "prettier")
        self.assertEqual(argv[0], str(local / "prettier"))
        self.assertIn("--stdin-filepath", argv)

    def test_unknown_extension_has_no_formatter(self) -> None:
        self.assertIsNone(formatter_argv("photo.png", self.root))
        self.assertIsNone(formatter_argv("Makefile", self.root))


class FormatPayloadTest(unittest.TestCase):
    """End-to-end through ruff, the formatter bundled with navin."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app.py").write_text("x=1\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_formats_a_python_buffer_without_touching_disk(self) -> None:
        payload = format_payload(
            _scope(self.root), path="app.py", content="x=1\ny =  2\n"
        )
        self.assertEqual(payload["tool"], "ruff")
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["formatted"], "x = 1\ny = 2\n")
        # The buffer travels with the request; the file itself is untouched.
        self.assertEqual((self.root / "app.py").read_text(), "x=1\n")

    def test_already_formatted_reports_unchanged(self) -> None:
        payload = format_payload(
            _scope(self.root), path="app.py", content="x = 1\n"
        )
        self.assertFalse(payload["changed"])

    def test_a_syntax_error_refuses_with_the_formatter_message(self) -> None:
        with self.assertRaises(FormatApiError) as caught:
            format_payload(_scope(self.root), path="app.py", content="def broken(:\n")
        self.assertEqual(caught.exception.status, 422)

    def test_an_unsupported_file_type_is_a_422(self) -> None:
        with self.assertRaises(FormatApiError) as caught:
            format_payload(_scope(self.root), path="notes.xyz", content="hello")
        self.assertEqual(caught.exception.status, 422)
        self.assertIn(".xyz", caught.exception.message)

    def test_a_path_outside_the_project_is_rejected(self) -> None:
        from navin.webui.lsp_api import LspApiError

        with self.assertRaises(LspApiError):
            format_payload(_scope(self.root), path="../escape.py", content="x=1\n")


if __name__ == "__main__":
    unittest.main()
