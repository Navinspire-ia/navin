// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { RuntimeHealth } from "./api";

type Translate = (key: string, opts?: Record<string, unknown>) => string;

export function resourcePercent(ratio?: number | null, unavailable = "Unavailable"): string {
  return typeof ratio === "number" && Number.isFinite(ratio)
    ? `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%` : unavailable;
}

export function runtimeResourceSummary(health: RuntimeHealth, t: Translate): string {
  const unavailable = t("runtime.unavailable", { defaultValue: "Unavailable" });
  const cpuPending = health.cpu ? t("runtime.measuring", { defaultValue: "Measuring..." }) : unavailable;
  return `CPU ${resourcePercent(health.cpu?.usedRatio, cpuPending)} · RAM ${resourcePercent(health.memory?.usedRatio, unavailable)} · ${t("runtime.diskLabel", { defaultValue: "Disk space" })} ${resourcePercent(health.disk?.usedRatio, unavailable)}`;
}

export function runtimePressureTitle(health: RuntimeHealth, t: Translate): string {
  const reasons = health.reasons ?? [];
  if (reasons.length > 1) return t("runtime.pressure", { defaultValue: "High machine resource use" });
  if (reasons.includes("memory")) return t("runtime.memory", { defaultValue: "Low available machine memory" });
  if (reasons.includes("disk")) return t("runtime.disk", { defaultValue: "Low workspace disk space" });
  if (reasons.includes("cpu")) return t("runtime.cpu", { defaultValue: "Sustained high machine CPU use" });
  return t("runtime.pressure", { defaultValue: "High machine resource use" });
}
