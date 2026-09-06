"""The typescript the build ships, and how tsc finds it.

A project with its own typescript must keep using it: the bundled copy is a
fallback for machines that never ran ``npm install``, not a replacement. These
tests pin that order, and the staging that puts the package in the bundle.
"""

from __future__ import annotations

import json
import sys
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest import mock

from navin import python_runtime
from navin.quality import linters

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packaging"))
import typescript_vendor  # noqa: E402


@contextmanager
def _frozen(meipass: str):
    with (
        mock.patch.object(sys, "frozen", True, create=True),
        mock.patch.object(sys, "_MEIPASS", meipass, create=True),
    ):
        yield


def _stage_fake_typescript(root: Path) -> Path:
    package = root / "tools" / "node_modules" / "typescript"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": "5.9.3"}), encoding="utf-8")
    (package / "bin" / "tsc").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    return package


class BundledNodePackageTest(unittest.TestCase):
    def test_a_source_install_has_nothing_bundled(self):
        self.assertIsNone(python_runtime.bundled_node_package("typescript"))

    def test_a_packaged_build_finds_the_package_it_ships(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _stage_fake_typescript(Path(tmp))
            with _frozen(tmp):
                self.assertEqual(python_runtime.bundled_node_package("typescript"), package)
                self.assertIsNone(python_runtime.bundled_node_package("eslint"))

    def test_a_directory_without_a_manifest_is_not_a_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tools" / "node_modules" / "typescript").mkdir(parents=True)
            with _frozen(tmp):
                self.assertIsNone(python_runtime.bundled_node_package("typescript"))


class TscResolutionTest(unittest.TestCase):
    spec = {
        "binary": {
            "name": "tsc",
            "project_paths": ["node_modules/.bin/tsc"],
            "node_script": "typescript/bin/tsc",
        }
    }

    def setUp(self):
        linters._resolve_binary.cache_clear()

    def tearDown(self):
        linters._resolve_binary.cache_clear()

    @staticmethod
    def _which(name):
        return "/usr/bin/node" if name == "node" else None

    def test_the_bundled_copy_is_used_when_the_machine_has_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = _stage_fake_typescript(Path(tmp))
            project = Path(tmp) / "project"
            project.mkdir()
            with _frozen(tmp), mock.patch.object(linters.shutil, "which", self._which):
                argv = linters.tool_argv(self.spec, project)
            self.assertEqual(argv, ["/usr/bin/node", str(package / "bin" / "tsc")])

    def test_the_project_copy_wins_over_the_bundled_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            _stage_fake_typescript(Path(tmp))
            project = Path(tmp) / "project"
            (project / "node_modules" / ".bin").mkdir(parents=True)
            local = project / "node_modules" / ".bin" / "tsc"
            local.write_text("#!/bin/sh\n", encoding="utf-8")
            local.chmod(0o755)
            with _frozen(tmp), mock.patch.object(linters.shutil, "which", self._which):
                argv = linters.tool_argv(self.spec, project)
            self.assertEqual(argv, [str(local)])

    def test_without_node_the_bundled_copy_is_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _stage_fake_typescript(Path(tmp))
            project = Path(tmp) / "project"
            project.mkdir()
            with (
                _frozen(tmp),
                mock.patch.object(linters.shutil, "which", lambda name: None),
            ):
                self.assertIsNone(linters.tool_argv(self.spec, project))

    def test_a_malformed_node_script_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            _stage_fake_typescript(Path(tmp))
            with _frozen(tmp), mock.patch.object(linters.shutil, "which", self._which):
                self.assertIsNone(linters._bundled_node_argv("typescript"))
                self.assertIsNone(linters._bundled_node_argv("typescript/bin/nope"))

    def test_the_table_points_tsc_at_the_bundled_package(self):
        table = linters._read_json(REPO_ROOT / "navin" / "quality" / "linters.json")
        binary = table["linters"]["tsc"]["binary"]
        self.assertEqual(binary["node_script"], "typescript/bin/tsc")
        self.assertIn("node_modules/.bin/tsc", binary["project_paths"])


class VendorExtractionTest(unittest.TestCase):
    @staticmethod
    def _tarball(path: Path, names: dict[str, bytes]) -> Path:
        with tarfile.open(path, "w:gz") as bundle:
            for name, payload in names.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                bundle.addfile(info, BytesIO(payload))
        return path

    def test_the_package_prefix_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._tarball(
                Path(tmp) / "ts.tgz",
                {
                    "package/package.json": b'{"version": "5.9.3"}',
                    "package/bin/tsc": b"#!/usr/bin/env node\n",
                },
            )
            dest = Path(tmp) / "vendor"
            typescript_vendor._extract(archive, dest)
            self.assertEqual(json.loads((dest / "package.json").read_text())["version"], "5.9.3")
            self.assertTrue((dest / "bin" / "tsc").is_file())

    def test_traversal_outside_the_vendor_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._tarball(Path(tmp) / "ts.tgz", {"package/../escaped": b"owned"})
            with self.assertRaises(typescript_vendor.VendorError):
                typescript_vendor._extract(archive, Path(tmp) / "vendor")

    def test_an_unexpected_layout_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._tarball(Path(tmp) / "ts.tgz", {"elsewhere/package.json": b"{}"})
            with self.assertRaises(typescript_vendor.VendorError):
                typescript_vendor._extract(archive, Path(tmp) / "vendor")

    def test_a_failed_extraction_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._tarball(Path(tmp) / "ts.tgz", {"elsewhere/package.json": b"{}"})
            dest = Path(tmp) / "vendor"
            with self.assertRaises(typescript_vendor.VendorError):
                typescript_vendor._extract(archive, dest)
            self.assertFalse(dest.exists())
            self.assertEqual(list(Path(tmp).glob(".*staging")), [])


class BuildWiringTest(unittest.TestCase):
    """The staging is worthless if the build forgets to call it."""

    def test_the_spec_includes_the_staged_package(self):
        spec = (REPO_ROOT / "packaging" / "pyinstaller" / "navin-onefile.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn("bundle_contents.typescript_data()", spec)

    def test_every_build_stages_and_verifies_it(self):
        for relative in (
            "packaging/linux/build-offline.sh",
            "packaging/macos/build-offline.sh",
            "packaging/windows/build-offline.ps1",
        ):
            script = (REPO_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(script=relative):
                self.assertIn("typescript_vendor.py", script)

    def test_the_manifest_pins_a_checksum(self):
        manifest = typescript_vendor.load_manifest()
        self.assertTrue(manifest["version"])
        self.assertRegex(manifest["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(manifest["url"].startswith("https://registry.npmjs.org/"))


class StagedPackageTest(unittest.TestCase):
    def test_what_is_staged_matches_the_manifest(self):
        staged = typescript_vendor.staged_version()
        if staged is None:
            self.skipTest("typescript is not staged in this checkout")
        self.assertEqual(staged, typescript_vendor.load_manifest()["version"])
        self.assertTrue((typescript_vendor.vendor_path() / "bin" / "tsc").is_file())


if __name__ == "__main__":
    unittest.main()
