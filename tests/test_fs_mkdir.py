# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Creating project folders from the sidebar (+dossier).

The folder must be usable from every host the picker can browse - Linux, WSL,
Windows drives, macOS volumes - so the name validation enforces the union of
their rules, not just the rules of the OS the gateway happens to run on.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.webui.fs_browse import (
    FsBrowseError,
    fs_mkdir_payload,
    validate_new_folder_name,
)


class FolderNameValidationTests(unittest.TestCase):
    def test_accepts_ordinary_names(self) -> None:
        for name in ("mon-projet", "Projet 2026", "app_v2", "été.notes"):
            self.assertEqual(validate_new_folder_name(name), name)

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(validate_new_folder_name("  demo  "), "demo")

    def test_rejects_empty_and_dot_names(self) -> None:
        for name in ("", "   ", ".", ".."):
            with self.assertRaises(FsBrowseError):
                validate_new_folder_name(name)

    def test_rejects_path_separators_and_windows_characters(self) -> None:
        for name in ("a/b", "a\\b", "a:b", "a?b", "a*b", 'a"b', "a<b", "a|b"):
            with self.assertRaises(FsBrowseError):
                validate_new_folder_name(name)

    def test_rejects_windows_traps(self) -> None:
        # Trailing dot is silently stripped by NTFS; CON & co are reserved
        # device names, with or without an extension. A trailing space is not
        # rejected but normalized away by the initial strip().
        for name in ("demo.", "CON", "con", "Con.backup", "LPT1"):
            with self.assertRaises(FsBrowseError):
                validate_new_folder_name(name)
        self.assertEqual(validate_new_folder_name("demo "), "demo")


class FsMkdirTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_creates_under_default_parent(self) -> None:
        payload = fs_mkdir_payload("demo", "", default_parent=str(self.root))
        self.assertTrue(payload["created"])
        self.assertEqual(payload["name"], "demo")
        self.assertTrue((self.root / "demo").is_dir())

    def test_explicit_parent_wins_and_missing_parents_are_created(self) -> None:
        parent = self.root / "nested" / "projects"
        payload = fs_mkdir_payload("demo", str(parent), default_parent=str(self.root))
        self.assertEqual(Path(payload["path"]), parent / "demo")
        self.assertTrue((parent / "demo").is_dir())

    def test_existing_folder_is_reused_not_an_error(self) -> None:
        (self.root / "demo").mkdir()
        payload = fs_mkdir_payload("demo", "", default_parent=str(self.root))
        self.assertFalse(payload["created"])
        self.assertEqual(Path(payload["path"]), self.root / "demo")

    def test_existing_file_with_that_name_is_a_conflict(self) -> None:
        (self.root / "demo").write_text("not a folder", encoding="utf-8")
        with self.assertRaises(FsBrowseError) as ctx:
            fs_mkdir_payload("demo", "", default_parent=str(self.root))
        self.assertEqual(ctx.exception.status, 409)

    def test_rejects_relative_parent_and_missing_parent(self) -> None:
        with self.assertRaises(FsBrowseError):
            fs_mkdir_payload("demo", "relative/path")
        with self.assertRaises(FsBrowseError):
            fs_mkdir_payload("demo", "", default_parent=None)

    def test_rejects_navin_internal_parent(self) -> None:
        internal = Path.home() / ".navin" / "sessions"
        with self.assertRaises(FsBrowseError):
            fs_mkdir_payload("demo", str(internal))


class FsMkdirRouteTest(unittest.TestCase):
    """The sidebar +folder button is useless if the route is not wired."""

    def test_mkdir_route_is_registered(self) -> None:
        from navin.webui.ws_http import GatewayHTTPHandler

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True

        class _Scope:
            project_path = Path("/tmp/navin-projects")

        class _Workspaces:
            def default_scope(self) -> _Scope:
                return _Scope()

        handler.workspaces = _Workspaces()

        class _Request:
            path = "/api/webui/fs/mkdir?name=demo"
            headers: dict[str, str] = {}

        with patch(
            "navin.webui.fs_browse.fs_mkdir_payload",
            return_value={"path": "/tmp/navin-projects/demo", "name": "demo", "created": True},
        ):
            response = asyncio.run(
                handler._dispatch_misc_routes(
                    None, _Request(), "/api/webui/fs/mkdir"
                )
            )
        self.assertIsNotNone(response, "route not registered")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
