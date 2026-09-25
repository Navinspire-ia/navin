# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Probe every official catalog source, one id at a time.

A 404 or a missing DNS name is a catalog bug, except a host that answers
a fake 404 from some runners. A WAF, a login wall, a timeout abroad, or
an HTTP 429 is recorded, not treated as an invented notice.
"""

from __future__ import annotations

import os
import unittest

from navin.tenders.fetchers import FETCHERS
from navin.tenders.probe import probe_catalog
from navin.tenders.sources import API_SOURCE_IDS, catalog

# Official hosts that time out, fail TLS, or challenge bots from some networks.
# The catalog URL stays. A 404 here is still a fail.
_TIMEOUT_ABROAD = frozenset({
    "egypt-etenders",
    "monaqasat",
    "czech-vvz",
    "tuneps",
    "niger-marches",
    "nigeria-nocopo",
    # GitHub runners sometimes time out on this host. A 404 still fails.
    "cameroon-armp",
    # GitHub runners sometimes time out on this host. A 404 still fails.
    "etimad",
})

# GitHub-hosted runners sometimes get HTTP 404 from this host and then reach
# the same URL on the next probe. The catalog URL stays. A DNS miss still fails.
_WAF_404 = frozenset({
    "ireland-etenders",
})


class TendersCatalogOneByOneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = probe_catalog(workers=10)
        cls.by_id = {row["id"]: row for row in cls.rows}

    def test_probe_covers_every_catalog_id_once(self) -> None:
        expected = [row["id"] for row in catalog()]
        got = [row["id"] for row in self.rows]
        self.assertEqual(sorted(got), sorted(expected))
        self.assertEqual(len(got), len(set(got)))

    def test_each_source_one_by_one(self) -> None:
        offline = (
            self.by_id["ted"].get("api_ok") is not True
            and int(self.by_id["ted"].get("portal_status") or 0) == 0
            and int(self.by_id["world-bank"].get("portal_status") or 0) == 0
        )
        if offline:
            self.skipTest("network blocked; empty pipeline is honest")

        for source in catalog():
            sid = source["id"]
            with self.subTest(sid):
                row = self.by_id[sid]
                status = int(row.get("portal_status") or 0)
                err = str(row.get("portal_error") or "")
                dns_miss = "Name or service not known" in err or "nodename nor servname" in err
                api_err = str(row.get("api_error") or "")
                # 429 means the host answered and asked CI to slow down.
                rate_limited = (
                    status == 429
                    or int(row.get("api_status") or 0) == 429
                    or "429" in api_err
                )
                if sid not in _WAF_404 or status != 404:
                    self.assertNotIn(status, {404, 410}, row)
                self.assertFalse(dns_miss, row)
                if sid in API_SOURCE_IDS and sid != "sam-gov" and not rate_limited:
                    self.assertTrue(row.get("api_ok"), row)
                if sid == "sam-gov":
                    self.assertTrue(row.get("portal_ok"), row)
                    self.assertIn("key", str(row.get("api_error") or "").lower())
                if sid not in _TIMEOUT_ABROAD and sid not in _WAF_404:
                    self.assertTrue(
                        row.get("reachable")
                        or row.get("portal_blocked")
                        or row.get("api_ok")
                        or rate_limited,
                        row,
                    )

    def test_each_wired_fetcher_returns_notices_or_honest_error(self) -> None:
        if os.environ.get("NAVIN_TENDERS_SKIP_LIVE") == "1":
            self.skipTest("live fetchers skipped")
        offline = (
            self.by_id["ted"].get("api_ok") is not True
            and int(self.by_id["ted"].get("portal_status") or 0) == 0
        )
        if offline:
            self.skipTest("network blocked; empty pipeline is honest")

        for sid, fetcher in FETCHERS.items():
            if sid == "sam-gov" and not (
                os.environ.get("SAM_API_KEY") or os.environ.get("SAM_GOV_API_KEY")
            ):
                continue
            with self.subTest(sid):
                rows, detail = fetcher()
                self.assertIsInstance(rows, list, detail)
                self.assertTrue(detail, sid)
                for notice in rows:
                    self.assertTrue(str(notice.get("id") or "").startswith("tn-"), notice)
                    self.assertEqual(notice.get("source_id"), sid)
                    self.assertTrue(str(notice.get("title") or "").strip(), notice)
                    self.assertNotEqual(notice.get("title"), "Untitled notice")
