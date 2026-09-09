# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Career offer retention: archive at 45 days, delete at 60."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.career.store import CareerStore, default_profile
from navin.webui.career_api import handle_career_action


def _job(oid: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": oid,
        "source": "remotive",
        "title": oid,
        "url": f"https://remotive.com/remote-jobs/{oid}",
        "stage": "discovered",
        "track": "freelance",
    }
    row.update(extra)
    return row


class CareerRetentionTest(unittest.TestCase):
    def test_defaults_are_45_and_60_days(self) -> None:
        from navin.career.retention import ARCHIVE_AFTER_DAYS, DELETE_AFTER_DAYS, retention_days

        self.assertEqual(ARCHIVE_AFTER_DAYS, 45)
        self.assertEqual(DELETE_AFTER_DAYS, 60)
        self.assertEqual(default_profile()["archive_after_days"], 45)
        self.assertEqual(default_profile()["delete_after_days"], 60)
        self.assertEqual(retention_days({}), (45, 60))
        self.assertEqual(retention_days({"archive_after_days": 90, "delete_after_days": 30}), (90, 90))

    def test_apply_retention_archives_then_deletes(self) -> None:
        from navin.career.retention import apply_retention

        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"wizard_complete": True, "titles": ["Data Engineer"]})
            fresh = _job("job-fresh", created_at=now - 10 * 86400)
            old = _job("job-old", created_at=now - 50 * 86400)
            stale = _job("job-stale", created_at=now - 70 * 86400)
            store.save_opportunities([fresh, old, stale])
            result = apply_retention(store, now=now)
            ids = {row["id"] for row in store.load_opportunities()}
            self.assertEqual(result["archived"], 1)
            self.assertEqual(result["deleted"], 1)
            self.assertIn("job-fresh", ids)
            self.assertIn("job-old", ids)
            self.assertNotIn("job-stale", ids)
            self.assertTrue(store.get_opportunity("job-old").get("archived"))
            self.assertFalse(store.get_opportunity("job-fresh").get("archived"))

    def test_desk_actions_favorite_archive_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"wizard_complete": True, "titles": ["Data Engineer"]})
            job = _job("job-act")
            store.save_opportunities([job])
            with patch("navin.webui.career_api._store", return_value=store):
                starred = handle_career_action("favorite", {"id": job["id"]})
                self.assertTrue(next(row for row in starred["opportunities"] if row["id"] == job["id"])["favorite"])
                parked = handle_career_action("archive", {"id": job["id"]})
                parked_row = next(row for row in parked["opportunities"] if row["id"] == job["id"])
                self.assertTrue(parked_row["archived"])
                self.assertEqual(parked["retention"]["archived"], 1)
                self.assertEqual(parked["retention"]["favorites"], 0)
                restored = handle_career_action("unarchive", {"id": job["id"]})
                restored_row = next(row for row in restored["opportunities"] if row["id"] == job["id"])
                self.assertFalse(restored_row["archived"])
                self.assertTrue(restored_row["favorite"])
                gone = handle_career_action("delete", {"id": job["id"]})
            self.assertEqual(gone["deleted"]["id"], job["id"])
            self.assertEqual(gone["opportunities"], [])

    def test_watch_skips_archived_offers(self) -> None:
        from navin.career.watch import pending_alerts

        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"wizard_complete": True, "titles": ["Data Engineer"]})
            store.save_opportunities(
                [
                    _job("job-live", match_score=90, stage="matched"),
                    _job("job-park", match_score=92, stage="matched", archived=True),
                ]
            )
            events = pending_alerts(store)
            self.assertEqual([row["id"] for row in events], ["job-live"])


if __name__ == "__main__":
    unittest.main()
