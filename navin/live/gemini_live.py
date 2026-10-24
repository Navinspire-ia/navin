# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Gemini Live behind the Live realtime engine.

The page always speaks the OpenAI Realtime protocol (navin/live/realtime.py).
For a Gemini Live model used with the user's own Gemini key, the gateway
translates both ways to the Gemini Live API (BidiGenerateContent):

page -> Gemini
  session.update                      -> setup (instructions, tools, voice, transcriptions)
  input_audio_buffer.append (24 kHz)  -> realtimeInput.audio (16 kHz PCM)
  conversation.item.create (text)     -> clientContent (a user turn)
  conversation.item.create (tool out) -> toolResponse
  response.create with instructions    -> clientContent (the call's opening line)
  response.create / response.cancel   -> nothing otherwise (Gemini answers on its own)

Gemini -> page
  serverContent audio parts           -> response.audio.delta
  serverContent.interrupted           -> input_audio_buffer.speech_started (barge-in)
  input / output transcriptions       -> ...input_audio_transcription.completed / response.audio_transcript.done
  serverContent.turnComplete          -> response.done
  toolCall                            -> response.function_call_arguments.done
"""

from __future__ import annotations

import array
import base64
import json
from typing import Any

GEMINI_LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def resample_pcm16_24k_to_16k(data: bytes) -> bytes:
    """Linear resampling of little-endian PCM16 mono, 24 kHz -> 16 kHz."""
    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    count = len(samples)
    if not count:
        return b""
    out_len = (count * 2) // 3
    out = array.array("h", bytes(out_len * 2))
    for i in range(out_len):
        position = i * 1.5
        left = int(position)
        right = min(left + 1, count - 1)
        weight = position - left
        out[i] = int(samples[left] * (1 - weight) + samples[right] * weight)
    return out.tobytes()


def _tool_declarations(tools: Any) -> list[dict[str, Any]]:
    declarations = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        parameters = tool.get("parameters") or {"type": "object", "properties": {}}
        declaration: dict[str, Any] = {"name": tool.get("name"), "description": tool.get("description", "")}
        if parameters.get("properties"):
            declaration["parameters"] = parameters
        declarations.append(declaration)
    return declarations


class GeminiLiveTranslator:
    """Stateful translation for one Live session."""

    def __init__(self, model: str) -> None:
        self.model = model if model.startswith("models/") else f"models/{model}"
        self.setup_sent = False
        self.call_names: dict[str, str] = {}
        self.input_text: list[str] = []
        self.output_text: list[str] = []
        self.responding = False

    # -- page -> Gemini --------------------------------------------------------

    def from_page(self, frame: dict[str, Any]) -> list[dict[str, Any]]:
        kind = frame.get("type")
        if kind == "session.update":
            if self.setup_sent:
                return []
            self.setup_sent = True
            session = frame.get("session") or {}
            setup: dict[str, Any] = {
                "model": self.model,
                "generationConfig": {"responseModalities": ["AUDIO"]},
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }
            if session.get("instructions"):
                setup["systemInstruction"] = {"parts": [{"text": str(session["instructions"])}]}
            if session.get("voice"):
                setup["generationConfig"]["speechConfig"] = {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": str(session["voice"])}}
                }
            declarations = _tool_declarations(session.get("tools"))
            if declarations:
                setup["tools"] = [{"functionDeclarations": declarations}]
            return [{"setup": setup}]
        if kind == "input_audio_buffer.append":
            try:
                pcm = base64.b64decode(str(frame.get("audio") or ""))
            except ValueError:
                return []
            audio = resample_pcm16_24k_to_16k(pcm)
            if not audio:
                return []
            return [{"realtimeInput": {"audio": {"data": base64.b64encode(audio).decode("ascii"),
                                                  "mimeType": "audio/pcm;rate=16000"}}}]
        if kind == "conversation.item.create":
            item = frame.get("item") or {}
            if item.get("type") == "function_call_output":
                call_id = str(item.get("call_id") or "")
                return [{"toolResponse": {"functionResponses": [{
                    "id": call_id,
                    "name": self.call_names.pop(call_id, ""),
                    "response": {"result": str(item.get("output") or "")},
                }]}}]
            texts = [str(part.get("text") or "") for part in item.get("content") or [] if isinstance(part, dict)]
            text = "\n".join(t for t in texts if t)
            if not text:
                return []
            return [{"clientContent": {"turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": True}}]
        if kind == "response.create":
            # Gemini answers turns on its own; only a response with its own
            # instructions (the opening line of the call) becomes a turn.
            instructions = str(((frame.get("response") or {}).get("instructions")) or "").strip()
            if instructions:
                return [{"clientContent": {"turns": [{"role": "user", "parts": [{"text": instructions}]}],
                                           "turnComplete": True}}]
        return []

    # -- Gemini -> page --------------------------------------------------------

    def from_gemini(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if "setupComplete" in message:
            return [{"type": "session.updated"}]
        tool_call = message.get("toolCall")
        if isinstance(tool_call, dict):
            for call in tool_call.get("functionCalls") or []:
                call_id = str(call.get("id") or call.get("name") or "")
                self.call_names[call_id] = str(call.get("name") or "")
                out.append({
                    "type": "response.function_call_arguments.done",
                    "call_id": call_id,
                    "name": call.get("name"),
                    "arguments": json.dumps(call.get("args") or {}),
                })
        content = message.get("serverContent")
        if isinstance(content, dict):
            if content.get("interrupted"):
                out.append({"type": "input_audio_buffer.speech_started"})
            transcription = content.get("inputTranscription")
            if isinstance(transcription, dict) and transcription.get("text"):
                self.input_text.append(str(transcription["text"]))
            transcription = content.get("outputTranscription")
            if isinstance(transcription, dict) and transcription.get("text"):
                if self.input_text:
                    out.append(self._flush_input())
                self.output_text.append(str(transcription["text"]))
            turn = content.get("modelTurn")
            if isinstance(turn, dict):
                for part in turn.get("parts") or []:
                    inline = part.get("inlineData") if isinstance(part, dict) else None
                    if isinstance(inline, dict) and str(inline.get("mimeType", "")).startswith("audio/"):
                        if not self.responding:
                            self.responding = True
                            if self.input_text:
                                out.append(self._flush_input())
                            out.append({"type": "response.created"})
                        out.append({"type": "response.audio.delta", "delta": inline.get("data", "")})
            if content.get("turnComplete") or content.get("generationComplete"):
                if self.input_text:
                    out.append(self._flush_input())
                if self.output_text:
                    out.append({"type": "response.audio_transcript.done", "transcript": "".join(self.output_text).strip()})
                    self.output_text = []
                if content.get("turnComplete"):
                    self.responding = False
                    out.append({"type": "response.done", "response": {"output": []}})
        return out

    def _flush_input(self) -> dict[str, Any]:
        text = "".join(self.input_text).strip()
        self.input_text = []
        return {"type": "conversation.item.input_audio_transcription.completed", "transcript": text}
