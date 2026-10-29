# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security audit end to end: orchestrator, saved state, agent tool and Cyber panel API.

The fixture project is deliberately vulnerable source code written to a
temporary folder and read as text: none of it is imported or run. External
scanners are replaced by fakes so the outcome does not depend on what this
machine has installed.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.tools.security_audit import SARIF_FILE, SecurityAuditTool
from navin.security import catalog
from navin.security.model import Finding
from navin.security.orchestrator import audit as audit_mod
from navin.security.orchestrator.audit import (
    AuditStateError,
    checklist_state,
    coverage_of,
    load_last_audit,
    mask_secret,
    run_audit,
    save_audit,
    set_check,
    state_dir,
)
from navin.security.scanners.registry import REGISTRY, ScannerRun, ScannerStatus
from navin.webui.security_audit_api import (
    SecurityAuditError,
    security_audit_payload,
    security_audit_sarif,
)

# Built at run time: GitHub push protection rejects a literal live-key shape, even in a test.
SECRET = "sk_live_" + "51HabcdefghijklmnopqrstuvwxYZ0123456789"

APP = '''
import sqlite3
import yaml
from flask import Flask, request

app = Flask(__name__)
db = sqlite3.connect(":memory:")

@app.route("/search")
def search():
    term = request.args.get("q", "")
    return str(db.execute(f"SELECT * FROM items WHERE name = '{term}'").fetchall())

@app.route("/config", methods=["POST"])
def config():
    return str(yaml.load(request.data))
'''

SETTINGS = f'''
STRIPE_KEY = "{SECRET}"
'''


def _project() -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "app.py").write_text(textwrap.dedent(APP).lstrip(), encoding="utf-8")
    (root / "settings.py").write_text(textwrap.dedent(SETTINGS).lstrip(), encoding="utf-8")
    (root / "requirements.txt").write_text("flask==3.0.0\npyyaml==6.0.1\n", encoding="utf-8")
    return tmp


def _statuses(available: set[str]):
    def detect(ids=None):
        return {sid: ScannerStatus(sid, sid in available, path=f"/usr/bin/{sid}" if sid in available else "")
                for sid in (ids or list(REGISTRY))}
    return detect


def _fake_run(scanner_id, root, *, timeout_s=None):
    if scanner_id == "pip_audit":
        finding = Finding(id="PYSEC-2024-1", family="supply_chain", title="pyyaml: arbitrary code execution", severity="high",
                          cve="CVE-2024-0001", file_path="requirements.txt", component="pyyaml", controls=["C4.01"], source="pip_audit")
        return ScannerRun(id=scanner_id, ok=True, status="ok", findings=[finding], duration_s=0.1)
    if scanner_id == "gitleaks":
        return ScannerRun(id=scanner_id, ok=False, status="timeout", detail=f"stopped after {timeout_s} s")
    raise AssertionError(f"{scanner_id} should not run")


class AuditRunTest(unittest.TestCase):
    def test_audit_records_every_engine_and_never_reads_a_missing_scanner_as_clean(self):
        with _project() as tmp, \
                mock.patch.object(audit_mod, "detect_scanners", _statuses({"pip_audit", "gitleaks"})), \
                mock.patch.object(audit_mod, "run_scanner", side_effect=_fake_run) as runner:
            result = run_audit(tmp)
        engines = {e.id: e for e in result.engines}
        self.assertEqual({k: engines[k].status for k in ("navin_dataflow", "navin_auth", "navin_tenant", "navin_business_logic", "navin_rules")},
                         dict.fromkeys(("navin_dataflow", "navin_auth", "navin_tenant", "navin_business_logic", "navin_rules"), "ok"))
        self.assertEqual((engines["pip_audit"].status, engines["pip_audit"].findings), ("ok", 1))
        self.assertEqual(engines["gitleaks"].status, "timeout")
        self.assertEqual(engines["semgrep"].status, "not_installed")
        self.assertTrue(engines["semgrep"].detail)  # how to install it
        self.assertEqual({c.args[0] for c in runner.call_args_list}, {"pip_audit", "gitleaks"})

        ids = {f.id for f in result.findings}
        self.assertTrue({"navin-dataflow-sql", "navin-dataflow-deserialization", "PYSEC-2024-1"} <= ids)
        sql = next(f for f in result.findings if f.id == "navin-dataflow-sql")
        self.assertEqual(sql.status, "confirmed")
        cov = result.coverage
        self.assertEqual(cov.status_of("C1.01"), "confirmed")
        self.assertEqual(cov.status_of("C4.01"), "potential")  # pip-audit ran and found one
        self.assertEqual(cov.status_of("C1.02"), "clean")  # XSS: the rule pack ran and found nothing
        self.assertTrue(cov.controls["C2.09"].partial)  # tenant isolation: static pass of an L3 control
        self.assertEqual(cov.status_of("C5.01"), "not_assessed")  # no mobile app here
        self.assertIn("no mobile or desktop target", cov.controls["C5.01"].reason)
        self.assertIn("no AI", cov.controls["C6.01"].reason)
        self.assertEqual(cov.status_of("C4.03"), "not_assessed")  # SBOM: syft/trivy did not run
        self.assertIn("gitleaks", result.not_run())
        self.assertEqual(result.levels_reached, ["L1"])
        self.assertIn("L2", result.plan["missing_inputs"])

        dumped = json.dumps(result.to_dict())
        self.assertNotIn(SECRET, dumped)  # the rule pack's evidence is masked
        notifications = result.sarif()["runs"][0]["invocations"][0]["toolExecutionNotifications"]
        self.assertTrue(any("gitleaks did not run: timeout" in n["message"]["text"] for n in notifications))

    def test_offline_and_turned_off_scanners_are_skipped_with_the_reason(self):
        with _project() as tmp, mock.patch.object(audit_mod, "detect_scanners", _statuses(set(REGISTRY))), \
                mock.patch.object(audit_mod, "run_scanner", side_effect=_fake_run):
            off = run_audit(tmp, scanners=False)
            offline = run_audit(tmp, scanners=["pip_audit", "npm_audit"], offline=True)
        self.assertTrue(all(e.status == "skipped" and "turned off" in e.detail for e in off.engines if e.kind == "scanner"))
        by_id = {e.id: e for e in offline.engines}
        self.assertEqual(by_id["npm_audit"].detail, "needs the network and the audit is offline")
        self.assertEqual(by_id["semgrep"].detail, "not requested")
        with self.assertRaisesRegex(ValueError, "unknown scanner"):
            run_audit(".", scanners=["nmap"])
        with self.assertRaises(NotADirectoryError):
            run_audit("/definitely/not/here")

    def test_secret_masking_keeps_four_characters(self):
        self.assertEqual(mask_secret(f'KEY = "{SECRET}"'), 'KEY = "sk_l****"')
        self.assertEqual(mask_secret("API_TOKEN=abcdef123456"), "API_TOKEN=abcd****")
        self.assertEqual(mask_secret("port = 8080"), "port = 8080")


class SavedStateTest(unittest.TestCase):
    def test_save_load_checklist_and_manual_ticks(self):
        with _project() as tmp, mock.patch.object(audit_mod, "detect_scanners", _statuses(set())):
            root = Path(tmp)
            self.assertIsNone(load_last_audit(root))
            empty = checklist_state(root)
            self.assertEqual((empty["done"], empty["total"]), (0, len(catalog.CONTROLS)))
            result = run_audit(root, scanners=False)
            saved = save_audit(root, result)
            self.assertTrue(saved.is_file())
            self.assertIn("last-audit.json", (state_dir(root) / ".gitignore").read_text(encoding="utf-8"))
            audit = load_last_audit(root)
            self.assertEqual(audit["id"], result.id)
            self.assertEqual(coverage_of(audit).status_of("C1.01"), "confirmed")
            state = checklist_state(root, audit)
            verified = {i["id"] for i in state["items"] if i["done"]}
            self.assertNotIn("C1.01", verified)  # open finding
            self.assertNotIn("C2.09", verified)  # partial static pass
            self.assertIn("C1.02", verified)
            self.assertEqual(sum(g["total"] for g in state["groups"]), len(catalog.CONTROLS))
            set_check(root, "C2.09", True, note="checked with two tenants by hand")
            ticked = checklist_state(root, audit)
            self.assertEqual(ticked["done"], state["done"] + 1)
            self.assertEqual(next(i for i in ticked["items"] if i["id"] == "C2.09")["note"], "checked with two tenants by hand")
            set_check(root, "C2.09", False)
            self.assertEqual(checklist_state(root, audit)["done"], state["done"])
            with self.assertRaises(KeyError):
                set_check(root, "C9.99", True)
            for _ in range(11):
                save_audit(root, result.__class__(**{**result.__dict__, "id": f"x{_:02d}"}))
            self.assertEqual(len(list((state_dir(root) / "audits").glob("*.json"))), 10)
            (state_dir(root) / "checklist.json").write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(AuditStateError, "could not be read"):
                checklist_state(root, audit)


class ToolTest(unittest.TestCase):
    def test_tool_actions_on_a_real_project(self):
        with _project() as tmp, mock.patch.object(audit_mod, "detect_scanners", _statuses(set())):
            root = Path(tmp)
            tool = SecurityAuditTool(workspace=root)
            self.assertEqual(tool.name, "security_audit")
            self.assertFalse(tool.read_only)
            no_audit = asyncio.run(tool.execute(action="report"))
            self.assertIn("no audit saved", str(no_audit))

            plan = asyncio.run(tool.execute(action="plan", base_url="http://127.0.0.1:5000"))
            header, body = plan.split("\n", 1)
            self.assertIn("levels=L1,L2", header)
            self.assertIn("semgrep", json.loads(body)["scanners_not_installed"])

            out = asyncio.run(tool.execute(action="run", scanners=False, sarif=True, max_findings=2))
            header, body = out.split("\n", 1)
            self.assertIn("security_audit id=", header)
            self.assertIn("confirmed=", header)
            data = json.loads(body)
            self.assertEqual(len(data["findings"]), 2)
            self.assertGreater(data["findings_total"], 2)
            self.assertEqual(data["sarif"], SARIF_FILE)
            self.assertEqual(json.loads((root / SARIF_FILE).read_text(encoding="utf-8"))["version"], "2.1.0")
            self.assertNotIn(SECRET, out)
            self.assertIn("not proof of security", data["reminder"])

            report = asyncio.run(tool.execute(action="report", lang="fr"))
            self.assertIn("Couverture de l'audit Navin Security", report)
            self.assertIn("Verifies en statique seulement", report)

            patch = asyncio.run(tool.execute(action="patch"))
            header, body = patch.split("\n", 1)
            patches = json.loads(body)["patches"]
            yaml_fix = next(p for p in patches if p["id"] == "navin-dataflow-deserialization")
            self.assertEqual(yaml_fix["validation"], "ok")
            self.assertIn("yaml.safe_load(request.data)", yaml_fix["diff"])
            self.assertIn("yaml.load(request.data)", (root / "app.py").read_text(encoding="utf-8"))  # never applied
            self.assertIn("no finding with fingerprint", str(asyncio.run(tool.execute(action="patch", finding="nope"))))

            checklist = asyncio.run(tool.execute(action="checklist", lang="ar"))
            self.assertIn(f"/{len(catalog.CONTROLS)}", checklist.split("\n", 1)[0])
            self.assertIn("قائمة تدقيق", checklist)
            self.assertIn("unknown action", str(asyncio.run(tool.execute(action="exploit"))))


class PanelApiTest(unittest.TestCase):
    def test_panel_actions(self):
        with _project() as tmp, mock.patch.object(audit_mod, "detect_scanners", _statuses(set())):
            self.assertEqual(security_audit_payload("catalog", {})["totals"]["controls"], len(catalog.CONTROLS))
            self.assertIsNone(security_audit_payload("last", {"path": tmp})["audit"])
            with self.assertRaises(SecurityAuditError) as missing:
                security_audit_sarif(tmp)
            self.assertEqual(missing.exception.status, 404)
            ran = security_audit_payload("run", {"path": tmp, "scanners": "false"})
            self.assertTrue(ran["audit"]["id"])
            self.assertEqual(ran["checklist"]["total"], len(catalog.CONTROLS))
            self.assertEqual(security_audit_payload("last", {"path": tmp})["audit"]["id"], ran["audit"]["id"])
            before = security_audit_payload("checklist", {"path": tmp})["done"]
            after = security_audit_payload("check", {"path": tmp, "control": "C2.09", "checked": "true"})
            self.assertEqual(after["done"], before + 1)
            coverage_md = security_audit_payload("coverage", {"path": tmp, "lang": "en"})["markdown"]
            self.assertIn("Navin Security audit coverage", coverage_md)
            self.assertIn("C2.02 MFA: bypass and enrolment (no automated check covers it yet", coverage_md)  # navin_auth ran, MFA is not one of its checks
            self.assertIn("C5.07 Tauri/Electron: allowlist, CSP, exposed commands, isolation (no mobile or desktop target", coverage_md)
            self.assertIn("pip_audit", coverage_md)  # dependencies: the scanners that did not run
            body, filename = security_audit_sarif(tmp)
            self.assertEqual(json.loads(body)["version"], "2.1.0")
            self.assertTrue(filename.endswith(".sarif"))
            for action, params, status in (
                ("run", {"path": "relative"}, 400),
                ("run", {"path": "/definitely/not/here"}, 404),
                ("check", {"path": tmp, "control": "C9.99"}, 400),
                ("run", {"path": tmp, "scanners": "maybe"}, 400),
                ("explode", {"path": tmp}, 400),
            ):
                with self.subTest(action=action, params=params), self.assertRaises(SecurityAuditError) as err:
                    security_audit_payload(action, params)
                self.assertEqual(err.exception.status, status)

    def test_http_route_end_to_end(self):
        import types
        import urllib.parse

        from websockets.datastructures import Headers
        from websockets.http11 import Request as WsRequest

        from navin.webui.ws_http import GatewayHTTPHandler

        def call(query: str, *, allowed: bool = True):
            handler = object.__new__(GatewayHTTPHandler)
            handler.check_api_token = lambda _request: allowed  # the token check itself has its own tests
            request = WsRequest(path=f"/api/webui/security-audit?{query}", headers=Headers([("Host", "127.0.0.1:18790")]))
            connection = types.SimpleNamespace(remote_address=("127.0.0.1", 51515))
            return asyncio.run(handler._dispatch_misc_routes(connection, request, "/api/webui/security-audit"))

        with _project() as tmp, mock.patch.object(audit_mod, "detect_scanners", _statuses(set())):
            path = urllib.parse.quote(tmp, safe="")
            self.assertEqual(call("action=catalog", allowed=False).status_code, 401)
            listed = call("action=catalog")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(json.loads(listed.body)["totals"]["controls"], len(catalog.CONTROLS))
            ran = call(f"action=run&scanners=false&path={path}")
            self.assertEqual(ran.status_code, 200, ran.body[:300])
            audit_id = json.loads(ran.body)["audit"]["id"]
            self.assertEqual(json.loads(call(f"action=last&path={path}").body)["audit"]["id"], audit_id)
            ticked = json.loads(call(f"action=check&control=C2.09&checked=true&path={path}").body)
            self.assertTrue(next(i for i in ticked["items"] if i["id"] == "C2.09")["manual"])
            sarif = call(f"action=sarif&path={path}")
            self.assertEqual(sarif.status_code, 200)
            self.assertEqual(json.loads(sarif.body)["version"], "2.1.0")
            self.assertIn("application/sarif+json", sarif.headers["Content-Type"])
            self.assertIn(".sarif", sarif.headers["Content-Disposition"])
            self.assertEqual(call("action=run&path=relative").status_code, 400)
            self.assertEqual(call("action=last&path=%2Fdefinitely%2Fnot%2Fhere").status_code, 404)

    def test_the_route_is_wired_off_the_loop(self):
        source = (Path(__file__).resolve().parents[1] / "navin" / "webui" / "ws_http.py").read_text(encoding="utf-8")
        self.assertIn('if got == "/api/webui/security-audit":', source)
        handler = source.split("async def _handle_webui_security_audit", 1)[1].split("\n    async def ", 1)[0]
        self.assertIn("check_api_token", handler)
        self.assertEqual(handler.count("asyncio.to_thread("), 2)


if __name__ == "__main__":
    unittest.main()
