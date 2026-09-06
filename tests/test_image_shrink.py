"""Images go to the model at a size every provider accepts and no bigger."""

from __future__ import annotations

import base64
import io
import unittest

from PIL import Image

from navin.utils.helpers import (
    IMAGE_MAX_BYTES,
    IMAGE_MAX_SIDE_PX,
    build_image_content_blocks,
    shrink_image_for_model,
)


def _png(width: int, height: int) -> bytes:
    image = Image.new("RGBA", (width, height), (30, 120, 200, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _size(raw: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(raw)) as image:
        return image.size


class ShrinkTest(unittest.TestCase):
    def test_a_small_image_is_passed_through_byte_for_byte(self) -> None:
        raw = _png(800, 600)
        out, mime, note = shrink_image_for_model(raw, "image/png")
        self.assertIs(out, raw)
        self.assertEqual(mime, "image/png")
        self.assertIsNone(note)

    def test_a_wide_screenshot_is_scaled_to_the_side_cap_and_says_so(self) -> None:
        raw = _png(3000, 1200)
        out, mime, note = shrink_image_for_model(raw, "image/png")
        self.assertEqual(_size(out), (IMAGE_MAX_SIDE_PX, 800))
        self.assertEqual(mime, "image/png")
        self.assertEqual(note, f"(image scaled to {IMAGE_MAX_SIDE_PX}x800 from 3000x1200)")

    def test_a_tall_full_page_capture_is_scaled_on_its_height(self) -> None:
        raw = _png(1280, 9000)
        out, _mime, _note = shrink_image_for_model(raw, "image/png")
        width, height = _size(out)
        self.assertEqual(height, IMAGE_MAX_SIDE_PX)
        self.assertEqual(width, round(1280 * IMAGE_MAX_SIDE_PX / 9000))

    def test_a_heavy_png_that_fits_the_side_cap_becomes_a_jpeg(self) -> None:
        import os

        # Random pixels do not compress: ~10 MB of PNG under the 2 000 px cap.
        image = Image.frombytes("RGB", (1900, 1900), os.urandom(1900 * 1900 * 3))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()
        self.assertGreater(len(raw), IMAGE_MAX_BYTES)
        out, mime, note = shrink_image_for_model(raw, "image/png")
        self.assertLessEqual(len(out), IMAGE_MAX_BYTES)
        self.assertEqual(mime, "image/jpeg")
        self.assertIn("re-encoded as jpeg", note)

    def test_gifs_and_svgs_are_never_touched(self) -> None:
        for mime in ("image/gif", "image/svg+xml"):
            with self.subTest(mime=mime):
                raw = b"whatever" * 10
                self.assertEqual(shrink_image_for_model(raw, mime), (raw, mime, None))

    def test_bytes_that_are_not_an_image_come_back_unchanged(self) -> None:
        raw = b"not an image at all"
        self.assertEqual(shrink_image_for_model(raw, "image/png"), (raw, "image/png", None))


class ContentBlocksTest(unittest.TestCase):
    def test_the_block_carries_the_scaled_image_and_the_note(self) -> None:
        blocks = build_image_content_blocks(_png(4000, 1000), "image/png", "/tmp/x.png", "(Image file: x.png)")
        url = blocks[0]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        payload = base64.b64decode(url.split(",", 1)[1])
        self.assertEqual(_size(payload), (IMAGE_MAX_SIDE_PX, 500))
        self.assertEqual(blocks[1]["text"], f"(Image file: x.png) (image scaled to {IMAGE_MAX_SIDE_PX}x500 from 4000x1000)")

    def test_a_small_image_keeps_its_plain_label(self) -> None:
        blocks = build_image_content_blocks(_png(640, 480), "image/png", "/tmp/y.png", "(Image file: y.png)")
        self.assertEqual(blocks[1]["text"], "(Image file: y.png)")


if __name__ == "__main__":
    unittest.main()
