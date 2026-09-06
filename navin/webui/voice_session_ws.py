"""WebUI realtime voice session envelope handling.

Transport and subscription fan-out stay on the WebSocket channel. This module
owns the duplex voice actions: session lifecycle, STT chunks, and TTS emit.
"""

from __future__ import annotations

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
    resolve_tts_config,
    should_cancel_tts,
    synthesize_speech_with_config,
    voice_realtime_allowed,
)
from navin.config.loader import load_config

_MAX_REQUEST_ID_LENGTH = 80
_MAX_SESSION_ID_LENGTH = 80
_MAX_SPEAK_TEXT_LENGTH = 8_000

# In-memory active sessions (gateway process lifetime).
_ACTIVE_SESSIONS: set[str] = set()


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
    try:
        audio = await synthesize_speech_with_config(text, resolve_tts_config(load_config()))
    except TtsError as exc:
        extra = {
            key: value
            for key, value in exc.extra.items()
            if key != "provider" or str(value).strip().lower() != "openrouter"
        }
        return _error(exc.detail, session_id=session_id, request_id=request_id, **extra)
    if should_cancel_tts(playing=True, user_speaking=cancelled):
        return None
    mime = "audio/mpeg"
    cfg = resolve_tts_config(load_config())
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
) -> list[tuple[str, dict[str, Any]]]:
    """Return zero or more WS (event, payload) pairs for one voice envelope."""
    msg_type = envelope.get("type")
    if msg_type == "voice_session_start":
        return await _handle_start(envelope)
    if msg_type == "voice_audio_chunk":
        return await _handle_audio_chunk(envelope)
    if msg_type == "voice_session_end":
        return await _handle_end(envelope)
    return [_error("unknown_voice_message", request_id=_valid_id(envelope.get("request_id")))]


async def _handle_start(envelope: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    request_id = _valid_id(envelope.get("request_id"))
    config = load_config()
    if not voice_realtime_allowed(config):
        return [
            _error(
                "plan_required",
                request_id=request_id,
                required_plans=["pro", "ultra", "team"],
            )
        ]
    tts = resolve_tts_config(config)
    stt = resolve_transcription_config(config)
    if not stt.enabled:
        return [_error("stt_disabled", request_id=request_id)]
    if not stt.configured:
        return [
            _error(
                "stt_not_configured",
                request_id=request_id,
                **(
                    {"provider": stt.provider}
                    if str(stt.provider).strip().lower() != "openrouter"
                    else {}
                ),
            )
        ]

    session_id = _valid_id(envelope.get("session_id"), max_len=_MAX_SESSION_ID_LENGTH)
    if session_id is None:
        session_id = uuid.uuid4().hex
    _ACTIVE_SESSIONS.add(session_id)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "auto_speak": tts.auto_speak,
        "tts_provider": tts.provider,
        "tts_configured": tts.configured,
        "voice": tts.voice,
    }
    if request_id:
        payload["request_id"] = request_id
    return [("voice_session_started", payload)]


async def _handle_audio_chunk(envelope: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    request_id = _valid_id(envelope.get("request_id"))
    session_id = _valid_id(envelope.get("session_id"), max_len=_MAX_SESSION_ID_LENGTH)
    if session_id is None or session_id not in _ACTIVE_SESSIONS:
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

    if not events:
        return [_error("empty_chunk", session_id=session_id, request_id=request_id)]
    return events


async def _handle_end(envelope: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    request_id = _valid_id(envelope.get("request_id"))
    session_id = _valid_id(envelope.get("session_id"), max_len=_MAX_SESSION_ID_LENGTH)
    if session_id is None:
        return [_error("invalid_session", request_id=request_id)]
    _ACTIVE_SESSIONS.discard(session_id)

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
    return events


def clear_voice_sessions_for_tests() -> None:
    """Test helper to reset in-memory session set."""
    _ACTIVE_SESSIONS.clear()
