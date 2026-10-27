// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { TFunction } from "i18next";
import { describe, expect, it, vi } from "vitest";

import { parseSubagentSteps, type SubagentProgressUpdate } from "@/lib/navin-client";

import {
  formatElapsed,
  formatTokens,
  createSubagentCardStore,
  subagentDismissedKey,
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

describe("finished cards stay until dismissed", () => {
  it("keeps the first finish time across duplicate frames", () => {
    let cards = upsertSubagentCard([], base({ taskId: "a", label: "A" }), 1_000);
    cards = upsertSubagentCard(cards, base({ taskId: "a", label: "A", done: true, phase: "done" }), 2_000);
    cards = upsertSubagentCard(cards, base({ taskId: "a", label: "A", done: true, phase: "done" }), 9_000);
    expect(cards[0]?.finishedAt).toBe(2_000);
  });

  it("store keeps finished cards (ok, failed, cancelled)", () => {
    const store = createSubagentCardStore();
    store.upsert(base({ taskId: "ok", label: "Ok", done: true, phase: "done" }));
    store.upsert(base({ taskId: "ko", label: "Ko", done: true, phase: "error", error: "boom" }));
    store.upsert(base({ taskId: "st", label: "St", done: true, phase: "error", errorKind: "cancelled" }));
    expect(store.getSnapshot().cards.map((c) => c.taskId)).toEqual(["ok", "ko", "st"]);
  });

  it("dismiss hides a card, ignores a late done frame, shows a resumed one", () => {
    const store = createSubagentCardStore();
    store.upsert(base({ taskId: "a", label: "A", done: true, phase: "error", error: "x" }));
    store.setExpanded(new Set(["a"]));
    store.dismiss("a");
    expect(store.getSnapshot().cards).toEqual([]);
    expect(store.getSnapshot().expanded.has("a")).toBe(false);
    expect(store.isDismissed("a")).toBe(true);
    store.upsert(base({ taskId: "a", label: "A", done: true, phase: "error", error: "x" }));
    expect(store.getSnapshot().cards).toEqual([]);
    store.upsert(base({ taskId: "a", label: "A", phase: "awaiting_tools" }));
    expect(store.getSnapshot().cards.map((c) => c.taskId)).toEqual(["a"]);
    expect(store.isDismissed("a")).toBe(false);
  });

  it("dismissFinished keeps only the cards still in flight", () => {
    const store = createSubagentCardStore();
    store.upsert(base({ taskId: "run", label: "Run" }));
    store.upsert(base({ taskId: "ok", label: "Ok", done: true, phase: "done" }));
    store.upsert(base({ taskId: "ko", label: "Ko", done: true, phase: "error" }));
    store.dismissFinished();
    expect(store.getSnapshot().cards.map((c) => c.taskId)).toEqual(["run"]);
  });

  it("persists dismissed ids per chat", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (k: string) => data.get(k) ?? null,
        setItem: (k: string, v: string) => void data.set(k, v),
      },
    });
    try {
      const store = createSubagentCardStore();
      store.reset("chat-9");
      store.upsert(base({ taskId: "a", label: "A", done: true, phase: "error" }));
      store.dismiss("a");
      expect(subagentDismissedKey("chat-9")).toBe("navin.subagents.dismissed.chat-9");
      expect(JSON.parse(data.get("navin.subagents.dismissed.chat-9") ?? "[]")).toEqual(["a"]);
      const fresh = createSubagentCardStore();
      fresh.reset("chat-9");
      fresh.upsert(base({ taskId: "a", label: "A", done: true, phase: "error" }));
      expect(fresh.getSnapshot().cards).toEqual([]);
      fresh.reset("chat-other");
      fresh.upsert(base({ taskId: "a", label: "A", done: true, phase: "error" }));
      expect(fresh.getSnapshot().cards).toHaveLength(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("store notifies on frames and resets on chat switch", () => {
    const store = createSubagentCardStore();
    let calls = 0;
    const off = store.subscribe(() => {
      calls += 1;
    });
    store.upsert(base({ taskId: "a", label: "A" }));
    expect(store.getSnapshot().cards).toHaveLength(1);
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
