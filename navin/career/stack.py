# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Canonical Career module stack: skills, tool, MCP, connector status."""

from __future__ import annotations

import os
from typing import Any

from navin.linkedin_mcp import linkedin_mcp_row

CAREER_SKILLS: tuple[str, ...] = (
    "career-agent",
    "job-search-agent",
    "cv-builder",
    "cv-tailoring",
    "cover-letter-writer",
    "ats-analyzer",
    "application-tracker",
    "followup-writer",
    "interview-coach",
    "offer-analyzer",
    "salary-negotiator",
    "freelance-rate-card",
    "career-advisor",
    "linkedin-optimizer",
    "scrape-operator",
    "scrapling",
    "web-extractor",
)

CAREER_MCP: tuple[dict[str, Any], ...] = (
    linkedin_mcp_row(
        role=(
            "Recommended option: your LinkedIn session for jobs, saved jobs, "
            "profile, people and inbox (stickerdaniel/linkedin-mcp-server). "
            "Never scrape linkedin.com. Never Easy Apply."
        ),
    ),
    {
        "id": "notion",
        "name": "Notion",
        "role": "Application notes and interview pages",
        "docs": "https://developers.notion.com/docs/mcp",
    },
    {
        "id": "github",
        "name": "GitHub",
        "role": "Public repos and portfolio proof",
        "docs": "https://github.com/github/github-mcp-server",
    },
    {
        "id": "exa",
        "name": "Exa",
        "role": "Public web research, never closed job pages",
        "docs": "https://exa.ai/mcp",
    },
)


def _env(*names: str, secrets: dict[str, str] | None = None) -> bool:
    bag = secrets or {}
    return all(bool(str(bag.get(name) or os.environ.get(name, "") or "").strip()) for name in names)


def api_key_flags(secrets: dict[str, str] | None = None) -> dict[str, bool]:
    """Which keyed APIs have credentials (local secrets or process env). Never returns the keys."""
    return {
        "adzuna": _env("ADZUNA_APP_ID", "ADZUNA_APP_KEY", secrets=secrets),
        "jooble": _env("JOOBLE_API_KEY", secrets=secrets),
        "usajobs": _env("USAJOBS_API_KEY", "USAJOBS_USER_AGENT", secrets=secrets),
    }


def connector_status(secrets: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Which official collectors can run without a scrape."""
    flags = api_key_flags(secrets)
    return [
        {
            "id": "remotive",
            "name": "Remotive",
            "live": True,
            "needs_key": False,
            "docs": "https://remotive.com/api/remote-jobs",
        },
        {
            "id": "ats",
            "name": "Greenhouse / Lever / Ashby",
            "live": True,
            "needs_key": False,
            "docs": "https://developers.greenhouse.io/",
        },
        {
            "id": "employers",
            "name": "ESN / consulting / agency feeds",
            "live": True,
            "needs_key": False,
            "ingest": "employer_feed",
            "docs": "https://api.smartrecruiters.com/v1/companies",
            "note": (
                "Directory of houses per market plus the employers you add. The feed behind "
                "each careers page is detected (Greenhouse, Lever, Ashby, SmartRecruiters, "
                "Workable, Recruitee, Teamtailor, Personio, Workday, careers page) and read directly."
            ),
        },
        {
            "id": "web-job-search",
            "name": "Web Job Search",
            "live": True,
            "needs_key": False,
            "docs": "https://html.duckduckgo.com/html/",
        },
        {
            "id": "web-search",
            "name": "Web Job Search",
            "live": True,
            "needs_key": False,
            "docs": "https://html.duckduckgo.com/html/",
        },
        {
            "id": "scrape",
            "name": "scrape + scrapling",
            "live": True,
            "needs_key": False,
            "docs": "https://scrapling.readthedocs.io/en/latest/",
        },
        {
            "id": "adzuna",
            "name": "Adzuna",
            "live": flags["adzuna"],
            "needs_key": True,
            "env": "ADZUNA_APP_ID + ADZUNA_APP_KEY",
            "docs": "https://developer.adzuna.com/",
        },
        {
            "id": "jooble",
            "name": "Jooble",
            "live": flags["jooble"],
            "needs_key": True,
            "env": "JOOBLE_API_KEY",
            "docs": "https://jooble.org/api/about",
        },
        {
            "id": "usajobs",
            "name": "USAJOBS",
            "live": flags["usajobs"],
            "needs_key": True,
            "env": "USAJOBS_API_KEY + USAJOBS_USER_AGENT",
            "docs": "https://developer.usajobs.gov/",
        },
        {
            "id": "linkedin",
            "name": "LinkedIn",
            "live": True,
            "needs_key": False,
            "recommended": True,
            "ingest": "public_listing",
            "docs": "https://www.linkedin.com/jobs/search",
            "note": (
                "Public guest job search, read without login, one request per second, "
                "capped per run. Saved jobs, inbox and profile need the LinkedIn MCP session."
            ),
        },
        {
            "id": "malt",
            "name": "Malt",
            "live": False,
            "needs_key": False,
            "ingest": "open_manual",
            "docs": "https://www.malt.fr/",
        },
    ]


def module_stack(secrets: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "tool": "career",
        "desk": "#/career",
        "skills": list(CAREER_SKILLS),
        "mcp": [dict(row) for row in CAREER_MCP],
        "connectors": connector_status(secrets),
        "rules": (
            "The career tool is the only desk API "
            "(Studio #/career, Tauri, HTTP /api/career, navin career, "
            "python -m navin.career.desk_cli). "
            "Use scrape + web_search on open hosts only. "
            "Web Job Search discovers pages; respect robots.txt before any fetch. "
            "LinkedIn public listings come from the guest job search inside "
            "career action=search (no login, rate limited). Never auto Easy Apply. "
            "Preferred markets change the match score. "
            "The gateway ticks career action=watch on heartbeat. "
            "The Career desk loop hunts (search then watch) on its saved "
            "schedule while the gateway is up. That is not heartbeat. "
            "Do not create a chat cron that searches or ticks. "
            "CV packs reuse Master CV facts only. "
            "Closed boards are official open + paste import. "
            "LinkedIn MCP (stickerdaniel/linkedin-mcp-server) is the recommended "
            "session option after the user enables it and signs in "
            "(uvx mcp-server-linkedin@latest --login or --import-from-browser). "
            "Use search_jobs, get_saved_jobs, get_job_details, get_my_profile, "
            "people and inbox from that session. Then career action=ingest "
            "(via=linkedin-mcp) or import, then prepare a CV. "
            "Never Easy Apply. "
            "connect_with_person and send_message need confirm=true."
        ),
    }
