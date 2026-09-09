# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A project folder carries the module it is worked in.

Picking that folder on the new-chat screen then reopens Code (or Montage, ...)
instead of dropping the user into plain chat with the workbench lost.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from navin.webui.sidebar_state import normalize_webui_sidebar_state

REPO = Path(__file__).resolve().parents[1]
WEBUI = REPO / "webui" / "src"


class FolderModuleTagStateTest(unittest.TestCase):
    def test_normalizes_code_alias_to_dev(self) -> None:
        state = normalize_webui_sidebar_state(
            {
                "module_by_path": {
                    "/home/me/app": "code",
                    "/home/me/clip": "montage",
                    "/home/me/meet": "meeting",
                    "/home/me/junk": "nope",
                }
            }
        )
        self.assertEqual(state["module_by_path"]["/home/me/app"], "dev")
        self.assertEqual(state["module_by_path"]["/home/me/clip"], "montage")
        self.assertEqual(state["module_by_path"]["/home/me/meet"], "meeting")
        self.assertNotIn("/home/me/junk", state["module_by_path"])

    def test_trailing_separator_is_the_same_folder(self) -> None:
        state = normalize_webui_sidebar_state(
            {"module_by_path": {"/home/me/app/": "dev"}}
        )
        self.assertEqual(state["module_by_path"], {"/home/me/app": "dev"})

    def test_windows_paths_key_on_forward_slashes(self) -> None:
        state = normalize_webui_sidebar_state(
            {"module_by_path": {"C:\\work\\app\\": "dev"}}
        )
        self.assertEqual(state["module_by_path"], {"C:/work/app": "dev"})

    def test_internal_storage_is_never_tagged(self) -> None:
        state = normalize_webui_sidebar_state(
            {"module_by_path": {"/home/me/.navin/workspace": "dev"}}
        )
        self.assertEqual(state["module_by_path"], {})

    def test_garbage_shapes_degrade_to_empty(self) -> None:
        self.assertEqual(normalize_webui_sidebar_state({})["module_by_path"], {})
        self.assertEqual(
            normalize_webui_sidebar_state({"module_by_path": ["/home/me/app"]})[
                "module_by_path"
            ],
            {},
        )


class FolderPickerOnNewChatTest(unittest.TestCase):
    """The selector exists as a component; the point is that it is rendered."""

    def test_hero_composer_renders_the_folder_picker(self) -> None:
        composer = (WEBUI / "components" / "thread" / "ThreadComposer.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("WorkspaceProjectPicker", composer)
        self.assertIn("from \"@/components/thread/WorkspaceControls\"", composer)
        # Props used to be swallowed under underscore aliases, which is what
        # left the new-chat screen without any way to choose the folder.
        self.assertNotIn("workspaceScope: _workspaceScope", composer)
        self.assertNotIn("onWorkspaceScopeChange: _onWorkspaceScopeChange", composer)
        self.assertIn("onChange={onWorkspaceScopeChange}", composer)

    def test_picker_only_shows_on_the_new_chat_composer(self) -> None:
        controls = (WEBUI / "components" / "thread" / "WorkspaceControls.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("isHero", controls)
        self.assertIn("controls?.can_change_project !== false", controls)


class LaunchOpensTheTaggedModuleTest(unittest.TestCase):
    def test_new_chat_launch_resolves_the_folder_module(self) -> None:
        module = (WEBUI / "lib" / "chat-module.ts").read_text(encoding="utf-8")
        app = (WEBUI / "App.tsx").read_text(encoding="utf-8")
        # The resolver lives next to viewForCreatedChat so "New chat" can stay
        # in Tchat while a tagged folder still reopens Code / Montage.
        self.assertIn("export function resolveProjectOpenView", module)
        self.assertIn("resolveProjectOpenView", app)
        self.assertIn("sidebarState.module_by_path", app)
        self.assertIn("module_by_path: { ...current.module_by_path", app)

    def test_default_workspace_is_never_tagged(self) -> None:
        """Otherwise every chat with no folder picked would jump into Code."""
        app = (WEBUI / "App.tsx").read_text(encoding="utf-8")
        tagger = app.split("// Tag the folder with the module", 1)[1].split("});", 1)[0]
        # The tag waits for the default scope, then skips it.
        self.assertIn("if (!defaultPath) return;", tagger)
        self.assertIn("sameWorkspacePath(key, defaultPath)", tagger)


if __name__ == "__main__":
    unittest.main()
