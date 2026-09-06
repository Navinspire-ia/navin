import { describe, expect, it } from "vitest";

import {
  addNotification,
  dismissNotification,
  markAllRead,
  markRead,
  relativeTimeParts,
  toastable,
  unreadCount,
  type NotificationEntry,
  type NotificationInput,
} from "./notifications";

const T0 = 1_000_000;

const failure: NotificationInput = {
  level: "error",
  source: "request",
  title: "Accept all failed",
  detail: "HTTP 500",
};

function seed(inputs: NotificationInput[], now = T0): NotificationEntry[] {
  return inputs.reduce<NotificationEntry[]>((list, input) => addNotification(list, input, now), []);
}

describe("notification actions", () => {
  it("carries the action so the panel can offer it", () => {
    const run = () => {};
    const [entry] = seed([
      { level: "info", source: "update", title: "Navin 1.0.3 is available", action: { label: "Update", run } },
    ]);
    expect(entry.action?.label).toBe("Update");
    expect(entry.action?.run).toBe(run);
  });

  it("keeps the action when a repeat folds in without one", () => {
    // The update check re-runs and raises the same version again. Losing the
    // button on the second pass would leave a dead notification behind.
    const run = () => {};
    const withAction: NotificationInput = {
      level: "info",
      source: "update",
      title: "Navin 1.0.3 is available",
      key: "update:1.0.3",
      action: { label: "Update", run },
    };
    const list = addNotification(
      seed([withAction]),
      { ...withAction, action: undefined },
      T0 + 1_000,
    );
    expect(list).toHaveLength(1);
    expect(list[0].action?.run).toBe(run);
  });
});

describe("addNotification", () => {
  it("puts the newest entry first", () => {
    const list = seed([
      { level: "info", source: "board", title: "first" },
      { level: "info", source: "board", title: "second" },
    ]);
    expect(list.map((entry) => entry.title)).toEqual(["second", "first"]);
  });

  it("starts unread with a single occurrence", () => {
    const [entry] = seed([failure]);
    expect(entry.read).toBe(false);
    expect(entry.repeats).toBe(1);
  });

  it("folds an immediate repeat into the existing entry", () => {
    const once = addNotification([], failure, T0);
    const twice = addNotification(once, failure, T0 + 500);
    expect(twice).toHaveLength(1);
    expect(twice[0].repeats).toBe(2);
    expect(twice[0].firstSeenAt).toBe(T0);
    expect(twice[0].lastSeenAt).toBe(T0 + 500);
  });

  it("makes a folded repeat unread again so it is not missed", () => {
    const once = addNotification([], failure, T0);
    const seen = markAllRead(once);
    const again = addNotification(seen, failure, T0 + 500);
    expect(again[0].read).toBe(false);
  });

  it("keeps the freshest detail when folding", () => {
    const once = addNotification([], failure, T0);
    const again = addNotification(once, { ...failure, detail: "HTTP 503" }, T0 + 500);
    expect(again[0].detail).toBe("HTTP 503");
  });

  it("opens a new entry when the repeat comes long after", () => {
    const once = addNotification([], failure, T0);
    const later = addNotification(once, failure, T0 + 10 * 60_000);
    expect(later).toHaveLength(2);
    expect(later[0].repeats).toBe(1);
  });

  it("moves a folded entry back to the top", () => {
    let list = addNotification([], failure, T0);
    list = addNotification(list, { level: "info", source: "board", title: "task moved" }, T0 + 1);
    list = addNotification(list, failure, T0 + 2);
    expect(list[0].title).toBe("Accept all failed");
    expect(list).toHaveLength(2);
  });

  it("treats a different source as a different notification", () => {
    let list = addNotification([], failure, T0);
    list = addNotification(list, { ...failure, source: "agent" }, T0 + 1);
    expect(list).toHaveLength(2);
  });

  it("folds entries sharing an explicit key despite differing text", () => {
    let list = addNotification([], { ...failure, key: "review", title: "one" }, T0);
    list = addNotification(list, { ...failure, key: "review", title: "two" }, T0 + 1);
    expect(list).toHaveLength(1);
    expect(list[0].repeats).toBe(2);
  });

  it("drops the oldest once the cap is reached", () => {
    let list: NotificationEntry[] = [];
    for (let i = 0; i < 5; i += 1) {
      list = addNotification(list, { level: "info", source: "board", title: `t${i}` }, T0 + i, {
        max: 3,
      });
    }
    expect(list.map((entry) => entry.title)).toEqual(["t4", "t3", "t2"]);
  });
});

describe("read state", () => {
  it("counts only unread entries", () => {
    const list = seed([failure, { level: "info", source: "board", title: "task moved" }]);
    expect(unreadCount(list)).toBe(2);
    expect(unreadCount(markAllRead(list))).toBe(0);
  });

  it("marks a single entry read without touching the others", () => {
    const list = seed([failure, { level: "info", source: "board", title: "task moved" }]);
    const after = markRead(list, list[0].id);
    expect(after[0].read).toBe(true);
    expect(after[1].read).toBe(false);
  });

  it("leaves the list alone when marking an unknown id", () => {
    const list = seed([failure]);
    expect(markRead(list, "nope")).toEqual(list);
  });
});

describe("dismissNotification", () => {
  it("removes just the requested entry", () => {
    const list = seed([failure, { level: "info", source: "board", title: "task moved" }]);
    const after = dismissNotification(list, list[0].id);
    expect(after).toHaveLength(1);
    expect(after[0].title).toBe("Accept all failed");
  });
});

describe("relativeTimeParts", () => {
  it("calls the first minute 'just now'", () => {
    expect(relativeTimeParts(T0, T0 + 59_000)).toEqual({ key: "justNow", count: 0 });
  });

  it("switches to minutes at the minute mark", () => {
    expect(relativeTimeParts(T0, T0 + 60_000)).toEqual({ key: "minutesAgo", count: 1 });
  });

  it("switches to hours once minutes would round to sixty", () => {
    expect(relativeTimeParts(T0, T0 + 90 * 60_000)).toEqual({ key: "hoursAgo", count: 2 });
  });

  it("never reports a negative age when clocks disagree", () => {
    expect(relativeTimeParts(T0 + 5_000, T0)).toEqual({ key: "justNow", count: 0 });
  });
});

describe("toastable", () => {
  it("surfaces a fresh unread problem", () => {
    const list = addNotification([], failure, T0);
    expect(toastable(list, T0 + 100).map((entry) => entry.title)).toEqual(["Accept all failed"]);
  });

  it("stays quiet for routine information and success", () => {
    let list = addNotification([], { level: "info", source: "board", title: "task moved" }, T0);
    list = addNotification(list, { level: "success", source: "agent", title: "done" }, T0);
    expect(toastable(list, T0 + 100)).toEqual([]);
  });

  it("stops showing a problem the user has already seen", () => {
    const list = markAllRead(addNotification([], failure, T0));
    expect(toastable(list, T0 + 100)).toEqual([]);
  });

  it("lets an old problem fall out of the toast area", () => {
    const list = addNotification([], failure, T0);
    expect(toastable(list, T0 + 60_000)).toEqual([]);
  });

  it("surfaces a success explicitly flagged toast, such as a finished download", () => {
    const list = addNotification(
      [],
      {
        level: "success",
        source: "session",
        title: "report.pdf saved",
        detail: "/home/user/Documents/report.pdf",
        toast: true,
      },
      T0,
    );
    expect(toastable(list, T0 + 100).map((entry) => entry.title)).toEqual(["report.pdf saved"]);
    // Still ages out and respects read state like everything else.
    expect(toastable(list, T0 + 60_000)).toEqual([]);
    expect(toastable(markAllRead(list), T0 + 100)).toEqual([]);
  });
});
