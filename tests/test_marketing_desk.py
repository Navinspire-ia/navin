from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.marketing.content import adapt_message, generate_content
from navin.marketing.creative import apply_vision, brief_creatives
from navin.marketing.desk import run_pipeline, snapshot
from navin.marketing.errors import MarketingError
from navin.marketing.growth import find_winners, ingest_metrics, run_growth_cycle
from navin.marketing.heartbeat import HEARTBEAT_MARKETING_ACTIONS, brand_is_armed
from navin.marketing.launch import build_launch_kit
from navin.marketing.plan import approve_campaign, build_campaign
from navin.marketing.positioning import build_positioning
from navin.marketing.research import build_research
from navin.marketing.store import MarketingStore
from navin.marketing.understand import scan_workspace, understand_product


class MarketingStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_brand_roundtrip_and_lists(self) -> None:
        saved = self.store.save_brand(
            {
                "company": "InvoiceAI",
                "colors": "navy, gold",
                "forbidden": ["cheap", "hack"],
            }
        )
        self.assertEqual(saved["company"], "InvoiceAI")
        self.assertEqual(saved["colors"], ["navy", "gold"])
        loaded = self.store.load_brand()
        self.assertEqual(loaded["forbidden"], ["cheap", "hack"])

    def test_unknown_channel_is_rejected(self) -> None:
        with self.assertRaises(MarketingError):
            self.store.upsert_content({"channel": "myspace", "body": "nope"})


class MarketingUnderstandTest(unittest.TestCase):
    def test_scan_readme_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "# InvoiceAI\n\nAutomates invoice capture for SMBs.\n",
                encoding="utf-8",
            )
            (root / "package.json").write_text('{"name":"invoice-ai"}\n', encoding="utf-8")
            scanned = scan_workspace(root)
        self.assertEqual(scanned["name"], "InvoiceAI")
        self.assertIn("invoice", scanned["category"])
        self.assertIn("node", scanned["stack"])

    def test_scan_skips_navinprojects_root(self) -> None:
        from navin.marketing.understand import _usable_product_name

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "NavinProjects"
            root.mkdir()
            (root / "README.md").write_text("# NavinProjects\n\nContainer.\n", encoding="utf-8")
            self.assertEqual(scan_workspace(root), {})
        self.assertEqual(_usable_product_name("NavinProjects"), "")
        self.assertEqual(_usable_product_name("InvoiceAI"), "InvoiceAI")
        self.assertFalse(brand_is_armed({"company": "NavinProjects"}, {"name": "NavinProjects"}))
        self.assertTrue(brand_is_armed({"company": "NavinProjects"}, {"name": "Acme", "site": "https://acme.test"}))

    def test_understand_writes_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            product = understand_product(
                store,
                extras={"name": "InvoiceAI", "one_liner": "Invoice capture for SMBs"},
            )
            self.assertEqual(product["name"], "InvoiceAI")
            self.assertEqual(store.load_brand()["product"], "InvoiceAI")
            self.assertTrue(brand_is_armed(store.load_brand(), product))

    def test_url_source_does_not_scan_workspace(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            decoy = Path(tmp) / "decoy"
            decoy.mkdir()
            (decoy / "README.md").write_text("# DecoyRepo\n\nWrong product.\n", encoding="utf-8")
            with patch(
                "navin.marketing.understand.harvest_live_site",
                return_value={
                    "site": "https://acme.test",
                    "name": "Acme",
                    "one_liner": "Live CRM",
                    "images": ["https://acme.test/og.png"],
                },
            ), patch("navin.marketing.understand.apply_harvest"):
                product = understand_product(
                    store,
                    workspace=decoy,
                    extras={"source_kind": "url", "site": "https://acme.test"},
                )
            self.assertEqual(product["name"], "Acme")
            self.assertEqual(product["source_kind"], "url")
            self.assertFalse(product.get("workspace"))
            self.assertNotIn("DecoyRepo", product["name"])

    def test_workspace_screenshots_are_served_as_desk_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            (root / "README.md").write_text("# ShotApp\n\nProduct stills.\n", encoding="utf-8")
            (root / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            store = MarketingStore(Path(tmp) / "desk")
            product = understand_product(store, workspace=root)
            self.assertTrue(product["screenshots"])
            self.assertTrue(product["screenshots"][0].startswith("/api/marketing?action=file"))
            self.assertTrue(any((store.root / "assets").glob("shot-*.png")))

    def test_brand_countries_and_positioning_does_not_repeat_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            saved = store.save_brand({"countries": "France, USA", "languages": "Francais"})
            self.assertEqual(saved["countries"], ["France", "USA"])
            understand_product(store, extras={"name": "InvoiceAI", "one_liner": "Invoice capture"})
            pos = build_positioning(store)
            self.assertNotIn("qui InvoiceAI", pos["statement"])
            self.assertIn("InvoiceAI est un", pos["statement"])


class MarketingPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))
        understand_product(self.store, extras={"name": "InvoiceAI", "one_liner": "Invoice capture"})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_positioning_and_research(self) -> None:
        pos = build_positioning(self.store)
        self.assertIn("PME", pos["icp"])
        self.assertIn("CFO", pos["personas"])
        self.assertEqual(pos["source"], "template")
        # Offline (conftest) and no injected search: the map is honestly empty, never seeded.
        empty = build_research(self.store)
        self.assertEqual(empty["competitors"], [])
        self.assertEqual(empty["mode"], "empty")

        seen: list[str] = []

        def search(query: str, limit: int) -> list[dict[str, str]]:
            seen.append(query)
            return [
                {"title": "Pennylane - Comptabilite et facturation pour PME", "url": "https://www.pennylane.com/fr/", "snippet": "Des 19 EUR/mois, factures et tresorerie."},
                {"title": "Top 10 des meilleurs logiciels de facturation 2026", "url": "https://www.appvizer.fr/comparatif", "snippet": "Comparatif."},
                {"title": "Yooz | Automatisation des factures fournisseurs", "url": "https://www.getyooz.com/fr", "snippet": "OCR et workflow."},
                {"title": "InvoiceAI - Invoice capture", "url": "https://invoiceai.example", "snippet": "Le produit lui-meme."},
                {"title": "Invoice automation trends 2026: AI takes over AP", "url": "https://blog.example.com/trends", "snippet": "Etude marche."},
            ][:limit]

        research = build_research(self.store, search=search)
        self.assertTrue(seen)
        names = [row["name"] for row in research["competitors"]]
        self.assertIn("Pennylane", names)
        self.assertIn("Yooz", names)
        self.assertNotIn("InvoiceAI", names)
        self.assertFalse(any("appvizer" in str(row.get("url") or "") for row in research["competitors"]))
        self.assertEqual(research["mode"], "web")
        self.assertIn("19 EUR/mois", research["pricing_notes"][0])
        self.assertTrue(any("trends" in item.lower() for item in research["trends"]))
        self.assertTrue(self.store.load_competitors())

    def test_campaign_content_and_launch(self) -> None:
        campaign = build_campaign(self.store, goal="1000 inscriptions", days=30, signups=1000)
        self.assertEqual(campaign["status"], "planned")
        self.assertEqual(campaign["mix"]["linkedin"], 20)
        approved = approve_campaign(self.store, campaign["id"])
        self.assertEqual(approved["status"], "approved")
        rows = generate_content(self.store, campaign_id=campaign["id"])
        channels = {row["channel"] for row in rows}
        self.assertIn("linkedin", channels)
        self.assertIn("x", channels)
        self.assertIn("hours", adapt_message("linkedin", self.store.load_product(), self.store.load_positioning()))
        self.assertTrue(any(row.get("cta") for row in rows))
        kit = build_launch_kit(self.store)
        ids = {item["id"] for item in kit["items"]}
        self.assertIn("producthunt", ids)
        self.assertIn("press", ids)

    def test_full_pipeline_snapshot(self) -> None:
        result = run_pipeline(self.store, goal="launch", days=30)
        desk = snapshot(self.store)
        self.assertTrue(desk["armed"])
        self.assertGreaterEqual(desk["kpis"]["campaigns"], 1)
        self.assertGreaterEqual(len(result["content"]), 4)
        self.assertEqual(desk["launch"]["status"], "ready")
        self.assertTrue((self.store.root / "launch" / "landing.md").is_file())
        self.assertTrue(any(item.get("file") == "landing.md" for item in desk["launch"]["items"]))

    def test_content_and_campaign_do_not_duplicate(self) -> None:
        first = generate_content(self.store, channels=["linkedin", "x"])
        second = generate_content(self.store, channels=["linkedin", "x"])
        self.assertEqual({row["id"] for row in first}, {row["id"] for row in second})
        self.assertEqual(
            sum(1 for row in self.store.load_content() if row.get("channel") == "linkedin" and not row.get("parent_id")),
            1,
        )
        one = build_campaign(self.store, goal="1000 inscriptions", days=30, signups=1000)
        two = build_campaign(self.store, goal="1000 inscriptions", days=30, signups=1000)
        self.assertEqual(one["id"], two["id"])
        self.assertEqual(
            sum(1 for row in self.store.load_campaigns() if row.get("status") == "planned"),
            1,
        )


class MarketingGrowthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))
        understand_product(self.store, extras={"name": "InvoiceAI"})
        build_positioning(self.store)
        self.rows = generate_content(self.store, channels=["linkedin", "x", "email"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_winner_spawns_variant(self) -> None:
        control, other, third = self.rows[:3]
        ingest_metrics(
            self.store,
            {
                "signups": 40,
                "by_content": {
                    control["id"]: {"views": 200, "clicks": 80, "conversions": 24},
                    other["id"]: {"views": 180, "clicks": 10, "conversions": 2},
                    third["id"]: {"views": 160, "clicks": 8, "conversions": 1},
                },
            },
        )
        winners = find_winners(self.store)
        self.assertEqual(winners[0]["id"], control["id"])
        growth = run_growth_cycle(self.store)
        self.assertEqual(len(growth["variants"]), 1)
        self.assertEqual(growth["variants"][0]["parent_id"], control["id"])
        self.assertEqual(self.store.get_content(control["id"])["status"], "winner")

    def test_vision_loop_blocks_then_passes(self) -> None:
        created = brief_creatives(self.store, kinds=["image"])
        blocked = apply_vision(self.store, created[0]["id"], verdict="BLOCK", notes="text overflow")
        self.assertEqual(blocked["status"], "revise")
        passed = apply_vision(self.store, created[0]["id"], verdict="PASS", score=92)
        self.assertEqual(passed["status"], "approved")
        self.assertEqual(passed["vision"]["verdict"], "PASS")


class MarketingSocialPackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))
        understand_product(
            self.store,
            extras={
                "name": "Acme CRM",
                "one_liner": "Close deals without the spreadsheet.",
                "site": "https://acme.test",
                "source_kind": "url",
            },
        )
        self.store.save_harvest(
            {
                "site": "https://acme.test",
                "name": "Acme CRM",
                "one_liner": "Close deals without the spreadsheet.",
                "headings": ["Close deals faster"],
                "ctas": ["Book a demo"],
            }
        )
        build_positioning(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_variants_are_the_four_networks(self) -> None:
        rows = generate_content(self.store)
        channels = {row["channel"] for row in rows}
        self.assertEqual(channels, {"linkedin", "facebook", "instagram", "tiktok"})
        linkedin = next(row for row in rows if row["channel"] == "linkedin")
        self.assertIn("Close deals faster", linkedin["body"])
        self.assertIn("Book a demo", linkedin["cta"])
        self.assertIn("Acme", " ".join(linkedin.get("hashtags") or []))
        tiktok = next(row for row in rows if row["channel"] == "tiktok")
        instagram = next(row for row in rows if row["channel"] == "instagram")
        self.assertIn("Close deals", tiktok["body"])
        self.assertIn("Close deals without the spreadsheet", instagram["body"])
        self.assertLess(instagram["body"].lower().count("acme crm"), 3)
        self.assertNotIn("NavinProjects", linkedin["body"])
        self.store.save_positioning({"value_prop": "NavinProjects automatise le travail repetitif"})
        self.store.upsert_content(
            {
                "channel": "email",
                "campaign_id": "old-campaign",
                "body": "NavinProjects leftover",
                "status": "ready",
            }
        )
        cleaned = generate_content(self.store)
        self.assertFalse(any("NavinProjects" in str(row.get("body") or "") for row in cleaned))
        self.assertFalse(any("NavinProjects" in str(row.get("body") or "") for row in self.store.load_content()))
        self.assertFalse(any(row.get("channel") == "email" for row in self.store.load_content()))

    def test_ship_pack_briefs_stills_and_clips(self) -> None:
        from navin.marketing.desk import ship_social_pack
        from navin.webui.marketing_desk_api import handle_marketing_action

        packed = ship_social_pack(self.store, generate=False)
        placements = {(row.get("kind"), row.get("placement")) for row in packed["desk"]["creatives"]}
        self.assertIn(("image", "linkedin post"), placements)
        self.assertIn(("image", "facebook post"), placements)
        self.assertIn(("image", "instagram post"), placements)
        self.assertIn(("image", "tiktok cover"), placements)
        self.assertIn(("video", "tiktok clip"), placements)
        self.assertIn(("video", "instagram reel"), placements)
        self.assertIn(("video", "facebook clip"), placements)
        self.assertIn(("video", "linkedin clip"), placements)
        social_channels = {row["channel"] for row in packed["social"]["posts"]}
        self.assertEqual(social_channels, {"linkedin", "facebook", "instagram", "tiktok"})
        linkedin_post = next(row for row in packed["social"]["posts"] if row["channel"] == "linkedin")
        creative = next(
            (item for item in packed["desk"]["creatives"] if item.get("id") == linkedin_post.get("creative_id")),
            {},
        )
        self.assertEqual(str(creative.get("placement") or ""), "linkedin post")
        clip = next(
            (item for item in packed["desk"]["creatives"] if item.get("id") == linkedin_post.get("clip_id")),
            {},
        )
        self.assertEqual(str(clip.get("placement") or ""), "linkedin clip")
        self.store.upsert_creative({"kind": "image", "placement": "instagram post", "prompt": "product leftover", "status": "brief"})
        again = ship_social_pack(self.store, generate=False)
        instagram_stills = [
            row
            for row in again["desk"]["creatives"]
            if row.get("kind") == "image" and row.get("placement") == "instagram post"
        ]
        self.assertEqual(len(instagram_stills), 1)
        with patch("navin.webui.marketing_desk_api._store", return_value=self.store):
            desk = handle_marketing_action("ship", {"generate": False})
        self.assertEqual(desk["ship"]["channels"], ["linkedin", "facebook", "instagram", "tiktok"])
        self.assertGreaterEqual(desk["ship"]["content"], 4)

    def test_ship_needs_a_real_product(self) -> None:
        from navin.marketing.desk import ship_social_pack
        from navin.marketing.errors import MarketingError

        empty = MarketingStore(Path(self.tmp.name) / "empty")
        with self.assertRaises(MarketingError):
            ship_social_pack(empty, generate=False)


class MarketingHeartbeatPolicyTest(unittest.TestCase):
    def test_heartbeat_actions_are_reads_only(self) -> None:
        self.assertIn("watch", HEARTBEAT_MARKETING_ACTIONS)
        self.assertNotIn("pipeline", HEARTBEAT_MARKETING_ACTIONS)
        self.assertNotIn("start", HEARTBEAT_MARKETING_ACTIONS)
        self.assertNotIn("ship", HEARTBEAT_MARKETING_ACTIONS)
        self.assertNotIn("produce", HEARTBEAT_MARKETING_ACTIONS)


if __name__ == "__main__":
    unittest.main()
