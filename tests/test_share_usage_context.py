# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A guest sees the model and the context fill of the shared session."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.session.context_usage_meta import LAST_CONTEXT_USAGE_KEY
from navin.share.context import session_context


class ShareUsageContextTest(unittest.TestCase):
    def _session(self, metadata: dict) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        rows = [
            {"_type": "metadata", "metadata": metadata, "created_at": "", "updated_at": ""},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        handle.write("\n".join(json.dumps(row) for row in rows))
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_model_and_fill_from_the_last_turn(self) -> None:
        path = self._session({
            LAST_CONTEXT_USAGE_KEY: {
                "prompt_tokens": 50_000,
                "peak_prompt_tokens": 50_000,
                "context_window": 1_000_000,
                "model": "moonshotai/kimi-k3",
            },
        })
        context = session_context(path, None)
        usage = context["usage"]
        self.assertEqual(usage["model"], "moonshotai/kimi-k3")
        self.assertEqual(usage["context_window"], 1_000_000)
        self.assertGreater(usage["tokens"], 0)
        self.assertGreaterEqual(usage["percent"], 0)
        self.assertEqual(context["conversation"]["model"], "moonshotai/kimi-k3")

    def test_session_without_usage(self) -> None:
        usage = session_context(self._session({}), None)["usage"]
        self.assertEqual(usage["model"], "")


if __name__ == "__main__":
    unittest.main()
