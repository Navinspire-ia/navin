import { describe, expect, it } from "vitest";

import { loadActivityVerbosity, previewMaxFor } from "@/lib/activity-verbosity";

describe("activity verbosity", () => {
  it("defaults to standard when storage is unavailable", () => {
    expect(loadActivityVerbosity()).toBe("standard");
  });
  it("limits rows in essential, keeps base in standard, shows all in debug", () => {
    expect(previewMaxFor("essential", 6)).toBe(2);
    expect(previewMaxFor("essential", 1)).toBe(1);
    expect(previewMaxFor("standard", 6)).toBe(6);
    expect(previewMaxFor("debug", 6)).toBeGreaterThan(1000);
  });
});
