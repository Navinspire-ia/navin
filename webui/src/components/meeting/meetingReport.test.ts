import { describe, expect, it } from "vitest";

import {
  analyzeMeeting,
  buildTurns,
  detectHighlights,
  formatDurationLabel,
  formatTimecode,
  renameSpeaker,
  rosterFromTranscript,
  splitTimecode,
  type MeetingSource,
} from "./meetingReport";

function source(partial: Partial<MeetingSource>): MeetingSource {
  return {
    title: "Point produit",
    createdAt: "2026-08-18T09:00:00.000Z",
    updatedAt: "2026-08-18T10:00:00.000Z",
    transcript: "",
    notes: "",
    summary: "",
    speakers: [],
    templateName: "Standard",
    ...partial,
  };
}

describe("timecodes", () => {
  it("formats capture offsets as [mm:ss] and [h:mm:ss]", () => {
    expect(formatTimecode(0)).toBe("00:00");
    expect(formatTimecode(195_000)).toBe("03:15");
    expect(formatTimecode(3_760_000)).toBe("1:02:40");
  });

  it("splits a leading timecode off a line", () => {
    expect(splitTimecode("[03:15] Alice: bonjour")).toEqual({
      time: "03:15",
      rest: "Alice: bonjour",
    });
    expect(splitTimecode("Alice: bonjour").time).toBeNull();
  });
});

describe("buildTurns with timecodes", () => {
  it("attaches the time to a labelled turn and strips it from the text", () => {
    const turns = buildTurns(
      "[03:15] Alice: on commence.\n[04:02] Bob: je suis prêt.",
      ["Alice", "Bob"],
    );
    expect(turns).toHaveLength(2);
    expect(turns[0]).toMatchObject({
      speaker: "Alice",
      text: "on commence.",
      time: "03:15",
    });
    expect(turns[1]).toMatchObject({ speaker: "Bob", time: "04:02" });
  });

  it("keeps the first timecode of an unlabelled paragraph", () => {
    const turns = buildTurns("[00:30] Premier point du jour.", []);
    expect(turns[0].time).toBe("00:30");
    expect(turns[0].text).toContain("Premier point");
  });
});

describe("speaker helpers with timecodes", () => {
  it("never mistakes a timecode for a speaker name", () => {
    const roster = rosterFromTranscript(
      "[03:15] Alice: bonjour\n[04:02] dernier point sans label",
      [],
    );
    expect(roster).toEqual(["Alice"]);
  });

  it("renames a speaker without touching the timecode", () => {
    const out = renameSpeaker("[03:15] Speaker 1: bonjour", "Speaker 1", "Alice");
    expect(out).toBe("[03:15] Alice: bonjour");
  });
});

describe("proposals and opinions", () => {
  it("detects a proposal and an opinion with their speakers", () => {
    const turns = buildTurns(
      "Alice: je propose de passer la limite de sauvegarde en revue demain matin.\n"
        + "Bob: à mon avis cette limite est déjà bien trop haute pour nous.",
      ["Alice", "Bob"],
    );
    const highlights = detectHighlights(turns, ["Alice", "Bob"]);
    const proposal = highlights.find((row) => row.kind === "proposal");
    const opinion = highlights.find((row) => row.kind === "opinion");
    expect(proposal?.owner).toBe("Alice");
    expect(opinion?.owner).toBe("Bob");
  });
});

describe("measured duration", () => {
  it("prefers the recorded duration over the word estimate", () => {
    const report = analyzeMeeting(
      source({ transcript: "Alice: bonjour à tous.", durationSec: 2_700 }),
    );
    expect(report.stats.durationSec).toBe(2700);
    expect(formatDurationLabel(2700)).toBe("45 min");
    expect(formatDurationLabel(3_900)).toBe("1 h 05");
  });

  it("falls back to the last timecode when no duration was measured", () => {
    const report = analyzeMeeting(
      source({ transcript: "[00:10] Alice: début.\n[12:40] Bob: fin." }),
    );
    expect(report.stats.durationSec).toBe(760);
  });
});
