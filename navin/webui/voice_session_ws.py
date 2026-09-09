# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WebUI realtime voice session envelope handling.

Transport and subscription fan-out stay on the WebSocket channel. This module
owns the duplex voice actions: session lifecycle, STT chunks, and TTS emit.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from typing import Any

from navin.audio.transcription import (
    TranscriptionIngressError,
    resolve_transcription_config,
    transcribe_audio_data_url,
)
from navin.audio.tts import (
    TtsError,
    live_voice_status,
    resolve_tts_config,
    should_cancel_tts,
    synthesize_speech_with_config,
)
from navin.config.loader import load_config, save_config

_MAX_REQUEST_ID_LENGTH = 80
_MAX_SESSION_ID_LENGTH = 80
_MAX_SPEAK_TEXT_LENGTH = 8_000

# In-memory active sessions (gateway process lifetime).
_ACTIVE_SESSIONS: set[str] = set()
_SESSION_OWNERS: dict[str, Any] = {}
_TTS_TASKS: dict[str, set[asyncio.Task[Any]]] = {}
_TTS_EPOCHS: dict[str, object] = {}
_TTS_LIMITS: dict[str, asyncio.Semaphore] = {}


def _cancel_session_speech(session_id: str) -> None:
    _TTS_EPOCHS[session_id] = object()
    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    for task in tuple(_TTS_TASKS.get(session_id, ())):
        if task is not current:
            task.cancel()


def close_voice_sessions(owner: Any) -> None:
    """Release microphone-session ownership and pending speech on disconnect."""
    for sid, candidate in list(_SESSION_OWNERS.items()):
        if candidate is owner:
            _cancel_session_speech(sid)
            _ACTIVE_SESSIONS.discard(sid)
            _SESSION_OWNERS.pop(sid, None)
            _TTS_EPOCHS.pop(sid, None)
            _TTS_LIMITS.pop(sid, None)


def _owns_session(session_id: str | None, owner: Any) -> bool:
    return bool(session_id and session_id in _ACTIVE_SESSIONS
                and _SESSION_OWNERS.get(session_id) is owner)


def _valid_id(value: Any, *, max_len: int = _MAX_REQUEST_ID_LENGTH) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= max_len:
        return value
    return None


def _error(
    detail: str,
    *,
    session_id: str | None = None,
    request_id: str | None = None,
    **extra: Any,
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {"detail": detail, **extra}
    if session_id:
        payload["session_id"] = session_id
    if request_id:
        payload["request_id"] = request_id
    return "voice_session_error", payload


async def _synthesize_tts_event(
    speak_text: str,
    *,
    session_id: str,
    request_id: str | None,
    cancelled: bool = False,
) -> tuple[str, dict[str, Any]] | None:
    if should_cancel_tts(playing=True, user_speaking=cancelled):
        return None
    text = speak_text.strip()
    if not text:
        return None
    if len(text) > _MAX_SPEAK_TEXT_LENGTH:
        return _error("speak_text_too_long", session_id=session_id, request_id=request_id)
    cfg = resolve_tts_config(load_config())
    epoch = _TTS_EPOCHS.setdefault(session_id, object())
    tasks = _TTS_TASKS.setdefault(session_id, set())
    task = asyncio.current_task()
    if task is not None:
        tasks.add(task)
    try:
        async with _TTS_LIMITS.setdefault(session_id, asyncio.Semaphore(3)):
            audio = await synthesize_speech_with_config(text, cfg)
    except TtsError as exc:
        extra = {
            key: value
            for key, value in exc.extra.items()
            if key != "provider" or str(value).strip().lower() != "openrouter"
        }
        return _error(exc.detail, session_id=session_id, request_id=request_id, **extra)
    finally:
        if task is not None:
            tasks.discard(task)
        if not tasks and _TTS_TASKS.get(session_id) is tasks:
            _TTS_TASKS.pop(session_id, None)
    if epoch is not _TTS_EPOCHS.get(session_id) or should_cancel_tts(playing=True, user_speaking=cancelled):
        return None
    mime = "audio/mpeg"
    if cfg.response_format == "wav":
        mime = "audio/wav"
    payload: dict[str, Any] = {
        "session_id": session_id,
        "mime": mime,
        "audio_base64": base64.b64encode(audio).decode("ascii"),
    }
    if request_id:
        payload["request_id"] = request_id
    return "tts_audio", payload


async def webui_voice_session_events(
    envelope: dict[str, Any],
    *,
    owner: Any = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return zero or more WS (event, payload) pairs for one voice envelope."""
    msg_type = envelope.get("type")
    if msg_type == "voice_session_start":
        return await _handle_start(envelope, owner=owner)
    if msg_type == "voice_audio_chunk":
        return await _handle_audio_chunk(envelope, owner=owner)
    if msg_type == "voice_session_end":
        return await _handle_end(envelope, owner=owner)
    if msg_type == "voice_prompt":
        return await _handle_prompt(envelope, owner=owner)
    return [_error("unknown_voice_message", request_id=_valid_id(envelope.get("request_id")))]


async def _handle_prompt(envelope: dict[str, Any], *, owner: Any = None) -> list[tuple[str, dict[str, Any]]]:
    """Live conversation: turn a raw transcript into the message the user would have typed.

    Runs after the STT round trip so the client can already show what was
    heard while the rewrite is in flight. Falls back to the raw text.
    """
    request_id = _valid_id(envelope.get("request_id"))
    session_id = _valid_id(envelope.get("session_id"), max_len=_MAX_SESSION_ID_LENGTH)
    if not _owns_session(session_id, owner):
        return [_error("invalid_session", request_id=request_id, session_id=session_id)]
    raw = envelope.get("text")
    if not isinstance(raw, str) or not raw.strip():
        return [_error("empty_prompt", session_id=session_id, request_id=request_id)]
    if len(raw) > _MAX_SPEAK_TEXT_LENGTH:
        raw = raw[:_MAX_SPEAK_TEXT_LENGTH]

    from navin.audio.voice_prompt import normalize_transcript, rewrite_voice_transcript

    context = envelope.get("context")
    prompt = await rewrite_voice_transcript(
        raw,
        context=context if isinstance(context, str) else None,
    )
    if not _owns_session(session_id, owner):
        return []
    raw_text = normalize_transcript(raw)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "raw_text": raw_text,
        "text": prompt or raw_text,
        "rewritten": bool(prompt) and prompt != raw_text,
    }
    if request_id:
        payload["request_id"] = request_id
    return [("voice_prompt_ready", payload)]


async def _handle_start(envelope: dict[str, Any], *, owner: Any = None) -> list[tuple[str, dict[str, Any]]]:
    request_id = _valid_id(envelope.get("request_id"))
    config = load_config()
    from navin.providers.managed_catalog import heal_managed_voice_settings

    # A first Live session does not depend on visiting Settings or syncing online.
    if heal_managed_voice_settings(config):
        save_config(config)
    tts = resolve_tts_config(config)
    stt = resolve_transcription_config(config)
    readiness = live_voice_status(config, stt=stt, tts=tts)
    if not readiness["ready"]:
        return [_error(readiness["reason"], request_id=request_id,
                       setup=readiness, settings_section="voice")]

    session_id = _valid_id(envelope.get("session_id"), max_len=_MAX_SESSION_ID_LENGTH)
    if session_id is None:
        session_id = uuid.uuid4().hex
    if session_id in _ACTIVE_SESSIONS and not _owns_session(session_id, owner):
        return [_error("invalid_session", request_id=request_id)]
    _ACTIVE_SESSIONS.add(session_id)
    _SESSION_OWNERS[session_id] = owner
    _TTS_EPOCHS.setdefault(session_id, object())
    payload: dict[str, Any] = {
        "session_id": session_id,
        "auto_speak": tts.auto_speak,
        "tts_provider": tts.provider,
        "tts_configured": tts.configured,
        "tts_model": tts.model,
        "stt_model": getattr(stt, "model", ""),
        "voice": tts.voice,
    }
    if request_id:
        payload["request_id"] = request_id
    return [("voice_session_started", payload)]


async def _handle_audio_chunk(envelope: dict[str, Any], *, owner: Any = None) -> list[tuple[str, dict[str, Any]]]:
    request_id = _valid_id(envelope.get("request_id"))
    session_id = _valid_id(envelope.get("session_id"), max_len=_MAX_SESSION_ID_LENGTH)
    if session_id is None or not _owns_session(session_id, owner):
        return [_error("invalid_session", request_id=request_id, session_id=session_id)]

    events: list[tuple[str, dict[str, Any]]] = []
    data_url = envelope.get("data_url")
    if data_url:
        try:
            text = await transcribe_audio_data_url(
                data_url,
                resolve_transcription_config(load_config()),
                duration_ms=envelope.get("duration_ms"),
            )
        except TranscriptionIngressError as exc:
            return [_error(exc.detail, session_id=session_id, request_id=request_id, **exc.extra)]
        if not _owns_session(session_id, owner):
            return []
        partial: dict[str, Any] = {
            "session_id": session_id,
            "text": text,
            "final": bool(envelope.get("final", False)),
        }
        if request_id:
            partial["request_id"] = request_id
        events.append(("transcript_partial", partial))

    speak_text = envelope.get("speak_text")
    barge_in = bool(envelope.get("barge_in") or envelope.get("cancel_tts"))
    if barge_in:
        # Client barge-in: cancel in-flight TTS without synthesizing.
        _cancel_session_speech(session_id)
        events.append(
            (
                "tts_cancelled",
                {
                    "session_id": session_id,
                    **({"request_id": request_id} if request_id else {}),
                },
            )
        )
    elif isinstance(speak_text, str) and speak_text.strip():
        tts_event = await _synthesize_tts_event(
            speak_text,
            session_id=session_id,
            request_id=request_id,
            cancelled=False,
        )
        if tts_event is not None:
            events.append(tts_event)
        else:
            return events

    if not events:
        return [_error("empty_chunk", session_id=session_id, request_id=request_id)]
    return events


async def _handle_end(envelope: dict[str, Any], *, owner: Any = None) -> list[tuple[str, dict[str, Any]]]:
    request_id = _valid_id(envelope.get("request_id"))
    session_id = _valid_id(envelope.get("session_id"), max_len=_MAX_SESSION_ID_LENGTH)
    if session_id is None or not _owns_session(session_id, owner):
        return [_error("invalid_session", request_id=request_id)]
    _ACTIVE_SESSIONS.discard(session_id)
    _cancel_session_speech(session_id)

    events: list[tuple[str, dict[str, Any]]] = []
    speak_text = envelope.get("speak_text")
    if isinstance(speak_text, str) and speak_text.strip():
        tts_event = await _synthesize_tts_event(
            speak_text,
            session_id=session_id,
            request_id=request_id,
            cancelled=bool(envelope.get("barge_in") or envelope.get("cancel_tts")),
        )
        if tts_event is not None:
            events.append(tts_event)

    ended: dict[str, Any] = {"session_id": session_id}
    if request_id:
        ended["request_id"] = request_id
    events.append(("voice_session_ended", ended))
    _SESSION_OWNERS.pop(session_id, None)
    _TTS_EPOCHS.pop(session_id, None)
    _TTS_LIMITS.pop(session_id, None)
    return events


def clear_voice_sessions_for_tests() -> None:
    """Test helper to reset in-memory session set."""
    _ACTIVE_SESSIONS.clear()
    for sid in list(_TTS_TASKS):
        _cancel_session_speech(sid)
    _SESSION_OWNERS.clear()
    _TTS_TASKS.clear()
    _TTS_EPOCHS.clear()
    _TTS_LIMITS.clear()
