import { useMemo } from "react";

import { CrmAddCard } from "@/components/crm/CrmAddCard";
import { CrmExportMenu } from "@/components/crm/CrmExportMenu";
import { CrmFicheFact, CrmFichePane } from "@/components/crm/CrmFichePane";
import { CrmInsightCard } from "@/components/crm/CrmInsights";
import { CrmListToolbar, type CrmToolbarFacet } from "@/components/crm/CrmListToolbar";
import { CrmMasterDetail } from "@/components/crm/CrmMasterDetail";
import { CrmPagination } from "@/components/crm/CrmPagination";
import { CrmTimeline } from "@/components/crm/CrmTimeline";
import { Button } from "@/components/ui/button";
import type { CrmAudit, CrmInsights, CrmMember, CrmRecord } from "@/lib/api";
import { countryLabel, LEAD_SOURCES, optionList } from "@/lib/crm-catalog";
import { displayText, LEAD_STAGES, leadName } from "@/lib/crm-format";
import {
  countryFilterOptions,
  countLabel,
  ownerFilterOptions,
  sourceFilterOptions,
  tagFilterOptions,
  useCrmFilteredPage,
} from "@/lib/crm-list";
import { cn } from "@/lib/utils";

type Props = {
  leads: CrmRecord[];
  members?: CrmMember[];
  busy: boolean;
  openId: string | null;
  timeline: CrmRecord[];
  audit?: CrmAudit[];
  insight: CrmInsights | null;
  canWrite: boolean;
  locale?: string;
  tx: (key: string, fallback: string) => string;
  onAdd: () => void;
  onEdit: (row: CrmRecord) => void;
  onDelete: (row: CrmRecord) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
  onConvert: (row: CrmRecord) => void;
  onOpenOpportunity?: (id: string) => void;
};

export function CrmLeads({
  leads,
  members = [],
  openId,
  timeline,
  audit,
  insight,
  canWrite,
  locale = "fr-FR",
  tx,
  onAdd,
  onEdit,
  onDelete,
  onOpen,
  onClose,
  onConvert,
  onOpenOpportunity,
}: Props) {
  const { filter, facets, setFilter, setFacet, filtered, slice, page, setPage, pageSize } = useCrmFilteredPage(
    leads,
    {},
    members,
  );
  const lang = locale.toLowerCase().startsWith("en") ? "en" : "fr";
  const facetsDef = useMemo<CrmToolbarFacet[]>(() => {
    const next: CrmToolbarFacet[] = [
      {
        key: "status",
        label: tx("crm.status", "Statut"),
        options: optionList(LEAD_STAGES, (value) => tx(`crm.leadStages.${value}`, value)),
      },
    ];
    const owners = ownerFilterOptions(leads, members);
    if (owners.length) next.push({ key: "owner", label: tx("crm.owner", "Proprietaire"), options: owners });
    const countries = countryFilterOptions(leads, lang);
    if (countries.length) next.push({ key: "country", label: tx("crm.country", "Pays"), options: countries });
    next.push({
      key: "source",
      label: tx("crm.source", "Source"),
      options: sourceFilterOptions(leads, LEAD_SOURCES, (value) => tx(`crm.sources.${value}`, value)),
    });
    const tags = tagFilterOptions(leads);
    if (tags.length) next.push({ key: "tag", label: tx("crm.tag", "Tag"), options: tags });
    return next;
  }, [lang, leads, members, tx]);

  const open = leads.find((row) => row.id === openId);
  const emptyFiltered = leads.length > 0 && filtered.length === 0;

  return (
    <CrmMasterDetail
      toolbar={
        <CrmListToolbar
          query={filter}
          onQueryChange={setFilter}
          facets={facetsDef}
          facetValues={facets}
          onFacetChange={setFacet}
          countLabel={countLabel(filtered.length, tx("crm.noun.lead", "lead"), tx("crm.noun.leads", "leads"))}
          tx={tx}
          actions={<CrmExportMenu kind="leads" rows={filtered} locale={locale} tx={tx} />}
        />
      }
      list={
        <>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {canWrite ? (
              <div className="pb-3">
                <CrmAddCard label={tx("crm.addLeadCard", "+ Ajouter un lead")} onClick={onAdd} compact />
              </div>
            ) : null}
            <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60">
              {leads.length === 0 ? (
                <li className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyLeads", "Ajoute un lead. Convertir cree contact + entreprise + deal.")}
                </li>
              ) : emptyFiltered ? (
                <li className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyFilter", "Aucun resultat pour ces filtres.")}
                </li>
              ) : (
                slice.map((row) => (
                  <li key={row.id} className="flex items-center gap-2 px-3 py-2 text-[13px]">
                    <button
                      type="button"
                      onClick={() => onOpen(row.id)}
                      className={cn(
                        "min-h-10 min-w-0 flex-1 cursor-pointer rounded-md px-1 text-left hover:bg-muted/40",
                        openId === row.id && "bg-muted/50",
                      )}
                    >
                      <div className="font-medium">{leadName(row)}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {[displayText(row.company), tx(`crm.leadStages.${String(row.status)}`, String(row.status))]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </button>
                    {row.status === "converti" ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 cursor-pointer text-[11px]"
                        onClick={() => {
                          const oppId = String(row.convertedOpportunityId || "");
                          if (oppId) onOpenOpportunity?.(oppId);
                        }}
                      >
                        {tx("crm.converted", "Converti")}
                      </Button>
                    ) : canWrite ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 cursor-pointer text-[11px]"
                        onClick={() => onConvert(row)}
                      >
                        {tx("crm.convertToOpportunity", "Transformer en opportunite")}
                      </Button>
                    ) : null}
                  </li>
                ))
              )}
            </ul>
          </div>
          <CrmPagination page={page} pageSize={pageSize} total={filtered.length} onPage={setPage} tx={tx} />
        </>
      }
      fiche={
        <CrmFichePane
          open={Boolean(open)}
          title={open ? leadName(open) : ""}
          emptyLabel={tx("crm.pickLead", "Choisis un lead")}
          canWrite={canWrite}
          tx={tx}
          onClose={onClose}
          onEdit={open ? () => onEdit(open) : undefined}
          onDelete={open ? () => onDelete(open) : undefined}
        >
          {open ? (
            <>
              <dl className="grid gap-2 rounded-xl border border-border/60 p-3 text-[12px]">
                <CrmFicheFact label={tx("crm.companyName", "Entreprise")} value={open.company} />
                <CrmFicheFact label={tx("crm.email", "Email")} value={open.email} />
                <CrmFicheFact label={tx("crm.phone", "Telephone")} value={open.phone} />
                <CrmFicheFact
                  label={tx("crm.country", "Pays")}
                  value={open.country ? countryLabel(String(open.country), lang) : ""}
                />
                <CrmFicheFact label={tx("crm.source", "Source")} value={open.source} />
                <CrmFicheFact label={tx("crm.score", "Score")} value={open.score} />
                <CrmFicheFact
                  label={tx("crm.status", "Statut")}
                  value={tx(`crm.leadStages.${String(open.status)}`, String(open.status))}
                />
              </dl>
              {canWrite && open.status !== "converti" ? (
                <Button
                  type="button"
                  size="sm"
                  className="h-8 w-full cursor-pointer text-[12px]"
                  onClick={() => onConvert(open)}
                >
                  {tx("crm.convertToOpportunity", "Transformer en opportunite")}
                </Button>
              ) : null}
              {open.status === "converti" && open.convertedOpportunityId ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-8 w-full cursor-pointer text-[12px]"
                  onClick={() => onOpenOpportunity?.(String(open.convertedOpportunityId))}
                >
                  {tx("crm.viewOpportunity", "Voir l'opportunite")}
                </Button>
              ) : null}
              <CrmInsightCard insight={insight} tx={tx} />
              <CrmTimeline timeline={timeline} audit={audit} leads={leads} tx={tx} />
            </>
          ) : null}
        </CrmFichePane>
      }
    />
  );
}
