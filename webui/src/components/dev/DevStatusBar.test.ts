// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { runtimeHealthTooltip, WslRestartChip } from "@/components/dev/DevStatusBar";
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

  it("ignores outdated restart advice and marks unmeasured resources unknown", () => {
    const health: RuntimeHealth = {
      level: "warning",
      ok: false,
      pressure: true,
      message: "High memory use - reload recommended",
      engine: { version: "2.0.4" },
    };
    const title = runtimeHealthTooltip(health, t);
    expect(title).not.toContain("reload recommended");
    expect(title).toContain("runtime.unavailable");
    expect(title).not.toContain("0%");
    expect(title).toContain("engineVersion");
  });
});

describe("WslRestartChip", () => {
  it("renders nothing while WSL answers", () => {
    expect(renderToStaticMarkup(createElement(WslRestartChip, { notice: null }))).toBe("");
  });

  it("names the distribution and offers the command that clears it", () => {
    const html = renderToStaticMarkup(
      createElement(WslRestartChip, {
        notice: {
          distro: "Ubuntu",
          state: "hung",
          sinceSeconds: 63,
          detail: "wsl.exe gave no answer in 20 s",
          command: "wsl --shutdown",
        },
      }),
    );
    expect(html).toContain('data-testid="wsl-restart-chip"');
    expect(html).toContain('data-distro="Ubuntu"');
    expect(html).toContain('role="alert"');
    expect(html).toContain("<button");
    expect(html).toContain("wsl --shutdown");
    expect(html).toContain("wsl.exe gave no answer in 20 s");
  });
});
