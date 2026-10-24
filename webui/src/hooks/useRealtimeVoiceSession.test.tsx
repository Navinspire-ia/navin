// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { NavinClient } from "@/lib/navin-client";
import { useRealtimeVoiceSession } from "./useRealtimeVoiceSession";

const mocks = vi.hoisted(() => ({
  capture: vi.fn(), close: vi.fn(),
  callbacks: null as null | { onError: (code: string, detail: string) => void },
}));
vi.mock("@/lib/realtime-live", () => ({
  liveInstructions: () => "Listen", realtimeBridgeUrl: () => "ws://local/live",
  RealtimeLiveSession: class {
    constructor(_options: unknown, callbacks: typeof mocks.callbacks) { mocks.callbacks = callbacks; }
    open = async () => ({ provider: "navin", model: "amazon/nova-2-sonic" });
    close = mocks.close;
  },
}));
vi.mock("@/lib/realtime-audio", () => ({
  startPcmCapture: mocks.capture,
  PcmPlayer: class { close() {} },
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it("releases a microphone granted after the provider has failed", async () => {
  let grant!: (stream: MediaStream) => void;
  const permission = new Promise<MediaStream>((resolve) => { grant = resolve; });
  const getUserMedia = vi.fn(() => permission);
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  const fallback = vi.fn();
  const trackStop = vi.fn();
  const hook = renderHook(() => useRealtimeVoiceSession({ client: { socketUrl: "ws://local", openSocket: vi.fn() } as unknown as NavinClient,
    token: "test", language: "fr", inputDeviceId: null, outputDeviceId: null,
    onTool: async () => "ok", onFallback: fallback, onError: vi.fn() }));
  let starting!: Promise<void>;
  await act(async () => { starting = hook.result.current.start(); });
  expect(getUserMedia).toHaveBeenCalledOnce();
  await act(async () => { mocks.callbacks!.onError("realtime_upstream_error", "Invalid tools"); });
  await act(async () => { grant({ getTracks: () => [{ stop: trackStop }] } as unknown as MediaStream); await starting; });
  expect(trackStop).toHaveBeenCalledOnce();
  expect(mocks.capture).not.toHaveBeenCalled();
  expect(fallback).toHaveBeenCalledExactlyOnceWith("realtime_upstream_error", "Invalid tools");
  expect(hook.result.current.state).toBe("idle");
});
