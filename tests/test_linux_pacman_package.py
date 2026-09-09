# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Arch pacman package built from a Tauri .deb payload.

The packer is a stdlib script: it parses GNU ar, restages usr/..., and writes
.pkg.tar.zst with tar/zstd. These checks build a tiny fake .deb so the Linux
release job does not have to run make appimage to prove the conversion.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKER = REPO_ROOT / "packaging" / "linux" / "pack_pacman.py"
HAVE_TAR = shutil.which("tar") is not None
HAVE_ZSTD = shutil.which("zstd") is not None
SKIP_ARCHIVE = "tar and zstd are required to build and read .pkg.tar.zst"

DEPENDS = (
    "webkit2gtk-4.1",
    "gtk3",
    "libayatana-appindicator",
    "hicolor-icon-theme",
    "adwaita-icon-theme",
    "noto-fonts-emoji",
)


def _gnu_ar(members: list[tuple[str, bytes]]) -> bytes:
    parts = [b"!<arch>\n"]
    for name, payload in members:
        header_name = name.encode("ascii")
        if len(header_name) > 16:
            raise ValueError(name)
        header = (
            header_name.ljust(16)
            + b"0".ljust(12)
            + b"0".ljust(6)
            + b"0".ljust(6)
            + b"100644".ljust(8)
            + str(len(payload)).encode("ascii").ljust(10)
            + b"`\n"
        )
        parts.append(header)
        parts.append(payload)
        if len(payload) % 2:
            parts.append(b"\n")
    return b"".join(parts)


def _data_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mtime = 0
            executable = name.endswith("navin") or name.endswith("navin-desktop")
            info.mode = 0o755 if executable else 0o644
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _fake_deb(files: dict[str, bytes]) -> bytes:
    return _gnu_ar(
        [
            ("debian-binary", b"2.0\n"),
            ("data.tar.gz", _data_tar_gz(files)),
        ]
    )


def _engine_files(version: str = "1.2.3") -> dict[str, bytes]:
    return {
        "usr/bin/navin-desktop": b"#!/bin/sh\necho desktop\n",
        "usr/lib/Navin/navin-dist/navin": b"#!/bin/sh\necho engine\n",
        "usr/lib/Navin/navin-dist/VERSION": f"{version}\n".encode("ascii"),
    }


def _run_packer(*, deb: Path, version: str, arch: str, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PACKER),
            "--deb",
            str(deb),
            "--version",
            version,
            "--arch",
            arch,
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _open_pkg(path: Path) -> tarfile.TarFile:
    try:
        return tarfile.open(path, mode="r:zst")
    except (tarfile.TarError, ValueError, OSError):
        proc = subprocess.run(
            ["zstd", "-d", "-c", "-q", str(path)],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise
        return tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:")


def _tar_name(name: str) -> str:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name


def _pkginfo_fields(text: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(" = ")
        if not sep:
            continue
        fields.setdefault(key, []).append(value)
    return fields


class PackPacmanHelpTest(unittest.TestCase):
    def test_help_exits_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(PACKER), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--deb", proc.stdout)
        self.assertIn("--version", proc.stdout)
        self.assertIn("--arch", proc.stdout)
        self.assertIn("--out", proc.stdout)


class PackPacmanMissingEngineTest(unittest.TestCase):
    def test_missing_engine_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-pacman-missing-") as tmp:
            root = Path(tmp)
            deb = root / "navin.deb"
            deb.write_bytes(
                _fake_deb(
                    {
                        "usr/bin/navin-desktop": b"#!/bin/sh\necho desktop\n",
                        "usr/lib/Navin/VERSION": b"1.2.3\n",
                    }
                )
            )
            out = root / "nested" / "navin-1.2.3-1-x86_64.pkg.tar.zst"
            proc = _run_packer(deb=deb, version="1.2.3", arch="x86_64", out=out)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("navin-dist/navin", proc.stderr)
            self.assertFalse(out.exists())


@unittest.skipUnless(HAVE_TAR and HAVE_ZSTD, SKIP_ARCHIVE)
class PackPacmanPackageTest(unittest.TestCase):
    def test_pkginfo_and_engine_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-pacman-ok-") as tmp:
            root = Path(tmp)
            deb = root / "navin.deb"
            deb.write_bytes(_fake_deb(_engine_files("1.2.3")))
            out = root / "os" / "linux" / "pacman" / "x64" / "navin-1.2.3-1-x86_64.pkg.tar.zst"
            proc = _run_packer(deb=deb, version="1.2.3", arch="x86_64", out=out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.is_file())
            with _open_pkg(out) as tf:
                names = [_tar_name(member.name) for member in tf.getmembers()]
                pkginfo_member = next(
                    member
                    for member in tf.getmembers()
                    if _tar_name(member.name) == ".PKGINFO"
                )
                handle = tf.extractfile(pkginfo_member)
                assert handle is not None
                pkginfo = handle.read().decode("utf-8")

        fields = _pkginfo_fields(pkginfo)
        self.assertEqual(fields.get("pkgname"), ["navin"])
        self.assertEqual(fields.get("pkgver"), ["1.2.3-1"])
        self.assertEqual(fields.get("arch"), ["x86_64"])
        self.assertEqual(fields.get("pkgdesc"), ["Navin AI workbench"])
        self.assertEqual(fields.get("url"), ["https://navin.live"])
        self.assertEqual(fields.get("license"), ["LicenseRef-Navin"])
        self.assertEqual(fields.get("depend"), list(DEPENDS))
        self.assertTrue(
            any(name.endswith("navin-dist/navin") for name in names),
            names,
        )
        self.assertTrue(any(name.endswith("usr/bin/navin-desktop") or name == "usr/bin/navin-desktop" for name in names), names)
        self.assertEqual(names[0], ".PKGINFO", names)
        self.assertIn(".PKGINFO", names)
        self.assertIn(".INSTALL", names)


if __name__ == "__main__":
    unittest.main()
