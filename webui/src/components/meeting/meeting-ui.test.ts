// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("meeting header chrome", () => {
  it("uses Fluent header actions and a Chat toggle, not focus maximize", () => {
    const src = readFileSync(resolve(__dirname, "MeetingWorkbench.tsx"), "utf8");
    expect(src).toContain('data-testid="meeting-toggle-chat"');
    expect(src).toContain('data-testid="meeting-record"');
    expect(src).toContain('data-testid="meeting-import"');
    expect(src).toContain('data-testid="meeting-new"');
    expect(src.indexOf('data-testid="meeting-new"')).toBeLessThan(
      src.indexOf('data-testid="meeting-toggle-chat"'),
    );
    expect(src.indexOf("ml-auto")).toBeGreaterThan(src.indexOf('data-testid="meeting-new"'));
    expect(src.indexOf("ml-auto")).toBeLessThan(src.indexOf('data-testid="meeting-toggle-chat"'));
    expect(src).toContain('text={recording ? elapsedLabel : tx("meeting.record"');
    expect(src).toContain('tx("meeting.importAction", "Import")');
    expect(src).toContain('tx("meeting.newAction", "New")');
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
