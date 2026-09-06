"""Show a vision model the images of earlier turns again.

A screenshot is sent to the model as a native image block only in the turn it
was attached. Session history keeps the file path, and replay used to hand the
model a bare ``[image: path]`` line: on the follow-up question ("and the second
error?") the model was answering from its own first description, or had to
notice it could ``read_file`` the path.

Here the most recent images of the replayed history are decoded again into
image blocks, within a fixed budget, when the model of the turn can read them.
A text-only model keeps the breadcrumbs untouched: :mod:`navin.agent.vision_guard`
already explains that situation for the current turn.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from navin.providers.model_capabilities import supports_vision
from navin.utils.helpers import detect_image_mime

# Every replayed image costs what a fresh screenshot costs, on every call of
# the turn. Four covers "the screenshot I sent two messages ago" without
# turning a long visual session into a slideshow the model pays for each time.
REPLAY_MAX_IMAGES = 4
# Only the recent past is worth re-showing: older images are usually settled.
REPLAY_MAX_USER_TURNS = 6
# Larger files are almost never screenshots; skip them rather than blow the
# request size on a replay the user did not ask for.
REPLAY_MAX_FILE_BYTES = 6 * 1024 * 1024


def image_block_for_path(path: str) -> dict[str, Any] | None:
    """Native image block for *path*, or ``None`` when it is not a readable image."""
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > REPLAY_MAX_FILE_BYTES:
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
    if not mime or not mime.startswith("image/"):
        return None
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"},
        "_meta": {"path": str(p)},
    }


def replay_history_images(
    history: list[dict[str, Any]],
    *,
    model: str | None,
    input_modalities: list[str] | None = None,
    max_images: int = REPLAY_MAX_IMAGES,
    max_user_turns: int = REPLAY_MAX_USER_TURNS,
) -> list[dict[str, Any]]:
    """Return *history* with recent user images decoded back into image blocks.

    Entries must carry their attachment paths under ``_meta["media"]``
    (``Session.get_history(with_media_refs=True)``). The input list is not
    mutated; entries that change are shallow-copied.
    """
    if not history or max_images <= 0:
        return history
    if not supports_vision(model, input_modalities=input_modalities):
        return history

    out = list(history)
    budget = max_images
    user_turns_seen = 0
    for index in range(len(out) - 1, -1, -1):
        if budget <= 0 or user_turns_seen >= max_user_turns:
            break
        entry = out[index]
        if not isinstance(entry, dict) or entry.get("role") != "user":
            continue
        user_turns_seen += 1
        meta = entry.get("_meta")
        media = meta.get("media") if isinstance(meta, dict) else None
        if not isinstance(media, list) or not media:
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            continue

        blocks: list[dict[str, Any]] = []
        # Most recent attachment first when a turn carried several and the
        # budget cannot take them all.
        for path in reversed([p for p in media if isinstance(p, str) and p]):
            if len(blocks) >= budget:
                break
            block = image_block_for_path(path)
            if block is not None:
                blocks.append(block)
        if not blocks:
            continue
        blocks.reverse()
        budget -= len(blocks)
        replaced = dict(entry)
        replaced["content"] = [*blocks, {"type": "text", "text": content}]
        out[index] = replaced

    if budget < max_images:
        logger.debug(
            "Replayed {} history image(s) for vision model {}", max_images - budget, model
        )
    return out
