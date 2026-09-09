// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { GithubPrSyncSuggestion } from "@/lib/types";

/** Mirror ProjectHomeView driftWarnings mapping (prod UI contract). */
function formatDriftWarnings(
  violations: Array<string | { detail?: string; constraint?: string }>,
): string[] {
  return violations
    .map((row) => {
      if (typeof row === "string") return row.trim();
      if (row && typeof row === "object") {
        return String(row.detail || row.constraint || "").trim();
      }
      return "";
    })
    .filter(Boolean)
    .slice(0, 6);
}

/** Mirror DevBoardPanel mark-done filter after accepting a PR suggestion. */
function withoutMarkedDone(
  suggestions: GithubPrSyncSuggestion[],
  taskId: string,
): GithubPrSyncSuggestion[] {
  return suggestions.filter((row) => row.task_id !== taskId);
}

describe("phase 7 polish UI contracts", () => {
  it("formats constraint drift warnings for Resume banner", () => {
    const lines = formatDriftWarnings([
      {
        constraint: "Never delete public APIs",
        detail: "Possible constraint drift: Never delete public APIs may relate to api.py",
      },
      "raw string warning",
      { constraint: "" },
    ]);
    expect(lines).toEqual([
      "Possible constraint drift: Never delete public APIs may relate to api.py",
      "raw string warning",
    ]);
  });

  it("removes PR sync suggestion after Mark done", () => {
    const rows: GithubPrSyncSuggestion[] = [
      {
        task_id: "t1",
        pr_url: "https://github.com/o/r/pull/1",
        merged: true,
      },
      {
        task_id: "t2",
        pr_url: "https://github.com/o/r/pull/2",
        merged: true,
      },
    ];
    expect(withoutMarkedDone(rows, "t1").map((r) => r.task_id)).toEqual(["t2"]);
  });
});
