import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useTranslation } from "react-i18next";
import { BadgeDollarSign, Check, Download, FileUp, Loader2, Play, RotateCcw, Trash2, X } from "lucide-react";

import {
  DeskSection,
  EmptyState,
  ErrorBanner,
  KpiTile,
  ScoreRing,
  SeverityChip,
  formatMoney,
  formatNumber,
  formatPercent,
  latnLocale,
} from "@/components/studio/desk-ui";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  analyzeAds,
  deleteAdsExport,
  exportAdsChanges,
  exportAdsReport,
  fetchAdsChanges,
  fetchAdsSnapshot,
  fileToBase64,
  ingestAdsExport,
  setAdsChangeStatus,
  toggleAdsExport,
  type AdsChange,
  type AdsChangeStatus,
  type AdsSnapshot,
} from "@/lib/ads-desk-api";
import { downloadDeskFile } from "@/lib/seo-desk-api";
import { cn } from "@/lib/utils";

type Tx = (key: string, fallback: string, values?: Record<string, unknown>) => string;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const PLATFORM_LABELS: Record<string, string> = {
  google: "Google Ads",
  microsoft: "Microsoft Ads",
  meta: "Meta Ads",
  linkedin: "LinkedIn Ads",
  tiktok: "TikTok Ads",
  reddit: "Reddit Ads",
  unknown: "Unknown",
};

const STATUS_TONE: Record<AdsChangeStatus, string> = {
  proposed: "bg-[#2f7de1]/10 text-[#1d4ed8] dark:text-[#93c5fd]",
  approved: "bg-[#1f9d74]/12 text-[#047857] dark:text-[#6ee7b7]",
  rejected: "bg-foreground/[0.06] text-muted-foreground",
  applied: "bg-[#6d5bd0]/12 text-[#5b21b6] dark:text-[#c4b5fd]",
};

export function AdsDesk({ token }: { token: string }) {
  const { t, i18n } = useTranslation();
  const tx: Tx = useCallback(
    (key, fallback, values) => String(t(`adsDesk.${key}`, { defaultValue: fallback, ...(values ?? {}) })),
    [t],
  );
  const locale = i18n.language;
  const [snap, setSnap] = useState<AdsSnapshot | null>(null);
  const [changes, setChanges] = useState<AdsChange[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [currency, setCurrency] = useState("");
  const [budget, setBudget] = useState("");
  const [dragging, setDragging] = useState(false);
  const [statusFilter, setStatusFilter] = useState<AdsChangeStatus | "all">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [proofFor, setProofFor] = useState<string[] | null>(null);
  const [level, setLevel] = useState("campaign");
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [next, list] = await Promise.all([fetchAdsSnapshot(token), fetchAdsChanges(token)]);
      setSnap(next);
      setChanges(list.changes);
      setCurrency((value) => value || next.settings.currency || "");
      setBudget((value) => value || (next.settings.monthly_budget ? String(next.settings.monthly_budget) : ""));
      setError(null);
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
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const act = async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(null);
    }
  };

  const importFiles = (files: FileList | File[]) =>
    act("import", async () => {
      const list = Array.from(files);
      for (const file of list) {
        await ingestAdsExport(token, file.name, await fileToBase64(file));
      }
      setNotice(tx("imported", "{{count}} export(s) imported. Run the analysis.", { count: list.length }));
      await load();
    });

  const analyze = () =>
    act("analyze", async () => {
      await analyzeAds(token, {
        currency: currency.trim().toUpperCase(),
        monthly_budget: budget.trim() ? Number(budget) : null,
      });
      setNotice(tx("analyzed", "Analysis updated."));
      await load();
    });

  const changeStatus = (verb: "approve" | "reject" | "mark_applied" | "reset", ids: string[], extra: { proof?: string; note?: string } = {}) =>
    act(verb, async () => {
      const answer = await setAdsChangeStatus(token, verb, ids, extra);
      setChanges(answer.changes);
      setSelected(new Set());
      setSnap((prev) => (prev ? { ...prev, changes: { count: answer.count, by_status: answer.by_status } } : prev));
    });

  const download = (format: "csv" | "google_editor" | "microsoft_bulk") =>
    act(`export-${format}`, async () => {
      downloadDeskFile(await exportAdsChanges(token, format));
    });

  const analysis = snap?.analysis ?? null;
  const cur = analysis?.currency || currency || "";
  const visibleChanges = useMemo(
    () => changes.filter((item) => statusFilter === "all" || item.status === statusFilter),
    [changes, statusFilter],
  );
  const levels = analysis ? Object.keys(analysis.entities) : [];
  const rows = analysis?.entities[level]?.top_by_spend ?? [];
  const levelKey = levels.join(",");
  useEffect(() => {
    const available = levelKey ? levelKey.split(",") : [];
    if (available.length && !available.includes(level)) {
      setLevel(available.includes("campaign") ? "campaign" : available[0]);
    }
  }, [level, levelKey]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground" data-testid="ads-desk-loading">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="ads-desk">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-5 py-4">
        {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
        {notice ? <p role="status" className="text-[12.5px] text-[#1f9d74]">{notice}</p> : null}

        <DeskSection
          title={tx("exportsTitle", "Platform exports")}
          actions={
            <>
              <Input value={currency} onChange={(event) => setCurrency(event.target.value)} placeholder="EUR" maxLength={8} aria-label={tx("currency", "Currency")} className="h-8 w-20 text-[12.5px]" />
              <Input value={budget} onChange={(event) => setBudget(event.target.value.replace(/[^0-9.]/g, ""))} placeholder={tx("budget", "Monthly budget")} aria-label={tx("budget", "Monthly budget")} className="h-8 w-36 text-[12.5px]" />
              <Button size="sm" className="rounded-full" disabled={busy !== null || !snap?.exports.some((item) => item.active)} onClick={() => void analyze()}>
                {busy === "analyze" ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Play className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                {tx("analyze", "Analyze")}
              </Button>
            </>
          }
        >
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (event.dataTransfer.files.length) void importFiles(event.dataTransfer.files);
            }}
            className={cn(
              "flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-4 py-5 text-center transition-colors",
              dragging ? "border-[#2f7de1] bg-[#2f7de1]/[0.06]" : "border-border/70",
            )}
            data-testid="ads-dropzone"
          >
            <FileUp className="h-5 w-5 text-muted-foreground" aria-hidden />
            <p className="text-[12.5px] text-foreground">
              {tx("drop", "Drop CSV or XLSX exports from Google, Microsoft, Meta, LinkedIn, TikTok or Reddit Ads")}
            </p>
            <Button size="sm" variant="outline" className="rounded-full" disabled={busy === "import"} onClick={() => fileInput.current?.click()}>
              {busy === "import" ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
              {tx("choose", "Choose files")}
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.tsv,.txt,.xlsx"
              multiple
              hidden
              onChange={(event) => {
                if (event.target.files?.length) void importFiles(event.target.files);
                event.target.value = "";
              }}
            />
          </div>
          {snap?.exports.length ? (
            <ul className="mt-3 divide-y divide-border/50">
              {snap.exports.map((item) => (
                <li key={item.id} className="flex items-center gap-2 py-2 text-[12.5px]">
                  <input
                    type="checkbox"
                    checked={item.active}
                    aria-label={tx("include", "Include in the analysis")}
                    onChange={(event) => void act("toggle", async () => { await toggleAdsExport(token, item.id, event.target.checked); await load(); })}
                  />
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground">{item.filename}</span>
                  <span className="shrink-0 rounded-full bg-foreground/[0.06] px-2 py-0.5 text-[11px]">{PLATFORM_LABELS[item.platform] ?? item.platform}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">{tx("rows", "{{count}} rows", { count: item.rows })}</span>
                  {item.warnings.length ? <span className="shrink-0 text-[11px] text-[#a15c07]" title={item.warnings.join("\n")}>{tx("warnings", "{{count}} warnings", { count: item.warnings.length })}</span> : null}
                  <button
                    type="button"
                    className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted/60"
                    aria-label={tx("deleteExport", "Remove this export")}
                    onClick={() => void act("delete", async () => { await deleteAdsExport(token, item.id); await load(); })}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </DeskSection>

        {!analysis ? (
          <EmptyState
            icon={<BadgeDollarSign className="h-5 w-5" aria-hidden />}
            title={tx("emptyTitle", "No analysis yet")}
            body={tx("emptyBody", "Import your campaign exports, then Analyze: spend, CPA, ROAS, wasted spend, creative fatigue and pacing, with changes you approve before anything moves.")}
          />
        ) : (
          <>
            <DeskSection
              title={tx("overview", "Account overview")}
              actions={
                <>
                  <span className="text-[11.5px] text-muted-foreground">
                    {analysis.platforms.map((item) => PLATFORM_LABELS[item] ?? item).join(", ")}
                    {" | "}
                    {new Date(analysis.analyzed_at).toLocaleString(latnLocale(locale))}
                  </span>
                  {(["html", "md"] as const).map((format) => (
                    <Button key={format} size="sm" variant="ghost" className="rounded-full" disabled={busy !== null} onClick={() => void act("report", async () => downloadDeskFile(await exportAdsReport(token, format)))}>
                      <Download className="me-1 h-3.5 w-3.5" aria-hidden />
                      {format.toUpperCase()}
                    </Button>
                  ))}
                </>
              }
            >
              <div className="flex flex-wrap items-center gap-5">
                <ScoreRing score={analysis.health.score} grade={tx("grade", "Grade {{grade}}", { grade: analysis.health.grade })} label={tx("health", "Account health")} />
                <div className="grid min-w-0 flex-1 grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
                  <KpiTile label={tx("spend", "Spend")} value={formatMoney(analysis.totals.cost, locale, cur)} />
                  <KpiTile label={tx("conversions", "Conversions")} value={formatNumber(analysis.totals.conversions, locale, 1)} />
                  <KpiTile label={tx("cpa", "CPA")} value={formatMoney(analysis.totals.cpa, locale, cur)} />
                  <KpiTile label={tx("roas", "ROAS")} value={analysis.totals.roas == null ? "-" : `${formatNumber(analysis.totals.roas, locale, 2)}x`} />
                  <KpiTile label={tx("ctr", "CTR")} value={formatPercent(analysis.totals.ctr, locale)} />
                  <KpiTile
                    label={tx("wasted", "Wasted spend")}
                    value={formatMoney(analysis.health.wasted_cost, locale, cur)}
                    hint={tx("savings", "Savings if approved: {{value}}/month", { value: formatMoney(analysis.health.estimated_monthly_savings, locale, cur) })}
                    tone={analysis.health.wasted_cost > 0 ? "bad" : "good"}
                  />
                </div>
              </div>
              {analysis.data_gaps.length ? (
                <ul className="mt-3 space-y-1 text-[11.5px] text-muted-foreground">
                  {analysis.data_gaps.slice(0, 6).map((gap, index) => (
                    <li key={`${gap.service}-${index}`}>{gap.service}: {gap.reason}</li>
                  ))}
                </ul>
              ) : null}
            </DeskSection>

            <DeskSection
              title={tx("entities", "Performance by level")}
              actions={levels.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setLevel(item)}
                  aria-pressed={level === item}
                  className={cn("h-7 rounded-full px-2.5 text-[11.5px] font-medium", level === item ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted/60")}
                >
                  {tx(`level.${item}`, item.replace(/_/g, " "))} {analysis.entities[item].count}
                </button>
              ))}
            >
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-[12px]">
                  <thead className="sticky top-0 bg-card text-muted-foreground">
                    <tr>
                      <th className="py-1.5 text-start font-medium">{tx("colName", "Name")}</th>
                      <th className="py-1.5 text-end font-medium">{tx("spend", "Spend")}</th>
                      <th className="py-1.5 text-end font-medium">{tx("clicks", "Clicks")}</th>
                      <th className="py-1.5 text-end font-medium">{tx("ctr", "CTR")}</th>
                      <th className="py-1.5 text-end font-medium">{tx("conversions", "Conversions")}</th>
                      <th className="py-1.5 text-end font-medium">{tx("cpa", "CPA")}</th>
                      <th className="py-1.5 text-end font-medium">{tx("roas", "ROAS")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.key} className="border-t border-border/40">
                        <td className="max-w-[22rem] truncate py-1.5 pe-2" title={row.key}>
                          {row.search_term || row.keyword || row.ad || row.ad_group || row.campaign || row.key}
                          {row.campaign && (row.ad_group || row.keyword || row.ad) ? <span className="ms-1 text-[11px] text-muted-foreground">{row.campaign}</span> : null}
                        </td>
                        <td className="py-1.5 text-end tabular-nums">{formatMoney(row.metrics.cost, locale, cur)}</td>
                        <td className="py-1.5 text-end tabular-nums">{formatNumber(row.metrics.clicks, locale)}</td>
                        <td className="py-1.5 text-end tabular-nums">{formatPercent(row.metrics.ctr, locale)}</td>
                        <td className="py-1.5 text-end tabular-nums">{formatNumber(row.metrics.conversions, locale, 1)}</td>
                        <td className="py-1.5 text-end tabular-nums">{formatMoney(row.metrics.cpa, locale, cur)}</td>
                        <td className="py-1.5 text-end tabular-nums">{row.metrics.roas == null ? "-" : `${formatNumber(row.metrics.roas, locale, 2)}x`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </DeskSection>

            <DeskSection title={tx("findingsTitle", "Findings ({{count}})", { count: analysis.findings.length })}>
              {analysis.findings.length === 0 ? (
                <p className="py-4 text-center text-[12.5px] text-muted-foreground">{tx("noFindings", "No issue found in these exports.")}</p>
              ) : (
                <ul className="space-y-2">
                  {analysis.findings.slice(0, 60).map((item, index) => (
                    <motion.li
                      key={`${item.code}-${index}`}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: Math.min(index, 10) * 0.02 }}
                      className="rounded-xl bg-muted/30 p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <SeverityChip severity={item.severity} label={tx(`severity.${item.severity}`, item.severity)} />
                        <span className="text-[13px] font-medium text-foreground">{item.title}</span>
                        {item.impact.cost ? <span className="ms-auto text-[11.5px] tabular-nums text-muted-foreground">{formatMoney(item.impact.cost, locale, cur)}</span> : null}
                      </div>
                      <p className="mt-1 text-[12.5px] text-foreground/85">{item.message}</p>
                      <p className="mt-1 text-[12px] text-muted-foreground">{item.recommendation}</p>
                    </motion.li>
                  ))}
                </ul>
              )}
            </DeskSection>
          </>
        )}

        <DeskSection
          title={tx("changesTitle", "Change queue")}
          actions={
            <>
              {(["all", "proposed", "approved", "applied", "rejected"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setStatusFilter(value)}
                  aria-pressed={statusFilter === value}
                  className={cn("h-7 rounded-full px-2.5 text-[11.5px] font-medium", statusFilter === value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted/60")}
                >
                  {tx(`status.${value}`, value)} {value === "all" ? changes.length : snap?.changes.by_status[value] ?? 0}
                </button>
              ))}
              <Button size="sm" variant="outline" className="rounded-full" disabled={busy !== null} onClick={() => void download("google_editor")}>
                <Download className="me-1 h-3.5 w-3.5" aria-hidden />
                Google Ads Editor
              </Button>
              <Button size="sm" variant="outline" className="rounded-full" disabled={busy !== null} onClick={() => void download("microsoft_bulk")}>
                <Download className="me-1 h-3.5 w-3.5" aria-hidden />
                Microsoft Bulk
              </Button>
              <Button size="sm" variant="ghost" className="rounded-full" disabled={busy !== null} onClick={() => void download("csv")}>
                CSV
              </Button>
            </>
          }
        >
          {selected.size ? (
            <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded-xl bg-muted/40 px-3 py-2 text-[12px]">
              <span className="me-1">{tx("selected", "{{count}} selected", { count: selected.size })}</span>
              <Button size="sm" className="h-7 rounded-full" onClick={() => void changeStatus("approve", [...selected])}><Check className="me-1 h-3.5 w-3.5" aria-hidden />{tx("approve", "Approve")}</Button>
              <Button size="sm" variant="outline" className="h-7 rounded-full" onClick={() => void changeStatus("reject", [...selected])}><X className="me-1 h-3.5 w-3.5" aria-hidden />{tx("reject", "Reject")}</Button>
              <Button size="sm" variant="outline" className="h-7 rounded-full" onClick={() => setProofFor([...selected])}>{tx("markApplied", "Mark applied")}</Button>
            </div>
          ) : null}
          {visibleChanges.length === 0 ? (
            <p className="py-4 text-center text-[12.5px] text-muted-foreground">{tx("noChanges", "No change in this view. Proposals appear after an analysis.")}</p>
          ) : (
            <ul className="divide-y divide-border/50">
              {visibleChanges.map((item) => (
                <li key={item.id} className="flex items-start gap-2 py-2.5" data-testid="ads-change">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={selected.has(item.id)}
                    aria-label={item.id}
                    onChange={(event) =>
                      setSelected((prev) => {
                        const next = new Set(prev);
                        if (event.target.checked) next.add(item.id);
                        else next.delete(item.id);
                        return next;
                      })
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", STATUS_TONE[item.status])}>{tx(`status.${item.status}`, item.status)}</span>
                      <span className="text-[13px] font-medium text-foreground">{tx(`action.${item.action}`, item.action.replace(/_/g, " "))}</span>
                      <span className="truncate text-[12px] text-muted-foreground">
                        {Object.values(item.target).filter(Boolean).join(" > ")}
                        {typeof item.params.keyword === "string" ? ` | ${item.params.keyword}` : ""}
                      </span>
                      {item.estimated_monthly_savings ? (
                        <span className="ms-auto text-[11.5px] tabular-nums text-[#1f9d74]">
                          {tx("perMonth", "{{value}}/month", { value: formatMoney(item.estimated_monthly_savings, locale, cur) })}
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-0.5 text-[12px] text-foreground/80">{item.rationale}</p>
                    {item.applied_proof ? <p className="mt-0.5 text-[11.5px] text-[#5b21b6] dark:text-[#c4b5fd]">{tx("proof", "Read-back")}: {item.applied_proof}</p> : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {item.status === "proposed" ? (
                      <>
                        <Button size="sm" className="h-7 rounded-full" disabled={busy !== null} onClick={() => void changeStatus("approve", [item.id])}>{tx("approve", "Approve")}</Button>
                        <Button size="sm" variant="ghost" className="h-7 rounded-full" disabled={busy !== null} onClick={() => void changeStatus("reject", [item.id])}>{tx("reject", "Reject")}</Button>
                      </>
                    ) : null}
                    {item.status === "approved" ? (
                      <Button size="sm" variant="outline" className="h-7 rounded-full" disabled={busy !== null} onClick={() => setProofFor([item.id])}>{tx("markApplied", "Mark applied")}</Button>
                    ) : null}
                    {item.status === "rejected" ? (
                      <Button size="sm" variant="ghost" className="h-7 rounded-full" disabled={busy !== null} onClick={() => void changeStatus("reset", [item.id])} aria-label={tx("reopen", "Reopen")}>
                        <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </DeskSection>
      </div>
      <ProofDialog
        tx={tx}
        ids={proofFor}
        onClose={() => setProofFor(null)}
        onConfirm={(proof) => {
          const ids = proofFor ?? [];
          setProofFor(null);
          void changeStatus("mark_applied", ids, { proof });
        }}
      />
    </div>
  );
}

function ProofDialog({ tx, ids, onClose, onConfirm }: { tx: Tx; ids: string[] | null; onClose: () => void; onConfirm: (proof: string) => void }) {
  const [proof, setProof] = useState("");
  useEffect(() => {
    if (ids) setProof("");
  }, [ids]);
  return (
    <Dialog open={Boolean(ids)} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{tx("proofTitle", "Confirm the change is live")}</DialogTitle>
          <DialogDescription>
            {tx("proofHelp", "Paste what the platform shows after the change (status, budget, negative keyword list). A change is marked applied only with this read-back.")}
          </DialogDescription>
        </DialogHeader>
        <Textarea value={proof} onChange={(event) => setProof(event.target.value)} rows={4} maxLength={4000} placeholder={tx("proofPlaceholder", "e.g. Campaign 'Generic' status: Paused (seen at 10:42)")} />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>{tx("cancel", "Cancel")}</Button>
          <Button disabled={!proof.trim()} onClick={() => onConfirm(proof.trim())}>{tx("markApplied", "Mark applied")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
