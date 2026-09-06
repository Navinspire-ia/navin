"""Working out what is inside an extension nobody described in advance.

The point of the probe is that it refuses as readily as it accepts: a registry
is mostly extensions with no language server in them, and one registered on a
guess would look installed and then answer nothing. So these tests care as much
about what is turned away, and why, as about what gets through.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.lsp import vsix, vsix_probe

# A server small enough to live in a test and real enough to answer: it frames
# one reply the way LSP requires and says it can do something.
_FAKE_SERVER = r"""
let buffer = "";
process.stdin.on("data", (chunk) => {
  buffer += chunk.toString("utf8");
  const split = buffer.indexOf("\r\n\r\n");
  if (split < 0) return;
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    result: { capabilities: { hoverProvider: true } },
  });
  process.stdout.write(`Content-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
});
"""

_MUTE_SERVER = "process.stdin.resume();\n"


def _node_missing() -> bool:
    import shutil

    return shutil.which("node") is None


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _manifest(root: Path, **contents: object) -> None:
    _write(root, "extension/package.json", json.dumps(contents))


class DeclaredLanguagesTest(unittest.TestCase):
    def test_reads_the_file_types_the_extension_declares(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(
                root,
                contributes={
                    "languages": [
                        {"id": "svelte", "extensions": [".svelte"]},
                        {"id": "ignored", "extensions": []},
                    ]
                },
            )
            spec = vsix_probe.declared_languages(root)
        assert spec is not None
        self.assertEqual(spec["extensions"], [".svelte"])
        self.assertEqual(spec["language_ids"], {".svelte": "svelte"})
        self.assertEqual(spec["languages"], ["svelte"])

    def test_falls_back_to_activation_events_for_a_builtin_language(self):
        """PHP is one VS Code already knows, so intelephense never redeclares it."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(root, activationEvents=["onLanguage:php", "onCommand:whatever"])
            spec = vsix_probe.declared_languages(root)
        assert spec is not None
        self.assertIn(".php", spec["extensions"])
        self.assertEqual(spec["language_ids"][".php"], "php")

    def test_activation_events_do_not_widen_a_declared_extension(self):
        """Svelte activates on TypeScript to read it, not to serve it."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(
                root,
                contributes={"languages": [{"id": "svelte", "extensions": [".svelte"]}]},
                activationEvents=["onLanguage:typescript", "onLanguage:javascript"],
            )
            spec = vsix_probe.declared_languages(root)
        assert spec is not None
        self.assertEqual(spec["extensions"], [".svelte"])

    def test_an_extension_that_claims_no_file_is_not_installable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(root, contributes={"themes": [{"label": "Dark"}]})
            self.assertIsNone(vsix_probe.declared_languages(root))

    def test_a_server_ranks_below_the_packaged_ones(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(root, contributes={"languages": [{"id": "x", "extensions": [".x"]}]})
            spec = vsix_probe.declared_languages(root)
        assert spec is not None
        self.assertGreater(spec["priority"], 20)


class CandidateTest(unittest.TestCase):
    def test_finds_a_compiled_server_without_the_executable_bit(self):
        """Unzipping drops permissions, so the file has to be recognised by content."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "extension" / "server" / "rust-analyzer"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 64)
            binary.chmod(0o644)
            candidates = vsix_probe.server_candidates(root)
        self.assertEqual([(binary, "native")], candidates)

    def test_ignores_a_data_file_that_merely_has_no_suffix(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "extension/LICENSE", "MIT")
            self.assertEqual(vsix_probe.server_candidates(root), [])

    def test_takes_a_bundle_named_after_the_product(self):
        """intelephense.js says nothing by its name; its size and place do."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "extension/lib/intelephense.js", "x" * (300 * 1024))
            _write(root, "extension/lib/small-helper.js", "x")
            candidates = vsix_probe.server_candidates(root)
        self.assertEqual([path.name for path, _ in candidates], ["intelephense.js"])

    def test_skips_a_server_built_for_the_browser(self):
        """A browser build talks over a worker port and would hang the handshake."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "extension/dist/browser/server.js", "x")
            _write(root, "extension/dist/node/server.js", "x")
            candidates = vsix_probe.server_candidates(root)
        self.assertEqual([str(p.relative_to(root)) for p, _ in candidates],
                         [os.path.join("extension", "dist", "node", "server.js")])

    def test_prefers_the_shallower_of_two_plausible_servers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "extension/out/server.js", "x")
            deep = "extension/node_modules/a/b/c/server.js"
            _write(root, deep, "x")
            candidates = vsix_probe.server_candidates(root)
        self.assertEqual(candidates[0][0].relative_to(root).parts[1], "out")


@unittest.skipIf(_node_missing(), "needs node to start a server")
class HandshakeTest(unittest.TestCase):
    def test_accepts_a_server_that_answers_initialize(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = _write(root, "server.js", _FAKE_SERVER)
            capabilities = vsix_probe.handshake(
                ["node", str(server), "--stdio"], root, timeout_s=20
            )
        self.assertEqual(capabilities, {"hoverProvider": True})

    def test_rejects_a_process_that_only_stays_alive(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = _write(root, "server.js", _MUTE_SERVER)
            capabilities = vsix_probe.handshake(
                ["node", str(server), "--stdio"], root, timeout_s=3
            )
        self.assertIsNone(capabilities)

    def test_a_command_that_does_not_exist_is_not_a_crash(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(
                vsix_probe.handshake(
                    [str(Path(tmp) / "nowhere")], Path(tmp), timeout_s=3
                )
            )


@unittest.skipIf(_node_missing(), "needs node to start a server")
class DetectTest(unittest.TestCase):
    def test_proves_the_server_before_accepting_it(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(root, contributes={"languages": [{"id": "x", "extensions": [".x"]}]})
            _write(root, "extension/out/server.js", _FAKE_SERVER)
            server, args, runtime, spec, capabilities = vsix_probe.detect(root)
        self.assertEqual(server.name, "server.js")
        self.assertEqual(args, ["--stdio"])
        self.assertEqual(runtime, "node")
        self.assertEqual(spec["extensions"], [".x"])
        self.assertTrue(capabilities["hoverProvider"])

    def test_refuses_an_extension_whose_candidates_all_stay_silent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(root, contributes={"languages": [{"id": "x", "extensions": [".x"]}]})
            _write(root, "extension/out/server.js", _MUTE_SERVER)
            with (
                patch.object(vsix_probe, "_NODE_ARGS", (["--stdio"],)),
                self.assertRaises(vsix.VsixError) as caught,
            ):
                vsix_probe.detect(root)
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("handshake", caught.exception.message)

    def test_says_so_when_there_is_no_server_at_all(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(root, contributes={"languages": [{"id": "x", "extensions": [".x"]}]})
            _write(root, "extension/themes/dark.json", "{}")
            with self.assertRaises(vsix.VsixError) as caught:
                vsix_probe.detect(root)
        self.assertIn("no language server", caught.exception.message)

    @unittest.skipIf(sys.platform == "win32", "posix permissions")
    def test_restores_the_executable_bit_the_archive_dropped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _manifest(root, contributes={"languages": [{"id": "x", "extensions": [".x"]}]})
            server = _write(root, "extension/server/thing", "#!/bin/sh\nexit 0\n")
            with server.open("r+b") as stream:
                stream.write(b"\x7fELF")
            server.chmod(0o644)
            with self.assertRaises(vsix.VsixError):
                vsix_probe.detect(root)
            mode = server.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)


@unittest.skipIf(_node_missing(), "needs node to start a server")
class InstallDetectedTest(unittest.TestCase):
    """The whole path: an id goes in, a working entry comes out."""

    def _archive(self, tmp: Path) -> Path:
        archive = tmp / "fake.vsix"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(
                "extension/package.json",
                json.dumps(
                    {
                        "version": "2.0.0",
                        "contributes": {"languages": [{"id": "demo", "extensions": [".demo"]}]},
                    }
                ),
            )
            zf.writestr("extension/out/server.js", _FAKE_SERVER)
        return archive

    def test_registers_what_it_found(self):
        with TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            with (
                patch.object(vsix, "install_root", return_value=Path(tmp) / "installed"),
                patch.object(vsix, "_user_table_path", return_value=Path(tmp) / "table.json"),
                patch.object(vsix, "resolve", return_value=("https://open-vsx.org/f.vsix", "2.0.0")),
                patch.object(
                    vsix,
                    "_download",
                    side_effect=lambda url, dest: dest.write_bytes(archive.read_bytes()),
                ),
            ):
                (Path(tmp) / "installed").mkdir()
                result = vsix.install_detected("acme/demo")
                table = json.loads((Path(tmp) / "table.json").read_text())

        self.assertEqual(result["extensions"], [".demo"])
        entry = table["servers"]["demo"]
        self.assertEqual(entry["args"][-1], "--stdio")
        self.assertEqual(entry[vsix.MANAGED_KEY]["marketplace"], "acme/demo")

    def test_leaves_nothing_behind_when_the_extension_has_no_server(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "fake.vsix"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "extension/package.json",
                    json.dumps(
                        {"contributes": {"languages": [{"id": "d", "extensions": [".d"]}]}}
                    ),
                )
                zf.writestr("extension/themes/dark.json", "{}")
            root = Path(tmp) / "installed"
            root.mkdir()
            with (
                patch.object(vsix, "install_root", return_value=root),
                patch.object(vsix, "_user_table_path", return_value=Path(tmp) / "table.json"),
                patch.object(vsix, "resolve", return_value=("https://open-vsx.org/f.vsix", "1")),
                patch.object(
                    vsix,
                    "_download",
                    side_effect=lambda url, dest: dest.write_bytes(archive.read_bytes()),
                ),
                self.assertRaises(vsix.VsixError),
            ):
                vsix.install_detected("acme/demo")
            self.assertEqual(list(root.iterdir()), [])
            self.assertFalse((Path(tmp) / "table.json").exists())

    def test_a_universal_build_is_the_fallback_for_this_machine(self):
        """Asking for the platform build first is what finds a compiled server."""
        calls: list[str | None] = []

        def resolve(slug, version=None, target=None):
            calls.append(target)
            if target:
                raise vsix.VsixError("no build for that platform", status=404)
            return "https://open-vsx.org/f.vsix", "2.0.0"

        with TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            with (
                patch.object(vsix, "install_root", return_value=Path(tmp) / "installed"),
                patch.object(vsix, "_user_table_path", return_value=Path(tmp) / "table.json"),
                patch.object(vsix, "resolve", side_effect=resolve),
                patch.object(
                    vsix,
                    "_download",
                    side_effect=lambda url, dest: dest.write_bytes(archive.read_bytes()),
                ),
            ):
                (Path(tmp) / "installed").mkdir()
                vsix.install_detected("acme/demo")

        self.assertEqual(calls, [vsix.host_target(), None])


class SearchTest(unittest.TestCase):
    def test_joins_the_registry_answer_with_what_is_installed(self):
        from navin.webui.lsp_servers_api import search_lsp_servers

        found = [
            {"slug": "acme/demo", "displayName": "Demo", "description": "",
             "version": "1", "downloads": 5},
            {"slug": "other/thing", "displayName": "Thing", "description": "",
             "version": "1", "downloads": 2},
        ]
        installed = {"demo": {vsix.MANAGED_KEY: {"marketplace": "acme/demo"}}}
        with (
            patch("navin.lsp.vsix_probe.search", return_value=found),
            patch.object(vsix, "installed", return_value=installed),
        ):
            payload = search_lsp_servers(query="demo")

        by_slug = {row["slug"]: row for row in payload["results"]}
        self.assertTrue(by_slug["acme/demo"]["installed"])
        self.assertEqual(by_slug["acme/demo"]["installedAs"], "demo")
        self.assertFalse(by_slug["other/thing"]["installed"])

    def test_an_empty_query_asks_the_registry_nothing(self):
        from navin.webui.lsp_servers_api import search_lsp_servers

        with patch("navin.lsp.vsix_probe.search") as searched:
            self.assertEqual(search_lsp_servers(query="  ")["results"], [])
        searched.assert_not_called()


class RouteTest(unittest.TestCase):
    """A payload nobody can reach is not a feature."""

    def _handler(self, *, local: bool = True):
        from navin.webui.route_cache import CoalescingCache
        from navin.webui.ws_http import GatewayHTTPHandler

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True
        handler.workspace_controls_available = lambda connection: local
        # The search route reaches the registry through the shared coalescing
        # cache, which __init__ builds and object.__new__ skips.
        handler.route_cache = CoalescingCache()
        return handler

    def _get(self, handler, path: str):
        import asyncio

        class _Request:
            def __init__(self, target: str) -> None:
                self.path = target
                self.headers = {}

        return asyncio.run(
            handler._dispatch_misc_routes(None, _Request(path), path.split("?", 1)[0])
        )

    def test_search_is_served(self):
        with patch(
            "navin.webui.lsp_servers_api.search_lsp_servers",
            return_value={"results": [], "query": "rust"},
        ) as searched:
            response = self._get(self._handler(), "/api/webui/lsp-servers/search?q=rust")
        self.assertIsNotNone(response, "route not registered")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(searched.call_args.kwargs["query"], "rust")

    def test_detected_install_is_served(self):
        with patch(
            "navin.webui.lsp_servers_api.install_detected_lsp_server",
            return_value={"ok": True},
        ) as install:
            response = self._get(
                self._handler(),
                "/api/webui/lsp-servers/install-detected?extension=rust-lang%2Frust-analyzer",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(install.call_args.kwargs["extension"], "rust-lang/rust-analyzer")

    def test_a_remote_browser_cannot_install_a_detected_server(self):
        response = self._get(
            self._handler(local=False),
            "/api/webui/lsp-servers/install-detected?extension=acme%2Fdemo",
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
