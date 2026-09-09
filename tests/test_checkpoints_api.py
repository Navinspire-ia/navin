# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for workspace checkpoints (shadow-git snapshots + restore)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navin.webui.checkpoints_api import (
    CheckpointError,
    create_checkpoint,
    diff_checkpoint,
    list_checkpoints,
    restore_checkpoint,
    shadow_git_dir,
)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NAVIN_CHECKPOINTS_HOME", str(tmp_path / "ckpt-home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("print('v1')\n", encoding="utf-8")
    (root / "lib").mkdir()
    (root / "lib" / "util.py").write_text("X = 1\n", encoding="utf-8")
    return root


def test_create_and_list(project: Path) -> None:
    first = create_checkpoint(project, label="initial", reason="manual")
    assert first["created"] is True
    assert first["id"]

    # Unchanged workspace: no new snapshot.
    again = create_checkpoint(project)
    assert again["created"] is False
    assert again["id"] == first["id"]

    (project / "app.py").write_text("print('v2')\n", encoding="utf-8")
    second = create_checkpoint(project, label="after edit", reason="pre-turn")
    assert second["created"] is True
    assert second["id"] != first["id"]

    listing = list_checkpoints(project)["checkpoints"]
    assert [c["label"] for c in listing] == ["after edit", "initial"]
    assert [c["reason"] for c in listing] == ["pre-turn", "manual"]
    assert all(c["ts"] > 0 for c in listing)


def test_list_without_any_checkpoint(project: Path) -> None:
    assert list_checkpoints(project) == {"checkpoints": []}


def test_diff_reports_changes(project: Path) -> None:
    first = create_checkpoint(project, label="base")
    (project / "app.py").write_text("print('changed')\n", encoding="utf-8")
    (project / "new.txt").write_text("brand new\n", encoding="utf-8")
    (project / "lib" / "util.py").unlink()

    diff = diff_checkpoint(project, first["id"])
    by_path = {f["path"]: f["status"] for f in diff["files"]}
    assert by_path["app.py"] == "M"
    assert by_path["new.txt"] == "A"
    assert by_path["lib/util.py"] == "D"
    assert "print('changed')" in diff["patch"]
    assert diff["truncated"] is False


def test_restore_roundtrip(project: Path) -> None:
    base = create_checkpoint(project, label="base")

    # Mutate everything: edit, create, delete.
    (project / "app.py").write_text("print('broken')\n", encoding="utf-8")
    (project / "junk.txt").write_text("added later\n", encoding="utf-8")
    (project / "lib" / "util.py").unlink()

    result = restore_checkpoint(project, base["id"])
    assert result["restored"] == base["id"]
    assert result["safety"]
    assert result["deletedFiles"] >= 1

    assert (project / "app.py").read_text() == "print('v1')\n"
    assert (project / "lib" / "util.py").read_text() == "X = 1\n"
    assert not (project / "junk.txt").exists()

    # The pre-restore state is itself a checkpoint: restore back to it.
    restore_checkpoint(project, result["safety"])
    assert (project / "app.py").read_text() == "print('broken')\n"
    assert (project / "junk.txt").exists()
    assert not (project / "lib" / "util.py").exists()


def test_default_excludes_are_not_snapshotted(project: Path) -> None:
    (project / "node_modules" / "pkg").mkdir(parents=True)
    (project / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "a.pyc").write_bytes(b"\x00")

    base = create_checkpoint(project, label="base")
    diff = diff_checkpoint(project, base["id"])
    assert diff["files"] == []

    # Restoring never touches excluded folders.
    restore_checkpoint(project, base["id"])
    assert (project / "node_modules" / "pkg" / "index.js").exists()


def test_project_git_repo_is_untouched(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "real"],
        cwd=project,
        check=True,
    )
    head_before = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True
        ).stdout.strip()
    )

    create_checkpoint(project, label="shadow snap")
    (project / "app.py").write_text("print('v2')\n", encoding="utf-8")
    create_checkpoint(project, label="second")

    head_after = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True
        ).stdout.strip()
    )
    assert head_after == head_before
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True
    ).stdout
    assert "app.py" in status  # the real repo still sees the edit as pending

    # Shadow repo lives outside the project.
    shadow = shadow_git_dir(project)
    assert project not in shadow.parents


def test_invalid_and_unknown_ids(project: Path) -> None:
    create_checkpoint(project)
    with pytest.raises(CheckpointError) as exc:
        diff_checkpoint(project, "not-a-sha!")
    assert exc.value.status == 400
    with pytest.raises(CheckpointError) as exc:
        restore_checkpoint(project, "abcdef123456")
    assert exc.value.status == 404


def test_spawn_pre_turn_checkpoint_is_best_effort(project: Path) -> None:
    from navin.webui.checkpoints_api import spawn_pre_turn_checkpoint

    thread = spawn_pre_turn_checkpoint(project, label="fix the login bug")
    thread.join(timeout=30)
    assert not thread.is_alive()
    listing = list_checkpoints(project)["checkpoints"]
    assert len(listing) == 1
    assert listing[0]["reason"] == "pre-turn"
    assert listing[0]["label"] == "fix the login bug"

    # A missing root must not raise (the turn goes on).
    bad = spawn_pre_turn_checkpoint(project / "nope", label="x")
    bad.join(timeout=30)
    assert not bad.is_alive()


def test_ambient_git_env_cannot_hijack_the_shadow_repo(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Navin can be started from a git hook or a wrapper script that already
    # exports GIT_DIR. The shadow repo is named on the command line precisely
    # so an inherited variable never wins.
    decoy = project.parent / "decoy.git"
    monkeypatch.setenv("GIT_DIR", str(decoy))
    monkeypatch.setenv("GIT_WORK_TREE", str(project.parent))

    first = create_checkpoint(project, label="hijack attempt")
    assert first["created"] is True
    assert not decoy.exists()
    assert (shadow_git_dir(project) / "HEAD").is_file()


def test_missing_root_raises_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVIN_CHECKPOINTS_HOME", str(tmp_path / "home"))
    with pytest.raises(CheckpointError) as exc:
        create_checkpoint(tmp_path / "does-not-exist")
    assert exc.value.status == 404
