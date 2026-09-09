# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 1.1: index-backed related_files for Tab completions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from navin.webui import assist_api


class ResolveRelatedFilesTest(unittest.TestCase):
    def test_returns_import_neighbors_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helpers.py").write_text(
                "def next_value():\n    return 1\n", encoding="utf-8"
            )
            (root / "app.py").write_text(
                "from helpers import next_value\n\ndef run():\n    return next_value()\n",
                encoding="utf-8",
            )
            from navin.index import get_index

            index = get_index(root)
            index.ensure()
            related = assist_api.resolve_related_files(root, "app.py", limit=4)
            paths = {item["path"] for item in related}
            self.assertIn("helpers.py", paths)
            self.assertTrue(any("next_value" in item["content"] for item in related))

    def test_accepts_absolute_editor_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib.py").write_text("X = 1\n", encoding="utf-8")
            (root / "main.py").write_text("import lib\n", encoding="utf-8")
            from navin.index import get_index

            get_index(root).ensure()
            related = assist_api.resolve_related_files(root, str(root / "main.py"))
            self.assertEqual(related[0]["path"], "lib.py")

    def test_missing_index_returns_empty(self) -> None:
        self.assertEqual(
            assist_api.resolve_related_files("/no/such/project", "a.py"),
            [],
        )


class CompletionUsesProjectRootTest(unittest.IsolatedAsyncioTestCase):
    async def test_project_root_auto_injects_related_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helpers.py").write_text("def tip():\n    pass\n", encoding="utf-8")
            (root / "app.py").write_text(
                "from helpers import tip\n\ndef main():\n    ",
                encoding="utf-8",
            )
            from navin.index import get_index

            get_index(root).ensure()
            ask = AsyncMock(return_value=("tip()", "m", "code", "chat"))
            with patch.object(assist_api, "_ask_completion_native_or_chat", ask):
                await assist_api.completion_payload(
                    path="app.py",
                    prefix="def main():\n    ",
                    suffix="\n",
                    project_root=root,
                )
            prompt = ask.await_args.kwargs["user_prompt"]
            self.assertIn("Related files", prompt)
            self.assertIn("helpers.py", prompt)


if __name__ == "__main__":
    unittest.main()
