"""End-to-end expectations for the SEO studio + Search Console MCP."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Import agent first so command↔agent circular imports resolve like other tests.
import navin.agent.skills  # noqa: F401
from navin.agent.model_routes import WORKFLOW_ROUTE_ROLES
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
from navin.webui.mcp_presets_api import (
    MCP_PRESETS,
    McpPresetError,
    _materialize_server,
    mcp_presets_payload,
)

ROOT = Path(__file__).resolve().parents[1]
SEO_CARDS = (
    "techAudit",
    "onPageAudit",
    "seoCompetitors",
    "contentAudit",
    "keywordResearch",
    "topicClusters",
    "questionsResearch",
    "localSeo",
    "seoArticle",
    "metaTags",
    "schemaMarkup",
    "linkStrategy",
)
SEO_PRELOAD = (
    "studio-expert-contract",
    "critic-reviewer",
    "seo-technical-auditor",
    "keyword-research",
    "on-page-seo-optimizer",
    "seo-content-writer",
    "backlink-strategy",
    "competitor-seo-analysis",
    "local-seo",
    "geo-ai-search-optimizer",
    "image-generation",
    "seo-data-provider",
)


class SeoMcpPresetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.by_name = {preset.name: preset for preset in MCP_PRESETS}

    def test_search_console_registered_under_seo_category(self) -> None:
        self.assertIn("search-console", self.by_name)
        preset = self.by_name["search-console"]
        self.assertEqual(preset.category, "seo")
        self.assertTrue(preset.install_supported)
        self.assertEqual(preset.transport, "stdio")
        self.assertEqual(preset.server.command, "uvx")
        self.assertEqual(preset.server.args, ["mcp-search-console"])
        self.assertEqual(preset.server.env.get("GSC_ALLOW_DESTRUCTIVE"), "false")

    def test_catalogue_payload_lists_search_console(self) -> None:
        names = {item["name"] for item in mcp_presets_payload()["presets"]}
        self.assertIn("search-console", names)

    def _materialize(self, query: dict[str, list[str]]):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "navin.webui.mcp_presets_api.get_runtime_subdir",
                side_effect=lambda *parts: root.joinpath(*parts),
            ):
                return _materialize_server(self.by_name["search-console"], query, None)

    def test_search_console_requires_oauth_or_service_account(self) -> None:
        with self.assertRaises(McpPresetError):
            self._materialize({})
        cfg = self._materialize(
            {"gsc_credentials_path": ["/tmp/sa.json"], "gsc_skip_oauth": ["true"]}
        )
        self.assertEqual(cfg.env["GSC_CREDENTIALS_PATH"], "/tmp/sa.json")
        self.assertEqual(cfg.env["GSC_SKIP_OAUTH"], "true")
        self.assertEqual(cfg.env.get("GSC_ALLOW_DESTRUCTIVE"), "false")

    def test_search_console_oauth_path(self) -> None:
        cfg = self._materialize(
            {"gsc_oauth_client_secrets_file": ["/tmp/client_secrets.json"]}
        )
        self.assertEqual(
            cfg.env["GSC_OAUTH_CLIENT_SECRETS_FILE"],
            "/tmp/client_secrets.json",
        )


class SeoProductModuleTest(unittest.TestCase):
    def test_module_registration_and_scoping(self) -> None:
        self.assertIn("seo", VALID_PRODUCT_MODULES)
        self.assertEqual(normalize_product_module("seo"), "seo")
        self.assertEqual(_MODULE_OWNED_COMMANDS["seo"], frozenset({"/seo"}))
        self.assertIn("/seo", CODE_HIDDEN_COMMANDS)
        self.assertTrue(is_command_allowed_for_module("/seo", "seo"))
        self.assertFalse(is_command_allowed_for_module("/seo", "code"))
        self.assertFalse(is_command_allowed_for_module("/seo", "marketing"))
        self.assertFalse(is_command_allowed_for_module("/seo", "ads"))
        self.assertFalse(is_command_allowed_for_module("/seo", "leads"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "seo"))
        self.assertFalse(is_command_allowed_for_module("/ads", "seo"))
        self.assertIn("SEO", module_mismatch_message("/seo", "code"))

    def test_palette_filters(self) -> None:
        code = {row["command"] for row in builtin_command_palette("code")}
        seo = {row["command"] for row in builtin_command_palette("seo")}
        self.assertNotIn("/seo", code)
        self.assertIn("/seo", seo)
        self.assertNotIn("/campaign", seo)
        self.assertNotIn("/ads", seo)
        self.assertNotIn("/leads", seo)

    def test_seo_exclusives_hidden_elsewhere(self) -> None:
        exclusive = exclusive_studio_skills()
        self.assertIn("seo-technical-auditor", exclusive.get("seo", set()))
        self.assertIn("seo-data-provider", exclusive.get("seo", set()))
        self.assertIn("seo-technical-auditor", disabled_skills_for_module("code"))
        self.assertIn("seo-data-provider", disabled_skills_for_module("marketing"))
        self.assertNotIn("seo-technical-auditor", disabled_skills_for_module("seo"))
        self.assertNotIn("seo-data-provider", disabled_skills_for_module("seo"))


class SeoWorkflowTest(unittest.TestCase):
    def test_command_spec_and_brief_cover_desk(self) -> None:
        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/seo", specs)
        self.assertEqual(specs["/seo"].lifecycle, "agent_turn")
        self.assertEqual(specs["/seo"].icon, "trending-up")
        self.assertTrue(specs["/seo"].accepts_args)

        title, skills, brief = _WORKFLOW_BRIEFS["/seo"]
        self.assertIn("SEO", title)
        names = [name.strip() for name in skills.split(",") if name.strip()]
        for skill in SEO_PRELOAD:
            self.assertIn(skill, names, skill)
            path = ROOT / "navin" / "skills" / skill / "SKILL.md"
            self.assertTrue(path.is_file(), f"missing skill {skill}")
        blob = f"{skills}\n{brief}"
        for token in (
            "seo-technical-auditor",
            "seo-data-provider",
            "audit_score.py",
            "seo-report",
            "never invent volume",
        ):
            self.assertIn(token, blob, token)

    def test_workflow_bags_and_model_route(self) -> None:
        self.assertIn("/seo", _DELIVERY_WORKFLOWS)
        self.assertIn("/seo", _TRACKED_WORKFLOWS)
        self.assertIn("/seo", _HTML_REPORT_WORKFLOWS)
        self.assertIn("seo-report", _HTML_REPORT_CLAUSE)
        self.assertEqual(WORKFLOW_ROUTE_ROLES.get("/seo"), "docs")

    def test_handler_preloads_delivery_html_board_and_route(self) -> None:
        msg = SimpleNamespace(
            content="",
            metadata={"product_module": "seo"},
            channel="cli",
            chat_id="seo-test",
        )
        ctx = SimpleNamespace(
            args="https://example.com - full technical audit",
            raw="/seo https://example.com - full technical audit",
            msg=msg,
            loop=None,
        )
        result = asyncio.run(_workflow_handler("/seo")(ctx))  # type: ignore[arg-type]
        self.assertIsNone(result)
        meta = dict(msg.metadata)
        self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))
        preloaded = meta.get(PRELOAD_SKILLS_METADATA_KEY) or []
        self.assertIn("seo-technical-auditor", preloaded)
        self.assertIn("seo-data-provider", preloaded)
        self.assertIn("image-generation", preloaded)
        self.assertIn("studio-html-report", preloaded)
        self.assertIn("project-board", preloaded)
        self.assertEqual(meta.get("model_route_role"), "docs")
        self.assertEqual(meta.get("original_command"), "/seo")
        self.assertIn("SEO studio", msg.content)

    def test_handler_rejects_seo_from_code_module(self) -> None:
        msg = SimpleNamespace(
            content="",
            metadata={"product_module": "code"},
            channel="cli",
            chat_id="seo-test",
        )
        ctx = SimpleNamespace(args="x", raw="/seo x", msg=msg, loop=None)
        out = asyncio.run(_workflow_handler("/seo")(ctx))  # type: ignore[arg-type]
        self.assertIsNotNone(out)
        self.assertIn("SEO", out.content)


class SeoSkillAndDocsTest(unittest.TestCase):
    def test_audit_score_script_exists(self) -> None:
        path = ROOT / "navin/skills/seo-technical-auditor/scripts/audit_score.py"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("def ", text)

    def test_seo_data_provider_does_not_invent_metrics(self) -> None:
        text = (ROOT / "navin/skills/seo-data-provider/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("DATAFORSEO", text)
        self.assertIn("SEMRUSH", text)
        self.assertIn("Never invent", text)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)

    def test_seo_docs_cover_gsc_and_data_rules(self) -> None:
        paths = (
            ROOT / "docs/navin_seo/en/README.md",
            ROOT / "docs/navin_seo/fr/README.md",
            ROOT / "site/front/content/docs/navin_seo/en/README.md",
            ROOT / "docs/navin_seo/en/actions.md",
            ROOT / "docs/navin_seo/fr/actions.md",
            ROOT / "docs/navin_seo/en/skills.md",
            ROOT / "docs/navin_seo/fr/skills.md",
            ROOT / "site/front/content/docs/navin_seo/en/skills.md",
        )
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)
                if path.name == "README.md":
                    for token in ("#/seo", "/seo", "search-console", "actions.md"):
                        self.assertIn(token, text, token)
                if path.name == "skills.md":
                    self.assertIn("image-generation", text)
                    self.assertIn("seo-data-provider", text)
                    self.assertIn("geo-ai-search-optimizer", text)

    def test_public_docs_catalog_lists_seo(self) -> None:
        for relative in (
            "site/front/src/lib/docs-catalog.ts",
            "site/back/src/lib/docs-catalog.ts",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(file=relative):
                self.assertIn('slug: "studio/seo"', text)
                self.assertIn('file: "navin_seo/en/README.md"', text)


class SeoFrontendWiringTest(unittest.TestCase):
    def test_webui_wires_seo_studio_surface(self) -> None:
        checks = {
            "webui/src/App.tsx": (
                '"seo"',
                'path === "/seo"',
                "onOpenSeoStudio",
            ),
            "webui/src/components/Sidebar.tsx": (
                '"seo"',
                "onOpenSeoStudio",
                "seoStudio",
            ),
            "webui/src/components/studio/StudioWorkspace.tsx": (
                '| "seo"',
                'command: "/seo"',
                "seoGroups",
            ),
            "webui/src/components/thread/ThreadComposer.tsx": (
                '"trending-up"',
                "TrendingUp",
            ),
            "webui/src/components/settings/SkillsCatalogSettings.tsx": (
                '"seo"',
            ),
        }
        for relative, tokens in checks.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in tokens:
                with self.subTest(file=relative, token=token):
                    self.assertIn(token, text)

    def test_studio_has_twelve_seo_cards(self) -> None:
        text = (
            ROOT / "webui/src/components/studio/StudioWorkspace.tsx"
        ).read_text(encoding="utf-8")
        for card_id in SEO_CARDS:
            with self.subTest(card=card_id):
                self.assertIn(f'"{card_id}"', text)
        self.assertEqual(len(SEO_CARDS), 12)

    def test_i18n_seo_keys_en_and_fr(self) -> None:
        for locale in ("en", "fr"):
            path = ROOT / f"webui/src/i18n/locales/{locale}/common.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(locale=locale):
                self.assertTrue(data["sidebar"]["seoStudio"])
                self.assertIn("seo", data["studio"])
                self.assertTrue(data["studio"]["seo"]["title"])
                greetings = data["thread"]["empty"]["greetings"]["seo"]
                self.assertEqual(set(greetings), {"a", "b", "c", "d"})
                self.assertIn("seo", data["thread"]["sessionInfo"]["createSeed"])
                self.assertIn("seo", data["thread"]["sessionInfo"]["modules"])
                groups = data["studio"]["groups"]
                for key in ("audit", "research", "optimize"):
                    self.assertIn(key, groups)
                cards = data["studio"]["cards"]
                for card_id in SEO_CARDS:
                    self.assertIn(card_id, cards, card_id)
                    self.assertTrue(cards[card_id]["label"])


class GtmStudioDocCountsTest(unittest.TestCase):
    """Lock the index counts that drifted from the studio UI."""

    def test_leads_index_says_seventeen_actions(self) -> None:
        text = (ROOT / "docs/navin_leads/README.md").read_text(encoding="utf-8")
        self.assertIn("17 studio actions", text)
        self.assertNotIn("16 studio actions", text)
        actions = (ROOT / "docs/navin_leads/en/actions.md").read_text(encoding="utf-8")
        self.assertIn("17 cards", actions)

    def test_documents_index_says_fifty_templates(self) -> None:
        text = (ROOT / "docs/navin_contenant/README.md").read_text(encoding="utf-8")
        self.assertIn("50 templates (25 business + 25 legal)", text)
        self.assertIn("50 modèles (25 métier + 25 juridiques)", text)
        self.assertNotIn("5 themes", text)
        templates = (ROOT / "docs/navin_contenant/en/templates.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("50 models", templates)

    def test_marketing_index_says_seventeen_actions(self) -> None:
        text = (ROOT / "docs/navin_marketing/README.md").read_text(encoding="utf-8")
        self.assertIn("17 studio actions", text)
        self.assertIn("17 actions du studio", text)
        en = (ROOT / "docs/navin_marketing/en/actions.md").read_text(encoding="utf-8")
        fr = (ROOT / "docs/navin_marketing/fr/actions.md").read_text(encoding="utf-8")
        site = (
            ROOT / "site/front/content/docs/navin_marketing/en/actions.md"
        ).read_text(encoding="utf-8")
        for blob in (en, fr, site):
            self.assertIn("17", blob.splitlines()[0])
            self.assertIn("/montage", blob)


if __name__ == "__main__":
    unittest.main()
