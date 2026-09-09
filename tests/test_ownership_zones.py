# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Ownership zone matching for shared org projects."""

from __future__ import annotations

import unittest

from navin.collab.ownership import (
    OwnershipZone,
    files_outside_actor_zones,
    owners_for_path,
    path_matches_glob,
    suggest_branch_name,
)


class OwnershipGlobTest(unittest.TestCase):
    def test_star_star_prefix(self):
        self.assertTrue(path_matches_glob("webui/src/App.tsx", "webui/**"))
        self.assertTrue(path_matches_glob("webui/src/a/b.ts", "webui/**"))
        self.assertFalse(path_matches_glob("navin/loop.py", "webui/**"))

    def test_single_star_segment(self):
        self.assertTrue(path_matches_glob("navin/agent/loop.py", "navin/agent/*"))
        self.assertFalse(path_matches_glob("navin/agent/x/y.py", "navin/agent/*"))

    def test_unowned_allowed(self):
        zones = [
            OwnershipZone(user_id="alice", path_glob="webui/**", label="Frontend"),
        ]
        self.assertEqual(
            files_outside_actor_zones(zones, "bob", ["docs/readme.md"]),
            [],
        )

    def test_foreign_zone_blocked(self):
        zones = [
            OwnershipZone(user_id="alice", path_glob="webui/**", label="Frontend"),
            OwnershipZone(user_id="bob", path_glob="navin/**", label="Backend"),
        ]
        self.assertEqual(
            files_outside_actor_zones(zones, "bob", ["webui/src/App.tsx", "navin/x.py"]),
            ["webui/src/App.tsx"],
        )

    def test_owners_for_path(self):
        zones = [
            OwnershipZone(user_id="alice", path_glob="site/**"),
            OwnershipZone(user_id="carol", path_glob="site/supabase/**"),
        ]
        owners = owners_for_path(zones, "site/supabase/x.sql")
        self.assertEqual(set(owners), {"alice", "carol"})

    def test_branch_name(self):
        self.assertEqual(
            suggest_branch_name("task-ab12", "Login Form"),
            "feat/task-ab12-login-form",
        )


if __name__ == "__main__":
    unittest.main()
