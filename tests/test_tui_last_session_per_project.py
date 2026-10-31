# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""navin-cli never resumes, in one project, the session of another project.

The CLI kept one "last session" for every project: opened in project B after
project A, it reopened A's key in B's folder. Both folders then held a chat
under the same key, and everything keyed by session (titles, pins, display
history, agents) mixed the two.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from navin.tui.prefs import TuiPrefs


def _write_session(root: Path, key: str, title: str, updated: str) -> None:
    folder = root / ".navin" / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    stem = base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")
    lines = [
        {"_type": "metadata", "key": key, "created_at": updated, "updated_at": updated,
         "metadata": {"title": title}},
        {"role": "user", "content": title},
    ]
    (folder / f"{stem}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_the_last_session_of_another_project_is_never_resumed(tmp_path: Path) -> None:
    finance, bbac = tmp_path / "finance", tmp_path / "bbac"
    finance.mkdir()
    bbac.mkdir()
    _write_session(finance, "cli:1792709209", "facture", "2026-10-23T00:46:50")
    prefs = TuiPrefs()
    prefs.remember_session(finance, "cli:1792709209")

    key = prefs.session_for(bbac)

    assert key != "cli:1792709209"
    assert re.fullmatch(r"cli:\d+", key)
    assert not (bbac / ".navin").exists()  # choosing a key creates nothing


def test_each_project_resumes_its_own_last_session(tmp_path: Path) -> None:
    finance, bbac = tmp_path / "finance", tmp_path / "bbac"
    finance.mkdir()
    bbac.mkdir()
    prefs = TuiPrefs()
    prefs.remember_session(finance, "cli:1")
    prefs.remember_session(bbac, "cli:2")

    assert prefs.session_for(finance) == "cli:1"
    assert prefs.session_for(str(bbac) + "/") == "cli:2"


def test_a_project_without_a_remembered_session_opens_its_latest_cli_chat(tmp_path: Path) -> None:
    finance, bbac = tmp_path / "finance", tmp_path / "bbac"
    finance.mkdir()
    _write_session(bbac, "cli:10", "old", "2026-01-01T10:00:00")
    _write_session(bbac, "cli:20", "recent", "2026-03-01T10:00:00")
    _write_session(bbac, "websocket:abc", "desktop", "2026-04-01T10:00:00")
    prefs = TuiPrefs()
    prefs.remember_session(finance, "cli:99")

    assert prefs.session_for(bbac) == "cli:20"


def test_the_legacy_global_last_session_is_kept_only_where_it_is_saved(tmp_path: Path) -> None:
    finance, bbac = tmp_path / "finance", tmp_path / "bbac"
    _write_session(finance, "cli:5", "facture", "2026-01-01T10:00:00")
    bbac.mkdir()
    prefs = TuiPrefs()
    prefs.last_session = "cli:5"  # file written by an earlier build: no map yet

    assert prefs.session_for(finance) == "cli:5"
    assert prefs.session_for(bbac) != "cli:5"


def test_prefs_round_trip_the_map(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tui.json"
    monkeypatch.setattr(TuiPrefs, "path", classmethod(lambda cls: path))
    prefs = TuiPrefs()
    prefs.remember_session(tmp_path, "cli:7")
    prefs.save()

    loaded = TuiPrefs.load()

    assert loaded.session_for(tmp_path) == "cli:7"
    path.write_text(json.dumps({"last_sessions": ["bad"]}), encoding="utf-8")
    assert TuiPrefs.load().last_sessions == {}
