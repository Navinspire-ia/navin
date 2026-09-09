# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: Dev mobile preview must remain after usage-PWA work.

The phone *usage* client is the WebUI PWA (see docs/mobile-usage-app.md).
``navin.mobile`` (detect / doctor / run / preview / bootstrap) is the Dev
workbench toolchain and must stay importable.
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class MobilePreviewUntouchedTest(unittest.TestCase):
    def test_preview_module_file_exists(self):
        preview = REPO_ROOT / "navin" / "mobile" / "preview.py"
        self.assertTrue(preview.is_file(), f"missing {preview}")

    def test_mobile_package_importable(self):
        mobile = importlib.import_module("navin.mobile")
        self.assertTrue(callable(getattr(mobile, "open_preview")))
        self.assertTrue(callable(getattr(mobile, "detect_mobile_project")))

    def test_preview_submodule_importable(self):
        mod = importlib.import_module("navin.mobile.preview")
        self.assertTrue(hasattr(mod, "open_preview"))
        self.assertTrue(hasattr(mod, "PreviewError"))

    def test_key_mobile_modules_still_present(self):
        for rel in (
            "navin/mobile/__init__.py",
            "navin/mobile/detect.py",
            "navin/mobile/doctor.py",
            "navin/mobile/run.py",
            "navin/mobile/preview.py",
            "navin/mobile/bootstrap.py",
            "navin/mobile/adb.py",
        ):
            path = REPO_ROOT / rel
            with self.subTest(rel=rel):
                self.assertTrue(path.is_file(), f"missing {path}")

    def test_dev_mobile_preview_ui_still_present(self):
        panel = (
            REPO_ROOT
            / "webui"
            / "src"
            / "components"
            / "dev"
            / "DevMobilePreview.tsx"
        )
        self.assertTrue(panel.is_file(), f"missing {panel}")
        text = panel.read_text(encoding="utf-8")
        self.assertIn("DevMobilePreview", text)


if __name__ == "__main__":
    unittest.main()
