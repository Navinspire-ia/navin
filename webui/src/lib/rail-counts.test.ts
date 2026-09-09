// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  PROJECT_RULES_CHANGED_EVENT,
  countBoardTasks,
  notifyProjectRulesChanged,
  railCountText,
} from "./rail-counts";

describe("countBoardTasks", () => {
  it("counts every task the user still has to care about", () => {
    const counts = countBoardTasks([
      { status: "backlog" },
      { status: "planned" },
      { status: "in_progress" },
      { status: "review" },
      { status: "blocked" },
      { status: "done" },
      { status: "cancelled" },
    ]);
    expect(counts).toEqual({ open: 5, inProgress: 1, blocked: 1 });
  });

  it("is zero for an empty or missing board", () => {
    expect(countBoardTasks([])).toEqual({ open: 0, inProgress: 0, blocked: 0 });
    expect(countBoardTasks(undefined)).toEqual({ open: 0, inProgress: 0, blocked: 0 });
  });
});

describe("railCountText", () => {
  it("keeps two digits and caps the rest", () => {
    expect(railCountText(1)).toBe("1");
    expect(railCountText(42)).toBe("42");
    expect(railCountText(99)).toBe("99");
    expect(railCountText(100)).toBe("99+");
    expect(railCountText(0)).toBe("0");
    expect(railCountText(Number.NaN)).toBe("0");
  });
});

describe("notifyProjectRulesChanged", () => {
  it("dispatches the rail refresh event on window", () => {
    const dispatchEvent = vi.fn();
    vi.stubGlobal("window", { dispatchEvent });
    vi.stubGlobal(
      "CustomEvent",
      class {
        type: string;
        constructor(type: string) {
          this.type = type;
        }
      },
    );
    try {
      notifyProjectRulesChanged();
    } finally {
      vi.unstubAllGlobals();
    }
    expect(dispatchEvent).toHaveBeenCalledTimes(1);
    expect(dispatchEvent.mock.calls[0][0].type).toBe(PROJECT_RULES_CHANGED_EVENT);
  });

  it("is a no-op without a window (server, tests)", () => {
    expect(() => notifyProjectRulesChanged()).not.toThrow();
  });
});

describe("rail pills in the workbench", () => {
  const workbench = readFileSync(
    resolve(__dirname, "../components/dev/DevWorkbench.tsx"),
    "utf8",
  );
  const rulesPanel = readFileSync(
    resolve(__dirname, "../components/dev/DevRulesPanel.tsx"),
    "utf8",
  );

  it("shows how many terminals, pages, files, rules and tasks are open", () => {
    expect(workbench).toContain("useRailCounts({");
    expect(workbench).toContain("railCountBadge(\n                  terminals.length");
    expect(workbench).toContain("railCountBadge(\n                  browserSrc ? 1 : 0");
    expect(workbench).toContain("railCountBadge(\n                  railCounts.rules");
    expect(workbench).toContain("railCountText(railCounts.tasks.open)");
    expect(workbench).toContain('data-testid="dev-rail-files-badge"');
  });

  it("uses the neutral tone for counts and keeps amber for attention", () => {
    expect(workbench).toContain('const RAIL_COUNT_TONE = "bg-foreground/[0.12] text-foreground/90"');
    // The badge span no longer forces white text: each tone brings its own.
    expect(workbench).not.toContain('"shrink-0 rounded-full text-center font-semibold text-white"');
    expect(workbench).toContain('tone: "bg-amber-500/90 text-white"');
    expect(workbench).toContain("railCounts.tasks.blocked > 0");
  });

  it("mirrors the terminal and browser counters on the toolbar", () => {
    expect(workbench).toContain('data-testid="dev-toolbar-terminal-badge"');
    expect(workbench).toContain('data-testid="dev-toolbar-browser-badge"');
  });

  it("only fetches Rules and Tasks counts while the rail is visible", () => {
    expect(workbench).toContain("enabled: Boolean(collapsed)");
  });

  it("lets the Rules panel refresh the pill after a save or create", () => {
    expect(rulesPanel.match(/notifyProjectRulesChanged\(\);/g)?.length).toBe(2);
  });
});
