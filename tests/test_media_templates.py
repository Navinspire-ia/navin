# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Marketing / Montage AWS media templates."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.utils.media_templates import (
    _FAMILIES,
    _TABS,
    MAX_MEDIA_TEMPLATES_PER_TURN,
    get_media_template,
    list_media_templates,
    load_bundled_catalog,
    materialize_media_template,
    normalize_media_template_mention,
    normalize_media_template_mentions,
    reset_media_template_cache,
)

_TAGS = (
    "portraits",
    "expressions",
    "photoshoots",
    "food",
    "backgrounds",
    "environments",
    "nature",
    "atmosphere",
    "startups",
    "aiagents",
    "fashion",
    "beauty",
)

_SAMPLE_ID = "stock-photos-portraits-916"


class MediaTemplateCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_media_template_cache()

    def tearDown(self) -> None:
        reset_media_template_cache()

    def test_bundled_catalog_covers_every_family_tag_and_tab(self) -> None:
        items = load_bundled_catalog()
        # Marketing: 9 families x 12 tags x (3 shots + 2 artwork + 2 graphics
        # + 2 videos). Montage: its own set, 6 families x 12 tags x (2 frames
        # + 3 cuts), no shared visuals.
        self.assertEqual(len(items), 9 * 12 * 9 + 6 * 12 * 5)
        ids = {item["id"] for item in items}
        self.assertIn(_SAMPLE_ID, ids)
        self.assertIn("camera-video-atmosphere-219", ids)
        kinds = {item["kind"] for item in items}
        self.assertEqual(kinds, {"image", "video"})
        formats = {item["format"] for item in items}
        self.assertTrue({"16:9", "9:16", "1:1", "21:9", "4:5"} <= formats)

        for family in _FAMILIES:
            for tag in _TAGS:
                photos = [
                    item
                    for item in items
                    if item["family"] == family
                    and item["kind"] == "image"
                    and item["tab"] == "photos"
                    and tag in item["tags"]
                ]
                videos = [
                    item
                    for item in items
                    if item["family"] == family
                    and item["kind"] == "video"
                    and tag in item["tags"]
                ]
                self.assertTrue(photos, f"missing photo {family}/{tag}")
                self.assertTrue(videos, f"missing video {family}/{tag}")
                for tab in _TABS:
                    found = [
                        item
                        for item in items
                        if item["family"] == family
                        and item["kind"] == "image"
                        and item["tab"] == tab
                        and tag in item["tags"]
                    ]
                    self.assertTrue(found, f"missing {tab} {family}/{tag}")

    def test_list_includes_both_studios(self) -> None:
        with patch(
            "navin.utils.media_templates.fetch_remote_catalog",
            return_value=None,
        ):
            marketing = list_media_templates("marketing")
            montage = list_media_templates("montage")
        self.assertEqual(marketing["source"], "bundled")
        # Marketing owns the full visual DNA (9 families); Montage is the edit
        # desk with its OWN video-heavy set (6 families, frames + cuts).
        self.assertEqual(len(marketing["items"]), 9 * 12 * 9)
        self.assertEqual(len(montage["items"]), 6 * 12 * 5)
        montage_families = {item["family"] for item in montage["items"]}
        self.assertEqual(
            montage_families,
            {"stock", "style", "location", "color", "effects", "camera"},
        )
        marketing_ids = {item["id"] for item in marketing["items"]}
        montage_ids = {item["id"] for item in montage["items"]}
        self.assertIn(_SAMPLE_ID, marketing_ids)
        self.assertIn("style-cut-portraits-916", montage_ids)
        # The two studios never share an item or a visual.
        self.assertFalse(marketing_ids & montage_ids)
        marketing_previews = {item["preview_url"] for item in marketing["items"]}
        montage_previews = {item["preview_url"] for item in montage["items"]}
        self.assertFalse(marketing_previews & montage_previews)
        montage_videos = [i for i in montage["items"] if i["kind"] == "video"]
        self.assertGreater(len(montage_videos), len(montage["items"]) // 2)

    def test_normalize_known_and_unknown(self) -> None:
        with patch(
            "navin.utils.media_templates.fetch_remote_catalog",
            return_value=None,
        ):
            mention = normalize_media_template_mention({"id": _SAMPLE_ID})
            missing = normalize_media_template_mention({"id": "does-not-exist"})
            bad = normalize_media_template_mention(_SAMPLE_ID)
        self.assertIsNotNone(mention)
        assert mention is not None
        self.assertEqual(mention["id"], _SAMPLE_ID)
        self.assertEqual(mention["family"], "stock")
        self.assertIn("portraits", mention["tags"])
        self.assertTrue(
            str(mention["url"]).endswith(f"media-templates/v1/stock/{_SAMPLE_ID}.jpg")
        )
        self.assertIsNone(missing)
        self.assertIsNone(bad)

    def test_get_media_template_rejects_bad_ids(self) -> None:
        self.assertIsNone(get_media_template("../etc/passwd"))
        self.assertIsNone(get_media_template(""))

    def test_normalize_mentions_multi_dedupe_and_cap(self) -> None:
        with patch(
            "navin.utils.media_templates.fetch_remote_catalog",
            return_value=None,
        ):
            items = load_bundled_catalog()
            ids = [item["id"] for item in items[:10]]
            # Single mapping payloads still work.
            single = normalize_media_template_mentions({"id": _SAMPLE_ID})
            # Duplicates collapse, unknown ids are dropped, cap is 6.
            payload = (
                [{"id": _SAMPLE_ID}, {"id": _SAMPLE_ID}, {"id": "nope"}]
                + [{"id": ident} for ident in ids]
            )
            many = normalize_media_template_mentions(payload)
            empty = normalize_media_template_mentions("not-a-list")
        self.assertEqual([m["id"] for m in single], [_SAMPLE_ID])
        self.assertEqual(len(many), MAX_MEDIA_TEMPLATES_PER_TURN)
        self.assertEqual(len({m["id"] for m in many}), MAX_MEDIA_TEMPLATES_PER_TURN)
        self.assertEqual(many[0]["id"], _SAMPLE_ID)
        self.assertEqual(empty, [])


class MediaTemplateMaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_media_template_cache()

    def tearDown(self) -> None:
        reset_media_template_cache()

    def test_materialize_downloads_into_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            mention = {"id": _SAMPLE_ID}

            def fake_download(url: str, dest: Path, timeout_s: float = 45.0) -> bool:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"fake-jpg")
                return True

            with patch(
                "navin.utils.media_templates.fetch_remote_catalog",
                return_value=None,
            ), patch(
                "navin.utils.media_templates._download",
                side_effect=fake_download,
            ):
                result = materialize_media_template(mention, workspace)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["source"], "s3")
            local = workspace / result["local_path"]
            self.assertTrue(local.is_file())
            self.assertEqual(local.read_bytes(), b"fake-jpg")
            self.assertIn(f"media-templates/{_SAMPLE_ID}.jpg", result["local_path"])

    def test_materialize_falls_back_to_preview_still(self) -> None:
        """403 on the S3 master must yield the preview still, never a fake."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            def fake_download(url: str, dest: Path, timeout_s: float = 45.0) -> bool:
                if "unsplash" not in url:
                    return False  # S3 master missing (403)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"preview-jpg")
                return True

            with patch(
                "navin.utils.media_templates.fetch_remote_catalog",
                return_value=None,
            ), patch(
                "navin.utils.media_templates._download",
                side_effect=fake_download,
            ):
                items = load_bundled_catalog()
                video_id = next(i["id"] for i in items if i["kind"] == "video")
                result = materialize_media_template({"id": video_id}, workspace)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["source"], "preview")
            # The still is a JPEG and must never wear the master's .mp4 name.
            self.assertTrue(result["local_path"].endswith(f"{video_id}-preview.jpg"))
            local = workspace / result["local_path"]
            self.assertEqual(local.read_bytes(), b"preview-jpg")

    def test_materialize_reports_missing_aws_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with patch(
                "navin.utils.media_templates.fetch_remote_catalog",
                return_value=None,
            ), patch(
                "navin.utils.media_templates._download",
                return_value=False,
            ):
                result = materialize_media_template({"id": _SAMPLE_ID}, workspace)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["source"], "missing")
            self.assertIsNone(result["local_path"])
            self.assertIn("AWS file is not uploaded yet", str(result.get("error") or ""))

    def test_context_provider_runs_off_the_event_loop(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from navin.utils.media_templates import media_template_context_provider

        items = load_bundled_catalog()
        second_id = next(
            item["id"] for item in items if item["id"] != _SAMPLE_ID
        )
        ctx = SimpleNamespace(
            metadata={
                "media_templates": [{"id": _SAMPLE_ID}, {"id": second_id}],
                "product_module": "marketing",
            },
            workspace=None,
        )
        with patch(
            "navin.utils.media_templates.fetch_remote_catalog",
            return_value=None,
        ):
            block = asyncio.run(media_template_context_provider(ctx))
        self.assertIsNotNone(block)
        assert block is not None
        self.assertIn("MEDIA REFERENCES (AWS) - 2 attached", block.content)
        self.assertIn("COMBINE ALL references", block.content)
        self.assertIn(_SAMPLE_ID, block.content)
        self.assertIn("STUDIO = MARKETING", block.content)

    def test_context_provider_orders_stop_when_material_missing(self) -> None:
        """Missing AWS object (403/404) must inject a hard anti-placeholder order."""
        import asyncio
        from types import SimpleNamespace

        from navin.utils.media_templates import media_template_context_provider

        with tempfile.TemporaryDirectory() as tmp:
            ctx = SimpleNamespace(
                metadata={
                    "media_templates": [{"id": _SAMPLE_ID}],
                    "product_module": "montage",
                },
                workspace=tmp,
            )
            with patch(
                "navin.utils.media_templates.fetch_remote_catalog",
                return_value=None,
            ), patch(
                "navin.utils.media_templates._download",
                return_value=False,
            ):
                block = asyncio.run(media_template_context_provider(ctx))
        self.assertIsNotNone(block)
        assert block is not None
        self.assertIn("download: missing", block.content)
        self.assertIn("STOP on visuals", block.content)
        self.assertIn(
            "NEVER generate placeholder or substitute media", block.content
        )
        self.assertIn("never assemble, package or deliver", block.content)


if __name__ == "__main__":
    unittest.main()
