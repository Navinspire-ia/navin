# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Threat model derived from discovery: assets, trust boundaries, scenarios.

It turns "this project has Postgres, JWT, Stripe and a tenant_id column" into
the sensitive assets at stake, the boundaries an attacker has to cross and
the families that matter most here, so the planner spends effort where the
project is actually exposed instead of running every tool blindly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from navin.security.catalog import FAMILY_IDS, family
from navin.security.orchestrator.discovery import Discovery


@dataclass
class Asset:
    id: str
    label: str
    sensitivity: str  # low | medium | high | critical
    evidence: str


@dataclass
class TrustBoundary:
    id: str
    label: str
    crosses: str


@dataclass
class Scenario:
    id: str
    family: str
    title: str
    actor: str
    precondition: str
    impact: str
    controls: list[str] = field(default_factory=list)


@dataclass
class ThreatModel:
    assets: list[Asset] = field(default_factory=list)
    boundaries: list[TrustBoundary] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    family_weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def top_families(self, limit: int = 8) -> list[str]:
        ranked = sorted(self.family_weights.items(), key=lambda kv: (-kv[1], FAMILY_IDS.index(kv[0])))
        return [fid for fid, _ in ranked[:limit]]


def build_threat_model(found: Discovery) -> ThreatModel:
    model = ThreatModel()
    weights = {fid: (1.0 if family(fid).priority == "critical" else 0.7) for fid in FAMILY_IDS}

    def bump(fid: str, amount: float) -> None:
        weights[fid] = weights.get(fid, 0.0) + amount

    # Assets --------------------------------------------------------------
    model.assets.append(Asset("source", "Source code and configuration", "medium", "always"))
    if found.databases:
        model.assets.append(Asset("database", "Application data (" + ", ".join(found.databases) + ")", "critical", "database drivers in manifests"))
        bump("injection", 1.0)
        bump("authorization", 0.5)
    if found.auth or "auth" in found.surfaces:
        model.assets.append(Asset("identities", "User identities, sessions and tokens", "critical", ", ".join(found.auth) or "role checks in code"))
        bump("authentication", 1.0)
        bump("authorization", 1.0)
    if "multitenant" in found.surfaces:
        model.assets.append(Asset("tenants", "Per-tenant data isolation", "critical", "tenant_id / RLS / Supabase detected"))
        bump("multitenancy", 2.0)
        bump("authorization", 0.5)
    if "payments" in found.surfaces:
        model.assets.append(Asset("money", "Payments, prices and credits", "critical", ", ".join(found.payments) or "price/checkout flow in code"))
        bump("business_logic", 2.0)
    if "env_files" in found.features or found.manifests:
        model.assets.append(Asset("secrets", "API keys, database URLs, signing keys", "critical", "env files or manifests present"))
        bump("secrets", 0.8)
    if found.infrastructure:
        model.assets.append(Asset("infrastructure", "Containers, clusters, cloud resources", "high", ", ".join(found.infrastructure)))
        bump("infrastructure", 1.0)
        bump("supply_chain", 0.5)
    if found.ai:
        model.assets.append(Asset("agent", "Model prompts, tools, RAG corpus", "high", ", ".join(found.ai)))
        bump("ai_agents", 2.0)
    if "mobile" in found.surfaces or "desktop" in found.surfaces:
        model.assets.append(Asset("device", "Local storage, IPC and update channel on the device", "high", ", ".join(found.native)))
        bump("mobile_desktop", 1.5)
    if found.dependencies:
        bump("supply_chain", 0.8)
    if "file_upload" in found.features:
        bump("files", 1.0)
    if "deserialization" in found.features or "yaml" in found.features:
        bump("code_execution", 1.0)
    if "frontend" in found.surfaces:
        bump("frontend", 1.0)
        bump("injection", 0.3)
    if "graphql" in found.surfaces:
        bump("api", 0.8)
        bump("availability", 0.5)
    if "api" in found.surfaces:
        bump("api", 1.0)
        bump("network", 0.5)
        bump("audit_logging", 0.3)
    if "webhooks" in found.features:
        bump("network", 0.5)
        bump("authentication", 0.3)

    # Boundaries ----------------------------------------------------------
    if "web" in found.surfaces or "api" in found.surfaces:
        model.boundaries.append(TrustBoundary("internet_app", "Internet -> application", "anonymous requests reach routes"))
    if "auth" in found.surfaces:
        model.boundaries.append(TrustBoundary("anon_user", "Anonymous -> authenticated user", "login, tokens, sessions"))
        model.boundaries.append(TrustBoundary("user_admin", "User -> privileged role", "role and permission checks"))
    if "multitenant" in found.surfaces:
        model.boundaries.append(TrustBoundary("tenant_tenant", "Tenant A -> tenant B", "tenant_id on every query, RLS, cache keys"))
    if "database" in found.surfaces:
        model.boundaries.append(TrustBoundary("app_db", "Application -> database", "queries built from user input"))
    if found.infrastructure:
        model.boundaries.append(TrustBoundary("container_host", "Container -> host / cluster / cloud account", "privileges, mounts, IAM"))
    if found.ai:
        model.boundaries.append(TrustBoundary("content_model", "Untrusted content -> model instructions -> tools", "prompt injection reaching tool calls"))
    if "mobile" in found.surfaces or "desktop" in found.surfaces:
        model.boundaries.append(TrustBoundary("device_app", "Other apps / user on the device -> app storage and IPC", "deep links, IPC, local files"))

    # Scenarios -----------------------------------------------------------
    if "database" in found.surfaces:
        model.scenarios.append(Scenario("sqli", "injection", "Query built from a request parameter reads other users' rows", "anonymous or user", "a route passes input to a query without parameters", "exfiltration", ["C1.01"]))
    if "auth" in found.surfaces:
        model.scenarios.append(Scenario("idor", "authorization", "Changing an id in the URL returns another user's resource", "authenticated user", "ownership not checked server-side", "disclosure", ["C2.07"]))
        model.scenarios.append(Scenario("jwt", "authentication", "Forged or unsigned token accepted", "anonymous", "weak secret, alg none or missing verification", "escalation", ["C2.03"]))
    if "multitenant" in found.surfaces:
        model.scenarios.append(Scenario("tenant_leak", "multitenancy", "User of tenant B reads or edits a document of tenant A", "authenticated user", "tenant_id taken from the request or RLS missing on a table", "exfiltration", ["C2.09"]))
    if "payments" in found.surfaces:
        model.scenarios.append(Scenario("price", "business_logic", "Client-supplied price or quantity accepted by the server", "authenticated user", "server trusts amounts from the request", "fraud", ["C3.08"]))
    if found.ai:
        model.scenarios.append(Scenario("prompt_injection", "ai_agents", "Content fetched by the agent instructs it to call a tool and leak data", "remote content author", "external text reaches the model without separation", "exfiltration", ["C6.01", "C6.06"]))
    if found.infrastructure:
        model.scenarios.append(Scenario("container_root", "infrastructure", "Compromised process runs as root with a mounted socket and escapes the container", "attacker with RCE", "root user, privileged flag or docker.sock mount", "compromise", ["C4.05"]))
    if found.dependencies:
        model.scenarios.append(Scenario("vuln_dep", "supply_chain", "Known vulnerable dependency reachable from the application", "anonymous", "pinned version with a CVE", "compromise", ["C4.01"]))
    if "frontend" in found.surfaces:
        model.scenarios.append(Scenario("dom_xss", "frontend", "Untrusted HTML rendered in the page steals the session", "anonymous", "innerHTML or dangerouslySetInnerHTML on user data", "escalation", ["C3.05"]))
    if "mobile" in found.surfaces or "desktop" in found.surfaces:
        model.scenarios.append(Scenario("local_tokens", "mobile_desktop", "Tokens stored in plain files readable by other apps", "local attacker", "no keychain / keystore use", "escalation", ["C5.01"]))
    model.scenarios.append(Scenario("secret_in_repo", "secrets", "A committed secret grants access to a production service", "anyone with repo access", "hardcoded key or tracked .env", "compromise", ["C1.10"]))

    model.family_weights = dict(sorted(weights.items(), key=lambda kv: -kv[1]))
    return model
