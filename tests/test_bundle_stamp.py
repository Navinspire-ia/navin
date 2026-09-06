"""The WebUI bundle a packaged target ships is the one the release built.

The desktop apps do not embed the interface in the Tauri binary: they serve it
from the bundle frozen inside their sidecar. Nothing used to check that bundle,
so a build machine with its own dependency resolution, a stale tree or a
half-copied directory all shipped an interface nobody had tested. These locks
cover the two halves of the fix: the stamp written by ``npm run build``, and
the verifier every build and every release job runs against it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# `packaging/` is a script directory, not a package: importing it by name
# would resolve the PyPI `packaging` distribution instead.
sys.path.insert(0, str(ROOT / "packaging"))

from verify_bundle_stamp import (  # noqa: E402
    STAMP_NAME,
    StampError,
    bundle_digest,
    bundle_files,
    verify,
)

STAMP_SCRIPT = ROOT / "webui/scripts/stamp-bundle.mjs"


def _bundle(root: Path, files: dict[str, str] | None = None) -> Path:
    """A minimal stamped bundle, the shape ``vite build`` leaves behind."""
    bundle = root / "navin" / "web" / "dist"
    (bundle / "assets").mkdir(parents=True)
    payload = files or {"index.html": "<html></html>", "assets/app.js": "console.log(1)"}
    for name, text in payload.items():
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    names = bundle_files(bundle)
    (bundle / STAMP_NAME).write_text(
        json.dumps(
            {
                "bundle": bundle_digest(bundle, names),
                "files": len(names),
                "version": "9.9.9",
                "commit": "0" * 40,
            }
        ),
        encoding="utf-8",
    )
    return bundle


class VerifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))

    def test_a_stamped_bundle_passes(self) -> None:
        bundle = _bundle(self.tmp)
        stamp = verify(bundle)
        self.assertEqual(stamp["files"], 2)
        self.assertEqual(stamp["version"], "9.9.9")

    def test_the_bundle_is_found_inside_a_frozen_tree(self) -> None:
        """PyInstaller nests it, and the depth differs per platform."""
        sidecar = self.tmp / "navin-dist" / "_internal"
        sidecar.mkdir(parents=True)
        _bundle(sidecar)
        self.assertEqual(verify(self.tmp / "navin-dist")["files"], 2)

    def test_an_edited_asset_is_caught(self) -> None:
        bundle = _bundle(self.tmp)
        (bundle / "assets" / "app.js").write_text("console.log(2)", encoding="utf-8")
        with self.assertRaises(StampError) as caught:
            verify(bundle)
        self.assertIn("does not match its own stamp", str(caught.exception))

    def test_a_missing_asset_is_caught(self) -> None:
        """A truncated copy is the failure mode of a network or robocopy step."""
        bundle = _bundle(self.tmp)
        (bundle / "assets" / "app.js").unlink()
        with self.assertRaises(StampError) as caught:
            verify(bundle)
        self.assertIn("was modified after it was built", str(caught.exception))

    def test_an_extra_asset_is_caught(self) -> None:
        bundle = _bundle(self.tmp)
        (bundle / "assets" / "injected.js").write_text("evil()", encoding="utf-8")
        with self.assertRaises(StampError):
            verify(bundle)

    def test_a_bundle_from_another_build_is_caught(self) -> None:
        """The point of --expect: this target did not ship what CI built."""
        bundle = _bundle(self.tmp)
        with self.assertRaises(StampError) as caught:
            verify(bundle, expect="f" * 64)
        self.assertIn("is not the bundle this release built", str(caught.exception))
        self.assertIsNotNone(verify(bundle, expect=json.loads(
            (bundle / STAMP_NAME).read_text(encoding="utf-8")
        )["bundle"]))

    def test_an_unstamped_bundle_says_which_failure_it_is(self) -> None:
        bundle = _bundle(self.tmp)
        (bundle / STAMP_NAME).unlink()
        with self.assertRaises(StampError) as caught:
            verify(self.tmp)
        self.assertIn("built by something other than", str(caught.exception))

    def test_no_bundle_at_all_is_not_a_pass(self) -> None:
        (self.tmp / "empty").mkdir()
        with self.assertRaises(StampError):
            verify(self.tmp / "empty")

    def test_the_cli_exit_code_carries_the_verdict(self) -> None:
        bundle = _bundle(self.tmp)
        script = ROOT / "packaging/verify_bundle_stamp.py"
        ok = subprocess.run(
            [sys.executable, str(script), str(bundle)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = subprocess.run(
            [sys.executable, str(script), str(bundle), "--expect", "a" * 64],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn("FAILED", bad.stderr)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class StampScriptTest(unittest.TestCase):
    """The stamp the release depends on, produced by the real script."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        # The script resolves the bundle from its own location, so it needs the
        # repository shape around it, not a copy of the repository.
        (self.tmp / "webui" / "scripts").mkdir(parents=True)
        (self.tmp / "navin" / "web" / "dist" / "assets").mkdir(parents=True)
        shutil.copy(STAMP_SCRIPT, self.tmp / "webui" / "scripts" / STAMP_SCRIPT.name)
        (self.tmp / "webui" / "package-lock.json").write_text("{}", encoding="utf-8")
        self.bundle = self.tmp / "navin" / "web" / "dist"
        (self.bundle / "index.html").write_text("<html></html>", encoding="utf-8")
        (self.bundle / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    def _run(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["node", str(self.tmp / "webui" / "scripts" / STAMP_SCRIPT.name)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def test_what_the_script_writes_is_what_the_verifier_recomputes(self) -> None:
        """Two implementations, one digest: JavaScript writes it, Python checks it."""
        self.assertEqual(self._run().returncode, 0)
        stamp = verify(self.bundle)
        self.assertEqual(stamp["files"], 2)
        self.assertEqual(len(stamp["bundle"]), 64)

    def test_the_digest_ignores_the_stamp_and_only_the_stamp(self) -> None:
        self.assertEqual(self._run().returncode, 0)
        first = json.loads((self.bundle / STAMP_NAME).read_text(encoding="utf-8"))["bundle"]
        # Re-stamping an unchanged bundle must land on the same digest, or the
        # cross-OS comparison in the release workflow would be noise.
        self.assertEqual(self._run().returncode, 0)
        second = json.loads((self.bundle / STAMP_NAME).read_text(encoding="utf-8"))["bundle"]
        self.assertEqual(first, second)
        (self.bundle / "assets" / "app.js").write_text("console.log(2)", encoding="utf-8")
        self.assertEqual(self._run().returncode, 0)
        third = json.loads((self.bundle / STAMP_NAME).read_text(encoding="utf-8"))["bundle"]
        self.assertNotEqual(first, third)

    def test_a_renamed_asset_changes_the_digest(self) -> None:
        """Paths are hashed too: a chunk that moved is a different bundle."""
        self.assertEqual(self._run().returncode, 0)
        before = json.loads((self.bundle / STAMP_NAME).read_text(encoding="utf-8"))["bundle"]
        (self.bundle / "assets" / "app.js").rename(self.bundle / "assets" / "app-2.js")
        self.assertEqual(self._run().returncode, 0)
        after = json.loads((self.bundle / STAMP_NAME).read_text(encoding="utf-8"))["bundle"]
        self.assertNotEqual(before, after)

    def test_an_empty_bundle_fails_the_build(self) -> None:
        for path in (self.bundle / "index.html", self.bundle / "assets" / "app.js"):
            path.unlink()
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no index.html", result.stderr)


class ReleaseWiringTest(unittest.TestCase):
    """The stamp is only worth what the release does with it."""

    def test_every_build_verifies_the_bundle_it_freezes(self) -> None:
        for relative in (
            "packaging/linux/build-offline.sh",
            "packaging/macos/build-offline.sh",
            "packaging/windows/build-offline.ps1",
        ):
            body = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("verify_bundle_stamp.py", body, relative)

    def test_npm_run_build_writes_the_stamp(self) -> None:
        pkg = json.loads((ROOT / "webui/package.json").read_text(encoding="utf-8"))
        self.assertIn("stamp-bundle.mjs", pkg["scripts"]["build"])

    def test_every_release_target_is_pinned_to_one_digest(self) -> None:
        """A target that shipped another bundle must fail the release, per OS."""
        workflow = (ROOT / ".github/workflows/os-release.yml").read_text(encoding="utf-8")
        self.assertIn("bundle: ${{ steps.stamp.outputs.bundle }}", workflow)
        self.assertEqual(
            workflow.count('--expect "${{ needs.webui.outputs.bundle }}"'),
            3,
            "linux, macos and windows must each check the digest",
        )

    def test_the_arm_linux_targets_are_built(self) -> None:
        """build-appimage.sh resolved aarch64 long before any runner did."""
        workflow = (ROOT / ".github/workflows/os-release.yml").read_text(encoding="utf-8")
        self.assertIn("ubuntu-22.04-arm", workflow)
        self.assertIn("os/linux/pacman/${{ matrix.arch }}/", workflow)

    def test_one_version_reaches_every_manifest(self) -> None:
        """webui/package.json sat at 1.0.0 while the rest moved to 1.3.0."""
        script = (ROOT / "scripts/set-version.sh").read_text(encoding="utf-8")
        expected = {
            "desktop/src-tauri/tauri.conf.json",
            "desktop/src-tauri/Cargo.toml",
            "desktop/package.json",
            "desktop-electron/package.json",
            "webui/package.json",
            "pyproject.toml",
            "navin/_version.py",
        }
        for relative in expected:
            self.assertIn(relative, script, relative)
        shipped = json.loads(
            (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )["version"]
        for relative in ("webui/package.json", "desktop/package.json"):
            found = json.loads((ROOT / relative).read_text(encoding="utf-8"))["version"]
            self.assertEqual(found, shipped, relative)

    def test_a_second_lockfile_cannot_come_back(self) -> None:
        self.assertFalse((ROOT / "webui/bun.lock").exists())
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in ("webui/bun.lock", "webui/pnpm-lock.yaml", "webui/yarn.lock"):
            self.assertIn(name, ignored, name)


class DoctorTest(unittest.TestCase):
    """``navin doctor`` names the bundle an installed app actually serves."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.bundle = _bundle(self.tmp)

    def _check(self):
        from unittest import mock

        from navin import diagnostics

        with mock.patch(
            "navin.webui.build.default_webui_dist_dir", return_value=self.bundle
        ):
            return diagnostics.webui_bundle_check()

    def test_it_reports_the_version_and_the_digest(self) -> None:
        check = self._check()
        self.assertTrue(check.ok)
        self.assertIn("9.9.9", check.detail)
        self.assertIn("2 files", check.detail)

    def test_a_tampered_bundle_is_flagged(self) -> None:
        (self.bundle / "assets" / "extra.js").write_text("x", encoding="utf-8")
        check = self._check()
        self.assertFalse(check.ok)
        self.assertIn("stamp says 2", check.detail)

    def test_a_missing_bundle_says_how_to_build_it(self) -> None:
        (self.bundle / "index.html").unlink()
        check = self._check()
        self.assertFalse(check.ok)
        self.assertIn("npm run build", check.hint)

    def test_an_older_bundle_without_a_stamp_is_not_an_error(self) -> None:
        """Installations predating the stamp must not read as broken."""
        (self.bundle / STAMP_NAME).unlink()
        check = self._check()
        self.assertTrue(check.ok)
        self.assertIn("unstamped", check.detail)


if __name__ == "__main__":
    unittest.main()
