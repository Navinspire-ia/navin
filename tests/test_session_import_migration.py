# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Migrating chats from other agents must yield real Navin sessions.

Imported conversations list, resume and load their project (AGENTS.md,
rules, skills) exactly like a chat started in Navin.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path

from navin.session.import_sessions import (
    ExternalSession,
    SourceReport,
    _origin_project,
    _parse_cursor_global,
    _parse_opencode,
    _title_from_messages,
    import_to_workspace,
)
from navin.session.manager import SessionManager


def _cursor_db(path: Path, project: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
    conn.execute(
        "CREATE TABLE composerHeaders (composerId TEXT PRIMARY KEY, workspaceId TEXT, createdAt INTEGER, "
        "lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, recency INTEGER, "
        "checkpointAt INTEGER, value TEXT, subagentTypeName TEXT)"
    )
    for cid, sub in (("main-1", 0), ("sub-1", 1)):
        conn.execute(
            "INSERT INTO composerHeaders (composerId, createdAt, lastUpdatedAt, isSubagent) VALUES (?, ?, ?, ?)",
            (cid, 1776248138665, 1776248238665, sub),
        )
        conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", (f"composerData:{cid}", json.dumps({
            "name": "Fix the login page",
            "fullConversationHeadersOnly": [
                {"bubbleId": "b1", "type": 1}, {"bubbleId": "b2", "type": 2}, {"bubbleId": "b3", "type": 2},
            ],
            "workspaceIdentifier": {"uri": {"path": str(project), "scheme": "file"}},
        })))
        for bid, text in (("b1", "the login button does nothing"), ("b2", ""), ("b3", "Fixed: the handler was unbound.")):
            conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", (f"bubbleId:{cid}:{bid}", json.dumps({"text": text})))
    conn.commit()
    conn.close()
    return path


def test_current_cursor_global_store_is_read(tmp_path):
    project = tmp_path / "app"
    project.mkdir()
    sessions = _parse_cursor_global(_cursor_db(tmp_path / "state.vscdb", project))
    assert [s.session_id for s in sessions] == ["main-1"]  # subagent chats are not user chats
    session = sessions[0]
    assert session.title == "Fix the login page"
    assert [(m["role"], m["content"]) for m in session.messages] == [
        ("user", "the login button does nothing"),
        ("assistant", "Fixed: the handler was unbound."),
    ]
    assert session.origin_cwd == str(project)
    assert session.created_at is not None


def test_opencode_keeps_its_project_directory(tmp_path):
    root = tmp_path / "storage"
    (root / "session" / "global").mkdir(parents=True)
    (root / "session" / "global" / "ses_1.json").write_text(json.dumps({
        "id": "ses_1", "title": "Refactor api", "directory": "/srv/api", "time": {"created": 1776181414726},
    }))
    (root / "message" / "ses_1").mkdir(parents=True)
    (root / "message" / "ses_1" / "msg_1.json").write_text(json.dumps({
        "id": "msg_1", "role": "user", "time": {"created": 1776181414726},
    }))
    (root / "part" / "msg_1").mkdir(parents=True)
    (root / "part" / "msg_1" / "prt_1.json").write_text(json.dumps({"type": "text", "text": "split the router"}))
    sessions = _parse_opencode(root)
    assert sessions and sessions[0].origin_cwd == "/srv/api"


def test_titles_skip_pasted_and_injected_blocks():
    assert _title_from_messages([
        {"role": "user", "content": '<pasted_content id="a">[2026-10-04] 500 errors...</pasted_content> why does it crash?'},
    ]) == "why does it"


def test_origin_project_rules():
    assert _origin_project(None) is None
    assert _origin_project(str(Path.home())) is None  # ~ is not a project
    assert _origin_project("/definitely/not/here") is None


def test_imported_session_is_shaped_like_a_native_one(tmp_path):
    project = tmp_path / "shop"
    (project / ".cursor" / "rules").mkdir(parents=True)
    (project / ".cursor" / "rules" / "style.mdc").write_text("---\nalwaysApply: true\n---\nAlways use 2-space indentation.\n")
    (project / ".cursor" / "rules" / "api.mdc").write_text("---\ndescription: REST conventions\nglobs: api/**\n---\nUse plural nouns.\n")
    (project / ".claude" / "skills" / "deploy-shop").mkdir(parents=True)
    (project / ".claude" / "skills" / "deploy-shop" / "SKILL.md").write_text(
        "---\nname: deploy-shop\ndescription: Deploy the shop to staging\n---\nRun ./deploy.sh staging\n"
    )
    (project / "AGENTS.md").write_text("Run `make check` before committing.\n")
    workspace = tmp_path / "navin-ws"
    workspace.mkdir()
    external = ExternalSession(
        source="cursor",
        session_id="main-1",
        title="Fix the login page",
        messages=[{"role": "user", "content": "fix login"}, {"role": "assistant", "content": "done"}],
        origin_path=tmp_path / "state.vscdb",
        created_at=datetime(2026, 10, 1, 9, 0),
        updated_at=datetime(2026, 10, 1, 9, 5),
        origin_cwd=str(project),
    )
    stats = import_to_workspace([SourceReport("cursor", "Cursor", tmp_path, "ready", [external])], workspace)
    assert stats[0].imported == 1

    session = SessionManager(workspace).get_or_create("websocket:import-cursor-main-1")
    assert session.metadata["webui"] is True
    assert session.metadata["workspace_scope"] == {"project_path": str(project.resolve()), "access_mode": "full"}
    assert [m["role"] for m in session.messages] == ["user", "assistant"]

    # Resuming it loads the project's own instructions and rules, as for a
    # conversation started in Navin on that folder.
    from navin.agent.context import ContextBuilder

    prompt = ContextBuilder(workspace).build_system_prompt(workspace=Path(session.metadata["workspace_scope"]["project_path"]))
    assert "make check" in prompt                    # AGENTS.md
    assert "2-space indentation" in prompt           # always-apply Cursor rule, inlined
    assert ".cursor/rules/api.mdc" in prompt         # conditional rule, listed for on-demand reading
    assert "deploy-shop" in prompt                   # Claude Code skill of the project


class UnreadableSourceTest(unittest.TestCase):
    """One source that blows up must not take the whole scan down."""

    def test_a_failing_parser_reports_the_source_instead_of_raising(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from navin.session import import_sessions as module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.jsonl").write_text("{}", encoding="utf-8")
            spec = module._SourceSpec("codex", "Codex", root, mock.Mock(side_effect=ValueError("bad bytes")), extra_roots=())
            with mock.patch.object(module, "_source_specs", return_value=[spec]):
                report = module.discover(["codex"], include_saved_roots=False)[0]
        self.assertEqual(report.status, "unsupported")
        self.assertIn("ValueError", report.note)
        self.assertIn("bad bytes", report.note)

    def test_a_failing_counter_reports_the_source_instead_of_raising(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from navin.session import import_sessions as module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = module._SourceSpec("codex", "Codex", root, mock.Mock(), extra_roots=())
            with mock.patch.object(module, "_source_specs", return_value=[spec]), \
                    mock.patch.dict(module._COUNTERS, {"codex": mock.Mock(side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad"))}):
                report = module.discover(["codex"], include_saved_roots=False, count_only=True)[0]
        self.assertEqual(report.status, "unsupported")
        self.assertIn("UnicodeDecodeError", report.note)


class UnreachableRootTest(unittest.TestCase):
    """A root that raises on exists() (an asleep WSL share) is treated as absent."""

    def test_a_root_whose_exists_raises_is_skipped(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from navin.session import import_sessions as module

        class Unreachable(type(Path())):
            def exists(self, *args, **kwargs):  # noqa: ANN002, ANN003
                raise OSError(53, "The network path was not found")

        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp)
            spec = module._SourceSpec(
                "codex", "Codex", Unreachable("/nowhere/at/all"), mock.Mock(return_value=[]), extra_roots=(good,),
            )
            with mock.patch.object(module, "_source_specs", return_value=[spec]):
                report = module.discover(["codex"], include_saved_roots=False)[0]
        self.assertEqual(report.status, "ready")
        self.assertEqual(report.root, good)

    def test_a_failure_listing_the_other_side_keeps_the_local_sources(self) -> None:
        from unittest import mock

        from navin.session import import_sessions as module

        with mock.patch.object(module, "_windows_homes", side_effect=OSError(53, "no route")):
            specs = module._source_specs()
        self.assertEqual({spec.name for spec in specs}, set(module.PARSEABLE_SOURCES))
