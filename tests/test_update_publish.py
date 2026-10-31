# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The static latest.json of the Tauri v2 updater, as the publisher writes it.

The plugin reads exactly this shape and installs nothing without a valid
signature, so the generator refuses an unsigned bundle rather than announcing a
release every client would reject.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "update" / "tauri_latest.py"
PUBLISH = ROOT / "scripts" / "publish-os-to-s3.sh"
BASE_URL = "https://navinagent.s3.eu-north-1.amazonaws.com"

_spec = importlib.util.spec_from_file_location("tauri_latest", SCRIPT)
assert _spec and _spec.loader
tauri_latest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tauri_latest)


class Minisign:
    """Enough of minisign to sign like `tauri signer sign` (prehashed, 'ED')."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.key_id = b"\x01\x02\x03\x04\x05\x06\x07\x08"

    @property
    def public(self) -> str:
        raw = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        text = (
            "untrusted comment: minisign public key: 0807060504030201\n"
            + base64.b64encode(b"Ed" + self.key_id + raw).decode()
            + "\n"
        )
        return base64.b64encode(text.encode()).decode()

    def sign(self, data: bytes) -> str:
        sig = self.key.sign(hashlib.blake2b(data, digest_size=64).digest())
        trusted = f"timestamp:1767225600\tfile:{len(data)}"
        global_sig = self.key.sign(sig + trusted.encode())
        text = (
            "untrusted comment: signature from tauri secret key\n"
            + base64.b64encode(b"ED" + self.key_id + sig).decode()
            + "\n"
            + f"trusted comment: {trusted}\n"
            + base64.b64encode(global_sig).decode()
            + "\n"
        )
        return base64.b64encode(text.encode()).decode()


@pytest.fixture
def signer() -> Minisign:
    return Minisign()


def _bundle(tmp_path: Path, name: str, signer: Minisign | None, data: bytes = b"bundle") -> str:
    path = tmp_path / name
    path.write_bytes(data + name.encode())
    if signer is not None:
        # Tauri ends the .sig without a newline; a trailing one must not leak in.
        (tmp_path / f"{name}.sig").write_text(signer.sign(path.read_bytes()) + "\n")
    return str(path)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


ALL = {
    "windows-x86_64": "Navin-Desktop-2.1.0-windows-x64-setup.exe",
    "darwin-aarch64": "Navin-Desktop-2.1.0-macos-arm64.app.tar.gz",
    "darwin-x86_64": "Navin-Desktop-2.1.0-macos-x64.app.tar.gz",
    "linux-x86_64": "Navin-2.1.0-x86_64.AppImage",
}


def test_exact_format(tmp_path, signer):
    out = tmp_path / "latest.json"
    args = ["--version", "2.1.0", "--notes", "Corrections", "--pub-date", "2026-01-31T12:00:00Z",
            "--base-url", BASE_URL, "--url-prefix", "v2.1.0", "--output", str(out),
            "--public-key", signer.public]
    for platform, name in ALL.items():
        args += ["--artifact", f"{platform}={_bundle(tmp_path, name, signer)}"]
    proc = _run(*args)
    assert proc.returncode == 0, proc.stderr
    latest = json.loads(out.read_text())
    assert list(latest) == ["version", "notes", "pub_date", "platforms"]
    assert latest["version"] == "2.1.0"
    assert latest["notes"] == "Corrections"
    assert latest["pub_date"] == "2026-01-31T12:00:00Z"
    assert set(latest["platforms"]) == set(ALL)
    for platform, name in ALL.items():
        entry = latest["platforms"][platform]
        assert set(entry) == {"signature", "url"}
        assert entry["url"] == f"{BASE_URL}/v2.1.0/{name}"
        assert entry["signature"] == (tmp_path / f"{name}.sig").read_text().strip()


def test_default_pub_date_is_rfc3339_utc(tmp_path, signer):
    out = tmp_path / "latest.json"
    exe = _bundle(tmp_path, ALL["windows-x86_64"], signer)
    proc = _run("--version", "2.1.0", "--base-url", BASE_URL, "--output", str(out),
                "--artifact", f"windows-x86_64={exe}")
    assert proc.returncode == 0, proc.stderr
    stamp = json.loads(out.read_text())["pub_date"]
    assert stamp.endswith("Z")
    parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_bundle_without_sig_is_refused(tmp_path, signer):
    out = tmp_path / "latest.json"
    signed = _bundle(tmp_path, ALL["windows-x86_64"], signer)
    unsigned = _bundle(tmp_path, ALL["linux-x86_64"], None)
    proc = _run("--version", "2.1.0", "--base-url", BASE_URL, "--output", str(out),
                "--artifact", f"windows-x86_64={signed}", "--artifact", f"linux-x86_64={unsigned}")
    assert proc.returncode != 0
    assert "Navin-2.1.0-x86_64.AppImage.sig missing" in proc.stderr
    assert "unsigned" in proc.stderr
    assert not out.exists()


def test_empty_sig_is_refused(tmp_path):
    exe = _bundle(tmp_path, ALL["windows-x86_64"], None)
    Path(exe + ".sig").write_text("\n")
    with pytest.raises(SystemExit, match="empty"):
        tauri_latest.build(version="2.1.0", notes="", pub_date="2026-01-31T12:00:00Z",
                           base_url=BASE_URL, url_prefix="v2.1.0",
                           artifacts=[f"windows-x86_64={exe}"])


@pytest.mark.parametrize("platform", ["windows-x64", "macos-arm64", "linux-appimage-x64", "darwin-universal"])
def test_platform_keys_are_tauri_targets(tmp_path, signer, platform):
    exe = _bundle(tmp_path, ALL["windows-x86_64"], signer)
    with pytest.raises(SystemExit, match="unknown Tauri platform"):
        tauri_latest.build(version="2.1.0", notes="", pub_date="2026-01-31T12:00:00Z",
                           base_url=BASE_URL, url_prefix="", artifacts=[f"{platform}={exe}"])


@pytest.mark.parametrize(
    ("platform", "name"),
    [("darwin-aarch64", "Navin-Desktop-2.1.0-macos-arm64.dmg"),
     ("linux-x86_64", "navin_2.1.0_amd64.deb"),
     ("windows-x86_64", "navin-cli-2.1.0-windows-x64.zip")],
)
def test_bundle_type_must_match_the_platform(tmp_path, signer, platform, name):
    path = _bundle(tmp_path, name, signer)
    with pytest.raises(SystemExit, match="not a Tauri updater bundle"):
        tauri_latest.build(version="2.1.0", notes="", pub_date="2026-01-31T12:00:00Z",
                           base_url=BASE_URL, url_prefix="", artifacts=[f"{platform}={path}"])


def test_signature_of_another_file_is_refused(tmp_path, signer):
    exe = _bundle(tmp_path, ALL["windows-x86_64"], signer)
    Path(exe).write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="does not match"):
        tauri_latest.build(version="2.1.0", notes="", pub_date="2026-01-31T12:00:00Z",
                           base_url=BASE_URL, url_prefix="", artifacts=[f"windows-x86_64={exe}"],
                           public_key=signer.public)


def test_signature_from_another_key_is_refused(tmp_path, signer):
    other = Minisign()
    other.key_id = b"\x09" * 8
    exe = _bundle(tmp_path, ALL["windows-x86_64"], other)
    with pytest.raises(SystemExit, match="another key"):
        tauri_latest.build(version="2.1.0", notes="", pub_date="2026-01-31T12:00:00Z",
                           base_url=BASE_URL, url_prefix="", artifacts=[f"windows-x86_64={exe}"],
                           public_key=signer.public)


def test_partial_publish_keeps_other_platforms_of_the_same_version(tmp_path, signer):
    previous = {
        "version": "2.1.0", "notes": "", "pub_date": "2026-01-30T00:00:00Z",
        "platforms": {"windows-x86_64": {"signature": "c2ln", "url": f"{BASE_URL}/v2.1.0/a.exe"},
                      "linux-x86_64": {"signature": "b2xk", "url": f"{BASE_URL}/v2.1.0/old.AppImage"},
                      "darwin-x86_64": {"signature": "", "url": f"{BASE_URL}/v2.1.0/x.app.tar.gz"}},
    }
    appimage = _bundle(tmp_path, ALL["linux-x86_64"], signer)
    latest = tauri_latest.build(version="2.1.0", notes="", pub_date="2026-01-31T12:00:00Z",
                                base_url=BASE_URL, url_prefix="v2.1.0",
                                artifacts=[f"linux-x86_64={appimage}"], previous=previous)
    assert latest["platforms"]["windows-x86_64"]["url"].endswith("/a.exe")
    assert latest["platforms"]["linux-x86_64"]["url"].endswith("/Navin-2.1.0-x86_64.AppImage")
    # An entry without a signature is never carried over.
    assert "darwin-x86_64" not in latest["platforms"]

    other = dict(previous, version="2.0.9")
    latest = tauri_latest.build(version="2.1.0", notes="", pub_date="2026-01-31T12:00:00Z",
                                base_url=BASE_URL, url_prefix="v2.1.0",
                                artifacts=[f"linux-x86_64={appimage}"], previous=other)
    assert set(latest["platforms"]) == {"linux-x86_64"}


def test_publish_script_parses():
    # Every CI runner has bash (Git Bash on Windows): a missing one is a broken
    # environment to report, not a check to drop.
    bash = shutil.which("bash")
    assert bash, "bash is required to syntax-check scripts/publish-os-to-s3.sh"
    # Fed on stdin with LF endings: no Windows path translation, and a CRLF
    # checkout cannot fail the check for a reason the release runner never sees.
    script = PUBLISH.read_bytes().replace(b"\r\n", b"\n")
    proc = subprocess.run([bash, "-n"], input=script, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")


def test_publish_script_documents_and_wires_the_switches():
    text = PUBLISH.read_text(encoding="utf-8")
    header = text.split("set -euo pipefail", 1)[0]
    for name in ("UPDATE_SUSPEND=1", "UPDATE_ROLLOUT=N", "CHANNEL=stable|beta|enterprise"):
        assert name in header
    assert '"${SUSPEND_ARGS[@]}"' in text
    assert "packaging/update/tauri_latest.py" in text
    assert "/tauri/latest.json" in text


def test_build_scripts_ship_the_updater_signature_publish_expects():
    """latest.json is built from <installer>.sig next to each shipped installer."""
    root = Path(__file__).resolve().parents[1]
    windows = (root / "packaging/windows/build-desktop.ps1").read_text(encoding="utf-8")
    linux = (root / "packaging/linux/build-appimage.sh").read_text(encoding="utf-8")
    macos = (root / "packaging/macos/build-desktop.sh").read_text(encoding="utf-8")
    publish = (root / "scripts/publish-os-to-s3.sh").read_text(encoding="utf-8")
    assert 'Copy-PublishedFile $NsisSig "$NsisOut.sig"' in windows
    assert 'cp "$appimage.sig" "$out_dir/$out_name.sig"' in linux
    # macOS: archived from the .app once the engine is embedded and signed,
    # never Tauri's own archive of the engine-less bundle.
    assert macos.index('embed_engine "$app"') < macos.index('tar -czf "$out_dir/$update_name"')
    assert "tauri signer sign" in macos
    # Without the key no signature survives: a stale one would not match.
    assert 'rm -f "$out_dir/$out_name.sig"' in linux
    assert 'rm -f "$out_dir/$update_name" "$out_dir/$update_name.sig"' in macos
    assert 'Remove-Item -LiteralPath "$NsisOut.sig" -Force' in windows
    # The names the build writes are the ones publication looks for.
    for name in ("-windows-x64-setup.exe", "-linux-x64.AppImage", "-macos-arm64.app.tar.gz"):
        assert name in publish
