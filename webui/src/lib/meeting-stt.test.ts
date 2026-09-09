// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  describeMeetingSttError,
  meetingSttErrorDetail,
  transcriptHasSpeakerLabels,
} from "./meeting-stt";

const tx = (key: string, fallback: string) => `${key}:${fallback}`;

describe("describeMeetingSttError", () => {
  it("maps empty instead of showing the raw code", () => {
    expect(describeMeetingSttError(new Error("empty"), tx)).toContain(
      "meeting.errors.empty",
    );
  });

  it("maps timeout and decode", () => {
    expect(describeMeetingSttError(new Error("transcription timed out"), tx)).toContain(
      "meeting.errors.timeout",
    );
    expect(describeMeetingSttError(new Error("decode"), tx)).toContain(
      "meeting.errors.badFormat",
    );
  });

  it("reads the message off Error and plain strings", () => {
    expect(meetingSttErrorDetail(new Error("empty"))).toBe("empty");
    expect(meetingSttErrorDetail("empty")).toBe("empty");
  });
});

describe("transcriptHasSpeakerLabels", () => {
  it("rejects a raw unlabelled transcript", () => {
    expect(
      transcriptHasSpeakerLabels(
        "Bonjour. On va commencer.\nJe me presente, Aymane.",
      ),
    ).toBe(false);
  });

  it("accepts labelled turns", () => {
    expect(
      transcriptHasSpeakerLabels(
        "Speaker 1: Bonjour.\nAymane Guedgadi: Je me presente.\nSpeaker 2: Ok.",
      ),
    ).toBe(true);
  });
});
