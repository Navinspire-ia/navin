import { describe, expect, it } from "vitest";

import type { SubagentProgressUpdate } from "@/lib/navin-client";

import {
  FINISHED_CARD_LINGER_MS,
  formatElapsed,
  pruneFinishedSubagentCards,
  upsertSubagentCard,
  type ParallelSubagentCard,
} from "./ParallelSubagentsPanel";

function base(
  partial: Partial<SubagentProgressUpdate> & Pick<SubagentProgressUpdate, "taskId" | "label">,
): SubagentProgressUpdate {
  return {
    chatId: "chat-1",
    phase: "initializing",
    statusLine: "Starting…",
    iteration: 0,
    done: false,
    model: "test-model",
    ...partial,
  };
}

describe("upsertSubagentCard", () => {
  it("stacks three parallel subagents as distinct cards", () => {
    let cards: ParallelSubagentCard[] = [];
    for (const label of ["P1-A", "P1-B", "P1-C"]) {
      cards = upsertSubagentCard(
        cards,
        base({ taskId: label.toLowerCase(), label, statusLine: `Starting ${label}` }),
      );
    }
    expect(cards).toHaveLength(3);
    expect(cards.map((c) => c.label)).toEqual(["P1-A", "P1-B", "P1-C"]);
    expect(cards.every((c) => !c.done)).toBe(true);
  });

  it("updates the same task in place instead of duplicating", () => {
    let cards = upsertSubagentCard(
      [],
      base({ taskId: "a1", label: "Artifacts", statusLine: "Starting…" }),
    );
    cards = upsertSubagentCard(
      cards,
      base({
        taskId: "a1",
        label: "Artifacts",
        phase: "awaiting_tools",
        statusLine: "Running write_file",
        iteration: 2,
      }),
    );
    expect(cards).toHaveLength(1);
    expect(cards[0]?.statusLine).toBe("Running write_file");
    expect(cards[0]?.iteration).toBe(2);
  });

  it("marks a card done with error", () => {
    let cards = upsertSubagentCard(
      [],
      base({ taskId: "x", label: "Broken" }),
    );
    cards = upsertSubagentCard(
      cards,
      base({
        taskId: "x",
        label: "Broken",
        phase: "error",
        statusLine: "boom",
        done: true,
        error: "boom",
      }),
    );
    expect(cards[0]?.done).toBe(true);
    expect(cards[0]?.error).toBe("boom");
  });

  it("dates a replayed card from when the subagent actually started", () => {
    const now = 1_000_000;
    const cards = upsertSubagentCard(
      [],
      base({ taskId: "old", label: "Deck", startedMsAgo: 660_000 }),
      now,
    );
    expect(cards[0]?.startedAt).toBe(now - 660_000);
  });

  it("keeps the original start across later frames", () => {
    const start = 500_000;
    let cards = upsertSubagentCard(
      [],
      base({ taskId: "a1", label: "Deck", startedMsAgo: 30_000 }),
      start,
    );
    cards = upsertSubagentCard(
      cards,
      base({ taskId: "a1", label: "Deck", statusLine: "Running write_file" }),
      start + 90_000,
    );
    expect(cards[0]?.startedAt).toBe(start - 30_000);
  });

  it("resets the clock when a queued card actually starts", () => {
    const enqueue = 500_000;
    let cards = upsertSubagentCard(
      [],
      base({
        taskId: "a1",
        label: "Deck",
        phase: "queued",
        statusLine: "Queued - waiting for a free slot",
        startedMsAgo: 0,
      }),
      enqueue,
    );
    cards = upsertSubagentCard(
      cards,
      base({
        taskId: "a1",
        label: "Deck",
        phase: "initializing",
        statusLine: "Starting…",
        startedMsAgo: 0,
      }),
      enqueue + 180_000,
    );
    expect(cards[0]?.startedAt).toBe(enqueue + 180_000);
    expect(cards[0]?.phase).toBe("initializing");
  });
});

describe("pruneFinishedSubagentCards", () => {
  it("keeps running cards and recently finished ones", () => {
    const now = Date.now();
    const cards: ParallelSubagentCard[] = [
      {
        ...base({ taskId: "old", label: "Old", done: true, phase: "done" }),
        updatedAt: now - FINISHED_CARD_LINGER_MS - 1000,
        startedAt: now - 60_000,
      },
      {
        ...base({ taskId: "fresh", label: "Fresh", done: true, phase: "done" }),
        updatedAt: now - 1000,
        startedAt: now - 30_000,
      },
      {
        ...base({ taskId: "run", label: "Run" }),
        updatedAt: now,
        startedAt: now - 5000,
      },
    ];
    expect(pruneFinishedSubagentCards(cards, now).map((c) => c.taskId)).toEqual([
      "fresh",
      "run",
    ]);
  });
});

describe("formatElapsed", () => {
  it("reads as seconds, then minutes, then hours", () => {
    expect(formatElapsed(0)).toBe("0s");
    expect(formatElapsed(12_400)).toBe("12s");
    expect(formatElapsed(59_999)).toBe("59s");
    expect(formatElapsed(60_000)).toBe("1m");
    expect(formatElapsed(11 * 60_000)).toBe("11m");
    expect(formatElapsed(64 * 60_000)).toBe("1h 4m");
    expect(formatElapsed(120 * 60_000)).toBe("2h");
  });

  it("never shows a negative duration for a clock skew", () => {
    expect(formatElapsed(-5000)).toBe("0s");
  });
});
