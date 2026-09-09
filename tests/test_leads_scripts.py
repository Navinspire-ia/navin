# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Leads scoring and enrichment scripts."""

from __future__ import annotations

import csv
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(script: str):
    path = ROOT / "navin" / "skills" / script
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScoreLeadsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load("lead-qualification/scripts/score_leads.py")

    def test_validate_ok_and_score_tiers(self) -> None:
        rows = [
            {
                "company": "Acme",
                "website": "https://acme.example",
                "source": "https://acme.example/about",
                "confidence": "high",
                "fit": "5",
                "need": "4",
                "timing": "4",
                "authority": "5",
                "budget": "3",
            },
            {
                "company": "Beta",
                "website": "https://beta.example",
                "source": "Pappers",
                "confidence": "medium",
                "icp_score": "45",
            },
        ]
        self.assertEqual(self.mod.validate(rows), [])
        score_a, tier_a = self.mod.score_row(rows[0])
        self.assertEqual(tier_a, "A")
        self.assertGreaterEqual(score_a, 70)
        score_b, tier_b = self.mod.score_row(rows[1])
        self.assertEqual(tier_b, "B")
        self.assertEqual(score_b, 45.0)

    def test_validate_rejects_duplicate_domain_and_bad_confidence(self) -> None:
        rows = [
            {
                "company": "Acme",
                "website": "https://acme.example",
                "source": "https://acme.example",
                "confidence": "high",
            },
            {
                "company": "Acme Dup",
                "website": "https://www.acme.example",
                "source": "https://news.example/x",
                "confidence": "maybe",
            },
        ]
        errors = self.mod.validate(rows)
        self.assertTrue(any("duplicate domain" in e for e in errors))
        self.assertTrue(any("confidence" in e for e in errors))

    def test_cli_validate_and_score_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "leads.csv"
            out = Path(tmp) / "leads-scored.csv"
            with src.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "company",
                        "website",
                        "source",
                        "confidence",
                        "icp_score",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "company": "Acme",
                        "website": "https://acme.example",
                        "source": "https://acme.example/team",
                        "confidence": "high",
                        "icp_score": "82",
                    }
                )
            import sys

            old = sys.argv
            try:
                sys.argv = ["score_leads.py", str(src), "-o", str(out)]
                self.mod.main()
            finally:
                sys.argv = old
            self.assertTrue(out.is_file())
            with out.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["tier"], "A")
            self.assertEqual(rows[0]["next_action"], "contact_now")


class EnrichLeadsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load("lead-enrichment/scripts/enrich_leads.py")

    def test_domain_and_name_helpers(self) -> None:
        self.assertEqual(
            self.mod._domain_from_website("https://www.Acme.Example/path"),
            "acme.example",
        )
        self.assertEqual(self.mod._split_name("Ada Lovelace"), ("Ada", "Lovelace"))

    def test_enrich_without_keys_stays_unverified(self) -> None:
        row = {
            "company": "Acme",
            "website": "https://acme.example",
            "person": "Ada Lovelace",
            "contact_hint": "ada.lovelace@acme.example",
        }
        out = self.mod.enrich_row(
            row, hunter_key="", apollo_key="", verify_existing=False
        )
        self.assertEqual(out["email"], "ada.lovelace@acme.example")
        self.assertEqual(out["email_status"], "unverified")
        self.assertEqual(out["enrichment_source"], "none")
        self.assertEqual(out["domain"], "acme.example")

    def test_enrich_uses_hunter_finder_when_mocked(self) -> None:
        calls: list[str] = []

        def fake_http(url: str, **kwargs):
            calls.append(url)
            return {
                "data": {
                    "email": "ada@acme.example",
                    "score": 95,
                    "verification": {"status": "valid"},
                }
            }

        self.mod._http_json = fake_http  # type: ignore[method-assign]
        out = self.mod.enrich_row(
            {
                "company": "Acme",
                "website": "acme.example",
                "person": "Ada Lovelace",
            },
            hunter_key="test-key",
            apollo_key="",
            verify_existing=False,
        )
        self.assertEqual(out["email"], "ada@acme.example")
        self.assertEqual(out["email_status"], "verified")
        self.assertEqual(out["enrichment_source"], "hunter_email_finder")
        self.assertTrue(any("email-finder" in u for u in calls))

    def test_keys_check_exit_codes(self) -> None:
        import sys

        old_argv = sys.argv
        old_env = os.environ.copy()
        try:
            os.environ.pop("HUNTER_API_KEY", None)
            os.environ.pop("APOLLO_API_KEY", None)
            sys.argv = ["enrich_leads.py", "--keys-check"]
            with self.assertRaises(SystemExit) as missing:
                self.mod.main()
            self.assertEqual(missing.exception.code, 2)

            os.environ["HUNTER_API_KEY"] = "x"
            sys.argv = ["enrich_leads.py", "--keys-check"]
            with self.assertRaises(SystemExit) as present:
                self.mod.main()
            self.assertEqual(present.exception.code, 0)
        finally:
            sys.argv = old_argv
            os.environ.clear()
            os.environ.update(old_env)


class LeadsModuleWiringTests(unittest.TestCase):
    def test_leads_brief_includes_real_hunt_stack(self) -> None:
        from navin.command.builtin import _WORKFLOW_BRIEFS
        from navin.command.modules import is_command_allowed_for_module

        title, skills, brief = _WORKFLOW_BRIEFS["/leads"]
        self.assertIn("Leads", title)
        for name in (
            "lead-enrichment",
            "crm-update-agent",
            "deep-web-research",
            "web-extractor",
            "lead-prospector",
        ):
            self.assertIn(name, skills)
        self.assertIn("enrich_leads.py", brief)
        self.assertIn("scrape", brief)
        self.assertTrue(is_command_allowed_for_module("/scrape", "leads"))
        self.assertTrue(is_command_allowed_for_module("/leads", "leads"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "leads"))


if __name__ == "__main__":
    unittest.main()
