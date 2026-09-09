// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api-error";
import type { DaemonStatus } from "@/lib/evolve-api";

import { classifyEvolveFailure, daemonAvailability } from "./evolveStatus";

function daemon(partial: Partial<DaemonStatus>): DaemonStatus {
  return { online: false, status: null, ...partial };
}

describe("daemonAvailability", () => {
  it("reports online when the gateway reached the daemon", () => {
    expect(daemonAvailability(daemon({ online: true, supported: true, reason: null })))
      .toEqual({ state: "online" });
  });

  it("separates a host that cannot host a daemon from one that just has none", () => {
    expect(daemonAvailability(daemon({ supported: false, reason: "unsupported" })))
      .toEqual({ state: "unsupported" });
    expect(daemonAvailability(daemon({ supported: true, reason: "not_running" })))
      .toEqual({ state: "offline" });
  });

  it("keeps a badly answering daemon distinct from an absent one", () => {
    expect(daemonAvailability(daemon({ supported: true, reason: "unreachable" })))
      .toEqual({ state: "unreachable" });
  });

  it("treats a gateway that predates the reason payload as merely stopped", () => {
    expect(daemonAvailability(daemon({}))).toEqual({ state: "offline" });
    expect(daemonAvailability(null)).toEqual({ state: "offline" });
  });
});

describe("classifyEvolveFailure", () => {
  it("recognises the websockets boilerplate and keeps it out of the detail", () => {
    const failure = classifyEvolveFailure(
      new ApiError(
        500,
        "Failed to open a WebSocket connection.\nSee server log for more information.",
      ),
    );
    expect(failure.kind).toBe("server");
    expect(failure.detail).toBe("");
  });

  it("keeps a server message that actually says something", () => {
    const failure = classifyEvolveFailure(new ApiError(503, "engine daemon not reachable"));
    expect(failure.kind).toBe("server");
    expect(failure.detail).toBe("engine daemon not reachable");
  });

  it("classifies auth, missing project, timeout and offline gateway", () => {
    expect(classifyEvolveFailure(new ApiError(401, "Unauthorized")).kind).toBe("unauthorized");
    expect(classifyEvolveFailure(new ApiError(404, "no such directory: /x")).kind)
      .toBe("notFound");
    expect(classifyEvolveFailure(new Error("Request timed out after 20000ms")).kind)
      .toBe("timeout");
    expect(classifyEvolveFailure(new TypeError("Failed to fetch")).kind).toBe("network");
  });

  it("falls back to unknown, and never throws on odd inputs", () => {
    expect(classifyEvolveFailure("boom")).toEqual({ kind: "unknown", detail: "boom" });
    expect(classifyEvolveFailure(null)).toEqual({ kind: "unknown", detail: "" });
    expect(classifyEvolveFailure(undefined)).toEqual({ kind: "unknown", detail: "" });
  });
});
