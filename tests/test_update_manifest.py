# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The signed manifest has to describe the store the artifacts really sit in.

A manifest is only as good as its URLs: signing a download that 404s produces a
failure that looks deliberate and verified. The publisher writes artifacts under
``v<version>/`` on S3, so that is what the manifest must say.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from navin.update import service

ROOT = Path(__file__).resolve().parents[1]
SIGN = ROOT / "packaging" / "update" / "sign_manifest.py"
BASE_URL = "https://navinagent.s3.eu-north-1.amazonaws.com"


class SignedManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        private = Ed25519PrivateKey.generate()
        self.secret = base64.b64encode(
            private.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        ).decode()
        self.public = private.public_key()

    def _sign(
        self,
        *artifacts: str,
        version: str = "1.0.3",
        prefix: str = "",
        merge_manifest: str = "",
    ) -> dict:
        out = self.tmp / "manifest"
        args = [
            sys.executable,
            str(SIGN),
            "--version",
            version,
            "--base-url",
            BASE_URL,
            "--output-dir",
            str(out),
        ]
        if prefix:
            args += ["--url-prefix", prefix]
        if merge_manifest:
            args += ["--merge-manifest", merge_manifest]
        for artifact in artifacts:
            args += ["--artifact", artifact]
        subprocess.run(
            args,
            check=True,
            env={**os.environ, "UPDATE_SIGNING_KEY": self.secret},
            capture_output=True,
        )
        raw = (out / "manifest.json").read_bytes()
        signature = base64.b64decode((out / "manifest.sig").read_text().strip())
        # Verifying here, not just parsing: the signature covers these exact
        # bytes, and any re-serialisation would break it in production too.
        self.public.verify(signature, raw)
        return json.loads(raw)

    def _artifact(self, name: str) -> str:
        path = self.tmp / name
        path.write_bytes(b"navin" * 100)
        return str(path)

    def test_urls_point_at_the_published_prefix(self):
        appimage = self._artifact("Navin-1.0.3-x86_64.AppImage")
        manifest = self._sign(f"linux-appimage-x64={appimage}", prefix="v1.0.3")
        url = manifest["artifacts"]["linux-appimage-x64"]["url"]
        self.assertEqual(url, f"{BASE_URL}/v1.0.3/Navin-1.0.3-x86_64.AppImage")

    def test_the_service_accepts_what_the_publisher_signs(self):
        appimage = self._artifact("Navin-1.0.3-x86_64.AppImage")
        manifest = self._sign(f"linux-appimage-x64={appimage}", prefix="v1.0.3")
        with (
            mock.patch.object(service, "__version__", "1.0.2"),
            mock.patch.object(service, "_install_kind", return_value="linux-appimage"),
            mock.patch.object(service.platform, "machine", return_value="x86_64"),
        ):
            info = service._release_info(
                manifest, base_url=BASE_URL, skipped_version=""
            )
        assert info is not None
        self.assertTrue(info["available"])
        self.assertEqual(
            info["artifact"]["url"],
            f"{BASE_URL}/v1.0.3/Navin-1.0.3-x86_64.AppImage",
        )
        self.assertEqual(
            info["artifact"]["size"], Path(appimage).stat().st_size
        )

    def test_every_platform_key_the_publisher_emits_is_one_the_app_asks_for(self):
        # The publisher and the installer agree on these names or the update is
        # invisible: the app looks up exactly one key and finds nothing.
        published = {
            "windows-setup-x64": "Navin-Desktop-1.0.3-windows-x64-setup.exe",
            "macos-app-arm64": "Navin-Desktop-1.0.3-macos-arm64.dmg",
            "macos-app-x64": "Navin-Desktop-1.0.3-macos-x64.dmg",
            "linux-appimage-x64": "Navin-1.0.3-x86_64.AppImage",
        }
        manifest = self._sign(
            *(f"{key}={self._artifact(name)}" for key, name in published.items()),
            prefix="v1.0.3",
        )
        for key, machine in (
            ("windows-setup-x64", "AMD64"),
            ("macos-app-arm64", "arm64"),
            ("macos-app-x64", "x86_64"),
            ("linux-appimage-x64", "x86_64"),
        ):
            kind = key.rsplit("-", 1)[0]
            with self.subTest(kind=kind):
                with (
                    mock.patch.object(service, "__version__", "1.0.2"),
                    mock.patch.object(service, "_install_kind", return_value=kind),
                    mock.patch.object(service.platform, "machine", return_value=machine),
                ):
                    info = service._release_info(
                        manifest, base_url=BASE_URL, skipped_version=""
                    )
                assert info is not None, f"{kind} found no artifact in the manifest"
                self.assertEqual(info["installKind"], kind)
                self.assertIn(published[key], info["artifact"]["url"])

    def test_apple_silicon_falls_back_to_x64_when_arm64_is_missing(self):
        x64 = "Navin-Desktop-1.0.3-macos-x64.dmg"
        manifest = self._sign(f"macos-app-x64={self._artifact(x64)}", prefix="v1.0.3")
        with (
            mock.patch.object(service, "__version__", "1.0.2"),
            mock.patch.object(service, "_install_kind", return_value="macos-app"),
            mock.patch.object(service.platform, "machine", return_value="arm64"),
        ):
            info = service._release_info(
                manifest, base_url=BASE_URL, skipped_version=""
            )
        assert info is not None
        self.assertIn(x64, info["artifact"]["url"])

    def test_an_artifact_on_another_host_is_refused(self):
        appimage = self._artifact("Navin-1.0.3-x86_64.AppImage")
        manifest = self._sign(f"linux-appimage-x64={appimage}", prefix="v1.0.3")
        manifest["artifacts"]["linux-appimage-x64"]["url"] = (
            "https://evil.example/Navin.AppImage"
        )
        with (
            mock.patch.object(service, "__version__", "1.0.2"),
            mock.patch.object(service, "_install_kind", return_value="linux-appimage"),
            mock.patch.object(service.platform, "machine", return_value="x86_64"),
        ):
            with self.assertRaises(service.UpdateError):
                service._release_info(manifest, base_url=BASE_URL, skipped_version="")

    def test_the_embedded_public_key_matches_the_signing_key(self):
        # This is the pairing the build relies on: the same secret derives the
        # key baked into the app and signs what the app is asked to trust.
        out = self.tmp / "public_key.txt"
        subprocess.run(
            [
                sys.executable,
                str(SIGN),
                "--public-key-only",
                "--write-public-key",
                str(out),
            ],
            check=True,
            env={**os.environ, "UPDATE_SIGNING_KEY": self.secret},
            capture_output=True,
        )
        embedded = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(out.read_text().strip())
        )
        self.assertEqual(
            embedded.public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            ),
            self.public.public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            ),
        )

    def test_partial_os_publish_keeps_the_other_platforms(self):
        windows = self._artifact("Navin-Desktop-1.0.3-windows-x64-setup.exe")
        first = self._sign(f"windows-setup-x64={windows}", prefix="v1.0.3")
        prev = self.tmp / "prev-manifest.json"
        prev.write_text(json.dumps(first), encoding="utf-8")
        linux = self._artifact("Navin-1.0.3-x86_64.AppImage")
        merged = self._sign(
            f"linux-appimage-x64={linux}",
            prefix="v1.0.3",
            merge_manifest=str(prev),
        )
        self.assertIn("windows-setup-x64", merged["artifacts"])
        self.assertIn("linux-appimage-x64", merged["artifacts"])
        self.assertEqual(
            merged["artifacts"]["windows-setup-x64"]["filename"],
            "Navin-Desktop-1.0.3-windows-x64-setup.exe",
        )

    def test_merge_does_not_keep_artifacts_from_another_version(self):
        windows = self._artifact("Navin-Desktop-1.0.2-windows-x64-setup.exe")
        old = self._sign(
            f"windows-setup-x64={windows}", version="1.0.2", prefix="v1.0.2"
        )
        prev = self.tmp / "old-manifest.json"
        prev.write_text(json.dumps(old), encoding="utf-8")
        linux = self._artifact("Navin-1.0.3-x86_64.AppImage")
        merged = self._sign(
            f"linux-appimage-x64={linux}",
            version="1.0.3",
            prefix="v1.0.3",
            merge_manifest=str(prev),
        )
        self.assertNotIn("windows-setup-x64", merged["artifacts"])
        self.assertIn("linux-appimage-x64", merged["artifacts"])


if __name__ == "__main__":
    unittest.main()
