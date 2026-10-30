import { describe, expect, it } from "vitest";

import { reconcileSessionRows } from "./useSessions";
import type { ChatSummary } from "@/lib/types";

const row = (key: string, extra: Partial<ChatSummary> = {}): ChatSummary =>
  ({
    key,
    channel: "websocket",
    chatId: key,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    title: key,
    preview: "",
    runStartedAt: null,
    workspaceScope: null,
    ...extra,
  }) as ChatSummary;

describe("reconcileSessionRows", () => {
  it("returns the previous array when nothing changed", () => {
    const prev = [row("a"), row("b")];
    const next = [row("a"), row("b")];
    expect(reconcileSessionRows(prev, next)).toBe(prev);
  });

  it("keeps identity of unchanged rows and replaces changed ones", () => {
    const prev = [row("a"), row("b")];
    const next = [row("a"), row("b", { preview: "new" })];
    const out = reconcileSessionRows(prev, next);
    expect(out).not.toBe(prev);
    expect(out[0]).toBe(prev[0]);
    expect(out[1]).not.toBe(prev[1]);
    expect(out[1].preview).toBe("new");
  });

  it("detects reorder and removal", () => {
    const prev = [row("a"), row("b"), row("c")];
    const reordered = reconcileSessionRows(prev, [row("b"), row("a"), row("c")]);
    expect(reordered.map((r) => r.key)).toEqual(["b", "a", "c"]);
    expect(reordered[0]).toBe(prev[1]);
    expect(reconcileSessionRows(prev, [row("a")]).map((r) => r.key)).toEqual(["a"]);
  });
});
