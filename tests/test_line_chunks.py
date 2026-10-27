# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunked line splitting gives exactly what ``str.splitlines`` gives."""

from __future__ import annotations

from navin.agent.tools.filesystem import _SPLIT_CHUNK_CHARS, _iter_line_chunks


def _joined(text: str) -> list[str]:
    out: list[str] = []
    for piece in _iter_line_chunks(text):
        out.extend(piece)
    return out


def test_every_separator_kind_matches_splitlines() -> None:
    sample = "a\nb\r\nc\rd\x0be\x0cf\x1cg\x85h\u2028i\u2029j\n\n\nk"
    assert _joined(sample) == sample.splitlines()
    assert _joined("") == []
    assert _joined("\n") == [""]
    assert _joined("no newline at all") == ["no newline at all"]


def test_pieces_are_cut_after_a_newline_and_lines_survive_the_cut(monkeypatch) -> None:
    import navin.agent.tools.filesystem as fs

    monkeypatch.setattr(fs, "_SPLIT_CHUNK_CHARS", 10)
    text = "".join(f"line {n:03d}\n" for n in range(50)) + "tail without newline"
    pieces = list(fs._iter_line_chunks(text))
    assert len(pieces) > 1
    assert [line for piece in pieces for line in piece] == text.splitlines()
    # A bare \r right before a cut is still one separator, never glued to the next piece.
    tricky = ("x" * 9 + "\r") * 5 + "\n" + "y" * 25 + "\n"
    assert [line for piece in fs._iter_line_chunks(tricky) for line in piece] == tricky.splitlines()


def test_a_line_longer_than_a_piece_is_kept_whole(monkeypatch) -> None:
    import navin.agent.tools.filesystem as fs

    monkeypatch.setattr(fs, "_SPLIT_CHUNK_CHARS", 8)
    text = "short\n" + "z" * 100 + "\nend\n"
    assert [line for piece in fs._iter_line_chunks(text) for line in piece] == text.splitlines()


def test_window_matches_a_slice_of_splitlines(monkeypatch) -> None:
    import navin.agent.tools.filesystem as fs

    monkeypatch.setattr(fs, "_SPLIT_CHUNK_CHARS", 64)
    text = "".join(f"row {n}\n" for n in range(1000))
    lines = text.splitlines()
    for start, count in ((0, 10), (995, 10), (37, 300), (1000, 5), (500, 0)):
        total, window = fs._line_window(text, start, count)
        assert total == len(lines)
        assert window == lines[start:start + count]


def test_default_piece_is_one_mebibyte() -> None:
    assert _SPLIT_CHUNK_CHARS == 1 << 20
