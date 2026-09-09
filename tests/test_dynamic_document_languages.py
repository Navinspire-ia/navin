# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Language-neutrality contract for every built-in document template."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
CATEGORIES = ("ppt", "word", "pdf", "excel")
HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
BODY_TAG_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)
IMAGE_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


class DynamicDocumentLanguageTests(unittest.TestCase):
    def test_all_catalog_metadata_requires_dynamic_localization(self) -> None:
        metadata_paths = [
            path
            for category in CATEGORIES
            for path in (TEMPLATES / category).glob("*/metadata.json")
        ]
        self.assertGreaterEqual(len(metadata_paths), 56)
        for path in metadata_paths:
            with self.subTest(path=path.relative_to(ROOT)):
                metadata = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(metadata.get("language_mode"), "dynamic")
                self.assertEqual(metadata.get("text_policy"), "replace_all_visible_text")
                self.assertEqual(
                    metadata.get("image_text_policy"),
                    "verify_or_replace_text_bearing_images",
                )
                self.assertIsInstance(metadata.get("output_formats"), list)
                self.assertTrue(metadata["output_formats"])

    def test_all_template_html_is_language_neutral_and_rtl_ready(self) -> None:
        html_paths = [
            path
            for category in CATEGORIES
            for path in (TEMPLATES / category).glob("*/*.html")
        ]
        self.assertGreaterEqual(len(html_paths), 210)
        for path in html_paths:
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text(encoding="utf-8")
                html_tag = HTML_TAG_RE.search(source)
                body_tag = BODY_TAG_RE.search(source)
                self.assertIsNotNone(html_tag)
                self.assertIsNotNone(body_tag)
                self.assertRegex(html_tag.group(0), r'\blang="und"')
                self.assertRegex(
                    html_tag.group(0),
                    r'\bdata-language-mode="dynamic"',
                )
                self.assertRegex(
                    body_tag.group(0),
                    r'\bdata-localize="all-visible-text"',
                )
                self.assertIn('id="navin-dynamic-language"', source)
                self.assertRegex(source, r'html\[dir="rtl"\]')

                for image_tag in IMAGE_TAG_RE.findall(source):
                    self.assertIn(
                        'data-localization="verify-text-free"',
                        image_tag,
                    )


if __name__ == "__main__":
    unittest.main()
