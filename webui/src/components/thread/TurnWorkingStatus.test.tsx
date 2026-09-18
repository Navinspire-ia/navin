// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import { useNavinStream } from "@/hooks/useNavinStream";
import type { NavinClient } from "@/lib/navin-client";
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
