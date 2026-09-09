# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The release workflow has to call scripts that exist, and has to run them.

A packaging script that gets renamed or moved breaks the release build only when
someone tags a version, which is the worst moment to find out. These checks read
the workflow the same way the runner does.

Distribution 100 % Tauri : chaque job produit uniquement l'app desktop native
(NSIS + MSI sur Windows, DMG sur macOS, AppImage/.deb/.rpm sur Linux).
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "os-release.yml"

# `sh packaging/foo.sh`, `python packaging/foo.py`, `-File packaging/foo.ps1`.
SCRIPT_CALL = re.compile(r"(?:sh|python3?|-File)\s+(packaging[/\\][\w./\\-]+\.(?:sh|py|ps1))")


def _run_steps(job: dict) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if step.get("run")]


class ReleaseWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.jobs = self.workflow["jobs"]

    def test_every_packaging_script_it_calls_is_present(self):
        called = set()
        for job in self.jobs.values():
            for command in _run_steps(job):
                called.update(
                    match.replace("\\", "/") for match in SCRIPT_CALL.findall(command)
                )
        self.assertTrue(called, "no packaging scripts found: the pattern stopped matching")
        for relative in sorted(called):
            self.assertTrue((REPO_ROOT / relative).is_file(), relative)

    def test_every_job_builds_the_tauri_desktop_app(self):
        """The only distributed product is the Tauri desktop app per platform."""
        expected = {
            "linux": "packaging/linux/build-appimage.sh",
            "macos": "packaging/macos/build-desktop.sh",
            "windows": "build-desktop.ps1",
        }
        for job_name, script in expected.items():
            commands = " ".join(_run_steps(self.jobs[job_name]))
            self.assertIn(script, commands, f"{job_name} never builds the desktop app")

    def test_the_linux_job_smoke_tests_its_sidecar(self):
        """--version only proves the binary links; this proves the engine boots."""
        commands = " ".join(_run_steps(self.jobs["linux"]))
        self.assertIn("packaging/smoke-test.sh", commands)

    def test_every_release_sidecar_gets_a_runtime_smoke_test(self):
        commands = {
            name: " ".join(_run_steps(self.jobs[name]))
            for name in ("linux", "macos", "windows")
        }
        self.assertIn("packaging/smoke-test.sh", commands["linux"])
        self.assertIn("packaging/smoke-test.sh", commands["macos"])
        self.assertIn(r"packaging\windows\smoke-test.ps1", commands["windows"])
        self.assertIn(r"NavinBuild\windows\navin-dist\navin.exe", commands["windows"])

    def test_smoke_tests_cover_connectivity_without_secrets_or_gui(self):
        posix = (REPO_ROOT / "packaging" / "smoke-test.sh").read_text(encoding="utf-8")
        windows = (
            REPO_ROOT / "packaging" / "windows" / "smoke-test.ps1"
        ).read_text(encoding="utf-8")
        for text in (posix, windows):
            self.assertIn("recommended URL", text)
            self.assertIn("gateway port", text)
            self.assertIn("/health", text)
            self.assertIn("ffmpeg", text)
            self.assertIn("chromium", text)
            self.assertNotIn("AZURE_CLIENT_SECRET", text)
        self.assertIn("--no-open", posix)
        self.assertIn('"--no-open"', windows)

    def test_no_job_calls_a_removed_packaging_chain(self):
        """Inno Setup, PyInstaller deb/rpm and the drag-and-drop DMG are gone."""
        forbidden = (
            "build-deb.sh",
            "build-rpm.sh",
            "build-dmg.sh",
            "navin-installer.iss",
            "installer-smoke-test",
        )
        for job_name, job in self.jobs.items():
            commands = " ".join(_run_steps(job))
            for needle in forbidden:
                self.assertNotIn(needle, commands, f"{job_name} still calls {needle}")

    def test_the_macos_image_can_actually_be_installed(self):
        """The DMG must carry the app AND an /Applications alias.

        Packing `hdiutil create -srcfolder <app>` produced an image holding
        Navin.app alone: the download page says "drag Navin into Applications"
        but there was nothing to drop it onto, so testers ran the app off the
        read-only volume and nothing was ever installed on their disk.
        """
        script = (REPO_ROOT / "packaging" / "macos" / "build-desktop.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("ln -s /Applications", script)
        self.assertIn('hdiutil create -volname "Navin" -srcfolder "$stage"', script)

    def test_the_macos_sidecar_is_version_stamped(self):
        """The sidecar build must stamp the version the frozen binary reports.

        Without the stamp, build-desktop.sh can only test that a sidecar tree
        EXISTS, and a tree left by a previous release (failed Rosetta x64
        pass, direct build-desktop.sh run) was silently bundled into a DMG
        named with the new version - old webui, old backend, wrong version.
        """
        script = (REPO_ROOT / "packaging" / "macos" / "build-offline.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('navin/_version.py', script)
        self.assertIn('> "$work/output/navin-dist/VERSION"', script)
        # A failed x64 rebuild must not leave the previous release's tree
        # behind, or the freshness gate is the only thing standing.
        self.assertIn('rm -rf "$root/os/macos/x64/navin-dist"', script)

    def test_the_macos_desktop_build_refuses_a_stale_sidecar(self):
        """build-desktop.sh must gate on the stamp, before AND after packing.

        Before: a sidecar whose stamp does not match tauri.conf.json's version
        is skipped (with the arch counted as skipped), never embedded. After:
        the mounted DMG is re-checked - engine stamp and app Info.plist must
        both carry the expected version, or the build fails instead of
        shipping a mislabelled artifact.
        """
        script = (REPO_ROOT / "packaging" / "macos" / "build-desktop.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('$sidecar_dir/VERSION', script)
        self.assertIn("is stale or unstamped", script)
        self.assertIn("shipped_engine_version", script)
        self.assertIn("CFBundleShortVersionString", script)
        # The stale path must skip, not embed: the skip branch returns before
        # any copy into src-tauri/binaries happens.
        stale_gate = script.find("is stale or unstamped")
        first_copy = script.find('cp -R "$sidecar_dir"')
        self.assertGreater(first_copy, stale_gate, "freshness gate must run before the sidecar copy")

    def test_the_windows_sidecar_is_version_stamped(self):
        """Same guarantee as macOS: the Windows sidecar carries its version.

        build-desktop.ps1 can reuse a staged sidecar (NAVIN_SKIP_SIDECAR_BUILD=1
        shell iteration); without the stamp that reuse silently shipped an old
        backend and webui inside an installer named with the new version.
        """
        script = (
            REPO_ROOT / "packaging" / "windows" / "build-offline.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(r"navin\_version.py", script)
        self.assertIn('Join-Path $SidecarDir "VERSION"', script)

    def test_the_windows_desktop_build_refuses_a_stale_sidecar(self):
        """build-desktop.ps1 must gate on the stamp before and after embedding.

        Before: a sidecar whose stamp does not match tauri.conf.json's version
        fails the build (never embedded). After: the tree robocopied into
        src-tauri\\binaries - the one the Tauri bundler actually packages - is
        re-checked, so a partial mirror cannot ship a mislabelled build.
        """
        script = (
            REPO_ROOT / "packaging" / "windows" / "build-desktop.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("is stale or unstamped", script)
        self.assertIn("does not match installer version", script)
        # The freshness gate must run before the sidecar is copied for embedding.
        stale_gate = script.find("is stale or unstamped")
        embed_copy = script.find('robocopy $SidecarDir (Join-Path $Binaries "navin-dist")')
        self.assertGreater(stale_gate, 0)
        self.assertGreater(
            embed_copy, stale_gate, "freshness gate must run before the sidecar copy"
        )

    def test_windows_tauri_sign_command_uses_full_powershell_path(self):
        """Tauri's bundler PATH from WSL interop cannot spawn bare `powershell`.

        That showed up as `failed to run powershell` after Azure had already
        signed the sidecar. The generated signCommand must call powershell.exe
        by its System32 path and run a local (non-UNC) copy of sign-windows.ps1.
        """
        script = (
            REPO_ROOT / "packaging" / "windows" / "build-desktop.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            r'System32\WindowsPowerShell\v1.0\powershell.exe',
            script,
        )
        self.assertNotIn('cmd = "powershell"', script)
        self.assertIn(r'NavinBuild\sign', script)
        self.assertIn("signing.env", script)

    def test_windows_sign_script_skips_helper_dlls(self):
        """Tauri walks every .exe/.dll in the bundle, including PyInstaller helpers.

        Azure Artifact Signing of winpty.dll (after winpty-agent.exe succeeded)
        aborted `make windows` with `failed to run powershell.exe`. Helper DLLs
        must exit 0 without calling Azure; the app exe, sidecar, MSI and NSIS
        setup must still be signed.
        """
        sign = (
            REPO_ROOT / "packaging" / "windows" / "sign-windows.ps1"
        ).read_text(encoding="utf-8")
        desktop = (
            REPO_ROOT / "packaging" / "windows" / "build-desktop.ps1"
        ).read_text(encoding="utf-8")
        skip = sign.find('if ($Ext -eq ".dll")')
        # The Azure CLI runs through Invoke-Logged so its output lands in the
        # sign log Tauri would otherwise swallow.
        azure_call = sign.find("Invoke-Logged $SigningCli")
        self.assertGreater(skip, 0, "sign-windows.ps1 must skip helper DLLs")
        self.assertGreater(
            azure_call,
            skip,
            "DLL skip must run before Azure Artifact Signing",
        )
        self.assertIn("skip helper DLL", sign)
        self.assertIn("exit 0", sign[skip : skip + 400])
        self.assertIn("$SidecarExe", desktop)
        self.assertIn("NavinUpdater.exe", desktop)
        self.assertIn("$MsiOut", desktop)
        self.assertIn("$NsisOut", desktop)
        self.assertIn("verify-windows-signatures.ps1", desktop)
        # powershell -File cannot bind -Path @(a, b); the second file becomes
        # a positional argument and aborts a successful sign (Navin 2.0.1).
        self.assertNotIn("-File $VerifyScript -Path @", desktop)
        self.assertIn("foreach ($Artifact in @($MsiOut, $NsisOut))", desktop)
        verify = (
            REPO_ROOT / "packaging" / "windows" / "verify-windows-signatures.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Get-AuthenticodeSignature", verify)
        self.assertIn("Status, StatusMessage, SignerCertificate", verify)
        self.assertIn("SmartScreen", verify)
        self.assertIn("NavinUpdater.exe", verify)

    def test_the_linux_sidecar_is_version_stamped_and_gated(self):
        """Same guarantee as macOS and Windows for the Linux bundles.

        build-appimage.sh embeds os/linux/bin/<arch>/navin-dist as-is; without
        the stamp and gate, a tree left by a previous `make linux` shipped an
        old backend and webui inside an AppImage/deb/rpm named with the new
        version.
        """
        offline = (
            REPO_ROOT / "packaging" / "linux" / "build-offline.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("navin/_version.py", offline)
        self.assertIn('> "$work/output/navin-dist/VERSION"', offline)

        appimage = (
            REPO_ROOT / "packaging" / "linux" / "build-appimage.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("is stale or unstamped", appimage)
        self.assertIn("does not match bundle version", appimage)
        # The freshness gate must run before the sidecar is copied for embedding.
        stale_gate = appimage.find("is stale or unstamped")
        embed_copy = appimage.find('cp -R "$sidecar" desktop/src-tauri/binaries/navin-dist')
        self.assertGreater(stale_gate, 0)
        self.assertGreater(
            embed_copy, stale_gate, "freshness gate must run before the sidecar copy"
        )

    def test_the_packaging_tree_is_actually_committed(self):
        """Present on disk is not enough: CI builds what git carries.

        The generic Python .gitignore ignores *.spec for the specs PyInstaller
        generates, which silently swallowed the hand-written one. Every local
        build worked and every CI desktop build died with "Spec file not
        found", a difference invisible until the workflow first ran.
        """
        listing = subprocess.run(  # noqa: S603
            ["git", "ls-files", "packaging/"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if listing.returncode != 0 or not listing.stdout.strip():
            self.skipTest("not a git checkout")
        tracked = set(listing.stdout.split())
        required = [
            "packaging/pyinstaller/navin-onefile.spec",
            "packaging/pyinstaller/bundle_contents.py",
            "packaging/pyinstaller/navin_entry.py",
            "packaging/macos/navin.icns",
            "packaging/macos/build-desktop.sh",
            "packaging/linux/build-appimage.sh",
            "packaging/windows/build-desktop.ps1",
        ]
        for relative in required:
            self.assertIn(relative, tracked, f"{relative} is ignored or untracked")

    def test_local_os_installers_are_ignored(self):
        """Release binaries stay out of git, whatever the version or folder.

        The official staging directory is os/ (already ignored). Copies dropped
        in windows/, macos/ or linux/ at the repo root, and any
        Navin-Desktop-<version>-... artifact, must match the same rules.
        """
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for folder in ("/windows/", "/macos/", "/linux/"):
            self.assertIn(folder, gitignore, f"{folder} missing from .gitignore")
        for pattern in ("*.exe", "*.msi", "*.dmg", "*.AppImage", "*.deb", "*.rpm"):
            self.assertIn(pattern, gitignore, f"{pattern} missing from .gitignore")
        listing = subprocess.run(  # noqa: S603
            ["git", "ls-files", "windows/", "macos/", "linux/"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if listing.returncode != 0:
            self.skipTest("not a git checkout")
        tracked = [line for line in listing.stdout.splitlines() if line.strip()]
        self.assertEqual(tracked, [], f"installers still tracked: {tracked}")
        samples = [
            "windows/x64/Navin-Desktop-9.9.9-windows-x64-setup.exe",
            "macos/arm64/Navin-Desktop-2.0.0-macos-arm64.dmg",
            "linux/appimage/x64/Navin-1.2.3-linux-x64.AppImage",
            "Navin-Desktop-4.0.0-windows-x64-setup.exe",
            "navin_4.0.0_amd64.deb",
            "navin-4.0.0-1.x86_64.rpm",
        ]
        for sample in samples:
            checked = subprocess.run(  # noqa: S603
                ["git", "check-ignore", "-q", sample],  # noqa: S607
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, f"not ignored: {sample}")


if __name__ == "__main__":
    unittest.main()
