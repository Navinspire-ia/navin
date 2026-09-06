import { afterEach, describe, expect, it, vi } from "vitest";

import {
  NavinClient,
  classifyWebSocketClose,
  reconnectDelayMs,
} from "./navin-client";

class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  serverClose(code: number): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

describe("WebSocket reconnect policy", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("classifies authentication, terminal and transient closes", () => {
    expect(classifyWebSocketClose(1008)).toBe("reauth");
    expect(classifyWebSocketClose(4001)).toBe("reauth");
    expect(classifyWebSocketClose(1009)).toBe("stop");
    expect(classifyWebSocketClose(1006)).toBe("retry");
  });

  it("applies bounded jitter to exponential backoff", () => {
    expect(reconnectDelayMs(0, 500, 15_000, () => 0)).toBe(400);
    expect(reconnectDelayMs(1, 500, 15_000, () => 0.5)).toBe(1000);
    expect(reconnectDelayMs(20, 500, 15_000, () => 1)).toBe(15_000);
  });

  it("reauthenticates and reattaches chats after an auth close", async () => {
    vi.useFakeTimers();
    const sockets: FakeSocket[] = [];
    const urls: string[] = [];
    const factory = (url: string) => {
      urls.push(url);
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    };
    const onReauth = vi.fn().mockResolvedValue("ws://host/ws?token=fresh");
    const client = new NavinClient({
      url: "ws://host/ws?token=old",
      socketFactory: factory,
      onReauth,
      random: () => 0.5,
    });

    client.attach("chat-1");
    client.connect();
    sockets[0].open();
    expect(sockets[0].sent).toContain(JSON.stringify({ type: "attach", chat_id: "chat-1" }));

    sockets[0].serverClose(1008);
    await vi.advanceTimersByTimeAsync(500);
    expect(onReauth).toHaveBeenCalledOnce();
    expect(urls).toEqual([
      "ws://host/ws?token=old",
      "ws://host/ws?token=fresh",
    ]);

    sockets[1].open();
    expect(sockets[1].sent).toContain(JSON.stringify({ type: "attach", chat_id: "chat-1" }));
  });

  it("does not reconnect after a terminal protocol close", async () => {
    vi.useFakeTimers();
    const socket = new FakeSocket();
    const factory = vi.fn(() => socket as unknown as WebSocket);
    const client = new NavinClient({
      url: "ws://host/ws",
      socketFactory: factory,
      random: () => 0.5,
    });

    client.connect();
    socket.open();
    socket.serverClose(1002);
    await vi.runAllTimersAsync();
    expect(client.status).toBe("closed");
    expect(factory).toHaveBeenCalledOnce();
  });
});

describe("bounded detached-chat buffering", () => {
  it("preserves lifecycle events and requests a canonical resync after overflow", async () => {
    const socket = new FakeSocket();
    const client = new NavinClient({
      url: "ws://host/ws",
      socketFactory: () => socket as unknown as WebSocket,
      reconnect: false,
    });
    const sessionUpdates: Array<[string, string | undefined]> = [];
    client.onSessionUpdate((chatId, scope) => {
      sessionUpdates.push([chatId, scope]);
    });
    client.connect();
    socket.open();

    socket.onmessage?.({
      data: JSON.stringify({ event: "turn_end", chat_id: "chat-overflow" }),
    } as MessageEvent);
    for (let index = 0; index < 2000; index += 1) {
      socket.onmessage?.({
        data: JSON.stringify({
          event: "stream_delta",
          chat_id: "chat-overflow",
          content: String(index),
          stream_id: "s1",
        }),
      } as MessageEvent);
    }

    const received: Array<{ event: string }> = [];
    client.onChat("chat-overflow", (event) => {
      received.push(event);
    });
    await Promise.resolve();

    expect(received).toHaveLength(2000);
    expect(received.some((event) => event.event === "turn_end")).toBe(true);
    expect(sessionUpdates).toContainEqual(["chat-overflow", "thread"]);
  });

  it("does not request a resync while the detached buffer stays within its cap", async () => {
    const socket = new FakeSocket();
    const client = new NavinClient({
      url: "ws://host/ws",
      socketFactory: () => socket as unknown as WebSocket,
      reconnect: false,
    });
    const sessionUpdate = vi.fn();
    client.onSessionUpdate(sessionUpdate);
    client.connect();
    socket.open();
    socket.onmessage?.({
      data: JSON.stringify({
        event: "stream_delta",
        chat_id: "chat-small",
        content: "hello",
        stream_id: "s1",
      }),
    } as MessageEvent);

    const received: Array<{ event: string }> = [];
    client.onChat("chat-small", (event) => received.push(event));
    await Promise.resolve();

    expect(received).toHaveLength(1);
    expect(sessionUpdate).not.toHaveBeenCalled();
  });
});
