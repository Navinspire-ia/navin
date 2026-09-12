// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Slash command seeded when the user opens Chat on a Studio desk. */
export const DESK_CHAT_COMMAND: Readonly<Record<string, string>> = {
  tenders: "/tenders",
  career: "/career",
  leads: "/leads",
  trading: "/trading",
  marketing: "/marketing",
  scraping: "/scrape",
  meeting: "/meeting",
};

const COMMAND_TOKEN = /^\/[a-z][a-z0-9_-]*/i;

/** Put the desk command first so the agent loads the matching tools. */
export function applyDeskChatCommand(current: string, command: string): string {
  const token = command.startsWith("/") ? command : `/${command}`;
  const prefix = `${token} `;
  const text = current ?? "";
  const trimmed = text.trim();
  if (!trimmed) return prefix;
  if (trimmed === token || trimmed.startsWith(`${token} `) || trimmed.startsWith(`${token}\n`)) {
    return /[\s\n]$/.test(text) ? text : `${text} `;
  }
  if (/^\/[a-z][a-z0-9_-]*\s*$/i.test(trimmed)) return prefix;
  if (COMMAND_TOKEN.test(trimmed)) return text.replace(COMMAND_TOKEN, token);
  return `${prefix}${text.replace(/^\s+/, "")}`;
}
