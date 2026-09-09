# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Built-in Montage platform render profiles."""

from __future__ import annotations

import unittest

from navin.montage.profiles import (
    RENDER_PROFILES,
    get_profile,
    list_profiles,
    render_profiles_help,
    resolve_package_profiles,
)


class MontageProfilesTest(unittest.TestCase):
    def test_canonical_platform_table(self) -> None:
        by_id = {p.id: p for p in RENDER_PROFILES}
        expected = {
            "youtube_landscape": (1920, 1080, "16:9"),
            "youtube_4k": (3840, 2160, "16:9"),
            "youtube_shorts": (1080, 1920, "9:16"),
            "instagram_reels": (1080, 1920, "9:16"),
            "instagram_feed": (1080, 1080, "1:1"),
            "tiktok": (1080, 1920, "9:16"),
            "linkedin": (1920, 1080, "16:9"),
            "cinematic": (2560, 1080, "21:9"),
        }
        self.assertEqual(set(by_id), set(expected))
        for pid, (w, h, ar) in expected.items():
            self.assertEqual(by_id[pid].width, w)
            self.assertEqual(by_id[pid].height, h)
            self.assertEqual(by_id[pid].aspect_ratio, ar)

    def test_default_package_excludes_4k_and_cinematic(self) -> None:
        defaults = resolve_package_profiles("default")
        ids = {p.id for p in defaults}
        self.assertIn("youtube_landscape", ids)
        self.assertIn("youtube_shorts", ids)
        self.assertIn("instagram_feed", ids)
        self.assertIn("tiktok", ids)
        self.assertIn("linkedin", ids)
        self.assertNotIn("youtube_4k", ids)
        self.assertNotIn("cinematic", ids)

    def test_all_includes_opt_in_profiles(self) -> None:
        ids = {p.id for p in resolve_package_profiles("all")}
        self.assertIn("youtube_4k", ids)
        self.assertIn("cinematic", ids)

    def test_comma_list(self) -> None:
        ids = [p.id for p in resolve_package_profiles("tiktok,cinematic")]
        self.assertEqual(ids, ["tiktok", "cinematic"])

    def test_get_and_help(self) -> None:
        self.assertIsNotNone(get_profile("YouTube_Shorts"))
        self.assertEqual(len(list_profiles()), len(RENDER_PROFILES))
        help_text = render_profiles_help()
        self.assertIn("youtube_landscape", help_text)
        self.assertIn("2560x1080", help_text)


if __name__ == "__main__":
    unittest.main()
