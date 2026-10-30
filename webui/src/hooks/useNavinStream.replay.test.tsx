// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

// Regression for issue #19: a turn that ran while the user was in another
// session must show every answer when the user comes back. Child cards
// (credential, worktree merge) subscribe in their own effect, which React runs
// before the parent's useNavinStream effect.

import { act, cleanup, render } from "@testing-library/react";
import { StrictMode, useEffect, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NavinClient } from "@/lib/navin-client";
import type { InboundEvent } from "@/lib/types";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import { useNavinStream } from "./useNavinStream";

class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  send(data: string): void { this.sent.push(data); }
  close(): void { this.readyState = 3; this.onclose?.({ code: 1000 }); }
  open(): void { this.readyState = 1; this.onopen?.(); }
}

const CHAT = "chat-back";

function ChildCard({ seen }: { seen: string[] }) {
  const { client } = useClient();
  useEffect(() => client.onChat(CHAT, (ev: InboundEvent) => { seen.push(ev.event); }), [client, seen]);
  return null;
}

type Snapshot = { contents: string[]; streaming: boolean };

function Thread({ snapshot, children }: { snapshot: Snapshot; children: ReactNode }) {
  const { messages, isStreaming } = useNavinStream(CHAT);
  snapshot.contents = messages.filter((m) => m.role === "assistant").map((m) => m.content);
  snapshot.streaming = isStreaming;
  return <>{children}</>;
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

function scenario(strict: boolean) {
  const socket = new FakeSocket();
  const client = new NavinClient({ url: "ws://host/ws", socketFactory: () => socket as unknown as WebSocket, reconnect: false });
  client.connect();
  socket.open();
  const send = (frame: Record<string, unknown>) =>
    socket.onmessage?.({ data: JSON.stringify({ chat_id: CHAT, ...frame }) } as MessageEvent);
  // The turn ran in the background: nobody is subscribed to this chat yet.
  send({ event: "delta", text: "First progress. ", stream_id: "s:1" });
  send({ event: "delta", text: "Final report.", stream_id: "s:1" });
  send({ event: "stream_end", stream_id: "s:1" });
  send({ event: "turn_end" });

  const cardSeen: string[] = [];
  const snapshot: Snapshot = { contents: [], streaming: true };
  const tree = (
    <ClientProvider client={client} token="">
      <Thread snapshot={snapshot}><ChildCard seen={cardSeen} /></Thread>
    </ClientProvider>
  );
  render(strict ? <StrictMode>{tree}</StrictMode> : tree);
  act(() => { vi.advanceTimersToNextFrame(); });
  act(() => { vi.advanceTimersByTime(1000); });
  return { cardSeen, snapshot };
}

describe("buffered background turn replay (issue #19)", () => {
  it("delivers every buffered frame to the thread and to the child card", () => {
    const { cardSeen, snapshot } = scenario(false);
    expect(snapshot.contents).toEqual(["First progress. Final report."]);
    expect(snapshot.streaming).toBe(false);
    expect(cardSeen).toEqual(["delta", "delta", "stream_end", "turn_end"]);
  });

  it("delivers every buffered frame under StrictMode remounts", () => {
    const { cardSeen, snapshot } = scenario(true);
    expect(snapshot.contents).toEqual(["First progress. Final report."]);
    expect(snapshot.streaming).toBe(false);
    // StrictMode mounts, unmounts and remounts the card in the same commit:
    // each live subscription gets the full replay, in order.
    expect(cardSeen).toEqual(["delta", "delta", "stream_end", "turn_end", "delta", "delta", "stream_end", "turn_end"]);
  });
});
