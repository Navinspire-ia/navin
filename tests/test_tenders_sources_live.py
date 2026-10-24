# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tender catalog shape and opt-in live reachability checks.

Set ``NAVIN_TENDERS_LIVE=1`` to contact official procurement portals. Live
responses, including HTTP and network failures, are reported without per-host
timeout or status exceptions.
"""

from __future__ import annotations

import os
import unittest

from navin.tenders.probe import probe_catalog
from navin.tenders.sources import catalog


class TendersCatalogShapeTest(unittest.TestCase):
    def test_every_source_has_https_url_and_unique_id(self) -> None:
        rows = catalog()
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(rows), 70)
        for row in rows:
            url = str(row.get("url") or "")
            self.assertTrue(url.startswith("https://"), row["id"])
            self.assertIn(row.get("ingest"), {"api", "rss", "html", "search", "opendata"})
            self.assertIn(row.get("coverage"), {"api", "opendata", "key", "covered", "search"})
            self.assertIn(row.get("priority"), {"P0", "P1", "P2", "P3"})

    def test_repaired_official_urls(self) -> None:
        by_id = {row["id"]: row for row in catalog()}
        self.assertIn(
            "procurement-search",
            by_id["world-bank"]["url"],
        )
        self.assertIn(
            "opportunities",
            by_id["world-bank-business"]["url"],
        )
        self.assertIn("comprasmx.buengobierno.gob.mx", by_id["mexico-compranet"]["url"])
        self.assertIn("armp.sn", by_id["senegal-armds"]["url"])
        self.assertNotIn("hacienda.gob.mx", by_id["mexico-compranet"]["url"])
        self.assertIn("iub.gov.lv", by_id["latvia-eis"]["url"])
        self.assertIn("viesiejipirkimai.lt", by_id["lithuania-cvpp"]["url"])
        self.assertIn("mf.gov.dz", by_id["algeria-marches"]["url"])
        self.assertNotIn("marchespublics.gov.dz", by_id["algeria-marches"]["url"])
        self.assertNotIn("cvpp.eviesiejipirkimai.lt", by_id["lithuania-cvpp"]["url"])


@unittest.skipUnless(
    os.environ.get("NAVIN_TENDERS_LIVE") == "1",
    "set NAVIN_TENDERS_LIVE=1 to access official tender portals",
)
class TendersSourcesLiveProbeTest(unittest.TestCase):
    def test_probe_every_catalog_source(self) -> None:
        rows = probe_catalog()
        self.assertEqual(len(rows), len(catalog()))
        by_id = {row["id"]: row for row in rows}
        ted = by_id["ted"]
        wb = by_id["world-bank"]
        self.assertTrue(ted.get("api_ok"), ted)
        self.assertTrue(wb.get("api_ok") or wb.get("portal_ok"), wb)

        for sid in (
            "world-bank",
            "world-bank-business",
            "mexico-compranet",
            "senegal-armds",
            "latvia-eis",
            "lithuania-cvpp",
            "algeria-marches",
        ):
            status = int(by_id[sid].get("portal_status") or 0)
            self.assertNotEqual(status, 404, by_id[sid])

        dead_404 = [
            row
            for row in rows
            if int(row.get("portal_status") or 0) == 404
            and row.get("api_ok") is not True
        ]
        self.assertEqual(dead_404, [], dead_404)

        reachable = [row for row in rows if row.get("reachable")]
        self.assertGreaterEqual(len(reachable), 30, [row["id"] for row in rows if not row.get("reachable")])


if __name__ == "__main__":
    unittest.main()
