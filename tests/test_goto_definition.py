# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Go-to-definition payload for editor F12 / Ctrl-click."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.index import get_index
from navin.security.workspace_access import WorkspaceSandboxStatus, WorkspaceScope
from navin.webui.symbols_api import SymbolsError, goto_definition_payload


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


class GotoDefinitionPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "auth.py").write_text(
            "class AuthService:\n"
            "    def validate(self, token: str) -> bool:\n"
            "        return bool(token)\n",
            encoding="utf-8",
        )
        (self.root / "pkg" / "other.py").write_text(
            "from pkg.auth import AuthService\n\n"
            "def login():\n"
            "    return AuthService()\n",
            encoding="utf-8",
        )
        get_index(self.root).ensure()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolves_class_definition(self) -> None:
        payload = goto_definition_payload(_scope(self.root), symbol="AuthService")
        self.assertEqual(payload["symbol"], "AuthService")
        self.assertGreaterEqual(payload["total"], 1)
        paths = {item["path"] for item in payload["items"]}
        self.assertIn("pkg/auth.py", paths)
        first = payload["items"][0]
        self.assertEqual(first["name"], "AuthService")
        self.assertGreaterEqual(first["line"], 1)

    def test_prefers_definitions_in_the_current_file(self) -> None:
        payload = goto_definition_payload(
            _scope(self.root),
            symbol="AuthService",
            path="pkg/auth.py",
        )
        self.assertEqual(payload["items"][0]["path"], "pkg/auth.py")

    def test_rejects_empty_and_invalid_symbols(self) -> None:
        with self.assertRaises(SymbolsError) as empty:
            goto_definition_payload(_scope(self.root), symbol="  ")
        self.assertEqual(empty.exception.status, 400)
        with self.assertRaises(SymbolsError) as bad:
            goto_definition_payload(_scope(self.root), symbol="foo-bar")
        self.assertEqual(bad.exception.status, 400)

    def test_unknown_symbol_returns_empty_items(self) -> None:
        payload = goto_definition_payload(
            _scope(self.root), symbol="DefinitelyMissingSymbolXYZ"
        )
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)


if __name__ == "__main__":
    unittest.main()
