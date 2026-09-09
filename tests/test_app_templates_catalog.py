# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""First-party app template catalog: unique slugs, licenses, /create mapping."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from navin.templates.apps import (
    APP_TEMPLATES,
    UNIVERSAL_AGENTS,
    get_app_template,
    is_publishable,
    list_app_templates,
    template_slug_for_create_app,
)


class AppTemplatesCatalogTest(unittest.TestCase):
    def test_catalog_has_unique_slugs_and_permissive_licenses(self):
        slugs = [row["slug"] for row in APP_TEMPLATES]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertGreaterEqual(len(APP_TEMPLATES), 47)
        for row in APP_TEMPLATES:
            self.assertIn(row["license"], {"MIT", "Apache-2.0"})
            self.assertTrue(row["source_github"].startswith("https://github.com/"))
            self.assertEqual(row["universal_agents"], UNIVERSAL_AGENTS)
            self.assertGreaterEqual(len(row["domain_agents"]), 2)
            self.assertLessEqual(len(row["domain_agents"]), 6)
            self.assertEqual(row["status"], "source")
            self.assertIn(row["audit_status"], {"pending", "in_review", "passed", "rejected"})
            if row["slug"] == "crm":
                self.assertEqual(row["audit_status"], "in_review")
            else:
                self.assertEqual(row["audit_status"], "pending")
            self.assertFalse(is_publishable(row))
            playbook = Path(__file__).resolve().parents[1] / "navin" / "templates" / "apps" / "packages" / row["slug"] / "install.json"
            self.assertTrue(playbook.is_file(), msg=f"missing install.json for {row['slug']}")
            data = json.loads(playbook.read_text(encoding="utf-8"))
            for key in ("system", "tools", "packages", "databases", "env", "install", "modify", "agents"):
                self.assertIn(key, data, msg=f"{row['slug']} install.json missing {key}")
            self.assertTrue(data["tools"], msg=f"{row['slug']} has no tools")
            self.assertTrue(data["install"]["steps"], msg=f"{row['slug']} has no install steps")
            self.assertTrue(data["env"], msg=f"{row['slug']} has no env keys")
            self.assertTrue(data["databases"], msg=f"{row['slug']} has no databases")
            self.assertTrue(data["packages"].get("all"), msg=f"{row['slug']} has no packages")
            env_keys = {item["key"] for item in data["env"]}
            self.assertNotIn("FIGMA_TOKEN", env_keys, msg=f"{row['slug']} picked docs-site env")
            self.assertFalse(
                any(key.startswith("CLAUDE_") for key in env_keys),
                msg=f"{row['slug']} leaked Claude hook env",
            )

    def test_curated_playbooks_are_runnable(self):
        ecommerce = json.loads(
            (Path(__file__).resolve().parents[1] / "navin" / "templates" / "apps" / "packages" / "ecommerce" / "install.json").read_text()
        )
        engines = {item["engine"] for item in ecommerce["databases"]}
        self.assertIn("postgres", engines)
        self.assertIn("redis", engines)
        self.assertTrue(ecommerce["packages"]["frontend"])
        self.assertTrue(ecommerce["packages"]["backend"])
        stock = json.loads(
            (Path(__file__).resolve().parents[1] / "navin" / "templates" / "apps" / "packages" / "stock-pilot" / "install.json").read_text()
        )
        self.assertTrue(stock["packages"]["frontend"])
        self.assertTrue(stock["packages"]["backend"])
        self.assertGreaterEqual(len(stock["env"]), 10)
        crm = json.loads(
            (Path(__file__).resolve().parents[1] / "navin" / "templates" / "apps" / "packages" / "crm" / "install.json").read_text()
        )
        self.assertEqual({item["engine"] for item in crm["databases"]}, {"supabase"})

    def test_get_and_filters(self):
        crm = get_app_template("crm")
        self.assertIsNotNone(crm)
        assert crm is not None
        self.assertEqual(crm["source_name"], "Atomic CRM")
        self.assertEqual(crm["create_app_id"], "crm")
        self.assertIsNone(get_app_template("missing"))
        featured = list_app_templates(featured_only=True)
        self.assertTrue(all(row["featured"] for row in featured))
        self.assertTrue(any(row["slug"] == "crm" for row in featured))
        ai = list_app_templates(kind="ai-product")
        self.assertTrue(all(row["kind"] == "ai-product" for row in ai))
        self.assertIn(get_app_template("ai-chat"), ai)

    def test_create_app_mapping(self):
        self.assertEqual(template_slug_for_create_app("crm"), "crm")
        self.assertEqual(template_slug_for_create_app("chatbot"), "ai-chat")
        self.assertIsNone(template_slug_for_create_app("unknown-app"))


if __name__ == "__main__":
    unittest.main()
