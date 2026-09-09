// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useMemo, useState } from "react";

import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { CrmAddCard } from "@/components/crm/CrmAddCard";
import { CrmExportMenu } from "@/components/crm/CrmExportMenu";
import { CrmFicheFact, CrmFichePane } from "@/components/crm/CrmFichePane";
import { CrmInsightCard } from "@/components/crm/CrmInsights";
import { CrmListToolbar, type CrmToolbarFacet } from "@/components/crm/CrmListToolbar";
import { CrmMasterDetail } from "@/components/crm/CrmMasterDetail";
import { CrmOutreach } from "@/components/crm/CrmOutreach";
import { CrmPagination } from "@/components/crm/CrmPagination";
import { CrmTimeline } from "@/components/crm/CrmTimeline";
import { Button } from "@/components/ui/button";
import type { CrmAudit, CrmChannelStatus, CrmDashboard, CrmInsights, CrmMember, CrmRecord } from "@/lib/api";
import { optionList } from "@/lib/crm-catalog";
import { companyName, contactName, displayText, money, OPP_STAGES, opportunityName } from "@/lib/crm-format";
import {
  countryFilterOptions,
  countLabel,
  ownerFilterOptions,
  tagFilterOptions,
  useCrmFilteredPage,
} from "@/lib/crm-list";
import { cn } from "@/lib/utils";

type Props = {
  rows: CrmRecord[];
  products: CrmRecord[];
  lines: CrmRecord[];
  contacts: CrmRecord[];
  companies?: CrmRecord[];
  members?: CrmMember[];
  view: "table" | "pipeline";
  busy: boolean;
  dash: CrmDashboard | null;
  openId: string | null;
  insight: CrmInsights | null;
  timeline: CrmRecord[];
  audit?: CrmAudit[];
  channels: { email: CrmChannelStatus; whatsapp: CrmChannelStatus; teams: CrmChannelStatus } | null;
  canWrite: boolean;
  currency: string;
  locale: string;
  tx: (key: string, fallback: string) => string;
  token: string;
  sessionKey: string;
  actor: string;
  onAdd: () => void;
  onEdit: (row: CrmRecord) => void;
  onDelete: (row: CrmRecord) => void;
  onMove: (id: string, stage: string) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
  onAddProduct: () => void;
  onAddLine: (opportunityId: string, body: Record<string, unknown>) => void;
  onDeleteLine?: (row: CrmRecord) => void;
  onOutreach: (body: Record<string, unknown>) => Promise<void>;
};

export function CrmOpportunities({
  rows,
  products,
  lines,
  contacts,
  companies = [],
  members = [],
  view,
  dash,
  openId,
  insight,
  timeline,
  audit,
  channels,
  canWrite,
  currency,
  locale,
  tx,
  token,
  sessionKey,
  actor,
  onAdd,
  onEdit,
  onDelete,
  onMove,
  onOpen,
  onClose,
  onAddProduct,
  onAddLine,
  onDeleteLine,
  onOutreach,
}: Props) {
  const [lineName, setLineName] = useState("");
  const [lineQty, setLineQty] = useState("1");
  const [linePrice, setLinePrice] = useState("");
  const [lineProduct, setLineProduct] = useState("");
  const extraHaystack = useCallback(
    (row: CrmRecord) => companyName(companies.find((item) => item.id === row.companyId)),
    [companies],
  );
  const { filter, facets, setFilter, setFacet, filtered, slice, page, setPage, pageSize } = useCrmFilteredPage(
    rows,
    { extraHaystack, statusKeys: ["stage", "status"] },
    members,
  );
  const lang = locale.toLowerCase().startsWith("en") ? "en" : "fr";
  const facetsDef = useMemo<CrmToolbarFacet[]>(() => {
    const next: CrmToolbarFacet[] = [
      {
        key: "status",
        label: tx("crm.stage", "Etape"),
        options: optionList(OPP_STAGES, (value) => tx(`crm.stages.${value}`, value)),
      },
    ];
    const owners = ownerFilterOptions(rows, members);
    if (owners.length) next.push({ key: "owner", label: tx("crm.owner", "Proprietaire"), options: owners });
    const countries = countryFilterOptions(rows, lang);
    if (countries.length) next.push({ key: "country", label: tx("crm.country", "Pays"), options: countries });
    const tags = tagFilterOptions(rows);
    if (tags.length) next.push({ key: "tag", label: tx("crm.tag", "Tag"), options: tags });
    return next;
  }, [lang, members, rows, tx]);

  const open = rows.find((row) => row.id === openId);
  const dealLines = lines.filter((row) => row.opportunityId === openId);
  const linked = contacts.filter((row) => (open?.contactIds as string[] | undefined)?.includes(row.id));
  const company = companies.find((row) => row.id === open?.companyId);
  const fmt = (value: number, rowCurrency?: unknown) =>
    money(value, String(rowCurrency || currency), locale);
  const emptyFiltered = rows.length > 0 && filtered.length === 0;

  const fiche = (
    <CrmFichePane
      open={Boolean(open)}
      title={open ? opportunityName(open) || displayText(open.id) : ""}
      emptyLabel={tx("crm.pickOpportunity", "Choisis une opportunite")}
      canWrite={canWrite}
      tx={tx}
      onClose={onClose}
      onEdit={open ? () => onEdit(open) : undefined}
      onDelete={open ? () => onDelete(open) : undefined}
      extraActions={
        canWrite && open ? (
          <CrmOutreach
            token={token}
            sessionKey={sessionKey}
            actor={actor}
            opportunity={open}
            contact={linked[0]}
            channels={channels}
            tx={tx}
            onOutreach={onOutreach}
          />
        ) : null
      }
    >
      {open ? (
        <>
          <p className="text-[12px] text-muted-foreground">
            {fmt(Number(open.amount || 0), open.currency)} · {displayText(open.probability) || "0"}% ·{" "}
            {tx(`crm.stages.${String(open.stage)}`, String(open.stage))}
          </p>
          <dl className="grid gap-2 rounded-xl border border-border/60 p-3 text-[12px]">
            <CrmFicheFact label={tx("crm.tabs.companies", "Entreprise")} value={companyName(company)} />
            <CrmFicheFact
              label={tx("crm.tabs.contacts", "Contacts")}
              value={linked.map((row) => contactName(row)).filter(Boolean).join(", ")}
            />
            <CrmFicheFact label={tx("crm.nextAction", "Prochaine action")} value={open.nextAction} />
          </dl>
          <div>
            <p className="text-[12px] font-semibold">{tx("crm.lines", "Lignes")}</p>
            <ul className="mt-1 text-[12px]">
              {dealLines.length === 0 ? (
                <li className="text-muted-foreground">{tx("crm.noLines", "Aucune ligne.")}</li>
              ) : (
                dealLines.map((row) => (
                  <li key={row.id} className="flex items-center justify-between gap-2">
                    <span>
                      {displayText(row.name) || tx("crm.line", "Ligne")} × {displayText(row.qty) || "1"}
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="tabular-nums">{fmt(Number(row.total || 0), open.currency)}</span>
                      {canWrite && onDeleteLine ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className="h-7 cursor-pointer text-[11px] text-destructive"
                          onClick={() => onDeleteLine(row)}
                        >
                          {tx("crm.delete", "Supprimer")}
                        </Button>
                      ) : null}
                    </span>
                  </li>
                ))
              )}
            </ul>
            {canWrite ? (
              <form
                className="mt-2 flex flex-wrap gap-1.5"
                onSubmit={(event) => {
                  event.preventDefault();
                  const product = products.find((row) => row.id === lineProduct);
                  void onAddLine(open.id, {
                    name: lineName || displayText(product?.name) || "Ligne",
                    productId: lineProduct,
                    qty: Number(lineQty || 1),
                    unitPrice: Number(linePrice || product?.defaultPrice || 0),
                  });
                  setLineName("");
                  setLinePrice("");
                }}
              >
                <div className="min-w-[10rem] flex-1">
                  <SearchableSelect
                    value={lineProduct}
                    onChange={setLineProduct}
                    options={products.map((row) => ({
                      value: row.id,
                      label: displayText(row.name) || row.id,
                      keywords: displayText(row.name).toLowerCase(),
                    }))}
                    allowEmpty
                    emptyLabelOption={tx("crm.freeLine", "Texte libre")}
                    placeholder={tx("crm.productName", "Produit")}
                    searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
                    emptyLabel={tx("crm.noResults", "Aucun resultat")}
                    className="h-8 text-[12px]"
                  />
                </div>
                <input
                  value={lineName}
                  onChange={(event) => setLineName(event.target.value)}
                  placeholder={tx("crm.lineName", "Libelle")}
                  className="h-8 rounded-md border border-border/70 px-2 text-[12px]"
                />
                <input
                  value={lineQty}
                  onChange={(event) => setLineQty(event.target.value)}
                  className="h-8 w-14 rounded-md border border-border/70 px-2 text-[12px]"
                />
                <input
                  value={linePrice}
                  onChange={(event) => setLinePrice(event.target.value)}
                  placeholder={tx("crm.unitPrice", "PU")}
                  className="h-8 w-20 rounded-md border border-border/70 px-2 text-[12px]"
                />
                <Button type="submit" size="sm" className="h-8 cursor-pointer text-[11px]">
                  {tx("crm.addLine", "Ajouter ligne")}
                </Button>
              </form>
            ) : null}
          </div>
          {canWrite ? (
            <div>
              <p className="text-[12px] font-semibold">{tx("crm.catalog", "Catalogue produits")}</p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-1 h-8 cursor-pointer text-[11px]"
                onClick={onAddProduct}
              >
                {tx("crm.addProductCard", "+ Ajouter un produit")}
              </Button>
            </div>
          ) : null}
          <CrmInsightCard insight={insight} tx={tx} />
          <CrmTimeline
            timeline={timeline}
            audit={audit}
            contacts={contacts}
            companies={companies}
            opportunities={rows}
            tx={tx}
          />
        </>
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
        filtered.length,
        tx("crm.noun.opportunity", "opportunite"),
        tx("crm.noun.opportunities", "opportunites"),
      )}
      tx={tx}
      searchPlaceholder={tx("crm.searchDeal", "Nom, entreprise, proprietaire...")}
      actions={
        <CrmExportMenu
          kind="opportunities"
          rows={filtered}
          companies={companies}
          contacts={contacts}
          locale={locale}
          tx={tx}
        />
      }
    />
  );

  const before = (
    <p className="shrink-0 pb-2 text-[12px] text-muted-foreground">
      {tx("crm.pipelineBar", "Pipeline")} : {fmt(dash?.pipelineTotal || 0)} ·{" "}
      {tx("crm.kpi.forecast", "Prevision")} : {fmt(dash?.forecast || 0)} · {filtered.length} deals
    </p>
  );

  if (view === "pipeline") {
    return (
      <CrmMasterDetail
        before={before}
        toolbar={toolbar}
        list={
          <div className="min-h-0 flex-1 overflow-x-auto pb-2">
            {canWrite ? (
              <div className="pb-3">
                <CrmAddCard label={tx("crm.addDealCard", "+ Ajouter une opportunite")} onClick={onAdd} compact />
              </div>
            ) : null}
            {emptyFiltered ? (
              <p className="py-8 text-center text-[12px] text-muted-foreground">
                {tx("crm.emptyFilter", "Aucun resultat pour ces filtres.")}
              </p>
            ) : (
              <div className="flex h-full min-h-[280px] min-w-min gap-3">
                {OPP_STAGES.map((stage) => {
                  const column = filtered.filter((row) => row.stage === stage);
                  const total = column.reduce((sum, row) => sum + Number(row.amount || 0), 0);
                  return (
                    <div
                      key={stage}
                      className="flex h-full max-h-full w-[220px] min-w-[220px] shrink-0 flex-col rounded-xl bg-muted/20 p-2"
                      onDragOver={(event) => {
                        if (!canWrite) return;
                        event.preventDefault();
                        event.dataTransfer.dropEffect = "move";
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        if (!canWrite) return;
                        const id = event.dataTransfer.getData("text/plain") || event.dataTransfer.getData("text/opp");
                        if (id) void onMove(id, stage);
                      }}
                    >
                      <p className="shrink-0 truncate px-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                        {tx(`crm.stages.${stage}`, stage)} · {fmt(total)}
                      </p>
                      <div className="mt-1.5 flex min-h-0 max-h-[min(32rem,calc(100vh-18rem))] flex-1 flex-col gap-1.5 overflow-y-auto">
                        {column.map((row) => (
                          <div
                            key={row.id}
                            draggable={canWrite}
                            onDragStart={(event) => {
                              event.dataTransfer.setData("text/plain", row.id);
                              event.dataTransfer.setData("text/opp", row.id);
                              event.dataTransfer.effectAllowed = "move";
                            }}
                            onClick={() => onOpen(row.id)}
                            className={cn(
                              "min-h-12 w-full shrink-0 cursor-grab rounded-lg border border-border/60 bg-background px-2 py-1.5 text-left text-[12px] hover:bg-muted/40 active:cursor-grabbing",
                              openId === row.id && "border-violet-400/70 bg-muted/40",
                            )}
                          >
                            <div className="font-medium">{opportunityName(row) || displayText(row.id)}</div>
                            <div className="tabular-nums text-muted-foreground">
                              {fmt(Number(row.amount || 0), row.currency)}
                            </div>
                          </div>
                        ))}
                        <div className="min-h-16 flex-1 rounded-md border border-dashed border-border/50" />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        }
        fiche={fiche}
      />
    );
  }

  return (
    <CrmMasterDetail
      before={before}
      toolbar={toolbar}
      list={
        <>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {canWrite ? (
              <div className="pb-3">
                <CrmAddCard label={tx("crm.addDealCard", "+ Ajouter une opportunite")} onClick={onAdd} compact />
              </div>
            ) : null}
            <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60">
              {rows.length === 0 ? (
                <li className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyOpportunities", "Cree une opportunite, ou convertis un lead.")}
                </li>
              ) : emptyFiltered ? (
                <li className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyFilter", "Aucun resultat pour ces filtres.")}
                </li>
              ) : (
                slice.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => onOpen(row.id)}
                      className={cn(
                        "flex min-h-10 w-full cursor-pointer items-center justify-between px-3 py-2 text-left text-[13px] hover:bg-muted/40",
                        openId === row.id && "bg-muted/50",
                      )}
                    >
                      <span>{opportunityName(row) || displayText(row.id)}</span>
                      <span className="tabular-nums text-muted-foreground">
                        {fmt(Number(row.amount || 0), row.currency)} ·{" "}
                        {tx(`crm.stages.${String(row.stage)}`, String(row.stage))}
                      </span>
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
