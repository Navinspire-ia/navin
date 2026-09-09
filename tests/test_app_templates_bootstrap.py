# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bootstrap writes env and starts only when the app source is present."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.templates.apps.bootstrap import (
    bootstrap_and_start,
    has_app_source,
    preview_url_from_playbook,
    write_env,
)
from navin.templates.apps.install import create_app, install_app


class AppTemplatesBootstrapTest(unittest.TestCase):
    def test_write_env_skips_noise_and_writes_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = write_env(
                root,
                {
                    "env": [
                        {"key": "CLAUDE_PROJECT_DIR", "default": "/tmp", "secret": False},
                        {"key": "HOME", "default": "/home/x", "secret": False},
                        {"key": "DATABASE_URL", "default": "postgres://navin:navin@127.0.0.1:5432/app"},
                        {"key": "JWT_SECRET", "default": "", "secret": True},
                    ]
                },
            )
            self.assertEqual(status, "written")
            text = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("DATABASE_URL=", text)
            self.assertIn("JWT_SECRET=change-me-jwt-secret", text)
            self.assertNotIn("CLAUDE_", text)
            self.assertNotIn("HOME=", text)
            self.assertEqual(write_env(root, {"env": []}), "exists")

    def test_preview_url_skips_database_ports(self):
        self.assertEqual(
            preview_url_from_playbook({"ports": [5432, 5174, 54321]}),
            "http://127.0.0.1:5174",
        )
        self.assertEqual(
            preview_url_from_playbook({"ports": [5432], "launch": {"dev": "npm run dev:demo"}}),
            "http://127.0.0.1:5174",
        )

    def test_install_without_source_does_not_write_root_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with patch("navin.templates.apps.bootstrap.start_app_processes") as spawn:
                result = install_app("crm", workspace, start=True, wait=True)
            spawn.assert_not_called()
            self.assertFalse((workspace / ".env").exists())
            self.assertEqual(result["bootstrap"]["env"], "no-source")
            self.assertFalse(result["started"])
            self.assertIsNone(result["preview_url"])

    def test_create_with_source_starts_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "mon-crm"
            cache = Path(tmp) / "cache"
            cache.mkdir()
            (cache / "package.json").write_text('{"name":"atomic-crm"}', encoding="utf-8")
            (cache / ".env.example").write_text("VITE_SUPABASE_URL=http://127.0.0.1:54321\n", encoding="utf-8")
            with patch(
                "navin.templates.apps.install.local_cache_dir",
                return_value=cache,
            ), patch(
                "navin.templates.apps.s3pack.download_template_tarball",
                return_value=False,
            ), patch(
                "navin.templates.apps.bootstrap.start_databases",
                return_value=["supabase:skip"],
            ), patch(
                "navin.templates.apps.bootstrap.install_packages",
                return_value=["node:ok"],
            ), patch(
                "navin.templates.apps.bootstrap.start_app_processes",
                return_value=["npm run dev:demo"],
            ) as spawn:
                result = create_app("crm", dest, start=True, wait=True)
            spawn.assert_called_once()
            self.assertTrue(has_app_source(dest, json.loads((dest / ".navin" / "apps" / "crm" / "install.json").read_text())))
            self.assertEqual(result["preview_url"], "http://127.0.0.1:5174")
            self.assertEqual(result["started"], ["npm run dev:demo"])
            self.assertTrue((dest / ".env").is_file())

    def test_start_false_never_spawns(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "idle"
            with patch(
                "navin.templates.apps.s3pack.download_template_tarball",
                return_value=False,
            ), patch(
                "navin.templates.apps.install.local_cache_dir",
                return_value=Path(tmp) / "missing",
            ), patch(
                "navin.templates.apps.bootstrap.start_app_processes",
            ) as spawn:
                create_app("crm", dest, start=False)
            spawn.assert_not_called()
            report = bootstrap_and_start(dest, "crm", start=False)
            self.assertEqual(report["started"], [])
            self.assertIsNone(report["preview_url"])


if __name__ == "__main__":
    unittest.main()
