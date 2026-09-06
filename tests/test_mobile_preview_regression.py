"""Regression: Dev mobile preview modules stay importable after usage-PWA work.

The mobile *usage* client is the WebUI PWA. ``navin.mobile`` remains the Dev
workbench toolchain (detect / doctor / run / preview / bootstrap). This test
guards against accidental breakage of that package surface.
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class MobilePreviewImportRegressionTest(unittest.TestCase):
    def test_navin_mobile_package_exports(self):
        mobile = importlib.import_module("navin.mobile")
        for name in (
            "detect_mobile_project",
            "run_doctor",
            "build_run_plan",
            "open_preview",
            "run_bootstrap",
            "resolve_adb_location",
            "host_platform",
        ):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(mobile, name)))

    def test_mobile_submodules_import(self):
        for mod in (
            "navin.mobile.detect",
            "navin.mobile.doctor",
            "navin.mobile.run",
            "navin.mobile.preview",
            "navin.mobile.bootstrap",
            "navin.mobile.adb",
        ):
            with self.subTest(mod=mod):
                importlib.import_module(mod)

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


class UsagePwaAssetsTest(unittest.TestCase):
    """Smoke-check that the usage PWA shell files exist beside the WebUI."""

    def test_manifest_and_service_worker_present(self):
        public = REPO_ROOT / "webui" / "public"
        manifest = public / "manifest.webmanifest"
        sw = public / "sw.js"
        self.assertTrue(manifest.is_file(), f"missing {manifest}")
        self.assertTrue(sw.is_file(), f"missing {sw}")
        data = manifest.read_text(encoding="utf-8")
        self.assertIn('"display": "standalone"', data)
        self.assertIn("navin-mark-192.png", data)
        sw_text = sw.read_text(encoding="utf-8")
        self.assertIn("navin-webui-shell", sw_text)

    def test_built_dist_includes_service_worker(self):
        """Gateway serves navin/web/dist - PWA assets must be copied there."""
        dist = REPO_ROOT / "navin" / "web" / "dist"
        sw = dist / "sw.js"
        manifest = dist / "manifest.webmanifest"
        self.assertTrue(sw.is_file(), f"missing {sw} - run: cd webui && npm run build")
        self.assertTrue(manifest.is_file(), f"missing {manifest}")
        index = (dist / "index.html").read_text(encoding="utf-8")
        self.assertIn("viewport-fit=cover", index)
        self.assertIn('rel="manifest"', index)

    def test_usage_pwa_doc_present(self):
        doc = REPO_ROOT / "docs" / "mobile-usage-app.md"
        self.assertTrue(doc.is_file(), f"missing {doc}")
        text = doc.read_text(encoding="utf-8")
        # Doc was rewritten as the mobile chat companion page; anchor on its
        # stable concepts (home-screen install + LAN access) instead of the
        # retired "Usage PWA" wording.
        self.assertIn("chat companion", text)
        self.assertIn("Add to Home Screen", text)
        self.assertIn("LAN", text)
        self.assertNotIn("\u2014", text)  # no em dash
        self.assertNotIn("\u2013", text)  # no en dash
        # Stub keeps old path discoverable.
        stub = REPO_ROOT / "docs" / "mobile-app.md"
        self.assertTrue(stub.is_file(), f"missing {stub}")
        self.assertIn("mobile-usage-app.md", stub.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
