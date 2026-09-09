# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Multi-file writes that either all land or none do.

Shared by every tool that edits more than one file at a time. A partial write is
worse than a refused one: half a rename leaves the project uncompilable, and the
agent has no record of which half succeeded. Callers therefore hand over the
complete set of final file contents and this module makes it atomic, records the
baselines the checkpoint store needs, and tells the session tracker what changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from navin.agent.checkpoints import record_file_before
from navin.utils import text_decode


def encode_for(content: str, source: text_decode.DecodedText | None) -> bytes:
    """Encode final text in the file's original encoding, newlines untouched.

    ``content`` already carries the line endings it should be written with, so
    the layout is passed with ``crlf`` cleared: re-applying the conversion here
    would double every carriage return in a CRLF file.
    """
    if source is None:
        return content.encode("utf-8")
    layout = text_decode.DecodedText(
        text="", encoding=source.encoding, bom=source.bom, crlf=False
    )
    return text_decode.encode(content, layout)


def write_all(
    writes: Mapping[Path, str],
    *,
    encodings: Mapping[Path, text_decode.DecodedText] | None = None,
    file_states=None,
) -> None:
    """Write every path, or restore all of them and re-raise.

    ``encodings`` carries what each file was decoded from, so a cp1252 or
    BOM-prefixed file is not silently rewritten as UTF-8. Files absent from it
    are written as UTF-8.
    """
    sources = encodings or {}
    backups: dict[Path, bytes | None] = {}
    for path in writes:
        record_file_before(path)
        backups[path] = path.read_bytes() if path.exists() else None

    try:
        for path, content in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encode_for(content, sources.get(path)))
    except Exception:
        for path, data in backups.items():
            if data is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        raise

    if file_states is not None:
        for path in writes:
            file_states.record_write(path)
