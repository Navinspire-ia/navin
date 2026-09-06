import { describe, expect, it } from "vitest";

import { normalizeActivityTimeline, type TurnUnit } from "@/lib/activity-timeline";
import type { UIMessage } from "@/lib/types";

const T0 = 1_700_000_000_000;

function user(createdAt: number): UIMessage {
  return { id: "u", role: "user", content: "go", createdAt, turnId: "turn-1", turnSeq: 0 };
}

function trace(id: string, lines: string[], createdAt: number): UIMessage {
  return {
    id,
    role: "assistant",
    content: lines.join("\n"),
    kind: "trace",
    createdAt,
    traces: lines,
    turnId: "turn-1",
    turnSeq: createdAt - T0,
  };
}

function answer(id: string, content: string, createdAt: number, extra: Partial<UIMessage> = {}): UIMessage {
  return { id, role: "assistant", content, createdAt, turnId: "turn-1", turnSeq: createdAt - T0, ...extra };
}

function reasoning(id: string, createdAt: number): UIMessage {
  return { ...answer(id, "", createdAt), reasoning: "Let me look." };
}

const shape = (units: TurnUnit[]) =>
  units.map((unit) => (unit.type === "message" ? unit.message.id : "activity"));

const activityUnits = (units: TurnUnit[]) =>
  units.filter((unit): unit is Extract<TurnUnit, { type: "activity" }> => unit.type === "activity");

describe("normalizeActivityTimeline: blocks of work between explanations", () => {
  it("keeps work and explanations in order and gives each block its own span", () => {
    const units = normalizeActivityTimeline([
      user(T0),
      reasoning("r1", T0 + 1_000),
      trace("t1", ['read_file({"path":"a.ts"})'], T0 + 20_000),
      answer("a1", "Now the TypeScript side.", T0 + 60_000),
      trace("t2", ['edit_file({"path":"b.ts"})'], T0 + 80_000),
      answer("a2", "Done.", T0 + 200_000, { latencyMs: 250_000 }),
    ]);

    expect(shape(units)).toEqual(["u", "activity", "a1", "activity", "a2"]);
    const [first, second] = activityUnits(units);
    // The first block starts with the user's message: the wait before the
    // first step is the model thinking. It ends when the explanation starts.
    expect(first.startedAtMs).toBe(T0);
    expect(first.endedAtMs).toBe(T0 + 60_000);
    expect(first.messages.map((message) => message.id)).toEqual(["r1", "t1"]);
    // The second block owns only its own steps, up to the final answer.
    expect(second.startedAtMs).toBe(T0 + 80_000);
    expect(second.endedAtMs).toBe(T0 + 200_000);
    // The turn-wide latency is shared, not re-attributed to one block.
    expect(first.turnLatencyMs).toBe(250_000);
    expect(second.turnLatencyMs).toBe(250_000);
  });

  it("ends a trailing block at the turn end, even when the answer that carries the latency came first", () => {
    const units = normalizeActivityTimeline([
      user(T0),
      answer("a1", "Let me check the build.", T0 + 5_000, { latencyMs: 90_000 }),
      trace("t1", ['exec({"command":"npm test"})'], T0 + 10_000),
    ]);

    // Completed turn: the answer is shown after the work it announced.
    expect(shape(units)).toEqual(["u", "activity", "a1"]);
    const [block] = activityUnits(units);
    expect(block.startedAtMs).toBe(T0 + 10_000);
    expect(block.endedAtMs).toBe(T0 + 90_000);
    expect(block.turnLatencyMs).toBe(90_000);
  });

  it("leaves a live trailing block open-ended", () => {
    const units = normalizeActivityTimeline(
      [user(T0), trace("t1", ['exec({"command":"npm test"})'], T0 + 5_000)],
      { preserveTrailingActivity: true },
    );

    const [block] = activityUnits(units);
    expect(block.startedAtMs).toBe(T0);
    expect(block.endedAtMs).toBeUndefined();
    expect(block.turnLatencyMs).toBeUndefined();
  });

  it("falls back to the first step when the user message has no timestamp", () => {
    const units = normalizeActivityTimeline([
      { ...user(T0), createdAt: Number.NaN },
      trace("t1", ['read_file({"path":"a.ts"})'], T0 + 3_000),
      answer("a1", "Done.", T0 + 9_000),
    ]);

    const [block] = activityUnits(units);
    expect(block.startedAtMs).toBe(T0 + 3_000);
    expect(block.endedAtMs).toBe(T0 + 9_000);
  });

  it("never reports an end before the start", () => {
    const units = normalizeActivityTimeline([
      user(T0),
      answer("a1", "First.", T0 + 1_000),
      // Out-of-order clock: the step is stamped after the answer that follows it.
      trace("t1", ['read_file({"path":"a.ts"})'], T0 + 50_000),
      answer("a2", "Second.", T0 + 40_000),
    ]);

    const blocks = activityUnits(units);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].endedAtMs).toBeUndefined();
  });
});
