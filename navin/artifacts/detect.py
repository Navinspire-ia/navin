"""Light auto-detect for fenced HTML / Mermaid blocks in assistant text."""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Match ```html / ```mermaid fences (optional language attrs ignored).
_FENCE_RE = re.compile(
    r"```(?P<lang>html|mermaid)[^\n]*\n(?P<body>.*?)(?:\n```|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def extract_fenced_artifacts(text: str) -> list[dict[str, str]]:
    """Return detected ``html`` / ``mermaid`` fence payloads from *text*.

    Each item: ``{"type", "title", "content", "id"}``. Ids are stable for the
    same content so re-detecting the same message upserts rather than duplicates.
    """
    if not text or "```" not in text:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _FENCE_RE.finditer(text):
        lang = match.group("lang").strip().lower()
        body = (match.group("body") or "").strip("\n")
        if not body.strip():
            continue
        digest = hashlib.sha1(f"{lang}\n{body}".encode("utf-8")).hexdigest()[:10]
        artifact_id = f"auto-{lang}-{digest}"
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        title = "HTML preview" if lang == "html" else "Mermaid diagram"
        found.append(
            {
                "type": lang,
                "title": title,
                "content": body,
                "id": artifact_id,
            }
        )
    return found


def upsert_fenced_artifacts(
    chat_id: str,
    text: str,
    *,
    store: Any | None = None,
    root: Any | None = None,
) -> list[dict[str, Any]]:
    """Detect fences in *text* and upsert them into the chat artifact store."""
    from navin.artifacts.store import ArtifactStore

    blocks = extract_fenced_artifacts(text)
    if not blocks:
        return []
    artifact_store = store or ArtifactStore(chat_id, root=root)
    results: list[dict[str, Any]] = []
    for block in blocks:
        results.append(
            artifact_store.upsert(
                artifact_type=block["type"],
                title=block["title"],
                content=block["content"],
                artifact_id=block["id"],
            )
        )
    return results
