# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin SM edition: no self-update, one edition stamp for the engine and the desktop shell."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from navin import edition
from navin.update import service

ROOT = Path(__file__).resolve().parents[1]


def _stamped(tree: Path) -> str:
    match = re.search(r'^EDITION = "([a-z]+)"$', (tree / "navin" / "edition.py").read_text(encoding="utf-8"), re.M)
    assert match is not None, "no EDITION line"
    return match.group(1)


def _script_tree(tmp_path: Path, *, desktop: bool) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "set-edition.sh", tmp_path / "scripts" / "set-edition.sh")
    (tmp_path / "navin").mkdir()
    shutil.copy2(ROOT / "navin" / "edition.py", tmp_path / "navin" / "edition.py")
    if desktop:
        shell = tmp_path / "desktop" / "src-tauri"
        shell.mkdir(parents=True)
        (shell / "edition.txt").write_text("standard\n", encoding="utf-8")
    return tmp_path


def _set(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(tree / "scripts" / "set-edition.sh"), *args],
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_the_committed_tree_is_the_standard_edition() -> None:
    # Committed as sm, every Navin install built from this tree would stop updating itself.
    assert _stamped(ROOT) == edition.EDITION == edition.STANDARD
    shell = ROOT / "desktop" / "src-tauri"
    # The public tree ships no desktop shell; where it exists its copy must agree.
    if shell.is_dir():
        assert (shell / "edition.txt").read_text(encoding="utf-8") == "standard\n"


def test_set_edition_stamps_the_engine_and_the_desktop_shell(tmp_path: Path) -> None:
    tree = _script_tree(tmp_path, desktop=True)
    shell_copy = tree / "desktop" / "src-tauri" / "edition.txt"
    assert _set(tree, "sm").returncode == 0
    assert (_stamped(tree), shell_copy.read_text(encoding="utf-8")) == ("sm", "sm\n")
    assert _set(tree, "standard").returncode == 0
    assert (_stamped(tree), shell_copy.read_text(encoding="utf-8")) == ("standard", "standard\n")
    refused = _set(tree, "enterprise")
    assert refused.returncode == 2 and "usage" in refused.stderr
    assert _stamped(tree) == "standard"


def test_set_edition_without_a_desktop_shell(tmp_path: Path) -> None:
    tree = _script_tree(tmp_path, desktop=False)
    assert _set(tree, "sm").returncode == 0
    assert _stamped(tree) == "sm" and not (tree / "desktop").exists()


def test_navin_sm_never_offers_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVIN_UPDATE_BASE_URL", "https://updates.example")
    assert service._update_config()[0] == "https://updates.example"

    monkeypatch.setattr(edition, "EDITION", edition.SM)
    assert service._update_config()[0] == ""
    assert service.updates_configured() is False
    checked = service.check_for_update(force=True)
    assert (checked["configured"], checked["available"]) == (False, False)
    assert checked["reason"] == service.ORGANIZATION_UPDATES_REASON
    assert service.update_policy_for("99.0.0") == {
        "allowed": False, "reason": service.ORGANIZATION_UPDATES_REASON, "mandatory": False,
    }
