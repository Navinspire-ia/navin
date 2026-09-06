import { describe, expect, it } from "vitest";

import type { PreviewLogEntry } from "@/lib/api";
import {
  isProblemLevel,
  mergePreviewEntries,
  localPreviewPortFromUrl,
  problemCount,
  seedTextFromDigest,
} from "./previewConsole";

function entry(id: number, level: string, text = "x"): PreviewLogEntry {
  return { id, level, text };
}

describe("localPreviewPortFromUrl", () => {
  it("extracts explicit localhost ports", () => {
    expect(localPreviewPortFromUrl("http://localhost:5173")).toBe(5173);
    expect(localPreviewPortFromUrl("http://127.0.0.1:3000/app?x=1")).toBe(3000);
    expect(localPreviewPortFromUrl("localhost:8080")).toBe(8080);
  });

  it("defaults ports by scheme", () => {
    expect(localPreviewPortFromUrl("http://localhost")).toBe(80);
    expect(localPreviewPortFromUrl("https://localhost")).toBe(443);
  });

  it("rejects non-local and invalid URLs", () => {
    expect(localPreviewPortFromUrl("http://example.com:3000")).toBeNull();
    expect(localPreviewPortFromUrl("https://navin.live/dashboard")).toBeNull();
    expect(localPreviewPortFromUrl("")).toBeNull();
    expect(localPreviewPortFromUrl("not a url at all :::")).toBeNull();
    expect(localPreviewPortFromUrl("ftp://localhost:21")).toBeNull();
  });
});

describe("mergePreviewEntries", () => {
  it("appends only entries newer than the last known id", () => {
    const existing = [entry(1, "log"), entry(2, "error")];
    const merged = mergePreviewEntries(existing, [
      entry(2, "error"),
      entry(3, "warn"),
    ]);
    expect(merged.map((e) => e.id)).toEqual([1, 2, 3]);
  });

  it("returns the same array when nothing is new", () => {
    const existing = [entry(5, "log")];
    expect(mergePreviewEntries(existing, [entry(4, "log")])).toBe(existing);
    expect(mergePreviewEntries(existing, [])).toBe(existing);
  });

  it("trims to the max size keeping the newest", () => {
    const existing = [entry(1, "log"), entry(2, "log")];
    const incoming = [entry(3, "log"), entry(4, "log")];
    const merged = mergePreviewEntries(existing, incoming, 3);
    expect(merged.map((e) => e.id)).toEqual([2, 3, 4]);
  });
});

describe("problemCount / isProblemLevel", () => {
  it("counts errors, page errors, and network failures only", () => {
    const entries = [
      entry(1, "log"),
      entry(2, "warn"),
      entry(3, "error"),
      entry(4, "pageerror"),
      entry(5, "network"),
    ];
    expect(problemCount(entries)).toBe(3);
    expect(isProblemLevel("error")).toBe(true);
    expect(isProblemLevel("log")).toBe(false);
  });
});

describe("seedTextFromDigest", () => {
  it("wraps the digest with an actionable instruction", () => {
    const seed = seedTextFromDigest("- [UNCAUGHT] TypeError: boom", 5173);
    expect(seed).toContain("http://localhost:5173");
    expect(seed).toContain("TypeError: boom");
    expect(seed).toContain("diagnose and fix");
  });

  it("returns empty for an empty digest", () => {
    expect(seedTextFromDigest("  ", 3000)).toBe("");
  });
});
