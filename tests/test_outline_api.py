# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Outline / document-symbol payload for the Code explorer panel."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.webui import symbols_api


def _scope(root: Path) -> SimpleNamespace:
    return SimpleNamespace(project_path=root)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "mod.py").write_text(
        "def alpha():\n    return 1\n\nclass Beta:\n    def method(self):\n        pass\n",
        encoding="utf-8",
    )
    return root


def test_outline_payload_lists_file_symbols(project: Path):
    payload = symbols_api.outline_payload(_scope(project), path="mod.py")
    names = {item["name"] for item in payload["items"]}
    assert "alpha" in names
    assert "Beta" in names
    assert payload["path"] == "mod.py"
    lines = [item["line"] for item in payload["items"]]
    assert lines == sorted(lines)


def test_outline_payload_accepts_absolute_path(project: Path):
    abs_path = str((project / "mod.py").resolve())
    payload = symbols_api.outline_payload(_scope(project), path=abs_path)
    assert payload["path"] == "mod.py"
    assert payload["total"] >= 1


def test_outline_payload_missing_path(project: Path):
    with pytest.raises(symbols_api.SymbolsError) as exc:
        symbols_api.outline_payload(_scope(project), path="")
    assert exc.value.status == 400
