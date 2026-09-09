// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Older Team-room turns stored a hidden briefing plus the typed line.
 * The bubble still shows only what the person typed.
 */

const TEAM_SEED_PREFIX = "You are Navin, a teammate in ";
const SAID_LINE = /\n[^\n]+ said:\n/;

export function visibleUserChatText(content: string): string {
  const text = (content ?? "").replace(/\r\n/g, "\n");
  if (!text.trimStart().startsWith(TEAM_SEED_PREFIX)) return content ?? "";
  const match = SAID_LINE.exec(text);
  if (!match) return text;
  const visible = text.slice(match.index + match[0].length).replace(/[ \t]+$/u, "");
  return visible.length > 0 ? visible : text;
}
