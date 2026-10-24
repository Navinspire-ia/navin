// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import en from "@/i18n/locales/en/common.json";
import fr from "@/i18n/locales/fr/common.json";

import type { UIMessage } from "@/lib/types";

import {
  assistantCopyFlags,
  AWAITING_SLOW_HINT_MS,
  promptUnitIndex,
  unitActionIndexes,
  unitKeysForDisplay,
  turnHasToolFlags,
  type DisplayUnit,
} from "./ThreadMessages";

function user(id: string): DisplayUnit {
  return {
    type: "message",
    message: { id, role: "user", content: id, kind: "message", createdAt: 1 } as UIMessage,
  };
}

function assistant(id: string): DisplayUnit {
  return {
    type: "message",
    message: { id, role: "assistant", content: id, kind: "message", createdAt: 1 } as UIMessage,
  };
}

describe("unitActionIndexes", () => {
  it("keeps user and fork indexes stable when rows are skipped", () => {
    const units = [user("u1"), assistant("a1"), user("u2"), assistant("a2")];
    const flags = assistantCopyFlags(units);
    const { userIndex, forkIndex } = unitActionIndexes(units, 4, flags);
    expect(userIndex[0]).toBe(4);
    expect(userIndex[2]).toBe(5);
    expect(forkIndex[1]).toBe(5);
    expect(forkIndex[3]).toBe(6);
  });
});

describe("promptUnitIndex", () => {
  it("finds the user turn that a rail jump must keep mounted", () => {
    const units = [user("u1"), assistant("a1"), user("u2")];
    expect(promptUnitIndex(units, "u2")).toBe(2);
    expect(promptUnitIndex(units, "a1")).toBe(1);
    expect(promptUnitIndex(units, "missing")).toBe(-1);
    expect(promptUnitIndex(units, null)).toBe(-1);
  });
});

describe("awaiting first output", () => {
  it("leaves the label and the clock to the composer status", () => {
    const source = readFileSync(resolve(__dirname, "ThreadMessages.tsx"), "utf8");
    const indicator = source.slice(source.indexOf("function AwaitingFirstOutputIndicator"));
    const body = indicator.slice(0, indicator.indexOf("function PulseDot"));
    expect(body).not.toContain("assistantThinking");
    expect(body).not.toContain("assistantWorkingFor");
    expect(body).not.toContain("setInterval");
    expect(AWAITING_SLOW_HINT_MS).toBe(60_000);
  });

  it("does not bake the duration into Working, so the clock cannot print twice", () => {
    expect(en.thread.assistantWorkingFor).toBe("Working");
    expect(fr.thread.assistantWorkingFor).toBe("En cours");
    expect(en.thread.assistantWorkingFor).not.toContain("{{duration}}");
    expect(fr.thread.assistantWorkingFor).not.toContain("{{duration}}");
  });
});

describe("turnHasToolFlags", () => {
  function activity(types: Array<"reasoning" | "tool">): DisplayUnit {
    const message = { id: "x", role: "tool", content: "", kind: "trace", createdAt: 1 } as UIMessage;
    return { type: "activity", messages: [message], items: types.map((type) => ({ type, message })) };
  }

  it("flags every activity block of a turn that ran a real tool", () => {
    const units = [
      user("u1"), activity(["reasoning"]), assistant("a1"), activity(["tool"]),
      user("u2"), activity(["reasoning"]), assistant("a2"),
    ];
    expect(turnHasToolFlags(units)).toEqual([false, true, false, true, false, false, false]);
  });
});

describe("unitKeysForDisplay", () => {
  it("keeps the same key when turnPhase fills in after the first frames", () => {
    const before: DisplayUnit[] = [
      {
        type: "message",
        message: {
          id: "live-1",
          role: "assistant",
          content: "hello",
          kind: "message",
          turnId: "turn-a",
          createdAt: 1,
        } as UIMessage,
      },
    ];
    const after: DisplayUnit[] = [
      {
        type: "message",
        message: {
          id: "hist-1",
          role: "assistant",
          content: "hello",
          kind: "message",
          turnId: "turn-a",
          turnPhase: "answer",
          createdAt: 1,
        } as UIMessage,
      },
    ];
    expect(unitKeysForDisplay(before)).toEqual(unitKeysForDisplay(after));
    expect(unitKeysForDisplay(before)[0]).toBe("turn-turn-a-assistant-1");
  });

  it("does not remount an activity cluster when the first trace id changes", () => {
    const first: DisplayUnit = {
      type: "activity",
      messages: [
        {
          id: "trace-live",
          role: "assistant",
          content: "crm",
          kind: "trace",
          turnId: "turn-b",
          createdAt: 1,
        } as UIMessage,
      ],
      items: [],
    };
    const hydrated: DisplayUnit = {
      type: "activity",
      messages: [
        {
          id: "trace-hist",
          role: "assistant",
          content: "crm",
          kind: "trace",
          turnId: "turn-b",
          turnPhase: "activity",
          createdAt: 1,
        } as UIMessage,
      ],
      items: [],
    };
    expect(unitKeysForDisplay([first])).toEqual(unitKeysForDisplay([hydrated]));
  });
});

describe("ThreadMessages scroll memo wiring", () => {
  it("does not allocate fork/revert lambdas in the list map", () => {
    const source = readFileSync(resolve(__dirname, "ThreadMessages.tsx"), "utf8");
    expect(source).toContain("const ThreadUnitRow = memo(function ThreadUnitRow");
    expect(source).toContain("isLatestTurn={index === latestActivityIndex}");
    expect(source).not.toContain("() => onForkFromMessage(forkIndex)");
    expect(source).not.toContain("(content) => onRevertResubmit(userIndexForEdit, content)");
  });
});

