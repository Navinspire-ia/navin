// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { MetagraphEdge, MetagraphPayload } from "@/lib/types";

/** Files that transitively depend on ``rootId`` (who breaks if root changes). */
export function impactIds(payload: MetagraphPayload, rootId: string): Set<string> {
  const reverse = new Map<string, string[]>();
  for (const edge of payload.edges) {
    const list = reverse.get(edge.target) ?? [];
    list.push(edge.source);
    reverse.set(edge.target, list);
  }
  const seen = new Set<string>([rootId]);
  const queue = [rootId];
  while (queue.length > 0) {
    const current = queue.pop()!;
    for (const next of reverse.get(current) ?? []) {
      if (seen.has(next)) continue;
      seen.add(next);
      queue.push(next);
    }
  }
  return seen;
}

/** Dependents only (excludes the changed root), sorted for UI lists. */
export function impactDependents(
  payload: MetagraphPayload,
  rootId: string,
): string[] {
  const ids = impactIds(payload, rootId);
  return [...ids].filter((id) => id !== rootId).sort((a, b) => a.localeCompare(b));
}

/** Direct importers of ``rootId`` (one hop on reverse edges). */
export function directImporters(
  edges: readonly MetagraphEdge[],
  rootId: string,
): string[] {
  const out: string[] = [];
  for (const edge of edges) {
    if (edge.target === rootId) out.push(edge.source);
  }
  return out.sort((a, b) => a.localeCompare(b));
}
