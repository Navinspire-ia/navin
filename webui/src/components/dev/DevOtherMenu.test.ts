// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const other = readFileSync(resolve(__dirname, "DevOtherMenu.tsx"), "utf8");
const dropdown = readFileSync(resolve(__dirname, "../ui/dropdown-menu.tsx"), "utf8");
const chrome = readFileSync(resolve(__dirname, "../../globals.css"), "utf8");

describe("Other menu scroll", () => {
  it("caps the panel to the viewport so Extensions stays reachable", () => {
    expect(other).toContain('maxHeight: "min(28rem, calc(100dvh - 5.5rem))"');
    expect(dropdown).toContain("100dvh-5.5rem");
    expect(dropdown).toContain("--radix-dropdown-menu-content-available-height,70dvh");
  });

  it("binds wheel on every dropdown so native-host can scroll without a bar", () => {
    expect(dropdown).toContain("bindMenuListWheel");
    expect(dropdown).toContain('data-menu-scroll=""');
  });

  it("keeps a visible scrollbar on Other under native-host chrome", () => {
    expect(chrome).toContain("html.native-host [data-menu-scroll]");
    expect(chrome).toContain("[data-menu-scroll]::-webkit-scrollbar");
    expect(chrome).toContain("html.native-host [data-panel-scroll]");
  });
});
