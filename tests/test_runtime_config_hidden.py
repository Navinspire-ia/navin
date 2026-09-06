"""The live config.json must not open in Code or leak through file tools."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools.filesystem import ReadFileTool
from navin.config.secrets import is_secret_leaf
from navin.security.workspace_access import default_workspace_scope
from navin.webui.file_preview import WebUIFilePreviewError, file_preview_payload
from navin.webui.file_tree import file_tree_payload


def _run(coro) -> str:
    result = asyncio.run(coro)
    return str(getattr(result, "content", result))


class RuntimeConfigHiddenTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.navin = self.root / ".navin"
        self.navin.mkdir()
        self.config = self.navin / "config.json"
        self.config.write_text(
            '{"channels":{"websocket":{"tokenIssueSecret":"must-not-leak"}}}\n',
            encoding="utf-8",
        )
        self.key = self.navin / ".config-key"
        self.key.write_text("not-a-real-key\n", encoding="utf-8")
        (self.root / "readme.txt").write_text("ok\n", encoding="utf-8")
        self.scope = default_workspace_scope(self.root, True)
        self.addCleanup(self._tmp.cleanup)

    def test_token_issue_secret_is_a_secret_leaf(self) -> None:
        self.assertTrue(is_secret_leaf("tokenIssueSecret"))
        self.assertTrue(is_secret_leaf("token_issue_secret"))

    def test_preview_refuses_live_config(self) -> None:
        with patch("navin.config.loader.get_config_path", return_value=self.config):
            with self.assertRaises(WebUIFilePreviewError) as ctx:
                file_preview_payload(str(self.config), scope=self.scope, allow_any=True)
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("hidden", str(ctx.exception.message).lower())

    def test_preview_refuses_config_key(self) -> None:
        with patch("navin.config.loader.get_config_path", return_value=self.config):
            with self.assertRaises(WebUIFilePreviewError) as ctx:
                file_preview_payload(str(self.key), scope=self.scope, allow_any=True)
        self.assertEqual(ctx.exception.status, 403)

    def test_preview_still_opens_project_files(self) -> None:
        with patch("navin.config.loader.get_config_path", return_value=self.config):
            payload = file_preview_payload("readme.txt", scope=self.scope, allow_any=True)
        self.assertIn("ok", payload["content"])

    def test_tree_hides_live_config_files(self) -> None:
        with patch("navin.config.loader.get_config_path", return_value=self.config):
            payload = file_tree_payload(str(self.navin), scope=self.scope)
        names = {entry["name"] for entry in payload["entries"]}
        self.assertNotIn("config.json", names)
        self.assertNotIn(".config-key", names)

    def test_read_file_refuses_live_config(self) -> None:
        tool = ReadFileTool(
            workspace=self.root,
            allowed_dir=self.root,
            restrict_to_workspace=True,
        )
        with patch("navin.config.loader.get_config_path", return_value=self.config):
            out = _run(tool.execute(path=str(self.config)))
        self.assertIn("Error", out)
        self.assertIn("hidden", out.lower())
        self.assertNotIn("must-not-leak", out)


if __name__ == "__main__":
    unittest.main()
