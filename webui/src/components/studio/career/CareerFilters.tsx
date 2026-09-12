// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, type ReactNode } from "react";
import {
  DefaultButton,
  Dropdown,
  IconButton,
  Panel,
  PanelType,
  Pivot,
  PivotItem,
  TextField,
  type IDropdownOption,
  type IDropdownStyles,
} from "@fluentui/react";

import "@/lib/fluent-icons";
import { BUTTON_STYLES, ICON_BUTTON_STYLES, SURFACE } from "@/components/studio/career/career-ui";
import { contractLabel, experienceLabel } from "@/lib/career-facts";
import {
  NONE_FACET,
  POSTED_WITHIN_DAYS,
  emptyOfferFilter,
  offerFacets,
  offerFilterActive,
  offerFilterCount,
  type OfferFacetFilter,
} from "@/lib/career-filters";
import type { CareerOpportunity } from "@/lib/career-api";
import { countryDropdownOptions } from "@/lib/country-options";
import { FacetSearchChip } from "@/components/studio/FacetSearchChip";
import { cn } from "@/lib/utils";

const DROPDOWN = { dropdown: { width: "100%" } };
const FILTER_CALLOUT = {
  preventDismissOnScroll: true,
  preventDismissOnResize: true,
  isBeakVisible: false,
};
const FIELD_GRID = "grid gap-4 sm:grid-cols-2";
/** Board-style quick filter: a pill that opens the same facet as the side panel. */
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
    color: "rgb(79 70 229)",
  },
};

/** Mission length buckets the way the boards print them. */
export const DURATION_CHOICES = [
  { key: "lte3", min: "", max: "3" },
  { key: "3to6", min: "3", max: "6" },
  { key: "6to12", min: "6", max: "12" },
  { key: "gte12", min: "12", max: "" },
] as const;
export const DAY_RATE_FLOORS = ["300", "400", "500", "600", "700", "800"] as const;
export const SALARY_FLOORS = ["30000", "40000", "50000", "60000", "80000", "100000"] as const;

export function durationChoice(filter: OfferFacetFilter): string {
  const min = filter.minDuration || "";
  const max = filter.maxDuration || "";
  if (!min && !max) return "";
  const hit = DURATION_CHOICES.find((choice) => choice.min === min && choice.max === max);
  return hit ? hit.key : "custom";
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

type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

export function CareerFilters({
  rows,
  hints,
  filter,
  tx,
  onChange,
  leading,
  trailing,
  track = "freelance",
  locale = "en",
}: {
  rows: CareerOpportunity[];
  hints?: string[];
  filter: OfferFacetFilter;
  tx: Tx;
  onChange: (next: OfferFacetFilter) => void;
  /** Match buckets and list tabs, printed on the same line as the search box. */
  leading?: ReactNode;
  trailing?: ReactNode;
  track?: string;
  locale?: string;
}) {
  const [open, setOpen] = useState(false);
  const facets = offerFacets(rows, hints);
  const none = tx("filterNoneValue", "Not set");
  const active = offerFilterActive(filter);
  const count = offerFilterCount(filter);
  const contractText = (key: string) => tx(`contract_${key.replace("-", "_")}`, contractLabel(key));
  const experienceText = (key: string) => tx(`experience_${key}`, experienceLabel(key));
  const remoteText = (key: string) =>
    key === "remote"
      ? tx("workRemote", "Full remote")
      : key === "hybrid"
        ? tx("workHybrid", "Hybrid")
        : key === "onsite"
          ? tx("workOnsite", "On site")
          : key;
  const postedWithinOptions: IDropdownOption[] = [
    { key: "", text: tx("filterAny", "All") },
    ...POSTED_WITHIN_DAYS.map((days) => ({
      key: days,
      text:
        days === "1"
          ? tx("postedWithin1", "Last 24 hours")
          : tx("postedWithinDays", "Last {{count}} days", { count: Number(days) }),
    })),
  ];
  const durationOptions: IDropdownOption[] = [
    { key: "", text: tx("filterAny", "All") },
    { key: "lte3", text: tx("durationUpTo3", "Up to 3 months") },
    { key: "3to6", text: tx("duration3to6", "3 to 6 months") },
    { key: "6to12", text: tx("duration6to12", "6 to 12 months") },
    { key: "gte12", text: tx("duration12plus", "1 year and more") },
  ];
  const durationKey = durationChoice(filter);
  if (durationKey === "custom") durationOptions.push({ key: "custom", text: tx("filterCustom", "Custom") });
  const freelance = track === "freelance";
  const payFloors: IDropdownOption[] = [
    { key: "", text: tx("filterAny", "All") },
    ...(freelance
      ? DAY_RATE_FLOORS.map((amount) => ({ key: amount, text: tx("payFloorDay", "{{amount}}+ per day", { amount }) }))
      : SALARY_FLOORS.map((amount) => ({
          key: amount,
          text: tx("payFloorYear", "{{amount}}k+ per year", { amount: Number(amount) / 1000 }),
        }))),
  ];
  const payKey = freelance ? filter.minDayRate || "" : filter.minSalary || "";
  if (payKey && !payFloors.some((option) => option.key === payKey)) {
    payFloors.push({ key: payKey, text: freelance ? `${payKey}+` : `${Number(payKey) / 1000}k+` });
  }
  const chip = (on: boolean) => (on ? CHIP_ACTIVE : CHIP);
  const searchCopy = {
    searchPlaceholder: tx("facetSearch", "Search..."),
    empty: tx("facetSearchEmpty", "No match."),
    clear: tx("clearFilters", "Clear filters"),
  };
  // Every country in the world, full names, the ones present in the list first.
  const countryOptions = countryDropdownOptions(facets.countries, locale, {
    noneKey: NONE_FACET,
    none,
    inResults: tx("countriesInResults", "In the results"),
    all: tx("allCountries", "All countries"),
  });
  return (
    <div className={cn(SURFACE, "min-w-0 space-y-3 p-3 sm:p-4")} data-testid="career-filters">
      <div className="flex flex-nowrap items-center gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" data-testid="career-filters-toolbar">
        {leading}
        <div className="min-w-[11rem] flex-1">
          <TextField
            ariaLabel={tx("searchOffers", "Search the list")}
            value={filter.query}
            placeholder={tx("searchOffersHint", "Title, company, stack, country...")}
            iconProps={{ iconName: "Search" }}
            onChange={(_, value) => onChange({ ...filter, query: value || "" })}
          />
        </div>
        <IconButton
          title={
            open
              ? tx("hideFilters", "Hide filters")
              : active
                ? tx("showFiltersOn", "Show filters · {{count}}", { count })
                : tx("showFilters", "Show filters")
          }
          ariaLabel={
            open
              ? tx("hideFilters", "Hide filters")
              : active
                ? tx("showFiltersOn", "Show filters · {{count}}", { count })
                : tx("showFilters", "Show filters")
          }
          iconProps={{ iconName: open ? "ChevronUp" : active ? "FilterSolid" : "Filter" }}
          onClick={() => setOpen((value) => !value)}
          checked={open || active}
          styles={ICON_BUTTON_STYLES}
          data-testid="career-toggle-filters"
        />
        <IconButton
          title={tx("clearFilters", "Clear filters")}
          ariaLabel={tx("clearFilters", "Clear filters")}
          iconProps={{ iconName: "ClearFilter" }}
          disabled={!active}
          onClick={() => onChange(emptyOfferFilter())}
          styles={ICON_BUTTON_STYLES}
          data-testid="career-clear-filters"
        />
        {trailing}
      </div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={tx("quickFiltersAria", "Quick filters")} data-testid="career-quick-filters">
        <FacetSearchChip
          label={tx("filterCountry", "Country")}
          options={countryOptions}
          selected={filter.countries}
          onChange={(countries) => onChange({ ...filter, countries })}
          copy={searchCopy}
          testId="career-chip-country"
        />
        <Dropdown
          multiSelect
          placeholder={tx("filterContract", "Contract")}
          ariaLabel={tx("filterContract", "Contract")}
          selectedKeys={filter.contracts || []}
          options={facets.contracts.map((key) => ({ key, text: contractText(key) }))}
          onChange={(_, option) => onChange({ ...filter, contracts: toggleKeys(filter.contracts || [], option) })}
          styles={chip((filter.contracts || []).length > 0)}
          calloutProps={FILTER_CALLOUT}
          data-testid="career-chip-contract"
        />
        <Dropdown
          placeholder={tx("filterDuration", "Duration")}
          ariaLabel={tx("filterDuration", "Duration")}
          selectedKey={durationKey || null}
          options={durationOptions}
          onChange={(_, option) => {
            const key = String(option?.key ?? "");
            const choice = DURATION_CHOICES.find((item) => item.key === key);
            onChange({ ...filter, minDuration: choice?.min ?? "", maxDuration: choice?.max ?? "" });
          }}
          styles={chip(durationKey !== "")}
          calloutProps={FILTER_CALLOUT}
          data-testid="career-chip-duration"
        />
        <Dropdown
          placeholder={tx("filterPay", "Pay")}
          ariaLabel={tx("filterPay", "Pay")}
          selectedKey={payKey || null}
          options={payFloors}
          onChange={(_, option) => {
            const key = String(option?.key ?? "");
            onChange(freelance ? { ...filter, minDayRate: key } : { ...filter, minSalary: key });
          }}
          styles={chip(payKey !== "")}
          calloutProps={FILTER_CALLOUT}
          data-testid="career-chip-pay"
        />
        <Dropdown
          multiSelect
          placeholder={tx("filterTelework", "Remote")}
          ariaLabel={tx("filterTelework", "Remote")}
          selectedKeys={filter.remotes}
          options={facets.remotes.map((key) => ({ key, text: remoteText(key) }))}
          onChange={(_, option) => onChange({ ...filter, remotes: toggleKeys(filter.remotes, option) })}
          styles={chip(filter.remotes.length > 0)}
          calloutProps={FILTER_CALLOUT}
          data-testid="career-chip-remote"
        />
        <Dropdown
          multiSelect
          placeholder={tx("filterExperience", "Experience")}
          ariaLabel={tx("filterExperience", "Experience")}
          selectedKeys={filter.experiences || []}
          options={facets.experiences.map((key) => ({ key, text: experienceText(key) }))}
          onChange={(_, option) => onChange({ ...filter, experiences: toggleKeys(filter.experiences || [], option) })}
          styles={chip((filter.experiences || []).length > 0)}
          calloutProps={FILTER_CALLOUT}
          data-testid="career-chip-experience"
        />
        <Dropdown
          placeholder={tx("filterPostedWithin", "Posted")}
          ariaLabel={tx("filterPostedWithin", "Posted")}
          selectedKey={filter.postedWithin || null}
          options={postedWithinOptions}
          onChange={(_, option) =>
            onChange({ ...filter, postedWithin: String(option?.key ?? "") as OfferFacetFilter["postedWithin"] })
          }
          styles={chip(Boolean(filter.postedWithin))}
          calloutProps={FILTER_CALLOUT}
          data-testid="career-chip-posted"
        />
      </div>
      {open ? (
        <Panel
          isOpen
          isLightDismiss
          type={PanelType.medium}
          headerText={tx("resultFilters", "Result filters")}
          closeButtonAriaLabel={tx("hideFilters", "Hide filters")}
          onDismiss={() => setOpen(false)}
          onRenderFooterContent={() => (
            <DefaultButton
              text={tx("hideFilters", "Hide filters")}
              iconProps={{ iconName: "Cancel" }}
              onClick={() => setOpen(false)}
              styles={BUTTON_STYLES}
            />
          )}
          isFooterAtBottom
        >
          <Pivot aria-label={tx("filterGroupsAria", "Filter groups")}>
            <PivotItem headerText={tx("filterGroupPlace", "Place")} itemKey="place">
              <div className={cn(FIELD_GRID, "pt-4")}>
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
                  selectedKeys={filter.domains}
                  options={facets.domains.map((key) => ({ key, text: optionLabel(key, none) }))}
                  onChange={(_, option) => onChange({ ...filter, domains: toggleKeys(filter.domains, option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                />
                <Dropdown
                  label={tx("filterCompany", "Company")}
                  multiSelect
                  selectedKeys={filter.companies}
                  options={facets.companies.map((key) => ({ key, text: optionLabel(key, none) }))}
                  onChange={(_, option) => onChange({ ...filter, companies: toggleKeys(filter.companies, option) })}
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
              </div>
            </PivotItem>
            <PivotItem headerText={tx("filterGroupRole", "Role")} itemKey="role">
              <div className={cn(FIELD_GRID, "pt-4")}>
                <Dropdown
                  label={tx("filterTrack", "Track")}
                  multiSelect
                  selectedKeys={filter.tracks}
                  options={facets.tracks.map((key) => ({ key, text: key }))}
                  onChange={(_, option) => onChange({ ...filter, tracks: toggleKeys(filter.tracks, option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                />
                <Dropdown
                  label={tx("filterRemote", "Work mode")}
                  multiSelect
                  selectedKeys={filter.remotes}
                  options={facets.remotes.map((key) => ({ key, text: remoteText(key) }))}
                  onChange={(_, option) => onChange({ ...filter, remotes: toggleKeys(filter.remotes, option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                />
                <Dropdown
                  label={tx("filterContract", "Contract")}
                  multiSelect
                  selectedKeys={filter.contracts || []}
                  options={facets.contracts.map((key) => ({ key, text: contractText(key) }))}
                  onChange={(_, option) => onChange({ ...filter, contracts: toggleKeys(filter.contracts || [], option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                  data-testid="career-filter-contract"
                />
                <Dropdown
                  label={tx("filterExperience", "Experience")}
                  multiSelect
                  selectedKeys={filter.experiences || []}
                  options={facets.experiences.map((key) => ({ key, text: experienceText(key) }))}
                  onChange={(_, option) => onChange({ ...filter, experiences: toggleKeys(filter.experiences || [], option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                  data-testid="career-filter-experience"
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
                  label={tx("filterStack", "Stack")}
                  multiSelect
                  selectedKeys={filter.stacks}
                  options={facets.stacks.map((key) => ({ key, text: key }))}
                  onChange={(_, option) => onChange({ ...filter, stacks: toggleKeys(filter.stacks, option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                />
                <Dropdown
                  label={tx("filterLanguage", "Language")}
                  multiSelect
                  selectedKeys={filter.languages}
                  options={facets.languages.map((key) => ({ key, text: key }))}
                  onChange={(_, option) => onChange({ ...filter, languages: toggleKeys(filter.languages, option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                />
              </div>
            </PivotItem>
            <PivotItem headerText={tx("filterGroupDates", "Dates")} itemKey="dates">
              <div className={cn(FIELD_GRID, "pt-4")}>
                <Dropdown
                  label={tx("filterPostedWithin", "Posted")}
                  selectedKey={filter.postedWithin || ""}
                  options={postedWithinOptions}
                  onChange={(_, option) =>
                    onChange({ ...filter, postedWithin: String(option?.key ?? "") as OfferFacetFilter["postedWithin"] })
                  }
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
                  data-testid="career-filter-posted-within"
                />
                <TextField
                  label={tx("filterMinDuration", "Min duration (months)")}
                  value={filter.minDuration || ""}
                  onChange={(_, value) => onChange({ ...filter, minDuration: value || "" })}
                />
                <TextField
                  label={tx("filterMaxDuration", "Max duration (months)")}
                  value={filter.maxDuration || ""}
                  onChange={(_, value) => onChange({ ...filter, maxDuration: value || "" })}
                />
                <TextField
                  type="date"
                  label={tx("filterPostedFrom", "Posted from")}
                  value={filter.postedFrom}
                  onChange={(_, value) => onChange({ ...filter, postedFrom: value || "" })}
                />
                <TextField
                  type="date"
                  label={tx("filterPostedTo", "Posted to")}
                  value={filter.postedTo}
                  onChange={(_, value) => onChange({ ...filter, postedTo: value || "" })}
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
              </div>
            </PivotItem>
            <PivotItem headerText={tx("filterGroupRange", "Score and pay")} itemKey="range">
              <div className={cn(FIELD_GRID, "pt-4")}>
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
                  label={tx("filterBucket", "Match")}
                  multiSelect
                  selectedKeys={filter.buckets}
                  options={facets.buckets.map((key) => ({ key, text: key }))}
                  onChange={(_, option) => onChange({ ...filter, buckets: toggleKeys(filter.buckets, option) })}
                  placeholder={tx("filterAny", "All")}
                  styles={DROPDOWN}
                  calloutProps={FILTER_CALLOUT}
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
                  label={tx("filterMinPay", "Min pay")}
                  value={filter.minPay}
                  onChange={(_, value) => onChange({ ...filter, minPay: value || "" })}
                />
                <TextField
                  label={tx("filterMaxPay", "Max pay")}
                  value={filter.maxPay}
                  onChange={(_, value) => onChange({ ...filter, maxPay: value || "" })}
                />
                <TextField
                  label={tx("filterMinDayRate", "Min day rate (TJM)")}
                  value={filter.minDayRate || ""}
                  onChange={(_, value) => onChange({ ...filter, minDayRate: value || "" })}
                  data-testid="career-filter-min-day-rate"
                />
                <TextField
                  label={tx("filterMinSalary", "Min yearly salary")}
                  value={filter.minSalary || ""}
                  onChange={(_, value) => onChange({ ...filter, minSalary: value || "" })}
                  data-testid="career-filter-min-salary"
                />
              </div>
            </PivotItem>
          </Pivot>
        </Panel>
      ) : null}
    </div>
  );
}
