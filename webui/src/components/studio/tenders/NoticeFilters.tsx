import { useState, type ReactNode } from "react";
import { DefaultButton, Dropdown, TextField, type IDropdownOption } from "@fluentui/react";

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
import type { TenderNotice } from "@/lib/tenders-api";

const DROPDOWN = { dropdown: { width: "100%" } };
const FIELD_GRID = "grid gap-4 sm:grid-cols-2 lg:grid-cols-4";

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
  onChange,
  onQuery,
  extra,
}: {
  rows: TenderNotice[];
  crafts: string[];
  filter: NoticeFacetFilter;
  query: string;
  tx: Tx;
  onChange: (next: NoticeFacetFilter) => void;
  onQuery: (value: string) => void;
  extra?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const facets = noticeFacets(rows, crafts);
  const none = tx("filterNoneValue", "Not set");
  const active = facetFilterActive(filter);
  const count = facetFilterCount(filter);
  return (
    <Surface className="grid gap-4 p-4 sm:p-5" data-testid="notice-filters">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <TextField
            label={tx("searchNotices", "Search a notice")}
            value={query}
            placeholder={tx("searchNoticesHint", "Title, country, buyer...")}
            onChange={(_, value) => onQuery(value || "")}
          />
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {extra}
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
        </div>
      </div>
      {open ? (
        <div className={FIELD_GRID} data-testid="notice-filter-fields">
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
            selectedKeys={filter.sectors}
            options={facets.sectors.map((key) => ({ key, text: optionLabel(key, none) }))}
            onChange={(_, option) => onChange({ ...filter, sectors: toggleKeys(filter.sectors, option) })}
            placeholder={tx("filterAny", "All")}
            styles={DROPDOWN}
            calloutProps={FILTER_CALLOUT}
          />
          <Dropdown
            label={tx("filterBuyer", "Buyer")}
            multiSelect
            selectedKeys={filter.buyers}
            options={facets.buyers.map((key) => ({ key, text: optionLabel(key, none) }))}
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
            options={[
              { key: "any", text: tx("filterAny", "All") },
              { key: "go", text: tx("filterGo", "GO") },
              { key: "nogo", text: tx("filterNogo", "No-go") },
              { key: "unscored", text: tx("filterUnscored", "Not scored") },
            ]}
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
            options={[
              { key: "any", text: tx("filterAny", "All") },
              { key: "has", text: tx("filterHasDeadline", "Has a deadline") },
              { key: "none", text: tx("filterNoDeadline", "No deadline") },
            ]}
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
