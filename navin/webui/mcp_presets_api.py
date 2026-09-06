"""MCP preset helpers for the WebUI settings and message surfaces."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import urllib.parse
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from navin.agent.tools.registry import ToolRegistry
from navin.apps.protocol import app_manifest, compact_dict
from navin.config.loader import load_config, resolve_config_env_vars, save_config
from navin.config.paths import get_runtime_subdir
from navin.config.schema import MCPServerConfig
from navin.utils.helpers import ensure_dir

QueryParams = dict[str, list[str]]

_MCP_PRESET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)
_SECRET_QUERY_RE = re.compile(
    r"([?&](?:[^=&]*(?:api[_-]?key|token|secret|password|bearer)[^=&]*)=)[^&#\s]+",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"((?:api[_-]?key|token|secret|password|bearer)(?:[=:]|\s+))[^,\s'\"&]+",
    re.IGNORECASE,
)
_MCP_ATTACHMENT_KEYS = (
    "name",
    "display_name",
    "category",
    "transport",
    "logo_url",
    "brand_color",
    "status",
    "configured",
)
_MAX_TEST_TOOLS = 16
_DEFAULT_TEST_TIMEOUT = 20
_DEFAULT_CUSTOM_TIMEOUT = 30
_CUSTOM_ACTIONS = {"custom", "import", "import-cursor", "tools"}

McpReload = Callable[[], Awaitable[dict[str, Any]]]


class McpPresetError(Exception):
    """WebUI-facing MCP preset error."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class McpPresetField:
    name: str
    label: str
    target: tuple[Literal["env", "url_param", "arg", "header"], str]
    secret: bool = True
    required: bool = True
    env_var: str | None = None
    placeholder: str = ""


@dataclass(frozen=True)
class McpPreset:
    name: str
    display_name: str
    category: str
    description: str
    docs_url: str
    transport: Literal["stdio", "streamableHttp", "sse", "oauth"]
    install_supported: bool
    brand_domain: str
    brand_color: str
    server: MCPServerConfig | None = None
    fields: tuple[McpPresetField, ...] = ()
    requires: str = ""
    note: str = ""
    # Product modules that should surface this preset (e.g. career on #/tools).
    modules: tuple[str, ...] = ()
    # When True, gateway/agent startup writes this preset into tools.mcp_servers
    # if missing (no required credential fields). Connection remains best-effort.
    auto_enable: bool = False


def _favicon_url(domain: str) -> str:
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


MCP_PRESETS: tuple[McpPreset, ...] = (
    McpPreset(
        name="browserbase",
        display_name="Browserbase",
        category="browser",
        description="Cloud browser automation through Browserbase's hosted MCP server.",
        docs_url="https://docs.browserbase.com/integrations/mcp/setup",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="browserbase.com",
        brand_color="#111827",
        requires="Browserbase API key",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.browserbase.com/mcp",
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="browserbase_api_key",
                label="Browserbase API key",
                target=("url_param", "browserbaseApiKey"),
                env_var="BROWSERBASE_API_KEY",
                placeholder="bb_live_...",
            ),
        ),
    ),
    McpPreset(
        name="playwright",
        display_name="Playwright",
        category="browser",
        description="Local browser inspection and automation with Playwright's MCP server.",
        docs_url="https://playwright.dev/docs/getting-started-mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="playwright.dev",
        brand_color="#2EAD33",
        requires="Node.js and npx",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@playwright/mcp@latest"],
            tool_timeout=60,
        ),
    ),
    McpPreset(
        name="context7",
        display_name="Context7",
        category="docs",
        description="Fetch current library docs and code examples while the agent works.",
        docs_url="https://context7.com/docs/resources/all-clients",
        transport="stdio",
        install_supported=True,
        brand_domain="context7.com",
        brand_color="#111827",
        requires="Node.js and npx; API key optional",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@upstash/context7-mcp@latest"],
            tool_timeout=45,
        ),
        fields=(
            McpPresetField(
                name="context7_api_key",
                label="Context7 API key",
                target=("arg", "--api-key"),
                env_var="CONTEXT7_API_KEY",
                placeholder="ctx7_...",
                required=False,
            ),
        ),
        note="Works without a key for basic public docs; add a key for higher limits or private docs.",
    ),
    McpPreset(
        name="firecrawl",
        display_name="Firecrawl",
        category="web",
        description="Scrape, crawl, search, and extract web pages through Firecrawl's MCP server.",
        docs_url="https://docs.firecrawl.dev/use-cases/developers-mcp",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="firecrawl.dev",
        brand_color="#EB5E28",
        requires="Network access",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.firecrawl.dev/v2/mcp",
            tool_timeout=60,
        ),
        note=(
            "Uses Firecrawl Keyless through the hosted MCP endpoint. No API key is required for "
            "the built-in preset; use a custom MCP server URL if you want account-specific limits."
        ),
    ),
    McpPreset(
        name="exa",
        display_name="Exa",
        category="web",
        description="Search the web and fetch clean page content through Exa's hosted MCP server.",
        docs_url="https://exa.ai/mcp",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="exa.ai",
        brand_color="#101010",
        requires="Network access",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.exa.ai/mcp",
            tool_timeout=45,
        ),
        note="Hosted Exa MCP endpoint currently does not require an API key.",
        modules=("career", "tenders"),
    ),
    McpPreset(
        name="linkedin",
        display_name="LinkedIn",
        category="sales",
        description=(
            "Recommended option: stickerdaniel/linkedin-mcp-server "
            "(uvx mcp-server-linkedin@latest). Company pages, people, posts, "
            "inbox and jobs through your own logged-in browser session. "
            "Tenders: buyer research, never a notice. Career: session jobs and "
            "profile, never scrape, never Easy Apply. Not an official LinkedIn API."
        ),
        docs_url="https://github.com/stickerdaniel/linkedin-mcp-server",
        transport="stdio",
        install_supported=True,
        brand_domain="linkedin.com",
        brand_color="#0A66C2",
        requires="uvx (Astral uv) and a LinkedIn login in the local browser session",
        server=MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["mcp-server-linkedin@latest"],
            env={"UV_HTTP_TIMEOUT": "300"},
            tool_timeout=180,
        ),
        note=(
            "Recommended option on Tenders and Career. "
            "Runs `uvx mcp-server-linkedin@latest`. "
            "First tool call that needs auth opens a LinkedIn login window. "
            "Or create the session first: `uvx mcp-server-linkedin@latest --login` "
            "or `--import-from-browser` (Chrome, Brave, Edge, ...). "
            "Profile lives in ~/.linkedin-mcp/. Official tools: get_person_profile, "
            "get_my_profile, connect_with_person, get_sidebar_profiles, get_inbox, "
            "get_conversation, search_conversations, send_message, get_company_profile, "
            "get_company_posts, search_companies, get_company_employees, search_jobs, "
            "get_saved_jobs, search_people, get_job_details, get_feed, search_posts, "
            "close_session. "
            "Tenders: job tools are hiring context only, never a notice. "
            "Career: job tools read the user session, then import or prepare a CV. "
            "Never scrape linkedin.com. Never Easy Apply. Never invent a notice. "
            "Never post a public bid. "
            "connect_with_person and send_message need confirm=true after the user agrees. "
            "Not auto-enabled."
        ),
        modules=("tenders", "career"),
    ),
    McpPreset(
        name="microsoft-learn",
        display_name="Microsoft Learn",
        category="docs",
        description="Search and fetch Microsoft Learn documentation through Microsoft's hosted MCP server.",
        docs_url="https://learn.microsoft.com/en-us/training/support/mcp",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="learn.microsoft.com",
        brand_color="#0078D4",
        requires="Network access",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://learn.microsoft.com/api/mcp",
            tool_timeout=45,
        ),
        note="Public documentation only; no authentication required.",
    ),
    McpPreset(
        name="aws-docs",
        display_name="AWS Documentation",
        category="docs",
        description="Search AWS documentation and service guidance through AWS Labs' documentation MCP server.",
        docs_url="https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server/",
        transport="stdio",
        install_supported=True,
        brand_domain="aws.amazon.com",
        brand_color="#FF9900",
        requires="uvx",
        server=MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["awslabs.aws-documentation-mcp-server@latest"],
            env={"FASTMCP_LOG_LEVEL": "ERROR", "AWS_DOCUMENTATION_PARTITION": "aws"},
            tool_timeout=60,
        ),
    ),
    McpPreset(
        name="brave-search",
        display_name="Brave Search",
        category="web",
        description="Run web, news, image, video, and local search through Brave Search.",
        docs_url="https://www.npmjs.com/package/@brave/brave-search-mcp-server",
        transport="stdio",
        install_supported=True,
        brand_domain="brave.com",
        brand_color="#FB542B",
        requires="Node.js, npx, and Brave Search API key",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@brave/brave-search-mcp-server@latest", "--transport", "stdio"],
            tool_timeout=45,
        ),
        fields=(
            McpPresetField(
                name="brave_api_key",
                label="Brave Search API key",
                target=("env", "BRAVE_API_KEY"),
                env_var="BRAVE_API_KEY",
                placeholder="BSA...",
            ),
        ),
    ),
    McpPreset(
        name="postman",
        display_name="Postman",
        category="api",
        description="Inspect and manage Postman APIs, collections, and workspaces through the local MCP server.",
        docs_url="https://learning.postman.com/docs/developer/postman-api/postman-mcp-server/postman-mcp-local-server",
        transport="stdio",
        install_supported=True,
        brand_domain="postman.com",
        brand_color="#FF6C37",
        requires="Node.js, npx, and Postman API key",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@postman/postman-mcp-server@latest", "--full"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="postman_api_key",
                label="Postman API key",
                target=("env", "POSTMAN_API_KEY"),
                env_var="POSTMAN_API_KEY",
                placeholder="PMAK-...",
            ),
        ),
    ),
    McpPreset(
        name="figma",
        display_name="Figma",
        category="design",
        description="Read design context from Figma using the local Dev Mode MCP server.",
        docs_url="https://help.figma.com/hc/en-us/articles/32132100833559-Guide-to-the-Figma-MCP-server",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="figma.com",
        brand_color="#F24E1E",
        requires="Figma desktop app with MCP enabled",
        server=MCPServerConfig(
            type="streamableHttp",
            url="http://127.0.0.1:3845/mcp",
            tool_timeout=45,
        ),
        note="Requires Figma Desktop Dev Mode MCP to be running locally.",
    ),
    McpPreset(
        name="debugmcp",
        display_name="DebugMCP",
        category="devops",
        description=(
            "Live breakpoints, stack frames, variables, and expression evaluation "
            "through the DebugMCP extension MCP server (real DAP debugging)."
        ),
        docs_url="https://github.com/microsoft/DebugMCP",
        transport="streamableHttp",
        install_supported=True,
        auto_enable=True,
        brand_domain="github.com",
        brand_color="#007ACC",
        requires="DebugMCP extension running in VS Code or Cursor",
        server=MCPServerConfig(
            type="streamableHttp",
            url="http://127.0.0.1:3001/mcp",
            tool_timeout=120,
        ),
        note=(
            "Auto-enabled in Navin config at gateway/agent startup. Start the "
            "DebugMCP extension for live tools; otherwise /debug falls back to "
            "logs/pdb. Useful tools: add_breakpoint, start_debugging, "
            "list_variable_names, get_variables_values, evaluate_expression, "
            "step_*, continue_execution, pause_execution. Pair with /debug and "
            "debug_repair(action=mcp_status)."
        ),
    ),
    McpPreset(
        name="github",
        display_name="GitHub",
        category="code",
        description="Repository, issue, and pull request workflows via GitHub's MCP server.",
        docs_url="https://github.com/github/github-mcp-server",
        transport="stdio",
        install_supported=True,
        brand_domain="github.com",
        brand_color="#24292F",
        requires="Docker and GitHub token",
        server=MCPServerConfig(
            type="stdio",
            command="docker",
            args=[
                "run",
                "-i",
                "--rm",
                "-e",
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "ghcr.io/github/github-mcp-server",
            ],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="github_token",
                label="GitHub token",
                target=("env", "GITHUB_PERSONAL_ACCESS_TOKEN"),
                env_var="GITHUB_PERSONAL_ACCESS_TOKEN",
                placeholder="ghp_...",
            ),
        ),
        modules=("career", "code"),
    ),
    McpPreset(
        name="notion",
        display_name="Notion",
        category="career",
        description=(
            "Official Notion MCP for Career notes, interview pages, and offer trackers. "
            "Not a LinkedIn scraper."
        ),
        docs_url="https://developers.notion.com/docs/mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="notion.so",
        brand_color="#111111",
        requires="Node.js, npx, and a Notion internal integration token",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@notionhq/notion-mcp-server"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="notion_token",
                label="Notion integration token",
                target=("env", "NOTION_TOKEN"),
                env_var="NOTION_TOKEN",
                placeholder="ntn_...",
            ),
        ),
        note=(
            "Official @notionhq/notion-mcp-server. Pair with the career tool for the "
            "live pipeline. Hosted alternative: https://mcp.notion.com/mcp. "
            "Do not use Notion or any MCP to scrape LinkedIn or auto Easy Apply. "
            "GitHub and Exa presets stay available for portfolio proof and public research. "
            "Enable them on Tools (#/tools)."
        ),
        modules=("career",),
    ),
    McpPreset(
        name="hubspot",
        display_name="HubSpot",
        category="sales",
        description="CRM contacts, companies, deals, and notes for the Leads studio via HubSpot's MCP server.",
        docs_url="https://developers.hubspot.com/docs/guides/crm/integrations/mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="hubspot.com",
        brand_color="#FF7A59",
        requires="Node.js, npx, and a HubSpot private app access token",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@hubspot/mcp-server"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="hubspot_access_token",
                label="HubSpot private app token",
                target=("env", "PRIVATE_APP_ACCESS_TOKEN"),
                env_var="HUBSPOT_ACCESS_TOKEN",
                placeholder="pat-na1-...",
            ),
        ),
        note=(
            "Scopes should cover CRM contacts, companies, and deals. "
            "The Leads studio prefers this MCP over raw curl when configured; "
            "workspace files under sales/crm/ remain the offline fallback. "
            "Pair with HUNTER_API_KEY / APOLLO_API_KEY for verified emails via "
            "lead-enrichment/scripts/enrich_leads.py, and Exa or Firecrawl for "
            "deeper public web research."
        ),
    ),
    McpPreset(
        name="salesforce",
        display_name="Salesforce",
        category="sales",
        description=(
            "Query and update Salesforce CRM records (SOQL, contacts, accounts, "
            "opportunities) via a token-based MCP. Official hosted Salesforce MCP "
            "is OAuth PKCE and is not pasted into Navin v1."
        ),
        docs_url="https://github.com/imazhar101/salesforce-mcp-jsforce",
        transport="stdio",
        install_supported=True,
        brand_domain="salesforce.com",
        brand_color="#00A1E0",
        requires=(
            "Node.js, npx, a Salesforce access token, and the org instance URL "
            "(from `sf org display` or an External Client App OAuth grant)"
        ),
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@imazhar101/salesforce-mcp-jsforce"],
            env={"SF_READONLY": "1"},
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="sf_access_token",
                label="Salesforce access token",
                target=("env", "SF_ACCESS_TOKEN"),
                env_var="SF_ACCESS_TOKEN",
                placeholder="00D...",
            ),
            McpPresetField(
                name="sf_instance_url",
                label="Salesforce instance URL",
                target=("env", "SF_INSTANCE_URL"),
                env_var="SF_INSTANCE_URL",
                secret=False,
                placeholder="https://your-domain.my.salesforce.com",
            ),
            McpPresetField(
                name="sf_readonly",
                label="Read-only tools (1 = no writes)",
                target=("env", "SF_READONLY"),
                env_var="SF_READONLY",
                secret=False,
                required=False,
                placeholder="1",
            ),
        ),
        note=(
            "Mint a token with `sf org display` or an External Client App. "
            "Official Hosted MCP Servers (Setup → API Catalog, OAuth PKCE, "
            "https://developer.salesforce.com/docs/platform/hosted-mcp-servers/overview) "
            "are not a paste-URL flow in Navin v1. Default SF_READONLY=1 strips "
            "create/update/delete tools. Never delete CRM records; mark lost with reason. "
            "Used by the Leads studio (`crm-update-agent`)."
        ),
    ),
    McpPreset(
        name="google-ads",
        display_name="Google Ads",
        category="ads",
        description=(
            "Query Google Ads accounts, campaigns, and metrics through Google's "
            "official Ads MCP server (stdio via pipx)."
        ),
        docs_url="https://github.com/googleads/google-ads-mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="ads.google.com",
        brand_color="#4285F4",
        requires=(
            "pipx, a Google Cloud project with the Google Ads API enabled, "
            "Application Default Credentials with the adwords scope, and a "
            "developer token with at least Explorer access"
        ),
        server=MCPServerConfig(
            type="stdio",
            command="pipx",
            args=[
                "run",
                "--spec",
                "git+https://github.com/googleads/google-ads-mcp.git",
                "google-ads-mcp",
            ],
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="google_ads_developer_token",
                label="Google Ads developer token",
                target=("env", "GOOGLE_ADS_DEVELOPER_TOKEN"),
                env_var="GOOGLE_ADS_DEVELOPER_TOKEN",
                placeholder="your-developer-token",
            ),
            McpPresetField(
                name="google_project_id",
                label="Google Cloud project ID",
                target=("env", "GOOGLE_PROJECT_ID"),
                env_var="GOOGLE_PROJECT_ID",
                secret=False,
                placeholder="my-gcp-project",
            ),
            McpPresetField(
                name="google_cloud_project",
                label="GOOGLE_CLOUD_PROJECT (optional alias)",
                target=("env", "GOOGLE_CLOUD_PROJECT"),
                env_var="GOOGLE_CLOUD_PROJECT",
                secret=False,
                required=False,
                placeholder="my-gcp-project",
            ),
            McpPresetField(
                name="google_application_credentials",
                label="ADC credentials JSON path",
                target=("env", "GOOGLE_APPLICATION_CREDENTIALS"),
                env_var="GOOGLE_APPLICATION_CREDENTIALS",
                secret=False,
                placeholder="/path/to/application_default_credentials.json",
            ),
            McpPresetField(
                name="google_ads_login_customer_id",
                label="Login customer ID (MCC, optional)",
                target=("env", "GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
                env_var="GOOGLE_ADS_LOGIN_CUSTOMER_ID",
                secret=False,
                required=False,
                placeholder="1234567890",
            ),
        ),
        note=(
            "Run once: gcloud auth application-default login "
            "--scopes=https://www.googleapis.com/auth/adwords,"
            "https://www.googleapis.com/auth/cloud-platform "
            "(optionally --client-id-file=...). Point "
            "GOOGLE_APPLICATION_CREDENTIALS at the printed ADC JSON path. "
            "pipx must be on PATH for the gateway process. Cron/loop jobs reuse "
            "these env values when the same gateway config is loaded. "
            "Used by the Ads studio (Settings → MCP, Other modules → Ads)."
        ),
    ),
    McpPreset(
        name="meta-ads",
        display_name="Meta Ads",
        category="ads",
        description=(
            "Manage Meta (Facebook/Instagram) ads via Meta's hosted Ads MCP "
            "at https://mcp.facebook.com/ads (reporting, campaigns, catalogs)."
        ),
        docs_url=(
            "https://developers.facebook.com/documentation/ads-commerce/"
            "ads-ai-connectors/ads-mcp-server/ads-mcp-server-get-started"
        ),
        transport="streamableHttp",
        install_supported=True,
        brand_domain="facebook.com",
        brand_color="#1877F2",
        requires=(
            "Network access and a Meta user access token with ads scopes "
            "(ads_read / ads_management / business_management, plus ads_mcp_management "
            "when using a Meta developer app). No local pipx/npx required."
        ),
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.facebook.com/ads",
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="meta_ads_authorization",
                label="Authorization header (Bearer token)",
                target=("header", "Authorization"),
                env_var="META_ADS_ACCESS_TOKEN",
                placeholder="Bearer EAAB...",
            ),
        ),
        note=(
            "Paste the full Authorization value including the Bearer prefix "
            "(Graph API Explorer or your Meta app OAuth). Navin does not run "
            "the browser OAuth popup for this remote MCP in v1. Account must "
            "be enabled for Meta Ads MCP (phased rollout). Prefer read-only "
            "scopes first; mutating tools can change live spend."
        ),
    ),
    McpPreset(
        name="tiktok-ads",
        display_name="TikTok Ads",
        category="ads",
        description=(
            "Query TikTok for Business campaigns, ad groups, ads, and reports "
            "via the community tiktok-ads-mcp (stdio). Official TikTok MCP is "
            "announced but not self-serve yet."
        ),
        docs_url="https://pypi.org/project/tiktok-ads-mcp/",
        transport="stdio",
        install_supported=True,
        brand_domain="tiktok.com",
        brand_color="#010101",
        requires=(
            "uvx (Astral uv) and a TikTok Marketing API app with App ID, Secret, "
            "and long-lived Access Token"
        ),
        server=MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["tiktok-ads-mcp"],
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="tiktok_app_id",
                label="TikTok App ID",
                target=("env", "TIKTOK_APP_ID"),
                env_var="TIKTOK_APP_ID",
                secret=False,
                placeholder="your-app-id",
            ),
            McpPresetField(
                name="tiktok_secret",
                label="TikTok App Secret",
                target=("env", "TIKTOK_SECRET"),
                env_var="TIKTOK_SECRET",
                placeholder="your-app-secret",
            ),
            McpPresetField(
                name="tiktok_access_token",
                label="TikTok Access Token",
                target=("env", "TIKTOK_ACCESS_TOKEN"),
                env_var="TIKTOK_ACCESS_TOKEN",
                placeholder="your-access-token",
            ),
        ),
        note=(
            "Create an app at the TikTok Marketing API portal, authorize "
            "advertiser accounts, and mint an access token. Official TikTok "
            "for Business MCP / Agentic Hub "
            "(https://ads.tiktok.com/apps_and_agents/agentic-hub) is not a "
            "public paste-URL endpoint yet - this preset uses the community "
            "PyPI server until TikTok ships a hosted URL. Cron/loop needs the "
            "same gateway env."
        ),
    ),
    McpPreset(
        name="reddit-ads",
        display_name="Reddit Ads",
        category="ads",
        description=(
            "Read (and optionally write) Reddit Ads API v3 campaigns, ad groups, "
            "ads, and performance via mcp-server-reddit-ads (stdio, read-only by default)."
        ),
        docs_url="https://github.com/camlowe/mcp-server-reddit-ads",
        transport="stdio",
        install_supported=True,
        brand_domain="reddit.com",
        brand_color="#FF4500",
        requires=(
            "Node.js, npx, and a Reddit Ads developer app with client ID, "
            "client secret, and refresh token (ads:read; ads:manage only if writes enabled)"
        ),
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "mcp-server-reddit-ads"],
            env={"REDDIT_ADS_WRITE_TIER": "read"},
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="reddit_client_id",
                label="Reddit client ID",
                target=("env", "REDDIT_CLIENT_ID"),
                env_var="REDDIT_CLIENT_ID",
                secret=False,
                placeholder="your-client-id",
            ),
            McpPresetField(
                name="reddit_client_secret",
                label="Reddit client secret",
                target=("env", "REDDIT_CLIENT_SECRET"),
                env_var="REDDIT_CLIENT_SECRET",
                placeholder="your-client-secret",
            ),
            McpPresetField(
                name="reddit_refresh_token",
                label="Reddit refresh token",
                target=("env", "REDDIT_REFRESH_TOKEN"),
                env_var="REDDIT_REFRESH_TOKEN",
                placeholder="your-refresh-token",
            ),
            McpPresetField(
                name="reddit_ads_write_tier",
                label="Write tier (read | safe | spend)",
                target=("env", "REDDIT_ADS_WRITE_TIER"),
                env_var="REDDIT_ADS_WRITE_TIER",
                secret=False,
                required=False,
                placeholder="read",
            ),
            McpPresetField(
                name="reddit_ads_account_id",
                label="Default ads account id (optional)",
                target=("env", "REDDIT_ADS_ACCOUNT_ID"),
                env_var="REDDIT_ADS_ACCOUNT_ID",
                secret=False,
                required=False,
                placeholder="a2_...",
            ),
        ),
        note=(
            "Register the app under ads.reddit.com → Business settings → "
            "Developer Applications (redirect http://localhost:8080), then run "
            "`npx mcp-server-reddit-ads auth` once to mint REDDIT_REFRESH_TOKEN. "
            "Default write tier is read (no spend changes). Raise to safe/spend "
            "only when you intend mutations."
        ),
    ),
    McpPreset(
        name="linkedin-ads",
        display_name="LinkedIn Ads",
        category="ads",
        description=(
            "Read LinkedIn Ads accounts, campaigns, targeting, and reports. "
            "Needs a Marketing API access token (r_ads / r_ads_reporting)."
        ),
        docs_url="https://www.npmjs.com/package/@cesteral/linkedin-mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="linkedin.com",
        brand_color="#0A66C2",
        requires=(
            "Node.js, npx, and a LinkedIn Marketing API access token with "
            "r_ads / r_ads_reporting (Advertising API product approved)"
        ),
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@cesteral/linkedin-mcp"],
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="linkedin_access_token",
                label="LinkedIn Marketing API access token",
                target=("env", "LINKEDIN_ACCESS_TOKEN"),
                env_var="LINKEDIN_ACCESS_TOKEN",
                placeholder="AQV...",
            ),
            McpPresetField(
                name="linkedin_api_version",
                label="LinkedIn-Version header (optional)",
                target=("env", "LINKEDIN_API_VERSION"),
                env_var="LINKEDIN_API_VERSION",
                secret=False,
                required=False,
                placeholder="202409",
            ),
        ),
        note=(
            "Create an app at linkedin.com/developers, request the Advertising "
            "API product, then mint a token with r_ads and r_ads_reporting. "
            "Prefer read-only first: write tools can create campaigns and change "
            "spend. Used by the Ads studio (Settings → MCP, Other modules → Ads)."
        ),
        modules=("ads",),
    ),
    McpPreset(
        name="search-console",
        display_name="Google Search Console",
        category="seo",
        description=(
            "Read Search Console properties, queries, pages, and sitemaps "
            "through the community mcp-search-console MCP (stdio via uvx)."
        ),
        docs_url="https://pypi.org/project/mcp-search-console/",
        transport="stdio",
        install_supported=True,
        brand_domain="search.google.com",
        brand_color="#34A853",
        requires=(
            "uvx (from Astral uv) or pipx, plus either an OAuth Desktop "
            "client_secrets.json or a service-account JSON with Search Console "
            "access (read-only by default)"
        ),
        server=MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["mcp-search-console"],
            env={"GSC_ALLOW_DESTRUCTIVE": "false"},
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="gsc_oauth_client_secrets_file",
                label="OAuth client_secrets.json path",
                target=("env", "GSC_OAUTH_CLIENT_SECRETS_FILE"),
                env_var="GSC_OAUTH_CLIENT_SECRETS_FILE",
                secret=False,
                required=False,
                placeholder="/path/to/client_secrets.json",
            ),
            McpPresetField(
                name="gsc_credentials_path",
                label="Service account JSON path",
                target=("env", "GSC_CREDENTIALS_PATH"),
                env_var="GSC_CREDENTIALS_PATH",
                secret=False,
                required=False,
                placeholder="/path/to/service_account.json",
            ),
            McpPresetField(
                name="gsc_skip_oauth",
                label="Skip OAuth (set true for service account)",
                target=("env", "GSC_SKIP_OAUTH"),
                env_var="GSC_SKIP_OAUTH",
                secret=False,
                required=False,
                placeholder="true",
            ),
            McpPresetField(
                name="gsc_data_state",
                label="Data state (all or final)",
                target=("env", "GSC_DATA_STATE"),
                env_var="GSC_DATA_STATE",
                secret=False,
                required=False,
                placeholder="all",
            ),
        ),
        note=(
            "Provide at least one credential path: OAuth "
            "GSC_OAUTH_CLIENT_SECRETS_FILE or service-account "
            "GSC_CREDENTIALS_PATH (set GSC_SKIP_OAUTH=true for SA). "
            "Primary launcher is uvx; if uvx is missing, install uv "
            "(https://docs.astral.sh/uv/) or run the same package via "
            "pipx run mcp-search-console. Destructive site/sitemap tools stay "
            "disabled (GSC_ALLOW_DESTRUCTIVE=false). Cron/loop jobs need the "
            "same gateway env files."
        ),
    ),
    McpPreset(
        name="kubernetes",
        display_name="Kubernetes",
        category="devops",
        description="Inspect and manage Kubernetes/OpenShift resources, pods, logs, events, and Helm through the Kubernetes MCP server.",
        docs_url="https://github.com/containers/kubernetes-mcp-server",
        transport="stdio",
        install_supported=True,
        brand_domain="kubernetes.io",
        brand_color="#326CE5",
        requires="Node.js, npx, and a kubeconfig",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "kubernetes-mcp-server@latest"],
            tool_timeout=60,
        ),
        note="Uses the current kubeconfig context. Avoid cluster-admin credentials on production clusters.",
    ),
    McpPreset(
        name="argocd",
        display_name="Argo CD",
        category="devops",
        description="GitOps operations: list, inspect, sync, and manage Argo CD applications, projects, and clusters.",
        docs_url="https://github.com/argoproj-labs/mcp-for-argocd",
        transport="stdio",
        install_supported=True,
        brand_domain="argoproj.github.io",
        brand_color="#EF7B4D",
        requires="Node.js, npx, Argo CD URL and API token",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "argocd-mcp@latest", "stdio"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="argocd_base_url",
                label="Argo CD base URL",
                target=("env", "ARGOCD_BASE_URL"),
                env_var="ARGOCD_BASE_URL",
                secret=False,
                placeholder="https://argocd.example.com",
            ),
            McpPresetField(
                name="argocd_api_token",
                label="Argo CD API token",
                target=("env", "ARGOCD_API_TOKEN"),
                env_var="ARGOCD_API_TOKEN",
                placeholder="eyJhbGci...",
            ),
        ),
    ),
    McpPreset(
        name="aws-api",
        display_name="AWS",
        category="devops",
        description="Run AWS CLI operations across all services through AWS Labs' API MCP server.",
        docs_url="https://awslabs.github.io/mcp/servers/aws-api-mcp-server/",
        transport="stdio",
        install_supported=True,
        brand_domain="aws.amazon.com",
        brand_color="#FF9900",
        requires="uvx and AWS credentials (profile or env vars)",
        server=MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["awslabs.aws-api-mcp-server@latest"],
            env={"FASTMCP_LOG_LEVEL": "ERROR"},
            tool_timeout=90,
        ),
        fields=(
            McpPresetField(
                name="aws_region",
                label="AWS region",
                target=("env", "AWS_REGION"),
                env_var="AWS_REGION",
                secret=False,
                required=False,
                placeholder="eu-west-1",
            ),
            McpPresetField(
                name="aws_profile",
                label="AWS profile",
                target=("env", "AWS_PROFILE"),
                env_var="AWS_PROFILE",
                secret=False,
                required=False,
                placeholder="default",
            ),
        ),
        note="Uses your local AWS credentials. Mutating operations should stay behind approvals.",
    ),
    McpPreset(
        name="azure",
        display_name="Azure",
        category="devops",
        description="Operate Azure resources (AKS, storage, monitor, ARM) through Microsoft's Azure MCP server.",
        docs_url="https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/",
        transport="stdio",
        install_supported=True,
        brand_domain="azure.microsoft.com",
        brand_color="#0078D4",
        requires="Node.js, npx, and an authenticated az CLI session",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@azure/mcp@latest", "server", "start"],
            tool_timeout=90,
        ),
        note="Authenticates with your local Azure credentials (az login).",
    ),
    McpPreset(
        name="gcloud",
        display_name="Google Cloud",
        category="devops",
        description="Run Google Cloud workflows (GCE, GKE, logging, storage) through Google's gcloud MCP server.",
        docs_url="https://github.com/googleapis/gcloud-mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="cloud.google.com",
        brand_color="#4285F4",
        requires="Node.js, npx, and an authenticated gcloud CLI",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@google-cloud/gcloud-mcp"],
            tool_timeout=90,
        ),
        note="Authenticates with your local gcloud credentials (gcloud auth login).",
    ),
    McpPreset(
        name="grafana",
        display_name="Grafana",
        category="devops",
        description="Query dashboards, datasources, alerts, and incidents through Grafana's MCP server.",
        docs_url="https://github.com/grafana/mcp-grafana",
        transport="stdio",
        install_supported=True,
        brand_domain="grafana.com",
        brand_color="#F46800",
        requires="Docker, Grafana URL and service account token",
        server=MCPServerConfig(
            type="stdio",
            command="docker",
            args=[
                "run",
                "--rm",
                "-i",
                "-e",
                "GRAFANA_URL",
                "-e",
                "GRAFANA_SERVICE_ACCOUNT_TOKEN",
                "mcp/grafana",
                "-t",
                "stdio",
            ],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="grafana_url",
                label="Grafana URL",
                target=("env", "GRAFANA_URL"),
                env_var="GRAFANA_URL",
                secret=False,
                placeholder="https://grafana.example.com",
            ),
            McpPresetField(
                name="grafana_service_account_token",
                label="Grafana service account token",
                target=("env", "GRAFANA_SERVICE_ACCOUNT_TOKEN"),
                env_var="GRAFANA_SERVICE_ACCOUNT_TOKEN",
                placeholder="glsa_...",
            ),
        ),
    ),
    McpPreset(
        name="gitlab",
        display_name="GitLab",
        category="devops",
        description="Projects, merge requests, issues, pipelines, and wikis through the GitLab MCP server.",
        docs_url="https://github.com/zereight/gitlab-mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="gitlab.com",
        brand_color="#FC6D26",
        requires="Node.js, npx, and a GitLab personal access token",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@zereight/mcp-gitlab"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="gitlab_personal_access_token",
                label="GitLab personal access token",
                target=("env", "GITLAB_PERSONAL_ACCESS_TOKEN"),
                env_var="GITLAB_PERSONAL_ACCESS_TOKEN",
                placeholder="glpat-...",
            ),
            McpPresetField(
                name="gitlab_api_url",
                label="GitLab API URL",
                target=("env", "GITLAB_API_URL"),
                env_var="GITLAB_API_URL",
                secret=False,
                required=False,
                placeholder="https://gitlab.com/api/v4",
            ),
        ),
        note="Defaults to gitlab.com; set the API URL for self-hosted instances.",
    ),
    McpPreset(
        name="supabase",
        display_name="Supabase",
        category="database",
        description="Inspect and manage Supabase projects through the Supabase MCP server.",
        docs_url="https://supabase.com/docs/guides/ai-tools/mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="supabase.com",
        brand_color="#3ECF8E",
        requires="Node.js, npx, and Supabase access token",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@supabase/mcp-server-supabase@latest", "--read-only"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="supabase_access_token",
                label="Supabase access token",
                target=("env", "SUPABASE_ACCESS_TOKEN"),
                env_var="SUPABASE_ACCESS_TOKEN",
                placeholder="sbp_...",
            ),
        ),
        note="MVP config starts read-only by default.",
    ),
)


def mcp_servers_denied_for_module(module: str | None) -> frozenset[str]:
    """MCP servers whose ``modules`` field excludes the active studio desk.

    Unscoped presets stay available everywhere. A missing module (CLI / Telegram)
    keeps every configured server. LinkedIn is on Tenders and Career. Tenders
    never sees Career-only Notion.
    """
    from navin.command.modules import normalize_product_module

    normalized = normalize_product_module(module)
    if normalized is None:
        return frozenset()
    denied = {
        preset.name
        for preset in MCP_PRESETS
        if preset.modules and normalized not in preset.modules
    }
    return frozenset(denied)


def mcp_deny_prefixes(module: str | None) -> frozenset[str]:
    """Prefixes of wrapped MCP tool names to refuse for *module*."""
    return frozenset(f"mcp_{name}_" for name in mcp_servers_denied_for_module(module))


def ensure_auto_enabled_mcp_presets(config: Any) -> list[str]:
    """Install zero-setup MCP presets into ``config.tools.mcp_servers`` if missing.

    Only presets with ``auto_enable=True``, ``install_supported``, a server
    definition, and no required credential fields are considered. Existing
    entries are never overwritten (operator customization wins). Returns the
    names that were added. Does not write disk - callers persist when needed.
    """
    tools = getattr(config, "tools", None)
    if tools is None:
        return []
    if not bool(getattr(tools, "auto_enable_mcp_presets", True)):
        return []
    servers = getattr(tools, "mcp_servers", None)
    if not isinstance(servers, dict):
        return []

    added: list[str] = []
    for preset in MCP_PRESETS:
        if not preset.auto_enable or not preset.install_supported or preset.server is None:
            continue
        if any(field.required for field in preset.fields):
            continue
        if preset.name in servers:
            continue
        servers[preset.name] = _with_managed_stdio_cwd(
            preset.name,
            _clone_server(preset.server),
        )
        added.append(preset.name)
    return added


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _query_value(query: QueryParams, key: str) -> str | None:
    raw = _query_first(query, key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _preset_by_name(name: str) -> McpPreset:
    if not name or _MCP_PRESET_NAME_RE.match(name) is None:
        raise McpPresetError("invalid MCP preset name")
    for preset in MCP_PRESETS:
        if preset.name == name:
            return preset
    raise McpPresetError("unknown MCP preset", status=404)


def _preset_by_name_optional(name: str) -> McpPreset | None:
    try:
        return _preset_by_name(name)
    except McpPresetError:
        return None


def _known_preset_names() -> set[str]:
    return {preset.name for preset in MCP_PRESETS}


def _known_mcp_names() -> set[str]:
    names = _known_preset_names()
    with suppress(Exception):
        names.update(load_config().tools.mcp_servers)
    return names


def _clip_ws_string(value: Any, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def normalize_mcp_preset_mentions(raw: Any) -> list[dict[str, Any]]:
    """Sanitize structured MCP preset mentions sent by the WebUI."""
    if not isinstance(raw, list):
        return []
    known = _known_mcp_names()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        name = _clip_ws_string(item.get("name"), 64)
        if not name or _MCP_PRESET_NAME_RE.match(name) is None:
            continue
        key = name.lower()
        if key in seen or key not in known:
            continue
        seen.add(key)
        row: dict[str, Any] = {"name": key}
        for field_name in _MCP_ATTACHMENT_KEYS[1:]:
            value = item.get(field_name)
            if isinstance(value, bool):
                row[field_name] = value
                continue
            limit = 512 if field_name == "logo_url" else 160
            text = _clip_ws_string(value, limit)
            if text:
                row[field_name] = text
        out.append(row)
    return out


def _clone_server(server: MCPServerConfig) -> MCPServerConfig:
    return MCPServerConfig.model_validate(server.model_dump(mode="json"))


def _with_managed_stdio_cwd(name: str, cfg: MCPServerConfig) -> MCPServerConfig:
    if cfg.command and (cfg.type in (None, "stdio")) and not cfg.cwd:
        cfg.cwd = str(ensure_dir(get_runtime_subdir("mcp") / name))
    return cfg


def _remove_managed_stdio_cwd(name: str, cfg: MCPServerConfig | None) -> bool:
    if cfg is None or not cfg.cwd:
        return False
    cwd = Path(cfg.cwd).expanduser().resolve(strict=False)
    managed = (get_runtime_subdir("mcp") / name).resolve(strict=False)
    if cwd != managed or not cwd.exists():
        return False
    if cwd.is_symlink() or cwd.is_file():
        cwd.unlink()
    else:
        shutil.rmtree(cwd)
    return True


def _url_with_param(url: str, key: str, value: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != key]
    query.append((key, value))
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def _arg_value(args: list[str], flag: str) -> str | None:
    prefix = f"{flag}="
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(prefix):
            return item[len(prefix):]
    return None


def _with_arg_value(args: list[str], flag: str, value: str) -> list[str]:
    out: list[str] = []
    skip_next = False
    prefix = f"{flag}="
    for item in args:
        if skip_next:
            skip_next = False
            continue
        if item == flag:
            skip_next = True
            continue
        if item.startswith(prefix):
            continue
        out.append(item)
    out.extend([flag, value])
    return out


def _field_value_from_config(field: McpPresetField, cfg: MCPServerConfig | None) -> str | None:
    if cfg is None:
        return None
    target_kind, target_name = field.target
    if target_kind == "env":
        value = cfg.env.get(target_name)
        return value if value else None
    if target_kind == "header":
        value = cfg.headers.get(target_name)
        return value if value else None
    if target_kind == "arg":
        return _arg_value(list(cfg.args), target_name)
    if target_kind == "url_param" and cfg.url:
        parsed = urllib.parse.urlsplit(cfg.url)
        values = urllib.parse.parse_qs(parsed.query).get(target_name)
        if values:
            return values[0]
    return None


def _field_configured(field: McpPresetField, cfg: MCPServerConfig | None) -> bool:
    value = _field_value_from_config(field, cfg)
    if value:
        return True
    return bool(field.env_var and os.environ.get(field.env_var))


def _field_payload(field: McpPresetField, cfg: MCPServerConfig | None) -> dict[str, Any]:
    return {
        "name": field.name,
        "label": field.label,
        "secret": field.secret,
        "required": field.required,
        "configured": _field_configured(field, cfg),
        "placeholder": field.placeholder,
        "env_var": field.env_var,
    }


def _resolve_field_value(
    field: McpPresetField,
    query: QueryParams,
    existing: MCPServerConfig | None,
) -> str | None:
    provided = _query_value(query, field.name)
    if provided:
        return provided
    current = _field_value_from_config(field, existing)
    if current:
        return current
    if field.env_var and os.environ.get(field.env_var):
        return f"${{{field.env_var}}}"
    return None


def _normalize_bearer_authorization(value: str) -> str:
    """Accept raw tokens or full ``Bearer <token>`` values for Meta Ads MCP."""
    cleaned = value.strip()
    if not cleaned:
        return cleaned
    if cleaned.lower().startswith("bearer "):
        token = cleaned[7:].strip()
        return f"Bearer {token}" if token else cleaned
    return f"Bearer {cleaned}"


def _search_console_has_credentials(cfg: MCPServerConfig | None) -> bool:
    """GSC accepts either OAuth client secrets or a service-account JSON path."""
    if cfg is None:
        return False
    oauth = (cfg.env.get("GSC_OAUTH_CLIENT_SECRETS_FILE") or "").strip()
    sa = (cfg.env.get("GSC_CREDENTIALS_PATH") or "").strip()
    if oauth or sa:
        return True
    return bool(
        os.environ.get("GSC_OAUTH_CLIENT_SECRETS_FILE")
        or os.environ.get("GSC_CREDENTIALS_PATH")
    )


def _materialize_server(
    preset: McpPreset,
    query: QueryParams,
    existing: MCPServerConfig | None,
) -> MCPServerConfig:
    if preset.server is None or not preset.install_supported:
        raise McpPresetError(f"{preset.display_name} is not supported yet", status=409)

    cfg = _clone_server(preset.server)
    for field_spec in preset.fields:
        value = _resolve_field_value(field_spec, query, existing)
        if field_spec.required and not value:
            raise McpPresetError(f"missing {field_spec.label}")
        if not value:
            continue
        target_kind, target_name = field_spec.target
        if target_kind == "env":
            cfg.env[target_name] = value
        elif target_kind == "header":
            if target_name == "Authorization":
                value = _normalize_bearer_authorization(value)
            cfg.headers[target_name] = value
        elif target_kind == "arg":
            cfg.args = _with_arg_value(list(cfg.args), target_name, value)
        elif target_kind == "url_param":
            cfg.url = _url_with_param(cfg.url, target_name, value)
    if preset.name == "search-console" and not _search_console_has_credentials(cfg):
        raise McpPresetError(
            "Provide OAuth client_secrets.json or a service-account JSON path"
        )
    return _with_managed_stdio_cwd(preset.name, cfg)


def _command_available(command: str) -> bool:
    if not command:
        return False
    if shutil.which(command):
        return True
    path = Path(command).expanduser()
    return path.exists() and path.is_file()


def _config_available(cfg: MCPServerConfig | None) -> bool:
    if cfg is None:
        return False
    if cfg.command:
        return _command_available(cfg.command)
    if cfg.url:
        return True
    return False


def _status_for(preset: McpPreset, cfg: MCPServerConfig | None) -> str:
    if cfg is None:
        return "not_installed" if preset.install_supported else "coming_soon"
    if any(field.required and not _field_configured(field, cfg) for field in preset.fields):
        return "missing_credentials"
    if preset.name == "search-console" and not _search_console_has_credentials(cfg):
        return "missing_credentials"
    if cfg.command and not _command_available(cfg.command):
        return "missing_dependency"
    return "configured"


def _connection_summary(cfg: MCPServerConfig | None) -> str:
    if cfg is None:
        return ""
    if cfg.command:
        return " ".join([cfg.command, *cfg.args[:2]]).strip()
    if cfg.url:
        parsed = urllib.parse.urlsplit(cfg.url)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return ""


def _tool_allowlist(cfg: MCPServerConfig | None) -> list[str]:
    if cfg is None:
        return ["*"]
    return list(cfg.enabled_tools)


def _managed_mcp_path(name: str, cfg: MCPServerConfig | None) -> list[str]:
    if cfg is None or not cfg.command:
        return []
    return [f"runtime:mcp/{name}"]


def _preset_manifest(preset: McpPreset, *, logo_url: str) -> dict[str, Any]:
    server = preset.server
    managed_paths = _managed_mcp_path(preset.name, server)
    field_specs = [
        compact_dict({
            "name": field.name,
            "target": field.target[0],
            "required": field.required,
            "secret": field.secret,
            "env_var": field.env_var,
        })
        for field in preset.fields
    ]
    capabilities = [
        compact_dict({
            "type": "mcp",
            "transport": preset.transport,
            "command": server.command if server and server.command else None,
            "args": list(server.args) if server and server.command else None,
            "url": _connection_summary(server) if server and server.url else None,
            "fields": field_specs,
        })
    ]
    return app_manifest(
        app_id=preset.name,
        display_name=preset.display_name,
        description=preset.description,
        category=preset.category,
        source="mcp-preset",
        docs_url=preset.docs_url,
        logo_url=logo_url,
        brand_color=preset.brand_color,
        capabilities=capabilities,
        install=compact_dict({
            "supported": preset.install_supported,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_present", "dependency_available"],
        }),
        remove=compact_dict({
            "supported": True,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_absent", "managed_paths_absent"] if managed_paths else ["config_absent"],
        }),
        trust={
            "registry": "mcp-presets",
            "level": "builtin",
            "review_status": "builtin_preset",
        },
    )


def _custom_manifest(name: str, cfg: MCPServerConfig) -> dict[str, Any]:
    transport = cfg.type or ("stdio" if cfg.command else "streamableHttp")
    managed_paths: list[str] = []
    return app_manifest(
        app_id=name,
        display_name=name,
        description="Custom MCP server from navin config.",
        category="custom",
        source="mcp-custom",
        brand_color="#64748B",
        capabilities=[
            compact_dict({
                "type": "mcp",
                "transport": transport,
                "command": cfg.command or None,
                "url": _connection_summary(cfg) if cfg.url else None,
            })
        ],
        install=compact_dict({
            "supported": True,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_present", "dependency_available"],
        }),
        remove=compact_dict({
            "supported": True,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_absent", "managed_paths_absent"] if managed_paths else ["config_absent"],
        }),
        trust={
            "registry": "user-config",
            "level": "user",
            "review_status": "user_managed",
        },
    )


def _preset_payload(preset: McpPreset, configured_servers: dict[str, MCPServerConfig]) -> dict[str, Any]:
    cfg = configured_servers.get(preset.name)
    status = _status_for(preset, cfg)
    configured = cfg is not None and status not in {"missing_credentials"}
    logo_url = _favicon_url(preset.brand_domain)
    return {
        "name": preset.name,
        "display_name": preset.display_name,
        "category": preset.category,
        "description": preset.description,
        "docs_url": preset.docs_url,
        "transport": preset.transport,
        "requires": preset.requires,
        "note": preset.note,
        "install_supported": preset.install_supported,
        "auto_enable": preset.auto_enable,
        "installed": cfg is not None,
        "configured": configured,
        "available": configured and _config_available(cfg),
        "status": status,
        "logo_url": logo_url,
        "brand_color": preset.brand_color,
        "required_fields": [_field_payload(field, cfg) for field in preset.fields],
        "connection_summary": _connection_summary(cfg),
        "enabled_tools": _tool_allowlist(cfg),
        "source": "preset",
        "modules": list(preset.modules),
        "manifest": _preset_manifest(preset, logo_url=logo_url),
    }


def _custom_payload(
    name: str,
    cfg: MCPServerConfig,
    *,
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    transport = cfg.type
    if not transport:
        transport = "stdio" if cfg.command else ("sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp")
    status = "missing_dependency" if cfg.command and not _command_available(cfg.command) else "configured"
    return {
        "name": name,
        "display_name": name,
        "category": "custom",
        "description": "Custom MCP server from navin config.",
        "docs_url": "",
        "transport": transport,
        "requires": "",
        "note": "",
        "install_supported": True,
        "installed": True,
        "configured": True,
        "available": _config_available(cfg),
        "status": status,
        "logo_url": None,
        "brand_color": "#64748B",
        "required_fields": [],
        "connection_summary": _connection_summary(cfg),
        "enabled_tools": _tool_allowlist(cfg),
        "tool_names": tool_names or [],
        "source": "custom",
        "manifest": _custom_manifest(name, cfg),
    }


def mcp_presets_payload(
    *,
    last_action: dict[str, Any] | None = None,
    tool_preview: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    config = load_config()
    known = _known_preset_names()
    preset_rows = [
        _preset_payload(preset, config.tools.mcp_servers)
        | ({"tool_names": tool_preview.get(preset.name, [])} if tool_preview and preset.name in tool_preview else {})
        for preset in MCP_PRESETS
    ]
    custom_rows = [
        _custom_payload(name, cfg, tool_names=(tool_preview or {}).get(name))
        for name, cfg in sorted(config.tools.mcp_servers.items())
        if name not in known
    ]
    payload: dict[str, Any] = {
        "presets": [*preset_rows, *custom_rows],
        "installed_count": len(config.tools.mcp_servers),
    }
    if last_action is not None:
        payload["last_action"] = last_action
    return payload


def _display_name_for(name: str, preset: McpPreset | None = None) -> str:
    return preset.display_name if preset is not None else name


def _action_message(action: str, preset: McpPreset, *, ok: bool = True) -> dict[str, Any]:
    verb = {
        "enable": "Enabled",
        "remove": "Removed",
        "test": "Checked",
    }.get(action, "Updated")
    payload: dict[str, Any] = {
        "ok": ok,
        "message": f"{verb} MCP preset for {preset.display_name}.",
    }
    if action == "enable":
        payload["installed"] = True
        payload["verification"] = ["config_present"]
    elif action == "remove":
        payload["removed"] = True
        payload["verification"] = ["config_absent"]
    return payload


def _server_action_message(action: str, name: str, *, ok: bool = True) -> dict[str, Any]:
    verb = {
        "custom": "Saved",
        "import": "Imported",
        "import-cursor": "Imported",
        "tools": "Updated tools for",
        "remove": "Removed",
    }.get(action, "Updated")
    payload: dict[str, Any] = {
        "ok": ok,
        "message": f"{verb} MCP server {name}.",
    }
    if action in {"custom", "import", "import-cursor"}:
        payload["installed"] = True
        payload["verification"] = ["config_present"]
    elif action == "remove":
        payload["removed"] = True
        payload["verification"] = ["config_absent"]
    return payload


def _scrub_test_error(text: str) -> str:
    scrubbed = _SECRET_QUERY_RE.sub(r"\1<redacted>", text.strip())
    scrubbed = _SECRET_ASSIGNMENT_RE.sub(r"\1<redacted>", scrubbed)
    return scrubbed[:400] if scrubbed else "Connection failed."


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _test_timeout(cfg: MCPServerConfig) -> int:
    raw = cfg.tool_timeout or _DEFAULT_TEST_TIMEOUT
    return max(5, min(int(raw), _DEFAULT_TEST_TIMEOUT))


async def _close_mcp_stacks(stacks: Mapping[str, Any]) -> None:
    for stack in stacks.values():
        with suppress(Exception):
            await stack.aclose()


async def mcp_presets_test_action(query: QueryParams) -> dict[str, Any]:
    """Connect to an enabled MCP preset and report its tool surface."""
    from navin.agent.tools.mcp import connect_mcp_servers, mcp_budget_notices

    name = (_query_first(query, "name") or "").strip()
    if not name:
        raise McpPresetError("missing MCP preset name")
    if _MCP_PRESET_NAME_RE.match(name) is None:
        raise McpPresetError("invalid MCP server name")
    preset = _preset_by_name_optional(name)
    display_name = _display_name_for(name, preset)

    try:
        config = resolve_config_env_vars(load_config())
    except ValueError as exc:
        return mcp_presets_payload(last_action={
            "ok": False,
            "message": _scrub_test_error(str(exc)),
            "error": _scrub_test_error(str(exc)),
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        })

    cfg = config.tools.mcp_servers.get(name)
    if cfg is None:
        raise McpPresetError(f"{display_name} is not enabled", status=404)

    status = _status_for(preset, cfg) if preset is not None else (
        "missing_dependency" if cfg.command and not _command_available(cfg.command) else "configured"
    )
    if status == "missing_credentials":
        last_action = {
            "ok": False,
            "message": f"{display_name} is missing required credentials.",
            "error": "missing credentials",
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
        return mcp_presets_payload(last_action=last_action)

    if cfg.command and not _command_available(cfg.command):
        last_action = {
            "ok": False,
            "message": f"{display_name} requires '{cfg.command}' on PATH.",
            "error": "missing dependency",
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
        return mcp_presets_payload(last_action=last_action)

    registry = ToolRegistry()
    stacks: dict[str, Any] = {}
    try:
        stacks = await asyncio.wait_for(
            connect_mcp_servers({name: cfg}, registry),
            timeout=_test_timeout(cfg),
        )
        tool_prefix = f"mcp_{name}_"
        tool_names = sorted(name for name in registry.tool_names if name.startswith(tool_prefix))
        ok = name in stacks
        if ok:
            last_action = {
                "ok": True,
                "message": (
                    f"{display_name} connected with {len(tool_names)} tools."
                    if tool_names
                    else f"{display_name} connected, but reported no tools."
                ),
                "tool_count": len(tool_names),
                "tool_names": tool_names[:_MAX_TEST_TOOLS],
                "checked_at": _checked_at(),
            }
            # A trimmed catalogue still connects, so the success message is the
            # only place the operator would notice tools went missing.
            overflow = mcp_budget_notices().get(name)
            if overflow:
                last_action["warning"] = overflow
        else:
            last_action = {
                "ok": False,
                "message": f"{display_name} did not complete an MCP handshake.",
                "error": "MCP handshake failed",
                "tool_count": 0,
                "tool_names": [],
                "checked_at": _checked_at(),
            }
    except asyncio.TimeoutError:
        last_action = {
            "ok": False,
            "message": f"{display_name} test timed out.",
            "error": "timeout",
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
    except Exception as exc:
        error = _scrub_test_error(str(exc))
        last_action = {
            "ok": False,
            "message": f"{display_name} could not connect.",
            "error": error,
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
    finally:
        await _close_mcp_stacks(stacks)

    preview = {name: last_action.get("tool_names", [])} if last_action.get("tool_names") else None
    return mcp_presets_payload(last_action=last_action, tool_preview=preview)


def _parse_json_value(raw: str | None, *, fallback: Any) -> Any:
    if raw is None or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpPresetError(f"invalid JSON: {exc.msg}") from exc


def _parse_string_list(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []
    parsed = _parse_json_value(raw, fallback=None)
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return [item for item in parsed if item.strip()]
    if isinstance(parsed, str):
        return shlex.split(parsed)
    raise McpPresetError("expected a JSON string array")


def _parse_string_map(raw: str | None) -> dict[str, str]:
    parsed = _parse_json_value(raw, fallback={})
    if not isinstance(parsed, dict):
        raise McpPresetError("expected a JSON object")
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise McpPresetError("JSON object values must be strings")
        if key.strip():
            out[key.strip()] = value
    return out


def _parse_enabled_tools(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return ["*"]
    values = _parse_string_list(raw)
    if "*" in values:
        return ["*"]
    return values


def _normalize_transport(value: str | None, *, command: str = "", url: str = "") -> Literal["stdio", "sse", "streamableHttp"]:
    raw = (value or "").strip()
    if not raw:
        if command:
            return "stdio"
        if url.rstrip("/").endswith("/sse"):
            return "sse"
        return "streamableHttp"
    aliases = {
        "stdio": "stdio",
        "sse": "sse",
        "streamableHttp": "streamableHttp",
        "streamable-http": "streamableHttp",
        "streamable_http": "streamableHttp",
        "http": "streamableHttp",
    }
    normalized = aliases.get(raw)
    if normalized is None:
        raise McpPresetError("unsupported MCP transport")
    return normalized  # type: ignore[return-value]


def _validated_server_name(name: str) -> str:
    if not name or _MCP_PRESET_NAME_RE.match(name) is None:
        raise McpPresetError("invalid MCP server name")
    return name.strip().lower()


def _custom_server_from_query(query: QueryParams) -> tuple[str, MCPServerConfig]:
    name = _validated_server_name((_query_first(query, "name") or "").strip())
    command = (_query_first(query, "command") or "").strip()
    url = (_query_first(query, "url") or "").strip()
    transport = _normalize_transport(_query_first(query, "transport"), command=command, url=url)
    if transport == "stdio" and not command:
        raise McpPresetError("stdio MCP servers require a command")
    if transport in {"sse", "streamableHttp"} and not url:
        raise McpPresetError("remote MCP servers require a URL")
    raw_timeout = (_query_first(query, "tool_timeout") or "").strip()
    tool_timeout = _DEFAULT_CUSTOM_TIMEOUT
    if raw_timeout:
        try:
            tool_timeout = max(5, min(int(raw_timeout), 600))
        except ValueError as exc:
            raise McpPresetError("tool_timeout must be an integer") from exc
    cfg = MCPServerConfig(
        type=transport,
        command=command if transport == "stdio" else "",
        args=_parse_string_list(_query_first(query, "args")),
        env=_parse_string_map(_query_first(query, "env")),
        cwd=(_query_first(query, "cwd") or "").strip() if transport == "stdio" else "",
        url=url if transport in {"sse", "streamableHttp"} else "",
        headers=_parse_string_map(_query_first(query, "headers")),
        tool_timeout=tool_timeout,
        enabled_tools=_parse_enabled_tools(_query_first(query, "enabled_tools")),
    )
    return name, cfg


def _mcp_server_config(name: str, raw: Any) -> tuple[str, MCPServerConfig]:
    server_name = _validated_server_name(name)
    if not isinstance(raw, Mapping):
        raise McpPresetError(f"MCP server '{server_name}' must be an object")
    command = str(raw.get("command") or "").strip()
    url = str(raw.get("url") or "").strip()
    transport_value = str(raw.get("type", raw.get("transport", "")) or "")
    transport = _normalize_transport(transport_value, command=command, url=url)
    if transport == "stdio" and not command:
        raise McpPresetError(f"MCP server '{server_name}' stdio transport requires a command")
    if transport in {"sse", "streamableHttp"} and not url:
        raise McpPresetError(f"MCP server '{server_name}' remote transport requires a URL")
    args = raw.get("args") or []
    env = raw.get("env") or {}
    headers = raw.get("headers") or {}
    cwd = str(raw.get("cwd") or "").strip()
    enabled_tools = raw.get("enabledTools", raw.get("enabled_tools", ["*"]))
    tool_timeout = raw.get("toolTimeout", raw.get("tool_timeout", _DEFAULT_CUSTOM_TIMEOUT))
    try:
        timeout_int = max(5, min(int(tool_timeout), 600))
    except (TypeError, ValueError):
        timeout_int = _DEFAULT_CUSTOM_TIMEOUT
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise McpPresetError(f"MCP server '{server_name}' args must be a string array")
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise McpPresetError(f"MCP server '{server_name}' env must be a string object")
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise McpPresetError(f"MCP server '{server_name}' headers must be a string object")
    if not isinstance(enabled_tools, list) or not all(isinstance(item, str) for item in enabled_tools):
        enabled_tools = ["*"]
    return server_name, MCPServerConfig(
        type=transport,
        command=command if transport == "stdio" else "",
        args=args,
        env=dict(env),
        cwd=cwd if transport == "stdio" else "",
        url=url if transport in {"sse", "streamableHttp"} else "",
        headers=dict(headers),
        tool_timeout=timeout_int,
        enabled_tools=list(enabled_tools),
    )


def _import_mcp_servers(raw_json: str | None) -> dict[str, MCPServerConfig]:
    parsed = _parse_json_value(raw_json, fallback=None)
    if not isinstance(parsed, Mapping):
        raise McpPresetError("MCP config must be a JSON object")
    servers = parsed.get("mcpServers", parsed)
    if not isinstance(servers, Mapping):
        raise McpPresetError("MCP config must contain mcpServers")
    out: dict[str, MCPServerConfig] = {}
    for name, raw_server in servers.items():
        if not isinstance(name, str):
            raise McpPresetError("MCP server names must be strings")
        server_name, cfg = _mcp_server_config(name, raw_server)
        out[server_name] = cfg
    if not out:
        raise McpPresetError("MCP config contains no servers")
    return out


def custom_mcp_action(action: str, query: QueryParams) -> dict[str, Any]:
    config = load_config()
    if action == "custom":
        name, cfg = _custom_server_from_query(query)
        config.tools.mcp_servers[name] = cfg
        save_config(config)
        payload = mcp_presets_payload(last_action=_server_action_message(action, name))
        payload["requires_restart"] = True
        return payload

    if action in {"import", "import-cursor"}:
        servers = _import_mcp_servers(_query_first(query, "config"))
        config.tools.mcp_servers.update(servers)
        save_config(config)
        payload = mcp_presets_payload(last_action={
            "ok": True,
            "message": f"Imported {len(servers)} MCP server(s).",
        })
        payload["requires_restart"] = True
        return payload

    if action == "tools":
        name = _validated_server_name((_query_first(query, "name") or "").strip())
        cfg = config.tools.mcp_servers.get(name)
        if cfg is None:
            raise McpPresetError("unknown MCP server", status=404)
        cfg.enabled_tools = _parse_enabled_tools(_query_first(query, "enabled_tools"))
        config.tools.mcp_servers[name] = cfg
        save_config(config)
        payload = mcp_presets_payload(last_action=_server_action_message(action, name))
        payload["requires_restart"] = True
        return payload

    raise McpPresetError(f"unknown MCP action '{action}'", status=404)


def mcp_presets_action(action: str, query: QueryParams) -> dict[str, Any]:
    name = (_query_first(query, "name") or "").strip()
    if not name:
        raise McpPresetError("missing MCP preset name")
    preset = _preset_by_name_optional(name)

    config = load_config()
    existing = config.tools.mcp_servers.get(name)

    if action == "enable":
        if preset is None:
            raise McpPresetError("unknown MCP preset", status=404)
        config.tools.mcp_servers[preset.name] = _materialize_server(preset, query, existing)
        save_config(config)
        payload = mcp_presets_payload(last_action=_action_message(action, preset))
        payload["requires_restart"] = True
        return payload

    if action == "remove":
        if preset is None and name not in config.tools.mcp_servers:
            raise McpPresetError("unknown MCP server", status=404)
        removed_runtime_files = False
        cleanup_error = ""
        if name in config.tools.mcp_servers:
            existing_cfg = config.tools.mcp_servers[name]
            try:
                removed_runtime_files = _remove_managed_stdio_cwd(name, existing_cfg)
            except OSError as exc:
                cleanup_error = str(exc)
            del config.tools.mcp_servers[name]
            save_config(config)
        last_action = (
            _action_message(action, preset)
            if preset is not None
            else _server_action_message(action, name)
        )
        if removed_runtime_files:
            last_action["message"] = f"{last_action['message']} Removed managed runtime files."
            last_action["managed_paths_removed"] = [f"runtime:mcp/{name}"]
            last_action["verification"] = ["config_absent", "managed_paths_absent"]
        if cleanup_error:
            last_action["ok"] = False
            last_action["message"] = (
                f"{last_action['message']} Could not remove managed runtime files: {cleanup_error}"
            )
            last_action["verification_failed"] = ["managed_paths_absent"]
        payload = mcp_presets_payload(last_action=last_action)
        payload["requires_restart"] = True
        return payload

    if action == "test":
        raise McpPresetError("MCP preset test must run through the async test action", status=500)

    raise McpPresetError(f"unknown MCP preset action '{action}'", status=404)


def attach_mcp_hot_reload_result(
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Merge an agent MCP reload acknowledgement into a WebUI settings payload."""
    payload = dict(payload)
    payload["hot_reload"] = result
    payload["requires_restart"] = bool(result.get("requires_restart"))
    last_action = dict(payload.get("last_action") or {})
    base_message = str(last_action.get("message") or "").strip()
    reload_message = str(result.get("message") or "").strip()
    if reload_message:
        last_action["message"] = (
            f"{base_message} {reload_message}" if base_message else reload_message
        )
    if "ok" not in last_action:
        last_action["ok"] = bool(result.get("ok", False))
    payload["last_action"] = last_action
    return payload


async def mcp_presets_settings_action(
    action: str | None,
    query: QueryParams,
    *,
    reload_mcp: McpReload | None = None,
) -> dict[str, Any]:
    """Run a WebUI MCP preset action and hot-reload the agent when config changes."""
    if action is None:
        return mcp_presets_payload()
    if action == "test":
        return await mcp_presets_test_action(query)
    if action in _CUSTOM_ACTIONS:
        payload = await asyncio.to_thread(custom_mcp_action, action, query)
    else:
        payload = await asyncio.to_thread(mcp_presets_action, action, query)
    if reload_mcp is not None:
        payload = attach_mcp_hot_reload_result(payload, await reload_mcp())
    return payload
