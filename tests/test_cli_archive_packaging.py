# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import hashlib
import runpy
import tarfile
import zipfile
from pathlib import Path

import pytest

from navin.update.service import _extract_archive

PACK = runpy.run_path(str(Path(__file__).resolve().parents[1] / "packaging" / "pack_cli_archive.py"))[
    "pack_cli_archive"
]


@pytest.mark.parametrize("platform,binary,suffix", [
    ("windows-x64", "navin.exe", ".zip"),
    ("macos-arm64", "navin", ".tar.gz"),
    ("macos-x64", "navin", ".tar.gz"),
    ("linux-x64", "navin", ".tar.gz"),
    ("linux-arm64", "navin", ".tar.gz"),
])
def test_cli_archive_round_trips_through_the_updater(tmp_path, platform, binary, suffix):
    tree = tmp_path / "build" / "navin-dist"
    resources = tree / "_internal" / "navin" / "web" / "dist"
    resources.mkdir(parents=True)
    (tree / "VERSION").write_text("2.0.3\n")
    (tree / binary).write_bytes(b"engine")
    (tree / binary).chmod(0o755)
    (tree / ".hidden-config").write_text("bundled")
    (resources / "index.html").write_text("same interface as the desktop")
    output = tmp_path / "release" / f"navin-cli-2.0.3-{platform}{suffix}"

    assert PACK(tree, output, "2.0.3") == output
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert output.with_name(f"{output.name}.sha256").read_text() == f"{digest}  {output.name}\n"
    if suffix == ".zip":
        assert zipfile.is_zipfile(output)
    else:
        assert tarfile.is_tarfile(output)
    restored = tmp_path / "restore"
    restored.mkdir()
    _extract_archive(output, restored)
    assert (restored / "navin-dist" / binary).read_bytes() == b"engine"
    assert (restored / "navin-dist" / ".hidden-config").read_text() == "bundled"
    assert (restored / "navin-dist" / "_internal/navin/web/dist/index.html").read_text() == (
        "same interface as the desktop"
    )


def test_a_stale_sidecar_cannot_be_published_under_a_new_version(tmp_path):
    tree = tmp_path / "navin-dist"
    tree.mkdir()
    (tree / "navin").write_bytes(b"old engine")
    (tree / "VERSION").write_text("1.0.0")
    output = tmp_path / "release.zip"

    with pytest.raises(ValueError, match="stale"):
        PACK(tree, output, "2.0.3")
    assert not output.exists()
