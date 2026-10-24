// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import { useNavinStream } from "@/hooks/useNavinStream";
import { NavinClient } from "@/lib/navin-client";
import type { ConnectionStatus, InboundEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";
import { TurnWorkingStatus } from "./TurnWorkingStatus";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-18T12:00:00Z"));
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: true, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

function setup(startedAt: number | null = null) {
  let chatEvent: (event: InboundEvent) => void = () => {};
  let connection: (status: ConnectionStatus) => void = () => {};
  let renders = 0;
  const sendMessage = vi.fn();
  const client = {
    onError: () => () => {},
    onStatus: (handler: typeof connection) => { connection = handler; return () => {}; },
    onChat: (_id: string, handler: typeof chatEvent) => { chatEvent = handler; return () => {}; },
    getRunStartedAt: () => startedAt,
    getGoalState: () => undefined,
    getPendingApprovals: () => [], getPendingChoices: () => [], sendMessage,
  } as unknown as NavinClient;
  function Chat() {
    renders += 1;
    const turn = useNavinStream("test");
    return <>
      <button onClick={() => turn.send("Continue")}>Start</button>
      {turn.isStreaming ? <button onClick={turn.stop}>Stop</button> : null}
      <TurnWorkingStatus active={turn.isStreaming} startedAt={turn.runStartedAt} activityText={turn.activityText} />
    </>;
  }
  render(<ClientProvider client={client} token=""><Chat /></ClientProvider>);
  return {
    event: (event: InboundEvent) => act(() => chatEvent(event)),
    status: (status: ConnectionStatus) => act(() => connection(status)),
    renders: () => renders, sendMessage,
  };
}
const tick = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

describe("persistent turn activity", () => {
  it("starts immediately and ticks through minutes without rerendering the chat", () => {
    const app = setup();
    expect(screen.queryByTestId("turn-working-status")).toBeNull();
    fireEvent.click(screen.getByText("Start"));
    expect(screen.getByRole("timer").textContent).toBe("0s");
    const renders = app.renders();
    tick(61_000);
    expect(screen.getByRole("timer").textContent).toBe("1m 1s");
    expect(screen.getByRole("status", { name: "Working" })).toBeTruthy();
    expect(app.renders()).toBe(renders);
  });

  it("stays visible between reasoning, tool work and a final answer until turn_end", () => {
    const app = setup();
    fireEvent.click(screen.getByText("Start"));
    app.event({ event: "reasoning_delta", chat_id: "test", text: "Checking" });
    tick(2000);
    app.event({ event: "stream_end", chat_id: "test" });
    tick(2000);
    expect(screen.getByRole("timer").textContent).toBe("4s");
    app.event({ event: "message", chat_id: "test", kind: "progress", text: "Running tests" });
    tick(2000);
    app.event({ event: "message", chat_id: "test", text: "Tests passed" });
    expect(screen.getByRole("timer").textContent).toBe("6s");
    app.event({ event: "turn_end", chat_id: "test" });
    expect(screen.queryByTestId("turn-working-status")).toBeNull();
  });

  it("keeps elapsed time during a disconnect and a live reconnect replay", () => {
    const started = Date.now() / 1000 - 65;
    const app = setup(started);
    expect(screen.getByText("Stop")).toBeTruthy();
    expect(screen.getByRole("timer").textContent).toBe("1m 5s");
    app.status("reconnecting");
    tick(20_000);
    expect(screen.getByRole("timer").textContent).toBe("1m 25s");
    app.status("open");
    app.event({ event: "goal_status", chat_id: "test", status: "running", started_at: started });
    tick(10_000);
    expect(screen.getByRole("timer").textContent).toBe("1m 35s");
  });

  it("ends immediately on Stop and resets for a new task", () => {
    const app = setup();
    fireEvent.click(screen.getByText("Start"));
    app.event({ event: "message", chat_id: "test", kind: "progress", text: "Checking files" });
    tick(5000);
    fireEvent.click(screen.getByText("Stop"));
    expect(screen.queryByTestId("turn-working-status")).toBeNull();
    expect(app.sendMessage).toHaveBeenLastCalledWith("test", "/stop");
    fireEvent.click(screen.getByText("Start"));
    expect(screen.getByRole("timer").textContent).toBe("0s");
    expect(screen.queryByTestId("turn-activity-text")).toBeNull();
  });

  it("replaces the short progress sentence without resetting time or exposing reasoning", () => {
    const app = setup();
    fireEvent.click(screen.getByText("Start"));
    app.event({ event: "message", chat_id: "test", kind: "progress", text: "Je verifie [les fichiers].\nPuis les tests." });
    tick(2000);
    expect(screen.getByTestId("turn-activity-text").textContent).toBe("Je verifie [les fichiers]. Puis les tests.");
    app.event({ event: "reasoning_delta", chat_id: "test", text: "Private thought" });
    app.event({ event: "message", chat_id: "test", kind: "progress", text: "Les tests passent." });
    expect(screen.getAllByTestId("turn-activity-text")).toHaveLength(1);
    expect(screen.getByTestId("turn-activity-text").textContent).toBe("Les tests passent.");
    expect(screen.getByRole("timer").textContent).toBe("2s");
    app.event({ event: "turn_end", chat_id: "test" });
    fireEvent.click(screen.getByText("Start"));
    expect(screen.queryByTestId("turn-activity-text")).toBeNull();
  });

  it("releases a stale run after a restarted gateway reports no live activity", () => {
    const app = setup(Date.now() / 1000 - 5);
    app.status("reconnecting");
    tick(1000);
    app.status("open");
    tick(9000);
    expect(screen.queryByTestId("turn-working-status")).toBeNull();
  });
});

describe("compacting the context", () => {
  it("says it beside Working instead of a silent spinner", () => {
    render(<TurnWorkingStatus active startedAt={null} activityText="Compacting context…" compacting />);
    expect(screen.getByRole("status", { name: "Working" })).toBeTruthy();
    expect(screen.getByTestId("turn-compacting").textContent).toBe("Compacting context…");
    expect(screen.queryByTestId("turn-activity-text")).toBeNull();
  });
});

describe("waiting on the user", () => {
  it("says it waits for an answer, without a spinner, and pauses the clock", () => {
    const started = Date.now() / 1000 - 10;
    const { rerender } = render(<TurnWorkingStatus active startedAt={started} activityText="Running tests" />);
    expect(screen.getByRole("timer").textContent).toBe("10s");
    rerender(<TurnWorkingStatus active startedAt={started} activityText="Running tests" waiting="choice" />);
    expect(screen.getByRole("status", { name: "Waiting for your answer" })).toBeTruthy();
    expect(screen.getByTestId("turn-waiting-icon")).toBeTruthy();
    expect(screen.queryByTestId("turn-activity-text")).toBeNull();
    expect(document.querySelector(".animate-spin")).toBeNull();
    tick(7 * 60_000);
    expect(screen.getByRole("timer").textContent).toBe("10s");
    rerender(<TurnWorkingStatus active startedAt={started} activityText="Running tests" />);
    expect(screen.getByRole("timer").textContent).toBe("10s");
    tick(3000);
    expect(screen.getByRole("timer").textContent).toBe("13s");
    expect(screen.getByTestId("turn-activity-text").textContent).toBe("Running tests");
  });

  it("says it waits for an approval and wins over thinking", () => {
    render(<TurnWorkingStatus active startedAt={null} thinking waiting="approval" />);
    expect(screen.getByRole("status", { name: "Waiting for your approval" })).toBeTruthy();
    tick(5000);
    expect(screen.getByRole("timer").textContent).toBe("0s");
  });

  it("forgets earlier pauses when a new turn starts", () => {
    const first = Date.now() / 1000 - 5;
    const { rerender } = render(<TurnWorkingStatus active startedAt={first} waiting="choice" />);
    tick(60_000);
    rerender(<TurnWorkingStatus active startedAt={first} />);
    expect(screen.getByRole("timer").textContent).toBe("5s");
    const second = Date.now() / 1000 - 2;
    rerender(<TurnWorkingStatus active startedAt={second} />);
    expect(screen.getByRole("timer").textContent).toBe("2s");
  });
});

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
  serverClose(code: number): void { this.readyState = 3; this.onclose?.({ code }); }
}

describe("switching chats while a turn runs", () => {
  function setupChats() {
    const socket = new FakeSocket();
    const client = new NavinClient({ url: "ws://host/ws", socketFactory: () => socket as unknown as WebSocket, reconnect: false });
    client.connect();
    socket.open();
    function Chat({ chatId }: { chatId: string }) {
      const turn = useNavinStream(chatId);
      return <TurnWorkingStatus active={turn.isStreaming} startedAt={turn.runStartedAt} activityText={turn.activityText} />;
    }
    const view = render(<ClientProvider client={client} token=""><Chat chatId="chat-a" /></ClientProvider>);
    return {
      socket,
      open: (chatId: string) => view.rerender(<ClientProvider client={client} token=""><Chat chatId={chatId} /></ClientProvider>),
      server: (event: object) => act(() => { socket.onmessage?.({ data: JSON.stringify(event) } as MessageEvent); }),
    };
  }

  it("keeps the server clock and live activity of a chat after A, B, then A again", () => {
    const app = setupChats();
    const started = Date.now() / 1000;
    app.server({ event: "goal_status", chat_id: "chat-a", status: "running", started_at: started });
    app.server({ event: "message", chat_id: "chat-a", kind: "progress", text: "Running tests" });
    tick(120_000);
    expect(screen.getByRole("timer").textContent).toBe("2m 0s");

    app.open("chat-b");
    expect(screen.queryByTestId("turn-working-status")).toBeNull();
    tick(30_000);
    app.server({ event: "message", chat_id: "chat-a", kind: "progress", text: "Building the app" });
    tick(5_000);

    app.open("chat-a");
    expect(screen.getByRole("timer").textContent).toBe("2m 35s");
    expect(screen.getByTestId("turn-activity-text").textContent).toBe("Building the app");
    // The server answers the re-attach with its replay, then keeps streaming.
    app.server({ event: "attached", chat_id: "chat-a" });
    app.server({ event: "goal_status", chat_id: "chat-a", status: "running", started_at: started });
    app.server({ event: "message", chat_id: "chat-a", kind: "progress", text: "Linting" });
    tick(5_000);
    expect(screen.getByRole("timer").textContent).toBe("2m 40s");
    expect(screen.getByTestId("turn-activity-text").textContent).toBe("Linting");
  });

  it.each(["overflow", "reconnect", "slice", "unknown-start"])(
    "keeps the clock across a chat switch with %s while away",
    (variant) => {
      const app = setupChats();
      const started = Date.now() / 1000;
      if (variant !== "unknown-start") {
        app.server({ event: "goal_status", chat_id: "chat-a", status: "running", started_at: started });
      }
      app.server({ event: "message", chat_id: "chat-a", kind: "progress", text: "Running tests" });
      tick(120_000);
      expect(screen.getByRole("timer").textContent).toBe("2m 0s");
      app.open("chat-b");
      if (variant === "overflow") {
        for (let i = 0; i < 2100; i += 1) app.server({ event: "delta", chat_id: "chat-a", text: "x" });
      }
      if (variant === "reconnect") {
        act(() => { app.socket.serverClose(1006); });
      }
      if (variant === "slice") {
        app.server({ event: "turn_end", chat_id: "chat-a" });
        app.server({ event: "goal_status", chat_id: "chat-a", status: "running", started_at: started });
      }
      app.server({ event: "message", chat_id: "chat-a", kind: "progress", text: "Building the app" });
      tick(30_000);
      app.open("chat-a");
      expect(screen.getByRole("timer").textContent).toBe("2m 30s");
      expect(screen.getByTestId("turn-activity-text").textContent).toBe("Building the app");
    },
  );
});
