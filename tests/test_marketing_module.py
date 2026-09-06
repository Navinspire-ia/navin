from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from navin.agent.loop import AgentLoop
from navin.agent.tools.visual_qa import VisualQAToolConfig, visual_qa_readiness
from navin.config.schema import Config
from navin.providers.visual_qa import (
    get_visual_qa_provider,
    visual_qa_provider_names,
)

ROOT = Path(__file__).resolve().parents[1]


class MarketingImageAutoQATest(unittest.TestCase):
    """generate_image must attach a QA verdict on marketing stills (P0 enforce)."""

    def _artifact_with_image(self, root: Path) -> dict:
        from PIL import Image

        path = root / "still.png"
        Image.new("RGB", (1600, 1600), "white").save(path)
        return {"id": "img_1", "path": str(path)}

    def _tool(self):
        from navin.agent.tools.image_generation import (
            ImageGenerationTool,
            ImageGenerationToolConfig,
        )

        return ImageGenerationTool(
            workspace="/tmp", config=ImageGenerationToolConfig(marketing_auto_qa=True)
        )

    def test_marketing_turn_attaches_a_verdict(self) -> None:
        from navin.agent.tools.context import RequestContext, request_context
        from navin.command.modules import PRODUCT_MODULE_METADATA_KEY

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = [self._artifact_with_image(Path(tmp))]
            ctx = RequestContext(
                channel="webui",
                chat_id="c1",
                metadata={PRODUCT_MODULE_METADATA_KEY: "marketing"},
            )
            with request_context(ctx):
                self._tool()._maybe_apply_marketing_qa(artifacts)
        self.assertIn("visual_qa", artifacts[0])
        self.assertIn(artifacts[0]["visual_qa"]["verdict"], {"PASS", "WARN", "BLOCK"})

    def test_non_marketing_turn_is_untouched(self) -> None:
        from navin.agent.tools.context import RequestContext, request_context
        from navin.command.modules import PRODUCT_MODULE_METADATA_KEY

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = [self._artifact_with_image(Path(tmp))]
            ctx = RequestContext(
                channel="webui",
                chat_id="c1",
                metadata={PRODUCT_MODULE_METADATA_KEY: "code"},
            )
            with request_context(ctx):
                self._tool()._maybe_apply_marketing_qa(artifacts)
        self.assertNotIn("visual_qa", artifacts[0])

    def test_tool_result_exposes_the_verdict_to_the_agent(self) -> None:
        from navin.utils.artifacts import generated_image_tool_result

        payload = json.loads(
            generated_image_tool_result(
                [
                    {
                        "id": "img_1",
                        "path": "/tmp/still.png",
                        "visual_qa": {"verdict": "WARN", "gate": "deterministic"},
                    }
                ]
            )
        )
        self.assertEqual(payload["artifacts"][0]["visual_qa"]["verdict"], "WARN")


class MarketingVisualQAWiringTest(unittest.TestCase):
    def test_config_schema_accepts_camel_and_snake_case(self) -> None:
        camel = Config.model_validate(
            {
                "tools": {
                    "visualQA": {
                        "enabled": True,
                        "passScore": 90,
                        "warnScore": 70,
                    }
                }
            }
        )
        self.assertTrue(camel.tools.visual_qa.enabled)
        self.assertEqual(camel.tools.visual_qa.pass_score, 90)
        snake = Config.model_validate(
            {"tools": {"visual_qa": {"block_on_vision_failure": False}}}
        )
        self.assertFalse(snake.tools.visual_qa.block_on_vision_failure)

    def test_chat_adapter_is_in_visual_qa_registry(self) -> None:
        self.assertIn("chat", visual_qa_provider_names())
        self.assertIsNotNone(get_visual_qa_provider("chat"))

    def test_readiness_reports_dependency_credentials_and_model(self) -> None:
        class FakeProvider:
            pass

        snapshot = SimpleNamespace(
            provider=FakeProvider(),
            model="mock-vision",
            context_window_tokens=1000,
            signature=("mock",),
            generation=None,
        )
        ready = visual_qa_readiness(
            VisualQAToolConfig(), lambda **_: snapshot
        )
        self.assertTrue(ready["dependency"])
        self.assertTrue(ready["credentials"])
        self.assertEqual(ready["model"], "mock-vision")
        self.assertTrue(ready["ready"])

    def test_tool_is_exposed_only_to_marketing_turns(self) -> None:
        marketing = SimpleNamespace(
            content="review this visual",
            metadata={"product_module": "marketing"},
        )
        code = SimpleNamespace(
            content="review this visual",
            metadata={"product_module": "code"},
        )
        no_module = SimpleNamespace(content="review this visual", metadata={})
        self.assertNotIn("visual_qa", AgentLoop._denied_tools(marketing, None))
        self.assertIn("visual_qa", AgentLoop._denied_tools(code, None))
        self.assertIn("visual_qa", AgentLoop._denied_tools(no_module, None))

    def test_four_marketing_skills_require_the_gate(self) -> None:
        for skill in (
            "product-visuals",
            "ad-creative-generator",
            "campaign-manager",
            "studio-expert-contract",
        ):
            text = (ROOT / "navin" / "skills" / skill / "SKILL.md").read_text(
                encoding="utf-8"
            )
            with self.subTest(skill=skill):
                self.assertIn("visual_qa", text)
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)

    def test_marketing_docs_match_seventeen_ui_actions(self) -> None:
        studio = (
            ROOT / "webui/src/components/studio/StudioWorkspace.tsx"
        ).read_text(encoding="utf-8")
        action_ids = (
            "productImages",
            "adVideo",
            "projectMontage",
            "socialVisuals",
            "brandKit",
            "posterDesign",
            "bannerPack",
            "productOrbit3d",
            "socialCarousel",
            "socialPosts",
            "blogArticle",
            "emailSequence",
            "landingCopy",
            "fullCampaign",
            "persona",
            "contentCalendar",
            "competitorScan",
        )
        self.assertEqual(len(action_ids), 17)
        for action_id in action_ids:
            self.assertIn(f'"{action_id}"', studio)
        for relative in (
            "docs/navin_marketing/en/actions.md",
            "docs/navin_marketing/fr/actions.md",
            "site/front/content/docs/navin_marketing/en/actions.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("17", text.splitlines()[0])
            self.assertIn("visual_qa", text)
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)

    def test_public_docs_catalog_lists_marketing_desk(self) -> None:
        pages = ("README", "desk", "loop", "heartbeat", "ai", "actions", "skills", "desktop")
        front_catalog = (ROOT / "site/front/src/lib/docs-catalog.ts").read_text(
            encoding="utf-8"
        )
        back_catalog = (ROOT / "site/back/src/lib/docs-catalog.ts").read_text(
            encoding="utf-8"
        )
        for page in pages:
            for lang in ("en", "fr"):
                path = ROOT / f"docs/navin_marketing/{lang}/{page}.md"
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertTrue(path.is_file())
                    text = path.read_text(encoding="utf-8")
                    self.assertIn("#/marketing", text)
                    self.assertNotIn("\u2014", text)
                    self.assertNotIn("\u2013", text)
            published = ROOT / f"site/front/content/docs/navin_marketing/en/{page}.md"
            with self.subTest(published=str(published.relative_to(ROOT))):
                self.assertTrue(
                    published.is_file(), "run: cd site/front && npm run sync-docs"
                )
            slug = "studio/marketing" if page == "README" else f"studio/marketing/{page}"
            file_ref = f'file: "navin_marketing/en/{page}.md"'
            for catalog in (front_catalog, back_catalog):
                self.assertIn(f'slug: "{slug}"', catalog, slug)
                self.assertIn(file_ref, catalog, file_ref)
        hub = (ROOT / "docs/navin_marketing/README.md").read_text(encoding="utf-8")
        self.assertIn("#/marketing", hub)
        for page in pages[1:]:
            self.assertIn(f"./en/{page}.md", hub, page)
            self.assertIn(f"./fr/{page}.md", hub, page)


if __name__ == "__main__":
    unittest.main()

