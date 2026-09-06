import type { ReactNode } from "react";
import { Search } from "lucide-react";

import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { Input } from "@/components/ui/input";
import type { SearchableOption } from "@/lib/crm-catalog";
import { cn } from "@/lib/utils";
import type { CrmFacetFilters } from "@/store/crm-ui";

export type CrmToolbarFacet = {
  key: keyof CrmFacetFilters;
  label: string;
  options: SearchableOption[];
};

type Props = {
  query: string;
  onQueryChange: (value: string) => void;
  facets?: CrmToolbarFacet[];
  facetValues: CrmFacetFilters;
  onFacetChange: <K extends keyof CrmFacetFilters>(key: K, value: CrmFacetFilters[K]) => void;
  countLabel: string;
  tx: (key: string, fallback: string) => string;
  searchPlaceholder?: string;
  showDateRange?: boolean;
  actions?: ReactNode;
  className?: string;
};

export function CrmListToolbar({
  query,
  onQueryChange,
  facets = [],
  facetValues,
  onFacetChange,
  countLabel,
  tx,
  searchPlaceholder,
  showDateRange,
  actions,
  className,
}: Props) {
  return (
    <div className={cn("flex shrink-0 flex-wrap items-center gap-2 pb-3", className)}>
      <label className="relative min-w-[200px] flex-1">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={searchPlaceholder || tx("crm.searchList", "Nom, email, telephone, entreprise...")}
          className="h-8 pl-8 text-[13px]"
          aria-label={tx("crm.searchList", "Nom, email, telephone, entreprise...")}
        />
      </label>
      {facets.map((facet) => (
        <div key={facet.key} className="w-36 shrink-0">
          <SearchableSelect
            value={facetValues[facet.key]}
            onChange={(value) => onFacetChange(facet.key, value)}
            options={facet.options}
            allowEmpty
            emptyLabelOption={tx("crm.filterAll", "Tous")}
            placeholder={facet.label}
            searchPlaceholder={tx("crm.searchPlaceholder", "Rechercher...")}
            emptyLabel={tx("crm.noResults", "Aucun resultat")}
            className="h-8 text-[12px]"
          />
        </div>
      ))}
      {showDateRange ? (
        <>
          <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>{tx("crm.dateFrom", "Du")}</span>
            <Input
              type="date"
              value={facetValues.dateFrom}
              onChange={(event) => onFacetChange("dateFrom", event.target.value)}
              className="h-8 w-36 text-[12px]"
            />
          </label>
          <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>{tx("crm.dateTo", "Au")}</span>
            <Input
              type="date"
              value={facetValues.dateTo}
              onChange={(event) => onFacetChange("dateTo", event.target.value)}
              className="h-8 w-36 text-[12px]"
            />
          </label>
        </>
      ) : null}
      <p className="ml-auto shrink-0 whitespace-nowrap text-[12px] tabular-nums text-muted-foreground">
        {countLabel}
      </p>
      {actions}
    </div>
  );
}
