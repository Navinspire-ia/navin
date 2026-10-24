// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { intlLocale } from "@/i18n/intl";

type ManagedUsage = {
  budget_micro_usd?: number;
  spent_micro_usd?: number;
  used_percent?: number;
} | null | undefined;

/**
 * Share of the monthly budget used (0-100), from the exact spend: the site
 * rounds to whole percents, so 0.76 $ of a 160 $ budget would read 0 %.
 */
export function usagePercent(usage: ManagedUsage): number {
  const budget = usage?.budget_micro_usd ?? 0;
  const exact = budget > 0
    ? ((usage?.spent_micro_usd ?? 0) / budget) * 100
    : (usage?.used_percent ?? 0);
  return Math.min(100, Math.max(0, exact));
}

/** One decimal under 10 % ("0,5"), "<0.1" for a trace, whole percents above. */
export function formatUsagePercent(percent: number): string {
  if (percent > 0 && percent < 0.1) return "<0.1";
  return new Intl.NumberFormat(intlLocale(), {
    maximumFractionDigits: percent < 10 ? 1 : 0,
  }).format(percent);
}
