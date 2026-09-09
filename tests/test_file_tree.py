# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""File-tree listing must stay cheap on the expand hot path."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

from navin.security.workspace_access import default_workspace_scope
from navin.webui.file_tree import file_tree_payload


def test_file_tree_lists_one_level_without_scaffold(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "docker").mkdir()
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")

    scope = default_workspace_scope(tmp_path, True)
    with mock.patch("navin.utils.helpers.ensure_project_scaffold") as scaffold:
        payload = file_tree_payload(None, scope=scope)

    scaffold.assert_not_called()
    names = {entry["name"] for entry in payload["entries"]}
    assert names == {"deploy", "README.md"}
    assert payload["entries"][0]["type"] == "dir"
    assert payload["entries"][1]["type"] == "file"

    nested = file_tree_payload(str(tmp_path / "deploy"), scope=scope)
    assert [e["name"] for e in nested["entries"]] == ["docker"]
    assert all(entry["size"] == 0 for entry in payload["entries"])


def test_file_tree_listing_is_fast_on_local_fs(tmp_path: Path) -> None:
    for i in range(40):
        (tmp_path / f"dir-{i}").mkdir()
        (tmp_path / f"file-{i}.txt").write_text("x", encoding="utf-8")

    started = time.perf_counter()
    payload = file_tree_payload(None, scope=default_workspace_scope(tmp_path, True))
    elapsed = time.perf_counter() - started

    assert len(payload["entries"]) == 80
    assert elapsed < 0.5
