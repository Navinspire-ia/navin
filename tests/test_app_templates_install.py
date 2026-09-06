"""Local create/install for app templates. AWS publish stays fail-closed."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.templates.apps.install import (
    AppTemplateError,
    create_app,
    install_app,
    is_installed,
    list_installed_slugs,
    publish_to_aws,
)


class AppTemplatesInstallTest(unittest.TestCase):
    def test_install_writes_overlay_without_copying_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = install_app("crm", workspace, start=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["slug"], "crm")
            self.assertFalse(result["copied_source"])
            self.assertFalse(result["can_publish"])
            plugin = workspace / ".navin" / "apps" / "crm"
            self.assertTrue((plugin / "navin.json").is_file())
            self.assertTrue((plugin / "overlay.json").is_file())
            self.assertTrue((plugin / "install.json").is_file())
            playbook = json.loads((plugin / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(playbook["slug"], "crm")
            self.assertTrue(playbook["databases"])
            self.assertTrue((plugin / "agents" / "sales-agent" / "system.md").is_file())
            self.assertTrue(is_installed(workspace, "crm"))
            self.assertIn("crm", list_installed_slugs(workspace))
            overlay = json.loads((plugin / "overlay.json").read_text(encoding="utf-8"))
            self.assertEqual(overlay["slug"], "crm")
            manifest = json.loads((plugin / "navin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "Navin CRM")
            self.assertEqual(manifest["audit_status"], "in_review")
            self.assertIn("query", manifest["mcp"]["tools"])
            again = install_app("crm", workspace, start=False)
            self.assertTrue(again["already_installed"])

    def test_create_without_cache_writes_plugin_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "mon-crm"
            missing = Path(tmp) / "no-cache"
            with patch(
                "navin.templates.apps.install.local_cache_dir",
                return_value=missing,
            ), patch(
                "navin.templates.apps.s3pack.download_template_tarball",
                return_value=False,
            ):
                result = create_app("crm", dest, start=False)
            self.assertTrue(result["ok"])
            self.assertFalse(result["copied_source"])
            self.assertTrue((dest / ".navin" / "apps" / "crm" / "navin.json").is_file())
            self.assertTrue((dest / "NAVIN.md").is_file())

    def test_create_rejects_non_empty_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "taken"
            dest.mkdir()
            (dest / "keep.txt").write_text("nope", encoding="utf-8")
            with self.assertRaises(AppTemplateError) as ctx:
                create_app("crm", dest, start=False)
            self.assertEqual(ctx.exception.status, 409)

    def test_unknown_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AppTemplateError) as ctx:
                install_app("not-a-real-template", tmp, start=False)
            self.assertEqual(ctx.exception.status, 404)

    def test_publish_refuses_unaudited_templates(self):
        with self.assertRaises(AppTemplateError) as ctx:
            publish_to_aws("crm")
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("Refuse AWS upload", ctx.exception.message)
        self.assertIn("crm", ctx.exception.message)


class AppTemplatesRouteTest(unittest.TestCase):
    """Create/install must be wired. An unwired route is 'API route not found' in the UI."""

    def _handler(self):
        from navin.webui.ws_http import GatewayHTTPHandler

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True
        handler.skills_workspace_path = Path(".")
        return handler

    def _get(self, path: str):
        import asyncio

        handler = self._handler()
        got = path.split("?", 1)[0]

        class _Request:
            def __init__(self, request_path: str) -> None:
                self.path = request_path
                self.headers = {}

        return asyncio.run(handler._dispatch_misc_routes(None, _Request(path), got))

    def test_create_and_install_routes_are_registered(self):
        with patch(
            "navin.webui.ws_http.install_app_template_payload",
            return_value={"ok": True, "action": "install", "slug": "crm"},
        ), patch(
            "navin.webui.ws_http.create_app_template_payload",
            return_value={"ok": True, "action": "create", "slug": "crm", "dest": "/tmp/x"},
        ), patch(
            "navin.webui.ws_http.list_app_templates_payload",
            return_value={"templates": []},
        ):
            for path in (
                "/api/webui/app-templates",
                "/api/webui/app-templates/install?slug=crm",
                "/api/webui/app-templates/create?slug=crm&dest=./tmp-route-probe",
            ):
                response = self._get(path)
                self.assertIsNotNone(response, f"route not registered: {path}")
                self.assertNotEqual(response.status_code, 404, f"404 for {path}")
                self.assertEqual(response.status_code, 200, f"expected 200 for {path}")


if __name__ == "__main__":
    unittest.main()
