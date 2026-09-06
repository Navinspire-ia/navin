"""The Visual Asset Policy is code, not a wish list."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.documents import ppt_assets, ppt_design


class TypeComesBeforeSearch(unittest.TestCase):
    def test_numbers_become_a_chart_not_a_stock_photo(self) -> None:
        self.assertEqual(ppt_assets.choose_asset_type("numbers"), "chart")

    def test_a_process_stays_a_native_diagram(self) -> None:
        self.assertEqual(ppt_assets.choose_asset_type("steps"), "diagram")
        self.assertEqual(
            ppt_assets.choose_asset_type(
                visual={"type": "architecture"}
            ),
            "diagram",
        )

    def test_a_user_file_beats_every_library(self) -> None:
        self.assertEqual(
            ppt_assets.choose_asset_type("abstract", user_asset="logo.png"),
            "user",
        )

    def test_geography_is_a_map_not_a_google_capture(self) -> None:
        self.assertEqual(ppt_assets.choose_asset_type("geography"), "map")


class SearchTheSceneNotTheTitle(unittest.TestCase):
    def test_an_ai_strategy_slide_does_not_search_its_title(self) -> None:
        query = ppt_assets.visual_intent("AI Transformation Strategy", kind="abstract")
        self.assertNotIn("transformation strategy", query.lower())
        self.assertIn("enterprise", query)
        self.assertIn("digital", query)

    def test_the_theme_colours_the_query(self) -> None:
        query = ppt_assets.visual_intent("Office", theme="premium_black")
        self.assertIn("dark", query)


class BlockedSources(unittest.TestCase):
    def test_google_images_is_refused(self) -> None:
        self.assertTrue(
            ppt_assets.blocked_source(
                "https://www.google.com/imgres?imgurl=https://cdn.example/x.jpg"
            )
        )

    def test_google_maps_is_refused(self) -> None:
        self.assertTrue(
            ppt_assets.blocked_source("https://www.google.com/maps/@48.8,2.3")
        )

    def test_a_paid_preview_is_refused(self) -> None:
        self.assertTrue(
            ppt_assets.blocked_source("https://www.shutterstock.com/image-photo/office")
        )

    def test_unsplash_is_allowed(self) -> None:
        self.assertFalse(
            ppt_assets.blocked_source("https://images.unsplash.com/photo-123")
        )
        self.assertTrue(
            ppt_assets.allowed_photo_host("https://images.unsplash.com/photo-123")
        )

    def test_check_url_cli_exits_nonzero_on_google(self) -> None:
        self.assertEqual(
            ppt_assets.main(
                ["check-url", "https://images.google.com/imghp"]
            ),
            1,
        )


class FitNeverStretches(unittest.TestCase):
    def test_photos_cover_and_screenshots_contain(self) -> None:
        self.assertEqual(ppt_assets.fit_for("photo"), "cover")
        self.assertEqual(ppt_assets.fit_for("screenshot"), "contain")
        self.assertEqual(ppt_assets.fit_for("logo"), "contain")
        self.assertEqual(ppt_assets.fit_for("illustration"), "contain")


class PhotoScore(unittest.TestCase):
    def test_a_tiny_watermarked_preview_scores_zero(self) -> None:
        self.assertEqual(
            ppt_assets.score_photo(
                {
                    "url": "https://cdn.example/thumb-watermark.jpg",
                    "width": 200,
                    "height": 120,
                    "provider": "pixabay",
                },
                "office",
            ),
            0,
        )

    def test_a_large_unsplash_frame_clears_the_threshold(self) -> None:
        score = ppt_assets.score_photo(
            {
                "url": "https://images.unsplash.com/photo-abc",
                "width": 4000,
                "height": 2667,
                "provider": "unsplash",
                "credit": "Jane Doe",
                "page_url": "https://unsplash.com/photos/abc",
            },
            "modern enterprise team digital technology",
        )
        self.assertGreaterEqual(score, 80)


class SvgSafety(unittest.TestCase):
    def test_a_script_tag_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ppt_assets.sanitize_svg('<svg><script>alert(1)</script></svg>')

    def test_current_color_takes_the_theme_accent(self) -> None:
        painted = ppt_assets.tint_svg(
            '<svg><path stroke="currentColor"/></svg>',
            "#0066FF",
        )
        self.assertIn("#0066FF", painted)
        self.assertNotIn("currentColor", painted)


class ResolveWithoutTheNetwork(unittest.TestCase):
    def test_icon_uses_the_first_trusted_library(self) -> None:
        def fake_get(url: str) -> tuple[bytes, str]:
            self.assertIn("lucide-static", url)
            return (
                b'<svg xmlns="http://www.w3.org/2000/svg"><path stroke="currentColor"/></svg>',
                "image/svg+xml",
            )

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "building.svg"
            path = ppt_assets.resolve_icon(
                "building-2",
                color="#111111",
                dest=dest,
                fetch=fake_get,
            )
            svg = path.read_text(encoding="utf-8")
            self.assertIn("#111111", svg)
            meta = json.loads(path.with_suffix(".svg.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["provider"], "lucide")
            self.assertEqual(meta["asset_type"], "icon")

    def test_photo_walks_unsplash_then_pexels(self) -> None:
        calls: list[str] = []

        def fake_search(provider: str, query: str, *, config=None) -> dict:
            calls.append(provider)
            if provider != "unsplash":
                return {"ok": False}
            return {
                "ok": True,
                "hits": [
                    {
                        "provider": "unsplash",
                        "url": "https://images.unsplash.com/photo-hi.jpg",
                        "page_url": "https://unsplash.com/photos/hi",
                        "credit": "Ada",
                        "width": 3000,
                        "height": 2000,
                    }
                ],
            }

        def fake_get(url: str) -> tuple[bytes, str]:
            self.assertTrue(url.startswith("https://images.unsplash.com/"))
            return b"\xff\xd8\xff", "image/jpeg"

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "hero.jpg"
            path = ppt_assets.resolve_photo(
                "AI Transformation Strategy",
                dest=dest,
                theme="startup",
                search=fake_search,
                fetch=fake_get,
            )
            self.assertEqual(path.read_bytes()[:3], b"\xff\xd8\xff")
            self.assertEqual(calls[0], "unsplash")
            meta = json.loads(path.with_suffix(".jpg.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["author"], "Ada")
            self.assertEqual(meta["asset_type"], "photography")

    def test_a_google_images_hit_is_skipped_even_if_search_returned_it(self) -> None:
        def fake_search(provider: str, query: str, *, config=None) -> dict:
            return {
                "ok": True,
                "hits": [
                    {
                        "provider": provider,
                        "url": "https://www.google.com/imgres?imgurl=x",
                        "width": 4000,
                        "height": 3000,
                    }
                ],
            }

        with self.assertRaises(FileNotFoundError):
            ppt_assets.resolve_photo(
                "office",
                search=fake_search,
                fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
            )


class AttachVisualOnRender(unittest.TestCase):
    def test_a_chart_slide_does_not_keep_image_png(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "chart",
            slide={
                "layout": "chart",
                "kind": "numbers",
                "title": "Revenue has tripled",
                "insight": "+43% year on year",
                "image": "image.png",
                "series": [{"label": "2026", "value": "120"}],
            },
        )
        self.assertNotIn("image.png", html)
        self.assertIn("nv-chart", html)

    def test_a_product_slide_asks_for_contain(self) -> None:
        payload = ppt_assets.attach_visual(
            {
                "layout": "text-image",
                "title": "The product",
                "visual": {"type": "product"},
            }
        )
        self.assertEqual(payload["visual"]["fit"], "contain")
        self.assertEqual(payload["visual"]["asset_type"], "screenshot")

    def test_an_already_chosen_file_is_treated_as_the_user_asset(self) -> None:
        payload = ppt_assets.attach_visual(
            {
                "layout": "text-image",
                "title": "The product",
                "visual": {"type": "product"},
                "image": "ui.png",
            }
        )
        self.assertEqual(payload["visual"]["asset_type"], "user")
        self.assertEqual(payload["visual"]["fit"], "contain")


class ThemePhotoPack(unittest.TestCase):
    def test_every_theme_ships_three_stills(self) -> None:
        ppt = Path(__file__).resolve().parents[1] / "templates" / "ppt"
        missing = []
        for folder in sorted(ppt.iterdir()):
            if not folder.is_dir() or folder.name.startswith(("_", ".")):
                continue
            pack = ppt_assets.theme_photo_pack(folder.name)
            if set(pack) != {"background", "left", "right"}:
                missing.append(folder.name)
        self.assertEqual(missing, [])

    def test_every_picker_theme_has_three_seeds(self) -> None:
        ppt = Path(__file__).resolve().parents[1] / "templates" / "ppt"
        names = {
            folder.name
            for folder in ppt.iterdir()
            if folder.is_dir() and not folder.name.startswith(("_", "."))
        }
        self.assertEqual(names, set(ppt_assets.THEME_PHOTO_SEEDS))
        for name, seeds in ppt_assets.THEME_PHOTO_SEEDS.items():
            self.assertEqual(set(seeds), {"background", "left", "right"}, name)

    def test_pack_fills_cover_left_and_right(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"\x00" * 24_000

        def fake_get(url: str) -> tuple[bytes, str]:
            self.assertIn("images.unsplash.com", url)
            return jpeg, "image/jpeg"

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            written = ppt_assets.seed_theme_photos(
                "startup",
                dest=folder,
                fetch=fake_get,
            )
            self.assertEqual(set(written), {"background", "left", "right"})
            pack = {
                slot: folder / f"{slot}.jpg"
                for slot in ("background", "left", "right")
            }
            with patch.object(ppt_assets, "theme_photo_pack", return_value=pack):
                cover = ppt_assets.attach_visual(
                    {"layout": "cover", "title": "Teams lose six hours a week"},
                    theme="startup",
                )
                left = ppt_assets.attach_visual(
                    {"layout": "image-text", "title": "The work sits on the left"},
                    theme="startup",
                )
                right = ppt_assets.attach_visual(
                    {"layout": "text-image", "title": "The proof sits on the right"},
                    theme="startup",
                )
            self.assertTrue(str(cover["image"]).endswith("background.jpg"))
            self.assertEqual(cover["visual"]["slot"], "background")
            self.assertTrue(str(left["image"]).endswith("left.jpg"))
            self.assertEqual(left["visual"]["slot"], "left")
            self.assertTrue(str(right["image"]).endswith("right.jpg"))
            self.assertEqual(right["visual"]["slot"], "right")

    def test_a_user_logo_beats_the_theme_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logo = Path(tmp) / "logo.png"
            logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
            pack = {
                "background": Path(tmp) / "background.jpg",
                "left": Path(tmp) / "left.jpg",
                "right": Path(tmp) / "right.jpg",
            }
            for path in pack.values():
                path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 24_000)
            with patch.object(ppt_assets, "theme_photo_pack", return_value=pack):
                payload = ppt_assets.attach_visual(
                    {"layout": "cover", "title": "Our name on the wall"},
                    theme="startup",
                    user_asset=str(logo),
                )
            self.assertEqual(payload["image"], str(logo))
            self.assertEqual(payload["visual"]["source"], "user")

    def test_a_process_slide_does_not_eat_a_lifestyle_photo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = {"right": Path(tmp) / "right.jpg"}
            pack["right"].write_bytes(b"\xff\xd8\xff" + b"\x00" * 24_000)
            with patch.object(ppt_assets, "theme_photo_pack", return_value=pack):
                payload = ppt_assets.attach_visual(
                    {
                        "layout": "process",
                        "kind": "steps",
                        "title": "An agent ships in 14 days",
                    },
                    theme="startup",
                )
            self.assertFalse(payload.get("image"))

    def test_cover_picks_the_theme_background_when_empty(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "cover",
            slide={
                "layout": "cover",
                "title": "Teams lose six hours a week",
            },
        )
        self.assertIn("nv-has-photo", html)
        self.assertIn("nv-scrim", html)
        self.assertIn("background.jpg", html)

    def test_cover_html_uses_the_background_still(self) -> None:
        html = ppt_design.materialize(
            "startup",
            "cover",
            slide={
                "layout": "cover",
                "title": "Teams lose six hours a week",
                "image": "hero.jpg",
            },
        )
        self.assertIn("nv-has-photo", html)
        self.assertIn("nv-scrim", html)
        self.assertIn("hero.jpg", html)


if __name__ == "__main__":
    unittest.main()
