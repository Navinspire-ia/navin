import { describe, expect, it } from "vitest";

import { liveVoiceSetup } from "./live-voice-setup";
import type { LiveVoiceSetup, SettingsPayload } from "./types";

describe("Live setup before microphone access", () => {
  it("does not mistake settings still loading for missing speech engines", () => {
    const status = liveVoiceSetup(null);
    expect(status.ready).toBe(false);
    expect(status.reason).toBe("loading");
    expect(status.missing).toEqual([]);
  });

  it("uses the gateway readiness contract for BYOK without a subscription", () => {
    const live: LiveVoiceSetup = {
      ready: true, reason: null, missing: [], mode: "byok", settings_section: "voice",
      stt: { provider: "groq", model: "whisper-large-v3", configured: true, enabled: true },
      tts: { provider: "openai", model: "gpt-4o-mini-tts", configured: true, voice: "cedar" },
    };
    expect(liveVoiceSetup({ voice: { live } } as SettingsPayload)).toBe(live);
  });

  it("explains that TTS is missing even when the microphone transcription is configured", () => {
    const status = liveVoiceSetup({
      transcription: { enabled: true, provider: "groq", provider_configured: true },
      voice: { realtime_allowed: true, tts_provider_configured: false },
    } as SettingsPayload);
    expect(status.ready).toBe(false);
    expect(status.missing).toEqual(["tts_not_configured"]);
  });

  it("honors explicit disabling on an otherwise configured account", () => {
    const status = liveVoiceSetup({
      transcription: { enabled: true, provider: "groq", provider_configured: true },
      voice: { realtime_enabled: false, tts_provider: "openai", tts_provider_configured: true },
    } as SettingsPayload);
    expect(status.reason).toBe("voice_disabled");
    expect(status.ready).toBe(false);
  });
});
