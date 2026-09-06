from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.webui.onboarding_state import (
    normalize_webui_onboarding,
    read_webui_onboarding,
    write_webui_onboarding,
)


class OnboardingStateTests(unittest.TestCase):
    def test_normalize_defaults(self) -> None:
        self.assertEqual(normalize_webui_onboarding(None), {"completed": False})
        self.assertEqual(normalize_webui_onboarding("x"), {"completed": False})

    def test_local_free_paths_are_accepted(self) -> None:
        # The wizard's keyless local flavours of the Free path persist as-is;
        # anything else is dropped rather than stored.
        for path in ("omniroute", "ollama", "free", "byok", "account", "skip"):
            with self.subTest(path=path):
                state = normalize_webui_onboarding({"completed": True, "path": path})
                self.assertEqual(state["path"], path)
        self.assertNotIn(
            "path", normalize_webui_onboarding({"completed": True, "path": "lmstudio"})
        )

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "navin.webui.onboarding_state.get_webui_dir",
                return_value=root,
            ):
                written = write_webui_onboarding(
                    {
                        "completed": True,
                        "completedAt": "2026-08-02T12:00:00Z",
                        "path": "skip",
                        "language": "fr",
                    }
                )
                self.assertTrue(written["completed"])
                self.assertEqual(written["path"], "skip")
                loaded = read_webui_onboarding()
                self.assertEqual(loaded["completed"], True)
                self.assertEqual(loaded["language"], "fr")
                raw = json.loads((root / "onboarding.json").read_text(encoding="utf-8"))
                self.assertTrue(raw["completed"])


if __name__ == "__main__":
    unittest.main()
