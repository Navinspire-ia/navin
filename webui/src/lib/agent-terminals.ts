// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { sameWorkspacePath } from "@/lib/workspace";

export const MAX_FINISHED_AGENT_TERMINALS = 3;

type TerminalRecord = { id: string; kind?: "pty" | "agent"; exited: boolean; chatId?: string };

/** Keep live work and user shells; completed command output remains in chat.
 *  The finished-command budget is per chat, so commands run in another
 *  project never push this project's last outputs out. */
export function retainAgentTerminals<T extends TerminalRecord>(tabs: T[], selected: string | null): T[] {
  const finished = new Map<string, string[]>();
  for (const tab of tabs) {
    if (tab.kind !== "agent" || !tab.exited) continue;
    const key = tab.chatId ?? "";
    finished.set(key, [...(finished.get(key) ?? []), tab.id]);
  }
  const recent = new Set([...finished.values()].flatMap((ids) => ids.slice(-MAX_FINISHED_AGENT_TERMINALS)));
  const kept = tabs.filter((tab) => tab.kind !== "agent" || !tab.exited || tab.id === selected || recent.has(tab.id));
  return kept.length === tabs.length ? tabs : kept;
}

/** True when a terminal tab belongs to the chat/project on screen. Strict:
 *  an agent tab shows only in the chat that ran it (no unscoped tab, no "*"
 *  broadcast leaking into every chat), a shell only in the project it was
 *  opened in (a shell opened with no project shows only while none is open).
 *  The others keep running and come back with their chat or project. */
export function terminalBelongsHere(
  tab: { kind?: "pty" | "agent"; chatId?: string; root?: string | null },
  chatId: string | null,
  root: string | null | undefined,
): boolean {
  if (tab.kind === "agent") return Boolean(chatId) && tab.chatId === chatId;
  const tabRoot = tab.root || null;
  const here = root || null;
  if (!tabRoot || !here) return !tabRoot && !here;
  return sameWorkspacePath(tabRoot, here);
}
