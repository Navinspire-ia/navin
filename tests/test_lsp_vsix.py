# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Installing language servers out of VS Code extension archives.

Nothing here touches the network or a real extension: the registry lookup and
the download are replaced by a zip built in the test, which is what lets these
run offline and keeps them honest about the parts navin actually owns - the
archive handling and the table entry it writes.
"""

from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from navin.lsp import manager as manager_mod
from navin.lsp import vsix


def _fake_vsix(path: Path, *, server_rel: str = "extension/server/out/server.js") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("extension/package.json", json.dumps({"version": "1.2.3"}))
        archive.writestr(server_rel, "process.stdin.resume();\n")
    return path


class CatalogTest(unittest.TestCase):
    def test_entries_are_well_formed(self):
        entries = vsix.catalog()
        self.assertTrue(entries)
        for name, spec in entries.items():
            with self.subTest(extension=name):
                self.assertRegex(str(spec["marketplace"]), r"^[^/]+/[^/]+$")
                self.assertTrue(spec["entrypoint"])
                self.assertIn(spec.get("runtime", "node"), {"node", "native"})
                self.assertTrue(spec.get("verified_version"))
                if spec.get("platform_specific"):
                    # A per-platform archive only exists because the server is a
                    # compiled program; a node script is the same everywhere.
                    self.assertEqual(spec.get("runtime"), "native")
                lsp = spec["lsp"]
                self.assertTrue(lsp["extensions"])
                self.assertTrue(lsp["root_markers"])
                for suffix in lsp["extensions"]:
                    self.assertTrue(suffix.startswith("."))
                    self.assertIn(suffix, lsp["language_ids"])

    def test_catalog_entries_match_the_server_table_contract(self):
        """A generated entry must satisfy what test_lsp asserts about servers."""
        for name, spec in vsix.catalog().items():
            entry = vsix._server_entry(
                spec["lsp"],
                Path("/tmp/whatever/server.js"),
                runtime=spec.get("runtime", "node"),
                slug=spec["marketplace"],
                version="1.0.0",
            )
            with self.subTest(extension=name):
                self.assertIsInstance(entry.get("binary"), dict)
                self.assertTrue(entry.get("extensions"))
                self.assertTrue(entry.get("root_markers"))
                for suffix in entry["extensions"]:
                    self.assertIn(suffix, entry.get("language_ids", {}))


class TargetPlatformTest(unittest.TestCase):
    def test_host_target_is_one_open_vsx_knows(self):
        known = {
            "linux-x64",
            "linux-arm64",
            "darwin-x64",
            "darwin-arm64",
            "win32-x64",
            "win32-arm64",
        }
        self.assertIn(vsix.host_target(), known)

    def test_a_compiled_server_asks_for_this_machines_build(self):
        seen: list[str] = []

        def fake_get(url, *args, **kwargs):
            seen.append(url)
            raise vsix.VsixError("stop here")

        with patch.object(vsix.httpx, "Client") as client:
            client.return_value.__enter__.return_value.get.side_effect = fake_get
            with self.assertRaises(vsix.VsixError):
                vsix.resolve("acme/demo", None, target="linux-x64")
        self.assertEqual(seen, ["https://open-vsx.org/api/acme/demo/linux-x64/latest"])

    def test_a_missing_platform_build_says_so(self):
        response = SimpleNamespace(status_code=404, raise_for_status=lambda: None, json=dict)
        with patch.object(vsix.httpx, "Client") as client:
            client.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaises(vsix.VsixError) as caught:
                vsix.resolve("acme/demo", None, target="linux-arm64")
        self.assertIn("linux-arm64", str(caught.exception))


class ArchiveSafetyTest(unittest.TestCase):
    def test_parent_traversal_is_rejected(self):
        with TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "evil.vsix"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escaped.js", "owned")
            with zipfile.ZipFile(archive_path) as archive, self.assertRaises(vsix.VsixError):
                vsix._safe_members(archive, Path(tmp) / "dest")

    def test_absolute_path_is_rejected(self):
        with TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "evil.vsix"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("/etc/passwd", "owned")
            with zipfile.ZipFile(archive_path) as archive, self.assertRaises(vsix.VsixError):
                vsix._safe_members(archive, Path(tmp) / "dest")

    def test_ordinary_archive_is_accepted(self):
        with TemporaryDirectory() as tmp:
            archive_path = _fake_vsix(Path(tmp) / "ok.vsix")
            with zipfile.ZipFile(archive_path) as archive:
                members = vsix._safe_members(archive, Path(tmp) / "dest")
            self.assertEqual(len(members), 2)

    def test_foreign_download_host_is_rejected(self):
        with self.assertRaises(vsix.VsixError):
            vsix._check_download_url("https://example.invalid/payload.vsix")
        with self.assertRaises(vsix.VsixError):
            vsix._check_download_url("http://open-vsx.org/payload.vsix")
        vsix._check_download_url("https://open-vsx.org/api/x/y/1/file/x.vsix")

    def test_bad_slug_is_rejected(self):
        for slug in ("nope", "../../etc/passwd", "a/b/c", ""):
            with self.subTest(slug=slug), self.assertRaises(vsix.VsixError):
                vsix._check_slug(slug)


class EntrypointTest(unittest.TestCase):
    def test_missing_path_falls_back_to_a_search(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            moved = root / "extension" / "dist" / "node" / "server.js"
            moved.parent.mkdir(parents=True)
            moved.write_text("x", encoding="utf-8")
            found = vsix._find_entrypoint(root, "extension/out/server.js")
            self.assertEqual(found, moved)

    def test_absent_server_is_an_error(self):
        with TemporaryDirectory() as tmp, self.assertRaises(vsix.VsixError):
            vsix._find_entrypoint(Path(tmp), "extension/out/server.js")

    def test_a_windows_executable_is_found_from_the_unix_spelling(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "extension" / "bin" / "terraform-ls.exe"
            binary.parent.mkdir(parents=True)
            binary.write_text("x", encoding="utf-8")
            self.assertEqual(
                vsix._find_entrypoint(root, "extension/bin/terraform-ls"), binary
            )


class NativeEntryTest(unittest.TestCase):
    def test_a_compiled_server_is_named_by_absolute_path(self):
        entry = vsix._server_entry(
            {"extensions": [".lua"], "args": [], "root_markers": [".git"]},
            Path("/opt/navin/lua/bin/lua-language-server"),
            runtime="native",
            slug="sumneko/lua",
            version="3.19.1",
        )
        # project_paths wins over the PATH lookup, and an absolute entry there
        # resolves to itself, which is what points navin at the extracted copy.
        self.assertEqual(entry["binary"]["name"], "lua-language-server")
        self.assertEqual(
            entry["binary"]["project_paths"], ["/opt/navin/lua/bin/lua-language-server"]
        )
        self.assertEqual(entry["args"], [])

    def test_the_absolute_path_really_resolves(self):
        from navin.quality.linters import _resolve_binary, tool_argv

        _resolve_binary.cache_clear()
        with TemporaryDirectory() as tmp:
            server = Path(tmp) / "lua-language-server"
            server.write_text("#!/bin/sh\n", encoding="utf-8")
            server.chmod(0o755)
            entry = vsix._server_entry(
                {"extensions": [".lua"], "args": [], "root_markers": [".git"]},
                server,
                runtime="native",
                slug="sumneko/lua",
                version="3.19.1",
            )
            self.assertEqual(tool_argv(entry, Path(tmp) / "project"), [str(server)])
        _resolve_binary.cache_clear()


class InstallTest(unittest.TestCase):
    def _install(self, tmp: str, **kwargs):
        archive = _fake_vsix(Path(tmp) / "fake.vsix")

        def fake_download(url: str, destination: Path) -> None:
            destination.write_bytes(archive.read_bytes())

        with (
            patch.object(vsix, "resolve", return_value=("https://open-vsx.org/f.vsix", "1.2.3")),
            patch.object(vsix, "_download", side_effect=fake_download),
            patch.object(vsix.shutil, "which", return_value="/usr/bin/node"),
        ):
            return vsix.install(
                "demo",
                slug="acme/demo",
                entrypoint="extension/server/out/server.js",
                lsp={
                    "languages": ["demo"],
                    "extensions": [".demo"],
                    "args": ["--stdio"],
                    "root_markers": [".git"],
                    "language_ids": {".demo": "demo"},
                    "priority": 20,
                },
                **kwargs,
            )

    def test_install_writes_a_launchable_entry(self):
        with TemporaryDirectory() as tmp:
            result = self._install(tmp)
        self.assertEqual(result["server"], "demo")
        self.assertEqual(result["version"], "1.2.3")
        self.assertTrue(Path(result["path"]).is_file())

        table = manager_mod._read_table()
        self.assertIn("demo", table)
        entry = table["demo"]
        self.assertEqual(entry["binary"], {"name": "node"})
        self.assertEqual(entry["args"], [result["path"], "--stdio"])
        self.assertEqual(entry["language_ids"], {".demo": "demo"})

    def test_installed_server_is_routed_for_its_suffix(self):
        with TemporaryDirectory() as tmp:
            self._install(tmp)
        self.assertIn("demo", [name for name, _ in manager_mod.servers_for(".demo")])

    def test_install_is_reported_as_installed(self):
        with TemporaryDirectory() as tmp:
            self._install(tmp)
        self.assertIn("demo", vsix.installed())

    def test_reinstall_replaces_the_previous_entry(self):
        with TemporaryDirectory() as tmp:
            self._install(tmp)
            self._install(tmp)
        self.assertEqual(len(vsix.installed()), 1)

    def test_uninstall_removes_entry_and_files(self):
        with TemporaryDirectory() as tmp:
            result = self._install(tmp)
            self.assertTrue(vsix.uninstall("demo"))
        self.assertNotIn("demo", manager_mod._read_table())
        self.assertFalse(Path(result["path"]).exists())
        self.assertFalse(vsix.uninstall("demo"))

    def test_hand_written_entries_are_left_alone(self):
        vsix._write_user_table({"servers": {"mine": {"binary": {"name": "x"}}}})
        with self.assertRaises(vsix.VsixError):
            vsix.uninstall("mine")
        with TemporaryDirectory() as tmp, self.assertRaises(vsix.VsixError):
            archive = _fake_vsix(Path(tmp) / "fake.vsix")
            with (
                patch.object(vsix, "resolve", return_value=("https://open-vsx.org/f.vsix", "1")),
                patch.object(
                    vsix,
                    "_download",
                    side_effect=lambda url, dest: dest.write_bytes(archive.read_bytes()),
                ),
                patch.object(vsix.shutil, "which", return_value="/usr/bin/node"),
            ):
                vsix.install(
                    "mine",
                    slug="acme/demo",
                    entrypoint="extension/server/out/server.js",
                    lsp={"extensions": [".x"], "root_markers": [".git"], "language_ids": {".x": "x"}},
                )

    def test_uncatalogued_extension_needs_full_details(self):
        with self.assertRaises(vsix.VsixError):
            vsix.install("unknown-thing")

    def test_invalid_server_name_is_rejected(self):
        with self.assertRaises(vsix.VsixError):
            vsix.install("../evil")


if __name__ == "__main__":
    unittest.main()
