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

/** True when a terminal tab belongs to the project on screen: the agent's
 *  commands of this chat (or a broadcast to every chat), the shells opened
 *  in this project. The others keep running and come back with their project. */
export function terminalBelongsHere(
  tab: { kind?: "pty" | "agent"; chatId?: string; root?: string | null },
  chatId: string | null,
  root: string | null | undefined,
): boolean {
  if (tab.kind === "agent") return !tab.chatId || tab.chatId === "*" || tab.chatId === chatId;
  return !tab.root || !root || sameWorkspacePath(tab.root, root);
}
