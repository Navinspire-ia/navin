# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Where a transcript file click opens: chat-side panel vs workbench editor.

The eye button on a file-edit row used to hand every click to the workbench
editor, including from the chat view where that editor is off screen. The file
opened somewhere invisible, so the button read as dead while the download button
next to it worked. These tests pin the routing to what is actually on screen.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.file_preview import file_download_payload, file_preview_payload

WEBUI = Path(__file__).resolve().parents[1] / "webui" / "src"
THREAD_SHELL = WEBUI / "components" / "thread" / "ThreadShell.tsx"
APP = WEBUI / "App.tsx"
MARKDOWN = WEBUI / "components" / "MarkdownTextRenderer.tsx"
CHIP = WEBUI / "components" / "FileReferenceChip.tsx"


class HandoffRequiresAVisibleEditorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.shell = THREAD_SHELL.read_text(encoding="utf-8")
        self.app = APP.read_text(encoding="utf-8")

    def _handler_body(self) -> str:
        match = re.search(
            r"const handleOpenFilePreview = useCallback\((.*?)\n  \}, \[(.*?)\]\);",
            self.shell,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "handleOpenFilePreview not found")
        return match.group(0)

    def test_the_editor_handoff_is_gated_on_the_workbench_being_visible(self) -> None:
        # The gate tightened since: the editor is only a real surface when the
        # visible workbench is the *code* one, hence codeWorkbenchVisible.
        body = self._handler_body()
        self.assertIn("codeWorkbenchVisible", body)
        self.assertRegex(
            body,
            r"if \(onOpenFileInEditor && codeWorkbenchVisible\)",
            "an unguarded handoff sends the file to an editor the user cannot see",
        )

    def test_the_chat_view_still_has_a_visible_surface(self) -> None:
        body = self._handler_body()
        self.assertIn(
            "openFilePreviewPanel(path)",
            body,
            "without the side panel the chat view has nowhere to show the file",
        )

    def test_the_shell_declares_and_defaults_the_visibility_prop(self) -> None:
        self.assertIn("workbenchVisible?: boolean;", self.shell)
        self.assertIn("workbenchVisible = false,", self.shell)

    def test_the_app_reports_real_workbench_visibility(self) -> None:
        # The state variable was renamed (view -> deskView); what matters is
        # that the prop is derived from the live view, not hardcoded.
        self.assertRegex(self.app, r"workbenchVisible=\{isWorkbenchView\(\w+\)\}")

    def test_agent_driven_previews_still_target_the_workbench(self) -> None:
        # open_file_preview and freshly written reports render on the left on
        # purpose - except when the visible workbench is not the code one, in
        # which case the left editor is off screen and the chat panel is the
        # only surface the user can actually see.
        match = re.search(
            r"const openAgentPreview = useCallback\((.*?)\n  \}, \[",
            self.shell,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertIn('onOpenFileInEditor(path, { mode: "preview" })', body)
        self.assertRegex(
            body,
            r"if \(workbenchVisible && !codeWorkbenchVisible\)",
            "a non-code workbench on screen must route agent previews to the "
            "chat panel instead of an invisible editor",
        )


class SidePanelCanRenderWhatDownloadCanFetchTest(unittest.TestCase):
    """The panel and the download button must agree on which files they accept.

    The chip offers both actions on the same row from the same path, so a file
    that downloads must also preview: otherwise the fix above just moves the
    dead button somewhere else.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.scope = default_workspace_scope(self.root, True)
        self.addCleanup(self._tmp.cleanup)

    def test_source_files_from_an_edit_row_preview_and_download(self) -> None:
        for name, body in (
            ("App.tsx", "export const App = () => null;\n"),
            ("index.css", ":root { color: red; }\n"),
            ("main.py", "print('hi')\n"),
        ):
            with self.subTest(name=name):
                (self.root / name).write_text(body, encoding="utf-8")
                # allow_any mirrors the panel's ``any=1`` query.
                preview = file_preview_payload(name, scope=self.scope, allow_any=True)
                self.assertEqual(preview["kind"], "text")
                self.assertIn(body.strip(), preview["content"])
                payload, _content_type, filename, _meta = file_download_payload(
                    name,
                    scope=self.scope,
                )
                self.assertEqual(filename, name)
                self.assertIn(body.strip().encode(), payload)

    def test_a_bare_pytest_name_opens_the_file_under_tests(self) -> None:
        target = self.root / "tests" / "test_best_of.py"
        target.parent.mkdir()
        target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        preview = file_preview_payload("test_best_of.py", scope=self.scope, allow_any=True)
        self.assertEqual(preview["kind"], "text")
        self.assertEqual(preview["display_path"], "tests/test_best_of.py")

    def test_an_absolute_path_works_like_the_chip_sends_it(self) -> None:
        # The row passes ``absolute_path``, not the display path.
        target = self.root / "src" / "App.tsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const App = () => null;\n", encoding="utf-8")
        preview = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(preview["kind"], "text")
        self.assertEqual(preview["display_path"], "src/App.tsx")


class FilenameClickAlwaysOpensPreviewTest(unittest.TestCase):
    """Bare `auth.py` chips used to drop onOpen until a probe succeeded.

    Probe misses (file lives under src/, Mac WebKit click swallowed by the
    tooltip wrapping the eye button) made both the name click and the view
    button look dead. Click and eye must always call the preview handler.
    """

    def test_inferred_chips_keep_on_open_even_when_the_probe_misses(self) -> None:
        source = MARKDOWN.read_text(encoding="utf-8")
        self.assertNotIn(
            "onOpen={onOpen && resolvedAvailable ? onOpen : undefined}",
            source,
        )
        self.assertIn("onOpen={onOpen}", source)

    def test_the_chip_falls_back_to_the_workspace_preview_handler(self) -> None:
        source = CHIP.read_text(encoding="utf-8")
        self.assertIn("onOpen ?? workspace?.onOpenPreview", source)

    def test_action_buttons_are_outside_the_path_tooltip_trigger(self) -> None:
        source = CHIP.read_text(encoding="utf-8")
        actions_at = source.find('data-testid="inline-file-actions"')
        trigger_close = source.find("</TooltipTrigger>")
        self.assertGreater(actions_at, 0)
        self.assertGreater(trigger_close, 0)
        self.assertGreater(
            actions_at,
            trigger_close,
            "wrapping eye/download in TooltipTrigger eats clicks on WebKit",
        )

    def test_the_eye_opens_on_pointer_down_so_the_first_click_is_not_eaten(self) -> None:
        source = CHIP.read_text(encoding="utf-8")
        self.assertIn("onPointerDown={openPreview}", source)
        self.assertIn("onPointerDown={interactive ? openPreview : undefined}", source)


if __name__ == "__main__":
    unittest.main()
