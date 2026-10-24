// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useRailCounts } from "./useRailCounts";

const mock = vi.hoisted(() => ({
  fetchBoard: vi.fn(),
  fetchRules: vi.fn(),
  boardListeners: [] as Array<(project?: string | null) => void>,
  token: "token-1",
}));

vi.mock("@/lib/api", () => ({
  fetchBoard: mock.fetchBoard,
  fetchProjectRules: mock.fetchRules,
}));

const client = {
  onBoardUpdate: (listener: (project?: string | null) => void) => {
    mock.boardListeners.push(listener);
    return () => {
      mock.boardListeners = mock.boardListeners.filter((item) => item !== listener);
    };
  },
};

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({ client, token: mock.token }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mock.boardListeners = [];
  mock.token = "token-1";
  mock.fetchBoard.mockResolvedValue({ tasks: [] });
  mock.fetchRules.mockResolvedValue({ rules: [], total: 2 });
});
afterEach(cleanup);

describe("rail counters and the rotating API token", () => {
  it("does not reload on a token rotation, and later reads use the new token", async () => {
    const { result, rerender } = renderHook(() =>
      useRailCounts({ sessionKey: "websocket:chat", projectPath: "/p", enabled: true }),
    );
    await waitFor(() => expect(result.current.rules).toBe(2));
    expect(mock.fetchRules).toHaveBeenCalledTimes(1);
    expect(mock.fetchBoard).toHaveBeenCalledTimes(1);

    mock.token = "token-2";
    rerender();
    await act(async () => {});
    expect(mock.fetchRules).toHaveBeenCalledTimes(1);
    expect(mock.fetchBoard).toHaveBeenCalledTimes(1);

    await act(async () => {
      mock.boardListeners.forEach((listener) => listener("/p"));
    });
    expect(mock.fetchBoard).toHaveBeenCalledTimes(2);
    expect(mock.fetchBoard).toHaveBeenLastCalledWith("token-2", "websocket:chat");
  });

  it("still loads once the first token arrives", async () => {
    mock.token = "";
    const { rerender } = renderHook(() =>
      useRailCounts({ sessionKey: "websocket:chat", projectPath: "/p", enabled: true }),
    );
    await act(async () => {});
    expect(mock.fetchRules).not.toHaveBeenCalled();

    mock.token = "token-1";
    rerender();
    await waitFor(() => expect(mock.fetchRules).toHaveBeenCalledWith("token-1", "websocket:chat"));
  });
});
