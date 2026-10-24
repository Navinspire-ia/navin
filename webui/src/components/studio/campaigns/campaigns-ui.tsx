// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Loader2, X } from "lucide-react";

import { intlLocale } from "@/i18n/intl";
import type { CampaignStatus, ContactStatus, ReplyClass, ReplyStatus, SendStatus } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";

export type Tx = (key: string, fallback: string, values?: Record<string, unknown>) => string;

/** `t("campaigns.<key>")` with an English fallback. */
export function useCampaignsTx(): Tx {
  const { t } = useTranslation();
  return useCallback(
    (key: string, fallback: string, values?: Record<string, unknown>) =>
      String(t(`campaigns.${key}`, { defaultValue: fallback, ...(values ?? {}) })),
    [t],
  );
}

export function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error ?? "");
}

export function formatDateTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(intlLocale(), { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat(intlLocale()).format(value || 0);
}

/** Rates arrive as 0..1. */
export function formatRate(value: number): string {
  const pct = Math.max(0, value || 0) * 100;
  return `${pct >= 10 || pct === 0 ? pct.toFixed(0) : pct.toFixed(1)}%`;
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

const TONES = {
  neutral: "bg-muted text-muted-foreground",
  blue: "bg-[#2f7de1]/10 text-[#2f7de1]",
  green: "bg-[#1f9d74]/10 text-[#1f9d74]",
  amber: "bg-[#d4892a]/12 text-[#a15c07] dark:text-[#f0b35a]",
  red: "bg-[#c2410c]/10 text-[#c2410c]",
  violet: "bg-[#6d5bd0]/10 text-[#6d5bd0] dark:text-[#a99cf0]",
} as const;
export type Tone = keyof typeof TONES;

export function Chip({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

const CAMPAIGN_TONE: Record<CampaignStatus, Tone> = {
  draft: "neutral",
  scheduled: "violet",
  sending: "blue",
  paused: "amber",
  sent: "green",
  failed: "red",
  cancelled: "neutral",
};

export function campaignStatusLabel(tx: Tx, status: CampaignStatus): string {
  const labels: Record<CampaignStatus, string> = {
    draft: "Draft",
    scheduled: "Scheduled",
    sending: "Sending",
    paused: "Paused",
    sent: "Sent",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return tx(`status.${status}`, labels[status] ?? status);
}

export function CampaignStatusChip({ status, tx }: { status: CampaignStatus; tx: Tx }) {
  return <Chip tone={CAMPAIGN_TONE[status] ?? "neutral"}>{campaignStatusLabel(tx, status)}</Chip>;
}

export const REPLY_CLASSES: ReplyClass[] = [
  "interested",
  "meeting",
  "question",
  "not_interested",
  "unsubscribe",
  "out_of_office",
  "bounce",
  "other",
];

const REPLY_TONE: Record<ReplyClass, Tone> = {
  interested: "green",
  meeting: "green",
  question: "blue",
  not_interested: "neutral",
  unsubscribe: "red",
  out_of_office: "amber",
  bounce: "red",
  other: "neutral",
};

export function replyClassLabel(tx: Tx, value: ReplyClass): string {
  const labels: Record<ReplyClass, string> = {
    interested: "Interested",
    meeting: "Meeting request",
    question: "Question",
    not_interested: "Not interested",
    unsubscribe: "Unsubscribe",
    out_of_office: "Out of office",
    bounce: "Bounce",
    other: "Other",
  };
  return tx(`replyClass.${value}`, labels[value] ?? value);
}

export function ReplyClassChip({ value, tx }: { value: ReplyClass; tx: Tx }) {
  return <Chip tone={REPLY_TONE[value] ?? "neutral"}>{replyClassLabel(tx, value)}</Chip>;
}

export const REPLY_STATUSES: ReplyStatus[] = ["new", "drafted", "answered", "handed_off", "ignored"];

export function replyStatusLabel(tx: Tx, value: ReplyStatus): string {
  const labels: Record<ReplyStatus, string> = {
    new: "New",
    drafted: "Draft ready",
    answered: "Answered",
    handed_off: "Handed off",
    ignored: "Ignored",
  };
  return tx(`replyStatus.${value}`, labels[value] ?? value);
}

export function contactStatusLabel(tx: Tx, value: ContactStatus): string {
  const labels: Record<ContactStatus, string> = {
    subscribed: "Subscribed",
    unsubscribed: "Unsubscribed",
    bounced: "Bounced",
  };
  return tx(`contactStatus.${value}`, labels[value] ?? value);
}

export function sendStatusLabel(tx: Tx, value: SendStatus): string {
  const labels: Record<SendStatus, string> = { queued: "Queued", sent: "Sent", failed: "Failed", skipped: "Skipped" };
  return tx(`sendStatus.${value}`, labels[value] ?? value);
}

export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  if (!message) return null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-xl border border-[#c2410c]/25 bg-[#c2410c]/[0.06] px-3 py-2 text-[12.5px] text-[#9a3412] dark:text-[#fdba74]">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onDismiss ? (
        <button type="button" onClick={onDismiss} className="shrink-0 rounded p-0.5 hover:bg-black/5" aria-label="Dismiss">
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

export function Notice({ message }: { message: string }) {
  if (!message) return null;
  return (
    <div role="status" className="rounded-xl border border-[#1f9d74]/25 bg-[#1f9d74]/[0.07] px-3 py-2 text-[12.5px] text-[#1f9d74]">
      {message}
    </div>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function EmptyState({ icon, title, body, action }: { icon?: ReactNode; title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-sm flex-col items-center gap-2 px-6 py-14 text-center">
      {icon ? <div className="grid h-12 w-12 place-items-center rounded-2xl bg-muted/60 text-muted-foreground">{icon}</div> : null}
      <p className="text-[14px] font-semibold text-foreground">{title}</p>
      {body ? <p className="text-[12.5px] leading-relaxed text-muted-foreground">{body}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function Card({ children, className, testId }: { children: ReactNode; className?: string; testId?: string }) {
  return <div data-testid={testId} className={cn("rounded-2xl border border-border/60 bg-card/80 shadow-sm", className)}>{children}</div>;
}

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={cn("block space-y-1", className)}>
      <span className="block text-[12px] font-medium text-foreground/85">{label}</span>
      {children}
      {hint ? <span className="block text-[11.5px] leading-snug text-muted-foreground">{hint}</span> : null}
    </label>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-[#1d6af0]" : "bg-foreground/20",
      )}
    >
      <span
        className={cn(
          "inline-block h-4 w-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4 rtl:-translate-x-4" : "translate-x-0.5 rtl:-translate-x-0.5",
        )}
      />
    </button>
  );
}

export const SELECT_CLASS =
  "h-9 w-full rounded-md border border-input bg-background px-2.5 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring/50";

export function Pager({
  offset,
  limit,
  total,
  onChange,
  tx,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
  tx: Tx;
}) {
  if (total <= limit) return null;
  const from = total ? offset + 1 : 0;
  const to = Math.min(total, offset + limit);
  return (
    <div className="flex items-center justify-end gap-2 px-3 py-2 text-[12px] text-muted-foreground">
      <span>{tx("pager.range", "{{from}}-{{to}} of {{total}}", { from, to, total })}</span>
      <button
        type="button"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
        className="rounded-md border border-border/70 px-2 py-1 hover:bg-muted/60 disabled:opacity-40"
      >
        {tx("pager.prev", "Previous")}
      </button>
      <button
        type="button"
        disabled={to >= total}
        onClick={() => onChange(offset + limit)}
        className="rounded-md border border-border/70 px-2 py-1 hover:bg-muted/60 disabled:opacity-40"
      >
        {tx("pager.next", "Next")}
      </button>
    </div>
  );
}

/** The local timezone name, shown next to schedule pickers. */
export function localTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

/** `<input type="datetime-local">` value from an ISO string, and back. */
export function isoToLocalInput(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function localInputToIso(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}
