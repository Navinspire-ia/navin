// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { createTerminalRecovery } from "./terminal-recovery";

// A terminal tab that has lost its shell hears about it several times over -
// once for the keystroke that failed, once per resize the fit addon fires - so
// these tests drive the clock by hand to pin down which of those reports turn
// into a re-open and which are swallowed.
function recovery(
  options: { quietMs?: number; maxAttempts?: number; settledMs?: number } = {},
) {
  let clock = 1000;
  const instance = createTerminalRecovery({ ...options, now: () => clock });
  return {
    instance,
    advance(ms: number) {
      clock += ms;
    },
  };
}

describe("createTerminalRecovery", () => {
  it("starts detached so a tab opened offline is not treated as a loss", () => {
    expect(recovery().instance.attached).toBe(false);
  });

  it("counts as attached once the gateway confirms a session", () => {
    const { instance } = recovery();
    instance.markAttached();
    expect(instance.attached).toBe(true);
  });

  it("re-opens on the first report that the shell is gone", () => {
    expect(recovery().instance.decide()).toBe("reopen");
  });

  it("swallows the reports that follow a re-open", () => {
    const { instance } = recovery({ quietMs: 4000 });
    expect(instance.decide()).toBe("reopen");
    expect(instance.decide()).toBe("wait");
    expect(instance.decide()).toBe("wait");
  });

  it("re-opens again once the quiet window has passed", () => {
    const { instance, advance } = recovery({ quietMs: 4000 });
    expect(instance.decide()).toBe("reopen");
    advance(4000);
    expect(instance.decide()).toBe("reopen");
  });

  it("stops re-opening after the attempt budget runs out", () => {
    const { instance, advance } = recovery({ quietMs: 10, maxAttempts: 2 });
    expect(instance.decide()).toBe("reopen");
    advance(10);
    expect(instance.decide()).toBe("reopen");
    advance(10);
    expect(instance.decide()).toBe("give-up");
  });

  it("keeps saying give-up rather than looping once it has given up", () => {
    const { instance, advance } = recovery({ quietMs: 10, maxAttempts: 1 });
    instance.decide();
    advance(10);
    expect(instance.decide()).toBe("give-up");
    advance(10);
    expect(instance.decide()).toBe("give-up");
  });

  it("hands a full budget to the next drop after a shell comes back and holds", () => {
    const { instance, advance } = recovery({ quietMs: 10, settledMs: 100, maxAttempts: 1 });
    expect(instance.decide()).toBe("reopen");
    instance.markAttached();
    advance(100);
    expect(instance.decide()).toBe("reopen");
    instance.markAttached();
    advance(100);
    expect(instance.decide()).toBe("reopen");
  });

  // A host that cannot keep a shell alive answers every re-open and then dies
  // again. Crediting each of those answers made the budget unreachable, and the
  // tab alternated "process exited" / "new shell started" without end.
  it("does not credit a shell that dies as soon as it opens", () => {
    const { instance, advance } = recovery({ quietMs: 10, settledMs: 1000, maxAttempts: 2 });
    expect(instance.decide()).toBe("reopen");
    instance.markAttached();
    advance(10);
    expect(instance.decide()).toBe("reopen");
    instance.markAttached();
    advance(10);
    expect(instance.decide()).toBe("give-up");
  });

  it("gives up in bounded time when every shell dies instantly", () => {
    const { instance, advance } = recovery({ quietMs: 10, settledMs: 1000, maxAttempts: 3 });
    let reopens = 0;
    for (let i = 0; i < 20; i += 1) {
      if (instance.decide() === "reopen") reopens += 1;
      instance.markAttached();
      advance(10);
    }
    expect(reopens).toBe(3);
  });

  describe("a shell that ended rather than went missing", () => {
    it("is not replaced by a new one", () => {
      const { instance, advance } = recovery({ quietMs: 10 });
      instance.markAttached();
      instance.markExited();
      advance(10);
      expect(instance.decide()).toBe("wait");
    });

    it("stays ended across a reconnect", () => {
      const { instance, advance } = recovery({ quietMs: 10 });
      instance.markAttached();
      instance.markExited();
      for (let i = 0; i < 5; i += 1) {
        advance(10);
        expect(instance.decide()).toBe("wait");
      }
    });

    it("does not block a tab the user opens again", () => {
      const { instance, advance } = recovery({ quietMs: 10 });
      instance.markAttached();
      instance.markExited();
      instance.markAttached();
      advance(10);
      expect(instance.decide()).toBe("reopen");
    });
  });
});
