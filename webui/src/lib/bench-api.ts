/** Navin Bench: GET /api/bench?action=latest. */
import { apiRequest } from "@/lib/api";

export interface BenchCell {
  model: string;
  strategy: string;
  missions: number;
  successes: number;
  tokens: number;
  usd: number;
  seconds: number;
  tests_failed: number;
  lint_delta: number;
  usd_per_success: number | null;
}

export interface BenchReport {
  repo: string;
  dry_run: boolean;
  generated_at: string;
  matrix: BenchCell[];
}

export const benchUrl = (action = "latest") => `/api/bench?action=${encodeURIComponent(action)}`;

export const fetchBenchReport = (token: string) =>
  apiRequest<{ report: BenchReport | null }>(benchUrl(), token);
