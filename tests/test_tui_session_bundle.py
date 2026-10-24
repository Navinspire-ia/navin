"""CLI export and opening of a chat as .navin-session (same file as the desktop menu)."""

from __future__ import annotations

from pathlib import Path

import pytest

from navin.session.bundle import BUNDLE_SUFFIX, read_manifest
from navin.session.manager import SessionManager
from navin.tui.session_bundle import (
    continue_command,
    default_export_dir,
    default_export_path,
    describe_bundle,
    export_cli_session,
    free_project_dir,
    open_cli_bundle,
)


def _chat(workspace: Path, key: str = "cli:direct") -> None:
    sessions = SessionManager(workspace)
    chat = sessions.get_or_create(key)
    chat.add_message("user", "depuis le terminal")
    sessions.save(chat)


def test_default_export_path_is_safe(tmp_path: Path) -> None:
    path = default_export_path(tmp_path, "Rapport / final: v2?", now=0)
    assert path.parent == tmp_path
    assert path.suffix == BUNDLE_SUFFIX
    assert path.name.startswith("Rapport-final-v2-")
    assert default_export_path(tmp_path, "", now=0).name.startswith("session-")


def test_default_export_dir_prefers_downloads_never_the_project(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert default_export_dir(tmp_path / "ws") == tmp_path / "ws" / "exports"
    (home / "Downloads").mkdir()
    assert default_export_dir(tmp_path / "ws") == home / "Downloads"


def test_cli_chat_round_trips_through_a_bundle(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    _chat(workspace)

    exported = export_cli_session(
        workspace, "cli:direct", project_root=workspace, output=tmp_path / "out" / "chat",
    )
    assert exported.path == tmp_path / "out" / f"chat{BUNDLE_SUFFIX}"
    assert read_manifest(exported.path)["session_key"] == "cli:direct"
    assert any(item.endswith(".env") for item in exported.excluded_secrets)
    assert describe_bundle(exported.path)["session_key"] == "cli:direct"

    into = free_project_dir(tmp_path, describe_bundle(exported.path))
    assert into == tmp_path / "project-2"
    opened = open_cli_bundle(exported.path, into)
    assert (into / "main.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert not (into / ".env").exists()
    command = continue_command(opened)
    assert command.startswith("navin-cli ")
    assert f"--session {opened.session_key}" in command or "--session '" in command


def test_describe_bundle_refuses_other_files(tmp_path: Path) -> None:
    note = tmp_path / "notes.txt"
    note.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        describe_bundle(note)
    with pytest.raises(ValueError):
        describe_bundle(tmp_path / f"missing{BUNDLE_SUFFIX}")


def test_cli_palette_offers_export_and_open() -> None:
    import inspect

    from navin.tui.app import NavinActions, NavinApp

    source = inspect.getsource(NavinActions._entries)
    for action in ("export_session", "open_shared_session"):
        assert f'"{action}"' in source
        assert callable(getattr(NavinApp, f"action_{action}", None))
