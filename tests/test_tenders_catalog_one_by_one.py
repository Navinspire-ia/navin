# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Probe contract tests and opt-in live checks for official tender sources.

The regular test gate exercises probe errors with mocked transport. Set
``NAVIN_TENDERS_LIVE=1`` for current portal/API responses; unhandled HTTP,
DNS, and timeout failures stay visible rather than becoming host exceptions.
"""

from __future__ import annotations

import os
import unittest
import urllib.error
from unittest import mock

from navin.tenders.fetchers import FETCHERS
from navin.tenders.probe import probe_catalog, probe_source
from navin.tenders.sources import API_SOURCE_IDS, catalog


class TenderProbeNetworkContractTest(unittest.TestCase):
    def test_timeout_does_not_promote_portal_reachability(self) -> None:
        source = {
            "id": "test-portal",
            "name": "Test portal",
            "url": "https://portal.example/",
        }
        with mock.patch(
            "navin.tenders.probe.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timed out"),
        ):
            row = probe_source(source)

        self.assertEqual(row["portal_status"], 0)
        self.assertIn("timed out", row["portal_error"])
        self.assertFalse(row["portal_ok"])
        self.assertFalse(row["portal_blocked"])
        self.assertFalse(row["reachable"])

    def test_http_404_remains_a_real_probe_failure(self) -> None:
        source = {
            "id": "test-portal",
            "name": "Test portal",
            "url": "https://portal.example/",
        }
        error = urllib.error.HTTPError(source["url"], 404, "Not Found", None, None)
        with mock.patch(
            "navin.tenders.probe.urllib.request.urlopen",
            side_effect=error,
        ):
            row = probe_source(source)

        self.assertEqual(row["portal_status"], 404)
        self.assertEqual(row["portal_error"], "Not Found")
        self.assertFalse(row["portal_ok"])
        self.assertFalse(row["reachable"])


@unittest.skipUnless(
    os.environ.get("NAVIN_TENDERS_LIVE") == "1",
    "set NAVIN_TENDERS_LIVE=1 to access official tender portals",
)
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
                self.assertNotIn(status, {404, 410}, row)
                self.assertFalse(dns_miss, row)
                if sid in API_SOURCE_IDS and sid != "sam-gov" and not rate_limited:
                    self.assertTrue(row.get("api_ok"), row)
                if sid == "sam-gov":
                    self.assertTrue(row.get("portal_ok"), row)
                    self.assertIn("key", str(row.get("api_error") or "").lower())
                self.assertTrue(
                    row.get("reachable")
                    or row.get("portal_blocked")
                    or row.get("api_ok")
                    or rate_limited,
                    row,
                )

    def test_each_wired_fetcher_returns_notices_or_honest_error(self) -> None:
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
