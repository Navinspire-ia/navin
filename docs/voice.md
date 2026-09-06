# Voice (STT, TTS, realtime)

Navin supports microphone transcription (STT) on all plans, and a realtime voice session (STT + TTS + barge-in) on Pro, Ultra, and Team (or when realtime voice is enabled in Settings).

## STT (transcription)

Configure under **Settings → Voice** / transcription providers (Groq Whisper, OpenAI, OpenRouter, AssemblyAI, SiliconFlow, and others offered in the panel).

In the desktop chat, use the microphone control to record. Navin returns a transcription into the composer or as a voice-session transcript depending on mode.

## TTS and realtime session

Open **Settings → Voice** to choose:

- TTS provider and voice
- auto-speak when you want spoken replies
- realtime session enablement (when your plan or local setting allows it)

Start a realtime voice session from the chat voice controls when available. Barge-in cancels in-progress speech when you start talking again.

## Plan gate

Realtime voice defaults to Pro / Ultra / Team. Free and Plus keep STT only unless realtime voice is enabled locally in Settings.

## Related

- [Mobile usage PWA](./mobile-usage-app.md) (mic on phone)
- [Configuration](./configuration.md)
