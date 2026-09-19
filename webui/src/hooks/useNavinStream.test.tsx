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
  const client = {
    onError: () => () => {}, onStatus: () => () => {},
    onChat: (_id: string, handler: typeof receive) => { receive = handler; return () => {}; },
    getRunStartedAt: () => null, getGoalState: () => undefined,
    getPendingApprovals: () => [], getPendingChoices: () => [], sendMessage: vi.fn(),
  } as unknown as NavinClient;
  const hook = renderHook(({ chatId }) => useNavinStream(chatId), {
    initialProps: { chatId: "test" },
    wrapper: ({ children }: PropsWithChildren) => <ClientProvider client={client} token="">{children}</ClientProvider>,
  });
  return { ...hook, event: (event: InboundEvent) => act(() => receive(event)) };
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
