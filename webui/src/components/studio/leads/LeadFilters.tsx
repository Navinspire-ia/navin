// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from "react";
import { DefaultButton, Dropdown, TextField, type IDropdownOption, type IDropdownStyles } from "@fluentui/react";

import { FacetSearchChip } from "@/components/studio/FacetSearchChip";
import {
  NONE_FACET,
  SCORE_FLOORS,
  emptyLeadFilter,
  leadFacets,
  leadFilterActive,
  type LeadEmailState,
  type LeadFacetFilter,
} from "@/components/studio/leads/lead-filters";
import { BUTTON_STYLES, SURFACE, type Tx } from "@/components/studio/leads/leads-ui";
import { sectorLabel } from "@/components/studio/leads/sector-label";
import { countryDropdownOptions } from "@/lib/country-options";
import { leadSourceLabel, type LeadRow } from "@/lib/leads-api";
import { cn } from "@/lib/utils";

/** Board-style quick filter pill, same silhouette as the searchable chips. */
const CHIP: Partial<IDropdownStyles> = {
  root: { minWidth: 118 },
  dropdown: { minWidth: 118 },
  title: {
    borderRadius: 999,
    height: 34,
    lineHeight: "32px",
    paddingLeft: 14,
    paddingRight: 32,
    fontSize: 13,
    fontWeight: 500,
  },
  caretDownWrapper: { right: 10, lineHeight: "32px", height: 34 },
};
const CHIP_ACTIVE: Partial<IDropdownStyles> = {
  ...CHIP,
  title: {
    borderRadius: 999,
    height: 34,
    lineHeight: "32px",
    paddingLeft: 14,
    paddingRight: 32,
    fontSize: 13,
    fontWeight: 600,
    borderColor: "rgb(79 70 229)",
    color: "rgb(67 56 202)",
  },
};
const CHIP_CALLOUT = { calloutMaxHeight: 320, preventDismissOnScroll: true, preventDismissOnResize: true };

function sentence(text: string): string {
  const raw = text.trim();
  if (!raw) return raw;
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

export function LeadFilters({
  rows,
  filter,
  query,
  tx,
  locale = "en",
  onChange,
  onQuery,
  leading,
  extra,
}: {
  /** Rows of the current bucket: the chips only offer values that exist there. */
  rows: LeadRow[];
  filter: LeadFacetFilter;
  query: string;
  tx: Tx;
  locale?: string;
  onChange: (next: LeadFacetFilter) => void;
  onQuery: (value: string) => void;
  /** Bucket tabs, printed on the same line as the search box. */
  leading?: ReactNode;
  extra?: ReactNode;
}) {
  const facets = leadFacets(rows);
  const none = tx("filterNoneValue", "Not set");
  const active = leadFilterActive(filter);
  const chip = (on: boolean) => (on ? CHIP_ACTIVE : CHIP);
  const searchCopy = {
    searchPlaceholder: tx("facetSearch", "Search..."),
    empty: tx("facetSearchEmpty", "No match."),
    clear: tx("clearFilters", "Clear filters"),
  };
  const countryOptions = countryDropdownOptions(facets.countries, locale, {
    noneKey: NONE_FACET,
    none,
    inResults: tx("countriesInResults", "In the results"),
    all: tx("allCountries", "All countries"),
  });
  const sourceOptions: IDropdownOption[] = facets.sources.map((key) => ({
    key,
    text: key === NONE_FACET ? none : leadSourceLabel(key),
  }));
  const sectorOptions: IDropdownOption[] = facets.sectors
    .map((key) => ({ key, text: key === NONE_FACET ? none : sectorLabel(key, locale) }))
    .sort((left, right) => {
      if (left.key === NONE_FACET) return 1;
      if (right.key === NONE_FACET) return -1;
      return String(left.text).localeCompare(String(right.text), locale);
    });
  const stageOptions: IDropdownOption[] = facets.stages.map((key) => ({
    key,
    text: tx(`stage.${key}`, sentence(key)),
  }));
  const scoreOptions: IDropdownOption[] = [
    { key: "", text: tx("filterAny", "All") },
    ...SCORE_FLOORS.map((floor) => ({ key: floor, text: tx("scoreFloor", "{{n}}+", { n: Number(floor) }) })),
  ];
  if (filter.minScore && !scoreOptions.some((option) => option.key === filter.minScore)) {
    scoreOptions.push({ key: filter.minScore, text: tx("scoreFloor", "{{n}}+", { n: Number(filter.minScore) }) });
  }
  const emailOptions: IDropdownOption[] = [
    { key: "any", text: tx("filterAny", "All") },
    { key: "has", text: tx("emailHas", "Has an email") },
    { key: "verified", text: tx("emailVerified", "Verified email") },
    { key: "none", text: tx("emailNone", "No email yet") },
  ];
  return (
    <div className={cn(SURFACE, "min-w-0 space-y-3 p-3 sm:p-4")} data-testid="lead-filters">
      <div
        className="flex flex-nowrap items-center gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        data-testid="lead-filters-toolbar"
      >
        {leading}
        <div className="min-w-[11rem] flex-1">
          <TextField
            ariaLabel={tx("searchLeads", "Search a lead")}
            value={query}
            placeholder={tx("searchLeadsHint", "Company, contact, email, sector...")}
            iconProps={{ iconName: "Search" }}
            onChange={(_, value) => onQuery(value || "")}
            data-testid="lead-search"
          />
        </div>
        <DefaultButton
          text={tx("clearFilters", "Clear")}
          title={tx("clearFiltersTitle", "Clear filters")}
          ariaLabel={tx("clearFiltersTitle", "Clear filters")}
          disabled={!active && !query}
          onClick={() => {
            onChange(emptyLeadFilter());
            onQuery("");
          }}
          styles={BUTTON_STYLES}
          data-testid="lead-clear-filters"
        />
        {extra}
      </div>
      <div
        className="flex flex-wrap items-center gap-2"
        role="group"
        aria-label={tx("quickFiltersAria", "Quick filters")}
        data-testid="lead-quick-filters"
      >
        <FacetSearchChip
          label={tx("filterCountry", "Country")}
          options={countryOptions}
          selected={filter.countries}
          onChange={(countries) => onChange({ ...filter, countries })}
          copy={searchCopy}
          testId="lead-chip-country"
        />
        <FacetSearchChip
          label={tx("filterSector", "Sector")}
          options={sectorOptions}
          selected={filter.sectors}
          onChange={(sectors) => onChange({ ...filter, sectors })}
          copy={searchCopy}
          width="lg"
          testId="lead-chip-sector"
        />
        <FacetSearchChip
          label={tx("filterSource", "Source")}
          options={sourceOptions}
          selected={filter.sources}
          onChange={(sources) => onChange({ ...filter, sources })}
          copy={searchCopy}
          testId="lead-chip-source"
        />
        <FacetSearchChip
          label={tx("filterStage", "Stage")}
          options={stageOptions}
          selected={filter.stages}
          onChange={(stages) => onChange({ ...filter, stages })}
          copy={searchCopy}
          testId="lead-chip-stage"
        />
        <Dropdown
          placeholder={tx("filterScore", "Score")}
          ariaLabel={tx("filterScore", "Score")}
          selectedKey={filter.minScore || null}
          options={scoreOptions}
          onChange={(_, option) => onChange({ ...filter, minScore: String(option?.key ?? "") })}
          styles={chip(Boolean(filter.minScore))}
          calloutProps={CHIP_CALLOUT}
          data-testid="lead-chip-score"
        />
        <Dropdown
          placeholder={tx("filterEmail", "Email")}
          ariaLabel={tx("filterEmail", "Email")}
          selectedKey={filter.email === "any" ? null : filter.email}
          options={emailOptions}
          onChange={(_, option) => onChange({ ...filter, email: String(option?.key || "any") as LeadEmailState })}
          styles={chip(filter.email !== "any")}
          calloutProps={CHIP_CALLOUT}
          data-testid="lead-chip-email"
        />
      </div>
    </div>
  );
}
