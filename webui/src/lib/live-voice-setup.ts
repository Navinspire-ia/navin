// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { LiveVoiceSetup, SettingsPayload } from "@/lib/types";

/** Older gateways can still explain what is missing before requesting the mic. */
export function liveVoiceSetup(settings: SettingsPayload | null | undefined): LiveVoiceSetup {
  if (settings?.voice?.live) return settings.voice.live;
  const stt = settings?.transcription;
  const tts = settings?.voice;
  const missing: string[] = [];
  if (stt?.enabled === false) missing.push("stt_disabled");
  else if (!stt?.provider_configured) missing.push("stt_not_configured");
  if (!tts?.tts_provider_configured) missing.push("tts_not_configured");
  const reason = !settings ? "loading" : tts?.realtime_enabled === false ? "voice_disabled"
    : missing[0] ?? (tts?.realtime_allowed ? null : "plan_required");
  return {
    ready: reason === null, reason, missing: settings ? missing : [],
    mode: stt?.provider === "navin" || tts?.tts_provider === "navin" ? "navin" : "byok",
    settings_section: "voice",
    stt: { provider: stt?.provider ?? "", model: stt?.model ?? "",
      configured: stt?.provider_configured ?? false, enabled: stt?.enabled ?? false },
    tts: { provider: tts?.tts_provider ?? "", model: tts?.tts_model ?? "",
      configured: tts?.tts_provider_configured ?? false, voice: tts?.voice ?? "" },
  };
}
