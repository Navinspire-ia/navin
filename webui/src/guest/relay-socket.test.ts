// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import { JOIN_STAGE_PERCENT, RelaySession, snapshotThreadSignature, type JoinStage, type ShareSnapshot } from "./relay-socket";
import { ShareCipher } from "./share-crypto";

const KEY = "93-SN-eN7t8ig1_BYkfDQPZc87gAuLIEW8FvzMcl958";
const SHARE = "abcdefghijklmnop1234";

class FakeWebSocket {
  static OPEN = 1;
  static last: FakeWebSocket | null = null;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.last = this;
  }

  send(text: string): void {
    this.sent.push(text);
  }

  close(): void {}

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(frame: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

// The relay decrypts off the main task with WebCrypto, so a fixed delay is a
// race on a loaded machine (a 1.7 MB frame took 600 ms under CI-like load).
// Wait on the state the assertion needs, with a generous ceiling.
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
async function settleUntil(ready: () => boolean, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!ready() && Date.now() < deadline) await settle();
}

describe("RelaySocket join stages", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("carries agent inspection over the encrypted session envelope", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const cipher = await ShareCipher.create(KEY, SHARE);
    const session = new RelaySession("wss://relay.test/s", cipher, "Ana", {
      onSnapshot: () => {}, onPayload: () => {}, onHostOnline: () => {}, onClosed: () => {},
    });
    const socket = session.socketFactory();
    const ws = FakeWebSocket.last!;
    ws.open();
    socket.send(JSON.stringify({ type: "subagent_history", chat_id: "shared", request_id: "r1", task_id: "worker", after: 100 }));
    await settleUntil(() => ws.sent.length > 0);
    const sealed = JSON.parse(ws.sent.at(-1)!);
    expect(await cipher.open(sealed.data)).toEqual({ type: "envelope", envelope: { type: "subagent_history", chat_id: "shared", request_id: "r1", task_id: "worker", after: 100 } });
  });

  it("reassembles complete large history events before delivering them to the chat client", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const cipher = await ShareCipher.create(KEY, SHARE);
    const session = new RelaySession("wss://relay.test/s", cipher, "Ana", { onSnapshot: () => {}, onPayload: () => {}, onHostOnline: () => {}, onClosed: () => {} });
    const socket = session.socketFactory();
    const ws = FakeWebSocket.last!;
    ws.open();
    ws.receive({ type: "data", data: await cipher.seal({ type: "snapshot", chat_id: "c1", rights: { drive: false, download: false } }) });
    await settleUntil(() => socket.readyState === FakeWebSocket.OPEN);
    const received: string[] = [];
    socket.onmessage = (event) => received.push(String(event.data));
    const frame = JSON.stringify({ event: "subagent_query_result", chat_id: "c1", request_id: "r1", result: { events: [{ payload: { result: "Full tool output ".repeat(100000) } }] } });
    const parts = [frame.slice(0, 700000), frame.slice(700000)];
    for (let index = 0; index < parts.length; index++) {
      ws.receive({ type: "data", data: await cipher.seal({ type: "event_part", id: "large", index, total: parts.length, frame_part: parts[index] }) });
    }
    await settleUntil(() => received.length > 0);
    expect(received).toEqual([frame]);
  });

  it("puts a snapshot sent in parts back together and opens the chat", async () => {
    // A page whose newest message alone is over one relay frame: the host
    // used to drop the snapshot, the guest stayed on "Loading" for good.
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const cipher = await ShareCipher.create(KEY, SHARE);
    const snapshots: unknown[] = [];
    const session = new RelaySession("wss://relay.test/s", cipher, "Ana", {
      onSnapshot: (snap) => snapshots.push(snap), onPayload: () => {}, onHostOnline: () => {}, onClosed: () => {},
    });
    const socket = session.socketFactory();
    const ws = FakeWebSocket.last!;
    ws.open();
    const snapshot = {
      type: "snapshot", chat_id: "c1", rights: { drive: false, download: false },
      thread: { messages: [{ role: "assistant", content: "Rapport " + "é".repeat(300000) }] },
    };
    const raw = JSON.stringify(snapshot);
    const parts = [raw.slice(0, 100000), raw.slice(100000, 200000), raw.slice(200000)];
    for (let index = 0; index < parts.length; index++) {
      ws.receive({ type: "data", data: await cipher.seal({ type: "payload_part", id: "p1", index, total: parts.length, part: parts[index] }) });
    }
    await settleUntil(() => socket.readyState === FakeWebSocket.OPEN);
    expect(snapshots).toEqual([snapshot]);

    // A broken sequence is dropped, never half delivered.
    ws.receive({ type: "data", data: await cipher.seal({ type: "payload_part", id: "p2", index: 1, total: 2, part: "{}" }) });
    await settle();
    expect(snapshots).toHaveLength(1);
  });

  it("tells a changed conversation from the same one sent again", () => {
    // Back on its seat, the page gets a snapshot again: it reopens the chat
    // only when something was said meanwhile (ids differ on every page).
    const snap = (messages: { id: string; role: string; content: string }[], pending = false): ShareSnapshot => ({
      chat_id: "c1", rights: { drive: false, download: false }, expires_at: 0,
      thread: { messages, has_pending_tool_calls: pending, page: { user_message_offset: 3 } },
    });
    const before = snap([{ id: "a-1", role: "user", content: "bonjour" }, { id: "a-2", role: "assistant", content: "salut" }]);
    const resent = snap([{ id: "b-1", role: "user", content: "bonjour" }, { id: "b-2", role: "assistant", content: "salut" }]);
    expect(snapshotThreadSignature(resent)).toBe(snapshotThreadSignature(before));
    const grown = snap([...(before.thread as { messages: { id: string; role: string; content: string }[] }).messages, { id: "b-3", role: "assistant", content: "suite" }]);
    expect(snapshotThreadSignature(grown)).not.toBe(snapshotThreadSignature(before));
    const longer = snap([{ id: "c-1", role: "user", content: "bonjour" }, { id: "c-2", role: "assistant", content: "salut, voici la suite" }]);
    expect(snapshotThreadSignature(longer)).not.toBe(snapshotThreadSignature(before));
    expect(snapshotThreadSignature(snap([{ id: "d", role: "user", content: "bonjour" }], true))).not.toBe(
      snapshotThreadSignature(snap([{ id: "e", role: "user", content: "bonjour" }])),
    );
  });

  it("reports each real step up to the snapshot", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const cipher = await ShareCipher.create(KEY, SHARE);
    const stages: JoinStage[] = [];
    const session = new RelaySession("wss://relay.test/s", cipher, "Ana", {
      onSnapshot: () => {},
      onPayload: () => {},
      onHostOnline: () => {},
      onClosed: () => {},
      onJoinStage: (stage) => stages.push(stage),
    });
    session.socketFactory();
    const ws = FakeWebSocket.last!;

    ws.open();
    ws.receive({ type: "welcome", host_online: true });
    await settleUntil(() => stages.includes("hello"));
    expect(stages).toEqual(["socket", "relay", "hello"]);

    const snapshot = await cipher.seal({
      type: "snapshot",
      chat_id: "c1",
      rights: { drive: false, download: false },
    });
    ws.receive({ type: "data", data: snapshot });
    await settleUntil(() => stages.includes("ready"));
    expect(stages).toEqual(["socket", "relay", "hello", "data", "ready"]);
    expect(stages.map((stage) => JOIN_STAGE_PERCENT[stage])).toEqual([20, 40, 60, 80, 100]);
  });

  it("keeps the seat when the relay has no room yet (host reconnecting) and only ends on a final close", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const cipher = await ShareCipher.create(KEY, SHARE);
    const online: boolean[] = [];
    const closed: string[] = [];
    const session = new RelaySession("wss://relay.test/s", cipher, "Ana", {
      onSnapshot: () => {}, onPayload: () => {}, onHostOnline: (value) => online.push(value), onClosed: (reason) => closed.push(reason),
    });
    session.socketFactory();
    FakeWebSocket.last!.receive({ type: "closed", reason: "the host is not connected", retry: true });
    await settleUntil(() => online.length > 0);
    expect(online).toEqual([false]);
    expect(closed).toEqual([]);
    expect(session.closedReason).toBeNull();
    expect(() => session.socketFactory()).not.toThrow();

    FakeWebSocket.last!.receive({ type: "closed", reason: "this link is not active" });
    await settleUntil(() => closed.length > 0);
    expect(closed).toEqual(["this link is not active"]);
    expect(() => session.socketFactory()).toThrow("this link is not active");
  });

  it("takes its seat back on reconnection and treats a full room as a retry, not an end", async () => {
    // A phone changing network used to dial as a brand-new guest while its
    // dead socket still held a seat: a few retries filled a 5-seat room and
    // the page showed "This share has ended: this session is full".
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const cipher = await ShareCipher.create(KEY, SHARE);
    const full: boolean[] = [];
    const closed: string[] = [];
    const session = new RelaySession("wss://relay.test/guest/abc", cipher, "Ana", {
      onSnapshot: () => {}, onPayload: () => {}, onHostOnline: () => {}, onClosed: (reason) => closed.push(reason),
      onRoomFull: (value) => full.push(value),
    });
    session.socketFactory();
    expect(FakeWebSocket.last!.url).toBe("wss://relay.test/guest/abc");
    FakeWebSocket.last!.open();
    FakeWebSocket.last!.receive({ type: "welcome", guest: "seat-42", host_online: true });
    await settleUntil(() => session.seat === "seat-42");
    expect(full).toEqual([false]);

    // The next dial names the seat; a full room is reported, never final.
    session.socketFactory();
    expect(FakeWebSocket.last!.url).toBe("wss://relay.test/guest/abc?seat=seat-42");
    FakeWebSocket.last!.receive({ type: "closed", reason: "this session is full", retry: true, full: true });
    await settleUntil(() => full.length > 1);
    expect(full).toEqual([false, true]);
    expect(closed).toEqual([]);
    expect(session.closedReason).toBeNull();
    expect(() => session.socketFactory()).not.toThrow();
  });
});
