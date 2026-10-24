// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useSyncExternalStore } from "react";

/**
 * The app each chat has running on this machine: learnt from the agent's
 * preview requests and from the Browser panel. The thread offers to share it
 * (AppShareHint); nothing is shared until the user asks.
 */
const sites = new Map<string, string>();
const listeners = new Set<() => void>();

function bare(chatId: string): string {
  return chatId.startsWith("websocket:") ? chatId.slice("websocket:".length) : chatId;
}

/** Only an app served by this machine can be shared. */
function isLocalSite(url: string): boolean {
  try {
    const parsed = new URL(url.includes("://") ? url : `http://${url}`);
    return /^(localhost|127\.0\.0\.1|\[::1\])$/.test(parsed.hostname) && Boolean(parsed.port);
  } catch {
    return false;
  }
}

export function noteRunningSite(chatId: string, url: string): void {
  const id = bare(chatId.trim());
  const site = url.trim();
  if (!id || !site || !isLocalSite(site) || sites.get(id) === site) return;
  sites.set(id, site);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useRunningSite(chatId: string | null): string | null {
  return useSyncExternalStore(subscribe, () => (chatId ? sites.get(bare(chatId)) ?? null : null));
}
