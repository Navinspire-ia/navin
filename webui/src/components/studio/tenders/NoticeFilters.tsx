// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, type ReactNode } from "react";
import { DefaultButton, Dropdown, TextField, type IDropdownOption, type IDropdownStyles } from "@fluentui/react";

import {
  NONE_FACET,
  emptyFacetFilter,
  facetFilterActive,
  facetFilterCount,
  noticeFacets,
  type NoticeDeadlineState,
  type NoticeFacetFilter,
  type NoticeGoFilter,
} from "@/components/studio/tenders/notice-filters";
import { BUTTON_STYLES, FILTER_CALLOUT, Surface, type Tx } from "@/components/studio/tenders/tenders-ui";
import { domainLabel } from "@/components/studio/tenders/domain-label";
import { countryDropdownOptions } from "@/lib/country-options";
import { FacetSearchChip } from "@/components/studio/FacetSearchChip";
import type { TenderNotice } from "@/lib/tenders-api";

const DROPDOWN = { dropdown: { width: "100%" } };
const FIELD_GRID = "grid gap-4 sm:grid-cols-2 lg:grid-cols-4";
/** Board-style quick filter: a pill that opens the same facet as the field grid. */
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
    borderColor: "rgb(4 120 87)",
    color: "rgb(4 120 87)",
  },
};

/** Budget floors the way buyers print them. */
export const BUDGET_FLOORS = ["10000", "50000", "100000", "250000", "500000", "1000000"] as const;
/** Publication windows for the Published chip, in days. */
export const PUBLISHED_WITHIN_DAYS = ["1", "7", "14", "30"] as const;

export function publishedFloor(days: string, now: Date = new Date()): string {
  const n = Number(days);
  if (!Number.isFinite(n) || n <= 0) return "";
  const date = new Date(now.getTime() - (n - 1) * 86_400_000);
  return date.toISOString().slice(0, 10);
}

/** Which Published chip matches the stored publishedFrom, "" when none, "custom" when set by hand. */
export function publishedChoice(filter: NoticeFacetFilter, now: Date = new Date()): string {
  if (!filter.publishedFrom && !filter.publishedTo) return "";
  if (filter.publishedTo) return "custom";
  const hit = PUBLISHED_WITHIN_DAYS.find((days) => publishedFloor(days, now) === filter.publishedFrom);
  return hit || "custom";
}

function budgetText(amount: string): string {
  const n = Number(amount);
  if (!Number.isFinite(n)) return amount;
  if (n >= 1_000_000) return `${n / 1_000_000}M`;
  if (n >= 1_000) return `${n / 1_000}k`;
  return String(n);
}

function toggleKeys(selected: string[], option?: IDropdownOption): string[] {
  if (!option) return selected;
  const key = String(option.key);
  if (option.selected) return selected.includes(key) ? selected : [...selected, key];
  return selected.filter((item) => item !== key);
}

function optionLabel(key: string, noneLabel: string): string {
  return key === NONE_FACET ? noneLabel : key;
}

export function NoticeFilters({
  rows,
  crafts,
  filter,
  query,
  tx,
  locale = "fr",
  onChange,
  onQuery,
  leading,
  extra,
}: {
  rows: TenderNotice[];
  crafts: string[];
  filter: NoticeFacetFilter;
  query: string;
  tx: Tx;
  locale?: string;
  onChange: (next: NoticeFacetFilter) => void;
  onQuery: (value: string) => void;
  /** Status chips and list tabs, printed on the same line as the search box. */
  leading?: ReactNode;
  extra?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const facets = noticeFacets(rows, crafts);
  const none = tx("filterNoneValue", "Not set");
  const active = facetFilterActive(filter);
  const count = facetFilterCount(filter);
  const chip = (on: boolean) => (on ? CHIP_ACTIVE : CHIP);
  const searchCopy = {
    searchPlaceholder: tx("facetSearch", "Search..."),
    empty: tx("facetSearchEmpty", "No match."),
    clear: tx("clearFilters", "Clear filters"),
  };
  const buyerOptions: IDropdownOption[] = facets.buyers.map((key) => ({ key, text: optionLabel(key, none) }));
  // CPV codes, TED form types and shouting categories read as words.
  const domainOptions: IDropdownOption[] = facets.sectors
    .map((key) => ({ key, text: key === NONE_FACET ? none : domainLabel(key, locale) }))
    .sort((left, right) => {
      if (left.key === NONE_FACET) return 1;
      if (right.key === NONE_FACET) return -1;
      return String(left.text).localeCompare(String(right.text), locale);
    });
  // Every country in the world, full names, the ones present in the list first.
  const countryOptions = countryDropdownOptions(facets.countries, locale, {
    noneKey: NONE_FACET,
    none,
    inResults: tx("countriesInResults", "In the results"),
    all: tx("allCountries", "All countries"),
  });
  const goOptions: IDropdownOption[] = [
    { key: "any", text: tx("filterAny", "All") },
    { key: "go", text: tx("filterGo", "GO") },
    { key: "nogo", text: tx("filterNogo", "No-go") },
    { key: "unscored", text: tx("filterUnscored", "Not scored") },
  ];
  const deadlineOptions: IDropdownOption[] = [
    { key: "any", text: tx("filterAny", "All") },
    { key: "has", text: tx("filterHasDeadline", "Has a deadline") },
    { key: "none", text: tx("filterNoDeadline", "No deadline") },
  ];
  const budgetOptions: IDropdownOption[] = [
    { key: "", text: tx("filterAny", "All") },
    ...BUDGET_FLOORS.map((amount) => ({
      key: amount,
      text: tx("budgetFloor", "{{amount}}+", { amount: budgetText(amount) }),
    })),
  ];
  const budgetKey = filter.minBudget || "";
  if (budgetKey && !budgetOptions.some((option) => option.key === budgetKey)) {
    budgetOptions.push({ key: budgetKey, text: tx("budgetFloor", "{{amount}}+", { amount: budgetText(budgetKey) }) });
  }
  const publishedOptions: IDropdownOption[] = [
    { key: "", text: tx("filterAny", "All") },
    ...PUBLISHED_WITHIN_DAYS.map((days) => ({
      key: days,
      text:
        days === "1"
          ? tx("publishedWithin1", "Last 24 hours")
          : tx("publishedWithinDays", "Last {{count}} days", { count: Number(days) }),
    })),
  ];
  const publishedKey = publishedChoice(filter);
  if (publishedKey === "custom") publishedOptions.push({ key: "custom", text: tx("filterCustom", "Custom") });
  return (
    <Surface className="min-w-0 space-y-3 p-3 sm:p-4" data-testid="notice-filters">
      <div
        className="flex flex-nowrap items-center gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        data-testid="notice-filters-toolbar"
      >
        {leading}
        <div className="min-w-[11rem] flex-1">
          <TextField
            ariaLabel={tx("searchNotices", "Search a notice")}
            value={query}
            placeholder={tx("searchNoticesHint", "Title, country, buyer...")}
            iconProps={{ iconName: "Search" }}
            onChange={(_, value) => onQuery(value || "")}
          />
        </div>
        <DefaultButton
          text={
            open
              ? tx("hideFilters", "Hide filters")
              : active
                ? tx("showFiltersOn", "Show filters · {{count}}", { count })
                : tx("showFilters", "Show filters")
          }
          iconProps={{ iconName: open ? "ChevronUp" : "Filter" }}
          onClick={() => setOpen((value) => !value)}
          styles={BUTTON_STYLES}
          data-testid="notice-toggle-filters"
        />
        <DefaultButton
          text={tx("clearFilters", "Clear filters")}
          disabled={!active}
          onClick={() => onChange(emptyFacetFilter())}
          styles={BUTTON_STYLES}
        />
        {extra}
      </div>
      <div
        className="flex flex-wrap items-center gap-2"
        role="group"
        aria-label={tx("quickFiltersAria", "Quick filters")}
        data-testid="notice-quick-filters"
      >
        <FacetSearchChip
          label={tx("filterCountry", "Country")}
          options={countryOptions}
          selected={filter.countries}
          onChange={(countries) => onChange({ ...filter, countries })}
          copy={searchCopy}
          tone="emerald"
          testId="notice-chip-country"
        />
        <FacetSearchChip
          label={tx("filterDomain", "Domain")}
          options={domainOptions}
          selected={filter.sectors}
          onChange={(sectors) => onChange({ ...filter, sectors })}
          copy={searchCopy}
          tone="emerald"
          width="lg"
          testId="notice-chip-domain"
        />
        <FacetSearchChip
          label={tx("filterBuyer", "Buyer")}
          options={buyerOptions}
          selected={filter.buyers}
          onChange={(buyers) => onChange({ ...filter, buyers })}
          copy={searchCopy}
          tone="emerald"
          width="lg"
          testId="notice-chip-buyer"
        />
        <Dropdown
          placeholder={tx("filterBudget", "Budget")}
          ariaLabel={tx("filterBudget", "Budget")}
          selectedKey={budgetKey || null}
          options={budgetOptions}
          onChange={(_, option) => onChange({ ...filter, minBudget: String(option?.key ?? "") })}
          styles={chip(budgetKey !== "")}
          calloutProps={FILTER_CALLOUT}
          data-testid="notice-chip-budget"
        />
        <Dropdown
          placeholder={tx("filterDeadlineState", "Deadline")}
          ariaLabel={tx("filterDeadlineState", "Deadline")}
          selectedKey={filter.deadlineState || null}
          options={deadlineOptions}
          onChange={(_, option) =>
            onChange({
              ...filter,
              deadlineState: (option && option.key !== "any" ? String(option.key) : "") as NoticeDeadlineState,
            })
          }
          styles={chip(Boolean(filter.deadlineState))}
          calloutProps={FILTER_CALLOUT}
          data-testid="notice-chip-deadline"
        />
        <Dropdown
          placeholder={tx("filterPublished", "Published")}
          ariaLabel={tx("filterPublished", "Published")}
          selectedKey={publishedKey || null}
          options={publishedOptions}
          onChange={(_, option) => {
            const key = String(option?.key ?? "");
            if (key === "custom") return;
            onChange({ ...filter, publishedFrom: publishedFloor(key), publishedTo: "" });
          }}
          styles={chip(publishedKey !== "")}
          calloutProps={FILTER_CALLOUT}
          data-testid="notice-chip-published"
        />
        <Dropdown
          placeholder={tx("filterGoPick", "GO / NO-GO")}
          ariaLabel={tx("filterGoPick", "GO / NO-GO")}
          selectedKey={filter.go || null}
          options={goOptions}
          onChange={(_, option) =>
            onChange({
              ...filter,
              go: (option && option.key !== "any" ? String(option.key) : "") as NoticeGoFilter,
            })
          }
          styles={chip(Boolean(filter.go))}
          calloutProps={FILTER_CALLOUT}
          data-testid="notice-chip-go"
        />
      </div>
      {open ? (
        <div className={FIELD_GRID} data-testid="notice-filter-fields">
          <Dropdown
            label={tx("filterCountry", "Country")}
            multiSelect
            selectedKeys={filter.countries}
            options={countryOptions}
            onChange={(_, option) => onChange({ ...filter, countries: toggleKeys(filter.countries, option) })}
            placeholder={tx("filterAny", "All")}
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterDomain", "Domain")}
            multiSelect
            selectedKeys={filter.sectors}
            options={domainOptions}
            onChange={(_, option) => onChange({ ...filter, sectors: toggleKeys(filter.sectors, option) })}
            placeholder={tx("filterAny", "All")}
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterBuyer", "Buyer")}
            multiSelect
            selectedKeys={filter.buyers}
            options={buyerOptions}
            onChange={(_, option) => onChange({ ...filter, buyers: toggleKeys(filter.buyers, option) })}
            placeholder={tx("filterAny", "All")}
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterSource", "Source")}
            multiSelect
            selectedKeys={filter.sources}
            options={facets.sources.map((key) => ({ key, text: optionLabel(key, none) }))}
            onChange={(_, option) => onChange({ ...filter, sources: toggleKeys(filter.sources, option) })}
            placeholder={tx("filterAny", "All")}
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterStage", "Stage")}
            multiSelect
            selectedKeys={filter.stages}
            options={facets.stages.map((key) => ({ key, text: key }))}
            onChange={(_, option) => onChange({ ...filter, stages: toggleKeys(filter.stages, option) })}
            placeholder={tx("filterAny", "All")}
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterCurrency", "Currency")}
            multiSelect
            selectedKeys={filter.currencies}
            options={facets.currencies.map((key) => ({ key, text: key }))}
            onChange={(_, option) => onChange({ ...filter, currencies: toggleKeys(filter.currencies, option) })}
            placeholder={tx("filterAny", "All")}
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterGoPick", "GO / NO-GO")}
            selectedKey={filter.go || "any"}
            options={goOptions}
            onChange={(_, option) =>
              onChange({
                ...filter,
                go: (option && option.key !== "any" ? String(option.key) : "") as NoticeGoFilter,
              })
            }
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterDeadlineState", "Deadline")}
            selectedKey={filter.deadlineState || "any"}
            options={deadlineOptions}
            onChange={(_, option) =>
              onChange({
                ...filter,
                deadlineState: (option && option.key !== "any" ? String(option.key) : "") as NoticeDeadlineState,
              })
            }
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <TextField
            type="date"
            label={tx("filterDeadlineFrom", "Deadline from")}
            value={filter.deadlineFrom}
            onChange={(_, value) => onChange({ ...filter, deadlineFrom: value || "" })}
          />
          <TextField
            type="date"
            label={tx("filterDeadlineTo", "Deadline to")}
            value={filter.deadlineTo}
            onChange={(_, value) => onChange({ ...filter, deadlineTo: value || "" })}
          />
          <TextField
            type="date"
            label={tx("filterPublishedFrom", "Published from")}
            value={filter.publishedFrom}
            onChange={(_, value) => onChange({ ...filter, publishedFrom: value || "" })}
          />
          <TextField
            type="date"
            label={tx("filterPublishedTo", "Published to")}
            value={filter.publishedTo}
            onChange={(_, value) => onChange({ ...filter, publishedTo: value || "" })}
          />
          <TextField
            type="date"
            label={tx("filterArrivedFrom", "Arrived from")}
            value={filter.arrivedFrom}
            onChange={(_, value) => onChange({ ...filter, arrivedFrom: value || "" })}
          />
          <TextField
            type="date"
            label={tx("filterArrivedTo", "Arrived to")}
            value={filter.arrivedTo}
            onChange={(_, value) => onChange({ ...filter, arrivedTo: value || "" })}
          />
          <TextField
            label={tx("filterMinScore", "Min score")}
            value={filter.minScore}
            onChange={(_, value) => onChange({ ...filter, minScore: value || "" })}
          />
          <TextField
            label={tx("filterMaxScore", "Max score")}
            value={filter.maxScore}
            onChange={(_, value) => onChange({ ...filter, maxScore: value || "" })}
          />
          <TextField
            label={tx("filterMinBudget", "Min budget")}
            value={filter.minBudget}
            onChange={(_, value) => onChange({ ...filter, minBudget: value || "" })}
          />
          <TextField
            label={tx("filterMaxBudget", "Max budget")}
            value={filter.maxBudget}
            onChange={(_, value) => onChange({ ...filter, maxBudget: value || "" })}
          />
        </div>
      ) : null}
    </Surface>
  );
}
