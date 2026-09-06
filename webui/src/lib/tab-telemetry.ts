/**
 * Local Tab / ghost latency ring buffer (Phase 0).
 *
 * Always-on locally for the Code status bar (P50/P90). Never uploaded.
 * Kill-switch: ``localStorage.navin.tabTelemetry.enabled = "0"``.
 */

export type TabTelemetrySample = {
  at: number;
  latencyMs: number;
  ttftMs?: number;
  route?: string;
};

const ENABLED_KEY = "navin.tabTelemetry.enabled";
const SAMPLES_KEY = "navin.tabTelemetry.samples";
const MAX_SAMPLES = 40;

export function isTabTelemetryEnabled(): boolean {
  try {
    return window.localStorage.getItem(ENABLED_KEY) !== "0";
  } catch {
    return true;
  }
}

export function setTabTelemetryEnabled(enabled: boolean): void {
  try {
    if (enabled) window.localStorage.removeItem(ENABLED_KEY);
    else window.localStorage.setItem(ENABLED_KEY, "0");
  } catch {
    // ignore quota / private mode
  }
}

export function recordTabLatency(sample: {
  latencyMs: number;
  ttftMs?: number;
  route?: string;
}): void {
  if (!isTabTelemetryEnabled()) return;
  if (!Number.isFinite(sample.latencyMs) || sample.latencyMs < 0) return;
  try {
    const prev = readTabLatencySamples();
    const next: TabTelemetrySample[] = [
      ...prev,
      {
        at: Date.now(),
        latencyMs: Math.round(sample.latencyMs),
        ...(sample.ttftMs !== undefined ? { ttftMs: Math.round(sample.ttftMs) } : {}),
        ...(sample.route ? { route: sample.route } : {}),
      },
    ].slice(-MAX_SAMPLES);
    window.localStorage.setItem(SAMPLES_KEY, JSON.stringify(next));
  } catch {
    // ignore
  }
}

export function readTabLatencySamples(): TabTelemetrySample[] {
  try {
    const raw = window.localStorage.getItem(SAMPLES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (row): row is TabTelemetrySample =>
          !!row &&
          typeof row === "object" &&
          typeof (row as TabTelemetrySample).latencyMs === "number",
      )
      .slice(-MAX_SAMPLES);
  } catch {
    return [];
  }
}

/** P50 / P90 over the local ring buffer (empty → null). */
export function tabLatencyPercentiles(
  samples: TabTelemetrySample[] = readTabLatencySamples(),
): { p50: number; p90: number; count: number } | null {
  if (samples.length === 0) return null;
  const sorted = samples.map((s) => s.latencyMs).sort((a, b) => a - b);
  const at = (q: number) => {
    const idx = Math.min(
      sorted.length - 1,
      Math.max(0, Math.ceil(q * sorted.length) - 1),
    );
    return sorted[idx]!;
  };
  return { p50: at(0.5), p90: at(0.9), count: sorted.length };
}
