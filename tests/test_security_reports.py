# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security reports (SARIF, coverage) and remediation (guidance, patch validation)."""

from __future__ import annotations

import asyncio
import difflib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.security import catalog
from navin.security.analyzers import checked_controls
from navin.security.model import Evidence, Finding
from navin.security.remediation import (
    apply_guidance,
    remediation_for,
    validate_patch,
    validate_patch_async,
)
from navin.security.reports import SARIF_SCHEMA, coverage, coverage_markdown, to_sarif
from navin.security.verification import confirm_dataflow

VULNERABLE = (
    "def search(db, term):\n"
    "    query = f\"SELECT * FROM items WHERE name = '{term}'\"\n"
    "    return db.execute(query).fetchall()\n"
)
FIXED = (
    "def search(db, term):\n"
    "    query = \"SELECT * FROM items WHERE name = ?\"\n"
    "    return db.execute(query, (term,)).fetchall()\n"
)
TEST_FILE = "def test_search():\n    assert search_ok()\n    assert no_injection()\n"


def _diff(path: str, old: str, new: str, *, new_file: bool = False, deleted: bool = False) -> str:
    before = "/dev/null" if new_file else f"a/{path}"
    after = "/dev/null" if deleted else f"b/{path}"
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), before, after))


def _sql_finding(path: str = "app/search.py") -> Finding:
    item = Finding(id="navin-dataflow-sql", family="injection", title="SQL injection: user input builds a query", severity="high",
                   cwe="CWE-89", file_path=path, line=3, endpoint="GET /search", controls=["C1.01"], source="navin_dataflow")
    confirm_dataflow(item, [f"{path}:1 source request.args.get('q')", f"{path}:2 query = f'...'", f"{path}:3 sink db.execute(query)"])
    return item


class SarifTest(unittest.TestCase):
    def test_log_shape_rules_locations_and_flows(self):
        sql = _sql_finding("app/my search.py")
        auth = Finding(id="navin-auth-missing", family="authorization", title="No auth", severity="high", cwe="CWE-862",
                       endpoint="POST /orders", controls=["C2.08"], confidence=0.45)
        second_sql = _sql_finding("app/other.py")
        second_sql.severity = "medium"
        with tempfile.TemporaryDirectory() as tmp:
            log = to_sarif([auth, sql, second_sql], root=tmp, tool_version="2.3.15", not_run={"semgrep": "not installed"})
        json.dumps(log)
        self.assertEqual((log["$schema"], log["version"]), (SARIF_SCHEMA, "2.1.0"))
        run = log["runs"][0]
        rules = run["tool"]["driver"]["rules"]
        self.assertEqual([r["id"] for r in rules], ["navin-dataflow-sql", "navin-auth-missing"])  # confirmed sorts first
        self.assertEqual(run["tool"]["driver"]["version"], "2.3.15")
        for result in run["results"]:
            self.assertEqual(rules[result["ruleIndex"]]["id"], result["ruleId"])
        first = run["results"][0]
        self.assertEqual((first["level"], first["properties"]["status"]), ("error", "confirmed"))
        location = first["locations"][0]["physicalLocation"]
        self.assertEqual(location["artifactLocation"], {"uri": "app/my%20search.py", "uriBaseId": "%SRCROOT%"})
        self.assertEqual(location["region"], {"startLine": 3})
        steps = first["codeFlows"][0]["threadFlows"][0]["locations"]
        self.assertEqual([s["location"]["physicalLocation"]["region"]["startLine"] for s in steps], [1, 2, 3])
        self.assertEqual(first["partialFingerprints"]["navinFingerprint/v1"], sql.fingerprint())
        self.assertEqual(rules[0]["properties"]["security-severity"], "8.0")  # the worst of the two SQL results
        self.assertEqual(rules[0]["helpUri"], "https://cwe.mitre.org/data/definitions/89.html")
        endpoint_only = next(r for r in run["results"] if r["ruleId"] == "navin-auth-missing")
        self.assertEqual(endpoint_only["locations"], [{"logicalLocations": [{"name": "POST /orders", "kind": "resource"}]}])
        self.assertNotIn("codeFlows", endpoint_only)
        self.assertEqual(run["invocations"][0]["toolExecutionNotifications"][0]["message"]["text"], "semgrep did not run: not installed")
        self.assertTrue(run["originalUriBaseIds"]["%SRCROOT%"]["uri"].startswith("file://"))

    def test_accepted_is_suppressed_and_fixed_is_left_out(self):
        accepted = _sql_finding("a.py")
        accepted.validation.state = "accepted"
        accepted.validation.note = "internal admin tool"
        fixed = _sql_finding("b.py")
        fixed.validation.state = "fixed"
        results = to_sarif([accepted, fixed])["runs"][0]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["suppressions"][0]["justification"], "internal admin tool")
        self.assertEqual(len(to_sarif([accepted, fixed], include_fixed=True)["runs"][0]["results"]), 2)
        self.assertEqual(to_sarif([])["runs"][0]["results"], [])


class CoverageTest(unittest.TestCase):
    def test_clean_only_where_an_engine_really_checked(self):
        checked = checked_controls(["navin_dataflow"])
        clean = coverage([], checked=checked)
        self.assertEqual(clean.status_of("C1.01"), "clean")
        self.assertEqual(clean.status_of("C1.02"), "not_assessed")  # XSS: no engine that checks it ran
        self.assertEqual(clean.controls["C1.02"].reason, "no engine that checks it ran")
        self.assertIn("semgrep", clean.controls["C1.02"].engines)
        self.assertEqual(clean.totals["assessed"], len(checked))
        self.assertEqual(clean.totals["controls"], len(catalog.CONTROLS))

    def test_findings_set_statuses_and_closed_ones_do_not_count(self):
        sql = _sql_finding()
        dependency = Finding(id="GHSA-x", family="supply_chain", title="old lib", severity="critical", controls=["C4.01"])
        fixed = _sql_finding("old.py")
        fixed.validation.state = "fixed"
        accepted = Finding(id="risk", family="api", title="accepted", severity="low", controls=["C3.02"])
        accepted.validation.state = "accepted"
        report = coverage([sql, dependency, fixed, accepted], checked=checked_controls(["navin_dataflow"]), planned={"C1.01", "C1.04"})
        self.assertEqual(report.status_of("C1.01"), "confirmed")
        self.assertEqual(report.status_of("C1.04"), "clean")
        self.assertEqual(report.status_of("C4.01"), "potential")  # a finding counts even where nothing was planned
        self.assertEqual(report.status_of("C1.06"), "not_assessed")
        self.assertEqual(report.controls["C1.06"].reason, "not planned in this audit")
        self.assertEqual(report.totals["by_status"], {"potential": 1, "confirmed": 1, "exploitable": 0})
        self.assertEqual(report.accepted, [accepted.fingerprint()])
        self.assertEqual(report.status_of("C3.02"), "potential")
        for fw in report.frameworks.values():
            self.assertEqual(fw["assessed"] + fw["not_assessed"], fw["controls"])
        self.assertIn("owasp_asvs_5", report.frameworks)
        self.assertIn("mitre_cwe", report.frameworks)
        json.dumps(report.to_dict())

    def test_markdown_in_each_managed_language(self):
        report = coverage([_sql_finding()], checked=checked_controls(["navin_dataflow"]))
        for lang, title in (("fr", "Couverture de l'audit"), ("en", "audit coverage"), ("ar", "تغطية تدقيق")):
            text = coverage_markdown(report, lang=lang)
            self.assertIn(title, text)
            self.assertIn("C1.02", text)  # listed as not assessed
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)
        self.assertIn("not proof", coverage_markdown(report, lang="de"))  # unmanaged language falls back to English


class GuidanceTest(unittest.TestCase):
    def test_cwe_first_family_second_and_scanner_advice_kept(self):
        self.assertIn("parameterized", remediation_for(_sql_finding()))
        generic = Finding(id="x", family="multitenancy", title="t", cwe="CWE-1")
        self.assertIn("tenant", remediation_for(generic))
        own = Finding(id="y", family="injection", title="t", cwe="CWE-89", remediation="Upgrade the ORM.")
        apply_guidance([own, generic])
        self.assertEqual(own.remediation, "Upgrade the ORM.")
        self.assertTrue(generic.remediation)


class PatchValidatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "app" / "search.py").write_text(VULNERABLE, encoding="utf-8")
        (self.root / "tests" / "test_search.py").write_text(TEST_FILE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_good_fix_applies_parses_and_leaves_the_project_untouched(self):
        diff = _diff("app/search.py", VULNERABLE, FIXED) + _diff("tests/test_injection.py", "", "def test_x():\n    assert True\n", new_file=True)
        check = validate_patch(self.root, diff)
        self.assertEqual((check.status, check.ok, check.files), ("ok", True, ["app/search.py", "tests/test_injection.py"]))
        self.assertEqual((check.added, check.removed), (4, 2))
        self.assertEqual((self.root / "app" / "search.py").read_text(encoding="utf-8"), VULNERABLE)
        self.assertFalse((self.root / "tests" / "test_injection.py").exists())
        self.assertTrue(asyncio.run(validate_patch_async(self.root, diff)).ok)

    def test_patches_that_do_not_apply_or_break_the_code(self):
        stale = _diff("app/search.py", VULNERABLE.replace("items", "products"), FIXED)
        self.assertEqual(validate_patch(self.root, stale).status, "does_not_apply")
        broken = validate_patch(self.root, _diff("app/search.py", VULNERABLE, FIXED.replace("(term,)", "(term,")))
        self.assertEqual(broken.status, "syntax_error")
        self.assertIn("no longer parses", broken.reasons[0])
        config = _diff("config.json", "", "{\"a\": 1,}\n", new_file=True)
        self.assertEqual(validate_patch(self.root, config).status, "syntax_error")

    def test_policy_refusals(self):
        cases = {
            "leaves the project": _diff("../outside.py", "", "x = 1\n", new_file=True),
            "is a secret or key file": _diff(".env", "", "TOKEN=1\n", new_file=True),
            "inside .git": _diff(".git/hooks/pre-commit", "", "echo\n", new_file=True),
            "must not delete it": _diff("tests/test_search.py", TEST_FILE, "", deleted=True),
            "must not weaken its tests": _diff("tests/test_search.py", TEST_FILE, "def test_search():\n    pass\n"),
            "too large": _diff("big.py", "", "x = 1\n" * 700, new_file=True),
            "not a unified diff": "please just trust me",
            "binary patches": "diff --git a/x.png b/x.png\nGIT binary patch\nliteral 0\n",
        }
        for reason, diff in cases.items():
            with self.subTest(reason=reason):
                check = validate_patch(self.root, diff)
                self.assertEqual(check.status, "rejected")
                self.assertTrue(any(reason in r for r in check.reasons), check.reasons)
                self.assertFalse(check.ok)

    def test_without_git_the_patch_is_unchecked_never_valid(self):
        with mock.patch("navin.security.remediation.patch_validator.shutil.which", return_value=None):
            check = validate_patch(self.root, _diff("app/search.py", VULNERABLE, FIXED))
        self.assertEqual((check.status, check.ok), ("unchecked", False))
        self.assertIn("git is not installed", check.reasons[0])


class EvidenceShapeTest(unittest.TestCase):
    def test_static_evidence_never_becomes_a_code_flow(self):
        item = Finding(id="s", family="secrets", title="key", file_path="a.py", line=2,
                       evidence=[Evidence(kind="static", description="1. a.py:2 looks like a trace", observed="1. a.py:2 x\n2. a.py:3 y")])
        result = to_sarif([item])["runs"][0]["results"][0]
        self.assertNotIn("codeFlows", result)
        self.assertEqual(result["properties"]["evidence"], ["static"])


class RemediationLoopTest(unittest.TestCase):
    """Analyze, propose a fix, validate it, apply it in a copy, rescan, close."""

    SOURCE = (
        "import subprocess\n"
        "import jwt\n"
        "import yaml\n"
        "from flask import Flask, request\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route(\"/ping\", methods=[\"POST\"])\n"
        "def ping():\n"
        "    host = request.form[\"host\"]\n"
        "    return subprocess.check_output(\"ping -c 1 \" + host, shell=True)\n"
        "\n"
        "@app.route(\"/count\", methods=[\"POST\"])\n"
        "def count():\n"
        "    path = request.form[\"path\"]\n"
        "    return subprocess.check_output(\"ls \" + path + \" | wc -l\", shell=True)\n"
        "\n"
        "@app.route(\"/config\", methods=[\"POST\"])\n"
        "def config():\n"
        "    return str(yaml.load(request.data))\n"
        "\n"
        "@app.route(\"/search\")\n"
        "def search():\n"
        "    return str(db.execute(f\"SELECT * FROM t WHERE n = '{request.args['q']}'\"))\n"
        "\n"
        "def read_token(token):\n"
        "    return jwt.decode(token, \"secret\", algorithms=[\"HS256\", \"none\"], options={\"verify_exp\": False, \"leeway\": 5})\n"
        "\n"
        "def peek(token):\n"
        "    return jwt.decode(token, options={\"verify_signature\": False})\n"
    )

    def test_full_loop_on_a_real_project(self):
        import shutil
        import subprocess

        from navin.security.analyzers import analyze_project
        from navin.security.remediation import propose_patches
        from navin.security.verification import add_regression_test, rescan

        if not shutil.which("git"):
            self.skipTest("git is needed to apply the patches")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text(self.SOURCE, encoding="utf-8")
            before = analyze_project(root, only=["navin_dataflow", "navin_auth"])
            by_line = {(f.id, f.line): f for f in before.findings}
            self.assertIn(("navin-dataflow-command", 11), by_line)
            self.assertIn(("navin-dataflow-command", 16), by_line)
            self.assertIn(("navin-dataflow-deserialization", 20), by_line)
            self.assertIn(("navin-auth-jwt", 27), by_line)
            self.assertIn(("navin-auth-jwt", 30), by_line)
            proposals = {(f.id, f.line): p for f, p in zip(before.findings, propose_patches(root, before.findings))}

            manual = {key: p.reason for key, p in proposals.items() if not p.generated}
            self.assertIn("shell syntax", manual[("navin-dataflow-command", 16)])  # the pipe needs a person
            self.assertIn("signing key", manual[("navin-auth-jwt", 30)])
            self.assertIn("no mechanical fix", manual[("navin-dataflow-sql", 24)])
            generated = {key: p for key, p in proposals.items() if p.generated}
            self.assertEqual(set(generated), {("navin-dataflow-command", 11), ("navin-dataflow-deserialization", 20), ("navin-auth-jwt", 27)})
            self.assertIn("+    return subprocess.check_output(['ping', '-c', '1', host])", generated[("navin-dataflow-command", 11)].diff)
            self.assertIn("+    return str(yaml.safe_load(request.data))", generated[("navin-dataflow-deserialization", 20)].diff)
            jwt_diff = generated[("navin-auth-jwt", 27)].diff
            self.assertIn('algorithms=["HS256"], options={"leeway": 5})', jwt_diff)
            self.assertIn("verify_exp back on", generated[("navin-auth-jwt", 27)].summary)

            for proposal in generated.values():  # each one validates and leaves the project as it was
                self.assertTrue(validate_patch(root, proposal.diff).ok, proposal.summary)
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), self.SOURCE)

            for proposal in generated.values():  # apply them, one after the other, the way a person would
                done = subprocess.run(["git", "apply", "-"], cwd=root, input=proposal.diff, text=True,
                                      capture_output=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)
            compile((root / "app.py").read_text(encoding="utf-8"), "app.py", "exec")
            after = analyze_project(root, only=["navin_dataflow", "navin_auth"])
            remaining = {(f.id, f.line) for f in after.findings}
            self.assertEqual(remaining, {
                ("navin-dataflow-command", 16), ("navin-dataflow-sql", 24), ("navin-auth-jwt", 30),
                ("navin-auth-missing", 14), ("navin-auth-missing", 19),  # POST routes with no auth: out of a patch's reach
            })
            self.assertIn("no mechanical fix for navin-auth-missing", manual[("navin-auth-missing", 14)])

            fixed = by_line[("navin-dataflow-command", 11)]
            (root / "tests").mkdir()
            (root / "tests" / "test_ping.py").write_text("def test_ping_rejects_shell():\n    assert True\n", encoding="utf-8")
            self.assertTrue(add_regression_test(fixed, "tests/test_ping.py", root).verified)
            self.assertTrue(rescan(fixed, after.findings, scanner_ran=True).verified)
            self.assertEqual(fixed.validation.state, "fixed")
            still = by_line[("navin-dataflow-command", 16)]
            self.assertFalse(rescan(still, after.findings, scanner_ran=True).verified)

    def test_only_python_files_with_a_line_and_inside_the_project(self):
        from navin.security.remediation import propose_patch

        outside = Finding(id="navin-dataflow-deserialization", family="code_execution", title="t", file_path="../x.py", line=1)
        js = Finding(id="navin-dataflow-command", family="injection", title="t", file_path="a.js", line=1)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("not a file of the project", propose_patch(tmp, outside).reason)
            self.assertIn("only cover Python", propose_patch(tmp, js).reason)


class CoverageLevelsTest(unittest.TestCase):
    def test_a_static_pass_never_reads_as_a_full_check_of_a_higher_level(self):
        checked = checked_controls(["navin_tenant", "navin_dataflow"])
        report = coverage([], checked=checked, levels=["L1"])
        tenant = report.controls["C2.09"]  # an L3 control
        self.assertEqual((tenant.status, tenant.partial), ("clean", True))
        self.assertIn("two tenants", tenant.reason)
        self.assertFalse(report.controls["C1.01"].partial)  # L1, reached
        self.assertEqual(report.totals["partial"], sum(1 for c in report.controls.values() if c.partial))
        text = coverage_markdown(report, lang="fr")
        self.assertIn("Verifies en statique seulement", text)
        self.assertIn("C2.09", text)
        hit = Finding(id="t", family="multitenancy", title="t", controls=["C2.09"])
        self.assertFalse(coverage([hit], checked=checked, levels=["L1"]).controls["C2.09"].partial)
        self.assertFalse(coverage([], checked=checked, levels=["L1", "L2", "L3"]).controls["C2.09"].partial)


if __name__ == "__main__":
    unittest.main()
