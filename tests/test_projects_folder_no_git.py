# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""~/NavinProjects holds projects: never a git repo at its root.

A memory repo there made every project created inside it part of that repo
(git finds the parent .git), and the agent ran git on projects that had none.
"""

from __future__ import annotations

from navin.utils.gitstore import GitStore

TRACKED = [".navin/SOUL.md", ".navin/USER.md", ".navin/memory/MEMORY.md"]


def test_no_repo_is_created_at_the_projects_folder(tmp_path, monkeypatch):
    projects = tmp_path / "NavinProjects"
    projects.mkdir()
    monkeypatch.setattr("navin.config.paths.get_default_workspace_path", lambda: projects)
    store = GitStore(projects, TRACKED)
    assert store.init() is False
    assert not (projects / ".git").exists()


def test_an_old_memory_repo_is_put_away_not_deleted(tmp_path, monkeypatch):
    projects = tmp_path / "NavinProjects"
    projects.mkdir()
    # Made before the rule: a memory repo at the root.
    monkeypatch.setattr("navin.config.paths.get_default_workspace_path", lambda: tmp_path / "elsewhere")
    assert GitStore(projects, TRACKED).init() is True
    monkeypatch.setattr("navin.config.paths.get_default_workspace_path", lambda: projects)
    store = GitStore(projects, TRACKED)
    assert store.retire_from_projects_container() is True
    assert not (projects / ".git").exists() and not (projects / ".gitignore").exists()
    assert (projects / ".navin" / "memory-history.git" / "HEAD").is_file()


def test_a_project_repo_elsewhere_is_left_alone(tmp_path, monkeypatch):
    projects = tmp_path / "NavinProjects"
    project = projects / "my-app"
    project.mkdir(parents=True)
    monkeypatch.setattr("navin.config.paths.get_default_workspace_path", lambda: projects)
    store = GitStore(project, TRACKED)
    assert store.init() is True
    assert store.retire_from_projects_container() is False
    assert (project / ".git").is_dir()
