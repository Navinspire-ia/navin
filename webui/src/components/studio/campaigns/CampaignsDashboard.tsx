// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo, useState } from "react";
import { CheckCircle2, CircleAlert, Loader2, Play, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Campaign, CampaignDashboard, ReplyClass } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import {
  CampaignStatusChip,
  Card,
  formatDateTime,
  formatNumber,
  formatRate,
  REPLY_CLASSES,
  replyClassLabel,
  type Tx,
} from "./campaigns-ui";
import { CampaignFeatureChips } from "./SequenceEditor";

const SERIES = [
  { key: "sent", color: "#2f7de1" },
  { key: "opened", color: "#1f9d74" },
  { key: "clicked", color: "#d4892a" },
  { key: "replied", color: "#6d5bd0" },
] as const;

export function CampaignsDashboardView({
  data,
  tx,
  onNewCampaign,
  onOpenCampaign,
  onOpenReplies,
  onOpenSettings,
  onRunLoop,
}: {
  data: CampaignDashboard;
  tx: Tx;
  onNewCampaign: () => void;
  onOpenCampaign: (campaign: Campaign) => void;
  onOpenReplies: (classification?: ReplyClass) => void;
  onOpenSettings: () => void;
  onRunLoop: () => Promise<void>;
}) {
  const { totals, setup, loop } = data;
  const [running, setRunning] = useState(false);
  const kpis = [
    { label: tx("kpi.sent30", "Sent (30 days)"), value: formatNumber(totals.sent_30d) },
    { label: tx("kpi.openRate", "Open rate"), value: formatRate(totals.open_rate) },
    { label: tx("kpi.clickRate", "Click rate"), value: formatRate(totals.click_rate) },
    { label: tx("kpi.replyRate", "Reply rate"), value: formatRate(totals.reply_rate) },
    { label: tx("kpi.bounceRate", "Bounce rate"), value: formatRate(totals.bounce_rate) },
    {
      label: tx("kpi.contacts", "Contacts"),
      value: formatNumber(totals.contacts),
      caption: tx("kpi.subscribed", "{{count}} subscribed", { count: formatNumber(totals.subscribed) }),
    },
    {
      label: tx("kpi.newReplies", "New replies"),
      value: formatNumber(totals.replies_new),
      onClick: () => onOpenReplies(),
      highlight: totals.replies_new > 0,
    },
  ];
  const checks = [
    {
      ok: setup.email_ready,
      label: tx("setup.email", "Email sending (SMTP)"),
      fix: setup.email_hint || tx("setup.emailFix", "Settings > Channels > Email: SMTP host, user, password, sender."),
    },
    {
      ok: setup.imap_ready,
      label: tx("setup.imap", "Reply inbox (IMAP)"),
      fix: tx("setup.imapFix", "Settings > Channels > Email: IMAP host, user and password, so replies are read and classified."),
    },
    {
      ok: setup.whatsapp_ready,
      label: tx("setup.whatsapp", "WhatsApp Cloud API"),
      fix: tx("setup.whatsappFix", "Campaigns settings: phone number id, WhatsApp Business account id and access token from Meta."),
    },
    ...(loop.wa_webhook_error
      ? [{ ok: false, label: tx("setup.waWebhook", "WhatsApp replies (webhook)"), fix: loop.wa_webhook_error }]
      : []),
    {
      ok: setup.tracking_ready,
      label: tx("setup.tracking", "Open and click tracking"),
      fix: tx("setup.trackingFix", "Campaigns settings: a public URL that reaches this Navin. Sending works without it."),
    },
  ];
  const classTotal = REPLY_CLASSES.reduce((sum, key) => sum + (data.replies_by_class[key] ?? 0), 0);

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-foreground">{tx("dashboard.title", "Campaigns overview")}</h2>
          <p className="text-[12.5px] text-muted-foreground">{tx("dashboard.subtitle", "Email and WhatsApp campaigns, replies and follow-up in one place.")}</p>
        </div>
        <Button size="sm" className="rounded-full" onClick={onNewCampaign}>
          <Plus className="me-1.5 h-4 w-4" aria-hidden />
          {tx("campaigns.new", "New campaign")}
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
        {kpis.map((kpi) => (
          <button
            key={kpi.label}
            type="button"
            disabled={!kpi.onClick}
            onClick={kpi.onClick}
            className={cn(
              "rounded-2xl border border-border/60 bg-card/80 px-3 py-3 text-start shadow-sm transition-colors enabled:hover:bg-muted/40",
              kpi.highlight && "border-[#6d5bd0]/40 bg-[#6d5bd0]/[0.06]",
            )}
          >
            <span className="block text-[11.5px] text-muted-foreground">{kpi.label}</span>
            <span className="mt-1 block text-[22px] font-semibold tabular-nums tracking-[-0.02em] text-foreground">{kpi.value}</span>
            {kpi.caption ? <span className="block text-[11px] text-muted-foreground">{kpi.caption}</span> : null}
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4 lg:col-span-2">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-[13px] font-semibold text-foreground">{tx("dashboard.timeline", "Last 30 days")}</h3>
            <div className="flex flex-wrap gap-3 text-[11.5px] text-muted-foreground">
              {SERIES.map((serie) => (
                <span key={serie.key} className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: serie.color }} />
                  {tx(`series.${serie.key}`, serie.key)}
                </span>
              ))}
            </div>
          </div>
          <TimelineChart rows={data.timeline} tx={tx} />
        </Card>

        <Card className="p-4">
          <h3 className="mb-3 text-[13px] font-semibold text-foreground">{tx("dashboard.replies", "Replies by type")}</h3>
          {classTotal === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">{tx("dashboard.noReplies", "No reply yet. Replies are read from your inbox and classified automatically.")}</p>
          ) : (
            <ul className="space-y-2">
              {REPLY_CLASSES.filter((key) => (data.replies_by_class[key] ?? 0) > 0).map((key) => {
                const count = data.replies_by_class[key] ?? 0;
                return (
                  <li key={key}>
                    <button type="button" onClick={() => onOpenReplies(key)} className="w-full text-start">
                      <div className="flex items-center justify-between text-[12.5px]">
                        <span className="text-foreground/85">{replyClassLabel(tx, key)}</span>
                        <span className="tabular-nums text-muted-foreground">{count}</span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-[#6d5bd0]" style={{ width: `${Math.max(4, (count / classTotal) * 100)}%` }} />
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4 lg:col-span-2">
          <h3 className="mb-2 text-[13px] font-semibold text-foreground">{tx("dashboard.recent", "Recent campaigns")}</h3>
          {data.recent_campaigns.length === 0 ? (
            <p className="py-6 text-center text-[12.5px] text-muted-foreground">{tx("dashboard.noCampaigns", "No campaign yet. Create one to reach a list by email or WhatsApp.")}</p>
          ) : (
            <ul className="divide-y divide-border/60">
              {data.recent_campaigns.map((campaign) => (
                <li key={campaign.id}>
                  <button type="button" onClick={() => onOpenCampaign(campaign)} className="flex w-full items-center gap-3 py-2.5 text-start hover:bg-muted/30">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium text-foreground">{campaign.name}</span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {campaign.channel === "whatsapp" ? "WhatsApp" : tx("channel.email", "Email")}
                        {" · "}
                        {tx("dashboard.recipients", "{{count}} recipients", { count: campaign.stats.recipients })}
                        {campaign.launched_at ? ` · ${formatDateTime(campaign.launched_at)}` : ""}
                      </span>
                    </span>
                    <span className="hidden sm:block">
                      <CampaignFeatureChips campaign={campaign} tx={tx} />
                    </span>
                    <span className="hidden text-[11.5px] tabular-nums text-muted-foreground sm:block">
                      {formatRate(campaign.stats.open_rate)} / {formatRate(campaign.stats.reply_rate)}
                    </span>
                    <CampaignStatusChip status={campaign.status} tx={tx} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="p-4">
          <h3 className="mb-2 text-[13px] font-semibold text-foreground">{tx("dashboard.setup", "Setup")}</h3>
          <ul className="space-y-2.5">
            {checks.map((check) => (
              <li key={check.label} className="flex gap-2">
                {check.ok ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#1f9d74]" aria-hidden />
                ) : (
                  <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-[#d4892a]" aria-hidden />
                )}
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-medium text-foreground">{check.label}</span>
                  {!check.ok ? <span className="block text-[11.5px] leading-snug text-muted-foreground">{check.fix}</span> : null}
                </span>
              </li>
            ))}
          </ul>
          <Button size="sm" variant="outline" className="mt-3 w-full rounded-full" onClick={onOpenSettings}>
            {tx("dashboard.openSettings", "Campaign settings")}
          </Button>
          <div className="mt-4 border-t border-border/60 pt-3 text-[11.5px] text-muted-foreground">
            <p className="font-medium text-foreground/85">{tx("loop.title", "Sending loop")}</p>
            <p>{tx("loop.lastTick", "Last run: {{when}}", { when: loop.last_tick ? formatDateTime(loop.last_tick) : tx("loop.never", "never") })}</p>
            <p>{tx("loop.lastPoll", "Inbox checked: {{when}}", { when: loop.last_imap_poll ? formatDateTime(loop.last_imap_poll) : tx("loop.never", "never") })}</p>
            <p>{tx("loop.activity", "{{sending}} sending, {{scheduled}} scheduled", { sending: loop.sending, scheduled: loop.scheduled })}</p>
            <Button
              size="sm"
              variant="ghost"
              className="mt-1.5 h-7 rounded-full px-2.5 text-[12px]"
              disabled={running}
              onClick={() => {
                setRunning(true);
                void onRunLoop().finally(() => setRunning(false));
              }}
            >
              {running ? <Loader2 className="me-1 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Play className="me-1 h-3.5 w-3.5" aria-hidden />}
              {tx("loop.runNow", "Run now")}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

/** Four lines over 30 days: plain SVG, readable in light and dark. */
export function TimelineChart({ rows, tx }: { rows: CampaignDashboard["timeline"]; tx: Tx }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 640;
  const height = 180;
  const pad = { top: 10, right: 8, bottom: 22, left: 30 };
  const max = useMemo(
    () => Math.max(1, ...rows.flatMap((row) => SERIES.map((serie) => row[serie.key]))),
    [rows],
  );
  if (!rows.length) {
    return <p className="py-10 text-center text-[12.5px] text-muted-foreground">{tx("dashboard.noData", "Nothing sent in the last 30 days.")}</p>;
  }
  const x = (index: number) => pad.left + (index * (width - pad.left - pad.right)) / Math.max(1, rows.length - 1);
  const y = (value: number) => pad.top + (1 - value / max) * (height - pad.top - pad.bottom);
  const ticks = [0, Math.round(max / 2), max];
  const active = hover !== null ? rows[hover] : null;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={tx("dashboard.timeline", "Last 30 days")}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} stroke="currentColor" className="text-border" strokeDasharray="3 4" />
            <text x={pad.left - 6} y={y(tick) + 3} textAnchor="end" className="fill-muted-foreground text-[9px]">
              {tick}
            </text>
          </g>
        ))}
        {[0, Math.floor((rows.length - 1) / 2), rows.length - 1].map((index) => (
          <text key={index} x={x(index)} y={height - 6} textAnchor="middle" className="fill-muted-foreground text-[9px]">
            {rows[index]?.date.slice(5)}
          </text>
        ))}
        {SERIES.map((serie) => (
          <polyline
            key={serie.key}
            fill="none"
            stroke={serie.color}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            points={rows.map((row, index) => `${x(index)},${y(row[serie.key])}`).join(" ")}
          />
        ))}
        {hover !== null ? <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={height - pad.bottom} stroke="currentColor" className="text-foreground/30" /> : null}
        {rows.map((row, index) => (
          <rect
            key={row.date}
            x={x(index) - (width - pad.left - pad.right) / rows.length / 2}
            y={pad.top}
            width={(width - pad.left - pad.right) / rows.length}
            height={height - pad.top - pad.bottom}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>
      {active ? (
        <div className="pointer-events-none absolute end-2 top-0 rounded-lg border border-border/70 bg-popover px-2.5 py-1.5 text-[11.5px] shadow">
          <p className="font-medium text-foreground">{active.date}</p>
          {SERIES.map((serie) => (
            <p key={serie.key} className="flex items-center gap-1.5 text-muted-foreground">
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: serie.color }} />
              {tx(`series.${serie.key}`, serie.key)}: <span className="tabular-nums text-foreground">{active[serie.key]}</span>
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
