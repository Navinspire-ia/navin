// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { UIMessage } from "@/lib/types";

import { buildJournalTimeline } from "./activity/activityJournalModel";
import {
  activityDurationMs,
  activityPhaseSplit,
  collectJournalSteps,
  collectShellRuns,
} from "./AgentActivityCluster";

function trace(id: string, lines: string[], createdAt = 1): UIMessage {
  return {
    id,
    role: "assistant",
    content: lines.join("\n"),
    kind: "trace",
    createdAt,
    traces: lines,
  };
}

function reasoning(id: string, createdAt: number): UIMessage {
  return { id, role: "assistant", content: "", createdAt, reasoning: "Let me look at the code." };
}

describe("journal steps dedupe one call across trace formats and messages", () => {
  const shellEvent = (phase: "start" | "end", extra: Record<string, unknown> = {}) => ({
    phase,
    call_id: "call-1",
    name: "exec",
    arguments: { command: "npm test", timeout: 60 },
    ...extra,
  });

  it("matches a history trace line (Python spacing) to its structured run", () => {
    const message: UIMessage = {
      ...trace("t1", ['exec({"command": "npm test", "timeout": 60})']),
      toolEvents: [shellEvent("start"), shellEvent("end", { result: "12 passed" })],
    };
    const steps = collectJournalSteps([message], false);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ kind: "shell", status: "done", output: "12 passed" });
    expect(buildJournalTimeline(steps, { streaming: false })).toHaveLength(1);
  });

  it("drops the same call written twice with other spacing in one message", () => {
    const message: UIMessage = {
      ...trace("t1", [
        'exec({"command":"npm test","timeout":60})',
        'exec({"command": "npm test", "timeout": 60})',
      ]),
      toolEvents: [shellEvent("end", { result: "ok" })],
    };
    expect(collectJournalSteps([message], false)).toHaveLength(1);
  });

  it("keeps one row when the start and end frames land in different trace messages", () => {
    const first: UIMessage = {
      ...trace("t1", ['exec({"command":"npm test","timeout":60})'], 1),
      toolEvents: [shellEvent("start")],
    };
    const second: UIMessage = {
      ...trace("t2", [], 2),
      content: "",
      toolEvents: [shellEvent("end", { result: "done" })],
    };
    const steps = collectJournalSteps([first, second], true);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ kind: "shell", status: "done" });
  });

  it("puts reasoning between the calls it led to", () => {
    const steps = collectJournalSteps([
      reasoning("r1", 1),
      trace("t1", ['exec({"command":"ls"})'], 2),
      { ...reasoning("r2", 3), reasoning: "Now the tests.", reasoningStreaming: true },
    ], true);
    expect(steps.map((step) => step.kind)).toEqual(["reasoning", "shell", "reasoning"]);
    expect(steps[2]).toMatchObject({ status: "running", text: "Now the tests." });
  });
});

describe("journal steps from trace lines", () => {
  it("walks tool calls in order and keeps reads, searches, shells and tools apart", () => {
    const steps = collectJournalSteps([
      trace("t1", [
        'read_file({"path":"webui/src/App.tsx"})',
        'grep({"pattern":"navin"})',
        'exec({"command":"npm test"})',
        'board({"action":"create","title":"Open Preview"})',
      ]),
    ], false);
    expect(steps.map((step) => step.kind)).toEqual(["read", "search", "shell", "tool"]);
    const entries = buildJournalTimeline(steps, { streaming: false });
    expect(entries.map((entry) => entry.kind)).toEqual(["explore", "shell", "tool"]);
    expect(entries[0].filesList).toEqual(["webui/src/App.tsx"]);
    expect(entries[0].queriesList).toEqual(["navin"]);
    expect(entries[1].command).toBe("npm test");
    expect(entries[2].target).toBe("Open Preview");
    expect(entries[2].tone).toBe("task");
  });

  it("keeps shell commands as always-visible runs", () => {
    const runs = collectShellRuns([
      trace("t1", ['exec({"command":"echo signes-de-vie"})']),
    ]);
    expect(runs.length).toBeGreaterThanOrEqual(1);
    expect(runs.some((run) => run.command.includes("echo"))).toBe(true);
  });

  it("keeps the sandbox fact from the meta frame through the end frame", () => {
    const args = { command: "npm test" };
    const message: UIMessage = {
      ...trace("t1", ['exec({"command":"npm test"})']),
      toolEvents: [
        { phase: "start", call_id: "c1", name: "exec", arguments: args },
        { phase: "output", call_id: "c1", name: "exec", arguments: args, sandbox: "native" },
        { phase: "output", call_id: "c1", name: "exec", arguments: args, output: "1 passed" },
        { phase: "end", call_id: "c1", name: "exec", arguments: args, result: "1 passed\nExit code: 0" },
      ],
    };
    const [run] = collectShellRuns([message]);
    expect(run.sandbox).toBe("native");
    expect(run.sandboxLifted).toBeUndefined();
    expect(run.status).toBe("done");
    expect(run.output).toContain("1 passed");
  });

  it("marks a command the user let out of the sandbox", () => {
    const args = { command: "sudo apt install jq", unsandboxed: true };
    const message: UIMessage = {
      ...trace("t1", ['exec({"command":"sudo apt install jq","unsandboxed":true})']),
      toolEvents: [
        { phase: "start", call_id: "c2", name: "exec", arguments: args },
        { phase: "output", call_id: "c2", name: "exec", arguments: args, sandbox_lifted: true },
        { phase: "end", call_id: "c2", name: "exec", arguments: args, result: "ok" },
      ],
    };
    const [run] = collectShellRuns([message]);
    expect(run.sandboxLifted).toBe(true);
    expect(run.sandbox).toBeUndefined();
  });

  it("says nothing about the sandbox when no frame mentions it", () => {
    const args = { command: "ls" };
    const message: UIMessage = {
      ...trace("t1", ['exec({"command":"ls"})']),
      toolEvents: [
        { phase: "start", call_id: "c3", name: "exec", arguments: args },
        { phase: "end", call_id: "c3", name: "exec", arguments: args, result: "a\nb" },
      ],
    };
    const [run] = collectShellRuns([message]);
    expect(run.sandbox).toBeUndefined();
    expect(run.sandboxLifted).toBeUndefined();
  });
});

describe("block duration in the cluster header", () => {
  const t0 = 1_700_000_000_000;
  const steps = [trace("t1", ['read_file({"path":"a.ts"})'], t0 + 20_000)];

  it("reads the block's own span before the turn-wide latency", () => {
    expect(activityDurationMs(steps, false, t0 + 999_000, 2_882_000, t0, t0 + 60_000)).toBe(60_000);
  });

  it("falls back to the turn latency when the block has no end", () => {
    expect(activityDurationMs(steps, false, t0 + 999_000, 2_882_000, t0, undefined)).toBe(2_882_000);
  });

  it("counts from the block start to now while live", () => {
    expect(activityDurationMs(steps, true, t0 + 95_000, undefined, t0 + 80_000, undefined)).toBe(15_000);
  });

  it("ignores an end stamped before the start", () => {
    expect(activityDurationMs(steps, false, t0, 1_000, t0 + 50_000, t0 + 40_000)).toBe(1_000);
  });
});

describe("time by phase", () => {
  const t0 = 1_700_000_000_000;

  it("gives each message the time until the next one and shares a trace across its calls", () => {
    const split = activityPhaseSplit([
      reasoning("r1", t0),
      trace("t1", [
        'read_file({"path":"a.ts"})',
        'edit_file({"path":"a.ts"})',
      ], t0 + 10_000),
      trace("t2", ['exec({"command":"npm test"})'], t0 + 30_000),
    ], 40_000, t0);
    expect(split).toEqual([
      { key: "thinking", ms: 10_000 },
      { key: "explore", ms: 10_000 },
      { key: "edit", ms: 10_000 },
      { key: "command", ms: 10_000 },
    ]);
  });

  it("leaves the time spent waiting on the user out of the work", () => {
    // exec at +0, the ask_user card opens at +10s and is answered at +7m10s.
    const echo: UIMessage = {
      id: "choice-r1",
      role: "tool",
      kind: "choice",
      content: "",
      createdAt: t0 + 430_000,
      interaction: { type: "choice", requestId: "r1", question: "Which file?", label: "config.ts", waitedMs: 420_000 },
    } as UIMessage;
    const messages = [trace("t1", ['exec({"command":"ls"})'], t0), echo, trace("t2", ['exec({"command":"wc"})'], t0 + 440_000)];
    const worked = activityDurationMs(messages, false, t0 + 999_000, undefined, t0, t0 + 450_000);
    expect(worked).toBe(30_000);
    const split = activityPhaseSplit(messages, worked, t0);
    expect(split).toEqual([{ key: "command", ms: 30_000 }]);
  });

  it("counts the wait before the first activity as thinking and drops slivers", () => {
    const split = activityPhaseSplit([
      trace("t1", ['board({"action":"list"})'], t0 + 5_000),
    ], 5_200, t0);
    expect(split).toEqual([{ key: "thinking", ms: 5_000 }]);
  });

  it("returns nothing without timestamps or without a duration", () => {
    expect(activityPhaseSplit([trace("t1", ['exec({"command":"ls"})'], 0)], 10_000)).toEqual([]);
    expect(activityPhaseSplit([trace("t1", ['exec({"command":"ls"})'], t0)], 0)).toEqual([]);
  });
});

describe("activity cluster source locks", () => {
  const cluster = readFileSync(resolve(__dirname, "AgentActivityCluster.tsx"), "utf8");
  const journal = readFileSync(resolve(__dirname, "activity/ActivityJournal.tsx"), "utf8");
  const row = readFileSync(resolve(__dirname, "activity/ReasoningRow.tsx"), "utf8");
  const bubble = readFileSync(resolve(__dirname, "../MessageBubble.tsx"), "utf8");
  const thread = readFileSync(resolve(__dirname, "ThreadMessages.tsx"), "utf8");
  const css = readFileSync(resolve(__dirname, "../../globals.css"), "utf8");

  it("keeps thinking in the journal, in time order, with a click-to-expand body", () => {
    // Reasoning is no longer one card pinned above the journal: each
    // segment is a journal row between the calls it led to.
    expect(cluster).not.toContain("<ReasoningCard");
    expect(cluster).not.toContain("<ReasoningRow");
    expect(journal).toContain("ReasoningJournalBody");
    expect(journal).toContain('data-testid="activity-reasoning-preview"');
    expect(journal).toContain("whitespace-pre-line");
    expect(row).toContain('data-testid="activity-reasoning-card"');
    expect(row).not.toContain("rounded-xl border");
    expect(row).toContain("line-clamp-6");
    expect(row).toContain("text-muted-foreground");
    expect(row).not.toContain("text-foreground/90");
    expect(row).toContain("REASONING_PROSE_CLASS");
    expect(row).toContain("duration?: string");
    expect(row).toContain('data-testid="activity-reasoning-copy"');
    expect(row).toContain("reasoningExpand");
    expect(row).toContain("useState(false)");
    expect(bubble).not.toContain("userToggled ? openLocal : true");
  });

  it("keeps the journal chronological, colored by tone, with outcome and details per row", () => {
    expect(cluster).toContain("ActivityJournal");
    expect(cluster).toContain("buildJournalTimeline");
    expect(cluster).toContain("JournalNowLine");
    expect(cluster).toContain("ActivityDigest");
    expect(journal).toContain('data-testid="activity-task-log"');
    expect(journal).toContain('data-testid="activity-journal-open"');
    expect(journal).toContain('data-testid="activity-journal-row"');
    expect(journal).toContain('data-testid="activity-journal-toggle"');
    expect(journal).toContain('data-testid="activity-journal-dot"');
    expect(journal).toContain('data-testid="activity-journal-aside"');
    expect(journal).toContain('data-testid="activity-digest"');
    expect(journal).toContain('data-testid="activity-now-line"');
    expect(journal).toContain("JOURNAL_TARGET_CLASS");
    expect(journal).toContain("text-foreground/92");
    expect(journal).toContain("--tone-explore");
    expect(journal).toContain("--tone-read");
    expect(journal).toContain("ShellRunCard");
    expect(journal).toContain("FileReferenceChip");
    expect(css).toContain("--tone-explore:");
    expect(css).toContain("--tone-read:");
    expect(css).toContain("--tone-error:");
  });

  it("follows the live turn, then folds it to a digest once it is done", () => {
    expect(cluster).toContain("isLatestTurn");
    expect(thread).toContain("isLatestTurn={index === latestActivityIndex}");
    expect(thread).toContain("lastActivityUnitIndex");
    // Open only while live (or when the turn ended without an answer).
    // Earlier blocks of a running turn stay open too: folding them above the
    // live block jumped the page under the reader.
    expect(cluster).toContain("isTurnStreaming || turnRunning || (isLatestTurn && !hasBodyBelow)");
    expect(thread).toContain("turnRunning={isStreaming && index > lastUserUnitIndex}");
    // The body slides shut instead of vanishing; file rows fold with it.
    expect(cluster).toContain("<ActivityFold open={bodyOpen}");
    expect(cluster).toContain('data-testid="file-edit-activity-toggle"');
    expect(cluster).toContain("useJustSettled(isTurnStreaming)");
    // Live, the diff block under the journal kept the followed tail on old
    // diffs while new steps landed above it: it only shows once settled.
    expect(cluster).toContain("fileEdits.length && !isTurnStreaming ?");
  });

  it("honors the Activity detail preference: expanded, compact and digest", () => {
    expect(cluster).toContain("useActivityMode");
    expect(cluster).toContain('activityMode === "expanded"');
    expect(cluster).toContain('activityMode === "digest"');
    expect(cluster).toContain('activityMode === "compact"');
    expect(cluster).toContain("compact={compactActivity}");
    expect(cluster).toContain("JOURNAL_PREVIEW_MAX_COMPACT");
    expect(row).toContain("compact?: boolean");
    expect(row).toContain("!open && !compact && summary");
  });

  it("shows where the time went and lets a task title open its board card", () => {
    expect(cluster).toContain("ActivityPhaseBar");
    expect(cluster).toContain("activityPhaseSplit(messages, durationMs, startedAtMs)");
    expect(journal).toContain('data-testid="activity-phase-split"');
    expect(journal).toContain('data-testid="activity-journal-task"');
    expect(journal).toContain("requestOpenBoardTask(link)");
  });
});
