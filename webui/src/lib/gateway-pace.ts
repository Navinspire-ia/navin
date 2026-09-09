// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Ambient "is the engine answering?" state, fed by the API client.
 *
 * A stalled gateway used to surface as a red message in whichever panel asked
 * first, each one wording it differently and none saying the others were
 * waiting on the same thing. The client now reports transport failures and
 * successes here; the status bar shows a single quiet chip while reads are
 * being retried, and drops it on the next answer.
 */

export type GatewayStall = "timeout" | "unreachable";

export interface GatewayPace {
  /** When the current run of transport failures started; null when healthy. */
  stalledSince: number | null;
  /** What the latest failure looked like. */
  stall: GatewayStall | null;
  /** Consecutive transport failures without a successful answer in between. */
  failures: number;
}

const HEALTHY: GatewayPace = { stalledSince: null, stall: null, failures: 0 };

let pace: GatewayPace = HEALTHY;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    try {
      listener();
    } catch {
      // One broken subscriber must not hide the state from the others.
    }
  }
}

export function getGatewayPace(): GatewayPace {
  return pace;
}

export function subscribeGatewayPace(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** A read timed out or the engine could not be reached. */
export function noteGatewayStalled(stall: GatewayStall, now: number = Date.now()): void {
  pace = {
    stalledSince: pace.stalledSince ?? now,
    stall,
    failures: pace.failures + 1,
  };
  emit();
}

/** Any gateway call succeeded: the engine is answering again. */
export function noteGatewayAnswered(): void {
  if (pace === HEALTHY) return;
  pace = HEALTHY;
  emit();
}

/** Tests only. */
export function resetGatewayPace(): void {
  pace = HEALTHY;
  listeners.clear();
}
