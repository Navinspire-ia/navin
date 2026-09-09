# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for structured security scan helpers and agent tool surface."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.security.findings import dedupe_findings, normalize_finding, severity_counts
from navin.security.poc import enrich_findings_with_poc, poc_for_finding
from navin.security.report_html import render_security_report_html, write_security_report
from navin.security.scan import run_security_scan, scan_heuristics


class FindingsSchemaTest(unittest.TestCase):
    def test_normalize_and_dedupe(self):
        a = normalize_finding(
            {
                "id": "shell-true",
                "severity": "high",
                "message": "shell=True",
                "file": "a.py",
                "line": 10,
            }
        )
        b = normalize_finding(
            {
                "id": "shell-true",
                "severity": "critical",
                "summary": "shell=True",
                "file_path": "a.py",
                "line": 12,
            }
        )
        merged = dedupe_findings([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["severity"], "critical")
        self.assertEqual(severity_counts(merged)["critical"], 1)


class HeuristicScanTest(unittest.TestCase):
    def test_detects_shell_true_and_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text(
                'import subprocess\n'
                'subprocess.run("ls", shell=True)\n'
                'password = "super-secret-value-99"\n',
                encoding="utf-8",
            )
            findings = scan_heuristics(root)
            ids = {f["id"] for f in findings}
            self.assertIn("shell-true", ids)
            self.assertIn("hardcoded-secret", ids)

    def test_run_security_scan_includes_poc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.py").write_text("pickle.loads(data)\n", encoding="utf-8")
            result = run_security_scan(root, kind="quick", max_findings=50)
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["finding_count"], 1)
            first = result["findings"][0]
            self.assertTrue(first.get("poc_sketch"))
            self.assertTrue(first.get("malicious_input_example"))


class PocAndReportTest(unittest.TestCase):
    def test_poc_for_sql_injection(self):
        poc = poc_for_finding(
            {"id": "sql-injection", "file_path": "db.py", "line": 42}
        )
        self.assertIn("OR 1=1", poc["malicious_input_example"])
        self.assertIn("db.py:42", poc["poc_sketch"])

    def test_html_report_contains_finding_and_plan(self):
        findings = enrich_findings_with_poc(
            [
                normalize_finding(
                    {
                        "id": "shell-true",
                        "severity": "high",
                        "summary": "subprocess shell=True",
                        "file_path": "run.py",
                        "line": 3,
                        "recommendation": "Use argv list.",
                    }
                )
            ]
        )
        doc = render_security_report_html(
            findings=findings,
            root="/tmp/proj",
            kind="quick",
            tools=[{"tool": "heuristic", "available": True, "count": 1}],
        )
        self.assertIn("Security review", doc)
        self.assertIn("shell-true", doc)
        self.assertIn("Remediation plan", doc)
        self.assertIn("Start with #N", doc)
        self.assertIn("Real example", doc)
        self.assertIn("Deliverables", doc)
        self.assertIn("@media print", doc)
        self.assertNotIn("\u2014", doc)

    def test_write_security_report_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_security_report(
                root,
                findings=[
                    {
                        "id": "inner-html",
                        "severity": "medium",
                        "summary": "XSS sink",
                        "file_path": "ui.tsx",
                        "line": 10,
                        "recommendation": "Sanitize.",
                    }
                ],
                kind="sast",
            )
            self.assertTrue(path.is_file())
            self.assertTrue(path.name.startswith("security-report-"))
            text = path.read_text(encoding="utf-8")
            self.assertIn("XSS sink", text)
            self.assertIn("onerror", text)


class SecurityScanToolImportTest(unittest.TestCase):
    def test_tool_registers(self):
        from navin.agent.tools.loader import ToolLoader

        discovered = {cls.__name__ for cls in ToolLoader().discover()}
        self.assertIn("SecurityScanTool", discovered)
        from navin.agent.tools.security_scan import SecurityScanTool

        self.assertEqual(SecurityScanTool().name, "security_scan")
        self.assertIn("write_report", SecurityScanTool().description)


if __name__ == "__main__":
    unittest.main()
