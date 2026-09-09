// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { visibleUserChatText } from "./chat-visible-text";

const seed = (name: string, ask: string) =>
  `You are Navin, a teammate in General. Reply in this room as yourself: warm, concise, you can joke. You also review code, give opinions, and use tools when useful. Never mention another chat or a panel on the right.\nRecent messages in the room:\n(new conversation)\n\n${name} said:\n${ask}`;

describe("visibleUserChatText", () => {
  it("keeps a normal chat line untouched", () => {
    expect(visibleUserChatText("cc")).toBe("cc");
    expect(visibleUserChatText("You said hello")).toBe("You said hello");
  });

  it("shows only the line typed after a Team briefing", () => {
    expect(visibleUserChatText(seed("You", "cc"))).toBe("cc");
    expect(visibleUserChatText(seed("Aymen", "ship the linux build"))).toBe(
      "ship the linux build",
    );
  });

  it("keeps a multi-line ask", () => {
    expect(visibleUserChatText(seed("You", "line one\nline two"))).toBe("line one\nline two");
  });
});
