// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import { parseSubagentSteps, type SubagentProgressUpdate } from "@/lib/navin-client";

import {
  FINISHED_CARD_LINGER_MS,
  formatElapsed,
  formatTokens,
  createSubagentCardStore,
  nextSubagentPruneAt,
  pruneFinishedSubagentCards,
  subagentStatusLine,
  subagentStepLabel,
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

describe("finished card linger", () => {
  it("counts from the finish, not from later duplicate frames", () => {
    let cards = upsertSubagentCard([], base({ taskId: "a", label: "A" }), 1_000);
    cards = upsertSubagentCard(
      cards,
      base({ taskId: "a", label: "A", done: true, phase: "done" }),
      2_000,
    );
    cards = upsertSubagentCard(
      cards,
      base({ taskId: "a", label: "A", done: true, phase: "done" }),
      9_000,
    );
    expect(cards[0]?.finishedAt).toBe(2_000);
    expect(pruneFinishedSubagentCards(cards, 2_000 + FINISHED_CARD_LINGER_MS)).toEqual([]);
  });

  it("keeps the deadline when a sibling still running sends frames", () => {
    let cards = upsertSubagentCard(
      [],
      base({ taskId: "done", label: "D", done: true, phase: "done" }),
      5_000,
    );
    cards = upsertSubagentCard(cards, base({ taskId: "run", label: "R" }), 5_100);
    const due = nextSubagentPruneAt(cards);
    for (let t = 6_000; t < 16_000; t += 400) {
      cards = upsertSubagentCard(
        cards,
        base({ taskId: "run", label: "R", phase: "awaiting_tools", iteration: t }),
        t,
      );
      expect(nextSubagentPruneAt(cards)).toBe(due);
    }
    expect(due).toBe(5_000 + FINISHED_CARD_LINGER_MS);
    expect(nextSubagentPruneAt(cards, new Set(["done"]))).toBeNull();
  });

  it("store notifies on frames and resets on chat switch", () => {
    const store = createSubagentCardStore();
    let calls = 0;
    const off = store.subscribe(() => {
      calls += 1;
    });
    store.upsert(base({ taskId: "a", label: "A" }));
    expect(store.getSnapshot().cards).toHaveLength(1);
    // Nothing to prune: no notification, same snapshot.
    const before = store.getSnapshot();
    store.prune();
    expect(store.getSnapshot()).toBe(before);
    store.reset();
    expect(store.getSnapshot().cards).toEqual([]);
    off();
    expect(calls).toBe(2);
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

describe("pruneFinishedSubagentCards with an opened card", () => {
  it("keeps a finished card the user has expanded", () => {
    const now = Date.now();
    const cards: ParallelSubagentCard[] = [
      {
        ...base({ taskId: "open", label: "Open", done: true, phase: "error" }),
        updatedAt: now - FINISHED_CARD_LINGER_MS - 1000,
        startedAt: now - 60_000,
      },
    ];
    expect(pruneFinishedSubagentCards(cards, now, new Set(["open"]))).toBe(cards);
    expect(pruneFinishedSubagentCards(cards, now)).toEqual([]);
  });
});

describe("subagent status text", () => {
  const t = ((key: string, opts?: { defaultValue?: string; name?: string }) =>
    (opts?.defaultValue ?? key).replace("{{name}}", opts?.name ?? "")) as unknown as TFunction;

  function card(partial: Partial<SubagentProgressUpdate>): ParallelSubagentCard {
    return {
      ...base({ taskId: "a", label: "A", ...partial }),
      updatedAt: 0,
      startedAt: 0,
    };
  }

  it("describes the latest step instead of raw tool output", () => {
    const line = subagentStatusLine(
      card({
        phase: "tools_completed",
        statusLine: "write_file: Successfully wrote 2982 characters",
        steps: [{ kind: "write", verb: "Wrote", target: "ar.json", status: "ok" }],
      }),
      t,
    );
    expect(line).toBe("Wrote ar.json");
  });

  it("names the stop reason in a few words", () => {
    expect(
      subagentStatusLine(
        card({ phase: "error", done: true, statusLine: "Model timed out", errorKind: "timeout" }),
        t,
      ),
    ).toBe("Model timed out");
  });

  it("never shows backend text for an unclassified error", () => {
    const line = subagentStatusLine(
      card({
        phase: "error",
        done: true,
        statusLine: "OSError at /home/a/x.py connecting to 10.0.0.1",
        errorKind: "other",
      }),
      t,
    );
    expect(line).toBe("Stopped on an error");
    expect(
      subagentStatusLine(
        card({ phase: "error", done: true, statusLine: "raw thing", error: "raw thing" }),
        t,
      ),
    ).toBe("Stopped on an error");
  });

  it("says the model is thinking or writing during a long generation", () => {
    const steps = [{ kind: "read", verb: "Read", target: "en.json", status: "ok" as const }];
    expect(subagentStatusLine(card({ phase: "thinking", steps }), t)).toBe("Thinking…");
    expect(subagentStatusLine(card({ phase: "responding", steps }), t)).toBe(
      "Writing response…",
    );
  });

  it("names the last step that went right, not a failed retry", () => {
    const line = subagentStatusLine(
      card({
        phase: "tools_completed",
        steps: [
          { kind: "read", verb: "Read", target: "en.json", status: "ok" },
          { kind: "write", verb: "Could not write", target: "fr.json", status: "error" },
        ],
      }),
      t,
    );
    expect(line).toBe("Read en.json");
  });

  it("names a patch without a path as edited files", () => {
    expect(
      subagentStepLabel(
        { kind: "patch", verb: "Edited files", target: "", status: "ok", name: "apply_patch" },
        t,
      ),
    ).toBe("Edited files");
  });

  it("labels an unknown tool by name", () => {
    expect(
      subagentStepLabel({ kind: "tool", verb: "Used crm", target: "", status: "ok", name: "crm" }, t),
    ).toBe("Used crm");
  });
});

describe("parseSubagentSteps", () => {
  it("keeps well-formed steps and drops junk", () => {
    expect(
      parseSubagentSteps([
        { kind: "read", verb: "Read", target: "a.py", status: "ok" },
        null,
        { kind: "run", verb: "Running", target: "ls", status: "running" },
        { kind: "write", verb: "Could", target: "b", status: "weird" },
      ]),
    ).toEqual([
      { kind: "read", verb: "Read", target: "a.py", status: "ok", name: undefined },
      { kind: "run", verb: "Running", target: "ls", status: "running", name: undefined },
      { kind: "write", verb: "Could", target: "b", status: "ok", name: undefined },
    ]);
    expect(parseSubagentSteps("nope")).toBeUndefined();
    expect(parseSubagentSteps([])).toBeUndefined();
  });
});

describe("formatTokens", () => {
  it("is compact", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(1200)).toBe("1.2k");
    expect(formatTokens(335_500)).toBe("336k");
    expect(formatTokens(2_000_000)).toBe("2M");
  });
});
