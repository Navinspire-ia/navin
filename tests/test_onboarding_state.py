# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from navin.webui.chat_defaults import (
    ChatDefaultsError,
    read_chat_defaults,
    update_chat_defaults,
)
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


class ChatDefaultsTests(unittest.TestCase):
    """The model and team a new chat starts with live on the gateway."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch("navin.webui.chat_defaults.get_webui_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nothing_picked_yet(self) -> None:
        self.assertEqual(read_chat_defaults(), {"model_preset": None, "team": None})

    def test_partial_updates_merge_and_null_clears(self) -> None:
        update_chat_defaults({"model_preset": "glm-5-2"})
        update_chat_defaults({"team": " sales "})
        self.assertEqual(read_chat_defaults(), {"model_preset": "glm-5-2", "team": "sales"})
        update_chat_defaults({"model_preset": None})
        self.assertEqual(read_chat_defaults(), {"model_preset": None, "team": "sales"})
        # Atomic write: no temp file is left next to the state.
        self.assertEqual([p.name for p in self.root.iterdir()], ["chat-defaults.json"])

    def test_bad_values_are_refused_not_stored(self) -> None:
        for patch in ({"team": 3}, {"model_preset": "x" * 81}, {"modelPreset": "glm"}):
            with self.subTest(patch=patch), self.assertRaises(ChatDefaultsError):
                update_chat_defaults(patch)
        self.assertFalse((self.root / "chat-defaults.json").exists())

    def test_a_corrupt_file_reads_as_nothing_picked(self) -> None:
        (self.root / "chat-defaults.json").write_text("{oops", encoding="utf-8")
        self.assertEqual(read_chat_defaults(), {"model_preset": None, "team": None})
        # The next pick rewrites it as valid JSON.
        update_chat_defaults({"team": "dev"})
        self.assertEqual(read_chat_defaults()["team"], "dev")

    def test_http_routes(self) -> None:
        from navin.webui.ws_http import GatewayHTTPHandler

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: request.authorized
        handler._log = SimpleNamespace(exception=lambda *a, **k: None)

        def call(path: str, body: object = None, *, authorized: bool = True):
            headers = {}
            if body is not None:
                headers["x-navin-file-body-0"] = base64.b64encode(json.dumps(body).encode()).decode()
            request = SimpleNamespace(path=path, headers=headers, authorized=authorized)
            response = asyncio.run(handler._dispatch_misc_routes(None, request, path))
            return response.status_code, json.loads(response.body) if response.status_code == 200 else None

        self.assertEqual(call("/api/webui/chat-defaults"), (200, {"model_preset": None, "team": None}))
        status, saved = call("/api/webui/chat-defaults/update", {"team": "infra"})
        self.assertEqual((status, saved["team"]), (200, "infra"))
        self.assertEqual(call("/api/webui/chat-defaults")[1]["team"], "infra")
        self.assertEqual(call("/api/webui/chat-defaults/update", {"team": 1})[0], 400)
        self.assertEqual(call("/api/webui/chat-defaults/update", ["team"])[0], 400)
        self.assertEqual(call("/api/webui/chat-defaults/update")[0], 400)
        self.assertEqual(call("/api/webui/chat-defaults", authorized=False)[0], 401)
        self.assertEqual(call("/api/webui/chat-defaults/update", {"team": "x"}, authorized=False)[0], 401)
        self.assertEqual(read_chat_defaults()["team"], "infra")


if __name__ == "__main__":
    unittest.main()
