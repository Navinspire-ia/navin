# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deep security scan: code, IaC, project checks, score, HTML and PDF reports."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.security.deep_scan import compute_score, run_deep_security_scan
from navin.security.report_deep import (
    pdf_backends_available,
    render_deep_report_html,
    render_deep_report_pdf,
)
from navin.webui.security_scan_api import (
    SecurityScanError,
    get_scan_result,
    security_report_payload,
    security_scan_payload,
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _vulnerable_project(root: Path) -> None:
    _write(
        root,
        "app/views.py",
        "import os\nimport hashlib\nimport random\n"
        "from flask import request, redirect\n"
        "DEBUG = True\n"
        "def login(cur, user):\n"
        "    cur.execute(f\"SELECT * FROM users WHERE name = '{user}'\")\n"
        "    digest = hashlib.md5(password.encode()).hexdigest()\n"
        "    reset_token = random.randint(0, 999999)\n"
        "    os.system('ls ' + user)\n"
        "    return redirect(request.args.get('next'))\n"
        "SESSION_COOKIE_SECURE = False\n",
    )
    _write(
        root,
        "Dockerfile",
        "FROM python:latest\nRUN curl -sSL https://x.example/install.sh | sh\n"
        "ENV API_TOKEN=sk_live_abcdef123456\nCMD [\"python\", \"app.py\"]\n",
    )
    _write(
        root,
        "docker-compose.yml",
        "services:\n  web:\n    image: app\n    privileged: true\n"
        "    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
    )
    _write(
        root,
        "deploy/k8s/deploy.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nspec:\n  template:\n    spec:\n"
        "      hostNetwork: true\n      containers:\n        - name: app\n"
        "          image: app:latest\n          securityContext:\n"
        "            privileged: true\n            runAsUser: 0\n",
    )
    _write(
        root,
        "infra/main.tf",
        'resource "aws_security_group" "web" {\n  ingress {\n    from_port = 22\n'
        '    cidr_blocks = ["0.0.0.0/0"]\n  }\n}\n'
        'resource "aws_s3_bucket" "logs" {\n  acl = "public-read"\n}\n'
        'resource "aws_db_instance" "db" {\n  publicly_accessible = true\n'
        '  password = "SuperSecretDbPass2026"\n}\n',
    )
    _write(
        root,
        ".github/workflows/ci.yml",
        "on: [pull_request]\npermissions: write-all\njobs:\n  build:\n    steps:\n"
        "      - run: echo \"${{ github.event.pull_request.title }}\"\n",
    )
    _write(root, ".env", "DATABASE_PASSWORD=prod-secret-value-2026\n")
    _write(root, "package.json", '{"dependencies": {"express": "^4.0.0"}}\n')
    # Test files are skipped: this one must not add a finding.
    _write(root, "tests/test_x.py", "cur.execute(f\"SELECT {x}\")\n")


class DeepScanTest(unittest.TestCase):
    def test_detects_code_iac_and_project_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _vulnerable_project(root)
            result = run_deep_security_scan(root, kind="quick")
        self.assertTrue(result["ok"])
        ids = {f["id"] for f in result["findings"]}
        expected = {
            "sql-execute-format",
            "debug-true",
            "weak-hash-password",
            "os-system",
            "redirect-tainted",
            "insecure-cookie",
            "insecure-random-token",
            "docker-latest-tag",
            "docker-curl-pipe-sh",
            "docker-env-secret",
            "docker-root-user",
            "compose-privileged",
            "compose-docker-socket",
            "k8s-privileged",
            "k8s-host-namespace",
            "k8s-run-as-root",
            "k8s-latest-image",
            "tf-open-ingress",
            "tf-public-acl",
            "tf-publicly-accessible",
            "tf-hardcoded-password",
            "gha-expression-injection",
            "gha-write-all",
            "env-credential",
            "express-no-helmet",
        }
        self.assertEqual(expected - ids, set(), f"missing rules; got {sorted(ids)}")
        self.assertFalse(any(f["file_path"].startswith("tests/") for f in result["findings"]))

        self.assertLess(result["score"], 40)
        self.assertEqual(result["grade"], "F")
        first = result["findings"][0]
        self.assertEqual(first["severity"], "critical")
        for item in result["findings"]:
            self.assertIn(item["owasp"], {f"A{n:02d}" for n in range(1, 11)})
            self.assertTrue(item["owasp_name"])
            self.assertTrue(item["poc_sketch"])
            self.assertTrue(item["malicious_input_example"])
        cwe = {f["id"]: f["cwe"] for f in result["findings"]}
        self.assertEqual(cwe["sql-execute-format"], "CWE-89")
        self.assertEqual(cwe["tf-open-ingress"], "CWE-284")

        self.assertEqual(result["by_owasp"][2]["id"], "A03")
        self.assertGreater(result["by_owasp"][2]["count"], 0)
        self.assertTrue(result["remediation_plan"])
        self.assertEqual(result["remediation_plan"][0]["order"], 1)
        self.assertEqual(result["remediation_plan"][0]["severity"], "critical")
        checks = {c["id"]: c["status"] for c in result["project_checks"]}
        self.assertEqual(checks["express-helmet"], "warn")
        self.assertEqual(checks["lockfile-node"], "warn")
        self.assertIn(result["file_kinds"]["terraform"], (1,))
        self.assertGreaterEqual(result["files_scanned"], 8)

    def test_clean_project_scores_100(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.py", "import json\nprint(json.dumps({'ok': True}))\n")
            _write(root, "Dockerfile", "FROM python:3.12-slim@sha256:abc\nUSER app\nCMD [\"python\"]\n")
            result = run_deep_security_scan(root, kind="quick")
        self.assertTrue(result["ok"])
        self.assertEqual(result["finding_count"], 0)
        self.assertEqual((result["score"], result["grade"]), (100, "A"))

    def test_score_grades(self):
        self.assertEqual(compute_score({}), (100, "A"))
        self.assertEqual(compute_score({"medium": 3}), (88, "B"))
        self.assertEqual(compute_score({"critical": 1, "high": 2}), (60, "C"))
        self.assertEqual(compute_score({"critical": 9}), (40, "D"))
        self.assertEqual(compute_score({"critical": 3, "high": 4, "medium": 6, "low": 8}), (0, "F"))


class DeepReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _vulnerable_project(root)
        self.result = run_deep_security_scan(root, kind="quick")

    def tearDown(self):
        self.tmp.cleanup()

    def test_html_report_sections(self):
        doc = render_deep_report_html(self.result, title="Security assessment - demo")
        for needle in (
            "Security assessment - demo",
            "Executive summary",
            "OWASP Top 10 (2021)",
            "Project checks",
            "Remediation plan",
            "Proof of exploitability",
            "sql-execute-format",
            "tf-open-ingress",
            "@media print",
            "contact@navinspire.com",
        ):
            self.assertIn(needle, doc)
        self.assertNotIn("\u2014", doc)
        self.assertNotIn("\u2013", doc)
        self.assertNotIn("<script", doc.lower())

    def test_pdf_report_bytes(self):
        self.assertIn("reportlab", pdf_backends_available())
        pdf = render_deep_report_pdf(self.result, title="Security assessment - demo")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 2000)

    def test_pdf_falls_back_to_reportlab_when_weasyprint_breaks(self):
        from navin.security import report_deep

        def broken(_html: str) -> bytes:
            raise OSError("cannot load library 'gobject-2.0'")

        with mock.patch.object(report_deep, "pdf_backends_available", return_value=["weasyprint", "reportlab"]), \
                mock.patch.object(report_deep, "_pdf_with_weasyprint", side_effect=broken):
            pdf = render_deep_report_pdf(self.result, title="demo")
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_pdf_without_backend_raises_with_detail(self):
        from navin.security import report_deep

        with mock.patch.object(report_deep, "pdf_backends_available", return_value=[]):
            with self.assertRaises(RuntimeError) as ctx:
                render_deep_report_pdf(self.result, title="demo")
        self.assertIn("install weasyprint or reportlab", str(ctx.exception))

        def broken(_html: str) -> bytes:
            raise OSError("cannot load library 'gobject-2.0'")

        with mock.patch.object(report_deep, "pdf_backends_available", return_value=["weasyprint"]), \
                mock.patch.object(report_deep, "_pdf_with_weasyprint", side_effect=broken):
            with self.assertRaises(RuntimeError) as ctx:
                render_deep_report_pdf(self.result, title="demo")
        self.assertIn("gobject-2.0", str(ctx.exception))


class SecurityScanApiTest(unittest.TestCase):
    def test_payload_store_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _vulnerable_project(root)
            payload = security_scan_payload(str(root), kind="quick")
            self.assertTrue(payload["scan_id"])
            self.assertIs(get_scan_result(payload["scan_id"]), payload)
            body, ctype, name = security_report_payload(payload["scan_id"], "html")
            self.assertTrue(ctype.startswith("text/html"))
            self.assertTrue(name.startswith("security-report-") and name.endswith(".html"))
            self.assertIn(b"OWASP", body)
            pdf, pctype, pname = security_report_payload(payload["scan_id"], "pdf")
            self.assertEqual(pctype, "application/pdf")
            self.assertTrue(pname.endswith(".pdf"))
            self.assertTrue(pdf.startswith(b"%PDF"))

    def test_report_pdf_without_backend_is_503(self):
        from navin.security import report_deep

        with tempfile.TemporaryDirectory() as tmp:
            payload = security_scan_payload(tmp, kind="quick")
        with mock.patch.object(report_deep, "pdf_backends_available", return_value=[]):
            with self.assertRaises(SecurityScanError) as ctx:
                security_report_payload(payload["scan_id"], "pdf")
        self.assertEqual(ctx.exception.status, 503)

    def test_errors(self):
        with self.assertRaises(SecurityScanError) as ctx:
            security_scan_payload("relative/path")
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(SecurityScanError) as ctx:
            security_scan_payload("/definitely/not/here/navin-x")
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(SecurityScanError) as ctx:
            security_report_payload("unknown-id", "html")
        self.assertEqual(ctx.exception.status, 404)
        with tempfile.TemporaryDirectory() as tmp:
            payload = security_scan_payload(tmp, kind="quick")
            with self.assertRaises(SecurityScanError):
                security_report_payload(payload["scan_id"], "docx")
            with self.assertRaises(SecurityScanError):
                security_scan_payload(tmp, kind="quick", subpath="../outside")


class SecurityScanToolDeepTest(unittest.TestCase):
    def test_tool_deep_output_writes_html_and_pdf(self):
        import asyncio
        import json

        from navin.agent.tools.security_scan import SecurityScanTool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _vulnerable_project(root)
            tool = SecurityScanTool(workspace=root)
            out = asyncio.run(tool.execute(kind="quick", write_report=True, write_pdf=True))
            header, body = out.split("\n", 1)
            self.assertIn("deep=true", header)
            self.assertIn("score=", header)
            self.assertIn("grade=F", header)
            data = json.loads(body)
            self.assertIn("remediation_plan", data)
            self.assertIn("by_owasp", data)
            self.assertTrue(Path(data["report_path"]).is_file())
            html_doc = Path(data["report_path"]).read_text(encoding="utf-8")
            self.assertIn("OWASP Top 10 (2021)", html_doc)
            self.assertIsNone(data["pdf_error"])
            self.assertTrue(Path(data["pdf_path"]).is_file())
            self.assertTrue(Path(data["pdf_path"]).read_bytes().startswith(b"%PDF"))

            # PDF only: the HTML rendering step is removed, the PDF stays.
            pdf_only = json.loads(asyncio.run(tool.execute(kind="quick", write_pdf=True)).split("\n", 1)[1])
            self.assertIsNone(pdf_only["report_path"])
            self.assertTrue(Path(pdf_only["pdf_path"]).is_file())
            self.assertFalse(Path(pdf_only["pdf_path"]).with_suffix(".html").exists())

            # No backend: the tool says so instead of pretending.
            from navin.security import report_deep
            with mock.patch.object(report_deep, "pdf_backends_available", return_value=[]):
                missing = json.loads(asyncio.run(tool.execute(kind="quick", write_pdf=True)).split("\n", 1)[1])
            self.assertIsNone(missing["pdf_path"])
            self.assertIn("no PDF backend", missing["pdf_error"])

            # Focused kinds keep the code-only pass and its legacy report.
            legacy = asyncio.run(tool.execute(kind="secrets", write_report=False))
            self.assertIn("deep=false", legacy.split("\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
