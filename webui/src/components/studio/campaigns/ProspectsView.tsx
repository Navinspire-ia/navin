// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { Building2, CheckCircle2, ExternalLink, KeyRound, Loader2, Radar, Sparkles, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { enrichContacts, fetchProspectProviders, importProspects, searchProspects } from "@/lib/campaigns-api";
import type {
  ContactList,
  EnrichResult,
  ImportResult,
  ProspectCriteria,
  ProspectProviders,
  ProspectSearch,
} from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { Card, Chip, EmptyState, ErrorBanner, Field, SELECT_CLASS, Switch, errorText, formatNumber, type Tx } from "./campaigns-ui";

const COUNTRIES = ["FR", "MA", "BE", "CH", "CA", "GB", "US", "DE", "ES", "AE", "SA", "TN", "DZ"];
const FREE_SOURCES = ["sirene", "web", "hiring", "osm"] as const;
const LIMITS = [10, 25, 50, 100];
const KEY_ORDER = ["apollo", "hunter", "pdl", "places", "crunchbase", "opencorporates", "companies_house", "pappers"] as const;
const KEY_LABELS: Record<(typeof KEY_ORDER)[number], string> = {
  apollo: "Apollo",
  hunter: "Hunter",
  pdl: "People Data Labs",
  places: "Google Places",
  crunchbase: "Crunchbase",
  opencorporates: "OpenCorporates",
  companies_house: "Companies House",
  pappers: "Pappers",
};

function splitList(text: string): string[] {
  return text.split(/[\n,;]+/).map((part) => part.trim()).filter(Boolean);
}

/** verified / risky / invalid badge for a contact's email (attrs.email_status). */
export function EmailStatusBadge({ status, tx }: { status?: string; tx: Tx }) {
  if (status === "verified") return <Chip tone="green">{tx("enrich.status.verified", "verified")}</Chip>;
  if (status === "invalid") {
    return (
      <span title={tx("enrich.status.invalidHint", "Never sent to")}>
        <Chip tone="red">{tx("enrich.status.invalid", "invalid")}</Chip>
      </span>
    );
  }
  if (status === "risky" || status === "unverified") return <Chip tone="amber">{tx("enrich.status.risky", "risky")}</Chip>;
  return null;
}

function ProvidersStrip({ providers, tx }: { providers: ProspectProviders | null; tx: Tx }) {
  if (!providers) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="prospect-providers">
      <span className="text-[11.5px] text-muted-foreground">{tx("prospects.sources", "Sources:")}</span>
      <Chip tone="green">{tx("prospects.freeSources", "Free sources")}</Chip>
      {KEY_ORDER.map((key) => (
        <Chip key={key} tone={providers.keys[key] ? "green" : "neutral"} className={providers.keys[key] ? "" : "opacity-60"}>
          {KEY_LABELS[key]}
        </Chip>
      ))}
      <Button
        size="sm"
        variant="ghost"
        className="h-7 rounded-full text-[12px]"
        onClick={() => { window.location.hash = "#/leads"; }}
      >
        <KeyRound className="me-1 h-3.5 w-3.5" aria-hidden />
        {tx("prospects.addKeys", "Add keys in Leads")}
      </Button>
    </div>
  );
}

export function ProspectsView({
  token,
  tx,
  lists,
  onImported,
  onOpenList,
  onCreateCampaign,
}: {
  token: string;
  tx: Tx;
  lists: ContactList[];
  onImported: () => Promise<void> | void;
  onOpenList: (listId: string) => void;
  onCreateCampaign: (listId: string) => void;
}) {
  const [providers, setProviders] = useState<ProspectProviders | null>(null);
  const [mode, setMode] = useState<"market" | "companies">("market");
  const [sector, setSector] = useState("");
  const [countries, setCountries] = useState<string[]>(["FR"]);
  const [sizeMin, setSizeMin] = useState(10);
  const [sizeMax, setSizeMax] = useState(500);
  const [cities, setCities] = useState("");
  const [sources, setSources] = useState<string[]>(["web", "hiring", "osm", "sirene"]);
  const [domains, setDomains] = useState("");
  const [titles, setTitles] = useState("CEO, Founder, Head of Sales");
  const [limit, setLimit] = useState(25);
  const [findPeople, setFindPeople] = useState(true);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ProspectSearch | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [listTarget, setListTarget] = useState("new");
  const [newListName, setNewListName] = useState("");
  const [tags, setTags] = useState("prospects");
  const [onlyEmail, setOnlyEmail] = useState(true);
  const [imported, setImported] = useState<ImportResult | null>(null);

  useEffect(() => {
    let live = true;
    fetchProspectProviders(token).then((value) => { if (live) setProviders(value); }).catch(() => undefined);
    return () => { live = false; };
  }, [token]);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, [busy]);

  const canFindPeople = Boolean(providers?.can_find_people);
  const criteria: ProspectCriteria = useMemo(() => {
    const base: ProspectCriteria = { titles: splitList(titles), limit, find_people: findPeople && canFindPeople };
    if (mode === "companies") return { ...base, domains: splitList(domains) };
    return { ...base, sector: sector.trim(), countries, size_min: sizeMin, size_max: sizeMax, cities: splitList(cities), sources };
  }, [canFindPeople, cities, countries, domains, findPeople, limit, mode, sector, sizeMax, sizeMin, sources, titles]);

  const ready = mode === "market" ? sector.trim().length > 1 : splitList(domains).length > 0;

  const run = async () => {
    setBusy(true);
    setElapsed(0);
    setError("");
    setImported(null);
    try {
      const found = await searchProspects(token, criteria);
      setResult(found);
      setPicked(new Set(found.rows.map((_, index) => index)));
      setNewListName(mode === "market" ? `${sector.trim()} ${countries.join(" ")}`.trim() : tx("prospects.defaultList", "Target companies"));
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  };

  const doImport = async () => {
    if (!result) return;
    setBusy(true);
    setError("");
    try {
      const done = await importProspects(token, {
        search_id: result.search_id,
        indexes: [...picked].sort((a, b) => a - b),
        only_with_email: onlyEmail,
        list_id: listTarget === "new" ? undefined : listTarget,
        new_list_name: listTarget === "new" ? newListName.trim() || tx("prospects.defaultList", "Target companies") : undefined,
        tags: splitList(tags),
      });
      setImported(done);
      await onImported();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  };

  const rows = result?.rows ?? [];
  const allPicked = rows.length > 0 && rows.every((_, index) => picked.has(index));

  return (
    <div className="space-y-4" data-testid="prospects-view">
      <Card className="space-y-4 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
              <Radar className="h-4 w-4" aria-hidden />
              {tx("prospects.title", "Find prospects")}
            </h2>
            <p className="mt-0.5 text-[12.5px] text-muted-foreground">
              {tx("prospects.subtitle", "Companies and decision makers from public registries, the web and your own provider keys. Preview first, then import the ones you want.")}
            </p>
          </div>
          <div className="flex rounded-xl border border-border/60 p-0.5" role="tablist">
            {(["market", "companies"] as const).map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={mode === key}
                onClick={() => setMode(key)}
                className={cn("h-8 rounded-lg px-3 text-[12.5px]", mode === key ? "bg-muted font-medium" : "text-muted-foreground")}
              >
                {key === "market" ? tx("prospects.byMarket", "By market") : tx("prospects.byCompanies", "By companies")}
              </button>
            ))}
          </div>
        </div>
        <ProvidersStrip providers={providers} tx={tx} />

        {mode === "market" ? (
          <div className="grid gap-3 md:grid-cols-2">
            <Field label={tx("prospects.sector", "Sector or keywords")}>
              <Input value={sector} onChange={(e) => setSector(e.target.value)} placeholder={tx("prospects.sectorPlaceholder", "e.g. accounting firms, SaaS, logistics")} />
            </Field>
            <Field label={tx("prospects.cities", "Cities (optional)")}>
              <Input value={cities} onChange={(e) => setCities(e.target.value)} placeholder="Paris, Lyon" />
            </Field>
            <Field label={tx("prospects.countries", "Countries")} className="md:col-span-2">
              <div className="flex flex-wrap gap-1.5">
                {COUNTRIES.map((code) => (
                  <button
                    key={code}
                    type="button"
                    aria-pressed={countries.includes(code)}
                    onClick={() => setCountries((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]))}
                    className={cn(
                      "h-7 rounded-full border px-2.5 text-[12px] tabular-nums",
                      countries.includes(code) ? "border-[#2f7de1]/50 bg-[#2f7de1]/10 text-[#2f7de1]" : "border-border/60 text-muted-foreground",
                    )}
                  >
                    {code}
                  </button>
                ))}
              </div>
            </Field>
            <Field label={tx("prospects.size", "Company size (employees)")}>
              <div className="flex items-center gap-2">
                <Input type="number" min={1} value={sizeMin} onChange={(e) => setSizeMin(Math.max(1, Number(e.target.value) || 1))} className="w-24" aria-label={tx("prospects.sizeMin", "Minimum")} />
                <span className="text-muted-foreground">-</span>
                <Input type="number" min={1} value={sizeMax} onChange={(e) => setSizeMax(Math.max(1, Number(e.target.value) || 1))} className="w-28" aria-label={tx("prospects.sizeMax", "Maximum")} />
              </div>
            </Field>
            <Field label={tx("prospects.freeSourcesLabel", "Free sources")}>
              <div className="flex flex-wrap gap-3 text-[12.5px]">
                {FREE_SOURCES.map((source) => (
                  <label key={source} className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      checked={sources.includes(source)}
                      onChange={() => setSources((prev) => (prev.includes(source) ? prev.filter((s) => s !== source) : [...prev, source]))}
                    />
                    {tx(`prospects.source.${source}`, { sirene: "French registry", web: "Open web", hiring: "Hiring signals", osm: "Maps (OSM)" }[source])}
                  </label>
                ))}
              </div>
            </Field>
          </div>
        ) : (
          <Field label={tx("prospects.domains", "Company domains, one per line")} hint={tx("prospects.domainsHint", "Find the decision makers at these exact companies.")}>
            <Textarea value={domains} onChange={(e) => setDomains(e.target.value)} rows={4} placeholder={"acme.com\nglobex.io"} />
          </Field>
        )}

        <div className="grid gap-3 md:grid-cols-[1fr_auto]">
          <Field label={tx("prospects.titles", "Decision makers to find")} hint={tx("prospects.titlesHint", "Comma-separated job titles.")}>
            <Input value={titles} onChange={(e) => setTitles(e.target.value)} />
          </Field>
          <Field label={tx("prospects.limit", "Results")}>
            <select className={cn(SELECT_CLASS, "w-28")} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {LIMITS.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </Field>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex max-w-xl items-start gap-2 text-[12.5px]">
            <Switch checked={findPeople && canFindPeople} onChange={setFindPeople} label={tx("prospects.findPeople", "Find a decision maker per company")} disabled={!canFindPeople} />
            <span>
              <span className="font-medium text-foreground">{tx("prospects.findPeople", "Find a decision maker per company")}</span>
              <span className="block text-muted-foreground">
                {canFindPeople
                  ? tx("prospects.findPeopleCredits", "Uses your Apollo / Hunter credits, one lookup per company. Without a direct email, the company's general contact is used.")
                  : tx("prospects.findPeopleNoKey", "Without a key you get each company's general contact (contact@, switchboard) from its public site, not a named decision maker. Add an Apollo or Hunter key in Leads for names and direct emails.")}
              </span>
            </span>
          </label>
          <Button onClick={() => void run()} disabled={!ready || busy} className="rounded-full" data-testid="prospects-run">
            {busy && !result ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : <Radar className="me-1.5 h-4 w-4" aria-hidden />}
            {busy && !imported ? tx("prospects.searching", "Searching... {{seconds}} s", { seconds: elapsed }) : tx("prospects.run", "Search")}
          </Button>
        </div>
      </Card>

      <ErrorBanner message={error} onDismiss={() => setError("")} />

      {result ? (
        <Card className="p-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-border/50 px-4 py-3 text-[12.5px]">
            <span className="flex items-center gap-1"><Building2 className="h-3.5 w-3.5" aria-hidden />{tx("prospects.countCompanies", "{{count}} companies", { count: result.count })}</span>
            <span className="flex items-center gap-1"><UserRound className="h-3.5 w-3.5" aria-hidden />{tx("prospects.countPeople", "{{count}} people", { count: result.with_person })}</span>
            <span className="flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden />{tx("prospects.countEmails", "{{count}} emails", { count: result.with_email })}</span>
            {result.with_general ? (
              <span className="flex items-center gap-1 text-muted-foreground" data-testid="prospects-general-count">
                <Building2 className="h-3.5 w-3.5" aria-hidden />
                {tx("prospects.countGeneral", "{{count}} general contacts", { count: result.with_general })}
              </span>
            ) : null}
            <span className="ms-auto flex gap-2">
              <Button size="sm" variant="ghost" className="h-7" onClick={() => setPicked(allPicked ? new Set() : new Set(rows.map((_, i) => i)))}>
                {allPicked ? tx("prospects.selectNone", "Select none") : tx("prospects.selectAll", "Select all")}
              </Button>
              <Button size="sm" variant="ghost" className="h-7" onClick={() => setPicked(new Set(rows.flatMap((row, i) => (row.email ? [i] : []))))}>
                {tx("prospects.selectWithEmail", "Only with email")}
              </Button>
            </span>
          </div>
          {rows.length === 0 ? (
            <EmptyState icon={<Radar className="h-5 w-5" />} title={tx("prospects.none", "No company found")} body={tx("prospects.noneHint", "Try broader keywords, more countries or more free sources.")} />
          ) : (
            <div className="max-h-[28rem] overflow-auto">
              <table className="w-full text-[12.5px]" data-testid="prospects-table">
                <thead className="sticky top-0 bg-card text-start text-[11px] uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="w-8 px-3 py-2" />
                    <th className="px-2 py-2 text-start">{tx("prospects.col.company", "Company")}</th>
                    <th className="px-2 py-2 text-start">{tx("prospects.col.person", "Person")}</th>
                    <th className="px-2 py-2 text-start">{tx("prospects.col.email", "Email")}</th>
                    <th className="hidden px-2 py-2 text-start lg:table-cell">{tx("prospects.col.phone", "Phone")}</th>
                    <th className="hidden px-2 py-2 text-start md:table-cell">{tx("prospects.col.details", "Details")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={`${row.domain}-${index}`} className="border-t border-border/40 align-top">
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          aria-label={row.company}
                          checked={picked.has(index)}
                          onChange={() => setPicked((prev) => {
                            const next = new Set(prev);
                            if (next.has(index)) next.delete(index);
                            else next.add(index);
                            return next;
                          })}
                        />
                      </td>
                      <td className="max-w-[14rem] px-2 py-2">
                        <span className="block truncate font-medium text-foreground">{row.company || "-"}</span>
                        {row.domain ? (
                          <a href={row.website || `https://${row.domain}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[11.5px] text-[#2f7de1] hover:underline">
                            {row.domain}<ExternalLink className="h-3 w-3" aria-hidden />
                          </a>
                        ) : null}
                      </td>
                      <td className="max-w-[12rem] px-2 py-2">
                        {row.first_name || row.last_name ? (
                          <span className="block truncate">{[row.first_name, row.last_name].filter(Boolean).join(" ")}</span>
                        ) : (
                          <span className="block truncate text-muted-foreground">
                            {row.contact_kind === "general" ? tx("prospects.generalContact", "General contact") : "-"}
                          </span>
                        )}
                        {row.role ? <span className="block truncate text-[11.5px] text-muted-foreground">{row.role}</span> : null}
                      </td>
                      <td className="max-w-[14rem] px-2 py-2">
                        <span className="block truncate">{row.email || "-"}</span>
                        <span className="flex flex-wrap gap-1">
                          {row.email ? <EmailStatusBadge status={row.email_status} tx={tx} /> : null}
                          {row.contact_kind === "general" ? (
                            <span title={tx("prospects.generalContactHint", "Company address or switchboard from its public site, not a named decision maker.")}>
                              <Chip tone="neutral">{tx("prospects.generalBadge", "company")}</Chip>
                            </span>
                          ) : null}
                        </span>
                      </td>
                      <td className="hidden px-2 py-2 lg:table-cell">{row.phone || "-"}</td>
                      <td className="hidden max-w-[16rem] px-2 py-2 text-[11.5px] text-muted-foreground md:table-cell">
                        {[row.country, row.sector, row.size ? `${row.size} emp.` : "", row.source].filter(Boolean).join(" · ")}
                        {row.signal ? <span className="block truncate">{row.signal}</span> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {rows.length ? (
            <div className="flex flex-wrap items-end gap-3 border-t border-border/50 px-4 py-3">
              <Field label={tx("prospects.importTo", "Import into")}>
                <select className={cn(SELECT_CLASS, "w-52")} value={listTarget} onChange={(e) => setListTarget(e.target.value)}>
                  <option value="new">{tx("prospects.newList", "New list")}</option>
                  {lists.map((list) => <option key={list.id} value={list.id}>{list.name}</option>)}
                </select>
              </Field>
              {listTarget === "new" ? (
                <Field label={tx("prospects.listName", "List name")}>
                  <Input value={newListName} onChange={(e) => setNewListName(e.target.value)} className="w-56" />
                </Field>
              ) : null}
              <Field label={tx("prospects.tags", "Tags")}>
                <Input value={tags} onChange={(e) => setTags(e.target.value)} className="w-40" />
              </Field>
              <label className="flex items-center gap-1.5 pb-2 text-[12.5px]">
                <input type="checkbox" checked={onlyEmail} onChange={(e) => setOnlyEmail(e.target.checked)} />
                {tx("prospects.onlyEmail", "Only rows with an email")}
              </label>
              <Button className="ms-auto rounded-full" disabled={busy || picked.size === 0} onClick={() => void doImport()} data-testid="prospects-import">
                {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
                {tx("prospects.import", "Import {{count}}", { count: picked.size })}
              </Button>
            </div>
          ) : null}
          {imported ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-border/50 bg-[#1f9d74]/[0.06] px-4 py-3 text-[12.5px]" role="status">
              <CheckCircle2 className="h-4 w-4 text-[#1f9d74]" aria-hidden />
              {tx("prospects.imported", "{{created}} added, {{updated}} updated, {{skipped}} skipped.", {
                created: formatNumber(imported.created), updated: formatNumber(imported.updated), skipped: formatNumber(imported.skipped),
              })}
              {imported.list_id ? (
                <span className="ms-auto flex gap-2">
                  <Button size="sm" variant="outline" className="h-8 rounded-full" onClick={() => onOpenList(imported.list_id)}>
                    {tx("prospects.openList", "Open list")}
                  </Button>
                  <Button size="sm" className="h-8 rounded-full" onClick={() => onCreateCampaign(imported.list_id)}>
                    {tx("prospects.createCampaign", "Create a campaign")}
                  </Button>
                </span>
              ) : null}
            </div>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

/** Enrich selected contacts: find missing emails, verify, add company facts. */
export function EnrichDialog({
  token,
  tx,
  ids,
  open,
  onClose,
  onDone,
}: {
  token: string;
  tx: Tx;
  ids: string[];
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [providers, setProviders] = useState<ProspectProviders | null>(null);
  const [findEmail, setFindEmail] = useState(true);
  const [verify, setVerify] = useState(true);
  const [company, setCompany] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<EnrichResult | null>(null);

  useEffect(() => {
    if (!open) return;
    setResult(null);
    setError("");
    let live = true;
    fetchProspectProviders(token).then((value) => { if (live) setProviders(value); }).catch(() => undefined);
    return () => { live = false; };
  }, [open, token]);

  const options = [
    { key: "find", label: tx("enrich.findEmail", "Find missing emails (Hunter)"), on: findEmail, set: setFindEmail, available: Boolean(providers?.can_find_email) },
    { key: "verify", label: tx("enrich.verify", "Verify emails (Hunter)"), on: verify, set: setVerify, available: Boolean(providers?.can_verify) },
    { key: "company", label: tx("enrich.company", "Company facts: sector, size (People Data Labs)"), on: company, set: setCompany, available: Boolean(providers?.can_enrich_company) },
  ];
  const anyAvailable = options.some((option) => option.available && option.on);

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const done = await enrichContacts(token, ids, {
        find_email: findEmail && Boolean(providers?.can_find_email),
        verify: verify && Boolean(providers?.can_verify),
        company: company && Boolean(providers?.can_enrich_company),
      });
      setResult(done);
      onDone();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-w-lg" data-testid="enrich-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" aria-hidden />{tx("enrich.title", "Enrich {{count}} contacts", { count: ids.length })}</DialogTitle>
          <DialogDescription>{tx("enrich.subtitle", "Uses your provider keys from the Leads desk. Each lookup spends one credit at the provider.")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          {options.map((option) => (
            <label key={option.key} className={cn("flex items-center gap-2 text-[13px]", !option.available && "opacity-50")}>
              <input type="checkbox" checked={option.on && option.available} disabled={!option.available} onChange={(e) => option.set(e.target.checked)} />
              {option.label}
              {!option.available ? <span className="text-[11.5px] text-muted-foreground">{tx("enrich.noKey", "(no key)")}</span> : null}
            </label>
          ))}
          {providers && !options.some((option) => option.available) ? (
            <p className="text-[12.5px] text-muted-foreground">{tx("enrich.needKeys", "Add a Hunter or People Data Labs key in the Leads desk (Keys) to enrich contacts.")}</p>
          ) : null}
          <p className="text-[11.5px] text-muted-foreground">{tx("enrich.invalidRule", "Emails verified as invalid are never sent to.")}</p>
        </div>
        <ErrorBanner message={error} onDismiss={() => setError("")} />
        {result ? (
          <div className="grid grid-cols-3 gap-2 rounded-xl bg-muted/40 p-3 text-center text-[12px]" role="status" data-testid="enrich-result">
            {([
              ["emails_found", tx("enrich.r.found", "emails found")],
              ["verified", tx("enrich.r.verified", "verified")],
              ["invalid", tx("enrich.r.invalid", "invalid")],
              ["companies", tx("enrich.r.companies", "companies")],
              ["processed", tx("enrich.r.processed", "processed")],
              ["skipped", tx("enrich.r.skipped", "skipped")],
            ] as const).map(([key, label]) => (
              <div key={key}>
                <div className="text-[18px] font-semibold tabular-nums text-foreground">{formatNumber(result[key])}</div>
                {label}
              </div>
            ))}
            {result.errors.length ? <p className="col-span-3 text-start text-[#c2410c]">{result.errors.slice(0, 3).join(" · ")}</p> : null}
          </div>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>{result ? tx("enrich.close", "Close") : tx("enrich.cancel", "Cancel")}</Button>
          {!result ? (
            <Button onClick={() => void run()} disabled={busy || !anyAvailable || ids.length === 0} data-testid="enrich-run">
              {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="me-1.5 h-4 w-4" aria-hidden />}
              {tx("enrich.run", "Enrich")}
            </Button>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
