import { describe, expect, it } from "vitest";
import { liveFramePoint, liveKeyInput, updateLiveSession } from "./agent-live";

describe("live screen input", () => {
  it("maps only actual screen pixels and drops letterbox bars and edges", () => {
    const rect = { left: 10, top: 20, width: 1000, height: 800 };
    expect(liveFramePoint(rect, 1000, 500, 510, 420)).toEqual({ x: 500, y: 250, width: 1000, height: 500 });
    expect(liveFramePoint(rect, 1000, 500, 510, 40)).toBeNull();
    expect(liveFramePoint(rect, 1000, 500, 1010, 420)).toBeNull();
    expect(liveFramePoint(rect, 0, 500, 510, 420)).toBeNull();
    expect(liveFramePoint(rect, 1000, 500, NaN, 420)).toBeNull();
  });

  it("keeps Shift for named navigation keys and types accented characters", () => {
    const base = { ctrlKey: false, altKey: false, metaKey: false, shiftKey: false };
    expect(liveKeyInput({ ...base, key: "Tab", shiftKey: true })).toEqual({ action: "key", payload: { key: "Shift+Tab" } });
    expect(liveKeyInput({ ...base, key: "ArrowLeft", shiftKey: true, ctrlKey: true })).toEqual({ action: "key", payload: { key: "Control+Shift+ArrowLeft" } });
    expect(liveKeyInput({ ...base, key: "é" })).toEqual({ action: "text", payload: { text: "é" } });
    expect(liveKeyInput({ ...base, key: "Dead" })).toBeNull();
    expect(liveKeyInput({ ...base, key: "x", isComposing: true })).toBeNull();
  });

  it("does not resurrect a closed session when a late frame arrives", () => {
    const start = { id: "desktop-1", chatId: "chat-1", phase: "start" as const };
    const opened = updateLiveSession(undefined, start);
    const human = updateLiveSession(opened, { ...start, phase: "action", userControl: true });
    expect(human.userControl).toBe(true);
    const closed = updateLiveSession(human, { ...start, phase: "exit" });
    expect(closed.userControl).toBe(false);
    expect(updateLiveSession(closed, { ...start, phase: "frame", data: "stale" })).toBe(closed);
  });
});
