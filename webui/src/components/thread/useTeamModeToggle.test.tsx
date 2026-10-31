// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useTeamModeToggle } from "./useTeamModeToggle";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("useTeamModeToggle", () => {
  it("moves at once and settles on the value the engine saved", async () => {
    const save = deferred<boolean>();
    const onSaved = vi.fn();
    const { result } = renderHook(() => useTeamModeToggle(() => save.promise, onSaved));
    expect(result.current.pending).toBeNull();

    act(() => result.current.toggle(false));
    // The switch shows "off" before any answer (the agent loop may be busy).
    expect(result.current.pending).toBe(false);
    await act(async () => { save.resolve(false); });
    expect(onSaved).toHaveBeenCalledWith(false);
    expect(result.current.pending).toBeNull();
    expect(result.current.failed).toBe(false);
  });

  it("goes back and reports a save that failed", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const save = deferred<boolean>();
    const onSaved = vi.fn();
    const { result } = renderHook(() => useTeamModeToggle(() => save.promise, onSaved));
    act(() => result.current.toggle(false));
    await act(async () => { save.reject(new Error("offline")); });
    expect(result.current.pending).toBeNull();
    expect(result.current.failed).toBe(true);
    expect(onSaved).not.toHaveBeenCalled();
    expect(error).toHaveBeenCalled();
    error.mockRestore();
  });

  it("saves quick switches in order and keeps the last one", async () => {
    const calls: Array<{ enabled: boolean; answer: ReturnType<typeof deferred<boolean>> }> = [];
    const save = (enabled: boolean) => {
      const answer = deferred<boolean>();
      calls.push({ enabled, answer });
      return answer.promise;
    };
    const onSaved = vi.fn();
    const { result } = renderHook(() => useTeamModeToggle(save, onSaved));

    act(() => {
      result.current.toggle(false);
      result.current.toggle(true);
    });
    expect(result.current.pending).toBe(true);
    // The second save waits for the first: the engine gets them in order.
    await act(async () => { await Promise.resolve(); });
    expect(calls.map((call) => call.enabled)).toEqual([false]);
    await act(async () => { calls[0].answer.resolve(false); });
    // The older answer does not bring "off" back while "on" is on its way.
    expect(result.current.pending).toBe(true);
    expect(onSaved).not.toHaveBeenCalled();
    expect(calls.map((call) => call.enabled)).toEqual([false, true]);
    await act(async () => { calls[1].answer.resolve(true); });
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onSaved).toHaveBeenCalledWith(true);
    expect(result.current.pending).toBeNull();
  });

  it("clears a past failure on the next switch", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    let fail = true;
    const save = vi.fn(async (enabled: boolean) => {
      if (fail) throw new Error("offline");
      return enabled;
    });
    const { result } = renderHook(() => useTeamModeToggle(save, vi.fn()));
    await act(async () => { result.current.toggle(true); });
    expect(result.current.failed).toBe(true);
    fail = false;
    act(() => result.current.toggle(true));
    expect(result.current.failed).toBe(false);
    await act(async () => { await Promise.resolve(); });
    expect(result.current.failed).toBe(false);
    expect(save).toHaveBeenCalledTimes(2);
    error.mockRestore();
  });
});
