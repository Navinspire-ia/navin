# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema for Navin app templates (marketplace catalog + future navin.json)."""

from __future__ import annotations

from typing import Literal, TypedDict

APP_TEMPLATE_SCHEMA = "navin-app-template.v1"
NAVIN_JSON_SCHEMA = "navin.json.v1"
OVERLAY_SCHEMA = "navin-app-overlay.v1"
INSTALL_JSON_SCHEMA = "navin-app-install.v2"
AWS_TEMPLATE_PREFIX = "templates/v1"
AWS_TEMPLATES_BASE_URL = "https://navinagent.s3.eu-north-1.amazonaws.com"

TemplateKind = Literal["business", "ai-product"]
TemplateStatus = Literal["source", "normalized"]
AuditStatus = Literal["pending", "in_review", "passed", "rejected"]
LicenseId = Literal["MIT", "Apache-2.0"]


class AppTemplate(TypedDict):
    """First-party catalog row. Lives in Navin code, not in gitignored clones."""

    schema: str
    slug: str
    name: str
    description: str
    kind: TemplateKind
    category: str
    stack: list[str]
    license: LicenseId
    source_github: str
    source_name: str
    create_app_id: str | None
    featured: bool
    status: TemplateStatus
    audit_status: AuditStatus
    universal_agents: list[str]
    domain_agents: list[str]


class AgentSpec(TypedDict, total=False):
    """One agent inside a template. User can override any field locally."""

    id: str
    name: str
    description: str
    system: str
    model: str
    permissions: list[str]
    tools: list[str]
    rag_collections: list[str]
    enabled: bool


class NavinJson(TypedDict, total=False):
    """Manifest of a normalized template, then copied into ``.navin/apps/<slug>/``."""

    schema: str
    slug: str
    name: str
    version: str
    license: LicenseId
    source_github: str
    stack: list[str]
    launch: str
    permissions: list[str]
    rag_collections: list[str]
    docker: bool
    seed: bool
    agents: list[AgentSpec]


class AppOverlay(TypedDict, total=False):
    """User fork of a template. Wins over the packaged navin.json."""

    schema: str
    slug: str
    disabled_agents: list[str]
    extra_agents: list[AgentSpec]
    agent_overrides: dict[str, AgentSpec]
    env: dict[str, str]
    notes: str
