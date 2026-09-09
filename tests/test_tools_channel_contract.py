# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tools catalog must match the real channel setup contract."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from navin.channels._setup import CHANNEL_SETUP_SPECS

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "webui/src/components/settings/channels/catalog.ts"


class ToolsChannelContractTest(unittest.TestCase):
    def test_catalog_field_keys_are_writable_setup_fields(self) -> None:
        text = CATALOG.read_text(encoding="utf-8")
        keys = re.findall(r'key:\s*"channels\.([a-z]+)\.([^"]+)"', text)
        self.assertTrue(keys)
        unknown: list[str] = []
        for name, field in keys:
            spec = CHANNEL_SETUP_SPECS.get(name)
            if spec is None or field not in spec.route_field_types:
                unknown.append(f"{name}.{field}")
        self.assertEqual(unknown, [])

    def test_catalog_contains_real_official_urls(self) -> None:
        text = CATALOG.read_text(encoding="utf-8")
        missing: list[str] = []
        for name, spec in CHANNEL_SETUP_SPECS.items():
            if name == "websocket" or not spec.official_url:
                continue
            if spec.official_url not in text:
                missing.append(f"{name}: {spec.official_url}")
        self.assertEqual(missing, [])

    def test_catalog_has_no_private_docs_or_cli_handoff(self) -> None:
        text = CATALOG.read_text(encoding="utf-8")
        self.assertNotIn("EIAGEN/navin-claw", text)
        self.assertNotIn("navin channels", text)
        self.assertNotIn("navin plugins", text)
        self.assertNotIn("bbernhard/signal-cli-rest-api", text)
        self.assertIn("https://github.com/AsamK/signal-cli", text)


if __name__ == "__main__":
    unittest.main()
