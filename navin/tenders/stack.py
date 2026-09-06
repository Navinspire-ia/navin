"""Canonical Tenders module stack: skills, tool, MCP, official connectors."""

from __future__ import annotations

from typing import Any

from navin.linkedin_mcp import (
    LINKEDIN_MCP_COMMAND,
    LINKEDIN_MCP_CONFIRM,
    LINKEDIN_MCP_DOCS,
    LINKEDIN_MCP_GROUPS,
    LINKEDIN_MCP_JOBS,
    LINKEDIN_MCP_LOGIN,
    LINKEDIN_MCP_OFFICIAL,
    LINKEDIN_MCP_TOOLS,
    linkedin_mcp_row,
)
from navin.tenders.sources import API_SOURCE_IDS, catalog, source_by_id

# Same list as the /tenders brief and the module preload. Chat without a slash
# must still load the writers and the scrape pair.
TENDERS_SKILLS: tuple[str, ...] = (
    "tender-agent",
    "rfp-writer",
    "tender-monitor",
    "proposal-writer",
    "sales-proposal-writer",
    "contract-reviewer",
    "critic-reviewer",
    "web-extractor",
    "scrape-operator",
    "scrapling",
    "archify",
)

# Companion tools the Tenders loop must keep (not a closed allowlist).
# The runner still exposes message, files and exec; career/trading stay denied.
# AgentLoop subtracts this set from the denylist while product_module=tenders
# (heartbeat still denies scrape/browser/cron/web_search).
TENDERS_TOOLS: tuple[str, ...] = (
    "tenders",
    "scrape",
    "web_search",
    "browser",
    "cron",
    "open_preview",
)

# Slash /tenders already injects these for Track A reports. Chat on #/tenders
# without a slash must get the same report stack.
TENDERS_REPORT_SKILLS: tuple[str, ...] = (
    "studio-html-report",
    "ui-ux-pro-max",
    "make-interfaces-feel-better",
)

# Optional MCP. Collect still prefers the wired HTTP APIs in API_SOURCE_IDS
# before any MCP fetch. LinkedIn is first: recommended buyer research.
TENDERS_MCP: tuple[dict[str, Any], ...] = (
    linkedin_mcp_row(
        role=(
            "Recommended buyer research: company pages, contacts, posts and "
            "inbox via your LinkedIn session (stickerdaniel/linkedin-mcp-server)"
        ),
    ),
    {
        "id": "exa",
        "name": "Exa",
        "role": "Public research on official hosts when a portal has no API",
        "docs": "https://exa.ai/mcp",
    },
)


def connector_status() -> list[dict[str, Any]]:
    """Which official collectors run without a scrape."""
    rows: list[dict[str, Any]] = []
    for sid in sorted(API_SOURCE_IDS):
        source = source_by_id(sid) or {}
        rows.append(
            {
                "id": sid,
                "name": source.get("name") or sid,
                "live": True,
                "needs_key": sid == "sam-gov",
                "access": source.get("access") or "api",
                "docs": source.get("url") or "",
            }
        )
    rows.append(
        {
            "id": "scrape",
            "name": "scrape + web_search on official hosts",
            "live": True,
            "needs_key": False,
            "access": "scrape",
            "docs": "https://scrapling.readthedocs.io/en/latest/",
        }
    )
    rows.append(
        {
            "id": "linkedin",
            "name": "LinkedIn MCP",
            "live": False,
            "needs_key": False,
            "access": "mcp_session",
            "ingest": "mcp_session",
            "recommended": True,
            "docs": LINKEDIN_MCP_DOCS,
        }
    )
    rows.append(
        {
            "id": "exa",
            "name": "Exa MCP",
            "live": False,
            "needs_key": False,
            "access": "mcp",
            "ingest": "mcp",
            "docs": "https://exa.ai/mcp",
        }
    )
    return rows


def _mcp_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, tuple):
            payload[key] = list(value)
        elif isinstance(value, dict):
            payload[key] = {
                inner: list(items) if isinstance(items, tuple) else items
                for inner, items in value.items()
            }
        else:
            payload[key] = value
    return payload


def module_stack() -> dict[str, Any]:
    return {
        "tool": "tenders",
        "desk": "#/tenders",
        "skills": list(TENDERS_SKILLS),
        "tools": list(TENDERS_TOOLS),
        "mcp": [_mcp_row_payload(row) for row in TENDERS_MCP],
        "connectors": connector_status(),
        "catalog": len(catalog()),
        "rules": (
            "The tenders tool is the only desk API "
            "(Studio #/tenders, Tauri, HTTP /api/tenders, navin tenders, "
            "python -m navin.tenders.desk_cli). "
            "Official API / Open Data first, then scrape + web_search on official hosts. "
            "Never invent a notice. Never bypass login, captcha or Cloudflare. "
            "follow is the silent heartbeat. Never collect, write or send from heartbeat. "
            "The Tenders desk loop hunts (collect then watch) on its saved "
            "schedule while the gateway is up. That is not heartbeat. "
            "Start/stop/schedule with tenders action=start/stop/schedule. "
            "Do not create a chat cron that collects or ticks. "
            "Default send_mode is approval. The desk never posts a public bid by itself. "
            "Exa MCP is optional public research. "
            "LinkedIn MCP (stickerdaniel/linkedin-mcp-server) is the recommended "
            "buyer-research option after the user enables it and signs in "
            "(uvx mcp-server-linkedin@latest --login or --import-from-browser). "
            "Use the official tools for the buyer company, people, posts and inbox. "
            "Job tools are hiring context only, never a notice. "
            "Never invent a notice from LinkedIn. "
            "connect_with_person and send_message need confirm=true. No paid aggregator."
        ),
    }
