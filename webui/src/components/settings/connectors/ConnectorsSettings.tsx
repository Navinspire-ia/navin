// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings > Channels: one grid of cards for the chat channels, the social
 * networks and the app connectors, under the channel tabs.
 *
 * The parent owns the search, the tab (connector categories and channel
 * buckets), the Connected / Not connected filter and the channel setup. This
 * list loads every matching app, shows a channel that is also an app on that
 * app's card only, keeps what belongs to the active tab, pages the cards
 * locally, reports the counts for the tab badges, and opens a dialog with the
 * connection form (sign-in first), the channel setup of a shared card and the
 * actions the agent can call once the app is connected.
 */

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { ChevronLeft, ChevronRight, ExternalLink, RefreshCw, Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { openInOsBrowser } from "@/lib/api";
import {
  type ConnectorActionsPage,
  type ConnectorCatalogPage,
  type ConnectorCategory,
  type ConnectorDetail,
  type ConnectorStatus,
  type ConnectorStatusFilter,
  type ConnectorSummary,
  errorMessage,
  fetchConnectorActions,
  fetchConnectorCatalog,
  pageWindow,
} from "@/lib/connectors-api";
import { cn } from "@/lib/utils";
import { type ChannelBucket, channelCountKey, FEATURED_CONNECTORS } from "@/components/settings/channels/catalog";

import { ConnectorIcon } from "./ConnectorIcon";
import { ConnectorSetupForm } from "./ConnectorSetupForm";
import { SEGMENT_GROUP_CLASS, segmentClass } from "./segmented";

export const CONNECTORS_PAGE_SIZE = 12;
const ACTIONS_PAGE_SIZE = 8;
const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

type Translate = (key: string, fallback: string, values?: Record<string, unknown>) => string;

function useDebounced<T>(value: T, ms: number): T {
  const [current, setCurrent] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setCurrent(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return current;
}

/** Page state that falls back to 1 whenever the filters change. */
function useKeyedPage(key: string): [number, (page: number) => void] {
  const [state, setState] = useState({ key, page: 1 });
  const page = state.key === key ? state.page : 1;
  const setPage = useCallback((next: number) => setState({ key, page: next }), [key]);
  return [page, setPage];
}

/** The server's largest catalog page (MAX_PAGE_SIZE in navin/connectors/catalog.py). */
const CATALOG_REQUEST_SIZE = 100;
const ALL_CATEGORIES = "*";

/** Every app matching the search and status, over as many pages as the server returns. */
async function fetchWholeCatalog(
  token: string,
  filters: { q: string; status: ConnectorStatusFilter },
  signal: AbortSignal,
): Promise<ConnectorCatalogPage> {
  const first = await fetchConnectorCatalog(token, { ...filters, page: 1, page_size: CATALOG_REQUEST_SIZE }, signal);
  if (first.pages <= 1) return first;
  const rest = await Promise.all(
    Array.from({ length: first.pages - 1 }, (_, index) =>
      fetchConnectorCatalog(token, { ...filters, page: index + 2, page_size: CATALOG_REQUEST_SIZE }, signal),
    ),
  );
  const items = [...first.items, ...rest.flatMap((next) => next.items)];
  return { ...first, items, page: 1, pages: 1, page_size: items.length };
}

/** Apps per category among the loaded ones. */
function countByCategory(items: readonly ConnectorSummary[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const item of items) counts[item.category] = (counts[item.category] ?? 0) + 1;
  return counts;
}

/** A chat channel or a social network, shown as a card among the apps. */
export interface ChannelCardEntry {
  /** Channel name, e.g. "telegram". */
  name: string;
  label: string;
  /** Second line of the card, e.g. "Chat channel". */
  kind: string;
  bucket: ChannelBucket;
  /** Bundled icon (see ConnectorIcon). */
  icon?: string;
  connected: boolean;
  badges: readonly string[];
  /** Service of the app connector for the same app: both share that card. */
  twin?: string;
}

type GridRow =
  | { type: "channel"; channel: ChannelCardEntry }
  | { type: "connector"; item: ConnectorSummary; channel?: ChannelCardEntry };

const NO_CHANNELS: readonly ChannelCardEntry[] = [];

/** Position of each featured app at the head of the grid. */
const FEATURED_RANK = new Map(FEATURED_CONNECTORS.map((service, index) => [service, index] as const));

export function ConnectorsSettings({
  token,
  query = "",
  categories,
  status = "",
  channels = NO_CHANNELS,
  buckets,
  onOpenChannel,
  renderChannel,
  onCounts,
}: {
  token: string | null | undefined;
  query?: string;
  /** Connector category ids of the active tab; undefined shows every app. */
  categories?: readonly string[];
  status?: ConnectorStatusFilter;
  /** Chat channels matching the same search and status, for every tab. */
  channels?: readonly ChannelCardEntry[];
  /** Channel buckets of the active tab; undefined shows every channel. */
  buckets?: readonly ChannelBucket[];
  onOpenChannel?: (name: string) => void;
  /** The setup of a channel, shown in the dialog of the app card it shares. */
  renderChannel?: (name: string) => ReactNode;
  /**
   * Cards per connector category and per channel bucket (channelCountKey) for
   * the tab badges, a shared card counted once; null while nothing is loaded
   * or when the app catalog failed.
   */
  onCounts?: (counts: Record<string, number> | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const reduceMotion = useReducedMotion();
  const tx: Translate = useCallback(
    (key, fallback, values) => String(t(`settings.connectors.${key}`, { defaultValue: fallback, ...(values ?? {}) })),
    [t],
  );
  const debouncedQuery = useDebounced(query.trim(), 200);
  const groupKey = categories ? categories.join(",") : ALL_CATEGORIES;
  const bucketKey = buckets ? buckets.join(",") : ALL_CATEGORIES;
  const [page, setPage] = useKeyedPage(JSON.stringify([debouncedQuery, groupKey, bucketKey, status]));
  const [catalog, setCatalog] = useState<ConnectorCatalogPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const listTopRef = useRef<HTMLDivElement | null>(null);
  // The latest callback: a new function from the parent never reloads the catalog.
  const onCountsRef = useRef(onCounts);
  useEffect(() => {
    onCountsRef.current = onCounts;
  }, [onCounts]);
  const goToPage = useCallback(
    (next: number) => {
      setPage(next);
      // The pager sits under the cards: bring the new page into view.
      listTopRef.current?.scrollIntoView?.({ block: "start", behavior: reduceMotion ? "auto" : "smooth" });
    },
    [reduceMotion, setPage],
  );

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetchWholeCatalog(token, { q: debouncedQuery, status }, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setCatalog(next);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(errorMessage(err));
        // No stale apps under the error: the channels stay, alone, and the
        // tab badges count them alone.
        setCatalog(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [token, debouncedQuery, status, reload]);

  // A channel that is also a loaded app lives on that app's card.
  const merged = useMemo(() => {
    const services = new Set((catalog?.items ?? []).map((item) => item.service));
    const twins: Record<string, ChannelCardEntry> = {};
    const alone: ChannelCardEntry[] = [];
    for (const channel of channels) {
      if (channel.twin && services.has(channel.twin)) twins[channel.twin] = channel;
      else alone.push(channel);
    }
    return { twins, alone };
  }, [catalog, channels]);

  // The tab badges follow every change of the cards (search, status, a
  // connection made in the dialog). Sent only when a number moves: the parent
  // rebuilds its channel list on every render.
  const countsKey = useMemo(() => {
    if (!catalog) return "";
    const counts = countByCategory(catalog.items);
    for (const channel of merged.alone) {
      const key = channelCountKey(channel.bucket);
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return JSON.stringify(counts);
  }, [catalog, merged]);
  useEffect(() => {
    onCountsRef.current?.(countsKey ? (JSON.parse(countsKey) as Record<string, number>) : null);
  }, [countsKey]);

  // The cards of the active tab, paged locally (the whole catalog is loaded):
  // the featured apps first, the channels in the middle, then every other app.
  const data = useMemo(() => {
    if (!catalog && !error) return null;
    const wantedCategories = groupKey === ALL_CATEGORIES ? null : new Set(groupKey.split(","));
    const wantedBuckets = bucketKey === ALL_CATEGORIES ? null : new Set(bucketKey.split(","));
    const apps = (catalog?.items ?? [])
      .filter((item) => !wantedCategories || wantedCategories.has(item.category))
      .map((item): GridRow => ({ type: "connector", item, channel: merged.twins[item.service] }));
    const rank = (row: GridRow) => (row.type === "connector" ? (FEATURED_RANK.get(row.item.service) ?? -1) : -1);
    const rows: GridRow[] = [
      ...apps.filter((row) => rank(row) >= 0).sort((a, b) => rank(a) - rank(b)),
      ...merged.alone
        .filter((channel) => !wantedBuckets || wantedBuckets.has(channel.bucket))
        .map((channel): GridRow => ({ type: "channel", channel })),
      ...apps.filter((row) => rank(row) < 0),
    ];
    const pages = Math.max(1, Math.ceil(rows.length / CONNECTORS_PAGE_SIZE));
    const current = Math.min(page, pages);
    return {
      categories: catalog?.categories ?? [],
      total: rows.length,
      page: current,
      pages,
      rows: rows.slice((current - 1) * CONNECTORS_PAGE_SIZE, current * CONNECTORS_PAGE_SIZE),
    };
  }, [catalog, error, merged, groupKey, bucketKey, page]);

  const categoryLabel = useCallback(
    (row: Pick<ConnectorCategory, "id" | "label" | "label_fr"> | undefined, id = "") =>
      String(
        t(`settings.connectors.categories.${row?.id ?? id}`, {
          defaultValue: (i18n.language || "").startsWith("fr") ? row?.label_fr ?? id : row?.label ?? id,
        }),
      ),
    [i18n.language, t],
  );
  const categoriesById = useMemo(
    () => Object.fromEntries((data?.categories ?? []).map((row) => [row.id, row])),
    [data?.categories],
  );
  const selectedSummary =
    data?.rows.flatMap((row) => (row.type === "connector" && row.item.service === selected ? [row.item] : []))[0] ?? null;
  const [panelSummary, setPanelSummary] = useState<ConnectorSummary | null>(null);
  useEffect(() => {
    if (selectedSummary) setPanelSummary(selectedSummary);
  }, [selectedSummary]);

  const onStatusChange = useCallback((service: string, next: ConnectorStatus) => {
    setCatalog((current) => {
      if (!current) return current;
      const item = current.items.find((row) => row.service === service);
      const delta = (next.connected ? 1 : 0) - (item?.status.connected ? 1 : 0);
      return {
        ...current,
        connected_total: current.connected_total + delta,
        items: current.items.map((row) => (row.service === service ? { ...row, status: next } : row)),
        categories: current.categories.map((row) =>
          item && row.id === item.category ? { ...row, connected: row.connected + delta } : row,
        ),
      };
    });
    setPanelSummary((current) => (current && current.service === service ? { ...current, status: next } : current));
  }, []);

  if (!token) return null;
  if (data && data.total === 0 && !error) return null;

  return (
    <div className="flex flex-col gap-3 pb-6" data-testid="connectors-settings">
      <div ref={listTopRef} className="scroll-mt-4 space-y-4">
          {error ? (
            <div role="alert" className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 px-4 py-10 text-center text-[13px]" data-testid="connectors-error">
              <p className="text-destructive">{tx("loadError", "Connectors could not be loaded.")}</p>
              <p className="max-w-md text-muted-foreground" dir="auto">
                {error}
              </p>
              <Button type="button" size="sm" variant="outline" onClick={() => setReload((n) => n + 1)}>
                <RefreshCw className="me-1.5 h-3.5 w-3.5" aria-hidden />
                {tx("retry", "Try again")}
              </Button>
            </div>
          ) : null}
          {loading && !data ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2.5" role="status" aria-label={tx("loading", "Loading connectors...")}>
              {Array.from({ length: CONNECTORS_PAGE_SIZE }, (_, index) => (
                <div key={index} className="h-[104px] animate-pulse rounded-2xl border border-border/50 bg-muted/40" />
              ))}
            </div>
          ) : data && data.rows.length ? (
            <div
              className={cn(
                // Columns follow the width actually available, not the viewport.
                "grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2.5 transition-opacity duration-150",
                loading && "opacity-60",
              )}
              data-testid="connectors-grid"
            >
              {data.rows.map((row, index) => (
                <motion.div
                  key={row.type === "channel" ? `channel:${row.channel.name}` : row.item.service}
                  initial={reduceMotion ? false : { opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ ...spring, delay: reduceMotion ? 0 : Math.min(index, 8) * 0.02 }}
                >
                  {row.type === "channel" ? (
                    <ChannelCard channel={row.channel} onOpen={onOpenChannel} tx={tx} />
                  ) : (
                    <ConnectorCard
                      item={row.item}
                      category={categoryLabel(categoriesById[row.item.category], row.item.category)}
                      channel={row.channel}
                      selected={selected === row.item.service}
                      onSelect={(service) => setSelected(selected === service ? null : service)}
                      tx={tx}
                    />
                  )}
                </motion.div>
              ))}
            </div>
          ) : null}
        {data && data.pages > 1 ? <Pagination page={data.page} pages={data.pages} onPage={goToPage} tx={tx} /> : null}
      </div>

      <Dialog
        open={Boolean(selected && panelSummary?.service === selected)}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        {selected && panelSummary && panelSummary.service === selected ? (
          <ConnectorDetailPanel
            key={selected}
            token={token}
            summary={panelSummary}
            category={categoryLabel(categoriesById[panelSummary.category], panelSummary.category)}
            channel={merged.twins[selected]}
            renderChannel={renderChannel}
            onClose={() => setSelected(null)}
            onStatusChange={onStatusChange}
            tx={tx}
          />
        ) : null}
      </Dialog>
    </div>
  );
}

function Segmented({
  label,
  value,
  options,
  onChange,
  testId,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
  testId: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={SEGMENT_GROUP_CLASS} data-testid={testId}>
      {options.map(([id, text]) => (
        <button
          key={id || "all"}
          type="button"
          role="radio"
          aria-checked={value === id}
          onClick={() => onChange(id)}
          className={segmentClass(value === id)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

const CARD_CLASS =
  "flex h-full w-full min-w-0 items-start gap-3 rounded-2xl border bg-card/90 p-3 text-start shadow-sm transition-[transform,border-color,box-shadow] duration-150 hover:border-border hover:shadow-md active:scale-[0.99]";

function ChannelCard({
  channel,
  onOpen,
  tx,
}: {
  channel: ChannelCardEntry;
  onOpen?: (name: string) => void;
  tx: Translate;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen?.(channel.name)}
      data-testid={`channel-card-${channel.name}`}
      className={cn(CARD_CLASS, "border-border/60")}
    >
      <ConnectorIcon icon={channel.icon} name={channel.label} />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-[14px] font-semibold text-foreground">{channel.label}</span>
          {channel.connected ? <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500" aria-hidden /> : null}
        </span>
        <span className="block truncate text-[12px] text-muted-foreground">{channel.kind}</span>
        {channel.badges.length ? (
          <span className="mt-1.5 flex flex-wrap items-center gap-1">
            {channel.badges.map((badge) => (
              <Badge key={badge}>{badge}</Badge>
            ))}
          </span>
        ) : null}
        <span
          className={cn(
            "mt-1.5 block truncate text-[12px] font-medium",
            channel.connected ? "text-emerald-700 dark:text-emerald-300" : "text-muted-foreground",
          )}
        >
          {channel.connected ? tx("connected", "Connected") : tx("notConnected", "Not connected")}
        </span>
      </span>
    </button>
  );
}

function ConnectorCard({
  item,
  category,
  channel,
  selected,
  onSelect,
  tx,
}: {
  item: ConnectorSummary;
  category: string;
  /** The chat channel of the same app, set up from this card's dialog too. */
  channel?: ChannelCardEntry;
  selected: boolean;
  onSelect: (service: string) => void;
  tx: Translate;
}) {
  const hasOAuth = item.auth_types.includes("oauth2");
  const hasKey = item.auth_types.some((kind) => kind !== "oauth2");
  const off = item.status.connected && item.status.enabled === false;
  return (
    <button
      type="button"
      onClick={() => onSelect(item.service)}
      aria-pressed={selected}
      data-testid={`connector-card-${item.service}`}
      className={cn(CARD_CLASS, selected ? "border-primary/50 ring-1 ring-primary/30" : "border-border/60")}
    >
      <ConnectorIcon icon={item.icon} name={item.name} />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-[14px] font-semibold text-foreground">{item.name}</span>
          {item.status.connected ? (
            <span className={cn("h-2 w-2 shrink-0 rounded-full", off ? "bg-amber-500" : "bg-emerald-500")} aria-hidden />
          ) : null}
        </span>
        <span className="block truncate text-[12px] text-muted-foreground">{category}</span>
        <span className="mt-1.5 flex flex-wrap items-center gap-1">
          {hasOAuth ? <Badge>OAuth</Badge> : null}
          {hasKey ? <Badge>{tx("badgeKey", "API key")}</Badge> : null}
          {channel ? (
            <span
              className={cn(
                "rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.04em]",
                channel.connected ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-muted text-muted-foreground",
              )}
              data-testid={`connector-channel-badge-${item.service}`}
            >
              {tx("badgeChannel", "Channel")}
            </span>
          ) : null}
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {tx("actionsCount", "Actions: {{count}}", { count: item.actions })}
          </span>
        </span>
        <span
          className={cn(
            "mt-1.5 block truncate text-[12px] font-medium",
            item.status.connected ? (off ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300") : "text-muted-foreground",
          )}
          dir="auto"
        >
          {item.status.connected
            ? off
              ? tx("off", "Turned off")
              : item.status.account
                ? tx("connectedAs", "Connected as {{account}}", { account: item.status.account })
                : tx("connected", "Connected")
            : tx("notConnected", "Not connected")}
        </span>
      </span>
    </button>
  );
}

function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
      {children}
    </span>
  );
}

function Pagination({
  page,
  pages,
  onPage,
  tx,
  testId = "connectors-pagination",
}: {
  page: number;
  pages: number;
  onPage: (page: number) => void;
  tx: Translate;
  testId?: string;
}) {
  return (
    <nav aria-label={tx("pagination", "Pages")} className="flex flex-wrap items-center justify-between gap-2 pt-1" data-testid={testId}>
      <span className="text-[12px] tabular-nums text-muted-foreground">{tx("pageOf", "Page {{page}} of {{pages}}", { page, pages })}</span>
      <div className="flex flex-wrap items-center gap-1">
        <Button type="button" size="sm" variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)} data-testid={`${testId}-prev`}>
          <ChevronLeft className="me-1 h-3.5 w-3.5 rtl:rotate-180" aria-hidden />
          {tx("previous", "Previous")}
        </Button>
        {pageWindow(page, pages).map((value, index) =>
          value === "gap" ? (
            <span key={`gap-${index}`} className="px-1 text-[12px] text-muted-foreground" aria-hidden>
              ...
            </span>
          ) : (
            <button
              key={value}
              type="button"
              aria-current={value === page ? "page" : undefined}
              onClick={() => onPage(value)}
              data-testid={`${testId}-page-${value}`}
              className={cn(
                "h-9 min-w-9 rounded-lg px-2 text-[12.5px] font-medium tabular-nums transition-[color,background-color,transform] duration-150 active:scale-[0.97]",
                value === page ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {value}
            </button>
          ),
        )}
        <Button type="button" size="sm" variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)} data-testid={`${testId}-next`}>
          {tx("next", "Next")}
          <ChevronRight className="ms-1 h-3.5 w-3.5 rtl:rotate-180" aria-hidden />
        </Button>
      </div>
    </nav>
  );
}

function ConnectorDetailPanel({
  token,
  summary,
  category,
  channel,
  renderChannel,
  onClose,
  onStatusChange,
  tx,
}: {
  token: string;
  summary: ConnectorSummary;
  category: string;
  /** The chat channel of the same app: its setup comes under the connection. */
  channel?: ChannelCardEntry;
  renderChannel?: (name: string) => ReactNode;
  onClose: () => void;
  onStatusChange: (service: string, status: ConnectorStatus) => void;
  tx: Translate;
}) {
  const [detail, setDetail] = useState<ConnectorDetail | null>(null);
  const [actionQuery, setActionQuery] = useState("");
  const [op, setOp] = useState("");
  const debouncedAction = useDebounced(actionQuery.trim(), 200);
  const [page, setPage] = useKeyedPage(JSON.stringify([summary.service, debouncedAction, op]));
  const [actions, setActions] = useState<ConnectorActionsPage | null>(null);
  const [actionsError, setActionsError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setActionsError("");
    fetchConnectorActions(token, summary.service, { q: debouncedAction, op, page, page_size: ACTIONS_PAGE_SIZE }, controller.signal)
      .then((next) => setActions(next))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setActionsError(errorMessage(err));
      });
    return () => controller.abort();
  }, [token, summary.service, debouncedAction, op, page]);

  const docs = detail?.docs || "";
  return (
    <DialogContent
      showCloseButton={false}
      className="flex max-h-[88vh] max-w-xl flex-col gap-0 overflow-hidden rounded-2xl p-0 sm:rounded-2xl"
      data-testid="connector-detail"
    >
      {/* The header stays put; the connection, the channel and the actions scroll under it. */}
      <div className="flex shrink-0 items-start gap-3 border-b border-border/60 px-5 pb-4 pt-5">
        <ConnectorIcon icon={summary.icon} name={summary.name} size="lg" />
        <div className="min-w-0 flex-1">
          <DialogTitle className="truncate text-[16px] font-semibold leading-tight text-foreground">{summary.name}</DialogTitle>
          <p className="truncate text-[12px] text-muted-foreground">{category}</p>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
            {summary.homepage ? (
              <button type="button" className="inline-flex items-center gap-1 text-[12px] text-primary underline-offset-4 hover:underline" onClick={() => void openInOsBrowser(token, summary.homepage)}>
                <ExternalLink className="h-3 w-3" aria-hidden />
                {tx("website", "Website")}
              </button>
            ) : null}
            {docs ? (
              <button type="button" className="inline-flex items-center gap-1 text-[12px] text-primary underline-offset-4 hover:underline" onClick={() => void openInOsBrowser(token, docs)}>
                <ExternalLink className="h-3 w-3" aria-hidden />
                {tx("apiReference", "API reference")}
              </button>
            ) : null}
          </div>
        </div>
        <Button type="button" size="icon" variant="ghost" className="h-9 w-9 shrink-0 rounded-full" onClick={onClose} aria-label={tx("close", "Close")} data-testid="connector-detail-close">
          <X className="h-4 w-4" aria-hidden />
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 pb-5 pt-4" data-testid="connector-detail-body">
      <DialogDescription
        className={cn("text-[12.5px] leading-5 text-muted-foreground [text-wrap:pretty]", !summary.description && "sr-only")}
        dir="auto"
      >
        {summary.description || summary.name}
      </DialogDescription>

      <section className="space-y-2.5" data-testid="connector-connection">
        <h4 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
          {tx("connectionTitle", "Connection")}
        </h4>
        <ConnectorSetupForm token={token} service={summary.service} onStatusChange={onStatusChange} onLoaded={setDetail} />
      </section>

      {channel && renderChannel ? (
        <section className="space-y-2.5 border-t border-border/60 pt-4" data-testid="connector-channel">
          <h4 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
            {tx("channelTitle", "Chat channel")}
          </h4>
          {renderChannel(channel.name)}
        </section>
      ) : null}

      <section className="space-y-2.5 border-t border-border/60 pt-4" data-testid="connector-actions">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
            {tx("actionsTitle", "What the agent can do")}
          </h4>
          {actions ? <span className="text-[11px] tabular-nums text-muted-foreground">{actions.total}</span> : null}
        </div>
        {/* One row: the action search, then the kind of action, drawn like the sign-in choice above. */}
        <div className="flex flex-wrap items-center gap-2" data-testid="connector-actions-toolbar">
          <div className="relative min-w-[12rem] flex-1">
            <Search className="pointer-events-none absolute start-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              value={actionQuery}
              onChange={(event) => setActionQuery(event.target.value)}
              placeholder={tx("searchActions", "Search actions")}
              className="h-9 rounded-lg ps-8 text-[12.5px]"
              data-testid="connector-actions-search"
            />
          </div>
          <Segmented
            label={tx("opFilter", "Kind of action")}
            value={op}
            onChange={setOp}
            options={[
              ["", tx("opAll", "All actions")],
              ["read", tx("opRead", "Read")],
              ["write", tx("opWrite", "Write")],
              ["destructive", tx("opDestructive", "Delete")],
            ]}
            testId="connector-actions-op"
          />
        </div>
        {actionsError ? (
          <p role="alert" className="text-[12.5px] text-destructive">
            {actionsError}
          </p>
        ) : actions && actions.items.length === 0 ? (
          <p className="py-3 text-[12.5px] text-muted-foreground">{tx("noActions", "No action matches.")}</p>
        ) : (
          <ul className="space-y-1.5">
            {(actions?.items ?? []).map((action) => (
              <li key={action.name} className="rounded-xl border border-border/50 bg-background/60 px-3 py-2">
                <div className="flex min-w-0 items-center gap-2">
                  <code className="min-w-0 truncate font-mono text-[12px] text-foreground" dir="ltr">
                    {action.name}
                  </code>
                  <span
                    className={cn(
                      "shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold",
                      action.op === "read"
                        ? "bg-sky-500/10 text-sky-700 dark:text-sky-300"
                        : action.op === "destructive"
                          ? "bg-destructive/10 text-destructive"
                          : "bg-amber-500/10 text-amber-700 dark:text-amber-300",
                    )}
                  >
                    {action.op === "read" ? tx("opRead", "Read") : action.op === "destructive" ? tx("opDestructive", "Delete") : tx("opWrite", "Write")}
                  </span>
                </div>
                {action.endpoint ? (
                  <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground" dir="ltr" data-testid="connector-action-endpoint">
                    {action.endpoint}
                  </p>
                ) : null}
                {action.description ? (
                  <p className="mt-0.5 line-clamp-2 text-[12px] leading-5 text-muted-foreground" dir="auto">
                    {action.description}
                  </p>
                ) : null}
                {action.params.length ? (
                  <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground/90" dir="ltr">
                    {action.params
                      .slice(0, 6)
                      .map(([name, , required]) => `${name}${required ? "*" : ""}`)
                      .join(", ")}
                    {action.params.length > 6 ? ", ..." : ""}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {actions && actions.pages > 1 ? (
          <Pagination page={actions.page} pages={actions.pages} onPage={setPage} tx={tx} testId="connector-actions-pagination" />
        ) : null}
      </section>
      </div>
    </DialogContent>
  );
}
