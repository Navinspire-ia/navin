# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Sidebar state remembers which module owns each chat."""

from __future__ import annotations

import unittest

from navin.webui.sidebar_state import normalize_webui_sidebar_state


class SidebarModuleByKeyTest(unittest.TestCase):
    def test_normalizes_code_alias_to_dev(self) -> None:
        state = normalize_webui_sidebar_state(
            {
                "module_by_key": {
                    "websocket:1": "code",
                    "websocket:2": "montage",
                    "websocket:3": "nope",
                }
            }
        )
        self.assertEqual(state["module_by_key"]["websocket:1"], "dev")
        self.assertEqual(state["module_by_key"]["websocket:2"], "montage")
        self.assertNotIn("websocket:3", state["module_by_key"])

    def test_keeps_chat_order_list(self) -> None:
        state = normalize_webui_sidebar_state(
            {"chat_order": ["websocket:2", "websocket:1", "websocket:2", ""]}
        )
        self.assertEqual(state["chat_order"], ["websocket:2", "websocket:1"])

    def test_keeps_studio_desks_including_tenders_and_career(self) -> None:
        state = normalize_webui_sidebar_state(
            {
                "module_by_key": {
                    "websocket:new": "chat",
                    "websocket:code": "dev",
                    "websocket:tenders": "tenders",
                    "websocket:career": "career",
                    "websocket:trading": "trading",
                    "websocket:marketing": "marketing",
                    "websocket:leads": "leads",
                    "websocket:crm": "crm",
                },
                "module_by_path": {
                    "/tmp/tenders-job": "tenders",
                    "/tmp/career-job": "career",
                    "/tmp/crm-job": "crm",
                },
            }
        )
        self.assertEqual(state["module_by_key"]["websocket:new"], "chat")
        self.assertEqual(state["module_by_key"]["websocket:code"], "dev")
        self.assertEqual(state["module_by_key"]["websocket:tenders"], "tenders")
        self.assertEqual(state["module_by_key"]["websocket:career"], "career")
        self.assertEqual(state["module_by_key"]["websocket:trading"], "trading")
        self.assertEqual(state["module_by_key"]["websocket:marketing"], "marketing")
        self.assertEqual(state["module_by_key"]["websocket:leads"], "leads")
        self.assertEqual(state["module_by_key"]["websocket:crm"], "crm")
        self.assertEqual(state["module_by_path"]["/tmp/tenders-job"], "tenders")
        self.assertEqual(state["module_by_path"]["/tmp/career-job"], "career")
        self.assertEqual(state["module_by_path"]["/tmp/crm-job"], "crm")


if __name__ == "__main__":
    unittest.main()
