// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { avatarBodyForVoice } from "./live-avatar";
import { realtimeSessionVoice } from "./live-voices";
import { envelopeLevel, speechEnvelope, syntheticSpeechLevel } from "./speech-envelope";

describe("avatarBodyForVoice", () => {
  it("uses the same female default for Nova speech and the avatar", () => {
    for (const [language, expected] of [["fr-FR", "ambre"], ["en", "tiffany"], ["de", "tina"], ["es", "lupe"]]) {
      const voice = realtimeSessionVoice("", "amazon/nova-2-sonic", language);
      expect(voice).toBe(expected);
      expect(avatarBodyForVoice(voice)).toBe("F");
    }
    expect(realtimeSessionVoice("florian", "amazon/nova-2-sonic", "fr")).toBe("florian");
    expect(realtimeSessionVoice("Kore", "gemini-live", "fr")).toBe("Kore");
    expect(realtimeSessionVoice("", "gemini-live", "fr")).toBeUndefined();
  });
  it("shows a man for male voices of every provider", () => {
    for (const voice of ["Remi", "Matthew", "florian", "carlos", "onyx", "rex", "Puck", "Zayd"]) {
      expect(avatarBodyForVoice(voice)).toBe("M");
    }
  });

  it("shows a woman for female, automatic and unknown voices", () => {
    for (const voice of ["Lea", "ambre", "tiffany", "Hala", "nova", "Kore", "auto", "", null, undefined, "someone-new"]) {
      expect(avatarBodyForVoice(voice)).toBe("F");
    }
  });
});

describe("speech envelope", () => {
  it("rests on silence and opens on speech-level audio", () => {
    const rate = 16_000;
    const samples = new Float32Array(rate);
    for (let i = rate / 2; i < rate; i += 1) samples[i] = 0.2 * Math.sin(i / 5);
    const frames = speechEnvelope(samples, rate);
    expect(envelopeLevel(frames, 0.1)).toBe(0);
    expect(envelopeLevel(frames, 0.8)).toBeGreaterThan(0.4);
    expect(envelopeLevel(frames, 5)).toBe(0);
    expect(envelopeLevel(frames, -1)).toBe(0);
  });

  it("keeps a plausible talking motion without an envelope", () => {
    const levels = [0, 0.1, 0.2, 0.3].map(syntheticSpeechLevel);
    expect(Math.max(...levels)).toBeGreaterThan(0);
    expect(levels.every((level) => level >= 0 && level <= 1)).toBe(true);
  });
});
