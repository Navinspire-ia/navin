// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Ban, Copy, Loader2, Mail, MessageCircle, Pause, Pencil, Play, Plus, Send as SendIcon, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import {
  cancelCampaign,
  deleteCampaign,
  duplicateCampaign,
  getCampaign,
  listSends,
  pauseCampaign,
  resumeCampaign,
} from "@/lib/campaigns-api";
import type { Campaign, CampaignStatus, Send, SendStatus } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import {
  CampaignStatusChip,
  campaignStatusLabel,
  Card,
  Chip,
  EmptyState,
  ErrorBanner,
  errorText,
  formatDateTime,
  formatNumber,
  formatRate,
  Loading,
  Pager,
  SELECT_CLASS,
  sendStatusLabel,
  type Tx,
} from "./campaigns-ui";
import { AbResultsPanel, CampaignFeatureChips, SequencePanel } from "./SequenceEditor";

const STATUSES: CampaignStatus[] = ["draft", "scheduled", "sending", "paused", "sent", "failed", "cancelled"];

export function CampaignsListView({
  campaigns,
  loading,
  tx,
  status,
  onStatus,
  onNew,
  onOpen,
}: {
  campaigns: Campaign[];
  loading: boolean;
  tx: Tx;
  status: CampaignStatus | "";
  onStatus: (status: CampaignStatus | "") => void;
  onNew: () => void;
  onOpen: (campaign: Campaign) => void;
}) {
  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex-1 text-[18px] font-semibold text-foreground">{tx("campaigns.title", "Campaigns")}</h2>
        <select className={cn(SELECT_CLASS, "w-44")} value={status} onChange={(event) => onStatus(event.target.value as CampaignStatus | "")} aria-label={tx("campaigns.filter", "Filter by status")}>
          <option value="">{tx("campaigns.all", "All statuses")}</option>
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {campaignStatusLabel(tx, value)}
            </option>
          ))}
        </select>
        <Button size="sm" className="rounded-full" onClick={onNew}>
          <Plus className="me-1.5 h-4 w-4" aria-hidden />
          {tx("campaigns.new", "New campaign")}
        </Button>
      </div>
      {loading && !campaigns.length ? (
        <Loading label={tx("loading", "Loading...")} />
      ) : campaigns.length === 0 ? (
        <EmptyState
          icon={<SendIcon className="h-5 w-5" aria-hidden />}
          title={tx("campaigns.empty", "No campaign yet")}
          body={tx("campaigns.emptyBody", "Pick an audience, write the message, preview it with real data, send a test, then launch or schedule.")}
          action={
            <Button size="sm" className="rounded-full" onClick={onNew}>
              {tx("campaigns.new", "New campaign")}
            </Button>
          }
        />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-[12.5px]">
            <thead className="bg-muted/30 text-[11.5px] text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-start font-medium">{tx("campaigns.name", "Name")}</th>
                <th className="px-2 py-2 text-start font-medium">{tx("campaigns.status", "Status")}</th>
                <th className="hidden px-2 py-2 text-end font-medium md:table-cell">{tx("campaigns.recipients", "Recipients")}</th>
                <th className="hidden px-2 py-2 text-end font-medium md:table-cell">{tx("kpi.openRate", "Open rate")}</th>
                <th className="hidden px-2 py-2 text-end font-medium lg:table-cell">{tx("kpi.clickRate", "Click rate")}</th>
                <th className="hidden px-2 py-2 text-end font-medium md:table-cell">{tx("kpi.replyRate", "Reply rate")}</th>
                <th className="hidden px-3 py-2 text-end font-medium lg:table-cell">{tx("campaigns.when", "When")}</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((campaign) => (
                <tr key={campaign.id} className="cursor-pointer border-t border-border/40 hover:bg-muted/25" onClick={() => onOpen(campaign)}>
                  <td className="px-3 py-2.5">
                    <span className="flex items-center gap-2">
                      {campaign.channel === "whatsapp" ? <MessageCircle className="h-3.5 w-3.5 text-[#1f9d74]" aria-hidden /> : <Mail className="h-3.5 w-3.5 text-[#2f7de1]" aria-hidden />}
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-foreground">{campaign.name}</span>
                        {campaign.subject ? <span className="block max-w-[22rem] truncate text-[11.5px] text-muted-foreground">{campaign.subject}</span> : null}
                        <CampaignFeatureChips campaign={campaign} tx={tx} />
                      </span>
                    </span>
                  </td>
                  <td className="px-2 py-2.5">
                    <CampaignStatusChip status={campaign.status} tx={tx} />
                  </td>
                  <td className="hidden px-2 py-2.5 text-end tabular-nums md:table-cell">{formatNumber(campaign.stats.recipients)}</td>
                  <td className="hidden px-2 py-2.5 text-end tabular-nums md:table-cell">{formatRate(campaign.stats.open_rate)}</td>
                  <td className="hidden px-2 py-2.5 text-end tabular-nums lg:table-cell">{formatRate(campaign.stats.click_rate)}</td>
                  <td className="hidden px-2 py-2.5 text-end tabular-nums md:table-cell">{formatRate(campaign.stats.reply_rate)}</td>
                  <td className="hidden px-3 py-2.5 text-end text-muted-foreground lg:table-cell">
                    {formatDateTime(campaign.launched_at || campaign.schedule_at || campaign.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

const SEND_PAGE = 50;

export function CampaignDetailView({
  token,
  campaignId,
  tx,
  onBack,
  onEdit,
  onChanged,
  onOpenReplies,
}: {
  token: string;
  campaignId: string;
  tx: Tx;
  onBack: () => void;
  onEdit: (campaign: Campaign) => void;
  onChanged: (campaign: Campaign | null) => void;
  onOpenReplies: (campaignId: string) => void;
}) {
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [sends, setSends] = useState<Send[]>([]);
  const [sendTotal, setSendTotal] = useState(0);
  const [sendStatus, setSendStatus] = useState<SendStatus | "">("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);

  const load = useCallback(async () => {
    try {
      const [next, page] = await Promise.all([
        getCampaign(token, campaignId),
        listSends(token, campaignId, { status: sendStatus, offset, limit: SEND_PAGE }),
      ]);
      setCampaign(next);
      setSends(page.items);
      setSendTotal(page.total);
      setError("");
    } catch (err) {
      setError(errorText(err));
    }
  }, [campaignId, offset, sendStatus, token]);

  useEffect(() => {
    void load();
  }, [load]);
  // A sending campaign moves on its own: follow it.
  useEffect(() => {
    if (!campaign || !["sending", "scheduled"].includes(campaign.status)) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [campaign, load]);

  const act = async (work: () => Promise<Campaign | null>) => {
    setBusy(true);
    setError("");
    try {
      const next = await work();
      onChanged(next);
      if (next) await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  if (!campaign) {
    return error ? (
      <div className="p-6">
        <ErrorBanner message={error} />
      </div>
    ) : (
      <Loading label={tx("loading", "Loading...")} />
    );
  }
  const stats = campaign.stats;
  const tiles = [
    { label: tx("detail.recipients", "Recipients"), value: formatNumber(stats.recipients) },
    { label: tx("detail.sent", "Sent"), value: formatNumber(stats.sent), caption: stats.queued ? tx("detail.queued", "{{count}} queued", { count: stats.queued }) : "" },
    { label: tx("detail.opened", "Opened"), value: formatNumber(stats.opened), caption: formatRate(stats.open_rate) },
    { label: tx("detail.clicked", "Clicked"), value: formatNumber(stats.clicked), caption: formatRate(stats.click_rate) },
    { label: tx("detail.replied", "Replied"), value: formatNumber(stats.replied), caption: formatRate(stats.reply_rate), onClick: () => onOpenReplies(campaign.id) },
    { label: tx("detail.failed", "Failed / bounced"), value: formatNumber(stats.failed + stats.bounced) },
    { label: tx("detail.unsubscribed", "Unsubscribed"), value: formatNumber(stats.unsubscribed) },
  ];
  const editable = campaign.status === "draft" || campaign.status === "paused";
  const progress = stats.recipients ? Math.min(100, ((stats.sent + stats.failed + stats.skipped) / stats.recipients) * 100) : 0;

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={onBack} className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={tx("detail.back", "Back to campaigns")}>
          <ArrowLeft className="h-4 w-4 rtl:-scale-x-100" aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[17px] font-semibold text-foreground">{campaign.name}</h2>
          <p className="text-[12px] text-muted-foreground">
            {campaign.channel === "whatsapp" ? "WhatsApp" : tx("channel.email", "Email")}
            {campaign.schedule_at && campaign.status === "scheduled" ? ` · ${tx("detail.scheduledFor", "scheduled for {{when}}", { when: formatDateTime(campaign.schedule_at) })}` : ""}
            {campaign.launched_at ? ` · ${tx("detail.launched", "launched {{when}}", { when: formatDateTime(campaign.launched_at) })}` : ""}
            {campaign.finished_at ? ` · ${tx("detail.finished", "finished {{when}}", { when: formatDateTime(campaign.finished_at) })}` : ""}
          </p>
        </div>
        <CampaignStatusChip status={campaign.status} tx={tx} />
        {editable ? (
          <Button size="sm" variant="outline" className="h-8 rounded-full" onClick={() => onEdit(campaign)}>
            <Pencil className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("detail.edit", "Edit")}
          </Button>
        ) : null}
        {campaign.status === "sending" || campaign.status === "scheduled" ? (
          <Button size="sm" variant="outline" className="h-8 rounded-full" disabled={busy} onClick={() => void act(() => pauseCampaign(token, campaign.id))}>
            <Pause className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("detail.pause", "Pause")}
          </Button>
        ) : null}
        {campaign.status === "paused" ? (
          <Button size="sm" className="h-8 rounded-full" disabled={busy} onClick={() => void act(() => resumeCampaign(token, campaign.id))}>
            <Play className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("detail.resume", "Resume")}
          </Button>
        ) : null}
        {["sending", "scheduled", "paused"].includes(campaign.status) ? (
          <Button size="sm" variant="ghost" className="h-8 rounded-full text-[#c2410c]" disabled={busy} onClick={() => void act(() => cancelCampaign(token, campaign.id))}>
            <Ban className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("detail.cancel", "Cancel sending")}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" className="h-8 rounded-full" disabled={busy} onClick={() => void act(async () => { const copy = await duplicateCampaign(token, campaign.id); onEdit(copy); return copy; })}>
          <Copy className="me-1.5 h-3.5 w-3.5" aria-hidden />
          {tx("detail.duplicate", "Duplicate")}
        </Button>
        {campaign.status !== "sending" ? (
          <Button size="sm" variant="ghost" className="h-8 rounded-full text-[#c2410c]" disabled={busy} onClick={() => setPendingDelete(true)}>
            <Trash2 className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("detail.delete", "Delete")}
          </Button>
        ) : null}
        {busy ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden /> : null}
      </div>
      <ErrorBanner message={error || campaign.last_error} onDismiss={error ? () => setError("") : undefined} />
      {campaign.status === "sending" ? (
        <div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-[#2f7de1] transition-[width] duration-500" style={{ width: `${progress}%` }} />
          </div>
          <p className="mt-1 text-[11.5px] text-muted-foreground">{tx("detail.progress", "{{pct}}% sent", { pct: progress.toFixed(0) })}</p>
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
        {tiles.map((tile) => (
          <button key={tile.label} type="button" disabled={!tile.onClick} onClick={tile.onClick} className="rounded-2xl border border-border/60 bg-card/80 px-3 py-2.5 text-start enabled:hover:bg-muted/40">
            <span className="block text-[11.5px] text-muted-foreground">{tile.label}</span>
            <span className="block text-[19px] font-semibold tabular-nums text-foreground">{tile.value}</span>
            {tile.caption ? <span className="block text-[11px] text-muted-foreground">{tile.caption}</span> : null}
          </button>
        ))}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <SequencePanel campaign={campaign} tx={tx} />
        <AbResultsPanel campaign={campaign} tx={tx} />
      </div>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-3 py-2">
          <p className="flex-1 text-[13px] font-semibold text-foreground">{tx("detail.sends", "Recipients")}</p>
          <select className={cn(SELECT_CLASS, "h-8 w-40")} value={sendStatus} onChange={(event) => { setOffset(0); setSendStatus(event.target.value as SendStatus | ""); }} aria-label={tx("detail.filterSends", "Filter recipients")}>
            <option value="">{tx("detail.allSends", "All")}</option>
            {(["queued", "sent", "failed", "skipped"] as SendStatus[]).map((value) => (
              <option key={value} value={value}>
                {sendStatusLabel(tx, value)}
              </option>
            ))}
          </select>
        </div>
        {sends.length === 0 ? (
          <p className="px-3 py-8 text-center text-[12.5px] text-muted-foreground">{tx("detail.noSends", "Nothing sent yet.")}</p>
        ) : (
          <table className="w-full text-[12.5px]">
            <thead className="text-[11.5px] text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-start font-medium">{tx("contacts.name", "Name")}</th>
                <th className="px-2 py-2 text-start font-medium">{tx("detail.address", "Address")}</th>
                <th className="px-2 py-2 text-start font-medium">{tx("campaigns.status", "Status")}</th>
                <th className="hidden px-2 py-2 text-start font-medium md:table-cell">{tx("detail.activity", "Activity")}</th>
                <th className="hidden px-3 py-2 text-end font-medium lg:table-cell">{tx("detail.sentAt", "Sent")}</th>
              </tr>
            </thead>
            <tbody>
              {sends.map((send) => (
                <tr key={send.id} className="border-t border-border/40">
                  <td className="max-w-[12rem] truncate px-3 py-2">{send.contact_name || "-"}</td>
                  <td className="max-w-[14rem] truncate px-2 py-2 text-muted-foreground">{send.address}</td>
                  <td className="px-2 py-2">
                    <Chip tone={send.status === "sent" ? "green" : send.status === "failed" ? "red" : send.status === "queued" ? "blue" : "neutral"}>{sendStatusLabel(tx, send.status)}</Chip>
                    {send.error ? <span className="block max-w-[16rem] truncate text-[11px] text-[#c2410c]" title={send.error}>{send.error}</span> : null}
                  </td>
                  <td className="hidden px-2 py-2 md:table-cell">
                    <span className="flex flex-wrap gap-1">
                      {send.open_count ? <Chip tone="green">{tx("detail.opens", "{{count}} opens", { count: send.open_count })}</Chip> : null}
                      {send.click_count ? <Chip tone="amber">{tx("detail.clicks", "{{count}} clicks", { count: send.click_count })}</Chip> : null}
                      {send.replied_at ? <Chip tone="violet">{tx("detail.repliedChip", "replied")}</Chip> : null}
                      {send.bounced ? <Chip tone="red">{tx("detail.bounced", "bounced")}</Chip> : null}
                    </span>
                  </td>
                  <td className="hidden px-3 py-2 text-end text-muted-foreground lg:table-cell">{formatDateTime(send.sent_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Pager offset={offset} limit={SEND_PAGE} total={sendTotal} onChange={setOffset} tx={tx} />
      </Card>

      <ConfirmDialog
        open={pendingDelete}
        title={tx("detail.deleteTitle", "Delete this campaign?")}
        description={tx("detail.deleteHelp", "Its statistics and recipient history are removed. Contacts stay.")}
        confirmLabel={tx("detail.delete", "Delete")}
        onCancel={() => setPendingDelete(false)}
        onConfirm={() => {
          setPendingDelete(false);
          void act(async () => {
            await deleteCampaign(token, campaign.id);
            onBack();
            return null;
          });
        }}
      />
    </div>
  );
}
