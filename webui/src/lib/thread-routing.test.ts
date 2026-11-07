import { describe, expect, it } from "vitest";

import { autoSendTargetsChat, runtimeModelFrameApplies } from "./thread-routing";

describe("runtimeModelFrameApplies", () => {
  it("keeps another chat's model out of the chat on screen", () => {
    expect(runtimeModelFrameApplies({ chatId: "chat-b", reason: "route:code" }, "chat-a")).toBe(false);
    expect(runtimeModelFrameApplies({ chatId: "chat-b", reason: "budget" }, "chat-a")).toBe(false);
  });

  it("shows a routed or task model only to the chat that named it", () => {
    expect(runtimeModelFrameApplies({ chatId: "chat-a", reason: "route:code" }, "chat-a")).toBe(true);
    expect(runtimeModelFrameApplies({ chatId: "chat-a", reason: "task:review" }, "chat-a")).toBe(true);
    // Seen live: a broadcast frame put another chat's routed model in the bar.
    expect(runtimeModelFrameApplies({ chatId: "*", reason: "route:code" }, "chat-a")).toBe(false);
    expect(runtimeModelFrameApplies({ chatId: null, reason: "task:review" }, "chat-a")).toBe(false);
    expect(runtimeModelFrameApplies({ chatId: "chat-a", reason: "route:code" }, null)).toBe(false);
  });

  it("still lets a change for every chat through", () => {
    expect(runtimeModelFrameApplies({ chatId: "*", reason: "budget" }, "chat-a")).toBe(true);
    expect(runtimeModelFrameApplies({ chatId: null, reason: null }, "chat-a")).toBe(true);
    expect(runtimeModelFrameApplies(undefined, null)).toBe(true);
  });
});

describe("autoSendTargetsChat", () => {
  it("sends a bound request only in its own chat", () => {
    expect(autoSendTargetsChat("websocket:a", "websocket:a")).toBe(true);
    expect(autoSendTargetsChat("websocket:a", "websocket:b")).toBe(false);
    expect(autoSendTargetsChat("websocket:a", null)).toBe(false);
  });

  it("leaves unbound requests (project actions) as they were", () => {
    expect(autoSendTargetsChat(undefined, "websocket:b")).toBe(true);
    expect(autoSendTargetsChat(null, null)).toBe(true);
  });
});
