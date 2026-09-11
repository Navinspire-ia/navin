# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""TUI history shows every visible turn, not a raw message window."""

from __future__ import annotations

import unittest

from navin.tui.history import visible_chat_rows

HIDDEN_HISTORY_META = "_hidden_history"


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str, **extra: object) -> dict:
    row: dict = {"role": "assistant", "content": text}
    row.update(extra)
    return row


class VisibleChatRowsTest(unittest.TestCase):
    def test_keeps_early_turns_when_the_tail_is_tools(self) -> None:
        messages: list[dict] = [_user("first question"), _assistant("first answer")]
        messages.append(
            _assistant(
                "",
                tool_calls=[
                    {
                        "id": "c1",
                        "function": {"name": "exec", "arguments": '{"command": "ls"}'},
                    }
                ],
            )
        )
        messages.extend(
            {"role": "tool", "tool_call_id": "c1", "content": f"line {i}"}
            for i in range(220)
        )
        messages.append(_user("latest paste"))
        messages.append(_assistant("latest reply"))

        rows, older = visible_chat_rows(messages, limit=50)
        self.assertEqual(older, 0)
        texts = [row["content"] for row in rows]
        self.assertIn("first question", texts)
        self.assertIn("first answer", texts)
        self.assertIn("latest paste", texts)
        self.assertIn("latest reply", texts)
        tools = [row for row in rows if row["tools"]]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tools"][0]["name"], "exec")
        self.assertIn("line 219", tools[0]["tools"][0]["result"] or "")

    def test_limit_slices_visible_turns_not_raw_messages(self) -> None:
        messages = []
        for i in range(10):
            messages.append(_user(f"u{i}"))
            messages.append(_assistant(f"a{i}"))
            messages.append(
                _assistant(
                    "",
                    tool_calls=[{"id": f"t{i}", "function": {"name": "read_file"}}],
                )
            )
            messages.append({"role": "tool", "tool_call_id": f"t{i}", "content": "ok"})
        rows, older = visible_chat_rows(messages, limit=6)
        self.assertEqual(older, 24)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["content"], "u8")
        self.assertEqual(rows[-1]["tools"][0]["id"], "t9")

    def test_reads_list_content_and_skips_hidden(self) -> None:
        messages = [
            _user("keep me"),
            {
                "role": "user",
                "content": [{"type": "text", "text": "parts ok"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "from parts"}],
            },
            {
                "role": "user",
                "content": "secret",
                HIDDEN_HISTORY_META: True,
            },
            {
                "role": "user",
                "content": "injected",
                "injected_event": "subagent_result",
            },
        ]
        rows, older = visible_chat_rows(messages)
        self.assertEqual(older, 0)
        self.assertEqual(
            [row["content"] for row in rows],
            ["keep me", "parts ok", "from parts"],
        )

    def test_keeps_tool_only_assistant_turns(self) -> None:
        rows, _older = visible_chat_rows(
            [
                _user("go"),
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu1",
                            "name": "read_file",
                            "input": {"path": "a.py"},
                        }
                    ],
                },
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["content"], "")
        self.assertEqual(rows[1]["tools"][0]["name"], "read_file")
        self.assertEqual(rows[1]["tools"][0]["arguments"]["path"], "a.py")


if __name__ == "__main__":
    unittest.main()
