# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HubSpot + Salesforce MCP presets for the Leads studio."""

from __future__ import annotations

import unittest
from pathlib import Path

import navin.agent.skills  # noqa: F401
from navin.command.builtin import _WORKFLOW_BRIEFS
from navin.webui.mcp_presets_api import (
    MCP_PRESETS,
    McpPresetError,
    _materialize_server,
    mcp_presets_payload,
)

ROOT = Path(__file__).resolve().parents[1]


class SalesforceMcpPresetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.by_name = {preset.name: preset for preset in MCP_PRESETS}

    def test_salesforce_registered_under_sales_next_to_hubspot(self) -> None:
        self.assertIn("hubspot", self.by_name)
        self.assertIn("salesforce", self.by_name)
        preset = self.by_name["salesforce"]
        self.assertEqual(preset.category, "sales")
        self.assertEqual(self.by_name["hubspot"].category, "sales")
        self.assertTrue(preset.install_supported)
        self.assertEqual(preset.transport, "stdio")
        self.assertEqual(preset.server.command, "npx")
        self.assertIn("@imazhar101/salesforce-mcp-jsforce", preset.server.args)
        self.assertEqual(preset.server.env.get("SF_READONLY"), "1")

    def test_catalogue_payload_lists_salesforce(self) -> None:
        names = {item["name"] for item in mcp_presets_payload()["presets"]}
        self.assertIn("salesforce", names)
        self.assertIn("hubspot", names)

    def test_materialize_requires_token_and_instance(self) -> None:
        with self.assertRaises(McpPresetError):
            _materialize_server(self.by_name["salesforce"], {}, None)
        with self.assertRaises(McpPresetError):
            _materialize_server(
                self.by_name["salesforce"],
                {"sf_access_token": ["00DTOKEN"]},
                None,
            )
        cfg = _materialize_server(
            self.by_name["salesforce"],
            {
                "sf_access_token": ["00DTOKEN"],
                "sf_instance_url": ["https://acme.my.salesforce.com"],
            },
            None,
        )
        self.assertEqual(cfg.env["SF_ACCESS_TOKEN"], "00DTOKEN")
        self.assertEqual(cfg.env["SF_INSTANCE_URL"], "https://acme.my.salesforce.com")
        self.assertEqual(cfg.env.get("SF_READONLY"), "1")

    def test_readonly_can_be_lifted(self) -> None:
        cfg = _materialize_server(
            self.by_name["salesforce"],
            {
                "sf_access_token": ["00DTOKEN"],
                "sf_instance_url": ["https://acme.my.salesforce.com"],
                "sf_readonly": ["0"],
            },
            None,
        )
        self.assertEqual(cfg.env["SF_READONLY"], "0")


class CrmSkillAndDocsTest(unittest.TestCase):
    def test_crm_skill_names_salesforce_preset(self) -> None:
        text = (ROOT / "navin/skills/crm-update-agent/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`salesforce`", text)
        self.assertIn("SF_ACCESS_TOKEN", text)
        self.assertIn("HubSpot", text)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)

    def test_leads_brief_mentions_salesforce_mcp(self) -> None:
        _title, _skills, brief = _WORKFLOW_BRIEFS["/leads"]
        self.assertIn("Salesforce MCP", brief)
        self.assertIn("HubSpot MCP", brief)

    def test_leads_docs_list_salesforce_preset(self) -> None:
        paths = (
            ROOT / "docs/navin_leads/en/README.md",
            ROOT / "docs/navin_leads/fr/README.md",
            ROOT / "docs/navin_leads/en/skills.md",
            ROOT / "docs/navin_leads/fr/skills.md",
            ROOT / "site/front/content/docs/navin_leads/en/README.md",
            ROOT / "site/front/content/docs/navin_leads/en/skills.md",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIn("Salesforce", text)
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)


if __name__ == "__main__":
    unittest.main()
