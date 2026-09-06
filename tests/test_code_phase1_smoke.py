"""Phase 0/1 smoke: Code happy-path APIs used by the workbench.

No browser - exercises the same backend surfaces the editor calls:
assist sanitize, related-file resolution, outline, and model route preference.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from navin.webui import assist_api, symbols_api


def _scope(root: Path) -> SimpleNamespace:
    return SimpleNamespace(project_path=root)


class CodePhase1SmokeTest(unittest.TestCase):
    def test_assist_preset_prefers_code_routes(self) -> None:
        class FakeConfig:
            model_routes = {
                "code": "tab-model",
                "fast": "chat-fast",
                "dev": "chat-dev",
            }

        with patch("navin.config.loader.load_config", return_value=FakeConfig()):
            self.assertEqual(assist_api._assist_preset(), "tab-model")

    def test_fim_sanitize_and_related_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "util.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
            (root / "calc.py").write_text(
                "from util import add\n\ndef total(xs):\n    return ",
                encoding="utf-8",
            )
            from navin.index import get_index

            get_index(root).ensure()
            related = assist_api.resolve_related_files(root, "calc.py")
            self.assertGreaterEqual(len(related), 1)
            self.assertEqual(related[0]["path"], "util.py")
            completion = assist_api.finalize_completion(
                "return ",
                "\n",
                "```python\nadd(xs)\n```",
            )
            self.assertEqual(completion, "add(xs)")

    def test_outline_payload_lists_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mod.py").write_text(
                "def alpha():\n    return 1\n\nclass Beta:\n    def method(self):\n        pass\n",
                encoding="utf-8",
            )
            payload = symbols_api.outline_payload(_scope(root), path="mod.py")
            names = {item.get("name") for item in payload.get("items") or []}
            self.assertTrue({"alpha", "Beta"} & names)


if __name__ == "__main__":
    unittest.main()
