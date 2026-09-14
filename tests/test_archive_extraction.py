# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive attachment extraction (zip / 7z / rar) for chat uploads."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from navin.utils import document


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_zip_text_members_are_extracted(tmp_path: Path):
    archive = make_zip(
        tmp_path / "bundle.zip",
        {
            "notes.md": b"# Notes\nhello",
            "src/main.py": b"print('ignored: .py is not a text format')",
            "data/logo.png": b"\x89PNG binary",
        },
    )
    text = document.extract_text(archive)
    assert "[archive: bundle.zip, 1 text file(s)]" in text
    assert "===== notes.md =====" in text
    assert "hello" in text


def test_zip_empty_of_text_members_reports_it(tmp_path: Path):
    archive = make_zip(tmp_path / "media.zip", {"photo.jpg": b"\xff\xd8"})
    text = document.extract_text(archive)
    assert "no readable text files inside" in text


def test_zip_with_too_many_members_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(document, "_MAX_OFFICE_ARCHIVE_MEMBERS", 3)
    archive = make_zip(
        tmp_path / "many.zip",
        {f"f{i}.txt": b"x" for i in range(5)},
    )
    text = document.extract_text(archive)
    assert "too many files" in text


def test_zip_oversized_member_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(document, "_MAX_OFFICE_MEMBER_SIZE", 16)
    archive = make_zip(tmp_path / "big.zip", {"big.txt": b"x" * 64})
    text = document.extract_text(archive)
    assert "oversized internal file" in text


def test_zip_total_expansion_is_bounded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(document, "_MAX_OFFICE_UNCOMPRESSED_SIZE", 32)
    archive = make_zip(
        tmp_path / "spread.zip",
        {"a.txt": b"x" * 20, "b.txt": b"y" * 20},
    )
    text = document.extract_text(archive)
    assert "safety limit" in text


def test_corrupt_zip_reports_a_read_error(tmp_path: Path):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip file")
    text = document.extract_text(archive)
    assert "failed to read archive" in text


def test_sevenzip_text_members_are_extracted(tmp_path: Path):
    py7zr = pytest.importorskip("py7zr")
    source = tmp_path / "src"
    source.mkdir()
    (source / "readme.txt").write_text("seven zip content")
    (source / "table.csv").write_text("a,b\n1,2\n")
    archive = tmp_path / "bundle.7z"
    with py7zr.SevenZipFile(archive, "w") as handle:
        handle.write(source / "readme.txt", arcname="readme.txt")
        handle.write(source / "table.csv", arcname="table.csv")
    text = document.extract_text(archive)
    assert "[archive: bundle.7z, 2 text file(s)]" in text
    assert "seven zip content" in text
    assert "a,b" in text


def test_sevenzip_without_the_library_reports_it(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "py7zr", None)
    archive = tmp_path / "bundle.7z"
    archive.write_bytes(b"fake")
    text = document.extract_text(archive)
    assert "py7zr" in text


@pytest.mark.skipif(
    not (shutil.which("unrar") or shutil.which("unar")),
    reason="no rar extraction tool on this machine",
)
def test_rar_text_members_are_extracted(tmp_path: Path):
    # Building a .rar needs the proprietary tool; only run when it exists.
    rar = tmp_path / "bundle.rar"
    marker = tmp_path / "marker.txt"
    marker.write_text("rar content")
    import subprocess  # noqa: PLC0415

    tool = shutil.which("unrar") or shutil.which("unar")
    subprocess.run([tool, "a", str(rar), str(marker)], check=True, capture_output=True)
    text = document.extract_text(rar)
    assert "rar content" in text


def test_rar_without_tool_reports_a_clear_error(tmp_path: Path, monkeypatch):
    rarfile = pytest.importorskip("rarfile")

    archive = tmp_path / "bundle.rar"
    archive.write_bytes(b"fake rar")

    class FakeRar:
        def __init__(self, *_args, **_kwargs):
            raise rarfile.RarCannotExec("no tool")

    monkeypatch.setattr(rarfile, "RarFile", FakeRar)
    text = document.extract_text(archive)
    assert "unrar" in text


def test_archives_are_listed_as_supported_extensions():
    assert {".zip", ".7z", ".rar"} <= document.SUPPORTED_EXTENSIONS
