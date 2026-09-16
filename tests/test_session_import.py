"""Tests for external session import (claude-code, codex, opencode)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from navin.session import import_sessions
from navin.session.import_sessions import ExternalSession, SourceReport
from navin.session.manager import SessionManager


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _claude_fixture(root: Path) -> Path:
    path = root / "p1" / "aaa.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "summary", "summary": "Fix the flaky queue test"},
            {"type": "file-history-snapshot", "snapshot": {}},
            {
                "type": "user",
                "isSidechain": False,
                "message": {"role": "user", "content": "pourquoi le test echoue"},
                "timestamp": "2026-09-04T20:16:12.915Z",
                "sessionId": "aaa-1",
            },
            {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "voici la cause"},
                        {"type": "tool_use", "name": "grep", "input": {}},
                    ],
                },
                "timestamp": "2026-09-04T20:16:20.000Z",
                "sessionId": "aaa-1",
            },
            {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "output"}],
                },
                "timestamp": "2026-09-04T20:16:21.000Z",
                "sessionId": "aaa-1",
            },
            {
                "type": "user",
                "isSidechain": True,
                "message": {"role": "user", "content": "sidechain noise"},
                "timestamp": "2026-09-04T20:16:22.000Z",
                "sessionId": "aaa-1",
            },
        ],
    )
    return path


def test_claude_code_parser(tmp_path: Path) -> None:
    _claude_fixture(tmp_path)
    sessions = import_sessions._parse_claude_code(tmp_path)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "aaa-1"
    assert session.title == "Fix the flaky queue test"
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"] == "pourquoi le test echoue"
    assert session.messages[1]["content"] == "voici la cause"
    assert session.created_at is not None
    assert session.updated_at >= session.created_at


def test_codex_parser(tmp_path: Path) -> None:
    path = tmp_path / "2026" / "05" / "01" / "rollout-x.jsonl"
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-05-01T16:29:34.950Z",
                "type": "session_meta",
                "payload": {"id": "meta-1", "cwd": "/tmp/proj"},
            },
            {
                "timestamp": "2026-05-01T16:29:35.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "instructions"}],
                },
            },
            {
                "timestamp": "2026-05-01T16:29:36.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "<environment_context> prod",
                        }
                    ],
                },
            },
            {
                "timestamp": "2026-05-01T16:30:00.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "salut navin"}],
                },
            },
            {
                "timestamp": "2026-05-01T16:30:05.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "bonjour"}],
                },
            },
        ],
    )
    sessions = import_sessions._parse_codex(tmp_path)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "meta-1"
    assert session.origin_cwd == "/tmp/proj"
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"] == "salut navin"
    assert session.title == "salut navin"


def test_opencode_parser(tmp_path: Path) -> None:
    root = tmp_path
    _write_json(
        root / "session" / "global" / "ses_1.json",
        {
            "id": "ses_1",
            "slug": "cosmic-lagoon",
            "title": "Historique conversation",
            "time": {"created": 1776181414726, "updated": 1776181494428},
        },
    )
    _write_json(
        root / "message" / "ses_1" / "msg_1.json",
        {"id": "msg_1", "role": "user", "time": {"created": 1776181414741}},
    )
    _write_json(
        root / "message" / "ses_1" / "msg_2.json",
        {"id": "msg_2", "role": "assistant", "time": {"created": 1776181414800}},
    )
    _write_json(
        root / "part" / "msg_1" / "prt_a.json",
        {"id": "prt_a", "type": "text", "text": "salut"},
    )
    _write_json(
        root / "part" / "msg_2" / "prt_b.json",
        {"id": "prt_b", "type": "step-start"},
    )
    _write_json(
        root / "part" / "msg_2" / "prt_c.json",
        {"id": "prt_c", "type": "text", "text": "bonjour"},
    )
    sessions = import_sessions._parse_opencode(root)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "ses_1"
    assert session.title == "Historique conversation"
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"] == "salut"
    assert session.messages[1]["content"] == "bonjour"
    assert session.created_at is not None


def test_import_to_workspace_lists_and_is_idempotent(tmp_path: Path) -> None:
    external = ExternalSession(
        source="claude-code",
        session_id="aaa-1",
        title="Fix the flaky queue test",
        messages=[{"role": "user", "content": "pourquoi", "timestamp": "2026-09-04T20:16:12+00:00"}],
        origin_path=tmp_path / "aaa.jsonl",
        created_at=datetime(2026, 9, 4, 20, 16, 12),
        updated_at=datetime(2026, 9, 4, 20, 16, 30),
    )
    report = SourceReport(
        name="claude-code",
        label="Claude Code",
        root=tmp_path,
        status="ready",
        sessions=[external],
    )
    workspace = tmp_path / "ws"
    stats = import_sessions.import_to_workspace([report], workspace)
    assert stats[0].imported == 1
    assert stats[0].skipped_existing == 0

    manager = SessionManager(workspace)
    listed = manager.list_sessions()
    assert [item["key"] for item in listed] == [
        "websocket:import-claude-code-aaa-1"
    ]
    assert listed[0]["title"] == "Fix the flaky queue test"
    assert listed[0]["preview"] == "pourquoi"

    stats_again = import_sessions.import_to_workspace([report], workspace)
    assert stats_again[0].imported == 0
    assert stats_again[0].skipped_existing == 1

    stats_overwrite = import_sessions.import_to_workspace(
        [report], workspace, overwrite=True
    )
    assert stats_overwrite[0].imported == 1
    assert len(manager.list_sessions()) == 1


def test_title_skips_throwaway_first_messages() -> None:
    messages = [
        {"role": "user", "content": ".exit"},
        {"role": "assistant", "content": "bonjour, que puis-je faire ?"},
    ]
    assert import_sessions._title_from_messages(messages) == (
        "bonjour, que puis-je faire ?"
    )
    messages = [
        {"role": "user", "content": "."},
        {"role": "user", "content": "vraie question sur le projet"},
    ]
    assert import_sessions._title_from_messages(messages) == (
        "vraie question sur le projet"
    )


def test_imported_timestamps_are_naive(tmp_path: Path) -> None:
    """Regression: tz-aware stored timestamps crash the gateway.

    Navin's autocompact does datetime.now() - session.updated_at with a
    naive now(); an aware updated_at raises TypeError on every startup.
    """
    external = ExternalSession(
        source="codex",
        session_id="tz-1",
        title="tz regression",
        messages=[
            {
                "role": "user",
                "content": "salut",
                "timestamp": "2026-05-01T16:30:00+00:00",
            }
        ],
        origin_path=tmp_path / "rollout.jsonl",
        created_at=datetime(2026, 5, 1, 16, 30, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, 16, 31, 0, tzinfo=timezone.utc),
    )
    report = SourceReport(
        name="codex", label="Codex", root=tmp_path, status="ready",
        sessions=[external],
    )
    workspace = tmp_path / "ws"
    import_sessions.import_to_workspace([report], workspace)

    path = next((workspace / "sessions").glob("*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    metadata = json.loads(lines[0])
    message = json.loads(lines[1])
    for stamp in (
        metadata["created_at"],
        metadata["updated_at"],
        message["timestamp"],
    ):
        assert not stamp.endswith("+00:00")
        assert not stamp.endswith("Z")
        # Must not raise: the exact autocompact subtraction.
        datetime.now() - datetime.fromisoformat(stamp)


def test_imported_keys_surface_in_webui_sidebar(tmp_path: Path) -> None:
    """Regression: the WebUI only lists sessions whose key starts with
    "websocket:" (ws_http._is_websocket_channel_session_key), so import
    keys must use that prefix and pin the workspace scope."""
    external = ExternalSession(
        source="codex",
        session_id="key-1",
        title="key regression",
        messages=[{"role": "user", "content": "hello"}],
        origin_path=tmp_path / "rollout.jsonl",
    )
    report = SourceReport(
        name="codex", label="Codex", root=tmp_path, status="ready",
        sessions=[external],
    )
    workspace = tmp_path / "ws"
    import_sessions.import_to_workspace([report], workspace)

    path = next((workspace / "sessions").glob("*.jsonl"))
    metadata = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert metadata["key"].startswith("websocket:import-codex-")
    assert (
        metadata["metadata"]["workspace_scope"]["project_path"]
        == str(workspace.expanduser().resolve())
    )


def test_parse_oh_my_pi_sessions(tmp_path: Path) -> None:
    """OMP transcripts (agent/sessions/<slug>/<iso>_<sid>.jsonl) convert
    with titles, cwd, naive timestamps and flattened content parts."""
    base = tmp_path / ".omp" / "agent" / "sessions" / "home-proj"
    base.mkdir(parents=True)
    transcript = base / "2026-05-02T10-00-00_abc123.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session",
                        "id": "omp-ses-1",
                        "cwd": "/home/aymen/proj",
                        "timestamp": 1746176400000,
                    }
                ),
                json.dumps({"type": "title", "title": "Refonte importeur"}),
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": 1746176401000,
                        "message": {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "bonjour"},
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": 1746176402000,
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "voila"},
                                {"type": "toolCall", "name": "read_file"},
                            ],
                        },
                    }
                ),
                "not json at all",
            ]
        ),
        encoding="utf-8",
    )
    sessions = import_sessions._parse_oh_my_pi(tmp_path / ".omp")
    assert len(sessions) == 1
    ses = sessions[0]
    assert ses.session_id == "omp-ses-1"
    assert ses.title == "Refonte importeur"
    assert ses.origin_cwd == "/home/aymen/proj"
    assert [m["role"] for m in ses.messages] == ["user", "assistant"]
    assert ses.messages[1]["content"] == "voila\n[tool call: read_file]"
    for stamp in (ses.created_at, ses.updated_at):
        assert stamp is not None and stamp.tzinfo is None
    assert ses.messages[0]["timestamp"].endswith(
        ses.messages[0]["timestamp"][-8:]
    ) and "+" not in ses.messages[0]["timestamp"]


def test_parse_cursor_composer_and_legacy(tmp_path: Path) -> None:
    """Cursor state.vscdb: composerData:<id> rows (new format) and the
    legacy chatdata blob both convert, with the workspace folder as
    origin_cwd."""
    import sqlite3

    ws = tmp_path / "workspaceStorage" / "abc123"
    ws.mkdir(parents=True)
    db_path = ws / "state.vscdb"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    conn.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        (
            "history.recentlyOpenedPathsList",
            json.dumps({"entries": [{"folderUri": "file:///home/aymen/proj"}]}),
        ),
    )
    conn.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        (
            "composerData:comp-9",
            json.dumps(
                {
                    "composerId": "comp-9",
                    "name": "Fix importer",
                    "createdAt": 1746176400000,
                    "conversation": [
                        {"type": 1, "text": "corrige le bug"},
                        {"type": 2, "text": "c'est fait"},
                    ],
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        (
            "workbench.panel.aichat.view.aichat.chatdata",
            json.dumps(
                {
                    "chats": [
                        {
                            "chatTitle": "Old chat",
                            "messages": [
                                {"type": 1, "text": "salut"},
                                {"type": 2, "text": "hello"},
                            ],
                        }
                    ]
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    sessions = import_sessions._parse_cursor(tmp_path / "workspaceStorage")
    by_title = {s.title: s for s in sessions}
    assert set(by_title) == {"Fix importer", "Old chat"}
    composer = by_title["Fix importer"]
    assert composer.session_id == "comp-9"
    assert composer.origin_cwd == "/home/aymen/proj"
    assert [m["role"] for m in composer.messages] == ["user", "assistant"]
    assert composer.created_at is not None and composer.created_at.tzinfo is None
    legacy = by_title["Old chat"]
    assert legacy.created_at is None
    assert legacy.messages[0]["content"] == "salut"


def test_extra_roots_scan_mounted_directories(
    tmp_path: Path, monkeypatch
) -> None:
    """Custom roots (e.g. a mounted Windows/macOS Cursor dir) are scanned
    and persisted; sessions are deduped across roots."""
    monkeypatch.setattr(
        import_sessions, "_EXTRA_ROOTS_FILE", tmp_path / "roots.json"
    )
    native_storage = tmp_path / "native" / "workspaceStorage"
    native = native_storage / "w1"
    native.mkdir(parents=True)
    mounted_storage = tmp_path / "mounted" / "workspaceStorage"
    mounted = mounted_storage / "w2"
    mounted.mkdir(parents=True)
    for base, sid in ((native, "comp-a"), (mounted, "comp-b")):
        import sqlite3

        conn = sqlite3.connect(base / "state.vscdb")
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        conn.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            (
                f"composerData:{sid}",
                json.dumps(
                    {
                        "composerId": sid,
                        "name": f"Chat {sid}",
                        "createdAt": 1746176400000,
                        "conversation": [{"type": 1, "text": "hello"}],
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

    import_sessions.save_extra_root("cursor", str(mounted_storage))
    assert import_sessions.load_extra_roots() == {
        "cursor": [str(mounted_storage)]
    }

    sessions = import_sessions.discover(
        ["cursor"], {"cursor": [native_storage]}
    )
    assert {s.session_id for s in sessions[0].sessions} == {"comp-a", "comp-b"}

    # Call-time roots merge with saved ones; duplicates are dropped.
    again = import_sessions.discover(
        ["cursor"], {"cursor": [mounted_storage, native_storage]}
    )
    assert len(again[0].sessions) == 2

    import_sessions.remove_extra_root("cursor", str(mounted_storage))
    assert import_sessions.load_extra_roots() == {}


def test_cursor_platform_roots_are_detected(tmp_path: Path, monkeypatch) -> None:
    """Cursor workspaceStorage is auto-detected on macOS and Windows layouts
    (Library/Application Support, AppData/Roaming), not just Linux."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    storage = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "workspaceStorage"
    )
    ws = storage / "w1"
    ws.mkdir(parents=True)
    conn = sqlite3.connect(ws / "state.vscdb")
    conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    conn.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        (
            "composerData:comp-mac",
            json.dumps(
                {
                    "composerId": "comp-mac",
                    "name": "Chat mac",
                    "conversation": [{"type": 1, "text": "bonjour"}],
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    reports = import_sessions.discover(["cursor"])
    assert reports[0].status == "ready"
    assert [s.session_id for s in reports[0].sessions] == ["comp-mac"]

def test_resolve_sources_validates_names() -> None:
    assert import_sessions.resolve_sources("auto") == (
        "claude-code",
        "codex",
        "opencode",
        "oh-my-pi",
        "cursor",
    )
    assert import_sessions.resolve_sources("codex") == ("codex",)
    assert import_sessions.resolve_sources("claude-code,codex") == (
        "claude-code",
        "codex",
    )
    assert import_sessions.resolve_sources("codex, codex") == ("codex",)
    with pytest.raises(ValueError):
        import_sessions.resolve_sources("warp")
    with pytest.raises(ValueError):
        import_sessions.resolve_sources("codex,warp")
    with pytest.raises(ValueError):
        import_sessions.resolve_sources(",")


def test_discover_reports_missing_and_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    reports = import_sessions.discover(["oh-my-pi", "cursor", "claude-code"])
    by_name = {report.name: report for report in reports}
    assert by_name["oh-my-pi"].status == "missing"
    assert by_name["cursor"].status == "missing"
    assert by_name["claude-code"].status == "missing"

    (tmp_path / ".omp").mkdir()
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    reports = import_sessions.discover(["oh-my-pi", "claude-code"])
    by_name = {report.name: report for report in reports}
    # oh-my-pi is parseable now: an empty tree yields a ready report
    # with zero sessions, not an "unsupported" note.
    assert by_name["oh-my-pi"].status == "ready"
    assert by_name["oh-my-pi"].sessions == []
    assert by_name["claude-code"].status == "ready"
    assert by_name["claude-code"].sessions == []
