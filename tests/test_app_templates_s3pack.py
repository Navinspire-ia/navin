"""Pack / extract app templates without hitting S3."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.templates.apps.s3pack import (
    catalog_export,
    extract_template_tarball,
    pack_template,
    template_public_url,
)


class AppTemplatesS3PackTest(unittest.TestCase):
    def test_public_url(self):
        self.assertEqual(
            template_public_url("crm"),
            "https://navinagent.s3.eu-north-1.amazonaws.com/templates/v1/crm.tar.gz",
        )

    def test_catalog_export_lists_all(self):
        payload = catalog_export()
        slugs = [row["slug"] for row in payload["templates"]]
        self.assertIn("crm", slugs)
        self.assertIn("ai-chat", slugs)
        self.assertGreaterEqual(len(slugs), 47)
        crm = next(row for row in payload["templates"] if row["slug"] == "crm")
        self.assertTrue(crm["aws_url"].endswith("templates/v1/crm.tar.gz"))

    def test_pack_and_extract_without_local_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            dest = Path(tmp) / "dest"
            missing = Path(tmp) / "no-cache"
            with patch(
                "navin.templates.apps.s3pack.local_cache_dir",
                return_value=missing,
            ):
                packed = pack_template("crm", out)
            self.assertTrue(Path(packed["path"]).is_file())
            self.assertGreater(packed["bytes"], 100)
            self.assertFalse(packed["copied_source"])
            extract_template_tarball(Path(packed["path"]), dest)
            self.assertTrue((dest / ".navin" / "apps" / "crm" / "navin.json").is_file())
            meta = json.loads((dest / "NAVIN_SOURCE.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["slug"], "crm")
            self.assertEqual(meta["aws_key"], "templates/v1/crm.tar.gz")


if __name__ == "__main__":
    unittest.main()
