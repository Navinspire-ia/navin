"""The Extensions panel: what it is told, and what it is allowed to do.

The install path itself is covered by test_lsp_vsix; what matters here is the
translation between that module and a browser - that a failure keeps the reason
it happened instead of becoming a 500, that installed and usable stay distinct,
and that the routes are actually wired, since a payload builder behind an
unwired route reads in the UI as "API route not found".
"""

from __future__ import annotations

import asyncio
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.lsp import vsix
from navin.webui.lsp_servers_api import (
    LspServersApiError,
    install_lsp_server,
    lsp_servers_payload,
    uninstall_lsp_server,
)


def _install_demo(tmp: str, name: str = "demo") -> dict:
    archive = Path(tmp) / "fake.vsix"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("extension/package.json", json.dumps({"version": "1.2.3"}))
        zf.writestr("extension/server/out/server.js", "process.stdin.resume();\n")
    with (
        patch.object(vsix, "resolve", return_value=("https://open-vsx.org/f.vsix", "1.2.3")),
        patch.object(
            vsix, "_download", side_effect=lambda url, dest: dest.write_bytes(archive.read_bytes())
        ),
        patch.object(vsix.shutil, "which", return_value="/usr/bin/node"),
    ):
        return install_lsp_server(
            name=name,
            extension="acme/demo",
            entrypoint="extension/server/out/server.js",
            suffixes=".demo",
        )


class PayloadTest(unittest.TestCase):
    def test_catalogue_is_offered_before_anything_is_installed(self):
        payload = lsp_servers_payload()
        names = {entry["name"] for entry in payload["extensions"]}
        self.assertEqual(names, set(vsix.catalog()))
        for entry in payload["extensions"]:
            with self.subTest(extension=entry["name"]):
                self.assertFalse(entry["installed"])
                self.assertTrue(entry["displayName"])
                self.assertTrue(entry["suffixes"])
                self.assertRegex(entry["marketplace"], r"^[^/]+/[^/]+$")
        self.assertTrue(payload["target"])

    def test_installed_entry_carries_its_version(self):
        with TemporaryDirectory() as tmp:
            _install_demo(tmp)
        entry = next(e for e in lsp_servers_payload()["others"] if e["name"] == "demo")
        self.assertTrue(entry["installed"])
        self.assertEqual(entry["version"], "1.2.3")
        self.assertEqual(entry["suffixes"], [".demo"])

    def test_installed_is_not_the_same_as_usable(self):
        """The panel has to be able to say "installed, but it will not start"."""
        with TemporaryDirectory() as tmp:
            _install_demo(tmp)
            project = Path(tmp) / "project"
            project.mkdir()
            with patch("navin.quality.linters.tool_argv", return_value=None):
                entry = next(
                    e for e in lsp_servers_payload(project)["others"] if e["name"] == "demo"
                )
        self.assertTrue(entry["installed"])
        self.assertFalse(entry["ready"])
        self.assertTrue(entry["detail"])

    def test_a_server_that_launches_is_reported_ready(self):
        with TemporaryDirectory() as tmp:
            _install_demo(tmp)
            project = Path(tmp) / "project"
            project.mkdir()
            entry = next(e for e in lsp_servers_payload(project)["others"] if e["name"] == "demo")
        self.assertTrue(entry["ready"])
        self.assertEqual(entry["detail"], "")

    def test_installing_returns_the_refreshed_list(self):
        with TemporaryDirectory() as tmp:
            payload = _install_demo(tmp)
        self.assertTrue(payload["ok"])
        self.assertIn("demo", {entry["name"] for entry in payload["others"]})


class ValidationTest(unittest.TestCase):
    def test_a_name_is_required(self):
        with self.assertRaises(LspServersApiError) as caught:
            install_lsp_server(name="  ")
        self.assertEqual(caught.exception.status, 400)

    def test_a_partial_custom_extension_says_what_is_missing(self):
        with self.assertRaises(LspServersApiError) as caught:
            install_lsp_server(name="demo", extension="acme/demo")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("the server path inside it", caught.exception.message)
        self.assertIn("the file types it handles", caught.exception.message)

    def test_suffixes_must_be_suffixes(self):
        with self.assertRaises(LspServersApiError) as caught:
            install_lsp_server(
                name="demo",
                extension="acme/demo",
                entrypoint="extension/server.js",
                suffixes="demo",
            )
        self.assertIn("must start with a dot", caught.exception.message)

    def test_an_uncatalogued_name_alone_is_refused_with_400(self):
        with self.assertRaises(LspServersApiError) as caught:
            install_lsp_server(name="nothing-like-this")
        self.assertEqual(caught.exception.status, 400)

    def test_a_missing_interpreter_is_a_precondition_not_a_crash(self):
        with (
            patch.object(vsix.shutil, "which", return_value=None),
            self.assertRaises(LspServersApiError) as caught,
        ):
            install_lsp_server(
                name="demo",
                extension="acme/demo",
                entrypoint="extension/server.js",
                suffixes=".demo",
            )
        self.assertEqual(caught.exception.status, 409)

    def test_an_unreachable_registry_is_reported_as_unavailable(self):
        with (
            patch.object(vsix.shutil, "which", return_value="/usr/bin/node"),
            patch.object(
                vsix, "resolve", side_effect=vsix.VsixError("could not reach", status=503)
            ),
            self.assertRaises(LspServersApiError) as caught,
        ):
            install_lsp_server(
                name="demo",
                extension="acme/demo",
                entrypoint="extension/server.js",
                suffixes=".demo",
            )
        self.assertEqual(caught.exception.status, 503)

    def test_removing_something_absent_is_a_404(self):
        with self.assertRaises(LspServersApiError) as caught:
            uninstall_lsp_server(name="demo")
        self.assertEqual(caught.exception.status, 404)

    def test_removing_a_hand_written_entry_is_refused(self):
        vsix._write_user_table({"servers": {"mine": {"binary": {"name": "x"}}}})
        with self.assertRaises(LspServersApiError) as caught:
            uninstall_lsp_server(name="mine")
        self.assertEqual(caught.exception.status, 409)

    def test_removing_stops_the_running_server(self):
        with TemporaryDirectory() as tmp:
            _install_demo(tmp)
            with patch("navin.lsp.manager.LspManager.drop_server") as dropped:
                uninstall_lsp_server(name="demo")
        dropped.assert_called_once_with("demo")


class _Request:
    def __init__(self, path: str) -> None:
        self.path = path
        self.headers = {}


class RouteTest(unittest.TestCase):
    """A payload nobody can reach is not a feature."""

    def _handler(self, *, local: bool = True):
        from navin.webui.ws_http import GatewayHTTPHandler

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True
        handler.workspace_controls_available = lambda connection: local
        return handler

    def _get(self, handler, path: str):
        got = path.split("?", 1)[0]
        return asyncio.run(handler._dispatch_misc_routes(None, _Request(path), got))

    def test_listing_is_served(self):
        response = self._get(self._handler(), "/api/webui/lsp-servers")
        self.assertIsNotNone(response, "route not registered")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertEqual(
            {entry["name"] for entry in body["extensions"]}, set(vsix.catalog())
        )

    def test_install_reports_the_reason_it_failed(self):
        response = self._get(
            self._handler(), "/api/webui/lsp-servers/install?name=nothing-like-this"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"catalogue", response.body)

    def test_install_passes_the_custom_details_through(self):
        with patch("navin.webui.lsp_servers_api.install_lsp_server") as install:
            install.return_value = {"ok": True}
            self._get(
                self._handler(),
                "/api/webui/lsp-servers/install?name=demo&extension=acme%2Fdemo"
                "&entrypoint=extension%2Fserver.js&suffixes=.demo&native=true",
            )
        kwargs = install.call_args.kwargs
        self.assertEqual(kwargs["extension"], "acme/demo")
        self.assertEqual(kwargs["suffixes"], ".demo")
        self.assertTrue(kwargs["native"])
        self.assertFalse(kwargs["platform_specific"])

    def test_a_remote_browser_cannot_install_onto_this_machine(self):
        response = self._get(
            self._handler(local=False), "/api/webui/lsp-servers/install?name=svelte"
        )
        self.assertEqual(response.status_code, 403)

    def test_a_remote_browser_cannot_uninstall_either(self):
        response = self._get(
            self._handler(local=False), "/api/webui/lsp-servers/uninstall?name=svelte"
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
