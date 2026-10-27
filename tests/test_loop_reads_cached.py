# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Reads made on the engine loop answer from memory once done.

The CLI and the Desktop engine run every agent on one event loop: the freeze
logs of 2026-10-26/27 showed the loop stopped on a session metadata read
(5.6 s), on the workspace setting (resolve(), 7 freezes) and on skill lookups.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from navin.agent import skills as skills_module
from navin.agent.skills import SkillsLoader, clear_home_skill_dir_cache, clear_skills_index_cache
from navin.config import paths
from navin.session.manager import Session, SessionManager


def test_the_workspace_setting_resolves_its_path_once(tmp_path, monkeypatch):
    legacy = tmp_path / "home" / ".navin" / "workspace"
    default = tmp_path / "NavinProjects"
    monkeypatch.setattr(paths, "_legacy_default_workspace_path", lambda: legacy)
    monkeypatch.setattr(paths, "get_default_workspace_path", lambda: default)
    paths._LEGACY_MATCH_CACHE.clear()
    calls: list[str] = []
    real = Path.resolve

    def counted(self, strict=False):
        calls.append(str(self))
        return real(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", counted)
    project = tmp_path / "project"
    assert paths.resolve_workspace_setting(str(project)) == project
    first = len(calls)
    assert first > 0
    for _ in range(50):
        assert paths.resolve_workspace_setting(str(project)) == project
    assert len(calls) == first, "the status line asks 8 times a second: no resolve after the first"
    # The legacy folder still sends to the new default, also through a symlink.
    legacy.parent.mkdir(parents=True)
    legacy.mkdir()
    link = tmp_path / "old-link"
    link.symlink_to(legacy)
    assert paths.resolve_workspace_setting(str(legacy)) == default
    assert paths.resolve_workspace_setting(str(link)) == default
    # Another home is another answer, not the cached one.
    other = tmp_path / "other-home" / ".navin" / "workspace"
    monkeypatch.setattr(paths, "_legacy_default_workspace_path", lambda: other)
    assert paths.resolve_workspace_setting(str(legacy)) == legacy


def _manager(tmp_path: Path) -> SessionManager:
    manager = SessionManager(tmp_path / "ws")
    session = Session(key="websocket:c1")
    session.metadata["workspace_scope"] = {"project_path": "/p", "access_mode": "full"}
    session.add_message("user", "hello")
    manager.save(session)
    return manager


def test_session_metadata_comes_from_memory_or_from_one_read_per_change(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    reads: list[str] = []
    real = SessionManager._read_session_metadata_file

    def counted(self, key, path):
        reads.append(key)
        return real(self, key, path)

    monkeypatch.setattr(SessionManager, "_read_session_metadata_file", counted)
    # Loaded in this engine: no disk at all.
    record = manager.read_session_metadata("websocket:c1")
    assert record["metadata"]["workspace_scope"]["project_path"] == "/p"
    assert reads == []
    # A copy: changing it never changes the session.
    record["metadata"]["workspace_scope"] = "changed"
    assert manager.get_or_create("websocket:c1").metadata["workspace_scope"]["project_path"] == "/p"

    # Not loaded: the file is read once, then reused while it does not change.
    manager.invalidate("websocket:c1")
    fresh = SessionManager(tmp_path / "ws")
    for _ in range(20):
        assert fresh.read_session_metadata("websocket:c1")["metadata"]["workspace_scope"]["access_mode"] == "full"
    assert reads == ["websocket:c1"]
    copy = fresh.read_session_metadata("websocket:c1")
    copy["metadata"].clear()
    assert fresh.read_session_metadata("websocket:c1")["metadata"], "the cached record is never handed out"

    # Another process rewrites the file: the next read sees it.
    path = fresh._get_session_path("websocket:c1")
    lines = path.read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    head["metadata"]["workspace_scope"]["access_mode"] = "read"
    time.sleep(0.01)
    path.write_text("\n".join([json.dumps(head), *lines[1:]]) + "\n", encoding="utf-8")
    assert fresh.read_session_metadata("websocket:c1")["metadata"]["workspace_scope"]["access_mode"] == "read"
    assert reads == ["websocket:c1", "websocket:c1"]
    assert fresh.read_session_metadata("websocket:missing") is None


def test_a_skill_lookup_is_checked_once_then_trusted_for_a_few_seconds(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    clear_home_skill_dir_cache()
    clear_skills_index_cache()
    root = tmp_path / "project" / ".navin" / "skills"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy" / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nBody.\n", encoding="utf-8")
    loader = SkillsLoader(tmp_path / "project", builtin_skills_dir=tmp_path / "none")
    checks: list[Path] = []
    real = SkillsLoader._contained_in_skill_roots

    def counted(self, path):
        checks.append(path)
        return real(self, path)

    monkeypatch.setattr(SkillsLoader, "_contained_in_skill_roots", counted)
    clock = {"now": 1000.0}
    monkeypatch.setattr(skills_module.time, "monotonic", lambda: clock["now"])
    for _ in range(30):
        assert loader._skill_path("deploy") is not None
    assert len(checks) == 1, "a prompt build asks for every skill: one check, not one per lookup"
    # After the window the file is checked again: a removed skill is not served.
    (root / "deploy" / "SKILL.md").unlink()
    clock["now"] += skills_module._VERIFIED_PATH_TTL_S
    assert loader._skill_path("deploy") is None
    clear_home_skill_dir_cache()
    clear_skills_index_cache()
