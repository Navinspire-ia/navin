# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration contract for the official Tenders source registry.

API / Open Data first. Official scrape next. TED covers EU-threshold
notices. No paid aggregator. No silent drop. No invented API.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.tenders.collect import collect
from navin.tenders.fetchers import FETCHERS
from navin.tenders.scrape_net import _api_hosts, _search_sources
from navin.tenders.sources import (
    API_SOURCE_IDS,
    NATIONAL_SCRAPE_IDS,
    catalog,
    coverage_holes,
    official_hosts,
    source_by_id,
)
from navin.tenders.store import TenderStore
from navin.webui.tenders_api import handle_tenders_action

ROOT = Path(__file__).resolve().parents[1]

# V1 sources the product promised. URLs are official public portals.
_API_FIRST = {
    "ted": "https://ted.europa.eu/",
    "boamp": "https://www.boamp.fr/",
    "find-a-tender": "https://www.find-tender.service.gov.uk/",
    "contracts-finder": "https://www.contractsfinder.service.gov.uk/",
    "sam-gov": "https://sam.gov/content/opportunities",
    "canadabuys": "https://canadabuys.canada.ca/",
    "world-bank": "https://projects.worldbank.org/en/projects-operations/procurement-search",
}
_DUAL_SCRAPE = {
    "place": "https://www.marches-publics.gouv.fr/",
    "belgium-eproc": "https://www.publicprocurement.be/",
    "simap": "https://www.simap.ch/",
    "luxembourg-pmp": "https://pmp.b2g.etat.lu/",
    "sweden-uhmynd": "https://www.upphandlingsmyndigheten.se/",
}
_AFRICA_SCRAPE = {
    "afdb": "https://www.afdb.org/en/projects-and-operations/procurement",
    "ungm": "https://www.ungm.org/Public/Notice",
    "maroc-marches": "https://www.marchespublics.gov.ma/",
    "haicop": "https://www.marchespublics.gov.tn/",
    "tuneps": "https://www.tuneps.tn/",
    "algeria-marches": "https://dgb.mf.gov.dz/?page_id=2515",
    "cameroon-armp": "https://www.armp.cm/",
    "benin-marches": "https://marches-publics.bj/",
    "senegal-armds": "https://www.armp.sn/",
    "cote-ivoire-sigmap": "https://www.marchespublics.ci/",
    "cote-ivoire-sigomap": "https://sigomap.gouv.ci/",
    "mali-dgmp": "https://www.dgmp.gouv.ml/",
    "niger-marches": "https://www.marchespublics.ne/",
    "burkina-arcop": "https://www.arcop.bf/",
    "kenya-tenders": "https://tenders.go.ke/",
    "ghana-ghaneps": "https://www.ghaneps.gov.gh/",
    "nigeria-nocopo": "https://nocopo.bpp.gov.ng/",
    "rwanda-umucyo": "https://www.umucyo.gov.rw/",
    "sa-etenders": "https://www.etenders.gov.za/",
    "egypt-etenders": "https://etenders.gov.eg/",
}
_TED_COVERED = ("germany-evergabe", "spain-pcsp", "italy-acquistinrete", "portugal-base")
_TABLE_FIELDS = ("country", "name", "access", "url")
_NO_PAID = ("mercell", "stotles", "marchesonline")


def _quiet_fetchers() -> dict:
    return {sid: (lambda sid=sid: ([], f"{sid} isolated")) for sid in API_SOURCE_IDS}


class TendersRegistryContractTest(unittest.TestCase):
    def test_no_coverage_holes_and_unique_https_ids(self) -> None:
        rows = catalog()
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(rows), 70)
        self.assertEqual(coverage_holes(), [])
        for row in rows:
            self.assertTrue(str(row["url"]).startswith("https://"), row["id"])
            self.assertTrue(row.get("access"), row["id"])
            for field in _TABLE_FIELDS:
                self.assertIn(field, row, row["id"])

    def test_fetchers_and_scrape_net_share_the_same_api_ids(self) -> None:
        self.assertEqual(set(FETCHERS), set(API_SOURCE_IDS))
        self.assertEqual(NATIONAL_SCRAPE_IDS, set(_DUAL_SCRAPE))

    def test_api_first_sources_are_never_scraped(self) -> None:
        by_id = {row["id"]: row for row in catalog()}
        for sid, url in _API_FIRST.items():
            row = by_id[sid]
            self.assertTrue(row["url"].startswith("https://"), sid)
            self.assertIn(url.split("://", 1)[1].split("/")[0].removeprefix("www."), row["url"])
            self.assertIn(row["coverage"], {"api", "opendata", "key"}, sid)
            self.assertIn(row["access"], {"api", "opendata", "api_key"}, sid)
        search_ids = {row["id"] for row in _search_sources(["FR", "GB", "US", "CA", "EU"])}
        self.assertFalse(search_ids & set(_API_FIRST))
        api_hosts = _api_hosts()
        self.assertIn("ted.europa.eu", api_hosts)
        self.assertIn("boamp.fr", {h.removeprefix("www.") for h in api_hosts} | api_hosts)

    def test_dual_national_portals_scrape_in_addition_to_ted(self) -> None:
        for sid, url in _DUAL_SCRAPE.items():
            row = source_by_id(sid)
            self.assertIsNotNone(row, sid)
            assert row is not None
            self.assertEqual(row["coverage"], "search", sid)
            self.assertEqual(row["access"], "scrape", sid)
            self.assertIn(url.split("://", 1)[1].rstrip("/").removeprefix("www."), row["url"])
        for sid in _TED_COVERED:
            row = source_by_id(sid)
            self.assertIsNotNone(row, sid)
            assert row is not None
            self.assertEqual(row["coverage"], "covered", sid)
            self.assertEqual(row["covered_by"], "ted", sid)
            self.assertEqual(row["access"], "covered", sid)

    def test_africa_and_ifi_portals_are_official_scrape(self) -> None:
        hosts = official_hosts()
        for sid, url in _AFRICA_SCRAPE.items():
            row = source_by_id(sid)
            self.assertIsNotNone(row, sid)
            assert row is not None
            self.assertEqual(row["access"], "scrape", sid)
            host = url.split("://", 1)[1].split("/")[0].removeprefix("www.")
            self.assertIn(host, row["url"])
            self.assertTrue(any(host == item or host.endswith(item) or item.endswith(host) for item in hosts), host)

    def test_catalog_url_fixture_matches_live_catalog(self) -> None:
        fixture = json.loads((ROOT / "webui/src/lib/tender-catalog-urls.json").read_text(encoding="utf-8"))
        live = [{"id": row["id"], "url": row["url"]} for row in catalog()]
        self.assertEqual(fixture, live)
        self.assertEqual(len(live), 76)
        for row in live:
            self.assertTrue(str(row["url"]).startswith("https://"), row["id"])

    def test_paid_aggregators_are_not_in_the_catalog(self) -> None:
        blob = " ".join(f"{row['id']} {row['name']} {row['url']}" for row in catalog()).lower()
        for name in _NO_PAID:
            self.assertNotIn(name, blob)

    def test_collect_routes_fetch_search_and_ted_cover_without_dropping_rows(self) -> None:
        countries = ["FR", "BE", "CH", "LU", "SE", "DE", "US"]
        picked = [
            "ted",
            "boamp",
            "place",
            "belgium-eproc",
            "simap",
            "luxembourg-pmp",
            "sweden-uhmynd",
            "germany-evergabe",
            "sam-gov",
        ]
        with patch.dict("navin.tenders.collect.FETCHERS", _quiet_fetchers(), clear=True):
            result = collect(
                countries=countries,
                crafts=["AI"],
                use_tools=False,
                source_ids=picked,
            )
        by_id = {row["source_id"]: row for row in result["reports"]}
        self.assertEqual(set(by_id), {row["id"] for row in catalog()})
        self.assertEqual(by_id["ted"]["kind"], "fetch")
        self.assertEqual(by_id["boamp"]["kind"], "fetch")
        self.assertEqual(by_id["place"]["kind"], "search")
        self.assertEqual(by_id["belgium-eproc"]["kind"], "search")
        self.assertEqual(by_id["simap"]["kind"], "search")
        self.assertEqual(by_id["luxembourg-pmp"]["kind"], "search")
        self.assertEqual(by_id["sweden-uhmynd"]["kind"], "search")
        self.assertEqual(by_id["germany-evergabe"]["kind"], "covered")
        self.assertEqual(by_id["sam-gov"]["kind"], "key")
        self.assertEqual(by_id["world-bank"]["kind"], "catalog")
        self.assertIn("not selected", by_id["world-bank"]["detail"])
        self.assertIn("outside the current profile", by_id["find-a-tender"]["detail"])

    def test_scrape_net_for_france_includes_place_not_ted(self) -> None:
        ids = {row["id"] for row in _search_sources(["FR"])}
        self.assertIn("place", ids)
        self.assertNotIn("ted", ids)
        self.assertNotIn("boamp", ids)
        self.assertNotIn("germany-evergabe", ids)

    def test_desk_snapshot_exposes_the_table_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
        rows = snap["catalog"]
        self.assertGreaterEqual(len(rows), 70)
        ted = next(row for row in rows if row["id"] == "ted")
        place = next(row for row in rows if row["id"] == "place")
        self.assertEqual(ted["country"], "EU")
        self.assertEqual(ted["access"], "api")
        self.assertTrue(ted["url"].startswith("https://"))
        self.assertEqual(place["access"], "scrape")
        zones = {group["zone"] for group in snap["catalog_by_zone"]}
        self.assertTrue({"europe", "africa", "americas", "international"} <= zones)
        europe = next(group for group in snap["catalog_by_zone"] if group["zone"] == "europe")
        self.assertGreaterEqual(len(europe["sources"]), 30)
        africa = next(group for group in snap["catalog_by_zone"] if group["zone"] == "africa")
        self.assertGreaterEqual(len(africa["sources"]), 15)
