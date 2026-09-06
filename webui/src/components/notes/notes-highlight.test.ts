import { describe, expect, it } from "vitest";

import { formatSnapshotStamp, highlightMatch } from "./notes-highlight";

describe("highlightMatch", () => {
  it("preserves text and highlights case-insensitively", () => {
    expect(highlightMatch("Décision Juillet validée", "juillet")).toEqual([
      { text: "Décision ", highlighted: false },
      { text: "Juillet", highlighted: true },
      { text: " validée", highlighted: false },
    ]);
  });

  it("returns plain text for an absent query", () => {
    expect(highlightMatch("Alpha", "beta")).toEqual([
      { text: "Alpha", highlighted: false },
    ]);
  });
});

describe("formatSnapshotStamp", () => {
  it("formats a backend UTC stamp", () => {
    expect(formatSnapshotStamp("20260823T004500000000", "en-US")).toContain("2026");
  });
});
