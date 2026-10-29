# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security catalog and finding model: the reference data every engine reads."""

from __future__ import annotations

import unittest

from navin.security import catalog
from navin.security.model import (
    Evidence,
    Finding,
    finding_from_dict,
    merge_findings,
    risk_score,
    severity_counts,
    sort_findings,
    status_counts,
)


class CatalogShapeTest(unittest.TestCase):
    def test_counts_match_the_reference(self):
        totals = catalog.catalog_payload()["totals"]
        self.assertEqual(totals["families"], 18)
        self.assertEqual(totals["controls"], 70)
        self.assertEqual(totals["groups"], 7)
        self.assertEqual(totals["frameworks"], 14)
        self.assertEqual(totals["levels"], 5)
        self.assertEqual(totals["skills"], 20)

    def test_every_group_has_ten_controls(self):
        for group in catalog.CONTROL_GROUPS:
            self.assertEqual(len(catalog.controls_for_group(group.id)), 10, group.id)

    def test_ids_are_unique_and_numbered(self):
        self.assertEqual(len(set(catalog.FAMILY_IDS)), 18)
        self.assertEqual(len(set(catalog.CONTROL_IDS)), 70)
        self.assertEqual([f.number for f in catalog.FAMILIES], list(range(1, 19)))

    def test_controls_reference_known_families_levels_and_skills(self):
        for item in catalog.CONTROLS:
            self.assertTrue(item.families, item.id)
            for fid in item.families:
                self.assertIn(fid, catalog.FAMILY_IDS, f"{item.id} -> {fid}")
            self.assertIn(item.level, catalog.LEVEL_IDS, item.id)
            self.assertIn(item.skill, catalog.SKILL_IDS, item.id)
            self.assertIn(item.detection, {"static", "dynamic", "architecture", "adversarial", "continuous"})
            self.assertTrue(item.scanners, item.id)
            self.assertTrue(item.references, item.id)

    def test_every_family_is_covered_by_a_control_and_a_skill(self):
        for fam in catalog.FAMILIES:
            self.assertTrue(catalog.controls_for_family(fam.id), fam.id)
            self.assertIn(fam.skill, catalog.SKILL_IDS, fam.id)

    def test_labels_ship_in_three_languages(self):
        for item in catalog.CONTROLS:
            self.assertEqual(set(item.label), {"fr", "en", "ar"}, item.id)
            for text in item.label.values():
                self.assertTrue(text.strip(), item.id)
        for fam in catalog.FAMILIES:
            self.assertEqual(set(fam.name), {"fr", "en", "ar"})
        for fw in catalog.FRAMEWORKS:
            self.assertEqual(set(fw.purpose), {"fr", "en", "ar"})
            self.assertTrue(fw.url.startswith("https://"))

    def test_no_long_dashes_anywhere(self):
        blob = str(catalog.catalog_payload())
        self.assertNotIn("\u2014", blob)
        self.assertNotIn("\u2013", blob)

    def test_levels_are_cumulative(self):
        l1 = catalog.controls_up_to_level("L1")
        l3 = catalog.controls_up_to_level("L3")
        l5 = catalog.controls_up_to_level("L5")
        self.assertTrue(set(c.id for c in l1) < set(c.id for c in l3) < set(c.id for c in l5))
        self.assertEqual(len(l5), 70)
        self.assertTrue(all(c.level == "L1" for c in l1))

    def test_lookups_raise_on_unknown_ids(self):
        with self.assertRaises(KeyError):
            catalog.family("nope")
        with self.assertRaises(KeyError):
            catalog.control("C9.99")
        with self.assertRaises(KeyError):
            catalog.level("L9")
        self.assertEqual(catalog.families_for_controls(["C2.07", "C2.09"]), ["authorization", "api", "multitenancy"])

    def test_checklist_markdown_counts_progress(self):
        text = catalog.checklist_markdown({"C1.01", "C1.02", "C9.99"}, lang="fr")
        self.assertIn("2/70", text)
        self.assertIn("## 1. Code source et execution (2/10)", text)
        self.assertIn("- [x] C1.01 Injection SQL/NoSQL/LDAP/XPath", text)
        self.assertIn("- [ ] C7.10", text)
        english = catalog.checklist_markdown(set(), lang="xx")
        self.assertIn("# Navin Security audit checklist", english)


class FindingModelTest(unittest.TestCase):
    def test_from_dict_and_round_trip(self):
        item = finding_from_dict(
            {
                "id": "sqli-fstring",
                "family": "injection",
                "title": "SQL built with an f-string",
                "severity": "HIGH",
                "cwe": "CWE-89",
                "file": "app/views.py",
                "line": "12",
                "confidence": 0.7,
                "evidence": [{"kind": "static", "description": "rule match", "tool": "navin_rules"}],
            }
        )
        self.assertEqual(item.severity, "high")
        self.assertEqual(item.line, 12)
        self.assertEqual(item.status, "potential")
        data = item.to_dict()
        self.assertEqual(data["fingerprint"], item.fingerprint())
        self.assertEqual(data["evidence"][0]["tool"], "navin_rules")

    def test_statuses_only_go_up(self):
        item = Finding(id="x", family="authorization", title="IDOR", status="confirmed")
        item.promote("potential")
        self.assertEqual(item.status, "confirmed")
        item.add_evidence(Evidence(kind="reproduction", description="cross-tenant read", request="GET /api/invoices/123"))
        self.assertEqual(item.status, "exploitable")
        with self.assertRaises(ValueError):
            item.promote("proven")
        with self.assertRaises(ValueError):
            Evidence(kind="guess", description="nope")

    def test_invalid_level_or_impact_is_refused(self):
        with self.assertRaises(ValueError):
            Finding(id="x", family="api", title="t", level="L7")
        with self.assertRaises(ValueError):
            Finding(id="x", family="api", title="t", impact="annoying")

    def test_merge_keeps_strongest_and_raises_confidence_on_agreement(self):
        a = Finding(id="r1", family="injection", title="SQLi", severity="medium", cwe="CWE-89",
                    file_path="app/db.py", line=10, source="navin_rules", confidence=0.5)
        b = Finding(id="r2", family="injection", title="SQLi (taint)", severity="high", cwe="CWE-89",
                    file_path="app/db.py", line=12, source="navin_dataflow", confidence=0.6,
                    evidence=[Evidence(kind="dataflow", description="request.args -> cursor.execute")])
        c = Finding(id="r3", family="secrets", title="token", severity="low", file_path="cfg.py", line=1)
        merged = merge_findings([a, b, c])
        self.assertEqual(len(merged), 2)
        top = merged[0]
        self.assertEqual(top.severity, "high")
        self.assertEqual(top.status, "confirmed")
        self.assertIn("multi-source", top.tags)
        self.assertAlmostEqual(top.confidence, 0.75)
        self.assertEqual(len(top.evidence), 1)

    def test_merge_of_the_same_object_twice_ends_and_keeps_one_evidence(self):
        # Regression: the same Finding listed twice appended its evidence to
        # itself while iterating it, never ended and filled the machine's memory.
        item = Finding(id="r1", family="injection", title="SQLi", cwe="CWE-89", file_path="a.py", line=1,
                       evidence=[Evidence(kind="static", description="match")])
        merged = merge_findings([item, item, item])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].evidence), 1)
        self.assertNotIn("multi-source", merged[0].tags)

    def test_sort_and_counts_and_score(self):
        items = [
            Finding(id="a", family="api", title="a", severity="low"),
            Finding(id="b", family="api", title="b", severity="critical", status="exploitable"),
            Finding(id="c", family="api", title="c", severity="high", status="confirmed"),
        ]
        ordered = sort_findings(items)
        self.assertEqual([f.id for f in ordered], ["b", "c", "a"])
        self.assertEqual(severity_counts(items)["critical"], 1)
        self.assertEqual(status_counts(items), {"potential": 1, "confirmed": 1, "exploitable": 1})
        score, grade = risk_score(items)
        self.assertEqual((score, grade), (57, "D"))
        self.assertEqual(risk_score([]), (100, "A"))


class LegacyBridgeTest(unittest.TestCase):
    def test_model_family_ids_match_the_catalog(self):
        from navin.security.model import _FAMILY_IDS

        self.assertEqual(_FAMILY_IDS, set(catalog.FAMILY_IDS))
        with self.assertRaises(ValueError):
            Finding(id="x", family="nonsense", title="t")

    def test_cwe_beats_category_and_unknown_falls_back_to_infrastructure(self):
        from navin.security.bridge import family_for

        self.assertEqual(family_for("Misconfiguration", "CWE-89"), "injection")
        self.assertEqual(family_for("Kubernetes"), "infrastructure")
        self.assertEqual(family_for("Request forgery"), "network")
        self.assertEqual(family_for("whatever", "CWE-9999"), "infrastructure")

    def test_legacy_rows_become_merged_findings(self):
        from navin.security.bridge import merge_legacy

        rows = [
            {"id": "sql-fstring", "severity": "high", "category": "Injection", "cwe": "CWE-89",
             "summary": "SQL f-string", "file": "a.py", "line": 10, "snippet": "execute(f'..')",
             "recommendation": "parametrize", "confidence": "high"},
            {"id": "sql-concat", "severity": "medium", "category": "Injection", "cwe": "CWE-89",
             "summary": "SQL concat", "file": "a.py", "line": 12},
        ]
        merged = merge_legacy(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].family, "injection")
        self.assertEqual(merged[0].severity, "high")
        self.assertEqual(merged[0].remediation, "parametrize")
        self.assertEqual(merged[0].evidence[0].kind, "static")


if __name__ == "__main__":
    unittest.main()
