// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Smooth infinite animations repaint every frame; with many parallel sessions
// they held the desktop shell at ~22 fps (issue #17). These rules keep the
// looping indicators stepped so each one repaints only a few times per cycle.
const css = readFileSync(resolve(__dirname, "../globals.css"), "utf8");

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const blocks = [...css.matchAll(new RegExp(`(?:^|[\\n,])\\s*${escaped}\\s*(?:,[^{]*)?\\{([^}]*)\\}`, "g"))];
  return blocks.map((m) => m[1]).join("\n");
}

describe("looping indicator animations stay stepped", () => {
  it("sidebar running dots use a discrete step-end loop", () => {
    expect(rule(".chat-run-dot")).toMatch(/animation:\s*chat-run-dot\s+1\.2s\s+step-end\s+infinite/);
  });

  it("spinners, ping and pulse tick instead of interpolating", () => {
    expect(rule(".animate-spin")).toMatch(/animation-timing-function:\s*steps\(\d+,\s*end\)/);
    expect(rule(".animate-ping")).toMatch(/animation-timing-function:\s*steps\(\d+,\s*end\)/);
  });

  it("run pulse icon is stepped", () => {
    expect(rule(".run-pulse-icon__ring")).toMatch(/steps\(\d+,\s*end\)\s+infinite/);
    expect(rule(".run-pulse-icon__dot")).toMatch(/steps\(\d+,\s*end\)\s+infinite/);
  });

  it("no smooth infinite animation is left on the running dots", () => {
    expect(css).not.toMatch(/chat-run-dot[^;{}]*ease-in-out[^;{}]*infinite/);
  });
});
