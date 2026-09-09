// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { CrmAddCard } from "@/components/crm/CrmAddCard";
import { CrmFicheFact, CrmFichePane } from "@/components/crm/CrmFichePane";
import { CrmListToolbar } from "@/components/crm/CrmListToolbar";
import { CrmMasterDetail } from "@/components/crm/CrmMasterDetail";
import { CrmPagination } from "@/components/crm/CrmPagination";
import type { CrmRecord } from "@/lib/api";
import { optionList } from "@/lib/crm-catalog";
import { displayText, money } from "@/lib/crm-format";
import { countLabel, uniqueFieldValues, useCrmFilteredPage } from "@/lib/crm-list";
import { cn } from "@/lib/utils";

type Props = {
  products: CrmRecord[];
  openId: string | null;
  canWrite: boolean;
  currency: string;
  locale: string;
  tx: (key: string, fallback: string) => string;
  onAdd: () => void;
  onEdit: (row: CrmRecord) => void;
  onDelete: (row: CrmRecord) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
};

export function CrmProducts({
  products,
  openId,
  canWrite,
  currency,
  locale,
  tx,
  onAdd,
  onEdit,
  onDelete,
  onOpen,
  onClose,
}: Props) {
  const { filter, facets, setFilter, setFacet, filtered, slice, page, setPage, pageSize } =
    useCrmFilteredPage(products);
  const currencies = uniqueFieldValues(products, "currency");
  const emptyFiltered = products.length > 0 && filtered.length === 0;
  const open = products.find((row) => row.id === openId);

  return (
    <CrmMasterDetail
      toolbar={
        <CrmListToolbar
          query={filter}
          onQueryChange={setFilter}
          facetValues={facets}
          onFacetChange={setFacet}
          countLabel={countLabel(filtered.length, tx("crm.noun.product", "produit"), tx("crm.noun.products", "produits"))}
          tx={tx}
          searchPlaceholder={tx("crm.searchProduct", "Nom, devise...")}
          facets={
            currencies.length > 1
              ? [
                  {
                    key: "currency",
                    label: tx("crm.currency", "Devise"),
                    options: optionList(currencies, (value) => value),
                  },
                ]
              : []
          }
        />
      }
      list={
        <>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {products.length === 0 ? (
              <div className="space-y-3">
                {canWrite ? (
                  <CrmAddCard label={tx("crm.addProductCard", "+ Ajouter un produit")} onClick={onAdd} compact />
                ) : null}
                <p className="py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyProducts", "Ajoute un produit au catalogue.")}
                </p>
              </div>
            ) : emptyFiltered ? (
              <div className="space-y-3">
                {canWrite ? (
                  <CrmAddCard label={tx("crm.addProductCard", "+ Ajouter un produit")} onClick={onAdd} compact />
                ) : null}
                <p className="py-8 text-center text-[12px] text-muted-foreground">
                  {tx("crm.emptyFilter", "Aucun resultat pour ces filtres.")}
                </p>
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                {canWrite ? (
                  <CrmAddCard label={tx("crm.addProductCard", "+ Ajouter un produit")} onClick={onAdd} compact />
                ) : null}
                {slice.map((row) => (
                  <button
                    key={row.id}
                    type="button"
                    onClick={() => onOpen(row.id)}
                    className={cn(
                      "min-h-10 w-full cursor-pointer rounded-2xl border border-border/60 p-3 text-left hover:bg-muted/30",
                      openId === row.id && "border-violet-400/70 bg-muted/30",
                    )}
                  >
                    <div className="text-[14px] font-semibold">{displayText(row.name) || row.id}</div>
                    <p className="mt-1 text-[12px] tabular-nums text-muted-foreground">
                      {money(Number(row.defaultPrice || 0), String(row.currency || currency), locale)}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </div>
          <CrmPagination page={page} pageSize={pageSize} total={filtered.length} onPage={setPage} tx={tx} />
        </>
      }
      fiche={
        <CrmFichePane
          open={Boolean(open)}
          title={open ? displayText(open.name) || open.id : ""}
          emptyLabel={tx("crm.pickProduct", "Choisis un produit")}
          canWrite={canWrite}
          tx={tx}
          onClose={onClose}
          onEdit={open ? () => onEdit(open) : undefined}
          onDelete={open ? () => onDelete(open) : undefined}
        >
          {open ? (
            <dl className="grid gap-2 rounded-xl border border-border/60 p-3 text-[12px]">
              <CrmFicheFact
                label={tx("crm.defaultPrice", "Prix")}
                value={money(Number(open.defaultPrice || 0), String(open.currency || currency), locale)}
              />
              <CrmFicheFact label={tx("crm.currency", "Devise")} value={open.currency || currency} />
            </dl>
          ) : null}
        </CrmFichePane>
      }
    />
  );
}
