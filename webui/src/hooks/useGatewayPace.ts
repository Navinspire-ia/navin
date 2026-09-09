// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState, useSyncExternalStore } from "react";

import {
  getGatewayPace,
  subscribeGatewayPace,
  type GatewayPace,
} from "@/lib/gateway-pace";

/** Live view of whether the engine is answering (fed by the API client). */
export function useGatewayPace(): GatewayPace {
  return useSyncExternalStore(subscribeGatewayPace, getGatewayPace, getGatewayPace);
}

/**
 * Whole seconds since the current stall began, refreshed every second while
 * one is in progress; 0 when the engine is healthy.
 */
export function useGatewayStallSeconds(pace: GatewayPace): number {
  const since = pace.stalledSince;
  const [seconds, setSeconds] = useState(() => stallSeconds(since));
  useEffect(() => {
    setSeconds(stallSeconds(since));
    if (since === null) return;
    const timer = window.setInterval(() => setSeconds(stallSeconds(since)), 1000);
    return () => window.clearInterval(timer);
  }, [since]);
  return seconds;
}

/** Whole seconds from `since` to `now`; 0 when there is no stall. */
export function stallSeconds(since: number | null, now: number = Date.now()): number {
  if (since === null) return 0;
  return Math.max(0, Math.round((now - since) / 1000));
}
