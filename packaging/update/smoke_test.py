#!/usr/bin/env python3
"""Local integration smoke test for signed update manifests and downloads."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _newer_than_current() -> str:
    """A version this build would accept, whatever this build happens to be.

    Hardcoding one means the test stops testing anything the day the app
    overtakes it: the manifest is then older than the running version and
    "no update available" reads as a pass.
    """
    from navin import __version__

    parts = [int(part) for part in __version__.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(part) for part in parts)


def main() -> None:
    newer = _newer_than_current()
    with tempfile.TemporaryDirectory(prefix="navin-update-test-") as raw:
        root = Path(raw)
        stable = root / "stable"
        stable.mkdir()
        artifact = root / "navin-linux-x64"
        artifact.write_bytes(b"verified-navin-update\n")
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        os.environ["NAVIN_UPDATE_PUBLIC_KEY"] = base64.b64encode(public).decode()
        os.environ["NAVIN_UPDATE_ALLOW_HTTP"] = "1"
        os.environ["NAVIN_INSTALL_KIND"] = "linux"
        os.environ["HOME"] = str(root / "home")

        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        server.directory = str(root)
        # SimpleHTTPRequestHandler reads cwd on older Python versions.
        old_cwd = Path.cwd()
        os.chdir(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            os.environ["NAVIN_UPDATE_BASE_URL"] = base_url
            manifest = {}

            def publish(
                version: str = newer,
                rollout: int = 100,
                digest: str | None = None,
            ) -> bytes:
                manifest.clear()
                manifest.update({
                    "schemaVersion": 1,
                    "channel": "stable",
                    "version": version,
                    "minimumVersion": "0.0.1",
                    "rollout": rollout,
                    "notes": "Updater smoke test",
                    "artifacts": {
                        "linux-x64": {
                            "url": f"{base_url}/{artifact.name}",
                            "size": artifact.stat().st_size,
                            "sha256": digest or hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        }
                    },
                })
                raw = json.dumps(
                    manifest,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                (stable / "manifest.json").write_bytes(raw)
                (stable / "manifest.sig").write_text(
                    base64.b64encode(private.sign(raw)).decode() + "\n"
                )
                return raw

            publish()

            from navin.update import service

            service._CACHE = (0.0, None)
            info = service.check_for_update(force=True)
            assert info["available"] and info["latestVersion"] == newer, info
            downloaded = service.download_update()
            assert Path(downloaded["path"]).read_bytes() == artifact.read_bytes()

            publish(version="0.0.1")
            service._CACHE = (0.0, None)
            assert not service.check_for_update(force=True)["available"]

            publish(rollout=0)
            service._CACHE = (0.0, None)
            assert not service.check_for_update(force=True)["available"]

            publish(digest="0" * 64)
            service._CACHE = (0.0, None)
            service.check_for_update(force=True)
            try:
                service.download_update()
            except service.UpdateError as exc:
                assert "checksum" in str(exc)
            else:
                raise AssertionError("artifact with a bad hash was accepted")

            publish()
            (stable / "manifest.sig").write_text(base64.b64encode(b"x" * 64).decode())
            service._CACHE = (0.0, None)
            try:
                service.check_for_update(force=True)
            except service.UpdateError as exc:
                assert "signature" in str(exc)
            else:
                raise AssertionError("tampered manifest signature was accepted")
        finally:
            server.shutdown()
            os.chdir(old_cwd)
    print("Signed update smoke test passed.")


if __name__ == "__main__":
    main()
