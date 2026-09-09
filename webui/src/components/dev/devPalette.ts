// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Lightweight fuzzy ranking for Ctrl+T / Ctrl+Shift+P pickers.
 * Lower score is better; null means no match.
 */
export function fuzzyScore(haystack: string, needle: string): number | null {
  const text = haystack.toLowerCase();
  const term = needle.trim().toLowerCase();
  if (!term) return 0;
  if (text === term) return 0;
  if (text.startsWith(term)) return 1;
  if (text.includes(term)) return 2;
  let ti = 0;
  for (let i = 0; i < text.length && ti < term.length; i += 1) {
    if (text[i] === term[ti]) ti += 1;
  }
  return ti === term.length ? 3 + (text.length - term.length) : null;
}

export type DevCommand = {
  id: string;
  label: string;
  hint?: string;
  keywords?: string;
  run: () => void;
};

export function filterCommands(
  commands: readonly DevCommand[],
  query: string,
): DevCommand[] {
  const term = query.trim();
  if (!term) return [...commands];
  const scored: { score: number; cmd: DevCommand }[] = [];
  for (const cmd of commands) {
    const hay = `${cmd.label} ${cmd.hint ?? ""} ${cmd.keywords ?? ""} ${cmd.id}`;
    const score = fuzzyScore(hay, term);
    if (score == null) continue;
    scored.push({ score, cmd });
  }
  scored.sort(
    (a, b) => a.score - b.score || a.cmd.label.localeCompare(b.cmd.label),
  );
  return scored.map((row) => row.cmd);
}
