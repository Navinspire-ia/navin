// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { BackgroundProcess } from "@/lib/api";

/** "3s", "4m 05s", "2h 12m" - compact elapsed time for the process list. */
export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/**
 * First meaningful line of the command, without shell noise, capped for the
 * list row. "cd app && npm run dev -- --port 3000" -> "npm run dev -- --port 3000".
 */
export function shortCommand(command: string, maxLength = 80): string {
  const firstLine = command.split("\n")[0].trim();
  const parts = firstLine.split("&&").map((part) => part.trim());
  const last = parts[parts.length - 1] || firstLine;
  return last.length > maxLength ? `${last.slice(0, maxLength - 1)}…` : last;
}

export type ProcessStatus = "running" | "exited" | "failed";

export function processStatus(proc: BackgroundProcess): ProcessStatus {
  if (proc.returncode === null) return "running";
  return proc.returncode === 0 ? "exited" : "failed";
}

/** Last N lines of the output tail, for the expanded row. */
export function tailLines(tail: string, maxLines = 40): string[] {
  const lines = tail.replace(/\r\n/g, "\n").split("\n");
  while (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines.slice(-maxLines);
}
