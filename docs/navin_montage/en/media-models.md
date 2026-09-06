# Montage media models (managed catalog)

When a **Navin Plus+** plan (or equivalent managed key) is active, Studio → Montage → **AI media providers** lists curated models. You can also point each modality at BYOK providers under Settings.

Defaults below match the shipped managed catalog (`managed_models` / `managed_catalog` fallback). Unit prices are catalog estimates; billed amount follows provider `usage.cost` when present. Local FFmpeg packaging and HyperFrames encode do **not** bill media APIs.

## Defaults (recommended)

| Modality | Default model | Role in the GTM loop |
| --- | --- | --- |
| Image | `google/gemini-3.1-flash-image` (Nano Banana 2) | Product / lifestyle / social stills tied to the linked app |
| Video | `google/veo-3.1-fast` | Short AI B-roll / promo clips (default ~720p / 8 s estimate) |
| Music | `google/lyria-3-clip-preview` | 30 s bed / jingle (0,04 $ / clip) |
| TTS | `google/gemini-3.1-flash-tts-preview` | Voiceover for demos and exports |
| STT | `qwen/qwen3-asr-flash-2026-02-10` | Captions / dictation from recorded audio |

## Image

| Slug | Name | Notes |
| --- | --- | --- |
| `google/gemini-3.1-flash-image` | Nano Banana 2 | **Default** - quality/price balance |
| `google/gemini-3-pro-image` | Nano Banana Pro | Premium 2K/4K |
| `bytedance-seed/seedream-4.5` | Seedream 4.5 | Economy ~0,04 $ / image |

## Video

| Slug | Name | Notes |
| --- | --- | --- |
| `google/veo-3.1-fast` | Veo 3.1 Fast | **Default** + audio; 720p ≈ 0,10 $/s |
| `google/veo-3.1` | Veo 3.1 | Premium from ≈ 0,40 $/s |
| `bytedance/seedance-2.5` | Seedance 2.5 | References / edit workflows |
| `kwaivgi/kling-v3.0-pro` | Kling v3.0 Pro | Cinematic control (multi-SKU) |
| `bytedance/seedance-2.0-fast` | Seedance 2.0 Fast | Token-billed; 720p/8 s ≈ 0,97 $ |

Prefer a **live browser demo** of the web/mobile UI for authenticity; use AI video for B-roll, transitions, or when no runnable UI exists yet.

## Music

| Slug | Name | Notes |
| --- | --- | --- |
| `google/lyria-3-clip-preview` | Lyria 3 Clip | **Default** - 30 s, 0,04 $ / clip |
| `google/lyria-3-pro-preview` | Lyria 3 Pro | Full song on request - 0,08 $ / song |

## TTS (voice out)

| Slug | Name | Notes |
| --- | --- | --- |
| `google/gemini-3.1-flash-tts-preview` | Gemini 3.1 Flash TTS | **Default** - 70+ languages |
| `fish-audio/s2.1-pro-free:free` | Fish Audio free | Prototyping only |
| `x-ai/grok-voice-tts-1.0` | Grok Voice TTS | Multilingual voices |
| `fish-audio/s2.1-pro` | Fish Audio Pro | Paid production fallback |

## STT (voice in / captions)

| Slug | Name | Notes |
| --- | --- | --- |
| `qwen/qwen3-asr-flash-2026-02-10` | Qwen3 ASR Flash | **Default** - ≈ 0,0021 $ / min |
| `openai/gpt-transcribe` | GPT Transcribe | 0,0045 $ / min |
| `nvidia/parakeet-tdt-0.6b-v3` | Parakeet TDT 0.6B v3 | 0,0015 $ / min |
| `microsoft/mai-transcribe-1.5` | MAI-Transcribe 1.5 | ≈ 0,006 $ / min, 43 languages |
| `deepgram/nova-3` | Deepgram Nova-3 | from ≈ 0,0043 $ / min |

## Multimodal text (vision route)

Used when the agent reasons over screenshots / frames (not the Montage picker itself):

| Slug | Name |
| --- | --- |
| `google/gemini-3.6-flash` | Gemini 3.6 Flash (main multimodal) |
| `xiaomi/mimo-v2.5` | MiMo V2.5 (economy multimodal) |

## Access

1. Plus+ managed key - pick models in Montage or Settings.
2. BYOK - configure providers, then the same modality settings.
3. FFmpeg-only - package an existing demo with **zero** media API spend.

Never auto-publish. Hand off to `/ads` only with explicit approval.
