// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { Briefcase, Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { importCrmAudience, previewCrmAudience } from "@/lib/campaigns-api";
import type { ContactList, CrmFilters, CrmKind, CrmPreview, ImportResult } from "@/lib/campaigns-types";
import { cn } from "@/lib/utils";
import { EmptyState, ErrorBanner, errorText, Field, formatNumber, SELECT_CLASS, type Tx } from "./campaigns-ui";

const KINDS: CrmKind[] = ["contacts", "leads", "opportunities", "companies"];
type FacetKey = keyof CrmPreview["facets"];
const FACET_BY_KIND: Record<CrmKind, FacetKey[]> = {
  contacts: ["tags", "owner", "source", "country"],
  leads: ["status", "owner", "source", "country", "tags"],
  opportunities: ["stage", "owner", "tags"],
  companies: ["industry", "country", "tags"],
};

/** A list name that says what the filters picked, e.g. "CRM leads: qualified, France". */
export function crmListName(kinds: CrmKind[], filters: CrmFilters, label: (kind: CrmKind) => string): string {
  const parts = [
    ...(filters.status ?? []),
    ...(filters.stage ?? []),
    ...(filters.industry ?? []),
    ...(filters.country ?? []),
    ...(filters.tags ?? []),
    ...(filters.owner ?? []),
    ...(filters.source ?? []),
  ];
  if (filters.q) parts.unshift(`"${filters.q}"`);
  if (filters.score_min) parts.push(`score >= ${filters.score_min}`);
  if (filters.amount_min) parts.push(`>= ${filters.amount_min}`);
  const head = `CRM ${kinds.map(label).join(" + ")}`;
  return (parts.length ? `${head}: ${parts.join(", ")}` : head).slice(0, 80);
}

export function CrmAudiencePicker({
  token,
  sessionKey,
  lists,
  tx,
  onImported,
  submitLabel,
}: {
  token: string;
  sessionKey: string | null;
  lists: ContactList[];
  tx: Tx;
  onImported: (result: ImportResult) => void;
  submitLabel?: string;
}) {
  const [kinds, setKinds] = useState<CrmKind[]>(["contacts"]);
  const [filters, setFilters] = useState<CrmFilters>({});
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<CrmPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [target, setTarget] = useState("__new");
  const [newName, setNewName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [tags, setTags] = useState("");

  const kindLabel = (kind: CrmKind) =>
    tx(`crm.kind.${kind}`, { contacts: "contacts", leads: "leads", opportunities: "opportunities", companies: "companies" }[kind]);

  const effective = useMemo<CrmFilters>(() => ({ ...filters, q: query.trim() || undefined }), [filters, query]);
  const suggested = useMemo(() => crmListName(kinds, effective, kindLabel), [kinds, effective]); // eslint-disable-line react-hooks/exhaustive-deps
  const listName = nameTouched ? newName : suggested;

  useEffect(() => {
    if (!sessionKey || !kinds.length) {
      setPreview(null);
      return;
    }
    let live = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      previewCrmAudience(token, sessionKey, kinds, effective)
        .then((next) => {
          if (!live) return;
          setPreview(next);
          setError("");
        })
        .catch((err) => { if (live) setError(errorText(err)); })
        .finally(() => { if (live) setLoading(false); });
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [effective, kinds, sessionKey, token]);

  if (!sessionKey) {
    return (
      <EmptyState
        icon={<Briefcase className="h-5 w-5" aria-hidden />}
        title={tx("crm.noSession", "Open a project chat to use its CRM")}
        body={tx("crm.noSessionBody", "The CRM belongs to a project. Open or select a chat in that project, then come back here.")}
      />
    );
  }

  const facetKeys = Array.from(new Set(kinds.flatMap((kind) => FACET_BY_KIND[kind])));
  const toggleValue = (key: FacetKey, value: string) => {
    setFilters((prev) => {
      const current = (prev[key] as string[] | undefined) ?? [];
      const next = current.includes(value) ? current.filter((item) => item !== value) : [...current, value];
      return { ...prev, [key]: next.length ? next : undefined };
    });
  };
  const facetLabel: Record<FacetKey, string> = {
    status: tx("crm.facet.status", "Status"),
    stage: tx("crm.facet.stage", "Stage"),
    owner: tx("crm.facet.owner", "Owner"),
    tags: tx("crm.facet.tags", "Tags"),
    source: tx("crm.facet.source", "Source"),
    country: tx("crm.facet.country", "Country"),
    industry: tx("crm.facet.industry", "Industry"),
  };

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await importCrmAudience(token, sessionKey, {
        kinds,
        filters: effective,
        ...(target === "__new" ? { new_list_name: listName.trim() || suggested } : { list_id: target }),
        tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      });
      onImported(result);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4" data-testid="crm-audience-picker">
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <div>
        <p className="mb-1.5 text-[12px] font-medium text-foreground/85">{tx("crm.from", "Take people from")}</p>
        <div className="flex flex-wrap gap-2">
          {KINDS.map((kind) => {
            const on = kinds.includes(kind);
            return (
              <label key={kind} className={cn("inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 text-[12.5px]", on ? "border-[#1d6af0]/50 bg-[#1d6af0]/[0.07]" : "border-border/70")}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => setKinds((prev) => (on ? prev.filter((k) => k !== kind) : [...prev, kind]))}
                  className="accent-[#1d6af0]"
                />
                {kindLabel(kind)}
              </label>
            );
          })}
        </div>
        <p className="mt-1 text-[11.5px] text-muted-foreground">
          {tx("crm.kindsHelp", "Opportunities and companies bring their linked contacts.")}
        </p>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute start-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tx("crm.search", "Search name, company, email...")} className="ps-8" />
      </div>

      {facetKeys.map((key) => {
        const values = preview?.facets[key] ?? [];
        if (!values.length) return null;
        const selected = (filters[key] as string[] | undefined) ?? [];
        return (
          <div key={key}>
            <p className="mb-1 text-[11.5px] font-medium text-muted-foreground">{facetLabel[key]}</p>
            <div className="flex flex-wrap gap-1.5">
              {values.slice(0, 30).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={selected.includes(value)}
                  onClick={() => toggleValue(key, value)}
                  className={cn(
                    "rounded-full border px-2.5 py-0.5 text-[12px] transition-colors",
                    selected.includes(value) ? "border-[#1d6af0] bg-[#1d6af0] text-white" : "border-border/70 hover:bg-muted/60",
                  )}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>
        );
      })}

      <div className="grid gap-3 sm:grid-cols-2">
        {kinds.includes("leads") ? (
          <Field label={tx("crm.scoreMin", "Minimum lead score")}>
            <Input type="number" min={0} max={100} value={filters.score_min ?? ""} onChange={(event) => setFilters((prev) => ({ ...prev, score_min: event.target.value ? Number(event.target.value) : undefined }))} />
          </Field>
        ) : null}
        {kinds.includes("opportunities") ? (
          <Field label={tx("crm.amountMin", "Minimum opportunity amount")}>
            <Input type="number" min={0} value={filters.amount_min ?? ""} onChange={(event) => setFilters((prev) => ({ ...prev, amount_min: event.target.value ? Number(event.target.value) : undefined }))} />
          </Field>
        ) : null}
      </div>

      <div className="rounded-xl border border-border/60 bg-muted/20 p-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px]">
          {loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden /> : null}
          <span className="font-semibold text-foreground" data-testid="crm-count">
            {tx("crm.count", "{{count}} people", { count: formatNumber(preview?.count ?? 0) })}
          </span>
          <span className="text-muted-foreground">{tx("crm.withEmail", "{{count}} with email", { count: formatNumber(preview?.with_email ?? 0) })}</span>
          <span className="text-muted-foreground">{tx("crm.withPhone", "{{count}} with phone", { count: formatNumber(preview?.with_phone ?? 0) })}</span>
          {preview?.project ? <span className="text-muted-foreground">{tx("crm.project", "Project: {{name}}", { name: preview.project })}</span> : null}
        </div>
        {preview?.sample.length ? (
          <ul className="mt-2 divide-y divide-border/50 text-[12px]">
            {preview.sample.slice(0, 6).map((row, index) => (
              <li key={`${row.email}-${index}`} className="flex gap-2 py-1">
                <span className="min-w-0 flex-1 truncate text-foreground/90">{row.name || row.email || row.phone}</span>
                <span className="hidden min-w-0 flex-1 truncate text-muted-foreground sm:block">{row.company}</span>
                <span className="truncate text-muted-foreground">{row.email || row.phone}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={tx("import.target", "Add to list")}>
          <select className={SELECT_CLASS} value={target} onChange={(event) => setTarget(event.target.value)}>
            <option value="__new">{tx("import.newList", "New list")}</option>
            {lists.map((list) => (
              <option key={list.id} value={list.id}>
                {list.name}
              </option>
            ))}
          </select>
        </Field>
        {target === "__new" ? (
          <Field label={tx("import.newListName", "New list name")}>
            <Input
              value={listName}
              maxLength={80}
              onChange={(event) => {
                setNameTouched(true);
                setNewName(event.target.value);
              }}
            />
          </Field>
        ) : null}
        <Field label={tx("import.tags", "Tags (comma separated)")} className="sm:col-span-2">
          <Input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="crm, q4" />
        </Field>
      </div>

      <div className="flex justify-end">
        <Button className="rounded-full" disabled={busy || !kinds.length || !(preview?.count)} onClick={() => void submit()}>
          {busy ? <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
          {submitLabel ?? tx("crm.import", "Import from CRM")}
        </Button>
      </div>
    </div>
  );
}
