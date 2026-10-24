# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An imported chat (a .navin-session, Cursor, Claude Code...) shows its history.

Imports write the conversation into the session, never into the display
transcript: the chat opened empty. The transcript is rebuilt from the session.
"""

from __future__ import annotations

from unittest import mock

from navin.webui import transcript as tr


def test_a_session_without_transcript_gets_one_from_its_messages(tmp_path):
    with mock.patch.object(tr, "get_webui_dir", return_value=tmp_path):
        key = "websocket:imported-chat"
        assert tr.build_webui_thread_response(key, limit=40, direction="latest") is None
        messages = [
            {"role": "user", "content": "Fais-moi une app de gestion de projet"},
            {"role": "assistant", "content": "C'est fait : kanban, tâches et membres."},
        ]
        tr.write_session_messages_as_transcript(key, messages)
        page = tr.build_webui_thread_response(key, session_messages=messages, limit=40, direction="latest")
        texts = [m.get("content") for m in page["messages"]]
        assert "Fais-moi une app de gestion de projet" in texts
        assert any("kanban" in str(t) for t in texts)


def test_reopening_a_chat_reuses_parsed_turns_until_the_file_changes(tmp_path):
    with mock.patch.object(tr, "get_webui_dir", return_value=tmp_path):
        key = "websocket:switch-back"
        tr.write_session_messages_as_transcript(key, [
            {"role": "user", "content": "premier"},
            {"role": "assistant", "content": "reponse"},
        ])
        first = tr.build_webui_thread_response(key, limit=40, direction="latest")
        with mock.patch.object(tr, "_read_transcript_file", wraps=tr._read_transcript_file) as reads:
            again = tr.build_webui_thread_response(key, limit=40, direction="latest")
            assert reads.call_count == 0
        assert [m.get("content") for m in again["messages"]] == [m.get("content") for m in first["messages"]]
        tr.append_transcript_object(key, {"event": "user", "chat_id": "switch-back", "text": "nouveau message"})
        updated = tr.build_webui_thread_response(key, limit=40, direction="latest")
        assert "nouveau message" in [m.get("content") for m in updated["messages"]]
