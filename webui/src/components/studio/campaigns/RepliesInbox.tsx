// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from "react";
import { Ban, CheckCheck, Inbox, Loader2, Mail, MessageCircle, Save, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { listReplies, sendReply, updateReply } from "@/lib/campaigns-api";
import type { Campaign, Reply, ReplyClass, ReplyStatus } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import {
  Chip,
  EmptyState,
  ErrorBanner,
  errorText,
  Field,
  formatDateTime,
  Loading,
  Notice,
  Pager,
  REPLY_CLASSES,
  REPLY_STATUSES,
  ReplyClassChip,
  replyClassLabel,
  replyStatusLabel,
  SELECT_CLASS,
  type Tx,
} from "./campaigns-ui";

const PAGE = 40;

export function RepliesInbox({
  token,
  tx,
  campaigns,
  teammates,
  initialClassification = "",
  initialCampaign = "",
  onChanged,
}: {
  token: string;
  tx: Tx;
  campaigns: Campaign[];
  /** Collaboration Mode teammates who can own a follow-up. */
  teammates: string[];
  initialClassification?: ReplyClass | "";
  initialCampaign?: string;
  onChanged?: () => void;
}) {
  const [status, setStatus] = useState<ReplyStatus | "">("new");
  const [classification, setClassification] = useState<ReplyClass | "">(initialClassification);
  const [campaignId, setCampaignId] = useState(initialCampaign);
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Reply[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState("");

  useEffect(() => setClassification(initialClassification), [initialClassification]);
  useEffect(() => setCampaignId(initialCampaign), [initialCampaign]);
  useEffect(() => {
    if (initialClassification || initialCampaign) setStatus("");
  }, [initialCampaign, initialClassification]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listReplies(token, { status, classification, campaign_id: campaignId, offset, limit: PAGE });
      setRows(page.items);
      setTotal(page.total);
      setError("");
      setOpenId((current) => (current && page.items.some((row) => row.id === current) ? current : page.items[0]?.id ?? ""));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [campaignId, classification, offset, status, token]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => setOffset(0), [status, classification, campaignId]);

  const open = rows.find((row) => row.id === openId) ?? null;
  const replace = (reply: Reply) => {
    setRows((prev) => prev.map((row) => (row.id === reply.id ? reply : row)));
    onChanged?.();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/60 p-3 md:px-4">
        <h2 className="flex-1 text-[16px] font-semibold text-foreground">{tx("replies.title", "Replies")}</h2>
        <select className={cn(SELECT_CLASS, "h-8 w-40")} value={status} onChange={(event) => setStatus(event.target.value as ReplyStatus | "")} aria-label={tx("replies.status", "Status")}>
          <option value="">{tx("replies.anyStatus", "Any status")}</option>
          {REPLY_STATUSES.map((value) => (
            <option key={value} value={value}>
              {replyStatusLabel(tx, value)}
            </option>
          ))}
        </select>
        <select className={cn(SELECT_CLASS, "h-8 w-44")} value={classification} onChange={(event) => setClassification(event.target.value as ReplyClass | "")} aria-label={tx("replies.classification", "Type")}>
          <option value="">{tx("replies.anyClass", "Any type")}</option>
          {REPLY_CLASSES.map((value) => (
            <option key={value} value={value}>
              {replyClassLabel(tx, value)}
            </option>
          ))}
        </select>
        <select className={cn(SELECT_CLASS, "h-8 w-48")} value={campaignId} onChange={(event) => setCampaignId(event.target.value)} aria-label={tx("replies.campaign", "Campaign")}>
          <option value="">{tx("replies.anyCampaign", "All campaigns")}</option>
          {campaigns.map((campaign) => (
            <option key={campaign.id} value={campaign.id}>
              {campaign.name}
            </option>
          ))}
        </select>
      </div>
      {error ? <div className="px-3 pt-3"><ErrorBanner message={error} onDismiss={() => setError("")} /></div> : null}
      <div className="flex min-h-0 flex-1">
        <div className="flex w-full min-w-0 flex-col border-e border-border/60 md:w-80 md:shrink-0">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {loading && !rows.length ? (
              <Loading label={tx("loading", "Loading...")} />
            ) : rows.length === 0 ? (
              <EmptyState icon={<Inbox className="h-5 w-5" aria-hidden />} title={tx("replies.empty", "No reply here")} body={tx("replies.emptyBody", "Answers to your campaigns are read from your inbox, classified, and land here.")} />
            ) : (
              <ul>
                {rows.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => setOpenId(row.id)}
                      className={cn("block w-full border-b border-border/40 px-3 py-2.5 text-start", row.id === openId ? "bg-sidebar-accent" : "hover:bg-muted/30")}
                    >
                      <span className="flex items-center gap-2">
                        {row.channel === "whatsapp" ? (
                          <MessageCircle className="h-3.5 w-3.5 shrink-0 text-[#1f9d74]" aria-label="WhatsApp" />
                        ) : (
                          <Mail className="h-3.5 w-3.5 shrink-0 text-[#2f7de1]" aria-label={tx("channel.email", "Email")} />
                        )}
                        <span className={cn("min-w-0 flex-1 truncate text-[13px]", row.status === "new" ? "font-semibold text-foreground" : "text-foreground/85")}>
                          {row.contact_name || row.from_addr}
                        </span>
                        <span className="shrink-0 text-[10.5px] text-muted-foreground">{formatDateTime(row.received_at)}</span>
                      </span>
                      <span className="block truncate text-[12px] text-muted-foreground">{row.subject}</span>
                      <span className="mt-1 flex flex-wrap gap-1">
                        <ReplyClassChip value={row.classification} tx={tx} />
                        {row.status !== "new" ? <Chip>{replyStatusLabel(tx, row.status)}</Chip> : null}
                        {row.assigned_to ? <Chip tone="violet">{row.assigned_to}</Chip> : null}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <Pager offset={offset} limit={PAGE} total={total} onChange={setOffset} tx={tx} />
        </div>
        <div className="hidden min-w-0 flex-1 overflow-y-auto md:block">
          {open ? <ReplyDetail key={open.id} token={token} tx={tx} reply={open} teammates={teammates} onChanged={replace} /> : null}
        </div>
      </div>
      {open ? (
        <div className="max-h-[60vh] overflow-y-auto border-t border-border/60 md:hidden">
          <ReplyDetail key={`m-${open.id}`} token={token} tx={tx} reply={open} teammates={teammates} onChanged={replace} />
        </div>
      ) : null}
    </div>
  );
}

/** WhatsApp only accepts free text within 24 hours of the customer's message. */
export function whatsappWindowEnd(reply: Pick<Reply, "channel" | "received_at">): Date | null {
  if (reply.channel !== "whatsapp" || !reply.received_at) return null;
  const received = new Date(reply.received_at);
  if (Number.isNaN(received.getTime())) return null;
  return new Date(received.getTime() + 24 * 3600 * 1000);
}

export function ReplyDetail({
  token,
  tx,
  reply,
  teammates,
  onChanged,
}: {
  token: string;
  tx: Tx;
  reply: Reply;
  teammates: string[];
  onChanged: (reply: Reply) => void;
}) {
  const [draft, setDraft] = useState(reply.draft_reply);
  const [subject, setSubject] = useState(reply.subject.toLowerCase().startsWith("re:") ? reply.subject : `Re: ${reply.subject}`);
  const [brief, setBrief] = useState(reply.handoff_brief);
  const [assigned, setAssigned] = useState(reply.assigned_to);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const windowEnd = whatsappWindowEnd(reply);
  const windowClosed = windowEnd !== null && windowEnd.getTime() <= Date.now();
  const isWhatsApp = reply.channel === "whatsapp";

  const run = async (label: string, work: () => Promise<Reply>, done: string) => {
    setBusy(label);
    setError("");
    try {
      const next = await work();
      onChanged(next);
      setNotice(done);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="space-y-4 p-4 md:p-5">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold text-foreground">{reply.contact_name || reply.from_addr}</p>
          <p className="truncate text-[12px] text-muted-foreground">
            {reply.from_addr}
            {reply.campaign_name ? ` · ${reply.campaign_name}` : ""}
            {` · ${formatDateTime(reply.received_at)}`}
          </p>
        </div>
        <select
          className={cn(SELECT_CLASS, "h-8 w-44")}
          value={reply.classification}
          aria-label={tx("replies.classification", "Type")}
          onChange={(event) =>
            void run("class", () => updateReply(token, { id: reply.id, classification: event.target.value as ReplyClass }), tx("replies.reclassified", "Type updated."))
          }
        >
          {REPLY_CLASSES.map((value) => (
            <option key={value} value={value}>
              {replyClassLabel(tx, value)}
            </option>
          ))}
        </select>
      </div>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <Notice message={notice} />
      <div className="rounded-2xl border border-border/60 bg-muted/20 p-3">
        <p className="mb-1 text-[12.5px] font-medium text-foreground">{reply.subject}</p>
        <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-foreground/90">{reply.body}</p>
      </div>
      {reply.summary ? (
        <div className="text-[12.5px]">
          <span className="font-medium text-foreground">{tx("replies.summary", "Summary")}</span>
          <span className="text-muted-foreground">{` (${tx("replies.confidence", "confidence {{pct}}%", { pct: Math.round((reply.confidence || 0) * 100) })}): `}</span>
          <span className="text-foreground/85">{reply.summary}</span>
        </div>
      ) : null}

      <div className="rounded-2xl border border-border/60 p-3">
        <p className="mb-2 text-[13px] font-semibold text-foreground">{tx("replies.handoff", "Take the lead")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={tx("replies.owner", "Owner")} hint={tx("replies.ownerHint", "A teammate of your Navin team, or a person.")}>
            <Input value={assigned} list={`owners-${reply.id}`} onChange={(event) => setAssigned(event.target.value)} placeholder={teammates[0] ?? tx("replies.ownerPlaceholder", "e.g. Jack")} />
            <datalist id={`owners-${reply.id}`}>
              {teammates.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </Field>
          <Field label={tx("replies.brief", "Brief")} hint={tx("replies.briefHint", "What the owner must know and do next.")} className="sm:col-span-2">
            <Textarea rows={4} value={brief} onChange={(event) => setBrief(event.target.value)} />
          </Field>
        </div>
        <div className="mt-2 flex justify-end">
          <Button
            size="sm"
            variant="outline"
            className="rounded-full"
            disabled={busy !== ""}
            onClick={() =>
              void run(
                "handoff",
                () => updateReply(token, { id: reply.id, assigned_to: assigned.trim(), handoff_brief: brief, status: assigned.trim() ? "handed_off" : reply.status }),
                tx("replies.handedOff", "Follow-up assigned."),
              )
            }
          >
            {busy === "handoff" ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Save className="me-1.5 h-3.5 w-3.5" aria-hidden />}
            {tx("replies.saveHandoff", "Save and hand off")}
          </Button>
        </div>
      </div>

      <div className="rounded-2xl border border-border/60 p-3">
        <p className="mb-2 text-[13px] font-semibold text-foreground">{tx("replies.answer", "Answer")}</p>
        {windowEnd ? (
          <p className={cn("mb-2 rounded-lg px-2.5 py-1.5 text-[12px]", windowClosed ? "bg-[#c2410c]/10 text-[#c2410c]" : "bg-[#1f9d74]/10 text-[#1f9d74]")} data-testid="wa-window">
            {windowClosed
              ? tx("replies.windowClosed", "The 24h WhatsApp window closed on {{when}}: answer with an approved template from a campaign.", { when: formatDateTime(windowEnd.toISOString()) })
              : tx("replies.windowOpen", "24h window: answer before {{when}}", { when: formatDateTime(windowEnd.toISOString()) })}
          </p>
        ) : null}
        {!isWhatsApp ? (
          <Field label={tx("content.subject", "Subject")}>
            <Input value={subject} onChange={(event) => setSubject(event.target.value)} />
          </Field>
        ) : null}
        <Textarea rows={7} className="mt-2" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={tx("replies.answerPlaceholder", "Write your answer, or let the campaign agent draft it.")} />
        <div className="mt-2 flex flex-wrap justify-end gap-2">
          <Button size="sm" variant="ghost" className="rounded-full" disabled={busy !== ""} onClick={() => void run("ignore", () => updateReply(token, { id: reply.id, status: "ignored" }), tx("replies.ignoredDone", "Marked as ignored."))}>
            <Ban className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("replies.ignore", "Ignore")}
          </Button>
          <Button size="sm" variant="ghost" className="rounded-full" disabled={busy !== ""} onClick={() => void run("handled", () => updateReply(token, { id: reply.id, status: "answered" }), tx("replies.handledDone", "Marked as handled."))}>
            <CheckCheck className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("replies.markHandled", "Mark handled")}
          </Button>
          <Button size="sm" variant="outline" className="rounded-full" disabled={busy !== "" || !draft.trim()} onClick={() => void run("draft", () => updateReply(token, { id: reply.id, draft_reply: draft, status: "drafted" }), tx("replies.draftSaved", "Draft saved."))}>
            {tx("replies.saveDraft", "Save draft")}
          </Button>
          <Button size="sm" className="rounded-full" disabled={busy !== "" || !draft.trim() || windowClosed} onClick={() => void run("send", () => sendReply(token, reply.id, draft, isWhatsApp ? "" : subject), tx("replies.sent", "Answer sent."))} data-testid="reply-send">
            {busy === "send" ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Send className="me-1.5 h-3.5 w-3.5" aria-hidden />}
            {tx("replies.send", "Send answer")}
          </Button>
        </div>
        {reply.answered_at ? <p className="mt-2 text-[11.5px] text-muted-foreground">{tx("replies.answeredAt", "Answered {{when}}", { when: formatDateTime(reply.answered_at) })}</p> : null}
      </div>
    </div>
  );
}
