// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo } from "react";

import { CrmAddCard } from "@/components/crm/CrmAddCard";
import { CrmFicheFact, CrmFichePane } from "@/components/crm/CrmFichePane";
import { CrmListToolbar, type CrmToolbarFacet } from "@/components/crm/CrmListToolbar";
import { CrmMasterDetail } from "@/components/crm/CrmMasterDetail";
import { CrmPagination } from "@/components/crm/CrmPagination";
import { Button } from "@/components/ui/button";
import type { CrmRecord } from "@/lib/api";
import { optionList } from "@/lib/crm-catalog";
import {
  ACTIVITY_KINDS,
  companyName,
  contactName,
  dayStart,
  displayText,
  formatWhen,
  leadName,
  opportunityName,
} from "@/lib/crm-format";
import { applyCrmFilters, countLabel, useCrmFilteredPage } from "@/lib/crm-list";
import { cn } from "@/lib/utils";
import { useCrmUi } from "@/store/crm-ui";

type Props = {
  rows: CrmRecord[];
  calendarRows: CrmRecord[];
  contacts?: CrmRecord[];
  companies?: CrmRecord[];
  opportunities?: CrmRecord[];
  leads?: CrmRecord[];
  view: "list" | "calendar";
  busy: boolean;
  month: Date;
  openId: string | null;
  canWrite: boolean;
  locale?: string;
  tx: (key: string, fallback: string) => string;
  onAdd: () => void;
  onEdit: (row: CrmRecord) => void;
  onDelete: (row: CrmRecord) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
  onPrevMonth: () => void;
  onNextMonth: () => void;
  onPickDay: (unix: number) => void;
};

export function CrmActivities({
  rows,
  calendarRows,
  contacts = [],
  companies = [],
  opportunities = [],
  leads = [],
  view,
  month,
  openId,
  canWrite,
  locale = "fr-FR",
  tx,
  onAdd,
  onEdit,
  onDelete,
  onOpen,
  onClose,
  onPrevMonth,
  onNextMonth,
  onPickDay,
}: Props) {
  const { filter, facets, setFilter, setFacet, filtered, slice, page, setPage, pageSize } = useCrmFilteredPage(rows);
  const storeFilter = useCrmUi((state) => state.filter);
  const storeFacets = useCrmUi((state) => state.facets);
  const calendarFiltered = useMemo(
    () => applyCrmFilters(calendarRows, storeFilter, storeFacets),
    [calendarRows, storeFacets, storeFilter],
  );
  const facetsDef = useMemo<CrmToolbarFacet[]>(
    () => [
      {
        key: "kind",
        label: tx("crm.activityKind", "Type"),
        options: optionList(ACTIVITY_KINDS, (value) => tx(`crm.kinds.${value}`, value)),
      },
    ],
    [tx],
  );

  const cells = useMemo(() => {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const startPad = (first.getDay() + 6) % 7;
    const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    const out: Array<{ date: Date | null; items: CrmRecord[] }> = [];
    for (let i = 0; i < startPad; i += 1) out.push({ date: null, items: [] });
    for (let day = 1; day <= days; day += 1) {
      const date = new Date(month.getFullYear(), month.getMonth(), day);
      const start = dayStart(date);
      const end = start + 86400;
      out.push({
        date,
        items: calendarFiltered.filter((row) => {
          const at = Number(row.at || 0);
          return at >= start && at < end;
        }),
      });
    }
    return out;
  }, [calendarFiltered, month]);

  const open = rows.find((row) => row.id === openId) || calendarRows.find((row) => row.id === openId);
  const emptyFiltered = rows.length > 0 && filtered.length === 0;
  const linkedContact = contacts.find((item) => item.id === open?.contactId);
  const linkedCompany = companies.find((item) => item.id === open?.companyId);
  const linkedDeal = opportunities.find((item) => item.id === open?.opportunityId);
  const linkedLead = leads.find((item) => item.id === open?.leadId);

  const fiche = (
    <CrmFichePane
      open={Boolean(open)}
      title={open ? displayText(open.title) || tx(`crm.kinds.${String(open.kind)}`, String(open.kind)) : ""}
      emptyLabel={tx("crm.pickActivity", "Choisis une activite")}
      canWrite={canWrite}
      tx={tx}
      onClose={onClose}
      onEdit={open ? () => onEdit(open) : undefined}
      onDelete={open ? () => onDelete(open) : undefined}
    >
      {open ? (
        <dl className="grid gap-2 rounded-xl border border-border/60 p-3 text-[12px]">
          <CrmFicheFact
            label={tx("crm.activityKind", "Type")}
            value={tx(`crm.kinds.${String(open.kind)}`, String(open.kind))}
          />
          <CrmFicheFact
            label={tx("crm.activityAt", "Date et heure")}
            value={open.at ? formatWhen(Number(open.at), locale) : ""}
          />
          <CrmFicheFact label={tx("crm.linkContact", "Contact")} value={contactName(linkedContact)} />
          <CrmFicheFact label={tx("crm.companyName", "Entreprise")} value={companyName(linkedCompany)} />
          <CrmFicheFact label={tx("crm.linkDeal", "Opportunite")} value={opportunityName(linkedDeal)} />
          <CrmFicheFact label={tx("crm.linkLead", "Lead")} value={leadName(linkedLead)} />
          <CrmFicheFact label={tx("crm.activityBody", "Detail")} value={open.body} />
        </dl>
      ) : null}
    </CrmFichePane>
  );

  const toolbar = (
    <CrmListToolbar
      query={filter}
      onQueryChange={setFilter}
      facets={facetsDef}
      facetValues={facets}
      onFacetChange={setFacet}
      countLabel={countLabel(
        view === "calendar" ? calendarFiltered.length : filtered.length,
        tx("crm.noun.activity", "activite"),
        tx("crm.noun.activities", "activites"),
      )}
      tx={tx}
      showDateRange
      searchPlaceholder={tx("crm.searchActivity", "Titre, detail...")}
    />
  );

  if (view === "calendar") {
    return (
      <CrmMasterDetail
        toolbar={toolbar}
        list={
          <div className="min-h-0 flex-1 overflow-y-auto">
            {canWrite ? (
              <div className="pb-3">
                <CrmAddCard label={tx("crm.addActivityCard", "+ Ajouter une activite")} onClick={onAdd} compact />
              </div>
            ) : null}
            <div className="mb-2 flex items-center justify-between">
              <Button type="button" size="sm" variant="outline" className="h-8 cursor-pointer" onClick={onPrevMonth}>
                {tx("crm.prev", "Precedent")}
              </Button>
              <p className="text-[13px] font-semibold">
                {month.toLocaleDateString(locale, { month: "long", year: "numeric" })}
              </p>
              <Button type="button" size="sm" variant="outline" className="h-8 cursor-pointer" onClick={onNextMonth}>
                {tx("crm.next", "Suivant")}
              </Button>
            </div>
            <div className="grid grid-cols-7 gap-1 text-[11px]">
              {cells.map((cell, index) => (
                <button
                  key={index}
                  type="button"
                  disabled={!cell.date}
                  onClick={() => {
                    if (!cell.date || !canWrite) return;
                    onPickDay(dayStart(cell.date));
                  }}
                  className="min-h-20 cursor-pointer rounded-lg border border-border/50 p-1 text-left hover:bg-muted/40 disabled:cursor-default disabled:opacity-40"
                >
                  <div className="tabular-nums text-muted-foreground">{cell.date ? cell.date.getDate() : ""}</div>
                  {cell.items.slice(0, 3).map((row) => (
                    <div
                      key={row.id}
                      className={cn("truncate text-[10px] hover:underline", openId === row.id && "font-semibold")}
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpen(row.id);
                      }}
                    >
                      {displayText(row.title)}
                    </div>
                  ))}
                </button>
              ))}
            </div>
          </div>
        }
        fiche={fiche}
      />
    );
  }

  return (
    <CrmMasterDetail
      toolbar={toolbar}
      list={
        <>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {canWrite ? (
              <div className="pb-3">
                <CrmAddCard label={tx("crm.addActivityCard", "+ Ajouter une activite")} onClick={onAdd} compact />
              </div>
            ) : null}
            <ul className="space-y-1.5">
              {rows.length === 0 ? (
                <li className="py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyActivities", "Journalise un appel, un mail ou une tache.")}
                </li>
              ) : emptyFiltered ? (
                <li className="py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyFilter", "Aucun resultat pour ces filtres.")}
                </li>
              ) : (
                slice.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => onOpen(row.id)}
                      className={cn(
                        "flex min-h-10 w-full cursor-pointer items-start justify-between gap-2 rounded-lg border border-border/60 px-3 py-2 text-left text-[13px] hover:bg-muted/40",
                        openId === row.id && "border-violet-400/70 bg-muted/30",
                      )}
                    >
                      <div className="min-w-0">
                        <span className="text-[10px] uppercase text-muted-foreground">
                          {tx(`crm.kinds.${String(row.kind)}`, String(row.kind))}
                        </span>
                        <div className="font-medium">{displayText(row.title)}</div>
                        {displayText(row.body) ? (
                          <p className="text-[12px] text-muted-foreground">{displayText(row.body)}</p>
                        ) : null}
                        {row.at ? (
                          <p className="text-[11px] tabular-nums text-muted-foreground">
                            {formatWhen(Number(row.at), locale)}
                          </p>
                        ) : null}
                      </div>
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
          <CrmPagination page={page} pageSize={pageSize} total={filtered.length} onPage={setPage} tx={tx} />
        </>
      }
      fiche={fiche}
    />
  );
}
