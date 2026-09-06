from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.marketing.ads import propose_ads
from navin.marketing.harvest import apply_harvest, parse_site_html
from navin.marketing.produce import produce_assets
from navin.marketing.seo import build_seo
from navin.marketing.social import build_social_calendar
from navin.marketing.store import MarketingStore
from navin.webui.marketing_desk_api import handle_marketing_action

HTML = """
<html>
  <head>
    <title>Acme CRM | Sales OS</title>
    <meta name="description" content="Pipeline for SMB sales teams." />
    <meta property="og:site_name" content="Acme" />
    <meta property="og:image" content="/og.png" />
    <meta name="theme-color" content="#6D28D9" />
    <link rel="icon" href="/logo.png" />
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400&display=swap" rel="stylesheet" />
  </head>
  <body>
    <h1>Close deals without the spreadsheet</h1>
    <a href="/pricing">Pricing</a>
    <a href="/about">About</a>
    <a href="https://linkedin.com/company/acme">LinkedIn</a>
    <a href="/start">Start free</a>
    <img src="/product.png" alt="product" />
  </body>
</html>
"""


class MarketingHarvestParseTest(unittest.TestCase):
    def test_parse_extracts_brand_and_pages(self) -> None:
        report = parse_site_html(HTML, "https://acme.test")
        self.assertEqual(report["name"], "Acme")
        self.assertIn("Pipeline", report["one_liner"])
        self.assertEqual(report["og_image"], "https://acme.test/og.png")
        self.assertEqual(report["logo"], "https://acme.test/logo.png")
        self.assertIn("#6D28D9", report["colors"])
        self.assertIn("DM Sans", report["fonts"])
        self.assertTrue(any("/pricing" in str(page.get("url")) for page in report["pages"]))
        self.assertEqual(report["social"]["linkedin"], "https://linkedin.com/company/acme")


class MarketingHarvestApplyTest(unittest.TestCase):
    def test_apply_fills_brand_seo_and_creatives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            report = parse_site_html(HTML, "https://acme.test")
            with patch("navin.marketing.harvest.download_image", return_value=None):
                apply_harvest(store, report, download=True)
            brand = store.load_brand()
            self.assertEqual(brand["site"], "https://acme.test")
            self.assertEqual(brand["company"], "Acme")
            self.assertIn("#6D28D9", brand["colors"])
            seo = store.load_seo()
            self.assertGreaterEqual(len(seo["pages"]), 1)
            creatives = store.load_creatives()
            self.assertTrue(any(row.get("source") == "site" for row in creatives))
            self.assertTrue(any(row.get("placement") == "site logo" for row in creatives))
            self.assertTrue(any(row.get("placement") == "site og" for row in creatives))

    def test_seo_ranking_is_ingested_not_invented(self) -> None:
        from navin.marketing.seo import ingest_ranking

        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            saved = ingest_ranking(
                store,
                {"keyword": "acme crm", "url": "https://acme.test", "position": 4},
            )
            self.assertEqual(saved["rankings"][0]["position"], 4)
            self.assertEqual(saved["rankings"][0]["source"], "ingested")
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                desk = handle_marketing_action(
                    "seo",
                    {"keyword": "acme crm", "url": "https://acme.test/pricing", "position": 2},
                )
            self.assertEqual(desk["seo"]["rankings"][0]["position"], 2)
            self.assertEqual(len(desk["seo"]["rankings"]), 2)


class MarketingOfferingsTest(unittest.TestCase):
    def test_seo_ads_social_and_produce_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            store.save_product({"name": "Acme", "one_liner": "SMB CRM", "site": "https://acme.test", "pain": "spreadsheets"})
            store.save_brand({"company": "Acme", "audience": "SMB operators", "countries": ["France"], "languages": ["Francais"]})
            store.upsert_content({"channel": "linkedin", "body": "hello", "status": "ready"})
            seo = build_seo(store)
            self.assertIn("Acme", seo["keywords"])
            ads = propose_ads(store)
            self.assertEqual(ads["spend"], 0)
            self.assertEqual(ads["campaigns"][0]["spend"], 0)
            social = build_social_calendar(store)
            self.assertGreaterEqual(len(social["posts"]), 1)
            self.assertEqual(social["posts"][0]["status"], "draft")
            produced = produce_assets(store, kinds=["image"], generate=False)
            self.assertEqual(produced["produced"], 0)


class MarketingHarvestApiTest(unittest.TestCase):
    def test_harvest_and_ads_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with patch(
                    "navin.webui.marketing_desk_api.harvest_live_site",
                    return_value=parse_site_html(HTML, "https://acme.test"),
                ), patch("navin.marketing.harvest.download_image", return_value=None):
                    desk = handle_marketing_action("harvest", {"site": "https://acme.test"})
                self.assertEqual(desk["brand"]["site"], "https://acme.test")
                self.assertEqual(desk["product"]["source_kind"], "url")
                self.assertGreaterEqual(len(desk["seo"]["pages"]), 1)
                self.assertGreaterEqual(len(desk["content"]), 1)
                self.assertGreaterEqual(len(desk["social"]["posts"]), 1)
                self.assertEqual(desk["ads"]["spend"], 0)
                self.assertTrue(any("Close deals" in (row.get("hook") or "") or "Close deals" in (row.get("body") or "") for row in desk["content"]))
                ads = handle_marketing_action("ads")
                self.assertEqual(ads["ads"]["spend"], 0)
                social = handle_marketing_action("social")
                self.assertGreaterEqual(len(social["social"]["posts"]), 1)
