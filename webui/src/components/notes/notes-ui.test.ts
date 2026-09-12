// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("notes header chrome", () => {
  it("uses a Fluent New button and a Chat toggle, not focus maximize", () => {
    const src = readFileSync(resolve(__dirname, "NotesWorkbench.tsx"), "utf8");
    expect(src).toContain('data-testid="notes-toggle-chat"');
    expect(src).toContain('data-testid="notes-new"');
    expect(src.indexOf('data-testid="notes-new"')).toBeLessThan(
      src.indexOf('data-testid="notes-toggle-chat"'),
    );
    expect(src.indexOf("ml-auto")).toBeGreaterThan(src.indexOf('data-testid="notes-new"'));
    expect(src.indexOf("ml-auto")).toBeLessThan(src.indexOf('data-testid="notes-toggle-chat"'));
    expect(src).toContain('t("notes.newAction"');
    expect(src).toContain("HEADER_BUTTON_STYLES");
    expect(src).toContain("PrimaryButton");
    expect(src).toContain("IconButton");
    expect(src).toContain('iconName: chatOpen ? "ChatSolid" : "Chat"');
    expect(src).not.toContain("onToggleFocus");
    expect(src).not.toContain("focusMode");
    expect(src).not.toContain("Maximize2");
    expect(src).not.toContain("\u2014");
    expect(src).not.toContain("\u2013");
  });
});
