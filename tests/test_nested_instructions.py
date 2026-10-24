from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navin.agent import nested_instructions as ni


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(ni.ENV_VAR, raising=False)
    ni.reset()
    yield
    ni.reset()


def _tree(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("repo rules", encoding="utf-8")
    ws = tmp_path / "pkg" / "app"
    (ws / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "pkg" / "AGENTS.md").write_text("pkg rules", encoding="utf-8")
    (ws / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (ws / "sub" / "AGENTS.md").write_text("sub rules", encoding="utf-8")
    (ws / "sub" / "deep" / "AGENTS.md").write_text("deep rules", encoding="utf-8")
    (ws / "sub" / "deep" / "f.py").write_text("x = 1\n", encoding="utf-8")
    return ws


def test_ancestors_stop_at_git_root_nearest_last(tmp_path):
    ws = _tree(tmp_path)
    files = ni.ancestor_files(ws)
    assert files == [tmp_path / "AGENTS.md", tmp_path / "pkg" / "AGENTS.md"]
    section = ni.ancestor_section(ws)
    assert section.index("repo rules") < section.index("pkg rules")
    assert "root rules" not in section


def test_workspace_at_git_root_has_no_ancestors(tmp_path):
    (tmp_path / ".git").mkdir()
    assert ni.ancestor_files(tmp_path) == []


def test_nested_files_between_root_and_file(tmp_path):
    ws = _tree(tmp_path)
    files = ni.nested_files_for("sub/deep/f.py", ws)
    assert files == [ws / "sub" / "AGENTS.md", ws / "sub" / "deep" / "AGENTS.md"]
    assert ni.nested_files_for(tmp_path / "AGENTS.md", ws) == []


def test_attach_once_per_session(tmp_path):
    ws = _tree(tmp_path)
    text, labels = ni.attach("edit_file", {"path": "sub/deep/f.py"}, ws, "s1")
    assert labels == ["sub/AGENTS.md", "sub/deep/AGENTS.md"]
    assert "sub rules" in text and "deep rules" in text and "root rules" not in text
    assert ni.attachment_for_tool("apply_patch", {"path": "sub/deep/f.py"}, ws, "s1") == ""
    assert "sub rules" in ni.attachment_for_tool("write_file", {"path": "sub/x"}, ws, "s2")
    assert ni.attachment_for_tool("grep", {"path": "sub/x"}, ws, "s3") == ""


def test_apply_patch_and_files_params(tmp_path):
    ws = _tree(tmp_path)
    _, labels = ni.attach("apply_patch", {"edits": [{"path": "sub/deep/f.py"}]}, ws, "p")
    assert labels == ["sub/AGENTS.md", "sub/deep/AGENTS.md"]


def test_skip_already_loaded_by_import_and_by_content(tmp_path):
    ws = _tree(tmp_path)
    (ws / "AGENTS.md").write_text("@sub/AGENTS.md\n", encoding="utf-8")
    (ws / "sub" / "deep" / "AGENTS.md").write_text("repo rules", encoding="utf-8")
    (ws / "other").mkdir()
    (ws / "other" / "AGENTS.md").write_text("@sub/AGENTS.md\n", encoding="utf-8")
    _, labels = ni.attach("read_file", {"path": "sub/deep/f.py"}, ws, "d")
    assert labels == []
    # Same text as the root file: skipped by content hash.
    _, labels = ni.attach("read_file", {"path": "other/x"}, ws, "d")
    assert labels == []


def test_modes(tmp_path, monkeypatch):
    ws = _tree(tmp_path)
    parts = ["## AGENTS.md\n\nroot rules"]
    assert "pkg rules" in ni.apply_to_bootstrap(parts, ws)[-1]
    monkeypatch.setenv(ni.ENV_VAR, "root-only")
    assert ni.apply_to_bootstrap(parts, ws) == parts
    assert ni.attachment_for_tool("read_file", {"path": "sub/deep/f.py"}, ws, "m") == ""
    monkeypatch.setenv(ni.ENV_VAR, "none")
    assert ni.apply_to_bootstrap(parts + ["## SOUL.md\n\nme"], ws) == ["## SOUL.md\n\nme"]
    monkeypatch.setenv(ni.ENV_VAR, "bogus")
    assert ni.mode() == "agents-md"


def test_context_builder_wiring(tmp_path):
    from navin.agent.context import ContextBuilder

    ws = _tree(tmp_path)
    builder = ContextBuilder.__new__(ContextBuilder)
    builder.workspace = ws
    text = builder._load_bootstrap_files(ws)
    assert text.index("root rules") < text.index("repo rules") < text.index("pkg rules")


def test_size_cap(tmp_path, monkeypatch):
    ws = _tree(tmp_path)
    (ws / "sub" / "AGENTS.md").write_text("a" * (ni.MAX_FILE_BYTES + 500), encoding="utf-8")
    text, _ = ni.attach("edit_file", {"path": "sub/deep/f.py"}, ws, "c")
    assert "[truncated: 500 more bytes]" in text
    monkeypatch.setattr(ni, "MAX_ATTACHMENT_BYTES", 100)
    ni.reset()
    text, labels = ni.attach("edit_file", {"path": "sub/deep/f.py"}, ws, "c")
    assert labels == ["sub/AGENTS.md"] and len(text) < 400


def test_session_lru(tmp_path, monkeypatch):
    ws = _tree(tmp_path)
    monkeypatch.setattr(ni, "MAX_SESSIONS", 2)
    for key in ("a", "b", "c"):
        ni.attach("read_file", {"path": "sub/x"}, ws, key)
    assert list(ni._SESSIONS) == ["b", "c"]


def test_notice_and_post_tool_hook(tmp_path, monkeypatch):
    from navin.agent import user_hooks

    monkeypatch.setattr(user_hooks, "load_hooks", lambda *a, **k: [])
    ws = _tree(tmp_path)
    assert ni.notice(["sub/AGENTS.md"]) == "Loaded instructions: sub/AGENTS.md"
    shown: list[str] = []

    async def notify(text: str) -> None:
        shown.append(text)

    out = asyncio.run(user_hooks.post_tool_use_feedback(
        ws, "hook-s", "edit_file", {"path": "sub/f"}, "ok", notify=notify,
    ))
    assert out and "sub rules" in out
    assert "Loaded instructions: sub/AGENTS.md" in shown
    again = asyncio.run(user_hooks.post_tool_use_feedback(ws, "hook-s", "write_file", {"path": "sub/f"}, "ok"))
    assert not again or "sub rules" not in again


def test_read_file_gets_notice_only(tmp_path):
    # read_file already inlines nested files in its own result.
    ws = _tree(tmp_path)
    text, labels = ni.attach("read_file", {"path": "sub/deep/f.py"}, ws, "r")
    assert text == "" and labels == ["sub/AGENTS.md", "sub/deep/AGENTS.md"]
    assert ni.attach("edit_file", {"path": "sub/deep/f.py"}, ws, "r") == ("", [])


def test_claude_md_fallback_per_directory(tmp_path):
    ws = _tree(tmp_path)
    (ws / "lib").mkdir()
    (ws / "lib" / "CLAUDE.md").write_text("lib rules", encoding="utf-8")
    assert ni.nested_files_for("lib/x.py", ws) == [ws / "lib" / "CLAUDE.md"]


def test_no_git_walk_reaches_filesystem_root(tmp_path):
    ws = tmp_path / "a" / "b"
    ws.mkdir(parents=True)
    (tmp_path / "a" / "AGENTS.md").write_text("a rules", encoding="utf-8")
    assert tmp_path / "a" / "AGENTS.md" in ni.ancestor_files(ws)


def test_disk_lookups_are_reused_across_calls(tmp_path, monkeypatch):
    # On a WSL project each lookup crosses the 9P bridge: a second edit in
    # the same folders must not walk the disk again.
    ws = _tree(tmp_path)
    calls = {"files": 0, "resolve": 0}
    real_files, real_resolve = ni._files_in_now, ni._resolve_now

    def files(*a, **k):
        calls["files"] += 1
        return real_files(*a, **k)

    def resolve(path):
        calls["resolve"] += 1
        return real_resolve(path)

    monkeypatch.setattr(ni, "_files_in_now", files)
    monkeypatch.setattr(ni, "_resolve_now", resolve)
    _text, labels = ni.attach("edit_file", {"path": "sub/deep/f.py"}, ws, "c1")
    assert labels == ["sub/AGENTS.md", "sub/deep/AGENTS.md"]
    first = dict(calls)
    assert first["files"] and first["resolve"]
    _text, labels = ni.attach("edit_file", {"path": "sub/deep/f.py"}, ws, "c2")
    assert labels == ["sub/AGENTS.md", "sub/deep/AGENTS.md"]
    assert calls == first


def test_writing_an_instruction_file_refreshes_lookups(tmp_path):
    ws = _tree(tmp_path)
    (ws / "lib").mkdir()
    (ws / "lib" / "f.py").write_text("y = 2\n", encoding="utf-8")
    assert ni.attach("edit_file", {"path": "lib/f.py"}, ws, "w1") == ("", [])
    (ws / "lib" / "AGENTS.md").write_text("lib rules", encoding="utf-8")
    # Inside the TTL a file written by someone else is not seen yet...
    assert ni.attach("edit_file", {"path": "lib/f.py"}, ws, "w2") == ("", [])
    # ...but the agent writing one is seen at once.
    text, labels = ni.attach("write_file", {"path": "lib/AGENTS.md"}, ws, "w3")
    assert labels == ["lib/AGENTS.md"] and "lib rules" in text


def test_lookups_expire_after_the_ttl(tmp_path, monkeypatch):
    ws = _tree(tmp_path)
    monkeypatch.setattr(ni, "CACHE_TTL_S", 0.0)
    (ws / "lib").mkdir()
    assert ni.attach("edit_file", {"path": "lib/f.py"}, ws, "t1") == ("", [])
    (ws / "lib" / "AGENTS.md").write_text("lib rules", encoding="utf-8")
    assert ni.attach("edit_file", {"path": "lib/f.py"}, ws, "t2")[1] == ["lib/AGENTS.md"]


def test_post_tool_hook_reads_the_disk_off_the_event_loop(tmp_path, monkeypatch):
    import threading

    from navin.agent import user_hooks

    monkeypatch.setattr(user_hooks, "load_hooks", lambda *a, **k: [])
    ws = _tree(tmp_path)
    threads: list[threading.Thread] = []
    real_attach = ni.attach

    def attach(*args, **kwargs):
        threads.append(threading.current_thread())
        return real_attach(*args, **kwargs)

    monkeypatch.setattr(ni, "attach", attach)

    async def run() -> tuple[str | None, threading.Thread]:
        out = await user_hooks.post_tool_use_feedback(ws, "off-loop", "edit_file", {"path": "sub/f"}, "ok")
        return out, threading.current_thread()

    out, loop_thread = asyncio.run(run())
    assert out and "sub rules" in out
    assert threads and all(t is not loop_thread for t in threads)
