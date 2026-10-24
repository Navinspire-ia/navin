// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, renderHook } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NavinClient } from "@/lib/navin-client";
import type { InboundEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";
import { useNavinStream } from "./useNavinStream";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

function setup() {
  let receive: (event: InboundEvent) => void = () => {};
  const sendMessage = vi.fn();
  const client = {
    onError: () => () => {}, onStatus: () => () => {},
    onChat: (_id: string, handler: typeof receive) => { receive = handler; return () => {}; },
    getRunStartedAt: () => null, getGoalState: () => undefined,
    getPendingApprovals: () => [], getPendingChoices: () => [], sendMessage,
  } as unknown as NavinClient;
  const hook = renderHook(({ chatId }) => useNavinStream(chatId), {
    initialProps: { chatId: "test" },
    wrapper: ({ children }: PropsWithChildren) => <ClientProvider client={client} token="">{children}</ClientProvider>,
  });
  return { ...hook, sendMessage, event: (event: InboundEvent) => act(() => receive(event)) };
}
const frame = () => act(() => { vi.advanceTimersToNextFrame(); });

describe("stream delivery", () => {
  it("publishes the entire received burst on the next frame without a typewriter backlog", () => {
    const app = setup();
    const chunks = ["Bonjour. ", "Une réponse reçue. ".repeat(600), "Dernier fragment."];
    for (const text of chunks) app.event({ event: "delta", chat_id: "test", text });
    frame();
    expect(app.result.current.messages.map((message) => message.content).join("")).toBe(chunks.join(""));
    expect(app.result.current.isStreaming).toBe(true);
    const delivered = app.result.current.messages;
    frame();
    expect(app.result.current.messages).toBe(delivered);
  });

  it("preserves text and ordering across reasoning, tools, and completion before a frame", () => {
    const app = setup();
    app.event({ event: "delta", chat_id: "test", text: "Avant." });
    app.event({ event: "reasoning_delta", chat_id: "test", text: "Vérification complète." });
    app.event({ event: "reasoning_end", chat_id: "test" });
    app.event({ event: "message", chat_id: "test", kind: "progress", text: "Tests en cours." });
    app.event({ event: "delta", chat_id: "test", text: "Après." });
    app.event({ event: "stream_end", chat_id: "test" });
    app.event({ event: "turn_end", chat_id: "test" });
    expect(app.result.current.messages.filter((message) => message.role === "assistant").map((message) => message.content)).toEqual(["Avant.", "Après."]);
    expect(app.result.current.messages.some((message) => message.reasoning === "Vérification complète.")).toBe(true);
    expect(app.result.current.isStreaming).toBe(false);
    const completed = app.result.current.messages;
    frame();
    expect(app.result.current.messages).toBe(completed);
  });

  it("cancels queued text on stop and when changing conversations", () => {
    const app = setup();
    app.event({ event: "delta", chat_id: "test", text: "Arrêt." });
    act(() => app.result.current.stop());
    frame();
    expect(app.result.current.isStreaming).toBe(false);
    const stopped = app.result.current.messages;
    frame();
    expect(app.result.current.messages).toBe(stopped);
    app.event({ event: "turn_end", chat_id: "test" });
    app.event({ event: "delta", chat_id: "test", text: "Ancienne conversation." });
    app.rerender({ chatId: "next" });
    frame();
    expect(app.result.current.messages).toEqual([]);
  });
});

describe("mid-stream retry", () => {
  it("drops the abandoned partial segment so the answer shows once", () => {
    const app = setup();
    app.event({ event: "delta", chat_id: "test", text: "The ans", stream_id: "s:0" });
    frame();
    app.event({ event: "stream_end", chat_id: "test", stream_id: "s:0", discarded: true });
    app.event({ event: "delta", chat_id: "test", text: "The answer is 42.", stream_id: "s:1" });
    app.event({ event: "stream_end", chat_id: "test", stream_id: "s:1" });
    app.event({ event: "turn_end", chat_id: "test" });
    expect(app.result.current.messages.filter((m) => m.role === "assistant").map((m) => m.content))
      .toEqual(["The answer is 42."]);
  });

  it("drops partial text still queued for the next frame and keeps earlier segments", () => {
    const app = setup();
    app.event({ event: "delta", chat_id: "test", text: "Checking.", stream_id: "s:0" });
    app.event({ event: "stream_end", chat_id: "test", stream_id: "s:0" });
    app.event({ event: "message", chat_id: "test", kind: "progress", text: "Tests en cours." });
    app.event({ event: "delta", chat_id: "test", text: "Half", stream_id: "s:1" });
    app.event({ event: "stream_end", chat_id: "test", stream_id: "s:1", discarded: true });
    app.event({ event: "delta", chat_id: "test", text: "Done.", stream_id: "s:2" });
    app.event({ event: "stream_end", chat_id: "test", stream_id: "s:2" });
    app.event({ event: "turn_end", chat_id: "test" });
    expect(app.result.current.messages.filter((m) => m.role === "assistant").map((m) => m.content))
      .toEqual(["Checking.", "Done."]);
  });
});

describe("prompts from another window", () => {
  it("draws a guest's prompt once, then the reply below it", () => {
    const app = setup();
    app.event({ event: "user_message", chat_id: "test", content: "salut", turn_id: "t1", author: "Ana" });
    app.event({ event: "user_message", chat_id: "test", content: "salut", turn_id: "t1", author: "Ana" });
    expect(app.result.current.messages.filter((m) => m.role === "user").map((m) => m.content)).toEqual(["salut"]);
    expect(app.result.current.isStreaming).toBe(true);
    app.event({ event: "delta", chat_id: "test", text: "Bonjour !" });
    app.event({ event: "stream_end", chat_id: "test" });
    app.event({ event: "turn_end", chat_id: "test" });
    expect(app.result.current.messages.map((m) => [m.role, m.content])).toEqual([
      ["user", "salut"],
      ["assistant", "Bonjour !"],
    ]);
  });
});

describe("queued follow-up after tool progress", () => {
  const inbound = (event: Record<string, unknown>) => event as unknown as InboundEvent;

  it("ends the turn after plain tool hints so the next queued prompt is sent", () => {
    const app = setup();
    act(() => app.result.current.send("first"));
    expect(app.result.current.isStreaming).toBe(true);
    app.event(inbound({
      event: "message", chat_id: "test", kind: "progress", text: "",
      agent_ui: { kind: "task_progress", data: { label: "Compacting context…", phase: "compacting" } },
    }));
    expect(app.result.current.isCompacting).toBe(true);
    // A hint with no task progress used to throw (variable read before its
    // declaration), which broke every later event of the turn.
    app.event(inbound({ event: "message", chat_id: "test", kind: "tool_hint", text: "Running pytest" }));
    expect(app.result.current.isCompacting).toBe(false);
    app.event({ event: "turn_end", chat_id: "test" });
    expect(app.result.current.isStreaming).toBe(false);
    act(() => app.result.current.send("queued follow-up"));
    expect(app.sendMessage).toHaveBeenLastCalledWith("test", "queued follow-up", undefined, expect.anything());
    expect(app.result.current.isStreaming).toBe(true);
  });
});
