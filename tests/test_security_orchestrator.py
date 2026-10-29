# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security orchestrator: discovery, threat model, plan, correlation, legacy bridge."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.security import catalog
from navin.security.bridge import family_for, finding_from_legacy, merge_legacy
from navin.security.model import Evidence, Finding
from navin.security.orchestrator import (
    build_threat_model,
    correlate,
    discover,
    plan_audit,
    route_skills,
    skill_path,
)
from navin.security.orchestrator.scan_planner import AuditInputs, reachable_levels, select_skills
from navin.security.orchestrator.skill_router import skill_scanners


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _saas_project(root: Path) -> None:
    _write(root, "package.json", json.dumps({
        "name": "shop", "dependencies": {"next": "14.0.0", "react": "18.2.0", "@supabase/supabase-js": "2.39.0",
                                         "stripe": "14.0.0", "jsonwebtoken": "9.0.0", "openai": "4.0.0"},
        "devDependencies": {"vitest": "1.0.0"},
    }))
    _write(root, "api/pyproject.toml", '[project]\nname = "api"\ndependencies = ["fastapi>=0.110", "asyncpg", "redis", "python-multipart"]\n')
    _write(root, "api/app/routes.py", (
        "from fastapi import APIRouter, Request\n"
        "router = APIRouter()\n"
        "@router.get('/api/invoices/{invoice_id}')\n"
        "async def invoice(invoice_id: int, request: Request):\n"
        "    tenant_id = request.headers.get('x-tenant')\n"
        "    return await db.fetch(f\"select * from invoices where id = {invoice_id}\")\n"
        "@router.post('/api/checkout')\n"
        "async def checkout(price: float, role: str = 'member'):\n"
        "    total = price\n"
        "    return total\n"
    ))
    _write(root, "supabase/migrations/0001_init.sql", "create table invoices (id int, tenant_id uuid);\nalter table invoices enable row level security;\ncreate policy tenant_read on invoices using (tenant_id = auth.uid());\n")
    _write(root, "Dockerfile", "FROM node:20\nUSER root\nCMD [\"node\", \"server.js\"]\n")
    _write(root, ".github/workflows/ci.yml", "on: pull_request_target\njobs: {}\n")
    _write(root, ".env", "STRIPE_KEY=sk_test_placeholder\n")
    _write(root, "web/components/Post.tsx", "export const Post = ({html}) => <div dangerouslySetInnerHTML={{__html: html}} />\n")


class DiscoveryTest(unittest.TestCase):
    def test_discovers_stack_surfaces_and_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _saas_project(root)
            found = discover(root, base_url="http://127.0.0.1:3000")
        self.assertIn("nextjs", found.frameworks)
        self.assertIn("fastapi", found.frameworks)
        self.assertIn("supabase", found.databases)
        self.assertIn("postgresql", found.databases)
        self.assertIn("jwt", found.auth)
        self.assertIn("stripe", found.payments)
        self.assertIn("llm", found.ai)
        self.assertIn("docker", found.infrastructure)
        self.assertIn("github_actions", found.infrastructure)
        self.assertIn("multitenant", found.features)
        self.assertIn("rls", found.features)
        self.assertIn("api/app/routes.py", found.entrypoints)
        for surface in ("web", "api", "auth", "database", "multitenant", "payments", "docker", "ai", "frontend"):
            self.assertIn(surface, found.surfaces, surface)
        self.assertEqual(found.base_url, "http://127.0.0.1:3000")
        self.assertEqual(found.languages.get("python"), 1)
        self.assertGreaterEqual(found.files_seen, 8)

    def test_invalid_manifest_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "package.json", "{not json")
            found = discover(root)
        self.assertTrue(any("invalid JSON" in note for note in found.notes))
        self.assertEqual(found.frameworks, [])

    def test_missing_root_raises(self):
        with self.assertRaises(NotADirectoryError):
            discover(Path("/nonexistent/navin-security-root"))


class ThreatModelAndPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _saas_project(root)
        self.found = discover(root)
        self.model = build_threat_model(self.found)

    def tearDown(self):
        self.tmp.cleanup()

    def test_threat_model_names_assets_boundaries_and_scenarios(self):
        asset_ids = {a.id for a in self.model.assets}
        self.assertTrue({"database", "identities", "tenants", "money", "secrets", "infrastructure", "agent"} <= asset_ids)
        boundary_ids = {b.id for b in self.model.boundaries}
        self.assertIn("tenant_tenant", boundary_ids)
        self.assertIn("content_model", boundary_ids)
        scenario_ids = {s.id for s in self.model.scenarios}
        self.assertTrue({"tenant_leak", "price", "idor", "prompt_injection", "secret_in_repo"} <= scenario_ids)
        top = self.model.top_families(limit=5)
        self.assertIn("multitenancy", top)
        self.assertIn("business_logic", top)
        self.assertIn("ai_agents", top)
        for scenario in self.model.scenarios:
            self.assertTrue(scenario.controls, scenario.id)

    def test_skill_router_activates_the_stack_skills(self):
        skills = select_skills(self.found, self.model)
        for expected in ("security-frontend", "security-api", "security-authentication", "security-authorization",
                         "security-multitenancy", "security-secrets", "security-supply-chain", "security-containers",
                         "security-ai-agents", "security-business-logic", "security-threat-modeling"):
            self.assertIn(expected, skills, expected)
        self.assertNotIn("security-mobile", skills)
        self.assertNotIn("security-desktop", skills)
        self.assertNotIn("security-cloud", skills)
        # Chaining and remediation wait for findings: never routed up-front.
        self.assertNotIn("security-attack-chaining", skills)
        self.assertNotIn("security-remediation", skills)

    def test_plan_names_why_each_skill_is_loaded(self):
        plan = plan_audit(self.found, self.model, AuditInputs())
        self.assertEqual([route["id"] for route in plan.skill_routes], plan.skills)
        routes = {route["id"]: route for route in plan.skill_routes}
        self.assertEqual(routes["security-threat-modeling"]["reason"], "always on")
        self.assertIn("stack: database", routes["security-injection"]["reason"])
        self.assertIn("docker", routes["security-containers"]["triggers"])
        for route in plan.skill_routes:
            self.assertTrue(route["reason"], route["id"])

    def test_levels_follow_the_inputs(self):
        levels, missing = reachable_levels(AuditInputs())
        self.assertEqual(levels, ["L1"])
        self.assertEqual(missing["L2"], ["base_url"])
        self.assertIn("two test tenants", missing["L3"])
        self.assertIn("human approval before sensitive operations", missing["L4"])
        full = AuditInputs(base_url="http://x", accounts={"admin": "a", "member": "b"}, tenants=["A", "B"],
                           authorized_targets=["http://x"], isolated_environment=True, human_approval=True, ci_integration=True)
        levels, missing = reachable_levels(full)
        self.assertEqual(levels, ["L1", "L2", "L3", "L4", "L5"])
        self.assertEqual(missing, {})

    def test_plan_is_static_only_without_a_target_and_lists_what_is_not_covered(self):
        plan = plan_audit(self.found, self.model, AuditInputs(), available_scanners={"gitleaks"})
        self.assertEqual(plan.levels, ["L1"])
        self.assertIn("C1.01", plan.controls)
        self.assertIn("C1.10", plan.controls)
        self.assertNotIn("C2.07", plan.controls)  # dynamic: needs a base_url
        self.assertNotIn("C5.01", plan.controls)  # no native target
        self.assertIn("C6.02", plan.controls)  # AI detected, static control
        self.assertIn("gitleaks", plan.scanners)
        self.assertNotIn("semgrep", plan.scanners)  # not installed
        self.assertIn("navin_rules", plan.scanners)  # native engine always usable
        self.assertEqual(plan.steps[0].level, "L1")
        self.assertIn("C2.07", plan.not_covered)
        self.assertEqual(len(plan.controls) + len(plan.not_covered), 70)

    def test_plan_reaches_architecture_with_accounts_and_tenants(self):
        inputs = AuditInputs(base_url="http://127.0.0.1:3000", accounts={"admin": "a", "member": "b"}, tenants=["A", "B"])
        plan = plan_audit(self.found, self.model, inputs)
        self.assertEqual(plan.levels, ["L1", "L2", "L3"])
        self.assertIn("C2.09", plan.controls)
        self.assertIn("C3.08", plan.controls)
        self.assertIn("navin_tenant", plan.scanners)
        self.assertIn("L4", plan.missing_inputs)


class CorrelatorTest(unittest.TestCase):
    def test_chains_idor_secret_and_cloud_access(self):
        idor = Finding(id="idor", family="authorization", title="IDOR on /api/invoices/{id}", severity="high",
                       endpoint="GET /api/invoices/123", cwe="CWE-639", impact="disclosure",
                       evidence=[Evidence(kind="dynamic", description="tenant B read invoice of tenant A")])
        secret = Finding(id="key", family="secrets", title="AWS key in invoice payload", severity="high",
                         file_path="api/app/routes.py", line=8, cwe="CWE-798")
        cloud = Finding(id="iam", family="infrastructure", title="IAM user has AdministratorAccess", severity="critical",
                        component="aws", impact="compromise")
        noise = Finding(id="info", family="audit_logging", title="no request id", severity="info")
        result = correlate([idor, secret, cloud, noise, idor])
        self.assertEqual(len(result.findings), 4)
        self.assertEqual(result.findings[0].status, "confirmed")
        self.assertIn("C2.07", result.findings[0].controls)
        titles = [p.title for p in result.attack_paths]
        self.assertIn("Authorization -> Secrets -> Infrastructure", titles)
        chain = next(p for p in result.attack_paths if len(p.steps) == 3)
        self.assertEqual(chain.impact, "compromise")
        self.assertEqual(chain.severity, "critical")
        self.assertEqual(chain.status, "hypothesis")
        self.assertEqual(chain.steps[1]["link"], "an unauthorized read returns a response that carries a secret")
        self.assertEqual(result.control_status["C2.07"], "confirmed")
        self.assertEqual(result.control_status["C1.01"], "clean")
        self.assertEqual(result.families_hit["authorization"], 1)
        self.assertEqual(result.follow_up_skills, ["security-attack-chaining", "security-remediation"])

    def test_unplanned_controls_are_not_assessed_not_clean(self):
        result = correlate([], planned_controls=["C1.01"])
        self.assertEqual(result.control_status["C1.01"], "clean")
        self.assertEqual(result.control_status["C2.07"], "not_assessed")
        self.assertEqual(result.attack_paths, [])
        self.assertEqual(result.follow_up_skills, [])


class SkillRouterTest(unittest.TestCase):
    def test_an_empty_stack_keeps_only_the_always_on_skills(self):
        routes = route_skills([])
        self.assertEqual(
            [r.id for r in routes],
            ["security-threat-modeling", "security-sast-review", "security-cryptography",
             "security-secrets", "security-supply-chain", "security-compliance"],
        )
        self.assertTrue(all(r.reasons == ["always on"] for r in routes))

    def test_a_route_carries_its_controls_scanners_and_playbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "security-injection/SKILL.md", "---\nname: security-injection\n---\n")
            routes = {r.id: r for r in route_skills(["database", "web"], root=root)}
            self.assertEqual(routes["security-injection"].path, str(root / "security-injection" / "SKILL.md"))
            self.assertIsNone(skill_path("security-api", root))
        injection = routes["security-injection"]
        self.assertEqual(injection.reasons, ["stack: database, web"])
        self.assertEqual(injection.controls, ["C1.01", "C1.02", "C1.03", "C1.04"])
        self.assertEqual(injection.scanners, ["semgrep", "bandit"])
        self.assertIn("SQLi", injection.teaches["en"])
        self.assertNotIn("security-api", routes)  # no api, graphql or websocket surface
        self.assertEqual(routes["security-frontend"].path, "")  # routed by web, playbook not under that root
        # Registry order, and only scanners the registry can run.
        self.assertEqual(routes["security-supply-chain"].scanners,
                         ["gitleaks", "trivy", "osv_scanner", "pip_audit", "npm_audit", "cargo_audit"])
        self.assertEqual(routes["security-supply-chain"].to_dict()["reason"], "always on")

    def test_findings_route_the_owner_skill_even_when_discovery_missed_the_surface(self):
        leak = Finding(id="apk-key", family="mobile_desktop", title="API key in the APK", severity="high",
                       file_path="android/app/src/main/res/values/strings.xml", line=3)
        result = correlate([leak])
        routes = {r.id: r for r in route_skills([], result)}
        self.assertEqual(routes["security-mobile"].reasons, ["findings: mobile_desktop (1)"])
        self.assertEqual(routes["security-mobile"].triggers, ["findings"])
        self.assertEqual(routes["security-remediation"].reasons, ["findings to fix and validate"])
        self.assertNotIn("security-attack-chaining", routes)  # one finding: nothing to chain
        self.assertNotIn("security-desktop", routes)  # the family has one owner

    def test_attack_paths_route_chaining_next_to_the_stack(self):
        idor = Finding(id="idor", family="authorization", title="IDOR", severity="high", endpoint="GET /api/x/1",
                       cwe="CWE-639", evidence=[Evidence(kind="dynamic", description="read another tenant")])
        secret = Finding(id="key", family="secrets", title="key in payload", severity="high", file_path="a.py", line=1)
        result = correlate([idor, secret])
        self.assertTrue(result.attack_paths)
        routes = {r.id: r for r in route_skills(["api"], result)}
        self.assertEqual(routes["security-attack-chaining"].reasons, ["attack paths link several findings"])
        self.assertEqual(routes["security-authorization"].reasons, ["stack: api", "findings: authorization (1)"])
        self.assertEqual(routes["security-secrets"].reasons, ["always on", "findings: secrets (1)"])

    def test_an_unknown_skill_is_refused(self):
        with self.assertRaisesRegex(KeyError, "unknown security skill"):
            skill_path("security-nope")


class SkillPlaybooksTest(unittest.TestCase):
    """The 20 shipped security-* playbooks stay in step with the catalog."""

    def test_every_catalog_skill_ships_a_playbook_that_matches_it(self):
        self.assertEqual(len(catalog.SKILLS), 20)
        for spec in catalog.SKILLS:
            path = skill_path(spec.id)
            self.assertIsNotNone(path, f"{spec.id} has no SKILL.md")
            text = path.read_text(encoding="utf-8")
            head = text.split("---")[1]
            self.assertIn(f"name: {spec.id}\n", head)
            self.assertIn("description: ", head)
            for ctl in catalog.CONTROLS:
                if ctl.skill == spec.id:
                    self.assertIn(f"| {ctl.id} |", text, f"{spec.id} omits {ctl.id}")
            for scanner in skill_scanners(spec.id):
                self.assertIn(scanner, text, f"{spec.id} omits scanner {scanner}")
            for section in ("## Method", "## Evidence and status", "## Anti-patterns"):
                self.assertIn(section, text, f"{spec.id} lacks {section}")
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)

    def test_routes_of_a_full_stack_all_point_at_a_real_playbook(self):
        surfaces = {s for spec in catalog.SKILLS for s in spec.triggers if s not in {"always", "findings"}}
        routes = route_skills(surfaces)
        self.assertEqual(len(routes), 18)  # the two finding-driven skills wait for findings
        for route in routes:
            self.assertTrue(Path(route.path).is_file(), route.id)


class LegacyBridgeTest(unittest.TestCase):
    def test_family_mapping_prefers_cwe_over_category(self):
        self.assertEqual(family_for("Misconfiguration", "CWE-89"), "injection")
        self.assertEqual(family_for("Deserialization", ""), "code_execution")
        self.assertEqual(family_for("unknown-category", ""), "infrastructure")

    def test_legacy_rows_become_findings_with_evidence(self):
        rows = [
            {"id": "py-sql-fstring", "severity": "high", "category": "Injection", "summary": "SQL f-string",
             "recommendation": "use parameters", "file_path": "app/views.py", "line": 7, "cwe": "CWE-89",
             "confidence": "high", "snippet": "cur.execute(f\"...\")"},
            {"id": "py-sql-fstring", "severity": "medium", "category": "Injection", "summary": "SQL f-string",
             "file_path": "app/views.py", "line": 8, "cwe": "CWE-89", "confidence": 0.4},
        ]
        merged = merge_legacy(rows, source="navin_rules")
        self.assertEqual(len(merged), 1)
        top = merged[0]
        self.assertEqual(top.family, "injection")
        self.assertEqual(top.severity, "high")
        self.assertEqual(top.status, "potential")
        self.assertEqual(top.confidence, 0.8)
        self.assertEqual(top.evidence[0].observed, 'cur.execute(f"...")')
        single = finding_from_legacy({"id": "x", "category": "Container", "line": "oops"})
        self.assertEqual(single.family, "infrastructure")
        self.assertIsNone(single.line)


if __name__ == "__main__":
    unittest.main()
