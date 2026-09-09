// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Client for the Evolve Engine gateway routes (`/api/webui/evolve/*`).
 *
 * The gateway proxies to the Rust navin-engine daemon (campaign submission)
 * and reads the engine's JSON artefacts from `<project>/.navin/` directly,
 * so the dashboard works even when no daemon is running.
 */

import { apiRequest } from "@/lib/api";

export interface LatencyStats {
  requests: number;
  failures: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  rps: number;
}

export interface CheckResult {
  name: string;
  verdict: "pass" | "weak" | "fail";
  detail: string;
  measured?: number;
  threshold?: number;
}

export interface FaultOutcome {
  fault: string;
  description: string;
  checks: CheckResult[];
  verdict: "pass" | "weak" | "fail";
  evidence?: string[];
}

export interface ProofReport {
  schema: string;
  commit: string;
  profile: string;
  collected_at: string;
  faults: FaultOutcome[];
  verdict: "pass" | "weak" | "fail";
  robustness_score: number;
  _file?: string;
}

export interface VariantOutcome {
  candidate_id: string;
  rationale: string;
  stats?: LatencyStats;
  p95_std_ms?: number;
  rps_std?: number;
  tests_passed?: boolean;
  invariants_ok?: boolean;
  gain_percent?: number;
  significant?: boolean;
  behavior_equivalent?: boolean;
  eligible: boolean;
  note: string;
  /// Unified diff of what this variant changed, winner or not.
  diff?: string | null;
}

export interface OptimizeReport {
  schema: string;
  commit: string;
  collected_at: string;
  objective: "p95" | "throughput";
  baseline: LatencyStats;
  baseline_p95_std_ms?: number;
  baseline_rps_std?: number;
  bench_repeats?: number;
  invariants_checked?: number;
  baseline_score: number;
  variants: VariantOutcome[];
  winner?: string;
  winner_gain_percent?: number;
  promotion_id?: string;
  promotion_outcome?: string;
  notes?: string[];
  _file?: string;
}

export interface EvolveRunReport {
  schema: string;
  commit: string;
  collected_at: string;
  profile: string;
  generator: string;
  robustness_before: number;
  verdict_before: string;
  findings_total: number;
  findings_addressed: number;
  outcomes: Array<{
    finding: string;
    title?: string;
    accepted?: string | null;
    promotion?: string | null;
    note?: string;
  }>;
  notes?: string[];
  _file?: string;
}

export interface Certificate {
  schema: string;
  finding: string;
  candidate_id: string;
  family: string;
  score_before: number;
  score_after: number;
  checksum: string;
  signature: string;
  public_key: string;
  issued_at: string;
}

export interface PromotionRecord {
  schema: string;
  id: string;
  finding: string;
  candidate_id: string;
  mode: string;
  outcome: "merged" | "branch_only" | "blocked";
  reasons: string[];
  branch?: string | null;
  commit_sha?: string | null;
  merged: boolean;
  certificate?: Certificate | null;
  diff?: string | null;
  pushed_to?: string | null;
  pull_request?: string | null;
  created_at: string;
  rolled_back_at?: string | null;
  _file?: string;
}

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export interface Finding {
  id: string;
  title: string;
  severity: Severity;
  confidence: "high" | "medium" | "low";
  related_fault?: string | null;
  symptom: string;
  root_cause: string;
  remediation: string;
  family: string;
  evidence?: string[];
}

export interface DiagnosisReport {
  schema: string;
  commit: string;
  collected_at: string;
  source_verdict: "pass" | "weak" | "fail";
  robustness_score: number;
  findings: Finding[];
  summary: string;
  notes?: string[];
  _file?: string;
}

export interface DaemonJob {
  id: number;
  kind: string;
  state: string;
  /// Why a job failed, or why it stopped: what the engine recorded about it.
  detail?: string | null;
}

/** Why the daemon is not answering. `unsupported` is a property of the host
 * (no Unix domain sockets, i.e. Windows), the others of the moment. */
export type DaemonReason = "unsupported" | "not_running" | "unreachable";

export interface DaemonStatus {
  online: boolean;
  status: {
    engine: string;
    protocol: number;
    root: string;
    uptime_secs: number;
    jobs: DaemonJob[];
  } | null;
  /** Absent on gateways older than the daemon-reason payload. */
  supported?: boolean;
  reason?: DaemonReason | null;
}

export interface ManifestHint {
  framework: string | null;
  start: string | null;
  test: string | null;
  url: string | null;
}

export interface EvolveOverview {
  root: string;
  daemon: DaemonStatus;
  engine_bin: string | null;
  model_presets: string[];
  default_preset: string | null;
  hint: ManifestHint | null;
  autorun: AutorunState | null;
  promotions: PromotionRecord[];
  proofs: ProofReport[];
  diagnoses: DiagnosisReport[];
  optimize_runs: OptimizeReport[];
  evolve_runs: EvolveRunReport[];
  fix_reports: Array<Record<string, unknown>>;
}

export interface CertVerification {
  authentic: boolean;
  candidate: string;
  checksum_ok: boolean;
  finding: string;
  gate_valid: boolean;
  promotion: string;
  public_key: string;
  score: { before: number; after: number };
  signature_ok: boolean;
}

export type CampaignKind = "proof.run" | "optimize.run" | "evolve.run";

export interface CampaignParams {
  start?: string;
  url?: string;
  profile?: string;
  objective?: string;
  test?: string;
  preset?: string;
  duration?: number;
  concurrency?: number;
  max_variants?: number;
  max_findings?: number;
  min_gain?: number;
  diff_vectors?: number;
  /** Prove the working tree as it stands (pending fixes included), not HEAD. */
  dirty?: boolean;
}

function url(action: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, value);
  });
  const suffix = query.toString();
  return `/api/webui/evolve/${action}${suffix ? `?${suffix}` : ""}`;
}

export async function fetchEvolveOverview(
  token: string,
  path: string,
): Promise<EvolveOverview> {
  return apiRequest<EvolveOverview>(url("overview", { path }), token);
}

export async function fetchEvolveStatus(
  token: string,
  path: string,
): Promise<DaemonStatus> {
  return apiRequest<DaemonStatus>(url("status", { path }), token);
}

export async function startEvolveDaemon(
  token: string,
  path: string,
): Promise<{ started: boolean; online: boolean }> {
  // The gateway waits up to 25s for the daemon to publish an endpoint.
  return apiRequest(url("daemon/start", { path }), token, undefined, 35_000);
}

export interface AutorunState {
  enabled: boolean;
  kind?: string | null;
}

export async function setEvolveAutorun(
  token: string,
  path: string,
  enable: boolean,
): Promise<AutorunState> {
  return apiRequest(url("autorun", { path, enable: String(enable) }), token);
}

export async function fetchEvolveDocs(
  token: string,
  lang: string,
): Promise<{ markdown: string; lang: string }> {
  return apiRequest(url("docs", { lang }), token);
}

export async function stopEvolveDaemon(
  token: string,
  path: string,
): Promise<{ stopped: boolean; online: boolean }> {
  return apiRequest(url("daemon/stop", { path }), token, undefined, 20_000);
}

export async function verifyEvolveCert(
  token: string,
  path: string,
  id: string,
): Promise<CertVerification> {
  return apiRequest<CertVerification>(url("verify", { path, id }), token);
}

export async function mergeEvolvePromotion(
  token: string,
  path: string,
  id: string,
): Promise<PromotionRecord> {
  return apiRequest<PromotionRecord>(url("merge", { path, id }), token, undefined, 130_000);
}

/// Pushing and talking to the forge is slow: allow the same budget as merge.
export async function publishEvolvePromotion(
  token: string,
  path: string,
  id: string,
): Promise<PromotionRecord> {
  return apiRequest<PromotionRecord>(url("pr", { path, id }), token, undefined, 130_000);
}

export async function rollbackEvolvePromotion(
  token: string,
  path: string,
  id: string,
): Promise<PromotionRecord> {
  return apiRequest<PromotionRecord>(url("rollback", { path, id }), token, undefined, 130_000);
}

export async function enqueueEvolveCampaign(
  token: string,
  path: string,
  kind: CampaignKind,
  params: CampaignParams,
): Promise<{ result?: { job?: number }; job?: number }> {
  const flat: Record<string, string | undefined> = { path, kind };
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") flat[key] = String(value);
  });
  return apiRequest(url("enqueue", flat), token, undefined, 20_000);
}

export async function cancelEvolveJob(
  token: string,
  path: string,
  job: number,
): Promise<{ result?: { cancelled?: boolean }; cancelled?: boolean }> {
  return apiRequest(url("cancel", { path, job: String(job) }), token, undefined, 20_000);
}
