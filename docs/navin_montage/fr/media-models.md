# Modèles média Montage (catalogue géré)

Avec un plan **Navin Plus+** (ou clé gérée équivalente), Studio → Montage → **Fournisseurs média IA** liste les modèles curés. Vous pouvez aussi brancher du BYOK sous Réglages.

Les défauts ci-dessous suivent le catalogue livré (`managed_models` / fallback `managed_catalog`). Les prix unitaires sont des estimations catalogue ; le débit réel suit `usage.cost` quand le provider le renvoie. Le packaging FFmpeg local et l'encode HyperFrames **ne facturent pas** les APIs média.

## Défauts (recommandés)

| Modalité | Modèle défaut | Rôle dans la boucle GTM |
| --- | --- | --- |
| Image | `google/gemini-3.1-flash-image` (Nano Banana 2) | Stills produit / lifestyle / social liés à l'app |
| Vidéo | `google/veo-3.1-fast` | B-roll / clips promo courts (estim. ~720p / 8 s) |
| Musique | `google/lyria-3-clip-preview` | Bed 30 s / jingle (0,04 $ / clip) |
| TTS | `google/gemini-3.1-flash-tts-preview` | Voix off pour démos et exports |
| STT | `qwen/qwen3-asr-flash-2026-02-10` | Sous-titres / dictée depuis l'audio |

## Image

| Slug | Nom | Notes |
| --- | --- | --- |
| `google/gemini-3.1-flash-image` | Nano Banana 2 | **Défaut** - équilibre qualité/prix |
| `google/gemini-3-pro-image` | Nano Banana Pro | Premium 2K/4K |
| `bytedance-seed/seedream-4.5` | Seedream 4.5 | Économique ~0,04 $ / image |

## Vidéo

| Slug | Nom | Notes |
| --- | --- | --- |
| `google/veo-3.1-fast` | Veo 3.1 Fast | **Défaut** + audio ; 720p ≈ 0,10 $/s |
| `google/veo-3.1` | Veo 3.1 | Premium à partir de ≈ 0,40 $/s |
| `bytedance/seedance-2.5` | Seedance 2.5 | Références / édition |
| `kwaivgi/kling-v3.0-pro` | Kling v3.0 Pro | Contrôle cinématique (multi-SKU) |
| `bytedance/seedance-2.0-fast` | Seedance 2.0 Fast | Facturation tokens ; 720p/8 s ≈ 0,97 $ |

Préférez une **démo navigateur live** de l'UI web/mobile pour l'authenticité ; réservez la vidéo IA au B-roll, transitions, ou quand l'UI n'est pas encore lançable.

## Musique

| Slug | Nom | Notes |
| --- | --- | --- |
| `google/lyria-3-clip-preview` | Lyria 3 Clip | **Défaut** - 30 s, 0,04 $ / clip |
| `google/lyria-3-pro-preview` | Lyria 3 Pro | Chanson complète sur demande - 0,08 $ / chanson |

## TTS (voix out)

| Slug | Nom | Notes |
| --- | --- | --- |
| `google/gemini-3.1-flash-tts-preview` | Gemini 3.1 Flash TTS | **Défaut** - 70+ langues |
| `fish-audio/s2.1-pro-free:free` | Fish Audio free | Prototypage seulement |
| `x-ai/grok-voice-tts-1.0` | Grok Voice TTS | Voix multilingues |
| `fish-audio/s2.1-pro` | Fish Audio Pro | Fallback production payant |

## STT (voix in / sous-titres)

| Slug | Nom | Notes |
| --- | --- | --- |
| `qwen/qwen3-asr-flash-2026-02-10` | Qwen3 ASR Flash | **Défaut** - ≈ 0,0021 $ / min |
| `openai/gpt-transcribe` | GPT Transcribe | 0,0045 $ / min |
| `nvidia/parakeet-tdt-0.6b-v3` | Parakeet TDT 0.6B v3 | 0,0015 $ / min |
| `microsoft/mai-transcribe-1.5` | MAI-Transcribe 1.5 | ≈ 0,006 $ / min, 43 langues |
| `deepgram/nova-3` | Deepgram Nova-3 | à partir de ≈ 0,0043 $ / min |

## Texte multimodal (route vision)

Quand l'agent raisonne sur captures / frames (hors sélecteur Montage) :

| Slug | Nom |
| --- | --- |
| `google/gemini-3.6-flash` | Gemini 3.6 Flash (multimodal principal) |
| `xiaomi/mimo-v2.5` | MiMo V2.5 (multimodal economy) |

## Accès

1. Clé gérée Plus+ - choisir les modèles dans Montage ou Réglages.
2. BYOK - configurer les providers, puis les mêmes modalités.
3. FFmpeg seul - packager une démo existante **sans** spend API média.

Jamais de publish auto. Passerelle `/ads` seulement avec accord explicite.
