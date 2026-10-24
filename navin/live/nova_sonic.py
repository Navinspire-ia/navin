# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Nova Sonic (AWS Bedrock) behind the Live realtime engine.

The page always speaks the OpenAI Realtime protocol (navin/live/realtime.py).
For Nova Sonic with the user's own AWS credentials, the gateway opens Bedrock's
bidirectional stream (InvokeModelWithBidirectionalStream) and translates:

page -> Nova Sonic
  session.update                      -> sessionStart, promptStart (voice, tools),
                                         system prompt, then the open microphone content
  input_audio_buffer.append (24 kHz)  -> audioInput (16 kHz PCM)
  conversation.item.create (text)     -> a user text content
  conversation.item.create (tool out) -> a tool result content
  response.create with instructions    -> a user text content (the call's opening line)

Nova Sonic -> page
  audioOutput (24 kHz PCM)            -> response.created, response.audio.delta
  textOutput USER                     -> conversation.item.input_audio_transcription.completed
  textOutput ASSISTANT (speculative)  -> response.audio_transcript.done
  textOutput {"interrupted": true}    -> input_audio_buffer.speech_started (barge-in)
  toolUse                             -> response.function_call_arguments.done
  contentEnd END_TURN / INTERRUPTED   -> response.done
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from contextlib import suppress
from typing import Any

from loguru import logger
from websockets.exceptions import ConnectionClosed

from navin.live.gemini_live import resample_pcm16_24k_to_16k

DEFAULT_MODEL = "amazon.nova-2-sonic-v1:0"
#: Nova 2 Sonic voices (all speak several languages).
VOICES = (
    "tiffany", "matthew", "amy", "olivia", "kiara", "arjun", "ambre", "florian",
    "beatrice", "lorenzo", "tina", "lennart", "lupe", "carlos", "carolina", "leo",
)
_FRENCH_HINTS = ("french", "français", "francais", "en français", "language is fr")
_ARABIC_HINTS = ("arabic", "arabe", "العربية")


def _voice_for(session: dict[str, Any]) -> str:
    asked = str(session.get("voice") or "").strip().lower()
    if asked in VOICES:
        return asked
    instructions = str(session.get("instructions") or "").lower()
    if any(hint in instructions for hint in _FRENCH_HINTS):
        return "ambre"
    if any(hint in instructions for hint in _ARABIC_HINTS):
        return "tiffany"
    return "tiffany"


def _tool_specs(tools: Any) -> list[dict[str, Any]]:
    specs = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function" or not tool.get("name"):
            continue
        schema = tool.get("parameters") or {"type": "object", "properties": {}}
        specs.append({"toolSpec": {
            "name": str(tool["name"]),
            "description": str(tool.get("description") or ""),
            "inputSchema": {"json": json.dumps(schema)},
        }})
    return specs


class NovaSonicTranslator:
    """Stateful translation for one Live session (pure: no network)."""

    def __init__(self) -> None:
        self.prompt = str(uuid.uuid4())
        self.audio_content = str(uuid.uuid4())
        self.started = False
        self.responding = False
        self.stages: dict[str, str] = {}
        self.roles: dict[str, str] = {}
        self.output_text: list[str] = []

    # -- page -> Nova Sonic ----------------------------------------------------

    def _text_turn(self, role: str, text: str) -> list[dict[str, Any]]:
        name = str(uuid.uuid4())
        return [
            {"contentStart": {"promptName": self.prompt, "contentName": name, "type": "TEXT",
                              "interactive": role == "USER", "role": role,
                              "textInputConfiguration": {"mediaType": "text/plain"}}},
            {"textInput": {"promptName": self.prompt, "contentName": name, "content": text}},
            {"contentEnd": {"promptName": self.prompt, "contentName": name}},
        ]

    def from_page(self, frame: dict[str, Any]) -> list[dict[str, Any]]:
        kind = frame.get("type")
        if kind == "session.update":
            if self.started:
                return []
            self.started = True
            session = frame.get("session") or {}
            prompt_start: dict[str, Any] = {
                "promptName": self.prompt,
                "textOutputConfiguration": {"mediaType": "text/plain"},
                "audioOutputConfiguration": {
                    "mediaType": "audio/lpcm", "sampleRateHertz": 24000, "sampleSizeBits": 16,
                    "channelCount": 1, "voiceId": _voice_for(session), "encoding": "base64", "audioType": "SPEECH",
                },
            }
            specs = _tool_specs(session.get("tools"))
            if specs:
                prompt_start["toolUseOutputConfiguration"] = {"mediaType": "application/json"}
                prompt_start["toolConfiguration"] = {"tools": specs}
            events: list[dict[str, Any]] = [
                {"sessionStart": {"inferenceConfiguration": {"maxTokens": 2048, "topP": 0.9, "temperature": 0.7}}},
                {"promptStart": prompt_start},
            ]
            instructions = str(session.get("instructions") or "").strip()
            if instructions:
                events += self._text_turn("SYSTEM", instructions)
            events.append({"contentStart": {
                "promptName": self.prompt, "contentName": self.audio_content, "type": "AUDIO",
                "interactive": True, "role": "USER",
                "audioInputConfiguration": {"mediaType": "audio/lpcm", "sampleRateHertz": 16000,
                                            "sampleSizeBits": 16, "channelCount": 1,
                                            "audioType": "SPEECH", "encoding": "base64"},
            }})
            return events
        if not self.started:
            return []
        if kind == "input_audio_buffer.append":
            try:
                pcm = base64.b64decode(str(frame.get("audio") or ""))
            except ValueError:
                return []
            audio = resample_pcm16_24k_to_16k(pcm)
            if not audio:
                return []
            return [{"audioInput": {"promptName": self.prompt, "contentName": self.audio_content,
                                    "content": base64.b64encode(audio).decode("ascii")}}]
        if kind == "conversation.item.create":
            item = frame.get("item") or {}
            if item.get("type") == "function_call_output":
                name = str(uuid.uuid4())
                return [
                    {"contentStart": {"promptName": self.prompt, "contentName": name, "type": "TOOL",
                                      "interactive": False, "role": "TOOL",
                                      "toolResultInputConfiguration": {
                                          "toolUseId": str(item.get("call_id") or ""), "type": "TEXT",
                                          "textInputConfiguration": {"mediaType": "text/plain"}}}},
                    {"toolResult": {"promptName": self.prompt, "contentName": name,
                                    "content": json.dumps({"result": str(item.get("output") or "")})}},
                    {"contentEnd": {"promptName": self.prompt, "contentName": name}},
                ]
            texts = [str(part.get("text") or "") for part in item.get("content") or [] if isinstance(part, dict)]
            text = "\n".join(t for t in texts if t).strip()
            return self._text_turn("USER", text) if text else []
        if kind == "response.create":
            instructions = str(((frame.get("response") or {}).get("instructions")) or "").strip()
            return self._text_turn("USER", instructions) if instructions else []
        return []

    def closing(self) -> list[dict[str, Any]]:
        if not self.started:
            return []
        return [
            {"contentEnd": {"promptName": self.prompt, "contentName": self.audio_content}},
            {"promptEnd": {"promptName": self.prompt}},
            {"sessionEnd": {}},
        ]

    # -- Nova Sonic -> page ----------------------------------------------------

    def _start_response(self) -> list[dict[str, Any]]:
        if self.responding:
            return []
        self.responding = True
        return [{"type": "response.created"}]

    def from_sonic(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = event.get("contentStart")
        if isinstance(start, dict):
            content_id = str(start.get("contentId") or "")
            self.roles[content_id] = str(start.get("role") or "")
            stage = ""
            with suppress(ValueError, TypeError):
                stage = str((json.loads(start.get("additionalModelFields") or "{}") or {}).get("generationStage") or "")
            self.stages[content_id] = stage
            return out
        text = event.get("textOutput")
        if isinstance(text, dict):
            content = str(text.get("content") or "")
            if '"interrupted"' in content.replace(" ", "") and "true" in content:
                self.output_text = []
                out.append({"type": "input_audio_buffer.speech_started"})
                return out
            role = str(text.get("role") or self.roles.get(str(text.get("contentId") or ""), ""))
            if role == "USER" and content.strip():
                out.append({"type": "conversation.item.input_audio_transcription.completed",
                            "transcript": content.strip()})
            elif role == "ASSISTANT" and content.strip():
                # The speculative text comes before its audio and is what is said;
                # the final copy repeats it later, often after the turn ended.
                if self.stages.get(str(text.get("contentId") or ""), "SPECULATIVE") != "FINAL":
                    self.output_text.append(content.strip())
            return out
        audio = event.get("audioOutput")
        if isinstance(audio, dict) and audio.get("content"):
            out += self._start_response()
            out.append({"type": "response.audio.delta", "delta": str(audio["content"])})
            return out
        tool = event.get("toolUse")
        if isinstance(tool, dict):
            out += self._start_response()
            out.append({
                "type": "response.function_call_arguments.done",
                "call_id": str(tool.get("toolUseId") or ""),
                "name": tool.get("toolName"),
                "arguments": str(tool.get("content") or "{}"),
            })
            return out
        end = event.get("contentEnd")
        if isinstance(end, dict):
            reason = str(end.get("stopReason") or "").upper()
            if str(end.get("type") or "").upper() == "AUDIO" and reason in {"END_TURN", "INTERRUPTED"}:
                if self.output_text:
                    out.append({"type": "response.audio_transcript.done", "transcript": " ".join(self.output_text)})
                    self.output_text = []
                self.responding = False
                out.append({"type": "response.done", "response": {"output": []}})
            elif reason == "TOOL_USE":
                self.responding = False
                out.append({"type": "response.done", "response": {"output": []}})
        return out


# -- the Bedrock stream --------------------------------------------------------


async def _open_stream(model: str, region: str, profile: str | None) -> Any:
    """Bidirectional stream with the AWS credentials (profile, environment, SSO...)."""
    import boto3
    from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient
    from aws_sdk_bedrock_runtime.config import AsyncBedrockRuntimeConfig
    from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamOperationInput
    from smithy_aws_core.identity import StaticCredentialsResolver
    from smithy_http.aio.crt import AWSCRTHTTPClient

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        # The bidirectional stream signs with AWS keys: a Bedrock API key alone is not enough.
        raise PermissionError("Live with Nova Sonic needs AWS access keys or an AWS profile in the Bedrock provider")
    frozen = credentials.get_frozen_credentials()
    config = await AsyncBedrockRuntimeConfig.resolve(
        region=region,
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
        aws_credentials_identity_resolver=StaticCredentialsResolver(),
        transport=AWSCRTHTTPClient(),
    )
    client = AsyncBedrockRuntimeClient(config=config)
    return await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=model)
    )


async def bridge(browser: Any, *, model: str, region: str, profile: str | None,
                 error_frame: Any, open_stream: Any = None) -> None:
    """Relay one Live session between the page and Nova Sonic."""
    from aws_sdk_bedrock_runtime.models import (
        BidirectionalInputPayloadPart,
        InvokeModelWithBidirectionalStreamInputChunk,
    )

    try:
        stream = await (open_stream or _open_stream)(model, region, profile)
    except Exception as exc:  # noqa: BLE001 - surfaced to the page, which falls back
        logger.info("live nova sonic: could not open the stream: {}", exc)
        with suppress(Exception):
            await browser.send(error_frame("realtime_unreachable", str(exc)[:200]))
            await browser.close(4502, "realtime_unreachable")
        return

    translator = NovaSonicTranslator()
    lock = asyncio.Lock()

    async def send(events: list[dict[str, Any]]) -> None:
        async with lock:
            for event in events:
                payload = json.dumps({"event": event}).encode("utf-8")
                await stream.input_stream.send(
                    InvokeModelWithBidirectionalStreamInputChunk(value=BidirectionalInputPayloadPart(bytes_=payload))
                )

    try:
        await browser.send(json.dumps({"type": "navin.ready", "provider": "bedrock", "model": model}))
    except (ConnectionClosed, OSError) as exc:
        # The page left while the stream opened: close the stream instead of leaking it.
        logger.info("live nova sonic: page gone before ready: {}", exc)
        with suppress(Exception):
            await stream.input_stream.close()
        return

    async def page_to_upstream() -> None:
        async for frame in browser:
            if isinstance(frame, bytes):
                continue
            try:
                parsed = json.loads(frame)
            except ValueError:
                continue
            events = translator.from_page(parsed if isinstance(parsed, dict) else {})
            if events:
                await send(events)
            if isinstance(parsed, dict) and parsed.get("type") == "session.update":
                await browser.send(json.dumps({"type": "session.updated"}))

    async def upstream_to_page() -> None:
        _, output = await stream.await_output()
        async for chunk in output:
            value = getattr(chunk, "value", None)
            raw = getattr(value, "bytes_", None)
            if raw is None:
                raise RuntimeError(f"Nova Sonic: {type(chunk).__name__} {str(value)[:200]}")
            try:
                event = (json.loads(raw.decode("utf-8")) or {}).get("event") or {}
            except (ValueError, UnicodeDecodeError):
                continue
            for message in translator.from_sonic(event):
                await browser.send(json.dumps(message))

    tasks = [asyncio.create_task(page_to_upstream()), asyncio.create_task(upstream_to_page())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None:
                logger.info("live nova sonic: session ended on {}", exc)
                with suppress(Exception):
                    await browser.send(error_frame("realtime_dropped", str(exc)[:200]))
    finally:
        with suppress(Exception):
            await send(translator.closing())
        with suppress(Exception):
            await stream.input_stream.close()
        with suppress(Exception):
            await browser.close()
