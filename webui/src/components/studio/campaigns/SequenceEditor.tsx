// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { ArrowDown, ArrowUp, Clock, Copy, FlaskConical, ListOrdered, Mail, Plus, Trash2, Trophy, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type {
  Campaign,
  CampaignSettings,
  SequenceStep,
  StepStats,
  VariantStats,
} from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { EmailEditor } from "./EmailEditor";
import { Card, Chip, Field, formatNumber, formatRate, SELECT_CLASS, type Tx } from "./campaigns-ui";

export const MAX_SEQUENCE_STEPS = 6;
export const MAX_SUBJECT_VARIANTS = 2;
/** Sends each variant needs before a winner is named. */
export const AB_MIN_SENDS = 20;

let stepCounter = 0;

export function newStep(previous?: SequenceStep): SequenceStep {
  stepCounter += 1;
  return {
    id: `new_${Date.now().toString(36)}_${stepCounter}`,
    delay_days: previous?.delay_days ?? 3,
    condition: previous?.condition ?? "no_reply",
    subject: "",
    html: previous?.html ?? "",
  };
}

/** Day of each follow-up counted from the first email (cumulative delays). */
export function sequenceDays(steps: SequenceStep[]): number[] {
  let day = 0;
  return steps.map((step) => {
    day += Math.max(1, Number(step.delay_days) || 1);
    return day;
  });
}

export function moveStep(steps: SequenceStep[], index: number, delta: -1 | 1): SequenceStep[] {
  const target = index + delta;
  if (target < 0 || target >= steps.length) return steps;
  const next = [...steps];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** Subject a follow-up goes out with: its own, or "Re: <first subject>" in the same thread. */
export function followUpSubject(step: SequenceStep, firstSubject: string): string {
  if (step.subject.trim()) return step.subject;
  return /^re:/i.test(firstSubject.trim()) ? firstSubject : `Re: ${firstSubject}`;
}

/**
 * The A/B winner: higher reply rate, then higher open rate. Null until every
 * variant has enough sends, because a handful of opens decides nothing.
 */
export function abWinner(variants: VariantStats[] | undefined, minSends = AB_MIN_SENDS): VariantStats | null {
  if (!variants || variants.length < 2) return null;
  if (variants.some((variant) => variant.sent < minSends)) return null;
  const ranked = [...variants].sort((a, b) => b.reply_rate - a.reply_rate || b.open_rate - a.open_rate);
  const [best, second] = ranked;
  if (best.reply_rate === second.reply_rate && best.open_rate === second.open_rate) return null;
  return best;
}

export function conditionLabel(tx: Tx, condition: SequenceStep["condition"]): string {
  if (condition === "no_open") return tx("sequence.condNoOpen", "If not opened");
  if (condition === "always") return tx("sequence.condAlways", "Always");
  return tx("sequence.condNoReply", "If no reply");
}

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;

export function weekdayLabel(day: number, locale?: string): string {
  // 2024-01-01 was a Monday: day 1..7 maps onto that week.
  const date = new Date(Date.UTC(2024, 0, day));
  return new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "UTC" }).format(date);
}

/** "Mon-Fri 09:00-18:00 Europe/Paris" (or the listed days when not a range). */
export function sendWindowSummary(window: CampaignSettings["send_window"] | undefined, locale?: string): string {
  if (!window?.enabled) return "";
  const days = [...window.days].sort((a, b) => a - b);
  const contiguous = days.length > 1 && days.every((day, index) => index === 0 || day === days[index - 1] + 1);
  const dayText = contiguous
    ? `${weekdayLabel(days[0], locale)}-${weekdayLabel(days[days.length - 1], locale)}`
    : days.map((day) => weekdayLabel(day, locale)).join(", ");
  return `${dayText} ${window.start}-${window.end} ${window.timezone}`;
}

export { WEEKDAYS };

/** Small chips for list rows: "Sequence: 3 steps", "A/B". */
export function CampaignFeatureChips({ campaign, tx }: { campaign: Pick<Campaign, "steps" | "subject_variants" | "channel">; tx: Tx }) {
  const steps = campaign.steps?.length ?? 0;
  const ab = (campaign.subject_variants?.length ?? 0) > 0;
  if (campaign.channel === "whatsapp" || (!steps && !ab)) return null;
  return (
    <span className="inline-flex flex-wrap gap-1" data-testid="campaign-feature-chips">
      {steps ? (
        <Chip tone="blue">
          <ListOrdered className="me-1 inline h-3 w-3" aria-hidden />
          {tx("sequence.chip", "Sequence: {{count}} steps", { count: steps + 1 })}
        </Chip>
      ) : null}
      {ab ? (
        <Chip tone="violet">
          <FlaskConical className="me-1 inline h-3 w-3" aria-hidden />
          A/B
        </Chip>
      ) : null}
    </span>
  );
}

/** Extra subjects B and C beside the main subject (A). */
export function SubjectVariantsEditor({
  draft,
  set,
  tx,
}: {
  draft: Partial<Campaign>;
  set: (patch: Partial<Campaign>) => void;
  tx: Tx;
}) {
  const variants = draft.subject_variants ?? [];
  const update = (next: string[]) => set({ subject_variants: next });
  return (
    <div className="space-y-2 sm:col-span-2" data-testid="ab-variants">
      {variants.map((variant, index) => (
        <Field key={index} label={tx("ab.variantLabel", "Subject {{letter}}", { letter: "BC"[index] })}>
          <div className="flex gap-1.5">
            <Input
              value={variant}
              maxLength={200}
              onChange={(event) => update(variants.map((value, i) => (i === index ? event.target.value : value)))}
              placeholder={tx("ab.variantPlaceholder", "Another subject to test")}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-10 w-10 shrink-0"
              onClick={() => update(variants.filter((_, i) => i !== index))}
              aria-label={tx("ab.remove", "Remove subject {{letter}}", { letter: "BC"[index] })}
            >
              <X className="h-4 w-4" aria-hidden />
            </Button>
          </div>
        </Field>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        {variants.length < MAX_SUBJECT_VARIANTS ? (
          <Button type="button" variant="outline" size="sm" className="h-8 rounded-full" onClick={() => update([...variants, ""])}>
            <FlaskConical className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {variants.length ? tx("ab.addAnother", "Add subject C") : tx("ab.add", "A/B test the subject")}
          </Button>
        ) : null}
        {variants.length ? (
          <p className="text-[11.5px] text-muted-foreground">
            {tx("ab.hint", "Contacts are split evenly between the subjects; the results compare open and reply rates.")}
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Follow-ups after the first email, with a day-by-day timeline. */
export function SequenceEditor({
  draft,
  set,
  tx,
  attrKeys,
}: {
  draft: Partial<Campaign>;
  set: (patch: Partial<Campaign>) => void;
  tx: Tx;
  attrKeys: string[];
}) {
  const steps = draft.steps ?? [];
  const days = sequenceDays(steps);
  const [openId, setOpenId] = useState<string>(steps[0]?.id ?? "");
  const update = (next: SequenceStep[]) => set({ steps: next });
  const patch = (index: number, value: Partial<SequenceStep>) =>
    update(steps.map((step, i) => (i === index ? { ...step, ...value } : step)));
  const firstSubject = draft.subject ?? "";

  const add = () => {
    const step = newStep(steps[steps.length - 1]);
    update([...steps, { ...step, html: "" }]);
    setOpenId(step.id);
  };

  return (
    <section className="space-y-3" data-testid="sequence-editor">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-foreground">{tx("sequence.title", "Follow-up sequence")}</p>
          <p className="text-[12px] text-muted-foreground">
            {tx("sequence.help", "Automatic follow-ups in the same thread. A reply, an unsubscribe or a bounce stops the sequence for that person.")}
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" className="h-8 rounded-full" disabled={steps.length >= MAX_SEQUENCE_STEPS} onClick={add} data-testid="sequence-add">
          <Plus className="me-1.5 h-3.5 w-3.5" aria-hidden />
          {tx("sequence.add", "Add a follow-up")}
        </Button>
      </div>

      <ol className="relative space-y-2 border-s border-border/70 ps-4">
        <li className="relative">
          <span className="absolute -start-[1.4rem] top-1.5 grid h-4 w-4 place-items-center rounded-full bg-[#2f7de1] text-white">
            <Mail className="h-2.5 w-2.5" aria-hidden />
          </span>
          <p className="text-[12.5px] text-foreground">
            <span className="font-semibold">{tx("sequence.day", "Day {{n}}", { n: 0 })}</span>
            {" · "}
            {tx("sequence.first", "First email")}
            {firstSubject ? <span className="text-muted-foreground">{` · ${firstSubject}`}</span> : null}
          </p>
        </li>
        {steps.map((step, index) => {
          const open = openId === step.id;
          return (
            <li key={step.id} className="relative" data-testid="sequence-step">
              <span className="absolute -start-[1.4rem] top-2.5 grid h-4 w-4 place-items-center rounded-full bg-[#6d5bd0] text-[9px] font-semibold text-white">
                {index + 1}
              </span>
              <Card className="p-0">
                <div className="flex flex-wrap items-center gap-2 px-3 py-2">
                  <button type="button" onClick={() => setOpenId(open ? "" : step.id)} className="min-w-0 flex-1 text-start" aria-expanded={open}>
                    <span className="block text-[12.5px] font-semibold text-foreground">
                      {tx("sequence.day", "Day {{n}}", { n: days[index] })}
                      {" · "}
                      {tx("sequence.followUp", "Follow-up {{n}}", { n: index + 1 })}
                    </span>
                    <span className="block truncate text-[11.5px] text-muted-foreground">
                      {conditionLabel(tx, step.condition)}
                      {" · "}
                      {followUpSubject(step, firstSubject) || tx("sequence.noSubject", "(no subject)")}
                    </span>
                  </button>
                  <Button type="button" variant="ghost" size="icon" className="h-8 w-8" disabled={index === 0} onClick={() => update(moveStep(steps, index, -1))} aria-label={tx("sequence.up", "Move up")}>
                    <ArrowUp className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                  <Button type="button" variant="ghost" size="icon" className="h-8 w-8" disabled={index === steps.length - 1} onClick={() => update(moveStep(steps, index, 1))} aria-label={tx("sequence.down", "Move down")}>
                    <ArrowDown className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    disabled={steps.length >= MAX_SEQUENCE_STEPS}
                    onClick={() => {
                      const copy = { ...newStep(step), subject: step.subject, html: step.html };
                      update([...steps.slice(0, index + 1), copy, ...steps.slice(index + 1)]);
                      setOpenId(copy.id);
                    }}
                    aria-label={tx("sequence.duplicate", "Duplicate")}
                  >
                    <Copy className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                  <Button type="button" variant="ghost" size="icon" className="h-8 w-8 text-[#c2410c]" onClick={() => update(steps.filter((_, i) => i !== index))} aria-label={tx("sequence.delete", "Delete follow-up {{n}}", { n: index + 1 })}>
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </div>
                {open ? (
                  <div className="space-y-3 border-t border-border/60 p-3">
                    <div className="grid gap-3 sm:grid-cols-3">
                      <Field label={tx("sequence.delay", "Wait (days)")} hint={tx("sequence.delayHint", "After the previous email.")}>
                        <div className="relative">
                          <Clock className="pointer-events-none absolute start-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
                          <Input
                            type="number"
                            min={1}
                            max={60}
                            value={step.delay_days}
                            onChange={(event) => patch(index, { delay_days: Math.max(1, Math.min(60, Number(event.target.value) || 1)) })}
                            className="ps-8"
                            aria-label={tx("sequence.delayFor", "Wait before follow-up {{n}}", { n: index + 1 })}
                          />
                        </div>
                      </Field>
                      <Field label={tx("sequence.condition", "Send it")} className="sm:col-span-2">
                        <select
                          className={SELECT_CLASS}
                          value={step.condition}
                          onChange={(event) => patch(index, { condition: event.target.value as SequenceStep["condition"] })}
                          aria-label={tx("sequence.conditionFor", "Condition of follow-up {{n}}", { n: index + 1 })}
                        >
                          {(["no_reply", "no_open", "always"] as const).map((value) => (
                            <option key={value} value={value}>
                              {conditionLabel(tx, value)}
                            </option>
                          ))}
                        </select>
                      </Field>
                    </div>
                    <Field label={tx("sequence.subject", "Subject (optional)")}>
                      <Input
                        value={step.subject}
                        maxLength={200}
                        onChange={(event) => patch(index, { subject: event.target.value })}
                        placeholder={tx("sequence.subjectPlaceholder", "Re: {{subject}}, same thread", { subject: firstSubject || "..." })}
                      />
                    </Field>
                    <EmailEditor
                      value={step.html}
                      onChange={(html) => patch(index, { html })}
                      tx={tx}
                      extraTags={attrKeys}
                      placeholder={tx("sequence.bodyPlaceholder", "Short and personal: a reminder, a new angle, a question...")}
                    />
                  </div>
                ) : null}
              </Card>
            </li>
          );
        })}
      </ol>
      {steps.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">{tx("sequence.empty", "No follow-up: only the first email is sent.")}</p>
      ) : null}
    </section>
  );
}

/** Campaign detail: what each follow-up did. */
export function SequencePanel({ campaign, tx }: { campaign: Campaign; tx: Tx }) {
  const steps = campaign.steps ?? [];
  if (campaign.channel === "whatsapp" || steps.length === 0) return null;
  const days = sequenceDays(steps);
  const byStep = new Map<number, StepStats>((campaign.stats.steps ?? []).map((row) => [row.step, row]));
  return (
    <Card className="p-4" testId="sequence-panel">
      <p className="mb-2 text-[13px] font-semibold text-foreground">{tx("sequence.panel", "Sequence")}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead className="text-start text-[11.5px] text-muted-foreground">
            <tr>
              <th className="py-1 text-start font-medium">{tx("sequence.step", "Step")}</th>
              <th className="px-2 py-1 text-start font-medium">{tx("sequence.condition", "Send it")}</th>
              <th className="px-2 py-1 text-end font-medium">{tx("detail.sent", "Sent")}</th>
              <th className="px-2 py-1 text-end font-medium">{tx("detail.opened", "Opened")}</th>
              <th className="px-2 py-1 text-end font-medium">{tx("sequence.skipped", "Skipped")}</th>
              <th className="px-2 py-1 text-end font-medium">{tx("detail.failed", "Failed / bounced")}</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-border/40">
              <td className="py-1.5">{tx("sequence.firstDay0", "Day 0 · first email")}</td>
              <td className="px-2 py-1.5 text-muted-foreground">-</td>
              <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(campaign.stats.sent)}</td>
              <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(campaign.stats.opened)}</td>
              <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(campaign.stats.skipped)}</td>
              <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(campaign.stats.failed + campaign.stats.bounced)}</td>
            </tr>
            {steps.map((step, index) => {
              const row = byStep.get(index + 1);
              return (
                <tr key={step.id} className="border-t border-border/40" data-testid="sequence-panel-row">
                  <td className="py-1.5">
                    {tx("sequence.dayStep", "Day {{day}} · follow-up {{n}}", { day: days[index], n: index + 1 })}
                  </td>
                  <td className="px-2 py-1.5 text-muted-foreground">{conditionLabel(tx, step.condition)}</td>
                  <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(row?.sent ?? 0)}</td>
                  <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(row?.opened ?? 0)}</td>
                  <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(row?.skipped ?? 0)}</td>
                  <td className="px-2 py-1.5 text-end tabular-nums">{formatNumber(row?.failed ?? 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11.5px] text-muted-foreground">
        {tx("sequence.skipLegend", "Skipped: the person replied, unsubscribed or bounced (or opened, for an 'If not opened' step). Follow-ups wait for their day and the sending window.")}
      </p>
    </Card>
  );
}

/** Campaign detail: compare the subjects. */
export function AbResultsPanel({ campaign, tx }: { campaign: Campaign; tx: Tx }) {
  const variants = campaign.stats.variants ?? [];
  if (!(campaign.subject_variants?.length) || variants.length === 0) return null;
  const winner = abWinner(variants);
  return (
    <Card className="p-4" testId="ab-panel">
      <div className="mb-2 flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-[#6d5bd0]" aria-hidden />
        <p className="flex-1 text-[13px] font-semibold text-foreground">{tx("ab.results", "A/B results")}</p>
        {!winner ? (
          <span className="text-[11.5px] text-muted-foreground" data-testid="ab-no-winner">
            {tx("ab.notEnough", "Not enough data yet ({{n}} sends per subject needed).", { n: AB_MIN_SENDS })}
          </span>
        ) : null}
      </div>
      <div className="space-y-2">
        {variants.map((variant) => {
          const isWinner = winner?.variant === variant.variant;
          return (
            <div
              key={variant.variant}
              className={cn(
                "flex flex-wrap items-center gap-3 rounded-xl border px-3 py-2",
                isWinner ? "border-[#1f9d74]/50 bg-[#1f9d74]/[0.06]" : "border-border/60",
              )}
              data-testid="ab-variant"
            >
              <span className={cn("grid h-6 w-6 place-items-center rounded-full text-[11px] font-semibold", isWinner ? "bg-[#1f9d74] text-white" : "bg-muted text-foreground")}>
                {variant.variant}
              </span>
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">{variant.subject}</span>
              <span className="text-[12px] tabular-nums text-muted-foreground">{tx("ab.sent", "{{n}} sent", { n: formatNumber(variant.sent) })}</span>
              <span className="text-[12px] tabular-nums">{tx("ab.openRate", "Opens {{rate}}", { rate: formatRate(variant.open_rate) })}</span>
              <span className="text-[12px] font-medium tabular-nums">{tx("ab.replyRate", "Replies {{rate}}", { rate: formatRate(variant.reply_rate) })}</span>
              {isWinner ? (
                <span className="inline-flex items-center gap-1 text-[11.5px] font-semibold text-[#1f9d74]" data-testid="ab-winner">
                  <Trophy className="h-3.5 w-3.5" aria-hidden />
                  {tx("ab.winner", "Winner")}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
