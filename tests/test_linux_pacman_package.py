# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Arch pacman package built from a Tauri .deb payload.

The packer is a stdlib script: it parses GNU ar, restages usr/..., and writes
.pkg.tar.zst with tar/zstd. These checks build a tiny fake .deb so the Linux
release job does not have to run make appimage to prove the conversion.
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKER = REPO_ROOT / "packaging" / "linux" / "pack_pacman.py"
HAVE_TAR = shutil.which("tar") is not None
HAVE_ZSTD = shutil.which("zstd") is not None
SKIP_ARCHIVE = "tar and zstd are required to build and read .pkg.tar.zst"
# Where the frozen engine lands in the .deb payload (PyInstaller one-dir).
GUARD_MEMBER = "usr/lib/Navin/navin-dist/_internal/navin/utils/wsl_oom_guard.sh"
GUARD_PATH = "/" + GUARD_MEMBER
GUARD_SOURCE = REPO_ROOT / "navin" / "utils" / "wsl_oom_guard.sh"
OLD_INSTALL = """\
post_install() {
    update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || true
    gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true
}

post_upgrade() {
    post_install
}
"""
GUARD_INSTALL = f"""\
post_install() {{
    update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || true
    gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true
    if [ -f '{GUARD_PATH}' ]; then
        sh '{GUARD_PATH}' install
    fi
}}

post_upgrade() {{
    post_install
}}

pre_remove() {{
    if [ -f '{GUARD_PATH}' ]; then
        sh '{GUARD_PATH}' remove
    fi
}}
"""


def _load_packer():
    spec = importlib.util.spec_from_file_location("navin_pack_pacman", PACKER)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {PACKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pack_pacman = _load_packer()

DEPENDS = (
    "webkit2gtk-4.1",
    "gtk3",
    "libayatana-appindicator",
    "hicolor-icon-theme",
    "adwaita-icon-theme",
    "noto-fonts-emoji",
    "gnome-keyring",
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


def _guard_files(version: str = "1.2.3") -> dict[str, bytes]:
    files = _engine_files(version)
    files[GUARD_MEMBER] = GUARD_SOURCE.read_bytes()
    return files


def _stage_deb(blob: bytes, dest: Path) -> None:
    """Restage a fake .deb payload the way pack_deb does."""
    data = next(
        (name, payload)
        for name, payload in pack_pacman.iter_ar_members(blob)
        if name.startswith("data.tar")
    )
    tf = pack_pacman._open_data_tar(*data)
    try:
        pack_pacman._stage_tar(tf, dest)
    finally:
        tf.close()


def _bash_n(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-n"], input=text, capture_output=True, text=True, check=False
    )


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


class PackPacmanDataTarTest(unittest.TestCase):
    """The .deb payload is read whatever its compression, and staged with its modes."""

    @staticmethod
    def _tar(mode: str) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode=mode) as tf:
            info = tarfile.TarInfo(name="usr/lib/Navin/navin-dist/navin")
            info.size = 3
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(b"run"))
        return buf.getvalue()

    def test_every_compression_opens(self) -> None:
        for name, mode in (("data.tar.gz", "w:gz"), ("data.tar.xz", "w:xz"), ("data.tar.bz2", "w:bz2"), ("data.tar", "w:")):
            with self.subTest(name=name):
                tf = pack_pacman._open_data_tar(name, self._tar(mode))
                try:
                    self.assertEqual(tf.getnames(), ["usr/lib/Navin/navin-dist/navin"])
                finally:
                    tf.close()

    def test_a_payload_named_after_another_compression_still_opens(self) -> None:
        tf = pack_pacman._open_data_tar("data.tar.xz", self._tar("w:gz"))
        try:
            self.assertEqual(tf.getnames(), ["usr/lib/Navin/navin-dist/navin"])
        finally:
            tf.close()

    def test_unknown_or_broken_payloads_are_pack_errors(self) -> None:
        with self.assertRaisesRegex(pack_pacman.PackError, "unsupported data archive data.cpio"):
            pack_pacman._open_data_tar("data.cpio", b"")
        with self.assertRaisesRegex(pack_pacman.PackError, "cannot read data.tar.gz"):
            pack_pacman._open_data_tar("data.tar.gz", b"not a tar archive")

    def test_a_mode_that_cannot_be_set_stops_the_build(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-pacman-chmod-") as tmp:
            tf = pack_pacman._open_data_tar("data.tar.gz", self._tar("w:gz"))
            denied = PermissionError(1, "Operation not permitted")
            try:
                with mock.patch.object(Path, "chmod", side_effect=denied):
                    with self.assertRaisesRegex(
                        pack_pacman.PackError,
                        "cannot set mode 755 on usr/lib/Navin/navin-dist/navin: .*Operation not permitted",
                    ):
                        pack_pacman._stage_tar(tf, Path(tmp))
            finally:
                tf.close()

    def test_staged_files_keep_their_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-pacman-mode-") as tmp:
            tf = pack_pacman._open_data_tar("data.tar.gz", self._tar("w:gz"))
            try:
                pack_pacman._stage_tar(tf, Path(tmp))
            finally:
                tf.close()
            staged = Path(tmp) / "usr/lib/Navin/navin-dist/navin"
            self.assertEqual(staged.stat().st_mode & 0o777, 0o755)


class PackPacmanOomGuardInstallTest(unittest.TestCase):
    """pacman runs .INSTALL as root: it carries the WSL OOM guard when shipped."""

    def test_payload_with_guard_runs_it_at_install_upgrade_and_removal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-pacman-guard-") as tmp:
            stage = Path(tmp) / "pkg"
            stage.mkdir()
            _stage_deb(_fake_deb(_guard_files()), stage)
            guard = pack_pacman.find_oom_guard(stage)
            pack_pacman._write_install(stage)
            written = (stage / ".INSTALL").read_text(encoding="utf-8")
        self.assertEqual(guard, GUARD_PATH)
        self.assertEqual(written, GUARD_INSTALL)
        self.assertEqual(pack_pacman.install_script(GUARD_PATH), GUARD_INSTALL)
        checked = _bash_n(written)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_payload_without_guard_keeps_the_old_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-pacman-noguard-") as tmp:
            stage = Path(tmp) / "pkg"
            stage.mkdir()
            _stage_deb(_fake_deb(_engine_files()), stage)
            guard = pack_pacman.find_oom_guard(stage)
            pack_pacman._write_install(stage)
            written = (stage / ".INSTALL").read_text(encoding="utf-8")
        self.assertIsNone(guard)
        self.assertEqual(written, OLD_INSTALL)
        self.assertEqual(pack_pacman.install_script(None), OLD_INSTALL)
        checked = _bash_n(written)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_hooks_call_the_guard_with_install_and_remove(self) -> None:
        """Source .INSTALL in bash and run the hooks pacman runs."""
        with tempfile.TemporaryDirectory(prefix="navin-pacman-hooks-") as tmp:
            root = Path(tmp)
            log = root / "calls.log"
            fake_guard = root / "wsl_oom_guard.sh"
            fake_guard.write_text(f'#!/bin/sh\necho "$1" >> "{log}"\n', encoding="utf-8")
            install = root / ".INSTALL"
            install.write_text(pack_pacman.install_script(str(fake_guard)), encoding="utf-8")
            for hook in ("post_install", "post_upgrade", "pre_remove"):
                proc = subprocess.run(
                    ["bash", "-c", f'. "{install}" && {hook}'],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, f"{hook}: {proc.stderr}")
            calls = log.read_text(encoding="utf-8").splitlines()
            fake_guard.unlink()
            proc = subprocess.run(
                ["bash", "-c", f'. "{install}" && post_install && pre_remove'],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            calls_after = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls, ["install", "install", "remove"])
        self.assertEqual(calls_after, calls)

    def test_guard_path_that_cannot_be_quoted_is_refused(self) -> None:
        for bad in ("usr/lib/wsl_oom_guard.sh", "/usr/lib/it's/wsl_oom_guard.sh"):
            with self.assertRaises(pack_pacman.PackError):
                pack_pacman.install_script(bad)


@unittest.skipUnless(HAVE_TAR and HAVE_ZSTD, SKIP_ARCHIVE)
class PackPacmanPackageTest(unittest.TestCase):
    def test_a_zst_payload_opens(self) -> None:
        # Natively from Python 3.14, through the zstd tool before.
        plain = PackPacmanDataTarTest._tar("w:")
        compressed = subprocess.run(
            ["zstd", "-q", "-c"], input=plain, capture_output=True, check=True,
        ).stdout
        tf = pack_pacman._open_data_tar("data.tar.zst", compressed)
        try:
            self.assertEqual(tf.getnames(), ["usr/lib/Navin/navin-dist/navin"])
        finally:
            tf.close()

    def test_package_install_runs_the_shipped_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-pacman-guard-pkg-") as tmp:
            root = Path(tmp)
            deb = root / "navin.deb"
            deb.write_bytes(_fake_deb(_guard_files("1.2.3")))
            out = root / "navin-1.2.3-1-x86_64.pkg.tar.zst"
            proc = _run_packer(deb=deb, version="1.2.3", arch="x86_64", out=out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with _open_pkg(out) as tf:
                members = {_tar_name(member.name): member for member in tf.getmembers()}
                handle = tf.extractfile(members[".INSTALL"])
                assert handle is not None
                install = handle.read().decode("utf-8")
        self.assertIn(GUARD_MEMBER, members)
        self.assertEqual(install, GUARD_INSTALL)

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
