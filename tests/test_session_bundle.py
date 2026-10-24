# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A whole session (conversation + project) can be continued on another machine."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from navin.session.bundle import (
    BundleError,
    export_session_bundle,
    import_session_bundle,
    read_manifest,
)
from navin.session.manager import SessionManager

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def test_a_git_project_session_is_rebuilt_elsewhere_without_its_secrets():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        src = Path(a) / "proj"
        src.mkdir()
        def run(*command: str) -> None:
            subprocess.run(command, cwd=src, check=True, capture_output=True)
        run("git", "init", "-q", "-b", "feature/x")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (src / "app.py").write_text("print(1)\n")
        (src / ".gitignore").write_text(".navin/\n")
        run("git", "add", ".")
        run("git", "commit", "-qm", "init")
        (src / "app.py").write_text("print(2)\n")            # uncommitted change
        (src / "new.py").write_text("x = 1\n")               # untracked
        (src / ".env").write_text("SECRET=1\n")              # must never travel
        (src / ".navin").mkdir()
        (src / ".navin" / "RESUME.md").write_text("resume\n")
        img = Path(a) / "shot.png"
        img.write_bytes(b"\x89PNG fake")
        sm = SessionManager(src)
        s = sm.get_or_create("cli:direct")
        s.add_message("user", f"look at {src}/app.py", media=[str(img)])
        s.add_message("assistant", "done")
        sm.save(s)
        out = export_session_bundle(src, "cli:direct", Path(a) / "out.navin-session")
        m = read_manifest(out.path)
        assert out.project_mode == "git" and out.excluded_secrets == [".env"]
        assert m["branch"] == "feature/x"
        res = import_session_bundle(out.path, Path(b) / "copy")
        d = res.project_dir
        assert (d / "app.py").read_text() == "print(2)\n"          # uncommitted change kept
        assert (d / "new.py").exists()                              # untracked file kept
        assert not (d / ".env").exists()                            # secrets never travel
        assert (d / ".navin" / "RESUME.md").exists()                # project memory kept
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=d, capture_output=True, text=True).stdout.strip()
        assert branch == "feature/x"
        assert not subprocess.run(["git", "remote"], cwd=d, capture_output=True, text=True).stdout.strip()
        s2 = SessionManager(d).get_or_create(res.session_key)
        assert len(s2.messages) == 2
        assert str(d) in s2.messages[0]["content"] and str(src) not in s2.messages[0]["content"]
        assert Path(s2.messages[0]["media"][0]).read_bytes().startswith(b"\x89PNG")


def test_a_plain_folder_travels_as_files_and_heavy_folders_stay_home(tmp_path):
    src = tmp_path / "plain"
    (src / "node_modules" / "x").mkdir(parents=True)
    (src / "node_modules" / "x" / "i.js").write_text("big")
    (src / "main.py").write_text("ok\n")
    (src / "id_rsa").write_text("PRIVATE")
    manager = SessionManager(src)
    session = manager.get_or_create("websocket:chat-1")
    session.add_message("user", "hello")
    manager.save(session)
    out = export_session_bundle(src, "websocket:chat-1", tmp_path / "plain.navin-session", )
    assert out.project_mode in {"files", "git"}
    res = import_session_bundle(out.path, tmp_path / "copy")
    assert (res.project_dir / "main.py").read_text() == "ok\n"
    assert not (res.project_dir / "node_modules").exists()
    assert not (res.project_dir / "id_rsa").exists()


def test_import_refuses_a_non_empty_folder_and_a_foreign_file(tmp_path):
    bogus = tmp_path / "x.navin-session"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "other"}))
    with pytest.raises(BundleError):
        read_manifest(bogus)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "file").write_text("x")
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "navin-session", "version": 1}))
        zf.writestr("session.jsonl", "")
    with pytest.raises(BundleError, match="not empty"):
        import_session_bundle(bogus, busy)


def test_a_path_escaping_the_project_is_never_extracted(tmp_path):
    bad = tmp_path / "evil.navin-session"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "navin-session", "version": 1, "project_mode": "files",
                                                  "session_key": "cli:direct", "source_root": "/x"}))
        zf.writestr("session.jsonl", json.dumps({"_type": "metadata", "key": "cli:direct"}) + "\n")
        zf.writestr("project/files/../../escaped.txt", "pwned")
    res = import_session_bundle(bad, tmp_path / "dest")
    assert not (tmp_path / "escaped.txt").exists()
    assert res.session_key == "cli:direct"


def test_a_desktop_chat_travels_with_its_project_and_lands_linked_to_the_copy(tmp_path):
    # Desktop: the chat lives in the gateway workspace and points at a project.
    gateway = tmp_path / "gateway"
    project = tmp_path / "work" / "shop"
    project.mkdir(parents=True)
    (project / "index.html").write_text("<h1>shop</h1>\n")
    manager = SessionManager(gateway)
    chat = manager.get_or_create("websocket:abc123")
    chat.metadata["workspace_scope"] = {"project_path": str(project), "access_mode": "restricted"}
    chat.add_message("user", f"edit {project}/index.html")
    manager.save(chat)

    out = export_session_bundle(gateway, "websocket:abc123", tmp_path / "s.navin-session", project_root=project)
    assert read_manifest(out.path)["source_root"] == str(project.resolve())

    receiver = tmp_path / "receiver-gateway"
    res = import_session_bundle(
        out.path, tmp_path / "copy",
        sessions_workspace=receiver, session_key="websocket:new1",
        metadata=lambda folder: {"webui": True, "workspace_scope": {"project_path": str(folder), "access_mode": "restricted"}},
    )
    assert (res.project_dir / "index.html").exists()
    copy = SessionManager(receiver).get_or_create("websocket:new1")
    assert copy.metadata["workspace_scope"]["project_path"] == str(res.project_dir)
    assert str(res.project_dir) in copy.messages[0]["content"]
    # Nothing was written into the receiver's project sessions folder.
    assert not (res.project_dir / "sessions").exists()


def test_a_bundle_for_someone_else_leaves_out_who_you_are(tmp_path):
    import zipfile

    from navin.session.bundle import export_session_bundle
    from navin.session.manager import SessionManager

    project = tmp_path / "proj"
    (project / ".navin" / "memory").mkdir(parents=True)
    (project / ".navin" / "USER.md").write_text("Lives in Paris, salary...")
    (project / ".navin" / "SOUL.md").write_text("Personality")
    (project / ".navin" / "memory" / "MEMORY.md").write_text("Project uses pnpm")
    (project / "app.py").write_text("print(1)\n")
    manager = SessionManager(project)
    session = manager.get_or_create("websocket:abc")
    session.add_message("user", "hi")
    manager.save(session)

    mine = export_session_bundle(project, "websocket:abc", tmp_path / "mine.navin-session")
    theirs = export_session_bundle(project, "websocket:abc", tmp_path / "theirs.navin-session", for_someone_else=True)
    mine_names = zipfile.ZipFile(mine.path).namelist()
    their_names = zipfile.ZipFile(theirs.path).namelist()
    assert "project/files/.navin/USER.md" in mine_names or any(n.endswith("navin/USER.md") for n in mine_names)
    assert not any(n.endswith("USER.md") or n.endswith("SOUL.md") for n in their_names)
    assert any(n.endswith("MEMORY.md") for n in their_names)


def test_conversations_stored_in_dot_navin_never_travel_as_project_files(tmp_path):
    src = tmp_path / "plain"
    src.mkdir()
    (src / "main.py").write_text("ok\n")
    (src / ".navin").mkdir()
    (src / ".navin" / "RESUME.md").write_text("resume\n")
    manager = SessionManager(src)
    for key in ("websocket:chat-1", "websocket:other-private-chat"):
        session = manager.get_or_create(key)
        session.add_message("user", "hello")
        manager.save(session)
    out = export_session_bundle(src, "websocket:chat-1", tmp_path / "x.navin-session")
    with zipfile.ZipFile(out.path) as zf:
        names = zf.namelist()
        assert not [n for n in names if "navin/sessions/" in n]
        assert any(n.endswith("navin/RESUME.md") for n in names)


def test_git_inside_wsl_gets_the_temp_bundle_path_it_can_write(monkeypatch):
    """A \\\\wsl.localhost repo runs git in the distribution: a Windows temp
    path handed as-is becomes a stray file in the repo and export fails with
    WinError 2. The path must be translated to /mnt/c/..."""
    from pathlib import PureWindowsPath

    from navin.session import bundle as mod
    from navin.utils.git_argv import GitRoute

    repo = Path("/tmp/anywhere")
    monkeypatch.setattr(mod, "git_route", lambda root: GitRoute(argv=("wsl.exe", "git"), root="/home/me/proj", distro="Ubuntu"))
    assert mod._git_path(PureWindowsPath(r"C:\Users\me\AppData\Local\Temp\navin-bundle-x\repo.bundle"), repo) == (
        "/mnt/c/Users/me/AppData/Local/Temp/navin-bundle-x/repo.bundle"
    )
    with pytest.raises(BundleError):
        mod._git_path(PureWindowsPath("relative/repo.bundle"), repo)
    monkeypatch.setattr(mod, "git_route", lambda root: GitRoute(argv=("git", "-C", str(root)), root=str(root)))
    assert mod._git_path(Path("/tmp/x/repo.bundle"), repo) == "/tmp/x/repo.bundle"
