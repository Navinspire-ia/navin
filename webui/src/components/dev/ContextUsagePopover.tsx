// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { RefObject } from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ContextUsagePayload } from "@/lib/types";

import {
  CONTEXT_BUCKET_COLORS,
  contextBucketLabel,
  contextBucketWidths,
  contextReasonLabel,
  formatContextTokens,
} from "./devWorkbenchUtils";

export function ContextUsagePopover({
  usage,
  pos,
  popoverRef,
  onClose,
}: {
  usage: ContextUsagePayload;
  pos: { bottom: number; right: number };
  popoverRef: RefObject<HTMLDivElement>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const percent = usage.percent ?? 0;
  const windowTokens = usage.context_window ?? 0;
  const buckets = usage.buckets ?? [];
  const included = usage.included ?? [];
  const widths = contextBucketWidths(buckets, windowTokens);
  const fillPercent =
    windowTokens > 0 ? Math.min(100, (Math.max(0, usage.tokens) / windowTokens) * 100) : 0;

  return (
    <div
      ref={popoverRef}
      role="dialog"
      aria-label={t("dev.status.contextTitle", { defaultValue: "Context usage" })}
      className="fixed z-[80] w-[22rem] rounded-lg border border-border/70 bg-background p-3 shadow-lg"
      style={{ bottom: pos.bottom, right: pos.right }}
    >
      <div className="mb-2 flex items-start justify-between gap-3">
        <div>
          <p className="text-[13px] font-medium text-foreground">
            {t("dev.status.contextTitle", { defaultValue: "Context usage" })}
          </p>
          <p className="text-[11px] text-muted-foreground">
            {t("dev.status.contextFull", {
              defaultValue: "{{percent}}% full",
              percent,
            })}
          </p>
        </div>
        <div className="flex items-start gap-2">
          <p className="text-right text-[11px] tabular-nums text-muted-foreground">
            {t("dev.status.contextTokens", {
              defaultValue: "~{{tokens}} / {{window}} tokens",
              tokens: formatContextTokens(usage.tokens ?? 0),
              window: formatContextTokens(windowTokens),
            })}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="rounded-sm p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={t("dev.status.contextClose", { defaultValue: "Close" })}
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      </div>
      {buckets.length > 0 ? (
        <>
          <div className="mb-2 flex h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
            {widths.map((segment) => (
              <span
                key={segment.id}
                className="h-full"
                style={{
                  width: `${segment.percent}%`,
                  backgroundColor: CONTEXT_BUCKET_COLORS[segment.id] ?? "#a8a29e",
                }}
              />
            ))}
          </div>
          <ul className="mb-2 space-y-1 text-[12px]">
            {buckets.map((bucket) => (
              <li key={bucket.id} className="flex items-center gap-2">
                <span
                  className="h-2 w-2 shrink-0 rounded-[2px]"
                  style={{
                    backgroundColor: CONTEXT_BUCKET_COLORS[bucket.id] ?? "#a8a29e",
                  }}
                  aria-hidden
                />
                <span className="min-w-0 flex-1 truncate text-muted-foreground">
                  {contextBucketLabel(bucket.id, bucket.label, t)}
                </span>
                <span className="tabular-nums text-foreground">
                  {formatContextTokens(bucket.tokens)}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : fillPercent > 0 ? (
        <div className="mb-2 h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
          <span
            className="block h-full"
            style={{ width: `${fillPercent}%`, backgroundColor: "#94a3b8" }}
          />
        </div>
      ) : null}
      {included.length > 0 ? (
        <div className="border-t border-border/40 pt-2">
          <p className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("dev.status.contextPacked", { defaultValue: "Packed into this turn" })}
          </p>
          <ul className="max-h-36 space-y-1 overflow-y-auto">
            {included.slice(0, 24).map((row) => (
              <li key={`${row.reason}:${row.path}`} className="min-w-0">
                <p className="truncate font-mono text-[11px] text-foreground" title={row.path}>
                  {row.path}
                </p>
                <p className="text-[10.5px] text-muted-foreground">
                  {contextReasonLabel(row.reason, t)}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
