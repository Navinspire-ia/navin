// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type {
  DaemonJob,
  DaemonStatus,
  EvolveOverview,
  ProofReport,
} from "@/lib/evolve-api";

/** Daemon job states after which polling must stop. */
const TERMINAL_STATES = new Set(["done", "failed", "cancelled", "error"]);

function asJobId(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^\d+$/.test(value)) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function extractJobId(result: {
  result?: { job?: number | string };
  job?: number | string;
}): number | null {
  return asJobId(result.result?.job) ?? asJobId(result.job);
}

/** Workspace Evolve must prove: the chat that owns the pending edits first.
 * If this chat has no folder yet, the last Code project is the fallback so
 * the file-space edits still have a root. A leftover Code folder must never
 * override a live chat workspace. */
export function proveProjectPath(
  sessionRoot: string | null | undefined,
  fallbackRoot?: string | null,
): string | null {
  return sessionRoot?.trim() || fallbackRoot?.trim() || null;
}

export function jobFromStatus(
  status: DaemonStatus | null,
  jobId: number,
): DaemonJob | null {
  const jobs = status?.status?.jobs ?? [];
  return jobs.find((job) => job.id === jobId) ?? null;
}

export function isJobTerminal(state: string | undefined | null): boolean {
  return state != null && TERMINAL_STATES.has(state.toLowerCase());
}

/** Most recent proof report of an overview (by collected_at). */
export function latestProof(
  overview: EvolveOverview | null,
): ProofReport | null {
  const proofs = overview?.proofs ?? [];
  if (!proofs.length) return null;
  return [...proofs].sort((a, b) =>
    (b.collected_at || "").localeCompare(a.collected_at || ""),
  )[0];
}

export function verdictTone(
  verdict: ProofReport["verdict"] | null | undefined,
): "good" | "warn" | "bad" | "muted" {
  if (verdict === "pass") return "good";
  if (verdict === "weak") return "warn";
  if (verdict === "fail") return "bad";
  return "muted";
}

/** "pass · 92/100" style label for the proof result line. */
export function proofSummary(proof: ProofReport | null): string {
  if (!proof) return "";
  const score = Math.round(proof.robustness_score);
  return `${proof.verdict} · ${score}/100`;
}
