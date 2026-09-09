# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end expectations for the Ads studio + MCP presets."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

# Import agent first so command↔agent circular imports resolve like other tests.
import navin.agent.skills  # noqa: F401
from navin.agent.model_routes import WORKFLOW_ROUTE_ROLES
from navin.agent.tools.base import Tool
from navin.command.builtin import (
    _DELIVERY_WORKFLOWS,
    _HTML_REPORT_CLAUSE,
    _HTML_REPORT_WORKFLOWS,
    _TRACKED_WORKFLOWS,
    _WORKFLOW_BRIEFS,
    BUILTIN_COMMAND_SPECS,
    _workflow_handler,
    builtin_command_palette,
)
from navin.command.modules import (
    _MODULE_OWNED_COMMANDS,
    CODE_HIDDEN_COMMANDS,
    PRELOAD_SKILLS_METADATA_KEY,
    REQUIRES_TOOL_DELIVERY_METADATA_KEY,
    VALID_PRODUCT_MODULES,
    disabled_skills_for_module,
    exclusive_studio_skills,
    is_command_allowed_for_module,
    module_mismatch_message,
    normalize_product_module,
)
from navin.config.schema import Config, MCPServerConfig
from navin.webui.mcp_presets_api import (
    MCP_PRESETS,
    McpPresetError,
    _materialize_server,
    mcp_presets_payload,
    mcp_presets_test_action,
)

ROOT = Path(__file__).resolve().parents[1]
ADS_PRESETS = (
    "google-ads", "meta-ads", "tiktok-ads", "reddit-ads", "linkedin-ads", "microsoft-ads",
)
# Ads studio platforms + Search Console (SEO companion used with paid/search).
CONNECTION_PRESETS = (*ADS_PRESETS, "search-console")
ADS_CARDS = (
    "googleAdsOverview",
    "googleAdsStructure",
    "googleAdsOptimize",
    "metaAdsOverview",
    "metaAdsStructure",
    "metaAdsOptimize",
    "tiktokAdsOverview",
    "tiktokAdsStructure",
    "tiktokAdsOptimize",
    "redditAdsOverview",
    "redditAdsStructure",
    "redditAdsOptimize",
    "linkedinAdsOverview",
    "linkedinAdsStructure",
    "linkedinAdsOptimize",
)
ENGINE_CARDS = (
    "adsImportAnalyze",
    "adsWasteNegatives",
    "adsChangesReview",
    "microsoftAdsOverview",
    "microsoftAdsStructure",
    "microsoftAdsOptimize",
)


class AdsMcpPresetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.by_name = {preset.name: preset for preset in MCP_PRESETS}

    def test_ads_presets_registered_under_ads_category(self) -> None:
        for name in ADS_PRESETS:
            with self.subTest(name=name):
                self.assertIn(name, self.by_name)
                preset = self.by_name[name]
                self.assertEqual(preset.category, "ads")
                self.assertTrue(preset.install_supported)
                self.assertTrue(preset.docs_url)
                self.assertTrue(preset.brand_domain)

    def test_google_ads_stdio_pipx_spec(self) -> None:
        preset = self.by_name["google-ads"]
        self.assertEqual(preset.transport, "stdio")
        self.assertEqual(preset.server.command, "pipx")
        self.assertIn("google-ads-mcp", preset.server.args)
        env = {field.target[1] for field in preset.fields}
        self.assertTrue(
            {
                "GOOGLE_ADS_DEVELOPER_TOKEN",
                "GOOGLE_PROJECT_ID",
                "GOOGLE_APPLICATION_CREDENTIALS",
            }
            <= env
        )

    def test_meta_ads_remote_bearer_header(self) -> None:
        preset = self.by_name["meta-ads"]
        self.assertEqual(preset.transport, "streamableHttp")
        self.assertEqual(preset.server.url, "https://mcp.facebook.com/ads")
        self.assertEqual(
            {field.target for field in preset.fields},
            {("header", "Authorization")},
        )
        cfg = _materialize_server(
            preset,
            {"meta_ads_authorization": ["Bearer TESTTOKEN"]},
            None,
        )
        self.assertEqual(cfg.headers.get("Authorization"), "Bearer TESTTOKEN")
        self.assertEqual(cfg.url, "https://mcp.facebook.com/ads")

    def test_tiktok_ads_uvx_env(self) -> None:
        preset = self.by_name["tiktok-ads"]
        self.assertEqual(preset.transport, "stdio")
        self.assertEqual(preset.server.command, "uvx")
        self.assertEqual(preset.server.args, ["tiktok-ads-mcp"])
        cfg = _materialize_server(
            preset,
            {
                "tiktok_app_id": ["app"],
                "tiktok_secret": ["secret"],
                "tiktok_access_token": ["token"],
            },
            None,
        )
        self.assertEqual(cfg.env["TIKTOK_APP_ID"], "app")
        self.assertEqual(cfg.env["TIKTOK_SECRET"], "secret")
        self.assertEqual(cfg.env["TIKTOK_ACCESS_TOKEN"], "token")

    def test_reddit_ads_read_only_default(self) -> None:
        preset = self.by_name["reddit-ads"]
        self.assertEqual(preset.transport, "stdio")
        self.assertEqual(preset.server.command, "npx")
        self.assertIn("mcp-server-reddit-ads", preset.server.args)
        self.assertEqual(preset.server.env.get("REDDIT_ADS_WRITE_TIER"), "read")
        cfg = _materialize_server(
            preset,
            {
                "reddit_client_id": ["id"],
                "reddit_client_secret": ["sec"],
                "reddit_refresh_token": ["rt"],
            },
            None,
        )
        self.assertEqual(cfg.env["REDDIT_REFRESH_TOKEN"], "rt")
        self.assertEqual(cfg.env.get("REDDIT_ADS_WRITE_TIER"), "read")

    def test_linkedin_ads_npx_token(self) -> None:
        preset = self.by_name["linkedin-ads"]
        self.assertEqual(preset.transport, "stdio")
        self.assertEqual(preset.server.command, "npx")
        self.assertIn("@cesteral/linkedin-mcp", preset.server.args)
        cfg = _materialize_server(
            preset,
            {"linkedin_access_token": ["AQVTOKEN"]},
            None,
        )
        self.assertEqual(cfg.env["LINKEDIN_ACCESS_TOKEN"], "AQVTOKEN")

    def test_microsoft_ads_npx_env_scoped_to_ads(self) -> None:
        preset = self.by_name["microsoft-ads"]
        self.assertEqual(preset.transport, "stdio")
        self.assertEqual(preset.server.command, "npx")
        self.assertIn("@cesteral/msads-mcp", preset.server.args)
        self.assertEqual(preset.modules, ("ads",))
        cfg = _materialize_server(
            preset,
            {
                "msads_access_token": ["tok"],
                "msads_developer_token": ["dev"],
                "msads_customer_id": ["123"],
                "msads_account_id": ["456"],
            },
            None,
        )
        self.assertEqual(cfg.env["MSADS_ACCESS_TOKEN"], "tok")
        self.assertEqual(cfg.env["MSADS_DEVELOPER_TOKEN"], "dev")
        self.assertEqual(cfg.env["MSADS_CUSTOMER_ID"], "123")
        self.assertEqual(cfg.env["MSADS_ACCOUNT_ID"], "456")
        with self.assertRaises(McpPresetError):
            _materialize_server(preset, {"msads_access_token": ["tok"]}, None)

    def test_catalogue_payload_lists_all_ads_presets(self) -> None:
        names = {item["name"] for item in mcp_presets_payload()["presets"]}
        self.assertTrue(set(ADS_PRESETS) <= names)

    def test_materialize_rejects_missing_required_credentials(self) -> None:
        cases = (
            ("google-ads", {}),
            ("meta-ads", {}),
            (
                "tiktok-ads",
                {"tiktok_app_id": ["app"], "tiktok_secret": ["secret"]},
            ),
            (
                "reddit-ads",
                {"reddit_client_id": ["id"], "reddit_client_secret": ["sec"]},
            ),
            ("linkedin-ads", {}),
        )
        for name, query in cases:
            with self.subTest(name=name):
                with self.assertRaises(McpPresetError):
                    _materialize_server(self.by_name[name], query, None)

    def test_google_ads_materialize_required_env(self) -> None:
        cfg = _materialize_server(
            self.by_name["google-ads"],
            {
                "google_ads_developer_token": ["devtok"],
                "google_project_id": ["proj-1"],
                "google_application_credentials": ["/tmp/adc.json"],
            },
            None,
        )
        self.assertEqual(cfg.env["GOOGLE_ADS_DEVELOPER_TOKEN"], "devtok")
        self.assertEqual(cfg.env["GOOGLE_PROJECT_ID"], "proj-1")
        self.assertEqual(cfg.env["GOOGLE_APPLICATION_CREDENTIALS"], "/tmp/adc.json")

    def test_meta_ads_normalizes_bearer_prefix(self) -> None:
        cfg = _materialize_server(
            self.by_name["meta-ads"],
            {"meta_ads_authorization": ["EAABRAWTOKEN"]},
            None,
        )
        self.assertEqual(cfg.headers["Authorization"], "Bearer EAABRAWTOKEN")
        cfg2 = _materialize_server(
            self.by_name["meta-ads"],
            {"meta_ads_authorization": ["Bearer  already "]},
            None,
        )
        self.assertEqual(cfg2.headers["Authorization"], "Bearer already")

    def test_search_console_requires_oauth_or_service_account(self) -> None:
        with self.assertRaises(McpPresetError):
            _materialize_server(self.by_name["search-console"], {}, None)
        cfg = _materialize_server(
            self.by_name["search-console"],
            {"gsc_credentials_path": ["/tmp/sa.json"], "gsc_skip_oauth": ["true"]},
            None,
        )
        self.assertEqual(cfg.env["GSC_CREDENTIALS_PATH"], "/tmp/sa.json")


class _FakeMcpTool(Tool):
    def __init__(self, tool_name: str) -> None:
        self._name = tool_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "fake"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def _sample_cfg(name: str) -> MCPServerConfig:
    by_name = {preset.name: preset for preset in MCP_PRESETS}
    queries: dict[str, dict[str, list[str]]] = {
        "google-ads": {
            "google_ads_developer_token": ["dev"],
            "google_project_id": ["proj"],
            "google_application_credentials": ["/tmp/adc.json"],
        },
        "meta-ads": {"meta_ads_authorization": ["Bearer META"]},
        "tiktok-ads": {
            "tiktok_app_id": ["app"],
            "tiktok_secret": ["secret"],
            "tiktok_access_token": ["token"],
        },
        "reddit-ads": {
            "reddit_client_id": ["id"],
            "reddit_client_secret": ["sec"],
            "reddit_refresh_token": ["rt"],
        },
        "linkedin-ads": {"linkedin_access_token": ["AQVTOKEN"]},
        "microsoft-ads": {
            "msads_access_token": ["tok"],
            "msads_developer_token": ["dev"],
            "msads_customer_id": ["123"],
            "msads_account_id": ["456"],
        },
        "search-console": {
            "gsc_oauth_client_secrets_file": ["/tmp/client_secrets.json"],
        },
    }
    return _materialize_server(by_name[name], queries[name], None)


class AdsMcpConnectionTest(unittest.IsolatedAsyncioTestCase):
    """Settings → MCP → Test connection path for Ads + Search Console."""

    async def test_connection_succeeds_for_ads_and_gsc_presets(self) -> None:
        for name in CONNECTION_PRESETS:
            with self.subTest(name=name):
                cfg = _sample_cfg(name)
                config = Config()
                config.tools.mcp_servers[name] = cfg

                async def _connect(servers: dict, registry: Any) -> dict:
                    server_name = next(iter(servers))
                    registry.register(_FakeMcpTool(f"mcp_{server_name}_list_accounts"))
                    return {server_name: object()}

                with (
                    patch(
                        "navin.webui.mcp_presets_api.load_config",
                        return_value=config,
                    ),
                    patch(
                        "navin.webui.mcp_presets_api.resolve_config_env_vars",
                        side_effect=lambda c: c,
                    ),
                    patch(
                        "navin.webui.mcp_presets_api._command_available",
                        return_value=True,
                    ),
                    patch(
                        "navin.agent.tools.mcp.connect_mcp_servers",
                        new=AsyncMock(side_effect=_connect),
                    ),
                    patch(
                        "navin.webui.mcp_presets_api._close_mcp_stacks",
                        new=AsyncMock(),
                    ),
                ):
                    payload = await mcp_presets_test_action({"name": [name]})
                action = payload["last_action"]
                self.assertTrue(action["ok"], action)
                self.assertGreaterEqual(action["tool_count"], 1)
                self.assertTrue(
                    any(t.startswith(f"mcp_{name}_") for t in action["tool_names"])
                )

    async def test_connection_reports_missing_credentials(self) -> None:
        cases = {
            "google-ads": MCPServerConfig(
                type="stdio", command="pipx", args=["run", "google-ads-mcp"]
            ),
            "meta-ads": MCPServerConfig(
                type="streamableHttp", url="https://mcp.facebook.com/ads"
            ),
            "tiktok-ads": MCPServerConfig(
                type="stdio", command="uvx", args=["tiktok-ads-mcp"]
            ),
            "reddit-ads": MCPServerConfig(
                type="stdio", command="npx", args=["-y", "mcp-server-reddit-ads"]
            ),
            "linkedin-ads": MCPServerConfig(
                type="stdio",
                command="npx",
                args=["-y", "@cesteral/linkedin-mcp"],
            ),
            "microsoft-ads": MCPServerConfig(
                type="stdio",
                command="npx",
                args=["-y", "@cesteral/msads-mcp"],
            ),
            "search-console": MCPServerConfig(
                type="stdio", command="uvx", args=["mcp-search-console"]
            ),
        }
        for name, cfg in cases.items():
            with self.subTest(name=name):
                config = Config()
                config.tools.mcp_servers[name] = cfg
                with (
                    patch(
                        "navin.webui.mcp_presets_api.load_config",
                        return_value=config,
                    ),
                    patch(
                        "navin.webui.mcp_presets_api.resolve_config_env_vars",
                        side_effect=lambda c: c,
                    ),
                    patch(
                        "navin.webui.mcp_presets_api._command_available",
                        return_value=True,
                    ),
                ):
                    payload = await mcp_presets_test_action({"name": [name]})
                action = payload["last_action"]
                self.assertFalse(action["ok"])
                self.assertIn("credential", action["error"].lower())

    async def test_connection_reports_missing_dependency(self) -> None:
        name = "tiktok-ads"
        config = Config()
        config.tools.mcp_servers[name] = _sample_cfg(name)
        with (
            patch("navin.webui.mcp_presets_api.load_config", return_value=config),
            patch(
                "navin.webui.mcp_presets_api.resolve_config_env_vars",
                side_effect=lambda c: c,
            ),
            patch("navin.webui.mcp_presets_api._command_available", return_value=False),
        ):
            payload = await mcp_presets_test_action({"name": [name]})
        action = payload["last_action"]
        self.assertFalse(action["ok"])
        self.assertIn("uvx", action["message"])
        self.assertIn("dependency", action["error"].lower())

    async def test_connection_handshake_failure_is_reported(self) -> None:
        name = "meta-ads"
        config = Config()
        config.tools.mcp_servers[name] = _sample_cfg(name)

        async def _empty_connect(servers: dict, registry: Any) -> dict:
            return {}

        with (
            patch("navin.webui.mcp_presets_api.load_config", return_value=config),
            patch(
                "navin.webui.mcp_presets_api.resolve_config_env_vars",
                side_effect=lambda c: c,
            ),
            patch("navin.webui.mcp_presets_api._command_available", return_value=True),
            patch(
                "navin.agent.tools.mcp.connect_mcp_servers",
                new=AsyncMock(side_effect=_empty_connect),
            ),
            patch("navin.webui.mcp_presets_api._close_mcp_stacks", new=AsyncMock()),
        ):
            payload = await mcp_presets_test_action({"name": [name]})
        action = payload["last_action"]
        self.assertFalse(action["ok"])
        self.assertIn("handshake", action["error"].lower())


class AdsProductModuleTest(unittest.TestCase):
    def test_module_registration_and_scoping(self) -> None:
        self.assertIn("ads", VALID_PRODUCT_MODULES)
        self.assertEqual(normalize_product_module("ads"), "ads")
        self.assertEqual(_MODULE_OWNED_COMMANDS["ads"], frozenset({"/ads"}))
        self.assertIn("/ads", CODE_HIDDEN_COMMANDS)
        self.assertTrue(is_command_allowed_for_module("/ads", "ads"))
        self.assertFalse(is_command_allowed_for_module("/ads", "code"))
        self.assertFalse(is_command_allowed_for_module("/ads", "marketing"))
        self.assertFalse(is_command_allowed_for_module("/ads", "seo"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "ads"))
        self.assertIn("Ads", module_mismatch_message("/ads", "code"))

    def test_palette_filters(self) -> None:
        code = {row["command"] for row in builtin_command_palette("code")}
        ads = {row["command"] for row in builtin_command_palette("ads")}
        self.assertNotIn("/ads", code)
        self.assertIn("/ads", ads)
        self.assertNotIn("/campaign", ads)
        self.assertNotIn("/seo", ads)

    def test_paid_ads_manager_is_ads_exclusive_and_hidden_elsewhere(self) -> None:
        exclusive = exclusive_studio_skills()
        self.assertIn("paid-ads-manager", exclusive.get("ads", set()))
        self.assertIn("paid-ads-manager", disabled_skills_for_module("code"))
        self.assertIn("paid-ads-manager", disabled_skills_for_module("marketing"))
        self.assertNotIn("paid-ads-manager", disabled_skills_for_module("ads"))


class AdsWorkflowTest(unittest.TestCase):
    def test_command_spec_and_brief_cover_platforms(self) -> None:
        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/ads", specs)
        self.assertEqual(specs["/ads"].lifecycle, "agent_turn")
        self.assertEqual(specs["/ads"].icon, "badge-dollar-sign")
        self.assertTrue(specs["/ads"].accepts_args)

        title, skills, brief = _WORKFLOW_BRIEFS["/ads"]
        self.assertIn("Ads", title)
        blob = f"{skills}\n{brief}"
        for token in (
            "paid-ads-manager",
            "google-ads",
            "microsoft-ads",
            "meta-ads",
            "tiktok-ads",
            "reddit-ads",
            "linkedin-ads",
            "ads-report",
            "mcp.facebook.com/ads",
            "`ads` engine",
            "action=pipeline",
            "ads/changes.jsonl",
            "status=approved",
        ):
            self.assertIn(token, blob, token)
        for skill in (name.strip() for name in skills.split(",") if name.strip()):
            path = ROOT / "navin" / "skills" / skill / "SKILL.md"
            self.assertTrue(path.is_file(), f"missing skill {skill}")

    def test_workflow_bags_and_model_route(self) -> None:
        self.assertIn("/ads", _DELIVERY_WORKFLOWS)
        self.assertIn("/ads", _TRACKED_WORKFLOWS)
        self.assertIn("/ads", _HTML_REPORT_WORKFLOWS)
        self.assertIn("ads-report", _HTML_REPORT_CLAUSE)
        self.assertEqual(WORKFLOW_ROUTE_ROLES.get("/ads"), "docs")

    def test_handler_preloads_delivery_html_board_and_route(self) -> None:
        msg = SimpleNamespace(
            content="",
            metadata={"product_module": "ads"},
            channel="cli",
            chat_id="ads-test",
        )
        ctx = SimpleNamespace(
            args="Google Ads overview for customer 123",
            raw="/ads Google Ads overview for customer 123",
            msg=msg,
            loop=None,
        )
        result = asyncio.run(_workflow_handler("/ads")(ctx))  # type: ignore[arg-type]
        self.assertIsNone(result)
        meta = dict(msg.metadata)
        self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))
        preloaded = meta.get(PRELOAD_SKILLS_METADATA_KEY) or []
        self.assertIn("paid-ads-manager", preloaded)
        self.assertIn("studio-html-report", preloaded)
        self.assertIn("project-board", preloaded)
        self.assertEqual(meta.get("model_route_role"), "docs")
        self.assertEqual(meta.get("original_command"), "/ads")
        self.assertIn("Ads studio", msg.content)
        self.assertIn("google-ads", msg.content)

    def test_handler_rejects_ads_from_code_module(self) -> None:
        msg = SimpleNamespace(
            content="",
            metadata={"product_module": "code"},
            channel="cli",
            chat_id="ads-test",
        )
        ctx = SimpleNamespace(args="x", raw="/ads x", msg=msg, loop=None)
        out = asyncio.run(_workflow_handler("/ads")(ctx))  # type: ignore[arg-type]
        self.assertIsNotNone(out)
        self.assertIn("Ads", out.content)


class AdsSkillAndDocsTest(unittest.TestCase):
    def test_paid_ads_manager_skill_targets_ads_studio(self) -> None:
        text = (ROOT / "navin/skills/paid-ads-manager/SKILL.md").read_text(
            encoding="utf-8"
        )
        front = text.split("---", 2)[1]
        self.assertIn("ads", front)
        for token in (
            "google-ads",
            "microsoft-ads",
            "meta-ads",
            "tiktok-ads",
            "reddit-ads",
            "linkedin-ads",
            "#/ads",
            "/ads",
            "ads-report",
            "action=pipeline",
            "changes",
        ):
            self.assertIn(token, text, token)

    def test_ads_docs_cover_five_platforms(self) -> None:
        paths = (
            ROOT / "docs/navin_ads/en/README.md",
            ROOT / "docs/navin_ads/fr/README.md",
            ROOT / "site/front/content/docs/navin_ads/en/README.md",
            ROOT / "site/front/content/docs/navin_ads/fr/README.md",
            ROOT / "docs/navin_ads/en/actions.md",
            ROOT / "docs/navin_ads/fr/actions.md",
            ROOT / "docs/navin_ads/en/skills.md",
            ROOT / "docs/navin_ads/fr/skills.md",
            ROOT / "site/front/content/docs/navin_ads/en/actions.md",
            ROOT / "site/front/content/docs/navin_ads/fr/actions.md",
            ROOT / "site/front/content/docs/navin_ads/en/skills.md",
            ROOT / "site/front/content/docs/navin_ads/fr/skills.md",
        )
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                for token in (
                    "google-ads",
                    "meta-ads",
                    "tiktok-ads",
                    "reddit-ads",
                    "linkedin-ads",
                ):
                    self.assertIn(token, text, token)
                if path.name in {"README.md", "actions.md"}:
                    self.assertIn("microsoft-ads" if path.name == "README.md" else "Microsoft Ads", text)
                    self.assertIn("`ads`", text)
                if path.name == "README.md":
                    for token in ("#/ads", "/ads", "actions.md", "skills.md"):
                        self.assertIn(token, text, token)
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)

    def test_paid_ads_manager_has_no_unicode_dashes(self) -> None:
        text = (ROOT / "navin/skills/paid-ads-manager/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)

    def test_public_docs_catalog_lists_ads(self) -> None:
        for relative in (
            "site/front/src/lib/docs-catalog.ts",
            "site/back/src/lib/docs-catalog.ts",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(file=relative):
                self.assertIn('slug: "studio/ads"', text)
                self.assertIn('file: "navin_ads/en/README.md"', text)

    def test_marketing_docs_point_to_ads_module(self) -> None:
        en = (ROOT / "docs/navin_marketing/en/README.md").read_text(encoding="utf-8")
        fr = (ROOT / "docs/navin_marketing/fr/README.md").read_text(encoding="utf-8")
        self.assertNotIn("GOOGLE_ADS_DEVELOPER_TOKEN", en)
        self.assertNotIn("## Connect Google Ads", en)
        self.assertTrue("#/ads" in en or "navin_ads" in en)
        self.assertTrue("#/ads" in fr or "navin_ads" in fr)


class AdsFrontendWiringTest(unittest.TestCase):
    def test_webui_wires_ads_studio_surface(self) -> None:
        checks = {
            "webui/src/App.tsx": (
                '"ads"',
                'path === "/ads"',
                "onOpenAdsStudio",
            ),
            "webui/src/components/Sidebar.tsx": (
                '"ads"',
                "onOpenAdsStudio",
                "adsStudio",
                "BadgeDollarSign",
            ),
            "webui/src/components/studio/StudioWorkspace.tsx": (
                '| "ads"',
                'command: "/ads"',
                "adsGroups",
                "googleAds",
                "metaAds",
                "tiktokAds",
                "redditAds",
                "linkedinAds",
            ),
            "webui/src/components/thread/ThreadShell.tsx": (
                '"ads"',
                "ads|campaign",
            ),
            "webui/src/components/thread/ThreadComposer.tsx": (
                '"badge-dollar-sign"',
                "BadgeDollarSign",
            ),
            "webui/src/components/settings/SkillsCatalogSettings.tsx": (
                '"ads"',
                "Ads",
            ),
        }
        for relative, tokens in checks.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in tokens:
                with self.subTest(file=relative, token=token):
                    self.assertIn(token, text)

    def test_studio_has_fifteen_platform_cards(self) -> None:
        text = (
            ROOT / "webui/src/components/studio/StudioWorkspace.tsx"
        ).read_text(encoding="utf-8")
        self.assertEqual(len(ADS_CARDS), 15)
        for card_id in ADS_CARDS:
            with self.subTest(card=card_id):
                self.assertIn(f'"{card_id}"', text)

    def test_studio_has_engine_and_microsoft_cards(self) -> None:
        text = (
            ROOT / "webui/src/components/studio/StudioWorkspace.tsx"
        ).read_text(encoding="utf-8")
        for card_id in ENGINE_CARDS:
            with self.subTest(card=card_id):
                self.assertIn(f'"{card_id}"', text)
        for token in ('"adsEngine"', '"microsoftAds"', "action=pipeline", "export_changes", "microsoft_bulk"):
            self.assertIn(token, text, token)

    def test_i18n_ads_keys_en_and_fr(self) -> None:
        for locale in ("en", "fr"):
            path = ROOT / f"webui/src/i18n/locales/{locale}/common.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(locale=locale):
                self.assertTrue(data["sidebar"]["adsStudio"])
                self.assertIn("ads", data["studio"])
                self.assertTrue(data["studio"]["ads"]["title"])
                greetings = data["thread"]["empty"]["greetings"]["ads"]
                self.assertEqual(set(greetings), {"a", "b", "c", "d"})
                self.assertIn("ads", data["thread"]["sessionInfo"]["createSeed"])
                self.assertIn("ads", data["thread"]["sessionInfo"]["modules"])
                groups = data["studio"]["groups"]
                for key in (
                    "adsEngine",
                    "googleAds",
                    "microsoftAds",
                    "metaAds",
                    "tiktokAds",
                    "redditAds",
                    "linkedinAds",
                ):
                    self.assertIn(key, groups)
                cards = data["studio"]["cards"]
                for card_id in (
                    "linkedinAdsOverview",
                    "linkedinAdsStructure",
                    "linkedinAdsOptimize",
                    *ENGINE_CARDS,
                ):
                    self.assertTrue(cards[card_id]["label"])
                    self.assertTrue(cards[card_id]["desc"])


if __name__ == "__main__":
    unittest.main()
