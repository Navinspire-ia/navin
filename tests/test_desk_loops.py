# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A desk loop stays asleep, its module unloaded, until the user starts it."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from navin.desk_loops import LOOP_DESKS, campaigns_have_work, desk_loop_armed


class DeskLoopArmedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, desk: str, body: str) -> None:
        (self.root / desk).mkdir(parents=True, exist_ok=True)
        (self.root / desk / "loop.json").write_text(body, encoding="utf-8")

    def test_a_desk_never_started_is_asleep(self) -> None:
        for desk in LOOP_DESKS:
            with self.subTest(desk=desk):
                self.assertFalse(desk_loop_armed(desk, self.root))

    def test_a_paused_loop_is_asleep(self) -> None:
        self._write("career", json.dumps({"enabled": False, "phase": "paused"}))
        self.assertFalse(desk_loop_armed("career", self.root))

    def test_a_started_loop_wakes(self) -> None:
        self._write("tenders", json.dumps({"enabled": True, "phase": "idle"}))
        self.assertTrue(desk_loop_armed("tenders", self.root))

    def test_a_broken_state_file_keeps_it_asleep(self) -> None:
        self._write("trading", "{not json")
        self.assertFalse(desk_loop_armed("trading", self.root))

    def test_the_check_does_not_load_the_desk(self) -> None:
        # A fresh interpreter: the rest of the suite imports the desks anyway.
        import subprocess
        import sys

        self._write("leads", json.dumps({"enabled": False}))
        probe = (
            "import sys; from pathlib import Path; from navin.desk_loops import desk_loop_armed; "
            f"desk_loop_armed('leads', Path({str(self.root)!r})); "
            "print('navin.leads.loop' in sys.modules)"
        )
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "False")


class CampaignsHaveWorkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "campaigns").mkdir()
        self.db = self.root / "campaigns" / "campaigns.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _campaigns(self, *statuses: str) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE campaigns (id INTEGER PRIMARY KEY, status TEXT)")
            conn.executemany("INSERT INTO campaigns (status) VALUES (?)", [(s,) for s in statuses])

    def test_no_database_means_no_work(self) -> None:
        self.assertFalse(campaigns_have_work(self.root))

    def test_drafts_only_mean_no_work(self) -> None:
        self._campaigns("draft", "draft")
        self.assertFalse(campaigns_have_work(self.root))

    def test_a_scheduled_or_sent_campaign_is_work(self) -> None:
        for status in ("scheduled", "sending", "sent"):
            with self.subTest(status=status):
                self.db.unlink(missing_ok=True)
                self._campaigns("draft", status)
                self.assertTrue(campaigns_have_work(self.root))


if __name__ == "__main__":
    unittest.main()
