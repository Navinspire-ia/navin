import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  Download,
  Gauge,
  Globe2,
  KeyRound,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Trash2,
  TrendingUp,
} from "lucide-react";

import {
  DeskSection,
  EmptyState,
  ErrorBanner,
  KpiTile,
  ScoreRing,
  SEVERITY_ORDER,
  SeverityChip,
  TrendChart,
  formatNumber,
  latnLocale,
  type DeskSeverity,
} from "@/components/studio/desk-ui";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  deleteSeoSite,
  downloadDeskFile,
  exportSeoReport,
  fetchSeoAudit,
  fetchSeoSnapshot,
  fetchSerpHistory,
  runSeoAudit,
  saveSeoKeys,
  takeSerpSnapshot,
  type SeoKeyName,
  type SeoReport,
  type SeoSnapshot,
  type SerpSnapshot,
} from "@/lib/seo-desk-api";
import { cn } from "@/lib/utils";

type Tx = (key: string, fallback: string, values?: Record<string, unknown>) => string;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** Where the tracked site ranks in a snapshot (null = not in the results). */
export function rankOf(snapshot: SerpSnapshot, host: string): number | null {
  if (!host) return null;
  const hit = snapshot.results.find((result) => hostOf(result.url).endsWith(host));
  return hit ? hit.position : null;
}

const METRIC_LABELS: Record<string, string> = {
  lcp_ms: "LCP",
  cls: "CLS",
  tbt_ms: "TBT",
  speed_index_ms: "Speed Index",
};

export function SeoDesk({ token, onAskAgent }: { token: string; onAskAgent?: (prompt: string) => void }) {
  const { t, i18n } = useTranslation();
  const tx: Tx = useCallback(
    (key, fallback, values) => String(t(`seoDesk.${key}`, { defaultValue: fallback, ...(values ?? {}) })),
    [t],
  );
  const locale = i18n.language;
  const [snap, setSnap] = useState<SeoSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(25);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [selected, setSelected] = useState<string>("");
  const [report, setReport] = useState<SeoReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await fetchSeoSnapshot(token);
      setSnap(next);
      setError(null);
      setSelected((current) => current || next.sites[0]?.site || "");
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) {
      setReport(null);
      return;
    }
    let live = true;
    setReportLoading(true);
    fetchSeoAudit(token, selected)
      .then((answer) => { if (live) setReport(answer.report); })
      .catch((failure) => { if (live) setError(errorText(failure)); })
      .finally(() => { if (live) setReportLoading(false); });
    return () => { live = false; };
  }, [selected, token]);

  useEffect(() => {
    if (!running) return;
    setElapsed(0);
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  const runAudit = async (target: string) => {
    if (!target.trim() || running) return;
    setRunning(true);
    setError(null);
    try {
      const answer = await runSeoAudit(token, target.trim(), maxPages);
      setReport(answer.report);
      setSelected(answer.site);
      setUrl("");
      await load();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setRunning(false);
    }
  };

  const site = snap?.sites.find((item) => item.site === selected) ?? null;

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground" data-testid="seo-desk-loading">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="seo-desk">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-5 py-4">
        <form
          className="flex flex-wrap items-center gap-2 rounded-2xl border border-border/60 bg-card/60 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void runAudit(url);
          }}
        >
          <Globe2 className="h-4 w-4 text-muted-foreground" aria-hidden />
          <Input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder={tx("urlPlaceholder", "https://your-site.com")}
            aria-label={tx("urlLabel", "Site URL")}
            className="h-9 min-w-[16rem] flex-1"
            disabled={running}
          />
          <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            {tx("maxPages", "Pages")}
            <select
              value={maxPages}
              onChange={(event) => setMaxPages(Number(event.target.value))}
              className="h-9 rounded-md border border-input bg-background px-2 text-[12.5px] text-foreground"
              disabled={running}
            >
              {[10, 25, 50, 100, 200].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <Button type="submit" disabled={running || !url.trim()} className="h-9 rounded-lg">
            {running ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : <Play className="me-1.5 h-4 w-4" aria-hidden />}
            {running ? tx("running", "Auditing... {{seconds}}s", { seconds: elapsed }) : tx("run", "Run audit")}
          </Button>
          <Button type="button" variant="outline" className="h-9 rounded-lg" onClick={() => setKeysOpen(true)}>
            <KeyRound className="me-1.5 h-4 w-4" aria-hidden />
            {tx("keys", "Data sources")}
          </Button>
        </form>
        {running ? (
          <p className="text-[12px] text-muted-foreground" role="status">
            {tx("runningHelp", "Crawling pages, checking robots.txt and the sitemap, measuring Core Web Vitals. Large sites take a few minutes.")}
          </p>
        ) : null}
        {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

        {!snap?.sites.length && !running ? (
          <EmptyState
            icon={<TrendingUp className="h-5 w-5" aria-hidden />}
            title={tx("emptyTitle", "No audit yet")}
            body={tx("emptyBody", "Enter your site URL and run a full audit: on-page, structured data, indexability, Core Web Vitals and a health score you can track over time.")}
          />
        ) : null}

        {snap && snap.sites.length > 0 ? (
          <div className="grid gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
            <aside className="flex flex-col gap-1.5" aria-label={tx("sites", "Sites")}>
              <p className="px-1 text-[11.5px] font-medium uppercase tracking-wide text-muted-foreground">{tx("sites", "Sites")}</p>
              {snap.sites.map((item) => (
                <button
                  key={item.site}
                  type="button"
                  onClick={() => setSelected(item.site)}
                  aria-pressed={item.site === selected}
                  className={cn(
                    "flex items-center gap-2 rounded-xl border px-3 py-2 text-start transition-colors",
                    item.site === selected ? "border-foreground/30 bg-foreground/[0.05]" : "border-border/60 hover:bg-muted/40",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium text-foreground">{item.site}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {new Date(item.last_audit_at).toLocaleString(latnLocale(locale))}
                    </span>
                  </span>
                  <span className="rounded-full bg-foreground/[0.06] px-2 py-0.5 text-[12px] font-semibold tabular-nums">{item.last_score}</span>
                </button>
              ))}
            </aside>

            <div className="flex min-w-0 flex-col gap-4">
              {reportLoading && !report ? (
                <div className="flex justify-center py-10 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" aria-hidden /></div>
              ) : null}
              {site && report ? (
                <SiteReport
                  tx={tx}
                  locale={locale}
                  token={token}
                  site={site}
                  report={report}
                  keys={snap.keys}
                  running={running}
                  onRerun={() => void runAudit(site.url)}
                  onOpenKeys={() => setKeysOpen(true)}
                  onDeleted={() => {
                    setSelected("");
                    setReport(null);
                    void load();
                  }}
                  onError={setError}
                  onAskAgent={onAskAgent}
                />
              ) : null}
            </div>
          </div>
        ) : null}

        {snap ? (
          <SerpTracker tx={tx} locale={locale} token={token} snap={snap} trackedHost={site ? hostOf(site.url) : ""} onOpenKeys={() => setKeysOpen(true)} onChanged={() => void load()} />
        ) : null}
      </div>
      {snap ? (
        <KeysDialog
          tx={tx}
          token={token}
          open={keysOpen}
          keys={snap.keys}
          onClose={() => setKeysOpen(false)}
          onSaved={(keys) => setSnap((prev) => (prev ? { ...prev, keys } : prev))}
        />
      ) : null}
    </div>
  );
}

function SiteReport({
  tx,
  locale,
  token,
  site,
  report,
  keys,
  running,
  onRerun,
  onOpenKeys,
  onDeleted,
  onError,
  onAskAgent,
}: {
  tx: Tx;
  locale: string;
  token: string;
  site: SeoSnapshot["sites"][number];
  report: SeoReport;
  keys: SeoSnapshot["keys"];
  running: boolean;
  onRerun: () => void;
  onOpenKeys: () => void;
  onDeleted: () => void;
  onError: (message: string) => void;
  onAskAgent?: (prompt: string) => void;
}) {
  const [severity, setSeverity] = useState<DeskSeverity | "all">("all");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const findings = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return report.findings.filter((item) =>
      (severity === "all" || item.severity === severity)
      && (!needle || `${item.title} ${item.message} ${item.url ?? ""}`.toLowerCase().includes(needle)),
    );
  }, [query, report.findings, severity]);
  const psi = report.metadata.performance?.psi;
  const crux = report.metadata.performance?.crux;
  const trend = [...site.audits].reverse().map((entry) => ({
    x: new Date(entry.at).toLocaleDateString(latnLocale(locale)),
    y: entry.score,
  }));

  const download = async (format: "html" | "md" | "json") => {
    setExporting(true);
    try {
      downloadDeskFile(await exportSeoReport(token, site.site, format));
    } catch (failure) {
      onError(errorText(failure));
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <DeskSection
        title={site.url}
        actions={
          <>
            <Button size="sm" variant="outline" className="rounded-full" onClick={onRerun} disabled={running}>
              <RefreshCw className="me-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("rerun", "Audit again")}
            </Button>
            {(["html", "md", "json"] as const).map((format) => (
              <Button key={format} size="sm" variant="ghost" className="rounded-full" disabled={exporting} onClick={() => void download(format)}>
                <Download className="me-1 h-3.5 w-3.5" aria-hidden />
                {format.toUpperCase()}
              </Button>
            ))}
            <Button
              size="sm"
              variant="ghost"
              className="rounded-full text-muted-foreground"
              onClick={() => {
                void deleteSeoSite(token, site.site).then(onDeleted).catch((failure) => onError(errorText(failure)));
              }}
              aria-label={tx("deleteSite", "Remove this site")}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-5">
          <ScoreRing score={report.health.score} grade={tx("grade", "Grade {{grade}}", { grade: report.health.grade })} label={tx("health", "Health score")} />
          <div className="grid min-w-0 flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
            <KpiTile label={tx("pages", "Pages crawled")} value={formatNumber(report.health.page_count, locale)} />
            <KpiTile label={tx("findings", "Findings")} value={formatNumber(report.health.finding_count, locale)} />
            <KpiTile
              label={tx("critical", "Critical + high")}
              value={formatNumber((report.health.counts.critical ?? 0) + (report.health.counts.high ?? 0), locale)}
              tone={(report.health.counts.critical ?? 0) + (report.health.counts.high ?? 0) > 0 ? "bad" : "good"}
            />
            <KpiTile
              label={tx("performance", "Performance (PSI)")}
              value={psi?.status === "ok" && typeof psi.performance_score === "number" ? Math.round(psi.performance_score * 100) : "-"}
              hint={psi?.status === "ok" ? tx("mobile", "Mobile") : tx("noPsi", "Needs a PSI key")}
            />
          </div>
        </div>
        {trend.length > 1 ? (
          <div className="mt-4">
            <p className="mb-1 text-[11.5px] font-medium text-muted-foreground">{tx("trend", "Health score over time")}</p>
            <TrendChart points={trend} label={tx("trend", "Health score over time")} max={100} />
          </div>
        ) : null}
      </DeskSection>

      <DeskSection title={<span className="inline-flex items-center gap-1.5"><Gauge className="h-4 w-4" aria-hidden />{tx("cwv", "Core Web Vitals")}</span>}>
        <div className="grid gap-3 md:grid-cols-2">
          <VitalsCard
            tx={tx}
            locale={locale}
            title={tx("psiTitle", "Lab data (PageSpeed Insights, mobile)")}
            block={psi}
            ready={keys.psi_ready}
            onOpenKeys={onOpenKeys}
          />
          <VitalsCard
            tx={tx}
            locale={locale}
            title={tx("cruxTitle", "Field data (Chrome UX Report, 28 days)")}
            block={crux}
            ready={keys.crux_ready}
            onOpenKeys={onOpenKeys}
          />
        </div>
      </DeskSection>

      <DeskSection
        title={tx("findingsTitle", "Findings ({{count}})", { count: report.findings.length })}
        actions={
          <>
            <div className="relative">
              <Search className="pointer-events-none absolute start-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tx("search", "Search findings")} className="h-8 w-48 ps-7 text-[12.5px]" />
            </div>
            {(["all", ...SEVERITY_ORDER] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setSeverity(value)}
                aria-pressed={severity === value}
                className={cn(
                  "h-7 rounded-full px-2.5 text-[11.5px] font-medium transition-colors",
                  severity === value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted/60",
                )}
              >
                {value === "all" ? tx("all", "All") : `${tx(`severity.${value}`, value)} ${report.health.counts[value] ?? 0}`}
              </button>
            ))}
          </>
        }
      >
        {findings.length === 0 ? (
          <p className="py-6 text-center text-[12.5px] text-muted-foreground">{tx("noFindings", "Nothing to fix in this view.")}</p>
        ) : (
          <ul className="divide-y divide-border/50">
            {findings.map((item, index) => {
              const key = `${item.code}-${item.url ?? ""}-${index}`;
              const expanded = open === key;
              return (
                <li key={key}>
                  <button
                    type="button"
                    onClick={() => setOpen(expanded ? null : key)}
                    aria-expanded={expanded}
                    className="flex w-full items-center gap-2 py-2.5 text-start"
                  >
                    <SeverityChip severity={item.severity} label={tx(`severity.${item.severity}`, item.severity)} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium text-foreground">{item.title}</span>
                      {item.url ? <span className="block truncate text-[11px] text-muted-foreground">{item.url}</span> : null}
                    </span>
                    <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", expanded && "rotate-180")} aria-hidden />
                  </button>
                  <AnimatePresence initial={false}>
                    {expanded ? (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden"
                      >
                        <div className="mb-3 space-y-2 rounded-xl bg-muted/30 p-3 text-[12.5px]">
                          <p className="text-foreground/90">{item.message}</p>
                          <p>
                            <span className="font-medium text-foreground">{tx("fix", "How to fix")}: </span>
                            <span className="text-foreground/85">{item.recommendation}</span>
                          </p>
                          {item.evidence.length ? (
                            <p className="break-words text-[11.5px] text-muted-foreground">
                              {tx("evidence", "Evidence")}: {item.evidence.map((ev) => `${ev.kind} = ${typeof ev.value === "string" ? ev.value.slice(0, 160) : JSON.stringify(ev.value)}`).join(" | ")}
                            </p>
                          ) : null}
                          {onAskAgent ? (
                            <Button
                              size="sm"
                              variant="outline"
                              className="rounded-full"
                              onClick={() => onAskAgent(`Fix this SEO finding on ${item.url ?? site.url}: ${item.title}. ${item.message} Recommendation: ${item.recommendation}`)}
                            >
                              {tx("askFix", "Ask the agent to fix it")}
                            </Button>
                          ) : null}
                        </div>
                      </motion.div>
                    ) : null}
                  </AnimatePresence>
                </li>
              );
            })}
          </ul>
        )}
      </DeskSection>

      <DeskSection title={tx("pagesTitle", "Pages crawled ({{count}})", { count: report.pages.length })}>
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-card text-start text-muted-foreground">
              <tr>
                <th className="py-1.5 text-start font-medium">{tx("colUrl", "URL")}</th>
                <th className="py-1.5 text-start font-medium">{tx("colStatus", "Status")}</th>
                <th className="py-1.5 text-start font-medium">{tx("colTitle", "Title")}</th>
              </tr>
            </thead>
            <tbody>
              {report.pages.map((page) => (
                <tr key={page.url} className="border-t border-border/40">
                  <td className="max-w-[18rem] truncate py-1.5 pe-2">{page.final_url || page.url}</td>
                  <td className={cn("py-1.5 pe-2 tabular-nums", (page.status ?? 0) >= 400 || !page.status ? "text-[#c2410c]" : "")}>{page.status || tx("network", "network")}</td>
                  <td className="max-w-[20rem] truncate py-1.5">{page.title || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DeskSection>
    </>
  );
}

function VitalsCard({
  tx,
  locale,
  title,
  block,
  ready,
  onOpenKeys,
}: {
  tx: Tx;
  locale: string;
  title: string;
  block?: { status: string; reason?: string; metrics?: Record<string, unknown> | null };
  ready: boolean;
  onOpenKeys: () => void;
}) {
  if (!block || block.status !== "ok") {
    return (
      <div className="rounded-xl border border-dashed border-border/70 p-3">
        <p className="text-[12.5px] font-medium text-foreground">{title}</p>
        <p className="mt-1 text-[12px] text-muted-foreground">
          {ready
            ? tx("vitalsFailed", "The measurement failed this time: {{reason}}", { reason: block?.reason ?? "" })
            : tx("vitalsGap", "No data: add a PageSpeed Insights API key to get real Core Web Vitals. Nothing is estimated.")}
        </p>
        {!ready ? (
          <Button size="sm" variant="outline" className="mt-2 rounded-full" onClick={onOpenKeys}>
            <KeyRound className="me-1.5 h-3.5 w-3.5" aria-hidden />
            {tx("addKey", "Add a key")}
          </Button>
        ) : null}
      </div>
    );
  }
  const metrics = block.metrics ?? {};
  const rows = Object.entries(metrics).map(([name, raw]) => {
    if (raw && typeof raw === "object" && "percentiles" in (raw as Record<string, unknown>)) {
      const p75 = (raw as { percentiles?: { p75?: number | string } }).percentiles?.p75;
      return { name, value: p75 === undefined ? "-" : String(p75) };
    }
    const value = typeof raw === "number" ? raw : null;
    const formatted = value === null ? "-" : name === "cls" ? formatNumber(value, locale, 3) : `${formatNumber(value, locale)} ms`;
    return { name, value: formatted };
  });
  return (
    <div className="rounded-xl border border-border/60 p-3">
      <p className="text-[12.5px] font-medium text-foreground">{title}</p>
      <dl className="mt-2 grid grid-cols-2 gap-2">
        {rows.map((row) => (
          <div key={row.name} className="rounded-lg bg-muted/30 px-2.5 py-1.5">
            <dt className="truncate text-[11px] text-muted-foreground">{METRIC_LABELS[row.name] ?? row.name.replace(/_/g, " ")}</dt>
            <dd className="text-[14px] font-semibold tabular-nums text-foreground">{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SerpTracker({
  tx,
  locale,
  token,
  snap,
  trackedHost,
  onOpenKeys,
  onChanged,
}: {
  tx: Tx;
  locale: string;
  token: string;
  snap: SeoSnapshot;
  trackedHost: string;
  onOpenKeys: () => void;
  onChanged: () => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [active, setActive] = useState(snap.serp_keywords[0] ?? "");
  const [history, setHistory] = useState<SerpSnapshot[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!active) {
      setHistory([]);
      return;
    }
    let live = true;
    fetchSerpHistory(token, active)
      .then((answer) => { if (live) setHistory(answer.snapshots); })
      .catch((failure) => { if (live) setMessage(errorText(failure)); });
    return () => { live = false; };
  }, [active, token]);

  const capture = async (term: string) => {
    if (!term.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const answer = await takeSerpSnapshot(token, term.trim());
      if (answer.status !== "ok") {
        setMessage(answer.reason ?? tx("serpGap", "Add DataForSEO or Semrush credentials to track rankings."));
      } else {
        setActive(term.trim());
        setKeyword("");
        const next = await fetchSerpHistory(token, term.trim());
        setHistory(next.snapshots);
        onChanged();
      }
    } catch (failure) {
      setMessage(errorText(failure));
    } finally {
      setBusy(false);
    }
  };

  const latest = history[history.length - 1];
  const points = history.map((item) => ({
    x: new Date(item.captured_at).toLocaleDateString(latnLocale(locale)),
    y: rankOf(item, trackedHost),
  }));

  return (
    <DeskSection
      title={tx("serpTitle", "Rankings (SERP tracking)")}
      actions={
        <form
          className="flex items-center gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            void capture(keyword);
          }}
        >
          <Input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder={tx("keyword", "Keyword to track")} className="h-8 w-52 text-[12.5px]" />
          <Button type="submit" size="sm" className="rounded-full" disabled={busy || !keyword.trim()}>
            {busy ? <Loader2 className="me-1 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            {tx("snapshot", "Check now")}
          </Button>
        </form>
      }
    >
      {!snap.keys.serp_ready ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          {tx("serpGap", "Add DataForSEO or Semrush credentials to track rankings. Positions are never estimated.")}
          <Button size="sm" variant="outline" className="rounded-full" onClick={onOpenKeys}>{tx("addKey", "Add a key")}</Button>
        </div>
      ) : null}
      {message ? <p className="mb-2 text-[12px] text-[#c2410c]" role="alert">{message}</p> : null}
      {snap.serp_keywords.length ? (
        <div className="mb-3 flex flex-wrap gap-1">
          {snap.serp_keywords.map((term) => (
            <button
              key={term}
              type="button"
              onClick={() => setActive(term)}
              aria-pressed={term === active}
              className={cn("h-7 rounded-full px-2.5 text-[12px]", term === active ? "bg-foreground text-background" : "bg-muted/50 text-foreground/80 hover:bg-muted")}
            >
              {term}
            </button>
          ))}
        </div>
      ) : null}
      {history.length > 1 && trackedHost ? (
        <>
          <p className="mb-1 text-[11.5px] text-muted-foreground">{tx("serpTrend", "Position of {{host}} (1 is the top)", { host: trackedHost })}</p>
          <TrendChart points={points} label={tx("serpTrendLabel", "Position over time")} invert max={Math.max(10, ...points.map((p) => p.y ?? 0))} />
        </>
      ) : null}
      {latest ? (
        <ol className="mt-2 space-y-1">
          {latest.results.slice(0, 10).map((result) => (
            <li
              key={`${result.position}-${result.url}`}
              className={cn(
                "flex items-center gap-2 rounded-lg px-2 py-1 text-[12.5px]",
                trackedHost && hostOf(result.url).endsWith(trackedHost) && "bg-[#2f7de1]/10",
              )}
            >
              <span className="w-6 shrink-0 text-end font-semibold tabular-nums">{result.position}</span>
              <span className="min-w-0 flex-1 truncate">{result.title || result.url}</span>
              <span className="max-w-[14rem] shrink-0 truncate text-[11px] text-muted-foreground">{hostOf(result.url)}</span>
            </li>
          ))}
          <li className="pt-1 text-[11px] text-muted-foreground">
            {tx("serpSource", "Source: {{source}}, {{date}}", {
              source: latest.source,
              date: new Date(latest.captured_at).toLocaleString(latnLocale(locale)),
            })}
          </li>
        </ol>
      ) : snap.keys.serp_ready ? (
        <p className="text-[12px] text-muted-foreground">{tx("serpEmpty", "Add a keyword to start tracking its ranking.")}</p>
      ) : null}
    </DeskSection>
  );
}

const KEY_FIELDS: Array<{ key: SeoKeyName; label: string; secret: boolean }> = [
  { key: "psi_api_key", label: "PageSpeed Insights API key", secret: true },
  { key: "crux_api_key", label: "Chrome UX Report API key (optional, PSI key is reused)", secret: true },
  { key: "dataforseo_login", label: "DataForSEO login", secret: false },
  { key: "dataforseo_password", label: "DataForSEO password", secret: true },
  { key: "semrush_api_key", label: "Semrush API key", secret: true },
];

function KeysDialog({
  tx,
  token,
  open,
  keys,
  onClose,
  onSaved,
}: {
  tx: Tx;
  token: string;
  open: boolean;
  keys: SeoSnapshot["keys"];
  onClose: () => void;
  onSaved: (keys: SeoSnapshot["keys"]) => void;
}) {
  const [values, setValues] = useState<Partial<Record<SeoKeyName, string>>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setValues({});
      setError(null);
    }
  }, [open]);
  const submit = async (clear: SeoKeyName[] = []) => {
    setSaving(true);
    setError(null);
    try {
      const answer = await saveSeoKeys(token, values, clear);
      onSaved(answer.keys);
      setValues({});
      if (!clear.length) onClose();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{tx("keysTitle", "SEO data sources")}</DialogTitle>
          <DialogDescription>
            {tx("keysHelp", "Optional keys for measured data. Without them the audit still runs; Core Web Vitals and rankings are reported as missing instead of guessed. Keys are stored encrypted in Settings.")}
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {KEY_FIELDS.map((field) => {
            const state = keys[field.key];
            return (
              <label key={field.key} className="grid gap-1">
                <span className="flex items-center justify-between gap-2 text-[12.5px] font-medium text-foreground">
                  {tx(`key.${field.key}`, field.label)}
                  {state.configured ? (
                    <span className="flex items-center gap-1.5 text-[11px] font-normal text-[#1f9d74]">
                      {state.source === "environment" ? tx("fromEnv", "from environment") : state.hint}
                      {state.source === "settings" ? (
                        <button type="button" className="text-muted-foreground underline-offset-2 hover:underline" onClick={() => void submit([field.key])}>
                          {tx("remove", "Remove")}
                        </button>
                      ) : null}
                    </span>
                  ) : null}
                </span>
                <Input
                  type={field.secret ? "password" : "text"}
                  autoComplete="off"
                  value={values[field.key] ?? ""}
                  onChange={(event) => setValues((prev) => ({ ...prev, [field.key]: event.target.value }))}
                  placeholder={state.configured ? tx("keepCurrent", "Leave empty to keep the current value") : ""}
                />
              </label>
            );
          })}
          {error ? <p className="text-[12px] text-[#c2410c]" role="alert">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>{tx("cancel", "Cancel")}</Button>
            <Button type="submit" disabled={saving || !Object.values(values).some((value) => value?.trim())}>
              {saving ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
              {tx("save", "Save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
