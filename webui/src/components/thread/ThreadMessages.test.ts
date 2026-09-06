import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import en from "@/i18n/locales/en/common.json";
import fr from "@/i18n/locales/fr/common.json";

import type { UIMessage } from "@/lib/types";

import {
  assistantCopyFlags,
  awaitingFirstOutputPhase,
  promptUnitIndex,
  unitActionIndexes,
  unitKeysForDisplay,
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

describe("awaitingFirstOutputPhase", () => {
  it("keeps the clock on one side of Working, never inside the label", () => {
    expect(awaitingFirstOutputPhase(0)).toBe("thinking");
    expect(awaitingFirstOutputPhase(14_999)).toBe("thinking");
    expect(awaitingFirstOutputPhase(15_000)).toBe("working");
    expect(awaitingFirstOutputPhase(59_999)).toBe("working");
    expect(awaitingFirstOutputPhase(60_000)).toBe("slow");
  });

  it("does not bake the duration into Working, so the clock cannot print twice", () => {
    expect(en.thread.assistantWorkingFor).toBe("Working");
    expect(fr.thread.assistantWorkingFor).toBe("En cours");
    expect(en.thread.assistantWorkingFor).not.toContain("{{duration}}");
    expect(fr.thread.assistantWorkingFor).not.toContain("{{duration}}");
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

