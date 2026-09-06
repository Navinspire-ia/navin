import { useState, type ReactNode } from "react";
import {
  DefaultButton,
  Dropdown,
  Panel,
  PanelType,
  Pivot,
  PivotItem,
  TextField,
  type IDropdownOption,
} from "@fluentui/react";

import "@/lib/fluent-icons";
import { BUTTON_STYLES, SURFACE } from "@/components/studio/career/career-ui";
import {
  NONE_FACET,
  emptyOfferFilter,
  offerFacets,
  offerFilterActive,
  offerFilterCount,
  type OfferFacetFilter,
} from "@/lib/career-filters";
import type { CareerOpportunity } from "@/lib/career-api";
import { cn } from "@/lib/utils";

const DROPDOWN = { dropdown: { width: "100%" } };
const FILTER_CALLOUT = {
  preventDismissOnScroll: true,
  preventDismissOnResize: true,
  isBeakVisible: false,
};
const FIELD_GRID = "grid gap-4 sm:grid-cols-2";

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
  trailing,
}: {
  rows: CareerOpportunity[];
  hints?: string[];
  filter: OfferFacetFilter;
  tx: Tx;
  onChange: (next: OfferFacetFilter) => void;
  trailing?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const facets = offerFacets(rows, hints);
  const none = tx("filterNoneValue", "Not set");
  const active = offerFilterActive(filter);
  const count = offerFilterCount(filter);
  return (
    <div className={cn(SURFACE, "p-4 sm:p-5")} data-testid="career-filters">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[14rem] flex-1">
          <TextField
            label={tx("searchOffers", "Search the list")}
            value={filter.query}
            placeholder={tx("searchOffersHint", "Title, company, stack, country...")}
            onChange={(_, value) => onChange({ ...filter, query: value || "" })}
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
        />
        <DefaultButton
          text={tx("clearFilters", "Clear filters")}
          disabled={!active}
          onClick={() => onChange(emptyOfferFilter())}
          styles={BUTTON_STYLES}
        />
        {trailing}
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
                  options={facets.countries.map((key) => ({ key, text: optionLabel(key, none) }))}
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
                  options={facets.remotes.map((key) => ({ key, text: key }))}
                  onChange={(_, option) => onChange({ ...filter, remotes: toggleKeys(filter.remotes, option) })}
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
              </div>
            </PivotItem>
          </Pivot>
        </Panel>
      ) : null}
    </div>
  );
}
