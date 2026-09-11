// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

describe("First-run wizard layout", () => {
  it("keeps a dedicated scroll pane so step 2 is reachable in Tauri", () => {
    const wizard = readFileSync(join(here, "FirstRunWizard.tsx"), "utf8");
    expect(wizard).toContain("host-no-drag");
    expect(wizard).toContain("overflow-y-auto");
    expect(wizard).toContain("min-h-0");
    expect(wizard).toContain('data-testid="first-run-wizard-scroll"');
    expect(wizard).toContain("h-[min(92dvh,calc(100dvh-1.5rem))]");
  });
});
