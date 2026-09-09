// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchContextUsage, fetchSkills, saveWorkspaceFile } from "./api";
import {
  getGatewayPace,
  noteGatewayAnswered,
  noteGatewayStalled,
  resetGatewayPace,
  subscribeGatewayPace,
} from "./gateway-pace";
import {
  GatewayUnreachableError,
  RequestTimeoutError,
  fetchWithTimeout,
  isTimeoutError,
  isTransportError,
  isUnreachableError,
  transportMessage,
} from "./http";
import { isRetryableRead } from "./read-routes";

describe("transport errors", () => {
  it("are typed, named and worded for the person reading them", () => {
    const timeout = new RequestTimeoutError("/api/x", 20_000);
    expect(timeout.name).toBe("RequestTimeoutError");
    expect(timeout.timeoutMs).toBe(20_000);
    expect(timeout.message).toBe(transportMessage("timeout", { seconds: 20 }));
    expect(timeout.message).not.toMatch(/\d+ms/);
    expect(timeout.message).not.toMatch(/browser/i);

    const cause = new TypeError("Failed to fetch");
    const down = new GatewayUnreachableError("/api/x", cause);
    expect(down.name).toBe("GatewayUnreachableError");
    expect(down.cause).toBe(cause);
    expect(down.message).toBe(transportMessage("unreachable"));
  });

  it("are recognised by class, by name and by the historical wording", () => {
    expect(isTimeoutError(new RequestTimeoutError("/x", 1))).toBe(true);
    expect(isTimeoutError({ name: "RequestTimeoutError" })).toBe(true);
    expect(isTimeoutError(new Error("Request timed out after 20000ms"))).toBe(true);
    expect(isTimeoutError(new Error("Something else"))).toBe(false);

    expect(isUnreachableError(new GatewayUnreachableError("/x"))).toBe(true);
    expect(isUnreachableError(new TypeError("Failed to fetch"))).toBe(true);
    expect(isUnreachableError(new TypeError("NetworkError when attempting to fetch"))).toBe(true);
    expect(isUnreachableError(new TypeError("x is not a function"))).toBe(false);

    expect(isTransportError(new RequestTimeoutError("/x", 1))).toBe(true);
    expect(isTransportError(new GatewayUnreachableError("/x"))).toBe(true);
    expect(isTransportError(new Error("HTTP 500"))).toBe(false);
  });
});

describe("fetchWithTimeout", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("rejects with RequestTimeoutError and aborts the underlying fetch", async () => {
    let aborted = false;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        return new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("aborted", "AbortError"));
          });
        });
      }),
    );
    const pending = fetchWithTimeout("/api/slow", {}, 1_000);
    const outcome = pending.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(1_000);
    const error = await outcome;
    expect(error).toBeInstanceOf(RequestTimeoutError);
    expect((error as RequestTimeoutError).url).toBe("/api/slow");
    expect(aborted).toBe(true);
  });

  it("forwards the caller's abort signal to the request", async () => {
    let seen: AbortSignal | null | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        seen = init?.signal;
        return new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        });
      }),
    );
    const controller = new AbortController();
    const pending = fetchWithTimeout("/api/x", { signal: controller.signal }, 10_000);
    const outcome = pending.catch((error: unknown) => error);
    controller.abort();
    const error = await outcome;
    expect(seen?.aborted).toBe(true);
    expect((error as DOMException).name).toBe("AbortError");
  });
});

describe("isRetryableRead", () => {
  it("allows the listings and probes every panel loads on open", () => {
    expect(isRetryableRead("/api/webui/skills")).toBe(true);
    expect(isRetryableRead("http://127.0.0.1:8765/api/sessions")).toBe(true);
    expect(isRetryableRead("/api/sessions/agent%3Amain/context-usage")).toBe(true);
    expect(isRetryableRead("/api/sessions/agent%3Amain/file-tree?path=src")).toBe(true);
    expect(isRetryableRead("/api/sessions/agent%3Amain/crm?action=list")).toBe(true);
    expect(isRetryableRead("/api/webui/skills/my-skill")).toBe(true);
    expect(isRetryableRead("/api/webui/runtime/health")).toBe(true);
  });

  it("refuses anything that changes state, even though every call is a GET", () => {
    expect(isRetryableRead("/api/sessions/agent%3Amain/file-save?path=a.ts")).toBe(false);
    expect(isRetryableRead("/api/sessions/agent%3Amain/delete")).toBe(false);
    expect(isRetryableRead("/api/sessions/agent%3Amain/crm?action=upsert")).toBe(false);
    expect(isRetryableRead("/api/webui/skills/install")).toBe(false);
    expect(isRetryableRead("/api/webui/skills/my-skill/enable")).toBe(false);
    expect(isRetryableRead("/api/webui/automations/run")).toBe(false);
    expect(isRetryableRead("/api/unknown")).toBe(false);
  });
});

describe("gateway pace", () => {
  beforeEach(() => resetGatewayPace());

  it("starts a stall on the first failure, keeps its start time, and clears on an answer", () => {
    const seen: number[] = [];
    subscribeGatewayPace(() => seen.push(getGatewayPace().failures));

    noteGatewayStalled("timeout", 1_000);
    noteGatewayStalled("unreachable", 5_000);
    expect(getGatewayPace()).toEqual({ stalledSince: 1_000, stall: "unreachable", failures: 2 });

    noteGatewayAnswered();
    expect(getGatewayPace()).toEqual({ stalledSince: null, stall: null, failures: 0 });
    expect(seen).toEqual([1, 2, 0]);

    // A healthy engine answering again is not an event.
    noteGatewayAnswered();
    expect(seen).toEqual([1, 2, 0]);
  });
});

describe("api reads ride out a stalled engine", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetGatewayPace();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

  it("retries an allow-listed read after a refused connection and reports the stall meanwhile", async () => {
    const calls: string[] = [];
    let attempt = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        calls.push(String(input));
        attempt += 1;
        if (attempt === 1) throw new TypeError("Failed to fetch");
        return json({ skills: [] });
      }),
    );
    const paces: Array<number | null> = [];
    subscribeGatewayPace(() => paces.push(getGatewayPace().stalledSince));

    const pending = fetchSkills("tok");
    await vi.advanceTimersByTimeAsync(400);
    await expect(pending).resolves.toEqual({ skills: [] });
    expect(calls).toHaveLength(2);
    expect(calls[0]).toBe("/api/webui/skills");
    // Stalled once, then healthy again on the answer.
    expect(paces[0]).not.toBeNull();
    expect(getGatewayPace().stalledSince).toBeNull();
  });

  it("grows the deadline on each attempt and gives up after the allowed retries", async () => {
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        attempts += 1;
        return new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        });
      }),
    );
    const outcome = fetchContextUsage("tok", "agent:main").catch((error: unknown) => error);
    // 20 s, then 30 s, then 45 s, plus the pauses in between.
    await vi.advanceTimersByTimeAsync(20_000 + 400 + 30_000 + 1_200 + 45_000 + 10);
    const error = await outcome;
    expect(error).toBeInstanceOf(RequestTimeoutError);
    expect(attempts).toBe(3);
    expect(getGatewayPace().failures).toBe(3);
    expect(getGatewayPace().stall).toBe("timeout");
  });

  it("never retries a mutation, so a save that only looked lost is not replayed", async () => {
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        attempts += 1;
        throw new TypeError("Failed to fetch");
      }),
    );
    const outcome = saveWorkspaceFile("tok", "agent:main", "a.ts", "x").catch(
      (error: unknown) => error,
    );
    await vi.advanceTimersByTimeAsync(5_000);
    const error = await outcome;
    expect(error).toBeInstanceOf(GatewayUnreachableError);
    expect((error as Error).message).toBe(transportMessage("unreachable"));
    expect(attempts).toBe(1);
  });
});
