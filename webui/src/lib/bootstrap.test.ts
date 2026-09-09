// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import { BootstrapAuthRequiredError, deriveRuntimeProfile, deriveWsUrl, fetchBootstrap } from "./bootstrap";
import { fetchWithTimeout } from "./http";

vi.mock("./http", () => ({
  fetchWithTimeout: vi.fn(),
}));

function stubWindow(port: string, hostname = "127.0.0.1", protocol = "http:") {
  vi.stubGlobal("window", {
    location: {
      port,
      hostname,
      protocol,
      host: port ? `${hostname}:${port}` : hostname,
    },
  });
}

describe("deriveWsUrl", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("routes through the Vite /__navin_ws proxy on the dev port", () => {
    // Regression: dev mode used to hit the gateway port directly, which
    // fails when the browser's host cannot reach it (Windows -> WSL2
    // localhost forwarding broken for that port). The Vite proxy always
    // works because the page itself was served from :5173.
    stubWindow("5173");
    const url = deriveWsUrl("/", "tok", "ws://127.0.0.1:8766/");
    expect(url).toBe("ws://127.0.0.1:5173/__navin_ws?token=tok");
    expect(deriveRuntimeProfile("ws://127.0.0.1:8766/").name).toBe("browser-vite");
  });

  it("keeps a non-root ws path behind the dev proxy", () => {
    stubWindow("5173");
    const url = deriveWsUrl("/ws", "tok", null);
    expect(url).toBe("ws://127.0.0.1:5173/__navin_ws/ws?token=tok");
  });

  it("prefers the server-provided ws_url outside dev", () => {
    stubWindow("8080");
    const url = deriveWsUrl("/", "tok", "ws://127.0.0.1:8766/?x=1");
    expect(url).toBe("ws://127.0.0.1:8766/?x=1&token=tok");
  });

  it("derives from window.location outside dev when no ws_url", () => {
    stubWindow("8766");
    const url = deriveWsUrl("/ws", "tok", undefined);
    expect(url).toBe("ws://127.0.0.1:8766/ws?token=tok");
  });

  it("uses wss for https pages", () => {
    stubWindow("", "example.test", "https:");
    const url = deriveWsUrl("/", "tok", undefined);
    expect(url).toBe("wss://example.test/?token=tok");
  });
});

describe("fetchBootstrap", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.mocked(fetchWithTimeout).mockReset();
  });

  it("retries a transient HTTP 500 then succeeds", async () => {
    vi.useFakeTimers();
    const ok = {
      ok: true,
      json: async () => ({
        token: "tok",
        api_token: "api",
        ws_path: "/",
      }),
    };
    vi.mocked(fetchWithTimeout)
      .mockResolvedValueOnce({ ok: false, status: 500 } as Response)
      .mockResolvedValueOnce(ok as Response);

    const pending = fetchBootstrap("");
    await vi.advanceTimersByTimeAsync(400);
    await expect(pending).resolves.toMatchObject({ token: "tok", api_token: "api" });
    expect(fetchWithTimeout).toHaveBeenCalledTimes(2);
  });

  it("does not retry an auth failure", async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValue({ ok: false, status: 401 } as Response);
    await expect(fetchBootstrap("")).rejects.toBeInstanceOf(BootstrapAuthRequiredError);
    expect(fetchWithTimeout).toHaveBeenCalledTimes(1);
  });
});
