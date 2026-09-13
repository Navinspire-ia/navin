import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ViteDevServer } from "vite";
import { afterEach, describe, expect, it, vi } from "vitest";

import { gatewayTarget, recoverOutdatedOptimizeDep } from "./vite.config";

describe("gatewayTarget", () => {
  it("uses connectable IPv4 loopback for an IPv4 wildcard bind", () => {
    expect(gatewayTarget("0.0.0.0", 8765)).toBe("http://127.0.0.1:8765");
  });

  it("uses bracketed IPv6 loopback for an IPv6 wildcard bind", () => {
    expect(gatewayTarget("::", 8765)).toBe("http://[::1]:8765");
    expect(gatewayTarget("[2001:db8::1]", 8765)).toBe("http://[2001:db8::1]:8765");
  });
});

describe("outdated dependency recovery", () => {
  const cacheDirs: string[] = [];

  afterEach(() => {
    vi.restoreAllMocks();
    for (const dir of cacheDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
  });

  function setup(cachePresent: boolean) {
    const cacheDir = fs.mkdtempSync(path.join(os.tmpdir(), "navin-vite-recovery-"));
    cacheDirs.push(cacheDir);
    if (cachePresent) {
      fs.mkdirSync(path.join(cacheDir, "deps"));
      fs.writeFileSync(path.join(cacheDir, "deps", "_metadata.json"), "{}");
    }
    const use = vi.fn();
    const server = {
      middlewares: { use },
      config: { cacheDir, logger: { warn: vi.fn(), error: vi.fn() } },
      ws: { send: vi.fn() },
      restart: vi.fn().mockResolvedValue(undefined),
    };
    const configure = recoverOutdatedOptimizeDep().configureServer;
    if (typeof configure !== "function") throw new Error("Missing middleware setup");
    configure.call({} as never, server as unknown as ViteDevServer);
    const finish = (
      url = "/node_modules/.vite/deps/@tiptap_react.js?v=old",
      statusCode = 504,
      statusMessage = "Outdated Optimize Dep",
    ) => {
      const res = Object.assign(new EventEmitter(), { statusCode, statusMessage });
      const next = vi.fn();
      use.mock.calls[0][0]({ url }, res, next);
      expect(next).toHaveBeenCalledOnce();
      res.emit("finish");
    };
    return { server, finish };
  }

  it("rebuilds a missing cache once for concurrent failed module requests", () => {
    const { server, finish } = setup(false);
    finish();
    finish("/node_modules/.vite/deps/@fluentui_react.js?v=old");
    expect(server.restart).toHaveBeenCalledExactlyOnceWith(true);
    expect(server.ws.send).not.toHaveBeenCalled();
  });

  it("reloads stale URLs without restarting a healthy optimizer, with throttling", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000);
    const { server, finish } = setup(true);
    finish();
    finish();
    expect(server.ws.send).toHaveBeenCalledExactlyOnceWith({ type: "full-reload", path: "*" });
    now.mockReturnValue(16_000);
    finish();
    expect(server.ws.send).toHaveBeenCalledTimes(2);
    expect(server.restart).not.toHaveBeenCalled();
  });

  it("ignores successful loads, other failures and API timeouts", () => {
    const { server, finish } = setup(false);
    finish("/node_modules/.vite/deps/react.js", 200, "OK");
    finish("/node_modules/.vite/deps/react.js", 500, "Internal Server Error");
    finish("/node_modules/.vite/deps/react.js", 504, "Gateway Timeout");
    finish("/api/leads", 504);
    expect(server.restart).not.toHaveBeenCalled();
    expect(server.ws.send).not.toHaveBeenCalled();
  });

  it("reports restart failures and allows a later recovery attempt", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000);
    const { server, finish } = setup(false);
    server.restart.mockRejectedValueOnce(new Error("optimizer unavailable"));
    finish();
    await vi.waitFor(() => expect(server.config.logger.error).toHaveBeenCalledOnce());
    now.mockReturnValue(16_000);
    finish();
    expect(server.restart).toHaveBeenCalledTimes(2);
  });
});
