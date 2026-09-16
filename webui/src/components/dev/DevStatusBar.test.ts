// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { runtimeHealthTooltip } from "@/components/dev/DevStatusBar";
import type { RuntimeHealth } from "@/lib/api";

const t = (key: string, opts?: Record<string, unknown>) =>
  `${key}:${JSON.stringify(opts ?? {})}`;

describe("runtimeHealthTooltip", () => {
  it("appends the engine version so a stale attached gateway is visible", () => {
    const health: RuntimeHealth = {
      level: "ok",
      ok: true,
      pressure: false,
      memory: { usedRatio: 0.42 },
      disk: { usedRatio: 0.31 },
      engine: { version: "2.0.4", executable: "/opt/navin/navin-dist/navin" },
    };
    const title = runtimeHealthTooltip(health, t);
    expect(title).toContain('"version":"2.0.4"');
    expect(title).toContain("engineVersion");
  });

  it("omits the engine segment when the payload has no engine identity", () => {
    const health: RuntimeHealth = {
      level: "ok",
      ok: true,
      pressure: false,
      memory: { usedRatio: 0.42 },
      disk: { usedRatio: 0.31 },
    };
    const title = runtimeHealthTooltip(health, t);
    expect(title).not.toContain("engineVersion");
  });

  it("keeps a server-provided message and still names the engine", () => {
    const health: RuntimeHealth = {
      level: "warning",
      ok: false,
      pressure: true,
      message: "RAM pressure",
      engine: { version: "2.0.4" },
    };
    const title = runtimeHealthTooltip(health, t);
    expect(title.startsWith("RAM pressure - ")).toBe(true);
    expect(title).toContain("engineVersion");
  });
});
