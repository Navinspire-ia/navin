#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Regenerate the Navin Cyber audit deliverables in docs/security/.

What it writes (all from the code and from real runs, nothing typed by hand except the per-family plans):

- navin_cyber_appsec_checklist.json / .md   the 302-control referential;
- cyber-coverage-matrix.json                 one row per control: implemented_and_tested | implemented_not_tested |
                                             partial | missing | blocked | not_applicable, with code and test references;
- cyber-gap-analysis.md                      the gaps by priority (P0 / P1 / P2) with a correction plan;
- cyber-test-plan.md                         scenarios and fixtures by surface;
- cyber-execution-evidence/                  an audit run on a synthetic project (engines and versions, matrix, findings,
                                             row-level security tests in a throwaway PostgreSQL, passive web checks on a local
                                             server), the pytest run of the security suites and the environment.

Run it from the repository root with the project's environment (navin is installed in it):
    .venv/bin/python scripts/cyber_coverage_report.py [--no-pytest] [--self]
Set NAVIN_PG_BIN to a PostgreSQL bin folder (initdb, pg_ctl, psql, postgres) to include the database run when the
machine has none on PATH. ``--self`` also audits this repository itself (counts only, no excerpt).
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from navin._version import __version__ as navin_version
from navin.security.analyzers.db_introspect import find_postgres_binaries
from navin.security.appsec import capabilities as caps
from navin.security.appsec import referential as ref
from navin.security.appsec.matrix import build_matrix, matrix_markdown
from navin.security.orchestrator.audit import run_audit
from navin.security.orchestrator.scan_planner import AuditInputs
from navin.security.scanners.registry import REGISTRY, detect_scanners

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "security"
EVIDENCE = OUT / "cyber-execution-evidence"
SECURITY_TESTS = [
    "tests/test_security_catalog.py", "tests/test_security_analyzers.py", "tests/test_security_audit.py",
    "tests/test_security_scanners.py", "tests/test_security_reports.py", "tests/test_security_orchestrator.py",
    "tests/test_security_rules.py", "tests/test_security_verification.py",
]
ENGINE_FILES = {
    caps.DISC: "navin/security/orchestrator/discovery.py", caps.TM: "navin/security/orchestrator/threat_model.py",
    caps.MX: "navin/security/appsec/matrix.py", caps.RULES: "navin/security/rules.py",
    caps.DF: "navin/security/analyzers/dataflow.py", caps.AUTH: "navin/security/analyzers/auth.py",
    caps.TEN: "navin/security/analyzers/tenant.py", caps.BL: "navin/security/analyzers/business_logic.py",
    caps.DB: "navin/security/analyzers/database.py", caps.DS: "navin/security/analyzers/datastores.py",
    caps.DBE: "navin/security/analyzers/db_introspect.py", caps.WEB: "navin/security/verification/web_baseline.py",
}
ENGINE_TESTS = {
    caps.DISC: "tests/test_security_orchestrator.py", caps.TM: "tests/test_security_orchestrator.py",
    caps.MX: "tests/test_security_audit.py", caps.RULES: "tests/test_security_rules.py",
    caps.DF: "tests/test_security_analyzers.py", caps.AUTH: "tests/test_security_analyzers.py",
    caps.TEN: "tests/test_security_analyzers.py", caps.BL: "tests/test_security_analyzers.py",
    caps.DB: "tests/test_security_analyzers.py", caps.DS: "tests/test_security_analyzers.py",
    caps.DBE: "tests/test_security_analyzers.py", caps.WEB: "tests/test_security_verification.py",
}
STATUSES = ("implemented_and_tested", "implemented_not_tested", "partial", "missing", "blocked", "not_applicable")
RISK_OF_GAP = {"P0": "high", "P1": "medium", "P2": "low"}

# What it takes to close a family: tools to reuse, what to integrate, how to accept it.
PLANS: dict[str, dict[str, str]] = {
    "DISC": {
        "reuse": "discovery.py (modules, sous-modules, branches, points d'entrée), Syft pour l'inventaire d'artefacts",
        "build": "lien artefact / commit / déploiement (DISC-010), composants orphelins (DISC-013), versions des frameworks résolues depuis les lockfiles (DISC-002), énumération DNS seulement sur une cible fournie (DISC-005)",
        "accept": "fixture monorepo à trois modules et un sous-module: chaque module, branche, point d'entrée et classe de fichier attendus; un composant sans référence est listé",
    },
    "ARCH": {
        "reuse": "threat_model.py, metagraph et code_index du projet",
        "build": "graphe d'architecture exportable et revue guidée par scénario; l'essentiel reste humain, Navin fournit les scénarios et consigne la décision",
        "accept": "chaque scénario de menace du modèle est lié à un test exécutable ou à une décision humaine datée",
    },
    "SAST": {
        "reuse": "Semgrep et Bandit (déjà au registre), règles Navin, analyse de flux Python; CodeQL si la licence le permet",
        "build": "analyse de flux JavaScript/TypeScript, Java, Go et C# (aujourd'hui des motifs sans flux), XQuery, ReDoS par analyse de complexité (recheck), XSS DOM, concurrence et TOCTOU",
        "accept": "pour chaque langage, un projet vulnérable et un projet sain: chaque classe SAST détectée dans le premier, silence dans le second",
    },
    "AUTH": {
        "reuse": "analyzers/auth.py (JWT, limitation de tentatives, webhooks, routes sans authentification)",
        "build": "politique de mots de passe, MFA, récupération de compte, OAuth2/OIDC (redirect URI, PKCE, state, nonce), durée des sessions, avec comptes synthétiques sur une cible isolée",
        "accept": "application de test avec deux comptes: chaque défaut volontaire (state absent, PKCE absent, session sans expiration) est trouvé et chaque contrôle sain passe",
    },
    "AUTHZ": {
        "reuse": "verification/policy.py et probe.py (périmètre, lecture seule, rédaction des secrets), web_baseline.py",
        "build": "module authz_matrix: matrice rôles-ressources, deux comptes synthétiques, BOLA, BFLA, BOPLA et mass assignment rejoués depuis l'OpenAPI, isolation entre tenants",
        "accept": "API de test avec une faille IDOR, une faille BFLA et un champ protégé modifiable: les trois sont trouvées, le compte A ne lit jamais l'objet de B sur la version corrigée",
    },
    "BUS": {
        "reuse": "business_logic.py (montants et prix venant du client)",
        "build": "machines à états décrites par le projet et rejouées sur une cible isolée, rafales concurrentes bornées pour la double dépense, idempotence",
        "accept": "workflow de test avec un saut d'étape et une double soumission possibles: les deux sont reproduits, puis refusés après correction",
    },
    "SECRET": {
        "reuse": "gitleaks sur l'arbre et sur l'historique, Trivy, règles Navin",
        "build": "secrets dans images, binaires et bundles JS (gitleaks sur les bundles, trivy image), journaux et artefacts CI; rotation et durée de vie restent des contrôles humains",
        "accept": "dépôt de test avec un secret supprimé de l'arbre mais présent dans l'historique, un secret dans un bundle: les deux sont trouvés, masqués dans le rapport",
    },
    "CRYP": {
        "reuse": "règles de cryptographie, web_baseline.py (HSTS, redirection)",
        "build": "configuration TLS d'une cible (sslyze ou testssl.sh), réutilisation de nonce, chiffrement des sauvegardes, rejeu",
        "accept": "serveur de test en TLS 1.0 et avec un certificat expiré: les deux sont signalés; version saine silencieuse",
    },
    "API": {
        "reuse": "web_baseline.py (introspection GraphQL, OpenAPI exposé, CORS), auth.py",
        "build": "comparaison routes / spécification (OpenAPI, GraphQL, protobuf), validation de schéma, limitation de débit dynamique, WebSocket, BOLA et BFLA",
        "accept": "API de test dont une route manque à la spécification et une autre n'est pas protégée: écart et défaut signalés",
    },
    "WEB": {
        "reuse": "web_baseline.py (contrôle passif GET/HEAD/OPTIONS borné)",
        "build": "DAST actif en préproduction (OWASP ZAP baseline puis full) avec garde-fous: XSS, CSRF, injection, upload, cache et CDN",
        "accept": "application volontairement vulnérable en préproduction isolée: chaque défaut actif retrouvé, aucune requête hors périmètre",
    },
    "DB": {
        "reuse": "database.py, datastores.py, db_introspect.py (PostgreSQL éphémère: SELECT, INSERT, UPDATE, DELETE en anonyme et avec deux utilisateurs)",
        "build": "tests réels pour MySQL/MariaDB, SQL Server, MongoDB dans des conteneurs éphémères, tenants par appartenance (table de membres), routes serveur utilisant service_role, introspection d'une base fournie depuis l'API, audit des lectures sensibles et des sauvegardes",
        "accept": "pour chaque moteur, un schéma faible et un schéma sain en conteneur jetable: chaque défaut confirmé par une requête, rien sur la base saine",
    },
    "DATA": {
        "reuse": "data_signals de discovery.py",
        "build": "classification des données (Presidio ou équivalent), cartographie des exports, des caches et des index; l'essentiel reste humain",
        "accept": "jeu de données synthétique avec des champs personnels: chacun classé, l'export et le cache qui le contiennent listés",
    },
    "SCA": {
        "reuse": "osv_scanner, trivy, pip_audit, npm_audit, cargo_audit (déjà au registre)",
        "build": "SBOM CycloneDX (Syft ou Trivy), CISA KEV et EPSS, paquets abandonnés (deps.dev), typosquatting, scripts d'installation, provenance (sigstore), licences",
        "accept": "projet avec une dépendance vulnérable connue exploitée, un paquet abandonné et un lockfile incohérent: trois constats, SBOM valide",
    },
    "CICD": {
        "reuse": "règles GitHub Actions, outil forge du projet",
        "build": "protection des branches et CODEOWNERS via l'API de la forge, OpenSSF Scorecard, signatures de commits, provenance SLSA",
        "accept": "dépôt de test sans protection de branche et avec un workflow injectable: les deux sont signalés",
    },
    "IAC": {
        "reuse": "règles Dockerfile, Kubernetes et Terraform; Checkov (au catalogue, hors registre); Trivy config",
        "build": "rendu Helm et Kustomize avant analyse, CloudFormation, NetworkPolicy, admission et Pod Security Standards, dérive Terraform",
        "accept": "chart Helm avec un conteneur privilégié et sans NetworkPolicy: signalés après rendu",
    },
    "RUN": {
        "reuse": "web_baseline.py (debug, actuator), règles de configuration",
        "build": "lecture de la configuration effective d'une cible, comparaison avec l'attendue, comportement fail-closed",
        "accept": "cible de test avec debug actif en production: signalé avec l'écart de configuration",
    },
    "MOBILE": {
        "reuse": "aucun moteur mobile: seulement les règles TLS du code source",
        "build": "MobSF en conteneur et apkleaks, lecture du manifeste Android (composants exportés) et du plist iOS (ATS, schémas d'URL)",
        "accept": "APK de test avec un composant exporté et du trafic en clair: les deux signalés",
    },
    "DESK": {
        "reuse": "règles de permissions et d'injection de commandes",
        "build": "analyse de tauri.conf.json (CSP, accès aux API) et d'Electron (nodeIntegration, contextIsolation), vérification des signatures de binaires, clés du mécanisme de mise à jour",
        "accept": "configuration Tauri avec CSP absente et Electron avec nodeIntegration actif: signalées",
    },
    "AI": {
        "reuse": "règles de clés LLM; compétence prompt-injection-defender du projet",
        "build": "analyse de la configuration MCP (serveurs non épinglés, portées trop larges), garak ou promptfoo contre un agent en bac à sable, isolation du RAG et de la mémoire, exfiltration par outils",
        "accept": "agent de test avec un outil shell et un document piégé: l'injection indirecte est reproduite en bac à sable puis bloquée après correction",
    },
    "OBS": {
        "reuse": "règles de journaux sensibles",
        "build": "revue des journaux structurés et des alertes; contrôles surtout humains",
        "accept": "journal de test contenant un jeton: signalé; absence d'alerte sur accès interdit consignée",
    },
    "SRV": {
        "reuse": "règles Terraform (jokers IAM)",
        "build": "analyse de serverless.yml, SAM, fonctions Terraform: authentification des déclencheurs, IAM minimal, délais, reprises et files de rebut",
        "accept": "fonction de test sans authentification et avec un rôle administrateur: les deux signalées",
    },
    "BIN": {
        "reuse": "cargo_audit, règles C et Rust unsafe",
        "build": "drapeaux de durcissement (checksec), harnais de fuzzing (cargo-fuzz, AFL++), modules WebAssembly",
        "accept": "binaire de test sans PIE ni canari: signalé; parseur fuzzé 60 secondes sans plantage",
    },
    "INT": {
        "reuse": "discovery.py (SDK tiers), auth.py (signature des webhooks)",
        "build": "portées des connecteurs, rotation des clés, URLs pré-signées, séparation des tenants chez les tiers",
        "accept": "projet avec un connecteur à portée excessive et un webhook non signé: les deux signalés",
    },
    "QA": {
        "reuse": "appsec/matrix.py, moteur de vérification",
        "build": "crochet de pull request et de release qui relance l'audit, suivi des faux positifs, propriétaires des lacunes",
        "accept": "une pull request qui ajoute une faille fait échouer le contrôle; le rapport machine liste les lacunes avec un responsable",
    },
}

SURFACE_PLANS = (
    ("Web", [
        ("Passif (déjà testé)", "serveur local faible puis durci", "tests/test_security_verification.py::WebBaselineTest",
         "en-têtes, cookies, CORS, source maps, debug exposé trouvés sur le faible; rien sur le durci; seulement GET, HEAD et OPTIONS; aucune requête hors origine"),
        ("DAST actif (à construire)", "application vulnérable en préproduction isolée, ZAP baseline puis full",
         "tests/test_security_dast.py (à créer)", "XSS, CSRF, injection, upload retrouvés; budget de requêtes respecté; cible hors périmètre refusée"),
    ]),
    ("API", [
        ("Authentification des routes (déjà testé)", "projet Flask de test", "tests/test_security_analyzers.py::AuthTest",
         "routes sans authentification, JWT sans algorithmes, webhooks non signés trouvés"),
        ("BOLA, BFLA, BOPLA (à construire)", "API de test, deux comptes synthétiques, OpenAPI",
         "tests/test_security_authz.py (à créer)", "le compte A ne lit, ne modifie, ne supprime aucun objet de B; champs protégés refusés"),
        ("Routes contre spécification (à construire)", "API avec une route absente de l'OpenAPI",
         "tests/test_security_api_spec.py (à créer)", "écart signalé dans les deux sens"),
    ]),
    ("Bases de données et RLS", [
        ("PostgreSQL / Supabase (déjà testé)", "migrations faibles et saines, PostgreSQL éphémère",
         "tests/test_security_analyzers.py::DatabaseTest::test_ephemeral_cluster_confirms_the_static_findings",
         "lecture anonyme, lecture entre utilisateurs, insertion avec le propriétaire d'un autre, changement de propriétaire constatés; table saine silencieuse; COPY TO PROGRAM refusé"),
        ("Autres moteurs, analyse statique (déjà testé)", "scripts MySQL, SQL Server, Oracle, configs Redis, MongoDB, Elasticsearch, Cassandra, règles Firebase, Compose, chaînes de connexion",
         "tests/test_security_analyzers.py::DatabaseTest::test_other_datastores_mysql_sqlserver_oracle_nosql_configs_and_connections",
         "chaque règle ds-* déclenchée sur le faible, aucune sur le durci"),
        ("Autres moteurs, base réelle (à construire)", "conteneurs jetables MySQL, SQL Server, MongoDB",
         "tests/test_security_datastores_live.py (à créer)", "comptes, privilèges et rôles lus dans le catalogue; défauts confirmés par une requête"),
        ("Tenants par appartenance (à construire)", "schéma avec table de membres et politique par organisation",
         "tests/test_security_analyzers.py (à étendre)", "un membre de l'organisation 1 ne lit rien de l'organisation 2"),
    ]),
    ("Mobile", [
        ("Android et iOS (à construire)", "APK et IPA minimaux, un composant exporté, du trafic en clair, une clé en dur",
         "tests/test_security_mobile.py (à créer)", "manifeste et plist analysés; chaque défaut volontaire trouvé; MobSF exécuté et sa version consignée"),
    ]),
    ("Desktop", [
        ("Tauri et Electron (à construire)", "tauri.conf.json avec CSP absente, Electron avec nodeIntegration actif, binaire non signé",
         "tests/test_security_desktop.py (à créer)", "chaque défaut signalé; signature vérifiée avec l'outil du système"),
    ]),
    ("Infrastructure", [
        ("Dockerfile, Kubernetes, Terraform, GitHub Actions (déjà testé)", "fichiers faibles et sains", "tests/test_security_rules.py",
         "règles tf-*, k8s-*, docker-*, gha-* déclenchées sur le faible, silence sur le sain"),
        ("Helm, Kustomize, NetworkPolicy (à construire)", "chart rendu puis analysé", "tests/test_security_iac_render.py (à créer)",
         "conteneur privilégié et absence de NetworkPolicy trouvés après rendu"),
    ]),
    ("IA, agents et MCP", [
        ("Configuration MCP et agents (à construire)", "configuration MCP avec serveur non épinglé et portée large; agent avec outil shell",
         "tests/test_security_ai.py (à créer)", "serveur non épinglé et portée large signalés; injection indirecte reproduite en bac à sable, outil bloqué après correction"),
    ]),
)


# ---------------------------------------------------------------------------
# Code and test references
# ---------------------------------------------------------------------------


class _Index:
    """Where a quoted identifier (a rule id) is written in the code and in the tests."""

    def __init__(self, files: list[Path]) -> None:
        self.lines: dict[Path, list[str]] = {f: f.read_text(encoding="utf-8").splitlines() for f in files}

    def find(self, token: str) -> str:
        prefix = token.endswith("*")
        core = token[:-1] if prefix else token
        pattern = re.compile(r"[\"']" + re.escape(core) + ("" if prefix else r"[\"']"))
        for path, lines in self.lines.items():
            for n, line in enumerate(lines, start=1):
                if pattern.search(line):
                    return f"{path.relative_to(ROOT).as_posix()}:{n}"
        return ""


def _indexes() -> tuple[_Index, _Index]:
    code = sorted((ROOT / "navin" / "security").rglob("*.py"))
    tests = sorted((ROOT / "tests").glob("test_security_*.py"))
    return _Index(code), _Index(tests)


# ---------------------------------------------------------------------------
# Audit of the existing Cyber mode
# ---------------------------------------------------------------------------


def _scanner_state() -> dict[str, dict[str, Any]]:
    return {sid: {"installed": s.available, "version": s.version, "path": s.path} for sid, s in detect_scanners().items()}


def _row(control: ref.Control, cap: caps.Capability, code: _Index, tests: _Index, scanners: dict[str, dict[str, Any]],
         fixture: dict[str, Any]) -> dict[str, Any]:
    cat = ref.category(control.category)
    tools: list[dict[str, Any]] = []
    for engine in cap.engines:
        if engine in REGISTRY:
            s = scanners.get(engine, {})
            tools.append({"engine": engine, "kind": "scanner", "installed_here": bool(s.get("installed")), "version": s.get("version", "")})
        else:
            tools.append({"engine": engine, "kind": "built_in", "installed_here": True, "version": navin_version,
                          "file": ENGINE_FILES.get(engine, ""), "tests": ENGINE_TESTS.get(engine, "")})
    code_refs = [r for r in dict.fromkeys(code.find(i) for i in cap.finding_ids) if r]
    test_refs = [r for r in dict.fromkeys(tests.find(i) for i in cap.finding_ids) if r]
    built_in = [t for t in tools if t["kind"] == "built_in"]
    scanner_only = bool(tools) and not built_in
    priority = caps.priority(control.id)
    if cap.mode == "automated":
        if scanner_only and not any(t["installed_here"] for t in tools):
            status = "blocked"
            reason = ("every engine of this control is an external scanner and none is installed on this machine: "
                      "the adapters are tested on recorded reports, the binaries were never run here")
        elif cap.finding_ids and not test_refs and not any(e in {caps.DISC, caps.MX, caps.TM} for e in cap.engines):
            status, reason = "implemented_not_tested", "the engine exists but no test asserts any of its finding ids"
        else:
            status, reason = "implemented_and_tested", ""
    elif cap.mode == "partial":
        status, reason = "partial", cap.note
    elif cap.mode == "manual":
        status, reason = "missing", "no automated check: a human reviews it (Navin lists it, the verdict stays human)"
    else:
        status, reason = "missing", cap.note
    gap = "" if status == "implemented_and_tested" else (cap.note or reason)
    return {
        "id": control.id, "category": cat.id, "category_name": cat.name["fr"], "task": control.task,
        "applicability": control.applicability, "verification_mode": control.verification_mode,
        "status": status, "capability": cap.mode, "manual_review": cap.mode == "manual",
        "priority": "" if status == "implemented_and_tested" else priority,
        "risk": "none" if status == "implemented_and_tested" else RISK_OF_GAP[priority],
        "tools": tools, "code_refs": code_refs[:5], "test_refs": test_refs[:5], "requires": list(cap.requires),
        "limits": cap.note, "gap": gap, "reason": reason, "fixture_run": fixture.get(control.id, {}),
        "applicable": True,
    }


def audit_existing(fixture_rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    code, tests = _indexes()
    scanners = _scanner_state()
    return [_row(c, caps.capability(c.id), code, tests, scanners, fixture_rows) for c in ref.CONTROLS]


# ---------------------------------------------------------------------------
# A real run on a synthetic project
# ---------------------------------------------------------------------------

_FIXTURE: dict[str, str] = {
    "app.py": '''
        import sqlite3
        import jwt
        import yaml
        from flask import Flask, request

        app = Flask(__name__)
        db = sqlite3.connect(":memory:")


        @app.route("/users")
        def users():
            name = request.args.get("name")
            return str(db.execute("SELECT * FROM users WHERE name = '%s'" % name).fetchall())


        @app.route("/config", methods=["POST"])
        def config():
            return str(yaml.load(request.data))


        @app.route("/me")
        def me():
            return str(jwt.decode(request.headers["Authorization"], "key"))
    ''',
    "requirements.txt": "flask==3.0.0\npyyaml==6.0.1\npyjwt==2.8.0\n",
    "package.json": '{"dependencies": {"react": "18.2.0", "mysql2": "3.9.0"}}\n',
    "src/db.ts": 'const url = "postgres://app@db:5432/shop?sslmode=disable";\n',
    "supabase/migrations/001_init.sql": """
        create table public.posts (id bigserial primary key, user_id uuid not null, body text not null);
        alter table public.posts enable row level security;
        create policy posts_read on public.posts for select using (true);
        create policy posts_insert on public.posts for insert to authenticated with check (true);
        create policy posts_update on public.posts for update to authenticated using (auth.uid() = user_id) with check (true);
        create table public.notes (id bigserial primary key, user_id uuid not null, body text not null);
        alter table public.notes enable row level security;
        create policy notes_own on public.notes for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
        create table public.audit_log (id bigserial primary key, user_id uuid, event text);
        grant all on public.audit_log to anon;
        create function public.get_secret() returns text language sql security definer as $$ select 'x' $$;
        create view public.all_notes as select * from public.notes;
    """,
    "db/mysql.sql": "CREATE USER 'app'@'%' IDENTIFIED BY '';\nGRANT ALL PRIVILEGES ON *.* TO 'app'@'%';\n",
    "docker-compose.yml": ("services:\n  db:\n    image: mysql:8\n    environment:\n      MYSQL_ROOT_PASSWORD: root\n"
                           "    ports:\n      - \"3306:3306\"\n"),
    "redis.conf": "bind 0.0.0.0\nprotected-mode no\n",
    "Dockerfile": "FROM python:latest\nADD . /app\nCMD [\"python\", \"/app/app.py\"]\n",
    "k8s/deploy.yaml": ("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: shop\nspec:\n  template:\n    spec:\n"
                        "      containers:\n        - name: shop\n          image: shop:1\n          securityContext:\n"
                        "            privileged: true\n"),
    ".github/workflows/ci.yml": ("on: pull_request_target\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
                                 "      - run: echo \"${{ github.event.pull_request.title }}\"\n"),
}


class _WeakSite(BaseHTTPRequestHandler):
    """A deliberately weak local site: the passive checks must find its defects and send only reads."""

    seen: list[str] = []

    def _reply(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        _WeakSite.seen.append(self.command)
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        origin = self.headers.get("Origin", "")
        base = {"Server": "Apache/2.4.1", "Set-Cookie": "sid=abc123; Path=/", "Content-Type": "text/html"}
        if origin:
            base["Access-Control-Allow-Origin"] = origin
        if path == "/":
            self._reply(200, b"<html><script src='/app.js'></script></html>", base)
        elif path == "/app.js.map":
            self._reply(200, b'{"version":3}', {"Content-Type": "application/json"})
        elif path == "/actuator/env":
            self._reply(200, b'{"propertySources":[{"name":"systemEnvironment","properties":{}}]}', {"Content-Type": "application/json"})
        elif path == "/openapi.json":
            self._reply(200, b'{"openapi":"3.0.0"}', {"Content-Type": "application/json"})
        elif path.startswith("/definitely-not-here"):
            self._reply(500, b"Traceback (most recent call last):\n  File 'app.py', line 3", base)
        else:
            self._reply(404, b"not found", base)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        self._reply(204, b"", {"Access-Control-Allow-Origin": self.headers.get("Origin", "*")})

    def log_message(self, *args: Any) -> None:
        return


def _write_fixture(root: Path) -> None:
    for rel, text in _FIXTURE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def fixture_run() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Audit the synthetic project (database and web checks included) and return the evidence and the per-control status."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WeakSite)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fixture-shop"
            root.mkdir()
            _write_fixture(root)
            result = run_audit(root, inputs=AuditInputs(base_url=base_url), scanners=True, database_ephemeral=True)
            data = result.to_dict()
    finally:
        server.shutdown()
        server.server_close()
    methods = sorted(set(_WeakSite.seen))
    data["root"] = "fixture-shop"
    matrix = build_matrix({**data, "root": "fixture-shop"})
    rows = {r["id"]: {"status": r["status"], "reason": r["reason"][:300]} for r in matrix["controls"]}
    evidence = {"started_at": started, "base_url": "http://127.0.0.1:<port> (local weak site)", "methods_received": methods,
                "audit": data, "matrix": matrix}
    return evidence, rows


def write_fixture_evidence(evidence: dict[str, Any]) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    data, matrix = evidence["audit"], evidence["matrix"]
    pg = find_postgres_binaries()
    env = {
        "generated_at": evidence["started_at"], "navin_version": navin_version, "python": platform.python_version(),
        "platform": platform.platform(), "postgres": {"found": bool(pg), "version": getattr(pg, "version", "")},
        "scanners": _scanner_state(),
        "note": "a scanner that is not installed is recorded as such: its controls stay blocked, never passed",
    }
    (EVIDENCE / "environment.json").write_text(json.dumps(env, indent=1) + "\n", encoding="utf-8")
    engines = [{k: e.get(k) for k in ("id", "kind", "status", "version", "findings", "duration_s", "detail", "checks_run")}
               for e in data["engines"]]
    (EVIDENCE / "fixture-engines.json").write_text(json.dumps(engines, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    findings = [{"id": f["id"], "severity": f["severity"], "status": f.get("status"), "cwe": f.get("cwe"), "source": f.get("source"),
                 "where": f"{f.get('file_path')}:{f.get('line')}" if f.get("file_path") else f.get("endpoint", ""),
                 "controls": f.get("controls"), "observed": ((f.get("evidence") or [{}])[0]).get("observed", "")[:300]}
                for f in data["findings"]]
    (EVIDENCE / "fixture-findings.json").write_text(json.dumps(findings, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    runtime = data.get("runtime_reports", {})
    (EVIDENCE / "fixture-database-role-tests.json").write_text(json.dumps(runtime.get("database", {}), indent=1) + "\n", encoding="utf-8")
    (EVIDENCE / "fixture-web-baseline.json").write_text(
        json.dumps({**runtime.get("web", {}), "methods_received_by_the_site": evidence["methods_received"]}, indent=1) + "\n", encoding="utf-8")
    (EVIDENCE / "fixture-matrix-summary.json").write_text(
        json.dumps({"summary": matrix["summary"], "applicability": matrix["applicability"], "categories": matrix["categories"]},
                   indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (EVIDENCE / "fixture-matrix.md").write_text(matrix_markdown(matrix), encoding="utf-8")


def self_audit() -> dict[str, Any]:
    """The audit of this repository, counts only: findings quote code, so no excerpt is kept."""
    started = time.monotonic()
    result = run_audit(ROOT, scanners=False)
    data = result.to_dict()
    sev = Counter(f["severity"] for f in data["findings"])
    return {
        "root": ROOT.name, "duration_s": round(time.monotonic() - started, 1), "files_seen": data["discovery"].get("files_seen"),
        "truncated": bool(data["discovery"].get("truncated")), "surfaces": data["discovery"].get("surfaces"),
        "findings": len(data["findings"]), "by_severity": dict(sev), "matrix_summary": data["matrix"]["summary"],
        "note": "pattern-based and dataflow findings of the repository itself: a signal to review, not a verdict",
    }


def pytest_evidence() -> str:
    cmd = [sys.executable, "-m", "pytest", *SECURITY_TESTS, "-q", "--tb=line"]
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
    tail = "\n".join(proc.stdout.strip().splitlines()[-12:])
    return f"$ {' '.join(['python', '-m', 'pytest', *SECURITY_TESTS, '-q', '--tb=line'])}\nstarted: {started}\nexit code: {proc.returncode}\n\n{tail}\n"


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(r["status"] for r in rows)
    by_priority = Counter(r["priority"] for r in rows if r["priority"])
    tested = by_status["implemented_and_tested"]
    applicable = len(rows) - by_status["not_applicable"]
    return {
        "total": len(rows), "applicable": applicable, "by_status": {s: by_status.get(s, 0) for s in STATUSES},
        "gaps_by_priority": {p: by_priority.get(p, 0) for p in ("P0", "P1", "P2")},
        "verified_coverage": round(100.0 * tested / applicable, 1) if applicable else 0.0,
        "manual_review": sum(1 for r in rows if r["manual_review"]),
        "formula": "contrôles applicables implémentés et testés avec preuves / contrôles applicables",
        "not_applicable_note": ("aucun contrôle n'est non applicable pour l'audit de Navin lui-même : le référentiel décrit ce que Navin "
                                "doit savoir vérifier, quel que soit le projet. La non-applicabilité se juge par projet, dans la matrice de chaque audit."),
    }


def write_matrix(rows: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    summary = _summary(rows)
    payload = {
        "referential": {"id": ref.REFERENTIAL_ID, "version": ref.VERSION, "controls": len(rows), "categories": len(ref.CATEGORIES)},
        "navin_version": navin_version, "generated_at": generated_at, "method": (
            "statuts déduits du code : un contrôle est implemented_and_tested quand un moteur de Navin le cible et qu'un test vérifie "
            "un de ses identifiants de constat ; blocked quand ses seuls moteurs sont des scanners externes absents de cette machine ; "
            "partial et missing selon capabilities.py. La colonne fixture_run donne le statut obtenu en auditant un projet synthétique."),
        "summary": summary, "controls": rows,
    }
    (OUT / "cyber-coverage-matrix.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def _bullets(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items]


def _validation_answers(rows: list[dict[str, Any]], summary: dict[str, Any], context: dict[str, Any]) -> list[str]:
    from navin.security import catalog
    from navin.security.analyzers import RULE_CONTROLS

    by_cat: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        by_cat[r["category"]][r["status"]] += 1
    empty = [f"{ref.category(c).number:02d}. {ref.category(c).name['fr']}" for c, n in by_cat.items()
             if not n["implemented_and_tested"] and not n["partial"]]
    thin = [f"{ref.category(c).number:02d}. {ref.category(c).name['fr']}" for c, n in by_cat.items()
            if not n["implemented_and_tested"] and n["partial"]]
    claimed = sorted(c.id for c in catalog.controls_for_scanner("navin_rules") if c.id not in RULE_CONTROLS)
    modes = Counter(c.verification_mode for c in ref.CONTROLS)
    needs = Counter(i for r in rows for i in r["requires"])
    db = context.get("database") or {}
    tests = {t["table"]: t.get("results", {}) for t in db.get("role_tests", []) if t.get("tested")}
    self_audit = context.get("self") or {}
    scanners = context.get("scanners") or {}
    installed = sorted(k for k, v in scanners.items() if v.get("installed"))
    lines = ["## Réponses aux questions de validation", ""]
    lines.append("1. **Catégories applicables entièrement manquantes :** " + (", ".join(empty) if empty else "aucune")
                 + ". Sans aucun contrôle automatisé et testé (seulement du partiel) : " + (", ".join(thin) or "aucune") + ".")
    lines.append(f"2. **Contrôles marqués couverts sans preuve :** {summary['by_status']['implemented_not_tested']} aujourd'hui. "
                 "Avant cet audit, une passe de règles déclarait aussi vérifiés des contrôles qu'aucune règle ne vérifie, ou si peu qu'un "
                 "« clean » était trompeur (une seule règle de TOCTOU, la seule présence d'un lockfile) : " + ", ".join(claimed) + ".")
    lines.append(f"3. **Lecture seule ou environnement isolé :** {modes['read_only']} contrôles en lecture seule, {modes['hybrid']} hybrides, "
                 f"{modes['staging_active']} actifs en préproduction. Entrées nécessaires : URL autorisée pour {needs.get('base_url', 0)}, "
                 f"comptes synthétiques pour {needs.get('accounts', 0)}, dépôt git pour {needs.get('git', 0)}; "
                 "le rejouage des migrations est une option explicite (`database_ephemeral`).")
    detail = "; ".join(f"{t}: " + ", ".join(f"{k} {v}" for k, v in res.items()) for t, res in tests.items())
    lines.append("4. **Rôles réels pour la RLS :** PostgreSQL local jetable, migrations appliquées par un rôle sans superutilisateur ni BYPASSRLS, "
                 "puis SELECT, INSERT, UPDATE et DELETE sous `anon` et sous `authenticated` avec deux utilisateurs synthétiques (claims JWT simulés), "
                 "le tout annulé par ROLLBACK. Résultat sur le projet synthétique : " + (detail or "non exécuté (PostgreSQL absent)") + ".")
    lines.append("5. **Déduplication, vérification, correctif :** les constats sont fusionnés par empreinte (famille, CWE, fichier, bloc de lignes, composant); "
                 "un constat statique confirmé par la base éphémère ou par le contrôle web passe à confirmé, à exploitable quand la requête le reproduit. "
                 "Le moteur de vérification ne ferme un constat qu'avec un nouveau scan et un test de régression, à la demande (NAV-CYBER-QA-009 reste partiel).")
    if self_audit:
        lines.append(f"6. **Tous les modules d'un monorepo :** la découverte liste modules, espaces de travail et sous-modules; elle lit au plus "
                     f"{self_audit.get('files_seen')} fichiers et le dit (`truncated`). Sur ce dépôt, elle s'est arrêtée à "
                     f"{self_audit.get('files_seen')} fichiers : NAV-CYBER-QA-003 ressort alors non concluant, jamais réussi.")
    else:
        lines.append("6. **Tous les modules d'un monorepo :** la découverte liste modules, espaces de travail et sous-modules et signale quand elle s'arrête avant la fin (`truncated`).")
    lines.append(f"7. **Contrôles non automatisables :** {summary['manual_review']} contrôles sont marqués `manual_review` dans les deux matrices; "
                 "ils ne passent jamais à réussi automatiquement.")
    lines.append(f"8. **Scanners intégrés, installés, exécutés :** {len(REGISTRY)} au registre (tableau ci-dessous); installés sur cette machine : "
                 + (", ".join(installed) if installed else "aucun") + ". Chaque audit enregistre pour chacun son état et sa version.")
    lines += ["", "## Scanners au registre", "", "| Scanner | Binaire | Licence | Familles | Installé ici | Version |", "|---|---|---|---|---|---|"]
    for sid, spec in REGISTRY.items():
        s = scanners.get(sid, {})
        lines.append(f"| {sid} | {spec.binary} | {spec.license} | {', '.join(spec.families)} | {'oui' if s.get('installed') else 'non'} | {s.get('version') or '-'} |")
    if self_audit:
        lines += ["", "## Audit de ce dépôt", "",
                  f"Navin a audité son propre dépôt sans scanner externe en {self_audit.get('duration_s')} s : {self_audit.get('findings')} constats "
                  f"({', '.join(f'{k} {v}' for k, v in (self_audit.get('by_severity') or {}).items())}), "
                  f"couverture vérifiée {(self_audit.get('matrix_summary') or {}).get('verified_coverage')} % des contrôles applicables. "
                  "Ce sont des signaux à trier (une partie vient des jeux d'essai volontairement vulnérables des tests), pas un verdict."]
    return lines


def write_gap_analysis(rows: list[dict[str, Any]], summary: dict[str, Any], generated_at: str, context: dict[str, Any]) -> None:
    lines = [
        "# Navin Cyber - analyse des écarts sur les 302 contrôles", "",
        f"Généré le {generated_at} sur Navin {navin_version} par `scripts/cyber_coverage_report.py`. Les statuts viennent du code et des tests, "
        "pas d'une déclaration: voir `cyber-coverage-matrix.json` pour chaque contrôle et `cyber-execution-evidence/` pour les exécutions.", "",
        "## Résumé chiffré", "", "| Statut | Contrôles |", "|---|---|",
    ]
    names = {"implemented_and_tested": "Implémenté et testé", "implemented_not_tested": "Implémenté, non testé", "partial": "Partiel",
             "missing": "Manquant", "blocked": "Bloqué", "not_applicable": "Non applicable"}
    lines += [f"| {names[s]} | {summary['by_status'][s]} |" for s in STATUSES]
    lines += ["", f"**Couverture vérifiée : {summary['verified_coverage']} %** ({summary['by_status']['implemented_and_tested']} contrôles "
              f"implémentés et testés sur {summary['applicable']} applicables). Les contrôles partiels ne comptent pas.", "",
              f"Parmi les {summary['by_status']['missing']} manquants, {summary['manual_review']} relèvent d'une revue humaine : "
              "Navin les liste, le verdict reste humain. " + summary["not_applicable_note"][0].upper() + summary["not_applicable_note"][1:], "",
              "Lacunes par priorité: " + ", ".join(f"{p} {n}" for p, n in summary["gaps_by_priority"].items()) + ".", "",
              "## Ce qui a été corrigé pendant cet audit (P0, faux sentiment de couverture)", ""]
    lines += _bullets([
        "Après une passe de règles, `navin_rules` marquait comme vérifiés tous les contrôles du catalogue de 70 qui le citent, y compris le stockage mobile, "
        "les liens profonds, la limitation de débit et la journalisation d'événements, qu'aucune règle ne vérifie. Les contrôles réellement couverts "
        "sont maintenant nommés dans `navin/security/analyzers/__init__.py` (RULE_CONTROLS); les autres restent « non évalués ».",
        "Un contrôle n'est « réussi » dans la matrice que si un moteur de sa liste a réellement tourné et que le contrôle est automatisé. "
        "Un scanner absent donne « bloqué », jamais « réussi ».",
        "L'étiquette « audit complet » est refusée tant qu'un contrôle applicable n'est pas vérifié (`summary.audit_complete`).",
        "Les migrations d'un dépôt audité ne tournent jamais sous un superutilisateur: sans cela, `COPY ... TO PROGRAM` aurait exécuté une commande sur la machine. "
        "Le rejouage dans PostgreSQL reste optionnel (`database_ephemeral`).",
        "Les contrôles web dynamiques n'envoient rien sans une URL fournie par l'utilisateur, seulement GET, HEAD et OPTIONS, sans sortir de l'origine.",
    ])
    lines += ["", "## Limites connues qui subsistent", ""]
    lines += _bullets([
        "Les règles sont des motifs: un contrôle « réussi » veut dire « aucun motif connu trouvé », pas « code sûr ». L'analyse de flux n'existe que pour Python.",
        "Aucun scanner externe n'est installé sur cette machine (voir `cyber-execution-evidence/environment.json`): leurs adaptateurs sont testés sur des rapports enregistrés, "
        "les binaires n'ont pas été exécutés ici.",
        "Les tests d'isolation avec de vrais comptes (BOLA, BFLA, tenants par appartenance) et le DAST actif n'existent pas encore.",
        "La base de données réellement testée par requêtes est PostgreSQL/Supabase; les autres moteurs sont analysés dans leurs scripts, configurations et chaînes de connexion.",
    ])
    for priority, title, blurb in (
        ("P0", "P0 - sécurité fondamentale", "failles non détectées sur les familles qui protègent les données et les accès"),
        ("P1", "P1 - couverture renforcée", "familles où la détection existe mais reste partielle ou humaine"),
        ("P2", "P2 - surfaces spécialistes", "mobile, desktop, serverless, natif: utiles quand la surface existe"),
    ):
        mine = [r for r in rows if r["priority"] == priority]
        lines += ["", f"## {title} ({len(mine)} lacunes)", "", blurb + ".", ""]
        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in mine:
            by_cat[r["category"]].append(r)
        for cat in ref.CATEGORIES:
            gaps = by_cat.get(cat.id, [])
            if not gaps:
                continue
            counts = Counter(g["status"] for g in gaps)
            plan = PLANS[cat.id]
            lines += [f"### {cat.number:02d}. {cat.name['fr']}", "",
                      f"{len(gaps)} lacunes: " + ", ".join(f"{names[s].lower()} {n}" for s, n in counts.items()) + ".", "",
                      f"- **À réutiliser :** {plan['reuse']}.", f"- **À intégrer :** {plan['build']}.",
                      f"- **Critère d'acceptation :** {plan['accept']}.", "- **Non-régression :** un test par défaut volontaire et un test du cas sain, dans le fichier de test de la famille.", "",
                      "| Contrôle | Statut | Limite ou raison |", "|---|---|---|"]
            lines += [f"| {g['id']} {g['task'][:80]} | {names[g['status']]} | {(g['gap'] or g['reason'])[:160].replace('|', '/')} |" for g in gaps]
            lines.append("")
    lines += [""] + _validation_answers(rows, summary, context)
    (OUT / "cyber-gap-analysis.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_test_plan(generated_at: str) -> None:
    lines = ["# Navin Cyber - plan de test", "",
             f"Généré le {generated_at}. Chaque scénario dit ce qui est déjà testé (avec le test) et ce qui reste à construire. "
             "Règles communes: données et comptes synthétiques, bases éphémères, aucune action sur la production, aucune cible hors périmètre autorisé, "
             "aucun vrai secret dans les jeux d'essai.", ""]
    for surface, scenarios in SURFACE_PLANS:
        lines += [f"## {surface}", "", "| Scénario | Jeu d'essai | Test | Résultat attendu |", "|---|---|---|---|"]
        lines += [f"| {s} | {fixture} | `{test}` | {expected} |" for s, fixture, test, expected in scenarios]
        lines.append("")
    lines += ["## Exécution de référence", "",
              "Les suites de sécurité tournent avec:", "", "```bash",
              "NAVIN_PG_BIN=/chemin/vers/postgresql/bin .venv/bin/python -m pytest " + " ".join(SECURITY_TESTS) + " -q",
              ".venv/bin/python scripts/cyber_coverage_report.py", "```", "",
              "`NAVIN_PG_BIN` est facultatif: sans PostgreSQL, les tests du cluster éphémère sont ignorés et la matrice marque la lecture du catalogue comme non faite.", ""]
    (OUT / "cyber-test-plan.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-pytest", action="store_true", help="do not re-run the security test suites")
    parser.add_argument("--self", action="store_true", help="also audit this repository (counts only)")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    (OUT / "navin_cyber_appsec_checklist.json").write_text(json.dumps(ref.referential_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "navin_cyber_appsec_checklist.md").write_text(ref.referential_markdown(), encoding="utf-8")

    evidence, fixture_rows = fixture_run()
    write_fixture_evidence(evidence)
    rows = audit_existing(fixture_rows)
    summary = write_matrix(rows, generated_at)
    context: dict[str, Any] = {"database": evidence["audit"].get("runtime_reports", {}).get("database", {}), "scanners": _scanner_state()}
    if args.self:
        context["self"] = self_audit()
        (EVIDENCE / "self-audit-summary.json").write_text(json.dumps(context["self"], indent=1) + "\n", encoding="utf-8")
    write_gap_analysis(rows, summary, generated_at, context)
    write_test_plan(generated_at)
    if not args.no_pytest:
        (EVIDENCE / "pytest-security.txt").write_text(pytest_evidence(), encoding="utf-8")
    print(json.dumps({"summary": summary, "fixture": evidence["matrix"]["summary"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
