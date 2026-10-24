#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Record Navin Live's welcome in English, French and Arabic.

The onboarding plays these to subscribers (webui LiveIntroCard). Two voices:

- ``--engine nova`` (default): Nova Sonic on Amazon Bedrock, the voice that
  answers subscribers in Navin Live, through the bridge the app uses
  (navin/live/nova_sonic.py). Needs AWS access to the model in the region,
  with ``bedrock:InvokeModelWithBidirectionalStream``.
- ``--engine vertex``: Gemini's voices on Google Vertex AI (the Gemini Live
  voices), read word for word by Gemini TTS, with a service account key.

    uv run python scripts/make-onboarding-voice.py --profile aymen-01 --region eu-north-1
    uv run python scripts/make-onboarding-voice.py --engine vertex --vertex-key guidia_vertex.json
    uv run python scripts/make-onboarding-voice.py --only fr
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from navin.live.nova_sonic import DEFAULT_MODEL, bridge  # noqa: E402

OUT_DIR = ROOT / "webui" / "public" / "onboarding"
SAMPLE_RATE = 24_000
# One voice per language, the ones Live picks for that language (female voices: the
# French and Arabic texts speak as a woman).
WELCOME = {
    "en": ("tiffany", "Hi, I'm Navin Live, your voice assistant, included in your plan. Talk to me like a teammate: "
           "I move your project forward with the agent, explain every Navin option and show it to you right on "
           "your screen. I sum up plans and reports, and I ask for your go-ahead when it matters. Whenever you're "
           "ready, tap the microphone and let's talk."),
    "fr": ("ambre", "Bonjour, je suis Navin Live, votre assistante vocale, incluse dans votre abonnement. Parlez-moi "
           "comme à un collègue : je fais avancer votre projet avec l'agent, je vous explique toutes les options de "
           "Navin et je vous les montre directement à l'écran. Je résume les plans et les rapports, et je vous "
           "demande votre accord quand il le faut. Quand vous voulez, appuyez sur le micro et parlons-en."),
    "ar": ("tiffany", "مرحبًا، أنا نافين لايف، مساعدتك الصوتية، وأنا مشمولة في اشتراكك. تحدّث معي كما تتحدث مع زميل: "
           "أطوّر مشروعك مع الوكيل، وأشرح لك كل خيارات نافين وأعرضها لك مباشرة على الشاشة. ألخّص الخطط والتقارير، "
           "وأطلب موافقتك عند الحاجة. عندما تكون جاهزًا، اضغط على الميكروفون ولنبدأ الحديث."),
}
LANGUAGE_NAMES = {"en": "English", "fr": "French", "ar": "Arabic"}
# Gemini TTS: one warm voice for the three languages (it detects the language).
VERTEX_VOICE = "Sulafat"
VERTEX_MODELS = ("gemini-2.5-pro-tts", "gemini-2.5-flash-tts")
VERTEX_STYLE = {
    "en": "Read this aloud in a warm, calm and friendly voice, at a natural pace",
    "fr": "Lis ce texte à voix haute, d'une voix chaleureuse, calme et souriante, à un rythme naturel",
    "ar": "اقرأ هذا النص بصوت دافئ وهادئ وودود، بإيقاع طبيعي",
}
SILENCE = base64.b64encode(b"\x00\x00" * (SAMPLE_RATE // 10)).decode("ascii")  # 100 ms at 24 kHz


class ScriptedPage:
    """Stands for the browser on the bridge: asks one line, keeps the audio."""

    def __init__(self, voice: str, language: str, text: str) -> None:
        self.frames: asyncio.Queue[str] = asyncio.Queue()
        self.audio: list[bytes] = []
        self.done = asyncio.Event()
        self.error = ""
        self.frames.put_nowait(json.dumps({"type": "session.update", "session": {
            "voice": voice,
            "instructions": f"You are Navin Live. Speak {LANGUAGE_NAMES[language]} only, warmly and clearly, "
                            "at a natural pace. Say exactly the text you are given, word for word, nothing else.",
        }}))
        self.frames.put_nowait(json.dumps({"type": "response.create", "response": {
            "instructions": f'Say exactly this, nothing else: "{text}"',
        }}))
        self._ready = False

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        kind = message.get("type")
        if kind == "response.audio.delta":
            self.audio.append(base64.b64decode(message["delta"]))
        elif kind == "response.done" and self.audio:
            self.done.set()
        elif kind == "error":
            self.error = str((message.get("error") or {}).get("message") or message)
            self.done.set()

    async def close(self, *_args: object) -> None:
        self.done.set()

    def __aiter__(self) -> ScriptedPage:
        return self

    async def __anext__(self) -> str:
        if not self.frames.empty():
            return self.frames.get_nowait()
        # Nova Sonic expects the microphone to keep streaming: silence until the line is said.
        try:
            await asyncio.wait_for(self.done.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            return json.dumps({"type": "input_audio_buffer.append", "audio": SILENCE})
        raise StopAsyncIteration


def error_frame(code: str, message: str) -> str:
    return json.dumps({"type": "error", "error": {"code": code, "message": message}})


async def record(language: str, *, model: str, region: str, profile: str | None) -> Path:
    voice, text = WELCOME[language]
    page = ScriptedPage(voice, language, text)
    await asyncio.wait_for(
        bridge(page, model=model, region=region, profile=profile, error_frame=error_frame), timeout=120,
    )
    if page.error or not page.audio:
        raise RuntimeError(page.error or "no audio came back")
    return _write_wav(language, page.audio)


def record_vertex(language: str, *, key: str, voice: str) -> Path:
    """Gemini TTS on Vertex AI: the text read word for word, 24 kHz PCM."""
    from google import genai
    from google.genai import types
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        key, scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    project = json.loads(Path(key).read_text(encoding="utf-8"))["project_id"]
    client = genai.Client(vertexai=True, project=project, location="global", credentials=credentials)
    _, text = WELCOME[language]
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice),
        )),
    )
    last: Exception | None = None
    for model in VERTEX_MODELS:
        try:
            response = client.models.generate_content(
                model=model, contents=f"{VERTEX_STYLE[language]}: {text}", config=config,
            )
            pcm = b"".join(
                part.inline_data.data for part in response.candidates[0].content.parts if part.inline_data
            )
            if pcm:
                return _write_wav(language, [pcm])
        except Exception as exc:  # noqa: BLE001 - the next model may be available
            last = exc
    raise RuntimeError(f"no Gemini TTS model answered: {last}")


def _write_wav(language: str, chunks: list[bytes]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"live-welcome-{language}.wav"
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(b"".join(chunks))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default=None, help="AWS profile (default: environment credentials)")
    parser.add_argument("--region", default="eu-north-1")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--only", choices=sorted(WELCOME), action="append")
    parser.add_argument("--engine", choices=("nova", "vertex"), default="nova")
    parser.add_argument("--vertex-key", default=str(ROOT / "guidia_vertex.json"), help="service account JSON")
    parser.add_argument("--voice", default="", help="override the voice (Nova Sonic or Gemini name)")
    args = parser.parse_args()
    failed = False
    for language in args.only or list(WELCOME):
        try:
            if args.engine == "vertex":
                path = record_vertex(language, key=args.vertex_key, voice=args.voice or VERTEX_VOICE)
            else:
                path = asyncio.run(record(language, model=args.model, region=args.region, profile=args.profile))
        except Exception as exc:  # noqa: BLE001 - one language failing does not stop the others
            print(f"{language}: failed: {exc}", file=sys.stderr)
            failed = True
            continue
        with wave.open(str(path)) as check:
            seconds = check.getnframes() / check.getframerate()
        print(f"{language}: {path.relative_to(ROOT)} ({seconds:.1f} s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
