// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({ fetchBoard: vi.fn() }));
vi.mock("@/lib/api", () => ({ fetchBoard: mock.fetchBoard }));

import { fetchBoardShared } from "./board-fetch";

beforeEach(() => {
  vi.useFakeTimers();
  mock.fetchBoard.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("fetchBoardShared", () => {
  it("shares one request between panels asking in the same tick", async () => {
    mock.fetchBoard.mockResolvedValue({ tasks: [1] });
    const a = fetchBoardShared("t", "k1");
    const b = fetchBoardShared("t", "k1");
    expect(mock.fetchBoard).toHaveBeenCalledTimes(1);
    expect(await a).toBe(await b);
  });

  it("never reuses a read started earlier, so a reload after a write asks again", async () => {
    let resolveFirst: (value: unknown) => void = () => undefined;
    mock.fetchBoard
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ tasks: ["fresh"] });
    const first = fetchBoardShared("t", "k2");
    vi.advanceTimersByTime(150);
    const second = fetchBoardShared("t", "k2");
    expect(mock.fetchBoard).toHaveBeenCalledTimes(2);
    resolveFirst({ tasks: ["stale"] });
    expect(await first).toEqual({ tasks: ["stale"] });
    expect(await second).toEqual({ tasks: ["fresh"] });
  });

  it("keeps boards apart and does not share a failure", async () => {
    mock.fetchBoard.mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ tasks: [] });
    await expect(fetchBoardShared("t", "k3")).rejects.toThrow("offline");
    await expect(fetchBoardShared("t", "k3")).resolves.toEqual({ tasks: [] });
    await fetchBoardShared("t", "k4");
    expect(mock.fetchBoard.mock.calls.map((call) => call[1])).toEqual(["k3", "k3", "k4"]);
  });
});
