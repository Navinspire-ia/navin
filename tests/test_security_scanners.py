# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security scanner registry: one parser test per adapter on a recorded
report, and run tests on fake binaries for every way a run can end."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.security import catalog
from navin.security.model import merge_findings
from navin.security.scanners import (
    REGISTRY,
    adapters,
    available_scanner_ids,
    detect_scanners,
    get_scanner,
    run_scanner,
    run_scanner_async,
)
from navin.security.scanners.registry import relative_paths

# The AWS documentation example key, built at run time so secret scanners and
# push protection never read a literal access key id in this file.
AWS_EXAMPLE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def _trivy_report(target: str = "requirements.txt") -> dict:
    return {
        "SchemaVersion": 2,
        "Results": [
            {
                "Target": target, "Class": "lang-pkgs", "Type": "pip",
                "Vulnerabilities": [{
                    "VulnerabilityID": "CVE-2024-34064", "PkgName": "jinja2", "InstalledVersion": "3.1.3",
                    "FixedVersion": "3.1.4", "Severity": "MEDIUM", "CweIDs": ["CWE-79"],
                    "Title": "jinja2: accepts keys containing non-attribute characters",
                    "CVSS": {"ghsa": {"V3Vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N", "V3Score": 5.4}},
                    "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2024-34064",
                }],
            },
            {
                "Target": "Dockerfile", "Class": "config", "Type": "dockerfile",
                "Misconfigurations": [
                    {"ID": "DS002", "AVDID": "AVD-DS-0002", "Title": "Image user should not be 'root'",
                     "Message": "Specify at least 1 USER command in Dockerfile with non-root user as argument",
                     "Resolution": "Add 'USER <non root user name>' line to the Dockerfile", "Severity": "HIGH",
                     "Status": "FAIL", "References": ["https://avd.aquasec.com/misconfig/ds002"],
                     "CauseMetadata": {"StartLine": 1, "EndLine": 1}},
                    {"ID": "DS001", "Title": "':latest' tag used", "Severity": "MEDIUM", "Status": "PASS"},
                ],
            },
            {
                "Target": ".env", "Class": "secret",
                "Secrets": [{"RuleID": "github-pat", "Category": "GitHub", "Severity": "CRITICAL",
                             "Title": "GitHub Personal Access Token", "StartLine": 2, "EndLine": 2,
                             "Match": "GITHUB_TOKEN=****************"}],
            },
        ],
    }


def _osv_report(manifest: str) -> dict:
    return {"results": [{
        "source": {"path": manifest, "type": "lockfile"},
        "packages": [
            {"package": {"name": "jinja2", "version": "3.1.3", "ecosystem": "PyPI"},
             "vulnerabilities": [{"id": "GHSA-h75v-3vvj-5mfj", "aliases": ["CVE-2024-34064"],
                                  "summary": "Jinja vulnerable to HTML attribute injection in the xmlattr filter",
                                  "database_specific": {"severity": "MODERATE", "cwe_ids": ["CWE-79"]}}],
             "groups": [{"ids": ["GHSA-h75v-3vvj-5mfj"], "max_severity": "5.4"}]},
            {"package": {"name": "requests", "version": "2.31.0", "ecosystem": "PyPI"},
             "vulnerabilities": [{"id": "GHSA-9wx4-h78v-vm56", "aliases": ["CVE-2024-35195"],
                                  "summary": "Requests Session keeps verify=False for later requests"}],
             "groups": [{"ids": ["GHSA-9wx4-h78v-vm56"], "max_severity": "5.6"}]},
        ],
    }]}


class AdapterTest(unittest.TestCase):
    """Each parser on a report recorded from the real tool."""

    def test_semgrep_maps_cwe_and_infers_the_family_without_one(self):
        doc = {"results": [
            {"check_id": "python.lang.security.audit.formatted-sql-query.formatted-sql-query",
             "path": "app/db.py", "start": {"line": 12, "col": 5},
             "extra": {"message": "Detected possible formatted SQL query. Use parameterized queries instead.",
                       "severity": "WARNING", "lines": "cur.execute(f\"SELECT * FROM t WHERE id={uid}\")",
                       "metadata": {"cwe": ["CWE-89: Improper Neutralization of Special Elements used in an SQL Command"],
                                    "owasp": ["A03:2021 - Injection"], "confidence": "HIGH",
                                    "references": ["https://owasp.org/Top10/A03_2021-Injection"]}}},
            {"check_id": "python.jwt.security.unverified-jwt-decode.unverified-jwt-decode",
             "path": "app/auth.py", "start": {"line": 3},
             "extra": {"message": "JWT decoded without verification", "severity": "ERROR", "metadata": {}}},
        ], "errors": []}
        sql, jwt = adapters.parse_semgrep(doc)
        self.assertEqual((sql.family, sql.cwe, sql.severity, sql.line), ("injection", "CWE-89", "medium", 12))
        self.assertEqual(sql.owasp, "A03:2021 - Injection")
        self.assertEqual(sql.confidence, 0.85)
        self.assertEqual(sql.evidence[0].location, "app/db.py:12")
        self.assertIn("cur.execute", sql.evidence[0].observed)
        self.assertEqual((jwt.family, jwt.severity, jwt.cwe), ("authentication", "high", ""))

    def test_gitleaks_never_copies_the_secret(self):
        doc = [{"Description": "AWS Access Key", "StartLine": 4, "File": "config/settings.py",
                "Match": f'aws_key = "{AWS_EXAMPLE_KEY}"', "Secret": AWS_EXAMPLE_KEY,
                "RuleID": "aws-access-token", "Commit": "", "Entropy": 3.6}]
        [item] = adapters.parse_gitleaks(doc)
        self.assertEqual((item.family, item.cwe, item.severity, item.line), ("secrets", "CWE-798", "high", 4))
        dumped = json.dumps(item.to_dict())
        self.assertNotIn(AWS_EXAMPLE_KEY, dumped)
        self.assertIn("****", item.evidence[0].observed)

    def test_trivy_reads_vulnerabilities_misconfigurations_and_secrets(self):
        vuln, mis, secret = adapters.parse_trivy(_trivy_report())
        self.assertEqual((vuln.family, vuln.cve, vuln.cwe, vuln.severity), ("supply_chain", "CVE-2024-34064", "CWE-79", "medium"))
        self.assertEqual((vuln.component, vuln.cvss, vuln.file_path), ("jinja2@3.1.3", 5.4, "requirements.txt"))
        self.assertIn("3.1.4", vuln.remediation)
        self.assertEqual((mis.family, mis.id, mis.severity, mis.line), ("infrastructure", "trivy:DS002", "high", 1))
        self.assertIn("USER", mis.remediation)
        self.assertEqual((secret.family, secret.severity, secret.file_path), ("secrets", "critical", ".env"))

    def test_osv_scanner_takes_the_cve_alias_and_the_group_score(self):
        jinja, requests = adapters.parse_osv_scanner(_osv_report("requirements.txt"))
        self.assertEqual((jinja.id, jinja.cve, jinja.severity, jinja.cvss), ("GHSA-h75v-3vvj-5mfj", "CVE-2024-34064", "medium", 5.4))
        self.assertEqual(jinja.cwe, "CWE-79")
        # No severity word: the CVSS score of the group decides.
        self.assertEqual((requests.severity, requests.cvss), ("medium", 5.6))
        self.assertIn("No fixed version", requests.remediation)

    def test_bandit_maps_issue_cwe_to_the_family(self):
        doc = {"errors": [], "results": [
            {"code": "6 subprocess.call(cmd, shell=True)\n", "filename": "app/run.py", "issue_confidence": "HIGH",
             "issue_cwe": {"id": 78, "link": "https://cwe.mitre.org/data/definitions/78.html"}, "issue_severity": "HIGH",
             "issue_text": "subprocess call with shell=True identified, security issue.", "line_number": 6,
             "more_info": "https://bandit.readthedocs.io/en/latest/plugins/b602_subprocess_popen_with_shell_equals_true.html",
             "test_id": "B602", "test_name": "subprocess_popen_with_shell_equals_true"},
            {"filename": "app/settings.py", "issue_confidence": "MEDIUM", "issue_cwe": {"id": 259, "link": ""},
             "issue_severity": "LOW", "issue_text": "Possible hardcoded password: 'admin'", "line_number": 3,
             "test_id": "B105", "test_name": "hardcoded_password_string"},
        ]}
        shell, password = adapters.parse_bandit(doc)
        self.assertEqual((shell.id, shell.family, shell.cwe, shell.severity, shell.confidence), ("bandit:B602", "injection", "CWE-78", "high", 0.8))
        self.assertEqual(shell.references[0][:8], "https://")
        self.assertEqual((password.family, password.severity), ("secrets", "low"))

    def test_pip_audit_reads_both_report_shapes_and_skips_unaudited(self):
        deps = [
            {"name": "flask", "version": "0.5", "vulns": [{"id": "PYSEC-2019-179", "fix_versions": ["1.0"],
             "aliases": ["CVE-2019-1010083", "GHSA-5wv5-4vpf-pj6m"],
             "description": "The Pallets Project Flask before 1.0 is affected by: unexpected memory usage."}]},
            {"name": "jinja2", "version": "3.1.4", "vulns": []},
            {"name": "navin", "skip_reason": "Dependency not found on PyPI and could not be audited"},
        ]
        for doc in ({"dependencies": deps, "fixes": []}, deps):
            [item] = adapters.parse_pip_audit(doc)
            self.assertEqual((item.id, item.cve, item.component, item.source), ("PYSEC-2019-179", "CVE-2019-1010083", "flask@0.5", "pip_audit"))
            self.assertIn("(1.0)", item.remediation)
            self.assertIn("GHSA-5wv5-4vpf-pj6m", item.tags)

    def test_npm_audit_reports_the_advisory_once_and_refuses_an_error_report(self):
        doc = {"auditReportVersion": 2, "vulnerabilities": {
            "minimist": {"name": "minimist", "severity": "critical", "isDirect": False,
                         "via": [{"source": 1096056, "name": "minimist", "dependency": "minimist",
                                  "title": "Prototype Pollution in minimist", "severity": "critical",
                                  "url": "https://github.com/advisories/GHSA-xvch-5gv4-984h", "cwe": ["CWE-1321"],
                                  "cvss": {"score": 9.8, "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
                                  "range": "<0.2.4"}],
                         "effects": ["mkdirp"], "range": "<=0.2.3", "fixAvailable": True},
            # Only vulnerable through minimist: no advisory of its own.
            "mkdirp": {"name": "mkdirp", "severity": "critical", "isDirect": True, "via": ["minimist"],
                       "range": "0.4.1 - 0.5.1", "fixAvailable": {"name": "mkdirp", "version": "0.5.6"}},
        }}
        [item] = adapters.parse_npm_audit(doc)
        self.assertEqual((item.id, item.family, item.cwe, item.severity, item.cvss), ("GHSA-xvch-5gv4-984h", "supply_chain", "CWE-1321", "critical", 9.8))
        self.assertEqual((item.component, item.file_path), ("minimist", "package-lock.json"))
        self.assertIn("<0.2.4", item.evidence[0].description)
        self.assertIn("npm audit fix", item.remediation)
        # npm names the direct dependency to bump when the flaw is deeper.
        doc["vulnerabilities"]["minimist"]["fixAvailable"] = {"name": "mkdirp", "version": "1.0.4", "isSemVerMajor": True}
        [bumped] = adapters.parse_npm_audit(doc)
        self.assertTrue(bumped.remediation.startswith("Upgrade mkdirp to 1.0.4: it pulls a fixed minimist."), bumped.remediation)
        self.assertIn("major version", bumped.remediation)
        doc["vulnerabilities"]["minimist"]["fixAvailable"] = False
        self.assertIn("No fixed version", adapters.parse_npm_audit(doc)[0].remediation)
        with self.assertRaisesRegex(adapters.ReportError, "registry.npmjs.org"):
            adapters.parse_npm_audit({"error": {"code": "ENOTFOUND", "summary": "request to https://registry.npmjs.org failed"}})

    def test_cargo_audit_scores_the_cvss_vector(self):
        doc = {"database": {"advisory-count": 600}, "vulnerabilities": {"found": True, "count": 1, "list": [{
            "advisory": {"id": "RUSTSEC-2020-0071", "package": "time", "title": "Potential segfault in the time crate",
                         "aliases": ["CVE-2020-26235", "GHSA-wcg3-cvx6-7396"],
                         "cvss": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:H",
                         "url": "https://github.com/time-rs/time/issues/293"},
            "versions": {"patched": [">=0.2.23"], "unaffected": ["=0.2.0"]},
            "package": {"name": "time", "version": "0.1.45"},
        }]}, "warnings": {}}
        [item] = adapters.parse_cargo_audit(doc)
        self.assertEqual((item.id, item.cve, item.cvss, item.severity), ("RUSTSEC-2020-0071", "CVE-2020-26235", 5.9, "medium"))
        self.assertEqual((item.component, item.file_path), ("time@0.1.45", "Cargo.lock"))
        self.assertIn(">=0.2.23", item.remediation)

    def test_cvss3_base_score_matches_the_specification(self):
        self.assertEqual(adapters.cvss3_base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"), 9.8)
        self.assertEqual(adapters.cvss3_base_score("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:H"), 5.9)
        self.assertEqual(adapters.cvss3_base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N"), 6.1)
        self.assertEqual(adapters.cvss3_base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N"), 0.0)
        for bad in ("", "AV:N/AC:L", "CVSS:2.0/AV:N/AC:L/Au:N/C:P/I:P/A:P", "CVSS:3.1/AV:N/AC:L", "CVSS:3.1/AV:Z/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"):
            self.assertIsNone(adapters.cvss3_base_score(bad), bad)
        self.assertEqual(adapters.severity_from_cvss(None, "high"), "high")


class DependencyMergeTest(unittest.TestCase):
    def test_two_advisories_in_one_lockfile_never_collapse(self):
        first = adapters.dependency_finding("trivy", advisory="CVE-2021-44906", package="minimist", version="1.2.5",
                                            manifest="package-lock.json", cwe="CWE-1321")
        second = adapters.dependency_finding("trivy", advisory="CVE-2022-46175", package="json5", version="2.2.1",
                                             manifest="package-lock.json", cwe="CWE-1321")
        self.assertEqual(len(merge_findings([first, second])), 2)

    def test_the_same_cve_from_trivy_and_osv_merges_into_one_multi_source_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            osv = relative_paths(adapters.parse_osv_scanner(_osv_report(str(root / "requirements.txt"))), root)
        self.assertEqual(osv[0].file_path, "requirements.txt")
        trivy = [f for f in adapters.parse_trivy(_trivy_report()) if f.family == "supply_chain"]
        merged = merge_findings([*trivy, *osv])
        self.assertEqual(len(merged), 2)
        jinja = next(f for f in merged if f.component.startswith("jinja2"))
        self.assertIn("multi-source", jinja.tags)
        self.assertEqual(len(jinja.evidence), 2)


class RegistryTest(unittest.TestCase):
    def test_every_scanner_is_described_and_planned_by_the_catalog(self):
        self.assertEqual(
            set(REGISTRY),
            {"semgrep", "gitleaks", "trivy", "osv_scanner", "bandit", "pip_audit", "npm_audit", "cargo_audit"},
        )
        l1_engines = set(catalog.level("L1").engines)
        for sid, spec in REGISTRY.items():
            info = spec.describe()
            self.assertTrue(info["license"], sid)
            self.assertTrue(info["homepage"].startswith("https://"), sid)
            self.assertTrue(info["install_hint"], sid)
            self.assertTrue(set(spec.families) <= set(catalog.FAMILY_IDS), sid)
            self.assertTrue(info["controls"], f"{sid} covers no catalog control")
            self.assertIn(sid, l1_engines)
            self.assertIn(0, spec.ok_exit_codes, sid)
        self.assertTrue(get_scanner("npm_audit").permissions.network)
        self.assertTrue(get_scanner("gitleaks").permissions.writes_report)
        with self.assertRaisesRegex(KeyError, "unknown scanner"):
            get_scanner("nessus")

    def test_pip_audit_always_audits_the_project_not_its_own_environment(self):
        spec = get_scanner("pip_audit")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            command = spec.command("pip-audit", root)
            self.assertEqual(command[-1], str(root))
            self.assertNotIn("-r", command)
            (root / "requirements.txt").write_text("flask==0.5\n", encoding="utf-8")
            command = spec.command("pip-audit", root)
            self.assertEqual(command[1:3], ["-r", "requirements.txt"])
            self.assertIn("json", command)


@unittest.skipIf(os.name == "nt", "fake scanners are POSIX scripts")
class RunScannerTest(unittest.TestCase):
    """Every way a run ends, on fake binaries that behave like the real tools."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name, "project").resolve()
        self.bin = Path(tmp.name, "bin")
        self.root.mkdir()
        self.bin.mkdir()
        patcher = mock.patch.dict(os.environ, {"PATH": str(self.bin)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\nimport json, sys, time\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def test_a_missing_binary_is_reported_never_a_clean_run(self):
        run = run_scanner("semgrep", self.root)
        self.assertEqual((run.ok, run.status, run.findings), (False, "not_installed", []))
        self.assertIn("pipx install semgrep", run.detail)
        self.assertEqual(asyncio.run(run_scanner_async("semgrep", self.root)).status, "not_installed")

    def test_a_project_without_the_manifest_is_not_applicable(self):
        self.fake("npm", "print('{}')")
        run = run_scanner("npm_audit", self.root)
        self.assertEqual(run.status, "not_applicable")
        self.assertIn("package-lock.json", run.detail)

    def test_semgrep_findings_come_back_relative_and_linked_to_controls(self):
        self.fake("semgrep", (
            "root = sys.argv[-1]\n"
            "print(json.dumps({'results': [{'check_id': 'py.sql', 'path': root + '/app/db.py', 'start': {'line': 3},"
            " 'extra': {'message': 'SQL built with an f-string', 'severity': 'ERROR',"
            " 'metadata': {'cwe': ['CWE-89: SQL Injection']}}}], 'errors': []}))\n"
            "sys.exit(1)"
        ))
        run = run_scanner("semgrep", self.root)
        self.assertEqual((run.ok, run.status, run.exit_code), (True, "ok", 1))
        [item] = run.findings
        self.assertEqual((item.file_path, item.evidence[0].location), ("app/db.py", "app/db.py:3"))
        self.assertIn("C1.01", item.controls)
        self.assertEqual(run.command[1:4], ["scan", "--config", "p/default"])
        self.assertEqual(run.to_dict()["findings"][0]["fingerprint"], item.fingerprint())

    def test_gitleaks_report_is_redacted_read_and_deleted(self):
        self.fake("gitleaks", (
            "report = sys.argv[sys.argv.index('--report-path') + 1]\n"
            "open(report, 'w').write(json.dumps([{'RuleID': 'generic-api-key', 'File': 'a.env', 'StartLine': 1,"
            " 'Match': 'KEY=REDACTED', 'Secret': 'REDACTED', 'Description': 'Generic API Key'}]))"
        ))
        run = run_scanner("gitleaks", self.root)
        self.assertEqual(run.status, "ok", run.detail)
        self.assertIn("--redact", run.command)
        report = Path(run.command[run.command.index("--report-path") + 1])
        self.assertFalse(report.exists())
        self.assertEqual([f.family for f in run.findings], ["secrets"])

    def test_an_unexpected_exit_code_fails_with_the_tool_error(self):
        (self.root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        self.fake("bandit", "sys.stderr.write('bandit: error: unrecognized arguments')\nsys.exit(2)")
        run = run_scanner("bandit", self.root)
        self.assertEqual((run.status, run.exit_code), ("failed", 2))
        self.assertIn("unrecognized arguments", run.detail)

    def test_unreadable_reports_are_invalid_output(self):
        self.fake("trivy", "print('Downloading vulnerability DB...')")
        self.assertEqual(run_scanner("trivy", self.root).status, "invalid_output")
        self.fake("trivy", "print(json.dumps([1, 2]))")
        run = run_scanner("trivy", self.root)
        self.assertEqual(run.status, "invalid_output")
        self.assertIn("unexpected report shape", run.detail)

    def test_an_empty_report_is_a_failure(self):
        (self.root / "Cargo.lock").write_text("version = 3\n", encoding="utf-8")
        self.fake("cargo-audit", "sys.stderr.write('error: could not fetch advisory database')")
        run = run_scanner("cargo_audit", self.root)
        self.assertEqual(run.status, "failed")
        self.assertIn("advisory database", run.detail)
        self.assertEqual(run.command[1:3], ["audit", "--json"])

    def test_npm_error_document_is_a_failure_not_zero_findings(self):
        (self.root / "package-lock.json").write_text("{}", encoding="utf-8")
        self.fake("npm", (
            "print(json.dumps({'error': {'code': 'ENOTFOUND', 'summary': 'request to https://registry.npmjs.org failed'}}))\n"
            "sys.exit(1)"
        ))
        run = run_scanner("npm_audit", self.root)
        self.assertEqual(run.status, "failed")
        self.assertIn("npm audit failed", run.detail)

    def test_osv_scanner_without_lockfile_is_not_applicable(self):
        self.fake("osv-scanner", "sys.stderr.write('No package sources found')\nsys.exit(128)")
        run = run_scanner("osv_scanner", self.root)
        self.assertEqual((run.status, run.exit_code), ("not_applicable", 128))

    def test_a_scanner_that_hangs_is_stopped_at_the_timeout(self):
        self.fake("osv-scanner", "time.sleep(30)")
        run = run_scanner("osv_scanner", self.root, timeout_s=1)
        self.assertEqual(run.status, "timeout")
        self.assertLess(run.duration_s, 10)

    def test_detection_reads_the_version_and_feeds_the_planner(self):
        self.fake("trivy", "print('Version: 0.56.2')")
        statuses = detect_scanners(["trivy", "semgrep"])
        self.assertEqual((statuses["trivy"].available, statuses["trivy"].version), (True, "0.56.2"))
        self.assertEqual((statuses["semgrep"].available, statuses["semgrep"].error), (False, "not_installed"))
        self.assertEqual(available_scanner_ids(statuses), {"trivy"})


if __name__ == "__main__":
    unittest.main()
