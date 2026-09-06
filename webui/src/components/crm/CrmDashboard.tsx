import { useState, type ReactNode } from "react";
import { motion } from "framer-motion";

import { CrmAddCard } from "@/components/crm/CrmAddCard";
import { CrmPagination } from "@/components/crm/CrmPagination";
import { CrmPipelineScene } from "@/components/crm/CrmPipelineScene";
import { Button } from "@/components/ui/button";
import type { CrmDashboard, CrmRecord } from "@/lib/api";
import { displayText, money, opportunityName, OPP_STAGES } from "@/lib/crm-format";
import { CRM_DASH_PAGE_SIZE, paginateRows } from "@/lib/crm-list";

type Follow = { opportunity?: CrmRecord; reason?: string; days?: number | null };

type Props = {
  dash: CrmDashboard | null;
  followups: Follow[];
  currency?: string;
  locale?: string;
  canWrite?: boolean;
  tx: (key: string, fallback: string) => string;
  onAdd?: (kind: "contacts" | "leads" | "opportunities") => void;
  onOpenDeal: (id: string) => void;
  onCreateFollowups: () => void;
};

function DashPagedList<T>({
  items,
  tx,
  children,
}: {
  items: T[];
  tx: (key: string, fallback: string) => string;
  children: (slice: T[]) => ReactNode;
}) {
  const [page, setPage] = useState(1);
  const slice = paginateRows(items, page, CRM_DASH_PAGE_SIZE);
  return (
    <>
      <div className="mt-2 max-h-56 min-h-0 overflow-y-auto">{children(slice)}</div>
      <CrmPagination
        page={page}
        pageSize={CRM_DASH_PAGE_SIZE}
        total={items.length}
        onPage={setPage}
        tx={tx}
      />
    </>
  );
}

export function CrmDashboardView({
  dash,
  followups,
  currency = "EUR",
  locale = "fr-FR",
  canWrite,
  tx,
  onAdd,
  onOpenDeal,
  onCreateFollowups,
}: Props) {
  const kpis = [
    { label: tx("crm.kpi.pipeline", "Pipeline total"), value: money(dash?.pipelineTotal || 0, currency, locale) },
    { label: tx("crm.kpi.open", "Opportunites ouvertes"), value: String(dash?.openCount || 0) },
    { label: tx("crm.kpi.won", "Gagnes ce mois"), value: String(dash?.wonThisMonth || 0) },
    { label: tx("crm.kpi.conversion", "Conversion"), value: `${dash?.conversion || 0} %` },
    { label: tx("crm.kpi.forecast", "Prevision du mois"), value: money(dash?.forecast || 0, currency, locale) },
  ];
  const relances = followups.length ? followups : ((dash?.followups || []) as Follow[]);
  const todos = dash?.todo || [];
  const topDeals = dash?.topDeals || [];
  return (
    <div className="h-full min-h-0 flex-1 space-y-4 overflow-y-auto">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {kpis.map((kpi, index) => (
          <motion.div
            key={kpi.label}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", duration: 0.3, bounce: 0, delay: index * 0.06 }}
            className="rounded-xl border border-border/60 bg-muted/20 px-3 py-2.5"
          >
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{kpi.label}</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">{kpi.value}</p>
          </motion.div>
        ))}
      </div>
      <CrmPipelineScene />
      <div className="flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
        {OPP_STAGES.filter((stage) => stage !== "perdu").map((stage) => (
          <span key={stage} className="rounded-full bg-muted px-2 py-0.5">
            {tx(`crm.stages.${stage}`, stage)} · {money(dash?.funnel?.[stage] || 0, currency, locale)}
          </span>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <div className="flex min-h-0 flex-col rounded-xl border border-border/60 p-3">
          <h2 className="text-[12px] font-semibold">{tx("crm.todo", "A faire aujourd'hui")}</h2>
          <DashPagedList items={todos} tx={tx}>
            {(slice) => (
              <ul className="space-y-1.5 text-[13px]">
                {todos.length === 0 ? (
                  <li className="text-muted-foreground">
                    {tx("crm.todoEmpty", "Aucune tache. Ask Navin d'en creer.")}
                  </li>
                ) : (
                  slice.map((row) => <li key={row.id}>{displayText(row.title)}</li>)
                )}
              </ul>
            )}
          </DashPagedList>
        </div>
        <div className="flex min-h-0 flex-col rounded-xl border border-border/60 p-3">
          <h2 className="text-[12px] font-semibold">{tx("crm.topDeals", "Opportunites importantes")}</h2>
          <DashPagedList items={topDeals} tx={tx}>
            {(slice) => (
              <ul className="space-y-1.5">
                {topDeals.length === 0 ? (
                  <li className="text-[13px] text-muted-foreground">
                    {tx("crm.topEmpty", "Pas encore de deal. Cree une opportunite.")}
                  </li>
                ) : (
                  slice.map((row) => (
                    <li key={row.id}>
                      <button
                        type="button"
                        className="min-h-10 cursor-pointer text-[13px] hover:underline"
                        onClick={() => onOpenDeal(row.id)}
                      >
                        {opportunityName(row) || displayText(row.id)} -{" "}
                        {money(Number(row.amount || 0), String(row.currency || currency), locale)}
                      </button>
                    </li>
                  ))
                )}
              </ul>
            )}
          </DashPagedList>
        </div>
        <div className="flex min-h-0 flex-col rounded-xl border border-border/60 p-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-[12px] font-semibold">{tx("crm.followups", "Relances")}</h2>
            <Button type="button" size="sm" variant="outline" className="h-7 cursor-pointer text-[11px]" onClick={onCreateFollowups}>
              {tx("crm.createFollowups", "Creer les taches")}
            </Button>
          </div>
          <DashPagedList items={relances} tx={tx}>
            {(slice) => (
              <ul className="space-y-1.5 text-[13px]">
                {relances.length === 0 ? (
                  <li className="text-muted-foreground">{tx("crm.followupsEmpty", "Rien a relancer.")}</li>
                ) : (
                  slice.map((item, index) => {
                    const opp = item.opportunity;
                    if (!opp) return null;
                    return (
                      <li key={opp.id || index}>
                        <button
                          type="button"
                          className="min-h-10 cursor-pointer text-left hover:underline"
                          onClick={() => onOpenDeal(opp.id)}
                        >
                          {opportunityName(opp) || displayText(opp.id)} - {displayText(item.reason)}
                        </button>
                      </li>
                    );
                  })
                )}
              </ul>
            )}
          </DashPagedList>
        </div>
      </div>
      {canWrite && onAdd ? (
        <div className="grid gap-2 sm:grid-cols-3">
          <CrmAddCard compact label={tx("crm.addContactCard", "+ Ajouter un contact")} onClick={() => onAdd("contacts")} />
          <CrmAddCard compact label={tx("crm.addLeadCard", "+ Ajouter un lead")} onClick={() => onAdd("leads")} />
          <CrmAddCard compact label={tx("crm.addDealCard", "+ Ajouter une opportunite")} onClick={() => onAdd("opportunities")} />
        </div>
      ) : null}
    </div>
  );
}
