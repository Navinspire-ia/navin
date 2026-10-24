# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The realtime Live engine: one voice-to-voice model, bridged by the gateway.

The browser speaks the OpenAI Realtime protocol (session.update,
input_audio_buffer.append, response.*...) to ``/api/live/realtime`` on the
local gateway. The gateway opens the upstream socket with the credentials,
which never reach the page, and relays frames both ways.

Upstream: the Live model chosen in Settings > Voice, nothing else (no model
is ever picked behind the user's back):

- ``navin``: the plan's LiteLLM gateway (``license.managed_api_base`` +
  managed key). LiteLLM bridges Nova Sonic (Bedrock), GPT Realtime, Gemini
  Live... behind one protocol; the model is the admin's Live default, synced
  into ``voice.live_realtime_model`` from the managed catalog.
- ``openai``: the user's own OpenAI key (``providers.openai``).
- ``gemini``: the user's own Gemini key, translated to the Gemini Live API
  (navin/live/gemini_live.py).
- ``qwen``: Qwen Omni Realtime with the user's own Alibaba key (16 kHz microphone).
- ``xai``: Grok Voice with the user's own xAI key (OpenAI Realtime-compatible).
- ``bedrock``: Nova Sonic with the user's own AWS credentials, over Bedrock's
  bidirectional stream (navin/live/nova_sonic.py).

When nothing is available (pipeline chosen, no model, no key) the bridge says
so at once and the page falls back to the STT -> chat -> TTS pipeline.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from loguru import logger
from websockets.exceptions import ConnectionClosed, WebSocketException

PATH = "/api/live/realtime"
STATUS_PATH = "/api/live/realtime/status"
OPENAI_DEFAULT_MODEL = "gpt-realtime"
XAI_DEFAULT_MODEL = "grok-voice-latest"
# One realtime turn of audio is small; a few MB covers any upstream frame.
MAX_FRAME_BYTES = 8 * 1024 * 1024
OPEN_TIMEOUT_S = 15.0
# A transient upstream hiccup (DNS blip, 5xx, reset) gets one more try before the page falls back.
CONNECT_ATTEMPTS = 2
CONNECT_RETRY_DELAY_S = 0.5
# WebSocket close codes that mean "the session simply ended" rather than a failure.
_CLEAN_CLOSE_CODES = {1000, 1001}


@dataclass(frozen=True)
class RealtimeTarget:
    provider: str
    model: str
    url: str
    headers: dict[str, str]

    def public(self) -> dict[str, Any]:
        return {"available": True, "provider": self.provider, "model": self.model}


def _ws_base(http_base: str) -> str:
    base = http_base.strip().rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    return base


def resolve_realtime_target(config: Any) -> RealtimeTarget | None:
    """Where the realtime engine connects, or None (the pipeline is used)."""
    voice = config.voice
    if getattr(voice, "live_engine", "auto") == "pipeline" or getattr(voice, "realtime_enabled", None) is False:
        return None
    provider = (getattr(voice, "live_realtime_provider", "") or "").strip().lower()
    model = (getattr(voice, "live_realtime_model", "") or "").strip()

    if provider in {"", "navin"}:
        base = (getattr(config.license, "managed_api_base", "") or "").strip()
        key = (getattr(config.providers.navin, "api_key", "") or "").strip()
        if base and key and model and base.startswith("https://"):
            return RealtimeTarget(
                provider="navin",
                model=model,
                url=f"{_ws_base(base)}/realtime?model={quote(model, safe='/._-:')}",
                headers={"Authorization": f"Bearer {key}"},
            )
        if provider == "navin":
            return None

    if provider == "gemini":
        gemini_cfg = getattr(config.providers, "gemini", None)
        key = (getattr(gemini_cfg, "api_key", "") or "").strip() if gemini_cfg else ""
        if not key or not model:
            return None
        from navin.live.gemini_live import GEMINI_LIVE_URL

        return RealtimeTarget(provider="gemini", model=model, url=GEMINI_LIVE_URL, headers={"x-goog-api-key": key})

    if provider == "bedrock":
        # Nova Sonic with the Bedrock provider's AWS credentials (navin/live/nova_sonic.py).
        from navin.providers.bedrock_media import decode_target, model_region, provider_credentials

        api_key, target = provider_credentials(getattr(config.providers, "bedrock", None))
        if not api_key:
            return None
        from navin.live.nova_sonic import DEFAULT_MODEL

        chosen = model or DEFAULT_MODEL
        region, profile = decode_target(target)
        url = f"bedrock://{model_region(chosen, region)}" + (f"?profile={quote(profile)}" if profile else "")
        return RealtimeTarget(provider="bedrock", model=chosen, url=url, headers={})

    if provider == "xai":
        # Grok Voice: OpenAI Realtime-compatible WebSocket (navin/live/realtime.py XaiTranscripts).
        xai_cfg = getattr(config.providers, "xai", None)
        key = (getattr(xai_cfg, "api_key", "") or "").strip() if xai_cfg else ""
        if not key:
            import os

            key = (os.environ.get("XAI_API_KEY") or "").strip()
        if not key:
            return None
        base = (getattr(xai_cfg, "api_base", "") or "").strip() if xai_cfg else ""
        chosen = model or XAI_DEFAULT_MODEL
        return RealtimeTarget(
            provider="xai",
            model=chosen,
            url=f"{_ws_base(base or 'https://api.x.ai/v1')}/realtime?model={quote(chosen, safe='/._-:')}",
            headers={"Authorization": f"Bearer {key}"},
        )

    if provider == "qwen":
        # Qwen Omni Realtime on the region / workspace host of the Qwen provider.
        from navin.providers.qwen_media import api_key_or_env, native_host

        qwen_cfg = getattr(config.providers, "qwen", None)
        key = api_key_or_env(getattr(qwen_cfg, "api_key", "") if qwen_cfg else "")
        if not key or not model:
            return None
        host = native_host(getattr(qwen_cfg, "api_base", "") if qwen_cfg else "")
        return RealtimeTarget(
            provider="qwen",
            model=model,
            url=f"{_ws_base(host)}/api-ws/v1/realtime?model={quote(model, safe='/._-:')}",
            headers={"Authorization": f"Bearer {key}"},
        )

    if provider == "openai":
        openai_cfg = getattr(config.providers, "openai", None)
        key = (getattr(openai_cfg, "api_key", "") or "").strip() if openai_cfg else ""
        if not key:
            return None
        base = (getattr(openai_cfg, "api_base", "") or "").strip() or "https://api.openai.com/v1"
        chosen = model or OPENAI_DEFAULT_MODEL
        return RealtimeTarget(
            provider="openai",
            model=chosen,
            url=f"{_ws_base(base)}/realtime?model={quote(chosen, safe='/._-:')}",
            headers={"Authorization": f"Bearer {key}", "OpenAI-Beta": "realtime=v1"},
        )
    return None


class XaiTranscripts:
    """Grok Voice sends what the user says as a running transcript
    (``...input_audio_transcription.updated``, cumulative). The page shows
    the ``.completed`` event: emit it once per item, when the answer starts,
    unless Grok already sent one."""

    def __init__(self) -> None:
        self.pending: dict[str, str] = {}

    def events(self, frame: str) -> list[str]:
        try:
            event = json.loads(frame)
        except ValueError:
            return [frame]
        if not isinstance(event, dict):
            return [frame]
        kind = event.get("type")
        item = str(event.get("item_id") or "")
        if kind == "conversation.item.input_audio_transcription.updated":
            self.pending[item] = str(event.get("transcript") or event.get("text") or "")
            return []
        if kind == "conversation.item.input_audio_transcription.completed":
            self.pending.pop(item, None)
            return [frame]
        if kind == "response.created" and self.pending:
            out = [json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                               "item_id": key, "transcript": text.strip()})
                   for key, text in self.pending.items() if text.strip()]
            self.pending.clear()
            return [*out, frame]
        return [frame]


QWEN_OMNI_VOICES = ("Cherry", "Serena", "Ethan", "Chelsie", "Tina")


class QwenRealtimeFrames:
    """Page frames for Qwen Omni Realtime: same event names as the OpenAI beta
    protocol, but the microphone must be 16 kHz PCM (the page sends 24 kHz),
    the formats are named ``pcm`` and the voice is one of Qwen's."""

    def from_page(self, frame: str) -> str | None:
        try:
            event = json.loads(frame)
        except ValueError:
            return frame
        if not isinstance(event, dict):
            return frame
        kind = event.get("type")
        if kind == "input_audio_buffer.append":
            import base64

            from navin.live.gemini_live import resample_pcm16_24k_to_16k

            try:
                pcm = base64.b64decode(str(event.get("audio") or ""))
            except ValueError:
                return None
            audio = resample_pcm16_24k_to_16k(pcm)
            if not audio:
                return None
            return json.dumps({"type": kind, "audio": base64.b64encode(audio).decode("ascii")})
        if kind == "session.update":
            session = dict(event.get("session") or {})
            session["modalities"] = ["text", "audio"]
            session["input_audio_format"] = "pcm"
            session["output_audio_format"] = "pcm"
            voice = str(session.get("voice") or "")
            session["voice"] = next((v for v in QWEN_OMNI_VOICES if v.lower() == voice.lower()), "Cherry")
            return json.dumps({**event, "session": session})
        return frame


class NovaGatewayFrames:
    """Translate Realtime's flat tools for the managed Bedrock adapter.

    LiteLLM's pinned Nova adapter reads Chat Completions-style nested
    functions. Passing Realtime's flat definitions creates empty tool names.
    Keep this compatibility step on the Navin Nova route only.
    """

    def from_page(self, frame: str) -> str | None:
        try:
            event = json.loads(frame)
        except ValueError:
            return frame
        if not isinstance(event, dict):
            return frame
        if event.get("type") == "input_audio_buffer.append":
            import base64

            from navin.live.gemini_live import resample_pcm16_24k_to_16k

            try:
                pcm = base64.b64decode(str(event.get("audio") or ""), validate=True)
            except ValueError:
                return None
            # LiteLLM's Nova adapter interprets pcm16 input as 16 kHz.
            audio = resample_pcm16_24k_to_16k(pcm)
            return json.dumps({**event, "audio": base64.b64encode(audio).decode("ascii")}) if audio else None
        if event.get("type") == "conversation.item.create":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                output = item.get("output", "")
                try:
                    parsed = json.loads(output) if isinstance(output, str) else output
                except ValueError:
                    parsed = None
                # Bedrock parses tool content as JSON even with text/plain.
                # LiteLLM forwards string outputs verbatim on this route.
                if not isinstance(parsed, (dict, list)):
                    parsed = {"result": output}
                return json.dumps({**event, "item": {**item, "output": json.dumps(parsed, ensure_ascii=False)}})
        if event.get("type") != "session.update":
            return frame
        session = event.get("session")
        if not isinstance(session, dict):
            return frame
        if not str(session.get("voice") or "").strip() or str(session.get("voice")).lower() == "auto":
            from navin.live.nova_sonic import _voice_for

            session = {**session, "voice": _voice_for(session)}
        if not isinstance(session.get("tools"), list):
            return json.dumps({**event, "session": session})
        tools = []
        for tool in session["tools"]:
            if isinstance(tool, dict) and tool.get("type") == "function" and "function" not in tool:
                tool = {"type": "function", "function": {
                    key: tool[key] for key in ("name", "description", "parameters") if key in tool
                }}
            tools.append(tool)
        return json.dumps({**event, "session": {**session, "tools": tools}})


def realtime_status(config: Any) -> dict[str, Any]:
    from navin.audio.tts import live_voice_status

    engine = getattr(config.voice, "live_engine", "auto")
    target = resolve_realtime_target(config)
    status: dict[str, Any] = target.public() if target else {"available": False}
    status["engine"] = engine
    pipeline = live_voice_status(config)
    status["pipeline_ready"] = pipeline["ready"]
    status["pipeline_reason"] = pipeline["reason"]
    return status


def _error_frame(code: str, message: str = "") -> str:
    return json.dumps({"type": "error", "error": {"type": "navin_bridge", "code": code, "message": message}})


async def _connect(target: RealtimeTarget) -> Any:
    from websockets.asyncio.client import connect

    return await connect(
        target.url,
        additional_headers=target.headers,
        max_size=MAX_FRAME_BYTES,
        open_timeout=OPEN_TIMEOUT_S,
        ping_interval=20,
        ping_timeout=20,
    )


async def bridge(browser: Any, config: Any, *, connect: Any = None) -> None:
    """Relay one Live session between the page and the realtime upstream."""
    target = resolve_realtime_target(config)
    if target is None:
        with suppress(Exception):
            await browser.send(_error_frame("realtime_unavailable"))
            await browser.close(4404, "realtime unavailable")
        return
    if target.provider == "bedrock":
        # Bedrock's bidirectional stream is not a WebSocket: its own bridge.
        from navin.live import nova_sonic
        from navin.providers.bedrock_media import decode_target

        region, profile = decode_target(target.url)
        await nova_sonic.bridge(browser, model=target.model, region=region, profile=profile, error_frame=_error_frame)
        return
    try:
        upstream = await _connect_with_retry(connect or _connect, target)
    except Exception as exc:  # noqa: BLE001 - surfaced to the page, which falls back
        status = _http_status(exc)
        code = "realtime_busy" if status in {429, 503} else "realtime_unreachable"
        logger.warning("live realtime: upstream refused ({}, {}): {}", target.provider, code, exc)
        with suppress(Exception):
            await browser.send(_error_frame(code, _describe(exc)))
            await browser.close(4502, code)
        return

    try:
        await browser.send(json.dumps({"type": "navin.ready", "provider": target.provider, "model": target.model}))
    except (ConnectionClosed, OSError) as exc:
        # The page left while we were connecting: close the upstream instead of leaking it.
        logger.info("live realtime: page gone before ready: {}", exc)
        with suppress(Exception):
            await upstream.close()
        return

    translator = None
    xai_transcripts = XaiTranscripts() if target.provider == "xai" else None
    qwen_frames = QwenRealtimeFrames() if target.provider == "qwen" else None
    nova_frames = NovaGatewayFrames() if target.provider == "navin" and "sonic" in target.model.lower() else None
    if target.provider == "gemini":
        from navin.live.gemini_live import GeminiLiveTranslator

        translator = GeminiLiveTranslator(target.model)

    async def page_to_upstream() -> None:
        async for frame in browser:
            if isinstance(frame, bytes):
                continue
            if translator is None:
                outgoing = (qwen_frames.from_page(frame) if qwen_frames else
                            nova_frames.from_page(frame) if nova_frames else frame)
                if outgoing is not None:
                    await upstream.send(outgoing)
                continue
            try:
                parsed = json.loads(frame)
            except ValueError:
                continue
            for message in translator.from_page(parsed if isinstance(parsed, dict) else {}):
                await upstream.send(json.dumps(message))

    async def upstream_to_page() -> None:
        async for frame in upstream:
            if translator is None:
                if isinstance(frame, bytes):
                    continue
                for message in (xai_transcripts.events(frame) if xai_transcripts else [frame]):
                    await browser.send(message)
                continue
            # Gemini Live sends its JSON in binary frames as well.
            try:
                parsed = json.loads(frame.decode("utf-8") if isinstance(frame, bytes) else frame)
            except (ValueError, UnicodeDecodeError):
                continue
            for event in translator.from_gemini(parsed if isinstance(parsed, dict) else {}):
                await browser.send(json.dumps(event))

    tasks = [asyncio.create_task(page_to_upstream()), asyncio.create_task(upstream_to_page())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is None:
                continue
            reason = _drop_reason(exc)
            if reason is None:
                continue
            logger.warning("live realtime: session ended ({}): {}", target.provider, reason)
            with suppress(Exception):
                await browser.send(_error_frame("realtime_dropped", reason))
    finally:
        with suppress(Exception):
            await upstream.close()
        with suppress(Exception):
            await browser.close()


def _http_status(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _describe(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:200]


async def _connect_with_retry(connect: Any, target: RealtimeTarget) -> Any:
    """Open the upstream socket, retrying once on a transient failure.

    A 4xx other than 429 (bad key, unknown model, forbidden) is final and is
    not retried: trying again would only delay the page's fallback.
    """
    last: BaseException | None = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return await connect(target)
        except (OSError, WebSocketException) as exc:
            # Network and handshake failures only; a final 4xx or the last attempt is re-raised below.
            last = exc
            status = _http_status(exc)
            final = status is not None and 400 <= status < 500 and status != 429
            if final or attempt == CONNECT_ATTEMPTS:
                raise
            logger.info("live realtime: connect attempt {} failed ({}), retrying", attempt, _describe(exc))
            await asyncio.sleep(CONNECT_RETRY_DELAY_S)
    raise last if last else RuntimeError("realtime connect failed")


def _drop_reason(exc: BaseException) -> str | None:
    """What to tell the page when a relay task died, or None for a clean close."""
    if not _is_closed(exc):
        return _describe(exc)
    # websockets: rcvd is the frame the peer sent (None when the connection just vanished).
    rcvd = getattr(exc, "rcvd", None)
    if rcvd is None:
        return "connection lost"
    code = getattr(rcvd, "code", None)
    if code in _CLEAN_CLOSE_CODES:
        return None
    reason = str(getattr(rcvd, "reason", "") or "").strip()
    return f"upstream closed ({code}{': ' + reason if reason else ''})"[:200]


def _is_closed(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionClosed)
