# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live reachability of every official tender source.

P0 APIs (TED, World Bank) must work when the network is up.
HTML portals are catalog-only: 403/405 WAF and timeouts abroad are recorded,
not treated as invented notices. A 404 on a catalog URL we just repaired is a fail.
"""

from __future__ import annotations

import unittest

from navin.tenders.probe import probe_catalog
from navin.tenders.sources import catalog

# Portals that often time out, challenge bots, or need a key. Official URLs stay.
_FLAKY_ABROAD = {
    "egypt-etenders",
    "capt-kuwait",
    "afdb",
    "iadb",
    "ungm",
    "sam-gov",
}


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


class TendersSourcesLiveProbeTest(unittest.TestCase):
    def test_probe_every_catalog_source(self) -> None:
        rows = probe_catalog()
        self.assertEqual(len(rows), len(catalog()))
        by_id = {row["id"]: row for row in rows}
        ted = by_id["ted"]
        wb = by_id["world-bank"]
        offline = (
            ted.get("api_ok") is not True
            and int(ted.get("portal_status") or 0) == 0
            and int(wb.get("portal_status") or 0) == 0
        )
        if offline:
            self.skipTest("network blocked; empty pipeline is honest")

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
            and row["id"] not in _FLAKY_ABROAD
        ]
        self.assertEqual(dead_404, [], dead_404)

        reachable = [row for row in rows if row.get("reachable")]
        self.assertGreaterEqual(len(reachable), 30, [row["id"] for row in rows if not row.get("reachable")])


if __name__ == "__main__":
    unittest.main()
