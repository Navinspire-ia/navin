import { useMemo, useState } from "react";

import { CrmAddCard } from "@/components/crm/CrmAddCard";
import { CrmFicheFact, CrmFichePane } from "@/components/crm/CrmFichePane";
import { CrmInsightCard } from "@/components/crm/CrmInsights";
import { CrmListToolbar, type CrmToolbarFacet } from "@/components/crm/CrmListToolbar";
import { CrmMasterDetail } from "@/components/crm/CrmMasterDetail";
import { CrmPagination } from "@/components/crm/CrmPagination";
import { CrmTimeline } from "@/components/crm/CrmTimeline";
import { Button } from "@/components/ui/button";
import type { CrmAudit, CrmInsights, CrmMember, CrmRecord } from "@/lib/api";
import { companyName, contactName, displayText, money, opportunityName } from "@/lib/crm-format";
import {
  countryFilterOptions,
  countLabel,
  ownerFilterOptions,
  tagFilterOptions,
  useCrmFilteredPage,
} from "@/lib/crm-list";
import { cn } from "@/lib/utils";

const TABS = ["contacts", "opportunities", "activities", "documents", "notes"] as const;

type Props = {
  companies: CrmRecord[];
  contacts: CrmRecord[];
  opportunities: CrmRecord[];
  activities: CrmRecord[];
  files: CrmRecord[];
  members?: CrmMember[];
  busy: boolean;
  openId: string | null;
  timeline: CrmRecord[];
  audit?: CrmAudit[];
  insight: CrmInsights | null;
  canWrite: boolean;
  currency: string;
  locale: string;
  tx: (key: string, fallback: string) => string;
  onAdd: () => void;
  onEdit: (row: CrmRecord) => void;
  onDelete: (row: CrmRecord) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
  onUpdate: (id: string, body: Record<string, unknown>) => void;
  onOpenContact: (id: string) => void;
  onOpenDeal: (id: string) => void;
};

export function CrmCompanies({
  companies,
  contacts,
  opportunities,
  activities,
  files,
  members = [],
  openId,
  timeline,
  audit,
  insight,
  canWrite,
  currency,
  locale,
  tx,
  onAdd,
  onEdit,
  onDelete,
  onOpen,
  onClose,
  onUpdate,
  onOpenContact,
  onOpenDeal,
}: Props) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("contacts");
  const { filter, facets, setFilter, setFacet, filtered, slice, page, setPage, pageSize } = useCrmFilteredPage(
    companies,
    {},
    members,
  );
  const lang = locale.toLowerCase().startsWith("en") ? "en" : "fr";
  const facetsDef = useMemo<CrmToolbarFacet[]>(() => {
    const next: CrmToolbarFacet[] = [];
    const owners = ownerFilterOptions(companies, members);
    if (owners.length) next.push({ key: "owner", label: tx("crm.owner", "Proprietaire"), options: owners });
    const countries = countryFilterOptions(companies, lang);
    if (countries.length) next.push({ key: "country", label: tx("crm.country", "Pays"), options: countries });
    const tags = tagFilterOptions(companies);
    if (tags.length) next.push({ key: "tag", label: tx("crm.tag", "Tag"), options: tags });
    return next;
  }, [companies, lang, members, tx]);

  const open = companies.find((row) => row.id === openId);
  const cts = contacts.filter((item) => item.companyId === openId);
  const opps = opportunities.filter((item) => item.companyId === openId);
  const acts = activities.filter((item) => item.companyId === openId);
  const docs = files.filter((item) => item.companyId === openId);
  const emptyFiltered = companies.length > 0 && filtered.length === 0;

  return (
    <CrmMasterDetail
      toolbar={
        <CrmListToolbar
          query={filter}
          onQueryChange={setFilter}
          facets={facetsDef}
          facetValues={facets}
          onFacetChange={setFacet}
          countLabel={countLabel(
            filtered.length,
            tx("crm.noun.company", "entreprise"),
            tx("crm.noun.companies", "entreprises"),
          )}
          tx={tx}
          searchPlaceholder={tx("crm.searchCompanyList", "Nom, secteur, pays, telephone...")}
        />
      }
      list={
        <>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {companies.length === 0 ? (
              <div className="space-y-3">
                {canWrite ? (
                  <CrmAddCard label={tx("crm.addCompanyCard", "+ Ajouter une entreprise")} onClick={onAdd} compact />
                ) : null}
                <p className="py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyCompanies", "Ajoute une entreprise, ou convertis un lead.")}
                </p>
              </div>
            ) : emptyFiltered ? (
              <div className="space-y-3">
                {canWrite ? (
                  <CrmAddCard label={tx("crm.addCompanyCard", "+ Ajouter une entreprise")} onClick={onAdd} compact />
                ) : null}
                <p className="py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyFilter", "Aucun resultat pour ces filtres.")}
                </p>
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {canWrite ? (
                  <CrmAddCard label={tx("crm.addCompanyCard", "+ Ajouter une entreprise")} onClick={onAdd} compact />
                ) : null}
                {slice.map((row) => {
                  const pot = opportunities
                    .filter((item) => item.companyId === row.id)
                    .reduce((sum, item) => sum + Number(item.amount || 0), 0);
                  return (
                    <button
                      key={row.id}
                      type="button"
                      onClick={() => onOpen(row.id)}
                      className={cn(
                        "min-h-10 w-full cursor-pointer rounded-2xl border border-border/60 p-3 text-left transition-[background-color,transform] duration-150 hover:bg-muted/30 active:scale-[0.96]",
                        openId === row.id && "border-violet-400/70 bg-muted/30",
                      )}
                    >
                      <div className="text-[14px] font-semibold">{companyName(row) || displayText(row.id)}</div>
                      <p className="text-[11px] text-muted-foreground">
                        {[row.industry, row.country].filter(Boolean).join(" · ") || tx("crm.noMeta", "Sans secteur")}
                      </p>
                      <p className="mt-2 text-[11px] tabular-nums">
                        {tx("crm.potential", "CA potentiel")} {money(pot, currency, locale)}
                      </p>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <CrmPagination page={page} pageSize={pageSize} total={filtered.length} onPage={setPage} tx={tx} />
        </>
      }
      fiche={
        <CrmFichePane
          open={Boolean(open)}
          title={open ? companyName(open) || displayText(open.id) : ""}
          emptyLabel={tx("crm.pickCompany", "Choisis une entreprise")}
          canWrite={canWrite}
          tx={tx}
          onClose={onClose}
          onEdit={open ? () => onEdit(open) : undefined}
          onDelete={open ? () => onDelete(open) : undefined}
        >
          {open ? (
            <>
              <dl className="grid gap-2 rounded-xl border border-border/60 p-3 text-[12px]">
                <CrmFicheFact label={tx("crm.industry", "Secteur")} value={open.industry} />
                <CrmFicheFact label={tx("crm.country", "Pays")} value={open.country} />
                <CrmFicheFact label={tx("crm.website", "Site")} value={open.website} />
                <CrmFicheFact label={tx("crm.phone", "Telephone")} value={open.phone} />
                <CrmFicheFact label={tx("crm.owner", "Proprietaire")} value={open.owner} />
              </dl>
              <div className="flex flex-wrap gap-1">
                {TABS.map((item) => (
                  <Button
                    key={item}
                    type="button"
                    size="sm"
                    variant={tab === item ? "default" : "outline"}
                    className="h-8 cursor-pointer text-[11px]"
                    onClick={() => setTab(item)}
                  >
                    {tx(`crm.companyTabs.${item}`, item)}
                  </Button>
                ))}
              </div>
              <div className="text-[13px]">
                {tab === "contacts"
                  ? cts.map((row) => (
                      <button
                        key={row.id}
                        type="button"
                        className="block min-h-10 cursor-pointer hover:underline"
                        onClick={() => onOpenContact(row.id)}
                      >
                        {contactName(row)}
                      </button>
                    ))
                  : null}
                {tab === "opportunities"
                  ? opps.map((row) => (
                      <button
                        key={row.id}
                        type="button"
                        className="block min-h-10 cursor-pointer hover:underline"
                        onClick={() => onOpenDeal(row.id)}
                      >
                        {opportunityName(row) || displayText(row.id)} - {money(Number(row.amount || 0), currency, locale)}
                      </button>
                    ))
                  : null}
                {tab === "activities"
                  ? acts.map((row) => (
                      <p key={row.id} className="py-1">
                        {displayText(row.title)}
                      </p>
                    ))
                  : null}
                {tab === "documents"
                  ? docs.length
                    ? docs.map((row) => <p key={row.id}>{displayText(row.name) || row.id}</p>)
                    : tx("crm.noDocuments", "Aucun document.")
                  : null}
                {tab === "notes" ? (
                  <textarea
                    defaultValue={displayText(open.notes)}
                    onBlur={(event) => onUpdate(open.id, { notes: event.target.value })}
                    className="min-h-28 w-full rounded-md border border-border/70 p-2 text-[12px]"
                  />
                ) : null}
              </div>
              <CrmInsightCard insight={insight} tx={tx} />
              <CrmTimeline
                timeline={timeline}
                audit={audit}
                contacts={contacts}
                companies={companies}
                opportunities={opportunities}
                tx={tx}
              />
            </>
          ) : null}
        </CrmFichePane>
      }
    />
  );
}
