// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import "@/i18n";
import { liveToolActivityLabelFor } from "@/lib/live-activity";

describe("liveToolActivityLabelFor", () => {
  it("returns the newest running step", () => {
    expect(liveToolActivityLabelFor([
      { phase: "start", call_id: "a", name: "read_file", arguments: { path: "src/a.ts" } },
      { phase: "start", call_id: "b", name: "exec", arguments: { command: "npm test" } },
    ])).toBe("Running npm test");
  });

  it("prefers a tool still running over a parallel one that already ended", () => {
    expect(liveToolActivityLabelFor([
      { phase: "start", call_id: "a", name: "read_file", arguments: { path: "src/a.ts" } },
      { phase: "start", call_id: "b", name: "exec", arguments: { command: "npm test" } },
      { phase: "end", call_id: "b", name: "exec" },
    ])).toBe("Reading a.ts");
  });

  it("falls back to the newest label when nothing is running, and null for no label", () => {
    expect(liveToolActivityLabelFor([
      { phase: "start", call_id: "b", name: "exec", arguments: { command: "ls" } },
      { phase: "end", call_id: "b", name: "exec" },
    ])).toBe("Running ls");
    expect(liveToolActivityLabelFor([{ phase: "end", call_id: "b", name: "exec" }])).toBeNull();
    expect(liveToolActivityLabelFor([])).toBeNull();
  });
});
