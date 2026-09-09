# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Artifact persistence helpers for generated media."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from navin.config.paths import get_media_dir
from navin.utils.helpers import detect_image_mime, ensure_dir

_DATA_IMAGE_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", re.DOTALL)
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_VIDEO_MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
_AUDIO_MIME_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
}

class ArtifactError(ValueError):
    """Raised when an artifact cannot be safely decoded or stored."""


def decode_image_data_url(data_url: str) -> tuple[bytes, str]:
    """Decode a base64 image data URL and return ``(bytes, mime)``."""
    match = _DATA_IMAGE_RE.match(data_url.strip())
    if match is None:
        raise ArtifactError("expected a base64 image data URL")

    declared_mime, encoded = match.groups()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ArtifactError("invalid base64 image payload") from exc

    detected_mime = detect_image_mime(raw)
    if detected_mime is None:
        raise ArtifactError("unsupported or unrecognized image data")
    if declared_mime != detected_mime:
        declared_mime = detected_mime
    return raw, declared_mime


def _safe_relative_dir(save_dir: str) -> Path:
    normalized = save_dir.replace("\\", "/").strip("/")
    if not normalized:
        raise ArtifactError("save_dir must not be empty")
    rel = PurePosixPath(normalized)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ArtifactError("save_dir must be a safe relative path")
    return Path(*rel.parts)


def artifact_directory(save_dir: str) -> Path:
    """Resolve a configured subfolder inside the media dir, refusing escapes."""
    media_root = get_media_dir().resolve()
    root = (media_root / _safe_relative_dir(save_dir)).resolve()
    try:
        root.relative_to(media_root)
    except ValueError as exc:
        raise ArtifactError("artifact directory escapes media root") from exc
    return root


def store_generated_image_artifact(
    data_url: str,
    *,
    prompt: str,
    model: str,
    source_images: list[str] | None = None,
    save_dir: str = "generated",
    provider: str = "openrouter",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist a generated image and sidecar metadata under the media root."""
    raw, mime = decode_image_data_url(data_url)
    ext = _MIME_EXTENSIONS.get(mime)
    if ext is None:
        raise ArtifactError(f"unsupported image MIME type: {mime}")

    now = created_at or datetime.now().astimezone()
    day_dir = ensure_dir(artifact_directory(save_dir) / now.strftime("%Y-%m-%d"))
    artifact_id = f"img_{uuid.uuid4().hex[:12]}"
    image_path = day_dir / f"{artifact_id}{ext}"
    metadata_path = day_dir / f"{artifact_id}.json"

    image_path.write_bytes(raw)
    metadata: dict[str, Any] = {
        "id": artifact_id,
        "path": str(image_path),
        "mime": mime,
        "prompt": prompt,
        "model": model,
        "provider": provider,
        "source_images": list(source_images or []),
        "created_at": now.isoformat(),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def store_generated_video_artifact(
    video: bytes,
    *,
    mime: str,
    prompt: str,
    model: str,
    source_images: list[str] | None = None,
    save_dir: str = "generated-video",
    provider: str = "gemini",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist a generated video and sidecar metadata under the media root."""
    if not video:
        raise ArtifactError("generated video payload is empty")
    ext = _VIDEO_MIME_EXTENSIONS.get(mime)
    if ext is None:
        raise ArtifactError(f"unsupported video MIME type: {mime}")

    now = created_at or datetime.now().astimezone()
    day_dir = ensure_dir(artifact_directory(save_dir) / now.strftime("%Y-%m-%d"))
    artifact_id = f"vid_{uuid.uuid4().hex[:12]}"
    video_path = day_dir / f"{artifact_id}{ext}"
    metadata_path = day_dir / f"{artifact_id}.json"

    video_path.write_bytes(video)
    metadata: dict[str, Any] = {
        "id": artifact_id,
        "path": str(video_path),
        "mime": mime,
        "prompt": prompt,
        "model": model,
        "provider": provider,
        "source_images": list(source_images or []),
        "created_at": now.isoformat(),
        "size_bytes": len(video),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def generated_video_tool_result(artifacts: list[dict[str, Any]]) -> str:
    """Return the compact structured result exposed to the LLM."""
    return json.dumps(
        {
            "artifacts": artifacts,
            "next_step": (
                "Call the message tool with the artifact paths in the media parameter "
                "to deliver the video to the user. Keep raw paths internal unless the "
                "user asks for debug details."
            ),
        },
        ensure_ascii=False,
    )


def store_generated_music_artifact(
    audio: bytes,
    *,
    mime: str,
    prompt: str,
    model: str,
    source_images: list[str] | None = None,
    save_dir: str = "generated-music",
    provider: str = "openrouter",
    transcript: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist a generated music clip and sidecar metadata under the media root."""
    if not audio:
        raise ArtifactError("generated music payload is empty")
    ext = _AUDIO_MIME_EXTENSIONS.get(mime) or _AUDIO_MIME_EXTENSIONS.get(mime.lower())
    if ext is None:
        # Lyria defaults to MP3; keep a usable artifact rather than failing hard.
        ext = ".mp3"
        mime = "audio/mpeg"

    now = created_at or datetime.now().astimezone()
    day_dir = ensure_dir(artifact_directory(save_dir) / now.strftime("%Y-%m-%d"))
    artifact_id = f"mus_{uuid.uuid4().hex[:12]}"
    audio_path = day_dir / f"{artifact_id}{ext}"
    metadata_path = day_dir / f"{artifact_id}.json"

    audio_path.write_bytes(audio)
    metadata: dict[str, Any] = {
        "id": artifact_id,
        "path": str(audio_path),
        "mime": mime,
        "prompt": prompt,
        "model": model,
        "provider": provider,
        "source_images": list(source_images or []),
        "created_at": now.isoformat(),
        "size_bytes": len(audio),
    }
    if transcript:
        metadata["transcript"] = transcript
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def generated_music_tool_result(artifacts: list[dict[str, Any]]) -> str:
    """Return the compact structured result exposed to the LLM."""
    return json.dumps(
        {
            "artifacts": artifacts,
            "next_step": (
                "Call the message tool with the artifact paths in the media parameter "
                "to deliver the music to the user. Keep raw paths internal unless the "
                "user asks for debug details."
            ),
        },
        ensure_ascii=False,
    )


def store_generated_speech_artifact(
    audio: bytes,
    *,
    mime: str,
    text: str,
    model: str,
    voice: str,
    save_dir: str = "generated-speech",
    provider: str = "navin",
    language: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist a synthesized voice track and sidecar metadata under the media root.

    Narration is kept apart from music so the montage step can pick the spoken
    track for ducking without guessing from filenames.
    """
    if not audio:
        raise ArtifactError("generated speech payload is empty")
    ext = _AUDIO_MIME_EXTENSIONS.get(mime) or _AUDIO_MIME_EXTENSIONS.get(mime.lower())
    if ext is None:
        ext = ".mp3"
        mime = "audio/mpeg"

    now = created_at or datetime.now().astimezone()
    day_dir = ensure_dir(artifact_directory(save_dir) / now.strftime("%Y-%m-%d"))
    artifact_id = f"spk_{uuid.uuid4().hex[:12]}"
    audio_path = day_dir / f"{artifact_id}{ext}"
    metadata_path = day_dir / f"{artifact_id}.json"

    audio_path.write_bytes(audio)
    metadata: dict[str, Any] = {
        "id": artifact_id,
        "path": str(audio_path),
        "mime": mime,
        "kind": "speech",
        "text": text,
        "model": model,
        "voice": voice,
        "provider": provider,
        "created_at": now.isoformat(),
        "size_bytes": len(audio),
    }
    if language:
        metadata["language"] = language
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def generated_speech_tool_result(artifacts: list[dict[str, Any]]) -> str:
    """Return the compact structured result exposed to the LLM."""
    return json.dumps(
        {
            "artifacts": artifacts,
            "next_step": (
                "Call the message tool with the artifact paths in the media parameter "
                "to deliver the voice track to the user. For a full video, pass these "
                "paths as the voice track of the montage assemble step. Keep raw paths "
                "internal unless the user asks for debug details."
            ),
        },
        ensure_ascii=False,
    )


def generated_image_tool_result(artifacts: list[dict[str, Any]]) -> str:
    """Return the compact structured result exposed to the LLM."""
    return json.dumps(
        {
            "artifacts": artifacts,
            "next_step": (
                "Use these artifact paths as reference_images for follow-up edits. "
                "Call the message tool with the artifact paths in the media parameter "
                "to deliver the images to the user. Keep raw paths internal unless the "
                "user asks for debug details."
            ),
        },
        ensure_ascii=False,
    )
