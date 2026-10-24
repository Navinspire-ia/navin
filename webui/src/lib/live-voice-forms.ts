// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { SettingsPayload, TranscriptionSettingsUpdate, VoiceSettingsUpdate } from "@/lib/types";

/**
 * The Live settings as the update endpoint takes them, from a settings
 * payload: the Voice settings screen and the Live avatar switch both save
 * through updateLiveVoiceSettings.
 */
export const DEFAULT_TRANSCRIPTION_SETTINGS: NonNullable<SettingsPayload["transcription"]> = {
  enabled: true,
  provider: "",
  provider_configured: false,
  model: "nvidia/parakeet-tdt-0.6b-v3",
  language: null,
  max_duration_sec: 120,
  max_upload_mb: 25,
  providers: [],
  managed_models: [],
};

export const DEFAULT_VOICE_SETTINGS: NonNullable<SettingsPayload["voice"]> = {
  tts_provider: "",
  tts_provider_configured: false,
  tts_model: "",
  voice: "auto",
  auto_speak: false,
  response_format: "mp3",
  realtime_enabled: null,
  realtime_allowed: false,
  realtime_required_plans: ["pro", "ultra", "team"],
  providers: [],
};

export function transcriptionFormFromPayload(payload: SettingsPayload): TranscriptionSettingsUpdate {
  const transcription = payload.transcription ?? DEFAULT_TRANSCRIPTION_SETTINGS;
  return {
    enabled: transcription.enabled,
    provider: transcription.provider,
    model: transcription.model,
    language: transcription.language ?? "",
    maxDurationSec: transcription.max_duration_sec,
    maxUploadMb: transcription.max_upload_mb,
  };
}

export function voiceFormFromPayload(payload: SettingsPayload): VoiceSettingsUpdate {
  const voice = payload.voice ?? DEFAULT_VOICE_SETTINGS;
  return {
    ttsProvider: voice.tts_provider,
    ttsModel: voice.tts_model,
    voice: voice.voice,
    autoSpeak: voice.auto_speak,
    responseFormat: voice.response_format,
    realtimeEnabled: voice.realtime_enabled,
    liveEngine: voice.live_engine ?? "auto",
    liveRealtime: payload.media_models?.live.selected ?? "",
    liveRealtimeVoice: voice.live_realtime_voice ?? "",
  };
}
