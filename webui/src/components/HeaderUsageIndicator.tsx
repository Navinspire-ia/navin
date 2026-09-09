// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useTranslation } from "react-i18next";

import { useAccount } from "@/hooks/useAccount";
import { cn } from "@/lib/utils";

/**
 * Compact "Usage : [bar] 26%" chip for the top bars, shown only when a
 * managed Navin subscription is active. Clicking opens Settings → Account.
 */
export function HeaderUsageIndicator({
  onClick,
  className,
}: {
  onClick?: () => void;
  className?: string;
}) {
  const { t, i18n } = useTranslation();
  const { account } = useAccount();

  const plan = (account?.plan || "").trim().toLowerCase();
  const usage = account?.usage ?? null;
  if (
    !account?.connected
    || !account.managed_key_active
    || !plan
    || plan === "free"
    || !usage
  ) {
    return null;
  }

  const percent = Math.min(100, Math.max(0, Math.round(usage.used_percent ?? 0)));
  const barClass =
    percent >= 95
      ? "bg-red-500"
      : percent >= 80
        ? "bg-amber-500"
        : "bg-foreground/85";
  const periodEndSec = usage.period_end || account.period_end || null;
  const renewalLabel = periodEndSec
    ? new Date(periodEndSec * 1000).toLocaleDateString(i18n.language || undefined)
    : null;
  const usageLabel = t("quotaLimit.monthlyUsage", {
    defaultValue: "Monthly usage",
  });
  const title = renewalLabel
    ? `${usageLabel} : ${percent}% · ${t("quotaLimit.renewal", {
        defaultValue: "Renewal",
      })} ${renewalLabel}`
    : `${usageLabel} : ${percent}%`;

  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={cn(
        "flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-[11.5px] font-medium",
        "text-muted-foreground/80 transition-colors hover:bg-muted/60 hover:text-foreground",
        "active:scale-[0.96]",
        className,
      )}
      data-testid="header-usage-indicator"
    >
      <span className="truncate">
        {t("quotaLimit.usageShort", { defaultValue: "Usage" })} :
      </span>
      <span
        className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span
          className={cn("block h-full rounded-full transition-[width]", barClass)}
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="tabular-nums">{percent}%</span>
    </button>
  );
}
