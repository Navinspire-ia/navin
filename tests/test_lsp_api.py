"""Editor LSP HTTP helpers: hover fallback, definition-at, rename validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.index import get_index
from navin.security.workspace_access import WorkspaceSandboxStatus, WorkspaceScope
from navin.webui.lsp_api import (
    LspApiError,
    code_actions_payload,
    completion_payload,
    definition_at_payload,
    hover_payload,
    references_at_payload,
    rename_payload,
    signature_help_payload,
)


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


class LspApiHelpersTest(unittest.TestCase):
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
        get_index(self.root).ensure()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_hover_falls_back_to_index_when_lsp_is_down(self) -> None:
        with mock.patch(
            "navin.lsp.LspManager.for_root",
            side_effect=RuntimeError("no server"),
        ):
            payload = hover_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=1,
                col=7,
            )
        self.assertEqual(payload["source"], "index")
        self.assertIn("AuthService", payload["contents"])

    def test_hover_stays_silent_when_the_index_only_knows_the_word(self) -> None:
        (self.root / "values.yaml").write_text(
            "resources:\n  requests:\n    cpu: 500m\n",
            encoding="utf-8",
        )
        with mock.patch(
            "navin.lsp.LspManager.for_root",
            side_effect=RuntimeError("no server"),
        ):
            payload = hover_payload(
                _scope(self.root),
                path="values.yaml",
                line=2,
                col=5,
            )
        self.assertEqual(payload["contents"], "")
        self.assertEqual(payload["source"], "none")

    def test_hover_drops_an_lsp_payload_that_only_echoes_the_word(self) -> None:
        (self.root / "values.yaml").write_text(
            "resources:\n  requests:\n    cpu: 500m\n",
            encoding="utf-8",
        )
        manager = mock.Mock()
        manager.hover.return_value = "`requests`"
        with mock.patch("navin.lsp.LspManager.for_root", return_value=manager):
            payload = hover_payload(
                _scope(self.root),
                path="values.yaml",
                line=2,
                col=5,
            )
        self.assertEqual(payload["contents"], "")
        self.assertEqual(payload["source"], "none")

    def test_definition_at_uses_index_fallback(self) -> None:
        with mock.patch(
            "navin.lsp.LspManager.for_root",
            side_effect=RuntimeError("no server"),
        ):
            payload = definition_at_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=1,
                col=7,
            )
        self.assertEqual(payload["source"], "index")
        self.assertGreaterEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["path"], "pkg/auth.py")

    def test_rename_rejects_invalid_new_name(self) -> None:
        with self.assertRaises(LspApiError) as caught:
            rename_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=1,
                col=7,
                new_name="bad-name",
            )
        self.assertEqual(caught.exception.status, 400)

    def test_absolute_path_is_accepted(self) -> None:
        with mock.patch(
            "navin.lsp.LspManager.for_root",
            side_effect=RuntimeError("no server"),
        ):
            payload = hover_payload(
                _scope(self.root),
                path=str(self.root / "pkg" / "auth.py"),
                line=1,
                col=7,
            )
        self.assertEqual(payload["path"], "pkg/auth.py")

    def test_references_fall_back_to_index(self) -> None:
        (self.root / "pkg" / "other.py").write_text(
            "from pkg.auth import AuthService\n\n"
            "def login():\n"
            "    return AuthService()\n",
            encoding="utf-8",
        )
        get_index(self.root).refresh()
        with mock.patch(
            "navin.lsp.LspManager.for_root",
            side_effect=RuntimeError("no server"),
        ):
            payload = references_at_payload(
                _scope(self.root),
                path="pkg/other.py",
                line=4,
                col=12,
            )
        self.assertEqual(payload["source"], "index")
        self.assertEqual(payload["symbol"], "AuthService")
        self.assertGreaterEqual(payload["total"], 1)

    def test_hover_sends_the_unsaved_editor_buffer_to_lsp(self) -> None:
        manager = mock.Mock()
        manager.hover.return_value = "buffer hover"
        content = "class RenamedBeforeSave:\n    pass\n"
        with mock.patch("navin.lsp.LspManager.for_root", return_value=manager):
            payload = hover_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=1,
                col=7,
                content=content,
            )
        self.assertEqual(payload["source"], "lsp")
        manager.hover.assert_called_once_with(
            "pkg/auth.py",
            1,
            7,
            text=content,
        )

    def test_unsaved_content_drives_identifier_fallback(self) -> None:
        content = "value = BrandNewUnsavedSymbol()\n"
        with mock.patch(
            "navin.lsp.LspManager.for_root",
            side_effect=RuntimeError("no server"),
        ):
            payload = references_at_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=1,
                col=12,
                content=content,
            )
        self.assertEqual(payload["symbol"], "BrandNewUnsavedSymbol")

    def test_completion_uses_the_language_server(self) -> None:
        manager = mock.Mock()
        manager.completion.return_value = [
            {"label": "validate", "kind": "method", "insert_text": "validate"}
        ]
        with mock.patch("navin.lsp.LspManager.for_root", return_value=manager):
            payload = completion_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=2,
                col=9,
                content="class AuthService:\n    def val\n",
                trigger=".",
            )
        self.assertEqual(payload["source"], "lsp")
        self.assertEqual(payload["items"][0]["label"], "validate")
        manager.completion.assert_called_once()

    def test_signature_help_uses_the_language_server(self) -> None:
        manager = mock.Mock()
        manager.signature_help.return_value = {
            "active_signature": 0,
            "active_parameter": 0,
            "signatures": [{"label": "validate(token)", "parameters": []}],
        }
        with mock.patch("navin.lsp.LspManager.for_root", return_value=manager):
            payload = signature_help_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=2,
                col=14,
            )
        self.assertEqual(payload["source"], "lsp")
        self.assertEqual(payload["signatures"][0]["label"], "validate(token)")

    def test_code_actions_use_the_language_server(self) -> None:
        manager = mock.Mock()
        manager.code_actions.return_value = [
            {"title": "Add annotation", "kind": "quickfix", "edits": {}}
        ]
        with mock.patch("navin.lsp.LspManager.for_root", return_value=manager):
            payload = code_actions_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=1,
                col=1,
            )
        self.assertEqual(payload["source"], "lsp")
        self.assertEqual(payload["items"][0]["title"], "Add annotation")

    def test_editor_queries_are_empty_when_lsp_is_down(self) -> None:
        with mock.patch(
            "navin.lsp.LspManager.for_root",
            side_effect=RuntimeError("no server"),
        ):
            completion = completion_payload(
                _scope(self.root), path="pkg/auth.py", line=1, col=1
            )
            signature = signature_help_payload(
                _scope(self.root), path="pkg/auth.py", line=1, col=1
            )
            actions = code_actions_payload(
                _scope(self.root), path="pkg/auth.py", line=1, col=1
            )
        self.assertEqual(completion["source"], "none")
        self.assertEqual(signature["signatures"], [])
        self.assertEqual(actions["items"], [])

    def test_workspace_rename_requires_save_before_apply(self) -> None:
        with self.assertRaises(LspApiError) as caught:
            rename_payload(
                _scope(self.root),
                path="pkg/auth.py",
                line=1,
                col=7,
                new_name="RenamedAuth",
                apply=True,
                content="class UnsavedAuth:\n    pass\n",
            )
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("save", caught.exception.message)


class DebugVerifyWorkflowTest(unittest.TestCase):
    def test_debug_requires_verify_and_repro_clause(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from navin.command.builtin import (
            _CODE_VERIFY_WORKFLOWS,
            _DEBUG_REPRO_VERIFY_CLAUSE,
            _workflow_handler,
        )
        from navin.command.modules import REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY

        self.assertIn("/debug", _CODE_VERIFY_WORKFLOWS)
        msg = SimpleNamespace(content="", metadata={}, channel="cli", chat_id="t")
        ctx = SimpleNamespace(
            args="fix the crash",
            raw="/debug fix the crash",
            msg=msg,
            loop=None,
        )
        asyncio.run(_workflow_handler("/debug")(ctx))  # type: ignore[arg-type]
        self.assertTrue(msg.metadata.get(REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY))
        self.assertIn(_DEBUG_REPRO_VERIFY_CLAUSE, msg.content)


if __name__ == "__main__":
    unittest.main()
