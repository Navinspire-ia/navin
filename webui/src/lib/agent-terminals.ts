// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export const MAX_FINISHED_AGENT_TERMINALS = 3;

type TerminalRecord = { id: string; kind?: "pty" | "agent"; exited: boolean };

/** Keep live work and user shells; completed command output remains in chat. */
export function retainAgentTerminals<T extends TerminalRecord>(tabs: T[], selected: string | null): T[] {
  const recent = new Set(tabs.filter((tab) => tab.kind === "agent" && tab.exited)
    .slice(-MAX_FINISHED_AGENT_TERMINALS).map((tab) => tab.id));
  const kept = tabs.filter((tab) => tab.kind !== "agent" || !tab.exited || tab.id === selected || recent.has(tab.id));
  return kept.length === tabs.length ? tabs : kept;
}
