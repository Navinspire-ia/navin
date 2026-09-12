# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import unittest

from navin.utils.pasted_content import (
    allocate_paste_token,
    collapse_text_for_composer,
    expand_pasted_content,
    pasted_content_label,
    should_collapse_pasted_text,
    split_long_user_text,
)


class PastedContentTests(unittest.TestCase):
    def test_threshold_is_1000_chars(self) -> None:
        self.assertFalse(should_collapse_pasted_text("a" * 999))
        self.assertTrue(should_collapse_pasted_text("a" * 1000))

    def test_label_matches_cursor(self) -> None:
        self.assertEqual(pasted_content_label(11448), "[Pasted Content 11448 chars]")
        self.assertEqual(
            pasted_content_label(11448, "2"),
            "[Pasted Content 11448 chars #2]",
        )

    def test_expand_puts_the_body_back(self) -> None:
        token = pasted_content_label(5)
        self.assertEqual(
            expand_pasted_content(f"fix this\n{token}", {token: "hello"}),
            "fix this\nhello",
        )

    def test_missing_token_stays_visible(self) -> None:
        token = pasted_content_label(3)
        self.assertEqual(expand_pasted_content(token, {}), token)

    def test_second_paste_of_same_length_gets_a_suffix(self) -> None:
        first = allocate_paste_token(12, {})
        second = allocate_paste_token(12, {first: "aaaaaaaaaaaa"})
        self.assertEqual(first, "[Pasted Content 12 chars]")
        self.assertEqual(second, "[Pasted Content 12 chars #2]")

    def test_keeps_a_short_prompt_in_front_of_a_dump(self) -> None:
        rest = "x" * 1200
        prefix, collapsed = split_long_user_text(f"regarde ca\n{rest}")
        self.assertEqual(prefix, "regarde ca")
        self.assertEqual(collapsed, rest)

    def test_chips_a_wall_with_no_short_prefix(self) -> None:
        blob = "y" * 1400
        prefix, collapsed = split_long_user_text(blob)
        self.assertEqual(prefix, "")
        self.assertEqual(collapsed, blob)

    def test_composer_collapse_keeps_the_body_for_send(self) -> None:
        blob = "z" * 1148
        display, pastes = collapse_text_for_composer(f"merci\n{blob}")
        self.assertEqual(display, f"merci\n[Pasted Content 1148 chars]")
        self.assertEqual(expand_pasted_content(display, pastes), f"merci\n{blob}")

    def test_composer_collapse_leaves_tokens_alone(self) -> None:
        token = pasted_content_label(1148)
        display, pastes = collapse_text_for_composer(token, {token: "kept"})
        self.assertEqual(display, token)
        self.assertEqual(pastes[token], "kept")

    def test_tui_composer_expands_before_submit(self) -> None:
        from navin.tui.widgets import Composer

        token = pasted_content_label(5)
        composer = Composer()
        composer._pastes = {token: "hello"}
        composer.load_text(f"fix {token}")
        self.assertEqual(composer.expand_for_submit(), "fix hello")


if __name__ == "__main__":
    unittest.main()
