// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { DESK_CHAT_COMMAND, applyDeskChatCommand } from "./desk-chat-command";

describe("desk chat command", () => {
  it("maps each Studio desk to its workflow command", () => {
    expect(DESK_CHAT_COMMAND.tenders).toBe("/tenders");
    expect(DESK_CHAT_COMMAND.career).toBe("/career");
    expect(DESK_CHAT_COMMAND.leads).toBe("/leads");
    expect(DESK_CHAT_COMMAND.scraping).toBe("/scrape");
    expect(DESK_CHAT_COMMAND.trading).toBe("/trading");
    expect(DESK_CHAT_COMMAND.marketing).toBe("/marketing");
    expect(DESK_CHAT_COMMAND.meeting).toBe("/meeting");
    expect(DESK_CHAT_COMMAND.notes).toBeUndefined();
  });

  it("hides Meeting and Notes chat until the header Chat button", () => {
    const app = readFileSync(resolve(__dirname, "../App.tsx"), "utf8");
    const offStart = app.indexOf("DESK_CHAT_OFF_BY_DEFAULT");
    const off = app.slice(offStart, app.indexOf("VIEW_BY_PRODUCT_MODULE", offStart));
    expect(off).toContain('"meeting"');
    expect(off).toContain('"notes"');
    expect(app).toContain("onToggleChat={onToggleDeskChat}");
    expect(app).toContain('deskView === "meeting"');
    expect(app).toContain('deskView === "notes"');
    expect(app).not.toContain("focusMode={workbenchFocus}");
  });

  it("seeds an empty composer with a trailing space", () => {
    expect(applyDeskChatCommand("", "/tenders")).toBe("/tenders ");
    expect(applyDeskChatCommand("   ", "/career")).toBe("/career ");
  });

  it("keeps a draft that already starts with the desk command", () => {
    expect(applyDeskChatCommand("/tenders", "/tenders")).toBe("/tenders ");
    expect(applyDeskChatCommand("/leads Find SaaS", "/leads")).toBe("/leads Find SaaS ");
    expect(applyDeskChatCommand("/career\nbrief", "/career")).toBe("/career\nbrief ");
  });

  it("swaps a lone other command, or only the leading token", () => {
    expect(applyDeskChatCommand("/career", "/tenders")).toBe("/tenders ");
    expect(applyDeskChatCommand("/career Find missions", "/tenders")).toBe("/tenders Find missions");
  });

  it("prefixes a free-text draft so the desk tools load", () => {
    expect(applyDeskChatCommand("hello", "/leads")).toBe("/leads hello");
  });
});
