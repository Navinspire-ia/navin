import { useMemo, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { DefaultButton, Dropdown, Icon, MessageBar, MessageBarType, PrimaryButton, TextField, Toggle, type IDropdownOption } from "@fluentui/react";

import type {
  MarketingConnector,
  MarketingContent,
  MarketingDesk,
  MarketingPublishChannelSettings,
  MarketingPublishReceipt,
} from "@/lib/marketing-api";
import {
  API_CHANNELS,
  CONNECTOR_FIELDS,
  CONTENT_CHANNELS,
  channelLabel,
  connectorTone,
  contentActions,
  formatWhen,
  scheduleValue,
  sortQueue,
  statusTone,
  summarizeQueue,
  type StatusTone,
} from "@/lib/marketing-publish";
import { cn } from "@/lib/utils";

export type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;
export type RunAction = (
  action: string,
  body?: Record<string, unknown>,
  after?: (next: MarketingDesk) => void,
) => Promise<boolean>;

const BUTTON_STYLES = { root: { minHeight: 36, cursor: "pointer" as const } };
const SMALL_BUTTON = { root: { minHeight: 32, padding: "0 10px", cursor: "pointer" as const } };
const SPRING = { type: "spring" as const, duration: 0.28, bounce: 0 };

const TONE_CLASS: Record<StatusTone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  success: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  warning: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  danger: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
};

export function Pill({ tone, children, testId }: { tone: StatusTone; children: ReactNode; testId?: string }) {
  return (
    <span
      data-testid={testId}
      className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide", TONE_CLASS[tone])}
    >
      {children}
    </span>
  );
}

function statusLabel(tx: Tx, status?: string | null): string {
  const key = String(status || "draft");
  const fallback: Record<string, string> = {
    draft: "Draft",
    ready: "Ready",
    approved: "Approved",
    scheduled: "Scheduled",
    published: "Published",
    failed: "Failed",
    winner: "Winner",
    retired: "Retired",
  };
  return tx(`status.${key}`, fallback[key] || key);
}

// -- Content queue ---------------------------------------------------------------------------

function ScheduleRow({
  busy,
  tx,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  tx: Tx;
  onSubmit: (when: string) => void;
  onCancel: () => void;
}) {
  const [when, setWhen] = useState("");
  const value = scheduleValue(when);
  return (
    <div className="mt-2 flex flex-wrap items-end gap-2 rounded-xl border border-border/60 bg-muted/30 p-2" data-testid="marketing-schedule-row">
      <TextField
        label={tx("scheduleWhen", "When")}
        placeholder="+2h, +1d, 2026-09-03T09:00"
        value={when}
        onChange={(_, next) => setWhen(next || "")}
        styles={{ root: { minWidth: 220 } }}
      />
      {["+1h", "+4h", "+1d", "+3d"].map((quick) => (
        <DefaultButton key={quick} text={quick} onClick={() => setWhen(quick)} styles={SMALL_BUTTON} />
      ))}
      <PrimaryButton text={tx("scheduleConfirm", "Schedule")} disabled={busy || !value} onClick={() => onSubmit(value)} styles={SMALL_BUTTON} />
      <DefaultButton text={tx("cancel", "Cancel")} onClick={onCancel} styles={SMALL_BUTTON} />
    </div>
  );
}

export function ContentQueue({
  desk,
  busy,
  locale,
  tx,
  run,
  onCopy,
  onNotice,
}: {
  desk: MarketingDesk;
  busy: string;
  locale: string;
  tx: Tx;
  run: RunAction;
  onCopy: (text: string) => void;
  onNotice: (text: string) => void;
}) {
  const reduceMotion = useReducedMotion();
  const [filter, setFilter] = useState<string>("all");
  const [showHistory, setShowHistory] = useState(false);
  const [scheduling, setScheduling] = useState<string>("");
  const [preview, setPreview] = useState<{ id: string; receipt: MarketingPublishReceipt } | null>(null);
  const summary = summarizeQueue(desk.queue);
  const connectors = useMemo(() => new Map((desk.connectors || []).map((row) => [row.channel, row])), [desk.connectors]);
  const rows = useMemo(() => {
    const all = sortQueue(desk.content || []);
    const scoped = filter === "all" ? all : all.filter((row) => row.channel === filter);
    return showHistory ? scoped : scoped.filter((row) => !["published", "winner", "retired"].includes(String(row.status || "")));
  }, [desk.content, filter, showHistory]);
  const historyCount = (desk.content || []).filter((row) => ["published", "winner", "retired"].includes(String(row.status || ""))).length;
  const channelsPresent = useMemo(() => {
    const present = new Set((desk.content || []).map((row) => String(row.channel || "")));
    return CONTENT_CHANNELS.filter((channel) => present.has(channel));
  }, [desk.content]);
  const isBusy = Boolean(busy);

  const publishOne = (row: MarketingContent) =>
    void run("publish", { ids: [row.id] }, (next) => {
      const report = next.publish || {};
      const sent = report.sent?.[0];
      const failed = report.failed?.[0];
      if (sent) {
        const url = sent.content?.published_url || sent.url || "";
        onNotice(
          sent.manual
            ? tx("publishedManual", "{{channel}}: marked published. Copy the text and paste it there.", { channel: channelLabel(row.channel) })
            : tx("publishedOk", "Published on {{channel}}{{url}}", { channel: channelLabel(row.channel), url: url ? ` - ${url}` : "" }),
        );
      } else if (failed) {
        onNotice(tx("publishFailed", "{{channel}} refused the post: {{error}}", { channel: channelLabel(row.channel), error: failed.error || "" }));
      }
    });

  const previewOne = (row: MarketingContent) =>
    void run("publish", { ids: [row.id], dry_run: true }, (next) => {
      const receipt = next.publish?.preview?.[0];
      if (receipt) setPreview({ id: row.id, receipt });
    });

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2" data-testid="marketing-queue-summary">
        <Pill tone={summary.due ? "info" : "neutral"}>{tx("queueDue", "{{n}} due", { n: summary.due })}</Pill>
        <Pill tone={summary.waiting ? "info" : "neutral"}>{tx("queueWaiting", "{{n}} scheduled", { n: summary.waiting })}</Pill>
        <Pill tone={summary.approved ? "info" : "neutral"}>{tx("queueApproved", "{{n}} approved", { n: summary.approved })}</Pill>
        {summary.blocked ? <Pill tone="warning">{tx("queueBlocked", "{{n}} waiting for a connector", { n: summary.blocked })}</Pill> : null}
        <Pill tone={summary.autoPublish ? "success" : "neutral"}>
          {summary.autoPublish
            ? tx("queueAuto", "auto-publish {{n}} per cycle", { n: summary.perCycle })
            : tx("queueApproval", "approval mode, {{n}} per cycle", { n: summary.perCycle })}
        </Pill>
        <span className="text-xs text-muted-foreground">
          {summary.readyChannels.length
            ? tx("readyChannels", "Connected: {{list}}", { list: summary.readyChannels.map(channelLabel).join(", ") })
            : tx("noReadyChannels", "No channel connected yet. Open Settings to add a token.")}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        <PrimaryButton
          text={tx("publishDue", "Publish what is due")}
          iconProps={{ iconName: "Send" }}
          disabled={isBusy || summary.sendable === 0}
          data-testid="marketing-publish-due"
          onClick={() =>
            void run("publish", {}, (next) => {
              const report = next.publish || {};
              onNotice(
                tx("publishReport", "{{sent}} published, {{failed}} failed, {{skipped}} still waiting.", {
                  sent: report.sent?.length || 0,
                  failed: report.failed?.length || 0,
                  skipped: report.skipped?.length || 0,
                }),
              );
            })
          }
          styles={BUTTON_STYLES}
        />
        <DefaultButton
          text={tx("writeAllChannels", "Write a post per channel")}
          iconProps={{ iconName: "EditNote" }}
          disabled={isBusy || !desk.armed}
          data-testid="marketing-write-all"
          onClick={() =>
            void run("content", { channels: filter === "all" ? [...CONTENT_CHANNELS] : [filter], fresh: true }, (next) =>
              onNotice(tx("contentDone", "Wrote {{n}} channel drafts.", { n: next.content.filter((row) => row.status === "ready").length })),
            )
          }
          styles={BUTTON_STYLES}
        />
        <DefaultButton
          text={tx("syncMetrics", "Sync metrics")}
          iconProps={{ iconName: "BarChart4" }}
          disabled={isBusy}
          data-testid="marketing-sync-metrics"
          onClick={() =>
            void run("measure", {}, (next) => {
              const report = next.measure || {};
              onNotice(
                report.error
                  ? tx("measureError", "Metrics: {{error}}", { error: report.error })
                  : tx("measureDone", "{{engagement}} posts measured, {{traffic}} tracked links with visits.", {
                      engagement: report.engagement || 0,
                      traffic: report.traffic || 0,
                    }),
              );
            })
          }
          styles={BUTTON_STYLES}
        />
        <Toggle
          className="mb-0 self-center"
          inlineLabel
          label={tx("showHistory", "Show published ({{n}})", { n: historyCount })}
          checked={showHistory}
          onChange={(_, checked) => setShowHistory(Boolean(checked))}
        />
      </div>

      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label={tx("channelFilter", "Channel filter")}>
        {["all", ...channelsPresent].map((channel) => (
          <button
            key={channel}
            type="button"
            role="tab"
            aria-selected={filter === channel}
            onClick={() => setFilter(channel)}
            className={cn(
              "cursor-pointer rounded-full px-3 py-1 text-xs transition-colors",
              filter === channel ? "bg-violet-500/20 font-medium text-foreground" : "bg-muted/60 text-muted-foreground hover:text-foreground",
            )}
          >
            {channel === "all" ? tx("allChannels", "All") : channelLabel(channel)}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="marketing-queue-empty">
          {desk.content?.length
            ? tx("queueClear", "Nothing waiting. Published posts are under Show published.")
            : tx("queueEmpty", "No drafts yet. Write a post per channel, or plan a campaign.")}
        </p>
      ) : (
        <ul className="grid gap-3" data-testid="marketing-queue-list">
          {rows.map((row) => {
            const actions = contentActions(row);
            const connector = connectors.get(String(row.channel || ""));
            const sendable = Boolean(connector?.ready || connector?.mode === "manual");
            const measured = desk.analytics.by_content?.[row.id];
            return (
              <li key={row.id} className="rounded-xl border border-border/60 px-3 py-3 shadow-[0_1px_0_rgba(15,23,42,0.04)]" data-testid={`marketing-content-${row.id}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{channelLabel(row.channel)}</span>
                    <Pill tone={statusTone(row.status)} testId={`marketing-status-${row.id}`}>
                      {statusLabel(tx, row.status)}
                    </Pill>
                    {row.source === "model" ? (
                      <Pill tone="neutral">{row.model ? tx("byModel", "model {{m}}", { m: row.model }) : tx("byModelShort", "model")}</Pill>
                    ) : null}
                    {row.status === "scheduled" && row.scheduled_at ? (
                      <span className="text-xs text-muted-foreground">{formatWhen(row.scheduled_at, locale)}</span>
                    ) : null}
                    {(row.status === "published" || row.status === "winner") && row.published_at ? (
                      <span className="text-xs text-muted-foreground">{formatWhen(row.published_at, locale)}</span>
                    ) : null}
                    {connector && !sendable && actions.publish ? (
                      <Pill tone="warning">{connector.bridge ? tx("viaWebhook", "via webhook") : tx("notConnected", "not connected")}</Pill>
                    ) : null}
                    {connector?.mode === "manual" && !connector.bridge && actions.publish ? (
                      <Pill tone="neutral">{tx("copyPaste", "copy and paste")}</Pill>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {actions.approve ? (
                      <DefaultButton
                        text={tx("approveContent", "Approve")}
                        iconProps={{ iconName: "CheckMark" }}
                        disabled={isBusy}
                        data-testid={`marketing-approve-${row.id}`}
                        onClick={() => void run("approve-content", { id: row.id })}
                        styles={SMALL_BUTTON}
                      />
                    ) : null}
                    {actions.schedule ? (
                      <DefaultButton
                        text={tx("scheduleContent", "Schedule")}
                        iconProps={{ iconName: "Clock" }}
                        disabled={isBusy}
                        data-testid={`marketing-schedule-${row.id}`}
                        onClick={() => setScheduling((current) => (current === row.id ? "" : row.id))}
                        styles={SMALL_BUTTON}
                      />
                    ) : null}
                    {actions.publish ? (
                      <PrimaryButton
                        text={
                          connector?.mode === "manual" && !connector.bridge
                            ? tx("markPosted", "Mark as posted")
                            : tx("publishNow", "Publish now")
                        }
                        iconProps={{ iconName: connector?.mode === "manual" && !connector.bridge ? "CheckMark" : "Send" }}
                        disabled={isBusy || !sendable}
                        data-testid={`marketing-publish-${row.id}`}
                        onClick={() => publishOne(row)}
                        styles={SMALL_BUTTON}
                      />
                    ) : null}
                    {actions.preview ? (
                      <DefaultButton
                        text={tx("previewContent", "Preview")}
                        iconProps={{ iconName: "View" }}
                        disabled={isBusy}
                        onClick={() => (preview?.id === row.id ? setPreview(null) : previewOne(row))}
                        styles={SMALL_BUTTON}
                      />
                    ) : null}
                    <DefaultButton
                      text={tx("copyDraft", "Copy")}
                      iconProps={{ iconName: "Copy" }}
                      disabled={!row.body}
                      onClick={() => onCopy(String(row.publish_text || row.body || ""))}
                      styles={SMALL_BUTTON}
                    />
                    {actions.unschedule ? (
                      <DefaultButton
                        text={tx("backToReady", "Back to ready")}
                        disabled={isBusy}
                        onClick={() => void run("unschedule", { id: row.id })}
                        styles={SMALL_BUTTON}
                      />
                    ) : null}
                    {actions.retire ? (
                      <DefaultButton
                        text={tx("retireContent", "Retire")}
                        disabled={isBusy}
                        onClick={() => void run("retire", { id: row.id })}
                        styles={SMALL_BUTTON}
                      />
                    ) : null}
                  </div>
                </div>
                {row.hook && !String(row.body || "").includes(row.hook) ? <p className="mt-2 text-sm font-medium">{row.hook}</p> : null}
                <p className="mt-1 whitespace-pre-wrap text-pretty text-sm">{row.body}</p>
                {row.published_url ? (
                  <a
                    className="mt-1 inline-block break-all text-xs text-violet-700 underline-offset-2 hover:underline dark:text-violet-300"
                    href={row.published_url}
                    target="_blank"
                    rel="noreferrer"
                    data-testid={`marketing-url-${row.id}`}
                  >
                    {row.published_url}
                  </a>
                ) : null}
                {row.error ? (
                  <p className="mt-1 text-pretty text-xs text-rose-600 dark:text-rose-300" data-testid={`marketing-error-${row.id}`}>
                    {row.error}
                  </p>
                ) : null}
                {measured?.measured_at ? (
                  <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                    {tx("measuredLine", "{{views}} views · {{clicks}} clicks · {{likes}} likes · {{shares}} shares", {
                      views: Math.round(measured.views || 0),
                      clicks: Math.round(measured.clicks || 0),
                      likes: Math.round(measured.likes || 0),
                      shares: Math.round(measured.shares || 0),
                    })}
                  </p>
                ) : null}
                <AnimatePresence initial={false}>
                  {scheduling === row.id ? (
                    <motion.div
                      key="schedule"
                      initial={reduceMotion ? false : { opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
                      transition={SPRING}
                      className="overflow-hidden"
                    >
                      <ScheduleRow
                        busy={isBusy}
                        tx={tx}
                        onCancel={() => setScheduling("")}
                        onSubmit={(when) =>
                          void run("schedule-content", { id: row.id, scheduled_at: when }, () => {
                            setScheduling("");
                            onNotice(tx("scheduled", "Scheduled. The loop posts it when due; Publish what is due sends it earlier."));
                          })
                        }
                      />
                    </motion.div>
                  ) : null}
                  {preview?.id === row.id ? (
                    <motion.div
                      key="preview"
                      initial={reduceMotion ? false : { opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
                      transition={SPRING}
                      className="overflow-hidden"
                    >
                      <div className="mt-2 rounded-xl border border-dashed border-border/70 bg-muted/30 p-3" data-testid={`marketing-preview-${row.id}`}>
                        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                          {tx("previewVia", "As it will go out via {{via}}", { via: channelLabel(preview.receipt.via) })}
                        </p>
                        <p className="mt-1 whitespace-pre-wrap text-sm">{preview.receipt.text}</p>
                        {preview.receipt.link ? <p className="mt-1 break-all text-xs text-muted-foreground">{preview.receipt.link}</p> : null}
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// -- Connectors and settings -----------------------------------------------------------------

function ConnectorCard({
  row,
  settings,
  busy,
  tx,
  locale,
  run,
  onNotice,
}: {
  row: MarketingConnector;
  settings: MarketingPublishChannelSettings;
  busy: boolean;
  tx: Tx;
  locale: string;
  run: RunAction;
  onNotice: (text: string) => void;
}) {
  const fields = CONNECTOR_FIELDS[row.channel] || [];
  const [draft, setDraft] = useState<Record<string, string>>({});
  const tone = connectorTone(row);
  const toneMap: Record<string, StatusTone> = { ready: "success", configured: "info", missing: "warning", manual: "neutral", bridged: "info" };
  const toneLabel: Record<string, string> = {
    ready: tx("connReady", "ready"),
    configured: tx("connConfigured", "configured, off"),
    missing: tx("connMissing", "missing {{list}}", { list: (row.missing || []).join(", ") }),
    manual: tx("connManual", "copy and paste"),
    bridged: tx("connBridged", "via webhook"),
  };
  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    const secrets = fields.filter((field) => field.secret && field.key in draft);
    const plain = fields.filter((field) => !field.secret && field.key in draft);
    for (const field of secrets) {
      const ok = await run("secret", { name: field.key, value: draft[field.key] });
      if (!ok) return;
    }
    if (plain.length) {
      const patch: Record<string, string> = {};
      for (const field of plain) patch[field.key] = draft[field.key];
      const ok = await run("settings", { publish: { [row.channel]: patch } });
      if (!ok) return;
    }
    setDraft({});
    onNotice(tx("connectorSaved", "{{channel}} saved.", { channel: channelLabel(row.channel) }));
  };

  if (row.mode === "manual") {
    return (
      <li className="rounded-xl border border-border/60 px-3 py-3" data-testid={`marketing-connector-${row.channel}`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{channelLabel(row.channel)}</span>
          <Pill tone={toneMap[tone]}>{toneLabel[tone]}</Pill>
        </div>
        <p className="mt-1 text-pretty text-xs text-muted-foreground">
          {row.bridge
            ? tx("manualBridgeHint", "No public posting API. Posts go to your webhook (Zapier, Make, Buffer bridge) which publishes them.")
            : tx("manualHint", "No public posting API. The desk marks the post published and you paste the text. Enable the webhook to automate it.")}
        </p>
      </li>
    );
  }

  return (
    <li className="rounded-xl border border-border/60 px-3 py-3" data-testid={`marketing-connector-${row.channel}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{channelLabel(row.channel)}</span>
          <Pill tone={toneMap[tone]} testId={`marketing-connector-state-${row.channel}`}>
            {toneLabel[tone]}
          </Pill>
          {row.account ? <span className="text-xs text-muted-foreground">{row.account}</span> : null}
          {row.tested_at ? (
            <span className="text-xs text-muted-foreground">{tx("testedAt", "tested {{when}}", { when: formatWhen(row.tested_at, locale) })}</span>
          ) : null}
        </div>
        <Toggle
          className="mb-0"
          inlineLabel
          label={tx("connectorEnabled", "Enabled")}
          checked={Boolean(settings.enabled)}
          disabled={busy || !row.configured}
          data-testid={`marketing-connector-toggle-${row.channel}`}
          onChange={(_, checked) => void run("settings", { publish: { [row.channel]: { enabled: Boolean(checked) } } })}
        />
      </div>
      <p className="mt-1 text-pretty text-xs text-muted-foreground">{row.hint}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {fields.map((field) => {
          const isSet = field.secret ? Boolean(row.secrets?.[field.key]) : Boolean(row.fields?.[field.key]);
          const label = tx(`field.${field.key}`, field.key.replace(/_/g, " "));
          return (
            <TextField
              key={field.key}
              label={field.secret ? `${label} ${isSet ? tx("secretSet", "(set)") : tx("secretUnset", "(not set)")}` : label}
              type={field.secret ? "password" : "text"}
              canRevealPassword={field.secret}
              placeholder={field.secret && isSet ? "••••••••" : field.placeholder}
              value={draft[field.key] ?? (field.secret ? "" : row.fields?.[field.key] || "")}
              onChange={(_, next) => setDraft((current) => ({ ...current, [field.key]: next || "" }))}
              data-testid={`marketing-field-${row.channel}-${field.key}`}
            />
          );
        })}
      </div>
      {row.last_error ? (
        <p className="mt-2 text-pretty text-xs text-rose-600 dark:text-rose-300" data-testid={`marketing-connector-error-${row.channel}`}>
          {row.last_error}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <PrimaryButton
          text={tx("saveConnector", "Save")}
          disabled={busy || !dirty}
          data-testid={`marketing-connector-save-${row.channel}`}
          onClick={() => void save()}
          styles={SMALL_BUTTON}
        />
        <DefaultButton
          text={tx("testConnector", "Test connection")}
          iconProps={{ iconName: "PlugConnected" }}
          disabled={busy || !row.configured}
          data-testid={`marketing-connector-test-${row.channel}`}
          onClick={() =>
            void run("connection", { channel: row.channel }, (next) => {
              const result = next.connection;
              if (result?.ok) onNotice(tx("connectionOk", "{{channel}} connected as {{account}}.", { channel: channelLabel(row.channel), account: result.account || "ok" }));
              else onNotice(tx("connectionFailed", "{{channel}}: {{error}}", { channel: channelLabel(row.channel), error: result?.error || "" }));
            })
          }
          styles={SMALL_BUTTON}
        />
      </div>
    </li>
  );
}

export function ConnectorsPane({
  desk,
  busy,
  locale,
  tx,
  run,
  onNotice,
}: {
  desk: MarketingDesk;
  busy: string;
  locale: string;
  tx: Tx;
  run: RunAction;
  onNotice: (text: string) => void;
}) {
  const isBusy = Boolean(busy);
  const settings = desk.settings || {};
  const publish = (settings.publish || {}) as Record<string, MarketingPublishChannelSettings | number | undefined>;
  const analytics = settings.analytics || {};
  const [utm, setUtm] = useState<string | null>(null);
  const [perCycle, setPerCycle] = useState<string | null>(null);
  const [analyticsDraft, setAnalyticsDraft] = useState<Record<string, string>>({});
  const [analyticsKey, setAnalyticsKey] = useState("");
  const connectors = (desk.connectors || []).filter((row) => API_CHANNELS.includes(row.channel as (typeof API_CHANNELS)[number]));
  const manual = (desk.connectors || []).filter((row) => row.mode === "manual");
  const provider = analyticsDraft.provider ?? String(analytics.provider || "");
  const keyName = provider === "matomo" ? "matomo_token" : "plausible_key";
  const analyticsSecretSet = Boolean(desk.secrets_set?.[keyName]);
  const modeOptions: IDropdownOption[] = [
    { key: "approval", text: tx("modeApproval", "Approval: I approve, the loop posts") },
    { key: "autonomous", text: tx("modeAutonomous", "Autonomous: the loop writes, posts and learns") },
  ];
  const providerOptions: IDropdownOption[] = [
    { key: "", text: tx("providerNone", "None (manual metrics)") },
    { key: "plausible", text: "Plausible" },
    { key: "matomo", text: "Matomo" },
  ];
  const ai = desk.ai;

  return (
    <div className="grid gap-4">
      <section className="rounded-2xl border border-border/70 bg-background/80 p-4 shadow-[0_10px_28px_rgba(15,23,42,0.06)]" data-testid="marketing-execution-card">
        <h3 className="mb-3 text-sm font-semibold">{tx("executionTitle", "How the desk publishes")}</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Dropdown
            label={tx("executionMode", "Execution mode")}
            selectedKey={String(settings.execution_mode || "approval")}
            options={modeOptions}
            disabled={isBusy}
            onChange={(_, option) => option && void run("settings", { execution_mode: String(option.key) })}
          />
          <TextField
            label={tx("perCycle", "Posts per loop cycle")}
            type="number"
            min={1}
            max={20}
            value={perCycle ?? String(publish.per_cycle || 1)}
            onChange={(_, next) => setPerCycle(next || "")}
            onBlur={() => {
              if (perCycle === null) return;
              const value = Math.max(1, Math.min(20, Number(perCycle) || 1));
              setPerCycle(null);
              if (value !== Number(publish.per_cycle || 1)) void run("settings", { publish: { per_cycle: value } });
            }}
          />
          <Toggle
            label={tx("autoPublish", "Auto-publish ready posts (autonomous mode only)")}
            checked={Boolean(settings.auto_publish)}
            disabled={isBusy}
            data-testid="marketing-auto-publish"
            onChange={(_, checked) => void run("settings", { auto_publish: Boolean(checked) })}
          />
          <Toggle
            label={tx("aiAssist", "Let a routed model write copy, positioning and research")}
            checked={settings.ai_assist !== false}
            disabled={isBusy}
            data-testid="marketing-ai-assist"
            onChange={(_, checked) => void run("settings", { ai_assist: Boolean(checked) })}
          />
          <TextField
            label={tx("utmCampaign", "UTM campaign tag")}
            placeholder="launch-q3"
            value={utm ?? String(settings.utm_campaign || "")}
            onChange={(_, next) => setUtm(next || "")}
            onBlur={() => {
              if (utm === null) return;
              const value = utm.trim();
              setUtm(null);
              if (value !== String(settings.utm_campaign || "")) void run("settings", { utm_campaign: value });
            }}
          />
        </div>
        <p className="mt-2 text-pretty text-xs text-muted-foreground">
          {tx("executionHint", "Every post carries a tracked link (utm_source = channel, utm_content = post id) so analytics can attribute visits and signups per post.")}
        </p>
      </section>

      <section className="rounded-2xl border border-border/70 bg-background/80 p-4 shadow-[0_10px_28px_rgba(15,23,42,0.06)]" data-testid="marketing-connectors-card">
        <h3 className="mb-1 text-sm font-semibold">{tx("connectorsTitle", "Channels")}</h3>
        <p className="mb-3 text-pretty text-xs text-muted-foreground">
          {tx("connectorsHint", "Tokens stay in marketing/secrets.json (owner-only file) and never appear in the desk snapshot.")}
        </p>
        <ul className="grid gap-3">
          {connectors.map((row) => (
            <ConnectorCard
              key={row.channel}
              row={row}
              settings={(publish[row.channel] as MarketingPublishChannelSettings) || {}}
              busy={isBusy}
              tx={tx}
              locale={locale}
              run={run}
              onNotice={onNotice}
            />
          ))}
        </ul>
        {manual.length ? (
          <>
            <h4 className="mb-2 mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">{tx("manualTitle", "Without a posting API")}</h4>
            <ul className="grid gap-2 sm:grid-cols-2">
              {manual.map((row) => (
                <ConnectorCard key={row.channel} row={row} settings={{}} busy={isBusy} tx={tx} locale={locale} run={run} onNotice={onNotice} />
              ))}
            </ul>
          </>
        ) : null}
      </section>

      <section className="rounded-2xl border border-border/70 bg-background/80 p-4 shadow-[0_10px_28px_rgba(15,23,42,0.06)]" data-testid="marketing-analytics-card">
        <h3 className="mb-1 text-sm font-semibold">{tx("analyticsSourceTitle", "Site analytics")}</h3>
        <p className="mb-3 text-pretty text-xs text-muted-foreground">
          {tx("analyticsSourceHint", "Plausible or Matomo read visits and goals per utm_content, so the scoreboard shows measured numbers instead of typed ones.")}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Dropdown
            label={tx("provider", "Provider")}
            selectedKey={provider}
            options={providerOptions}
            disabled={isBusy}
            onChange={(_, option) => option && setAnalyticsDraft((current) => ({ ...current, provider: String(option.key) }))}
          />
          <TextField
            label={tx("siteId", "Site id")}
            placeholder={provider === "matomo" ? "3" : "example.com"}
            value={analyticsDraft.site_id ?? String(analytics.site_id || "")}
            onChange={(_, next) => setAnalyticsDraft((current) => ({ ...current, site_id: next || "" }))}
          />
          <TextField
            label={tx("baseUrl", "Base URL")}
            placeholder={provider === "matomo" ? "https://stats.example.com" : "https://plausible.io (default)"}
            value={analyticsDraft.base_url ?? String(analytics.base_url || "")}
            onChange={(_, next) => setAnalyticsDraft((current) => ({ ...current, base_url: next || "" }))}
          />
          <TextField
            label={tx("analyticsGoal", "Signup goal (Plausible)")}
            placeholder="Signup"
            value={analyticsDraft.goal ?? String(analytics.goal || "")}
            onChange={(_, next) => setAnalyticsDraft((current) => ({ ...current, goal: next || "" }))}
          />
          <TextField
            label={`${tx(`field.${keyName}`, keyName.replace(/_/g, " "))} ${analyticsSecretSet ? tx("secretSet", "(set)") : ""}`.trim()}
            type="password"
            canRevealPassword
            value={analyticsKey}
            onChange={(_, next) => setAnalyticsKey(next || "")}
            data-testid="marketing-analytics-key"
          />
        </div>
        {analytics.last_error ? <p className="mt-2 text-xs text-rose-600 dark:text-rose-300">{analytics.last_error}</p> : null}
        {analytics.measured_at ? (
          <p className="mt-2 text-xs text-muted-foreground">{tx("measuredAt", "last measured {{when}}", { when: formatWhen(analytics.measured_at, locale) })}</p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <PrimaryButton
            text={tx("saveAnalytics", "Save analytics")}
            disabled={isBusy || (!Object.keys(analyticsDraft).length && !analyticsKey)}
            data-testid="marketing-analytics-save"
            onClick={async () => {
              if (analyticsKey) {
                const ok = await run("secret", { name: keyName, value: analyticsKey });
                if (!ok) return;
                setAnalyticsKey("");
              }
              if (Object.keys(analyticsDraft).length) {
                const ok = await run("settings", { analytics: analyticsDraft });
                if (!ok) return;
                setAnalyticsDraft({});
              }
              onNotice(tx("analyticsSaved", "Analytics source saved."));
            }}
            styles={SMALL_BUTTON}
          />
          <DefaultButton
            text={tx("testAnalytics", "Test analytics")}
            iconProps={{ iconName: "PlugConnected" }}
            disabled={isBusy || !provider}
            onClick={() =>
              void run("connection", { channel: "analytics" }, (next) => {
                const result = next.connection;
                if (result?.ok) onNotice(tx("analyticsOk", "{{provider}} answers: {{n}} visitors over 30 days.", { provider: result.provider || provider, n: Math.round(result.traffic || 0) }));
                else onNotice(tx("analyticsFailed", "Analytics: {{error}}", { error: result?.error || "" }));
              })
            }
            styles={SMALL_BUTTON}
          />
        </div>
      </section>

      <section className="rounded-2xl border border-border/70 bg-background/80 p-4 shadow-[0_10px_28px_rgba(15,23,42,0.06)]" data-testid="marketing-ai-card">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">{tx("aiTitle", "Writing model")}</h3>
          <Pill tone={ai?.enabled && ai.routed ? "success" : ai?.enabled ? "warning" : "neutral"}>
            {!ai?.enabled
              ? tx("aiOff", "off: templates only")
              : ai.routed
                ? tx("aiRouted", "{{n}} of {{total}} tasks routed", { n: ai.routed, total: ai.tasks.length })
                : tx("aiUnrouted", "no model routed: templates only")}
          </Pill>
        </div>
        <p className="mt-1 text-pretty text-xs text-muted-foreground">
          {tx("aiHint", "Positioning, posts, variants of winners, competitor extraction and the launch kit go through your model routes (Settings > Models). Without a route the desk stays on honest templates.")}
        </p>
        {ai?.tasks?.length ? (
          <ul className="mt-3 grid gap-1 text-xs sm:grid-cols-2">
            {ai.tasks.map((task) => (
              <li key={task.task} className="flex items-center justify-between gap-2 rounded-lg bg-muted/40 px-2 py-1">
                <span className="font-medium">{tx(`aiTask.${task.task}`, task.task)}</span>
                <span className="text-muted-foreground">
                  {task.preset ? `${task.preset}${task.model ? ` · ${task.model}` : ""}` : tx("aiTaskUnrouted", "role {{role}}, no route", { role: task.role })}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {!ai?.enabled ? (
          <MessageBar className="mt-3" messageBarType={MessageBarType.info}>
            {tx("aiOffHint", "Turn on the model toggle above, then route the roles deep / docs / fast to a provider.")}
          </MessageBar>
        ) : null}
      </section>
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        <Icon iconName="Info" /> {tx("settingsFooter", "Loop order each cycle: publish due posts, measure, learn from winners, then write the next batch.")}
      </p>
    </div>
  );
}
