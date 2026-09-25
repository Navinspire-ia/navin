# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Escape text for Textual markup (Static, notify, Content.from_markup)."""

from __future__ import annotations


def escape(text: object) -> str:
    """Insert arbitrary text into Textual markup as literal characters.

    ``rich.markup.escape`` follows Rich's grammar: it leaves ``[$var]``,
    ``[/]`` after a backslash and truncated tags alone, which Textual then
    parses as markup and fails on (``auto closing tag ('[/]') has nothing to
    close``). Textual reads ``\\[`` as a literal bracket and keeps every other
    backslash, so escaping each ``[`` round-trips any model, log or path text.
    """
    return str(text).replace("[", "\\[")
