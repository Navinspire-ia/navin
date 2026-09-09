// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export type ConnectionChoice = { id: string; label: string };

export type ProviderConnectionCatalog = {
  regions: ConnectionChoice[];
  plans: ConnectionChoice[];
  protocols: ConnectionChoice[];
  default_region: string;
  default_plan: string;
  default_protocol: string;
  bases: Record<string, string>;
  docs_url?: string | null;
  notes?: string | null;
};

export function lookupConnectionBase(
  catalog: Pick<ProviderConnectionCatalog, "bases" | "default_region" | "default_plan" | "default_protocol">,
  region?: string | null,
  plan?: string | null,
  protocol?: string | null,
): string | undefined {
  const resolvedRegion = (region || catalog.default_region).trim().toLowerCase();
  const resolvedPlan = (plan || catalog.default_plan).trim().toLowerCase();
  const resolvedProtocol = (protocol || catalog.default_protocol).trim().toLowerCase();
  const keys = [
    `${resolvedRegion}|${resolvedPlan}|${resolvedProtocol}`,
    `${resolvedRegion}|*|${resolvedProtocol}`,
    `${resolvedRegion}|${resolvedPlan}|*`,
    `${resolvedRegion}|*|*`,
    `*|${resolvedPlan}|${resolvedProtocol}`,
    `*|*|${resolvedProtocol}`,
    `*|${resolvedPlan}|*`,
    `*|*|*`,
  ];
  for (const key of keys) {
    const found = catalog.bases[key];
    if (found) return found;
  }
  return undefined;
}
