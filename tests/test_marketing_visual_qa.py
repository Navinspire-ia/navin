from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from navin.marketing.visual_qa import (
    VisualQAPolicy,
    deterministic_checks,
    deterministic_gate,
    run_visual_qa,
    write_visual_qa_report,
)
from navin.providers.visual_qa import (
    VisualQAProviderError,
    VisualQAProviderResult,
    normalize_visual_analysis,
)


def _dimension(status: str = "PASS", score: int = 95) -> dict:
    return {
        "status": status,
        "score": score,
        "evidence": ["candidate visibly matches the supplied reference"],
        "recommendation": "",
    }


def _analysis(status: str = "PASS", score: int = 95) -> dict:
    return {
        "dimensions": {
            "product_fidelity": _dimension(status, score),
            "logo_fidelity": _dimension(status, score),
            "text_accuracy": _dimension(status, score),
            "color_fidelity": _dimension(status, score),
            "composition": _dimension(status, score),
        }
    }


class FakeProvider:
    def __init__(self, analysis: dict | None = None, *, invalid: bool = False):
        self.analysis = analysis or _analysis()
        self.invalid = invalid

    async def analyze(self, **kwargs):
        if self.invalid:
            raise VisualQAProviderError("mock invalid response")
        return VisualQAProviderResult(
            analysis=self.analysis,
            provider="mock",
            model="mock-vision",
            usage={"prompt_tokens": 12, "completion_tokens": 8},
            cost_usd=0.002,
        )


class MarketingVisualQATest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.good = self.root / "good.png"
        self.reference = self.root / "reference.png"
        self.bad = self.root / "bad.png"

        for path in (self.good, self.reference):
            image = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((180, 180, 1420, 1420), fill=(30, 80, 180))
            draw.ellipse((500, 500, 1100, 1100), fill=(240, 190, 20))
            image.save(path)
        Image.new("RGBA", (400, 200), (128, 128, 128, 80)).save(self.bad)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_deterministic_gate_passes_a_clean_image(self) -> None:
        result = deterministic_gate(
            self.good, policy=VisualQAPolicy(min_sharpness=1, min_contrast=1)
        )
        self.assertIn(result["verdict"], {"PASS", "WARN"})
        self.assertIsNotNone(result["score"])

    def test_deterministic_gate_blocks_a_tiny_alpha_image(self) -> None:
        result = deterministic_gate(
            self.bad,
            requirements={"width": 1600, "height": 1600, "alpha": False},
        )
        self.assertEqual(result["verdict"], "BLOCK")

    def test_deterministic_checks_cover_required_dimensions(self) -> None:
        metadata, findings = deterministic_checks(
            self.good,
            policy=VisualQAPolicy(min_sharpness=1, min_contrast=1),
            requirements={
                "width": 1600,
                "height": 1600,
                "aspect_ratio": "1:1",
                "marketplace": "amazon",
                "alpha": False,
                "safe_zone_percent": 5,
            },
        )
        self.assertEqual((metadata["width"], metadata["height"]), (1600, 1600))
        names = {finding["check"] for finding in findings}
        self.assertEqual(
            names,
            {
                "dimensions",
                "aspect_ratio",
                "sharpness",
                "contrast",
                "alpha",
                "marketplace_background",
                "safe_zones",
            },
        )

    async def test_good_asset_with_reference_can_pass(self) -> None:
        report = await run_visual_qa(
            self.good,
            references=[self.reference],
            claims=["product_fidelity", "logo_fidelity"],
            requirements={"aspect_ratio": "1:1", "alpha": False},
            provider=FakeProvider(),
            policy=VisualQAPolicy(
                pass_score=75,
                warn_score=50,
                min_sharpness=1,
                min_contrast=1,
                default_safe_zone_percent=1,
            ),
        )
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["provider"]["model"], "mock-vision")
        self.assertEqual(report["provider"]["cost_usd"], 0.002)

    async def test_fidelity_claim_without_reference_never_passes(self) -> None:
        report = await run_visual_qa(
            self.good,
            claims=["product_fidelity"],
            provider=FakeProvider(),
            policy=VisualQAPolicy(min_sharpness=1, min_contrast=1),
        )
        self.assertEqual(report["verdict"], "BLOCK")
        missing = [
            item
            for item in report["findings"]
            if item["check"] == "product_fidelity" and item["source"] == "gate"
        ]
        self.assertEqual(missing[0]["status"], "BLOCK")

    async def test_invalid_provider_response_is_conservative(self) -> None:
        report = await run_visual_qa(
            self.good,
            references=[self.reference],
            claims=["product_fidelity"],
            provider=FakeProvider(invalid=True),
            policy=VisualQAPolicy(min_sharpness=1, min_contrast=1),
        )
        self.assertEqual(report["verdict"], "BLOCK")
        self.assertFalse(report["pass"])
        self.assertTrue(
            any(item["check"] == "vision_analysis" for item in report["findings"])
        )

    async def test_no_provider_blocks_instead_of_simulating(self) -> None:
        report = await run_visual_qa(
            self.good,
            references=[self.reference],
            provider=None,
            policy=VisualQAPolicy(min_sharpness=1, min_contrast=1),
        )
        self.assertEqual(report["verdict"], "BLOCK")

    async def test_bad_deterministic_asset_blocks(self) -> None:
        report = await run_visual_qa(
            self.bad,
            references=[self.reference],
            requirements={
                "width": 1600,
                "height": 1600,
                "aspect_ratio": "1:1",
                "alpha": False,
                "marketplace": "amazon",
            },
            provider=FakeProvider(),
        )
        self.assertEqual(report["verdict"], "BLOCK")
        blocked = {
            item["check"]
            for item in report["findings"]
            if item["status"] == "BLOCK"
        }
        self.assertTrue({"dimensions", "aspect_ratio", "alpha"} <= blocked)

    async def test_reports_are_valid_json_and_markdown(self) -> None:
        report = await run_visual_qa(
            self.good,
            references=[self.reference],
            provider=FakeProvider(),
            policy=VisualQAPolicy(min_sharpness=1, min_contrast=1),
        )
        paths = write_visual_qa_report(report, self.root, report_name="campaign-a")
        json_path = Path(paths["json"])
        markdown_path = Path(paths["markdown"])
        self.assertEqual(json.loads(json_path.read_text())["schema_version"], 1)
        self.assertIn("# Marketing visual QA", markdown_path.read_text())
        self.assertEqual(json_path.parent, self.root / "marketing" / "qa")
        self.assertFalse(any(json_path.parent.glob("*.tmp")))

    def test_provider_schema_rejects_missing_evidence(self) -> None:
        invalid = _analysis()
        invalid["dimensions"]["composition"]["evidence"] = []
        with self.assertRaises(VisualQAProviderError):
            normalize_visual_analysis(invalid)


if __name__ == "__main__":
    unittest.main()

