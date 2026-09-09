// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { buildHeldByPlanGaps, missionControlState, planItemTone, planQualityVerdict } from "./ThreadPlanPanel";
import type { BoardTaskStatus, SessionPlanQuality } from "@/lib/api";

const ready: SessionPlanQuality = { status: "ready", gaps: [] };
const gaps: SessionPlanQuality = {
  status: "gaps",
  gaps: [
    {
      kind: "acceptance_criteria_missing",
      task_id: null,
      message: "The mission ledger has no acceptance_criteria.",
    },
  ],
};

describe("planQualityVerdict (P2-7 pre-Build judge)", () => {
  it("warns about a plan with gaps while Build is clickable", () => {
    // Acceptance: a plan without acceptance_criteria shows a warning before
    // the Build button is used.
    expect(planQualityVerdict(gaps, true)).toBe("gaps");
  });

  it("declares a complete plan ready", () => {
    expect(planQualityVerdict(ready, true)).toBe("ready");
  });

  it("stays silent when Build is not actionable", () => {
    // Mid-run or on a finished plan the verdict is history, not advice.
    expect(planQualityVerdict(gaps, false)).toBe("hidden");
    expect(planQualityVerdict(ready, false)).toBe("hidden");
  });

  it("renders nothing when an older gateway sends no verdict", () => {
    expect(planQualityVerdict(undefined, true)).toBe("hidden");
    expect(planQualityVerdict(null, true)).toBe("hidden");
  });

  it("treats a gaps status without concrete gaps as ready", () => {
    expect(planQualityVerdict({ status: "gaps", gaps: [] }, true)).toBe("ready");
  });
});

describe("buildHeldByPlanGaps (the warning actually blocks)", () => {
  it("holds Build while the gaps are unreviewed", () => {
    // The warning used to be decoration: a plan with no acceptance criteria
    // could still be handed to the builder in one click.
    expect(buildHeldByPlanGaps(gaps, true, false)).toBe(true);
  });

  it("releases Build once the user acknowledges the gaps", () => {
    // The judge is a heuristic, so it must never strand a plan for good.
    expect(buildHeldByPlanGaps(gaps, true, true)).toBe(false);
  });

  it("never holds a plan the judge cleared", () => {
    expect(buildHeldByPlanGaps(ready, true, false)).toBe(false);
  });

  it("never holds when there is no verdict to act on", () => {
    expect(buildHeldByPlanGaps(undefined, true, false)).toBe(false);
    expect(buildHeldByPlanGaps(null, true, false)).toBe(false);
  });

  it("does not hold a Build that is already unavailable", () => {
    expect(buildHeldByPlanGaps(gaps, false, false)).toBe(false);
  });
});

describe("the gaps override starts the build", () => {
  const source = readFileSync(resolve(__dirname, "ThreadPlanPanel.tsx"), "utf8");

  it("calls onBuild from the warning link, not just unlock the held button", () => {
    // "Build anyway" only set gapsAcknowledged, so the click looked dead: the
    // held button silently became clickable and the plan sat at 1/2.
    const link = source.slice(source.indexOf('data-testid="thread-plan-build-anyway"'));
    const handler = link.slice(0, link.indexOf("className="));
    expect(handler).toContain("setGapsAcknowledged(true)");
    expect(handler).toContain("onBuild?.(plan)");
  });

  it("points the disabled button at the warning that explains it", () => {
    expect(source).toContain("aria-describedby={buildHeldByGaps ? gapsCardId : undefined}");
  });

  it("labels the override like the button it replaces", () => {
    expect(source).toContain('tx("thread.plan.qualityResumeAnyway", "Resume anyway")');
    for (const locale of ["en", "fr"]) {
      const bundle = JSON.parse(
        readFileSync(resolve(__dirname, `../../i18n/locales/${locale}/common.json`), "utf8"),
      ) as { thread: { plan: Record<string, string> } };
      expect(bundle.thread.plan.qualityResumeAnyway).toBeTruthy();
      expect(bundle.thread.plan.qualityBuildAnyway).toBeTruthy();
    }
  });
});

describe("missionControlState", () => {
  it("pauses a running ledger when the chat is idle", () => {
    expect(missionControlState({ version: 2, ledger_status: "running" }, false)).toEqual({
      canPause: true,
      canResume: false,
    });
  });

  it("resumes a paused ledger when the chat is idle", () => {
    expect(missionControlState({ version: 3, ledger_status: "paused" }, false)).toEqual({
      canPause: false,
      canResume: true,
    });
  });

  it("hides both controls while a run is live or the mission is closed", () => {
    expect(missionControlState({ version: 2, ledger_status: "running" }, true)).toEqual({
      canPause: false,
      canResume: false,
    });
    expect(missionControlState({ version: 4, ledger_status: "done" }, false)).toEqual({
      canPause: false,
      canResume: false,
    });
    expect(missionControlState(null, false)).toEqual({
      canPause: false,
      canResume: false,
    });
  });
});

function planItem(overrides: Partial<{
  done: boolean;
  blocked: boolean;
  cancelled: boolean;
  status: BoardTaskStatus;
  active: boolean;
}> = {}) {
  return {
    done: false,
    blocked: false,
    cancelled: false,
    status: "backlog" as BoardTaskStatus,
    active: false,
    ...overrides,
  };
}

describe("planItemTone", () => {
  it("colors done, current, planned, blocked, and pending differently", () => {
    expect(planItemTone(planItem({ done: true }), false)).toBe("done");
    expect(planItemTone(planItem({ status: "in_progress" }), true)).toBe("active");
    expect(planItemTone(planItem({ status: "planned" }), false)).toBe("planned");
    expect(planItemTone(planItem({ blocked: true }), false)).toBe("blocked");
    expect(planItemTone(planItem({ status: "review" }), false)).toBe("review");
    expect(planItemTone(planItem({ status: "fix" }), false)).toBe("fix");
    expect(planItemTone(planItem(), false)).toBe("pending");
  });

  it("keeps cancelled above other statuses", () => {
    expect(planItemTone(planItem({ cancelled: true, done: true }), true)).toBe("cancelled");
  });
});

describe("plan checklist stays visible during a run", () => {
  const source = readFileSync(resolve(__dirname, "ThreadPlanPanel.tsx"), "utf8");

  it("does not auto-collapse the list when streaming starts", () => {
    expect(source).not.toContain("if (isStreaming) {\n      setExpanded(false);");
    expect(source).toContain('data-testid="thread-plan-row"');
    expect(source).toContain("data-tone={tone}");
    expect(source).not.toContain("item.description");
    expect(source).not.toContain("focusBlurb");
    expect(source).not.toContain("detailsOpen");
  });
});

describe("View plan opens in the workbench", () => {
  const source = readFileSync(resolve(__dirname, "ThreadPlanPanel.tsx"), "utf8");

  it("asks the Code column to show the markdown, not a side sheet", () => {
    // The full plan used to slide in as a right-hand Sheet over the chat.
    // View plan now dispatches the workbench event so the markdown sits in
    // the editor column (same place as Tasks or Git).
    const button = source.slice(source.indexOf('data-testid="thread-plan-view"'));
    const handler = button.slice(0, button.indexOf("className="));
    expect(handler).toContain("requestOpenSessionPlan()");
    expect(source).not.toContain("SheetContent");
    expect(source).not.toContain("thread-plan-view-sheet");
  });
});
