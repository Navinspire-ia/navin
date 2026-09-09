// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  audioSeekTarget,
  botShouldPoll,
  fromDiskRecord,
  highlightParts,
  meetingMatches,
  normalizeCachedMeetings,
  toDiskRecord,
  writeMeetingCache,
} from "@/components/meeting/meetingPersistence";

describe("meeting persistence", () => {
  it("normalizes the v2 cache idempotently", () => {
    const source = [{
      id: "one",
      title: "Weekly",
      createdAt: "2026-08-23T01:00:00Z",
      updatedAt: "2026-08-23T01:00:00Z",
      transcript: "Speaker A: ship",
      notes: "",
      templateId: "standard",
      speakers: ["Speaker A"],
    }];
    const first = normalizeCachedMeetings(source);
    expect(normalizeCachedMeetings(first)).toEqual(first);
  });

  it("round trips a disk record", () => {
    const meeting = normalizeCachedMeetings([{
      id: "one",
      title: "Weekly",
      createdAt: "2026-08-23T01:00:00Z",
      updatedAt: "2026-08-23T01:01:00Z",
      transcript: "[00:05] Ana: ship",
      notes: "Friday",
      summary: "Ship Friday",
      templateId: "standard",
      speakers: ["Ana"],
      durationSec: 10,
      diarizationSource: "provider_native",
    }])[0];
    expect(fromDiskRecord(toDiskRecord(meeting) as never)).toMatchObject(meeting);
  });

  it("searches all meeting text and highlights safely", () => {
    const meeting = normalizeCachedMeetings([{
      id: "one",
      title: "Roadmap",
      transcript: "Ship API v2",
      notes: "",
    }])[0];
    expect(meetingMatches(meeting, "roadmap api")).toBe(true);
    expect(highlightParts("API (v2) API", "(v2)")).toEqual([
      { text: "API ", match: false },
      { text: "(v2)", match: true },
      { text: " API", match: false },
    ]);
  });

  it("replays a global timecode inside the matching audio segment", () => {
    const target = audioSeekTarget(
      [{ id: "a", offset_ms: 0 }, { id: "b", offset_ms: 5000 }],
      7.25,
    );
    expect(target).toEqual({
      segment: { id: "b", offset_ms: 5000 },
      relativeSeconds: 2.25,
    });
  });

  it("resumes polling only for active bot states", () => {
    expect(botShouldPoll("live")).toBe(true);
    expect(botShouldPoll("waiting")).toBe(true);
    expect(botShouldPoll("interrupted")).toBe(false);
    expect(botShouldPoll("ended")).toBe(false);
  });

  it("reports an offline cache quota failure", () => {
    const storage = {
      setItem: () => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      },
    };
    expect(writeMeetingCache(storage, [])).toBe(false);
  });
});
