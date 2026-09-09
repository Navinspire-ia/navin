// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { ChoiceGroup, DefaultButton, PrimaryButton, TextField } from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { LeadsScene } from "@/components/studio/leads/LeadsScene";
import { BUTTON_STYLES, SPRING, SURFACE, type Tx } from "@/components/studio/leads/leads-ui";
import { countryLabel, countryOptions, normalizeCountryValue } from "@/lib/crm-catalog";
import { LEAD_SOURCES, type LeadSource, type LeadsProfile } from "@/lib/leads-api";
import { cn } from "@/lib/utils";

function splitList(raw: string): string[] {
  return raw
    .split(/[,;\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function LeadsStart({
  tx,
  profile,
  busy,
  onLaunch,
  onDetails,
  onSave,
  onBack,
}: {
  tx: Tx;
  profile: LeadsProfile;
  busy: boolean;
  onLaunch: (body: Record<string, unknown>) => void;
  onDetails: () => void;
  onSave?: (body: Record<string, unknown>) => void;
  onBack?: () => void;
}) {
  const reduceMotion = useReducedMotion();
  const { i18n, t } = useTranslation();
  const lang = i18n.language || "fr";
  const [name, setName] = useState(profile.icp_name || "");
  const [sector, setSector] = useState(profile.sector || "");
  const [titles, setTitles] = useState((profile.titles || []).join(", "));
  const [sizeMin, setSizeMin] = useState(String(profile.size_min || 20));
  const [sizeMax, setSizeMax] = useState(String(profile.size_max || 200));
  const [count, setCount] = useState(String(profile.count || 50));
  const [countries, setCountries] = useState<string[]>(() => {
    const fromProfile = (profile.countries || [])
      .map((item) => normalizeCountryValue(item))
      .filter(Boolean);
    return fromProfile.length ? [...new Set(fromProfile)] : ["FR"];
  });
  const [offer, setOffer] = useState(profile.offer || "");
  const [cities, setCities] = useState((profile.cities || []).join(", "));
  const [keywords, setKeywords] = useState((profile.keywords || []).join(", "));
  const [signals, setSignals] = useState((profile.signals || []).join(", "));
  const [sources, setSources] = useState<string[]>(
    profile.sources?.length ? [...profile.sources] : [...LEAD_SOURCES],
  );
  const [mode, setMode] = useState<string>(profile.execution_mode === "autonomous" ? "autonomous" : "approval");
  const [cap, setCap] = useState(String(profile.daily_send_cap ?? 20));
  const [sender, setSender] = useState(profile.sender_name || "");
  const [more, setMore] = useState(
    Boolean(profile.offer || profile.cities?.length || profile.keywords?.length || profile.signals?.length),
  );
  const ready = Boolean(name.trim() && sector.trim() && countries.length);
  const picked = useMemo(() => new Set(countries.map((iso) => iso.toUpperCase())), [countries]);
  const countryChoices = useMemo(
    () => countryOptions(lang).filter((row) => !picked.has(row.value)),
    [lang, picked],
  );

  const addCountry = (raw: string) => {
    const iso = normalizeCountryValue(raw);
    if (!iso || picked.has(iso)) return;
    setCountries([...countries, iso]);
  };

  const toggleSource = (source: LeadSource) =>
    setSources((current) => (current.includes(source) ? current.filter((item) => item !== source) : [...current, source]));

  const payload = (): Record<string, unknown> => ({
    icp_name: name.trim(),
    sector: sector.trim(),
    titles: splitList(titles),
    countries,
    size_min: Number(sizeMin) || 20,
    size_max: Number(sizeMax) || 200,
    count: Number(count) || 50,
    offer: offer.trim(),
    cities: splitList(cities),
    keywords: splitList(keywords),
    signals: splitList(signals),
    sources,
    execution_mode: mode,
    daily_send_cap: Math.max(0, Number(cap) || 0),
    sender_name: sender.trim(),
    wizard_ready: true,
  });
  const launch = () => onLaunch(payload());

  const sourceLabel: Record<LeadSource, string> = {
    web: tx("start.sourceWeb", "Web search + list pages"),
    osm: tx("start.sourceOsm", "OpenStreetMap"),
    hiring: tx("start.sourceHiring", "Hiring signals (LinkedIn jobs)"),
  };

  return (
    <div className="mx-auto grid w-full max-w-4xl gap-6" data-testid="leads-start">
      <motion.div
        initial={reduceMotion ? false : { opacity: 0, y: 12, filter: "blur(4px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        transition={SPRING}
        className={cn(SURFACE, "grid gap-6 p-6 sm:p-8")}
      >
        <div className="grid gap-6 sm:grid-cols-[1fr_auto] sm:items-center">
          <div className="min-w-0">
            <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
              {tx("start.kicker", "Waterfall prospecting")}
            </p>
            <h2 className="mt-2 text-balance text-3xl font-semibold tracking-tight">
              {tx("start.title", "Who do you want to find?")}
            </h2>
            <p className="mt-3 max-w-lg text-pretty text-sm leading-relaxed text-muted-foreground">
              {tx(
                "start.sub",
                "Registries, hiring signals, localized web search, list pages and OpenStreetMap first, rotating cities every hunt. Apollo, Hunter, PDL and Pappers only fill what is still missing. LinkedIn people pages are never scraped.",
              )}
            </p>
          </div>
          <div className="w-full sm:w-52">
            <LeadsScene
              matchRatio={ready ? 0.82 : 0.28}
              active={!busy}
              label={tx("sceneAria", "Three dimensional lead funnel")}
            />
          </div>
        </div>
        <TextField
          label={tx("start.icp", "ICP name")}
          value={name}
          onChange={(_, value) => setName(value || "")}
          placeholder={tx("start.icpPh", "French SaaS 20-200")}
        />
        <TextField
          label={tx("start.sector", "Sector")}
          value={sector}
          onChange={(_, value) => setSector(value || "")}
          placeholder={tx("start.sectorPh", "SaaS, logistics, clinics")}
        />
        <TextField
          label={tx("start.titles", "Roles to find")}
          value={titles}
          onChange={(_, value) => setTitles(value || "")}
          placeholder="CEO, CTO, Head of Data"
        />
        <div className="grid gap-4 sm:grid-cols-3">
          <TextField label={tx("start.sizeMin", "Min employees")} value={sizeMin} onChange={(_, v) => setSizeMin(v || "")} />
          <TextField label={tx("start.sizeMax", "Max employees")} value={sizeMax} onChange={(_, v) => setSizeMax(v || "")} />
          <TextField label={tx("start.count", "How many companies")} value={count} onChange={(_, v) => setCount(v || "")} />
        </div>
        <div className="grid gap-2" data-testid="leads-start-countries">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {tx("start.countriesLabel", "Countries")}
          </p>
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("start.countriesHint", "Search the full country list and add as many as you want.")}
          </p>
          <SearchableSelect
            value=""
            options={countryChoices}
            onChange={addCountry}
            placeholder={tx("start.addCountry", "Add a country")}
            searchPlaceholder={t("crm.searchCountry", { defaultValue: "Search a country" })}
            emptyLabel={t("crm.noResults", { defaultValue: "No results" })}
            allowEmpty
          />
          {countries.length ? (
            <ul className="flex flex-wrap gap-2" aria-label={tx("start.countriesAria", "Countries")}>
              {countries.map((iso) => (
                <li key={iso}>
                  <button
                    type="button"
                    onClick={() => setCountries(countries.filter((item) => item !== iso))}
                    title={iso}
                    className="min-h-10 cursor-pointer rounded-full bg-indigo-500/16 px-3 text-[13px] font-medium transition-[transform,background-color] duration-150 hover:bg-indigo-500/24 active:scale-[0.96]"
                  >
                    {countryLabel(iso, lang) || iso} ×
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              {tx("start.countriesEmpty", "Add at least one country to hunt.")}
            </p>
          )}
        </div>
        <div className="grid gap-3" aria-label={tx("start.sourcesAria", "Open sources")}>
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {tx("start.sources", "Open sources (registries and your keys are always on)")}
          </p>
          <div className="flex flex-wrap gap-2">
            {LEAD_SOURCES.map((source) => (
              <button
                key={source}
                type="button"
                onClick={() => toggleSource(source)}
                aria-pressed={sources.includes(source)}
                data-testid={`leads-source-${source}`}
                className={cn(
                  "min-h-10 cursor-pointer rounded-full px-4 text-sm font-medium outline outline-1",
                  sources.includes(source)
                    ? "bg-indigo-600 text-white outline-indigo-600"
                    : "bg-background text-foreground outline-black/10 hover:bg-muted/60 dark:outline-white/10",
                )}
              >
                {sourceLabel[source]}
              </button>
            ))}
          </div>
        </div>
        <button
          type="button"
          onClick={() => setMore((value) => !value)}
          aria-expanded={more}
          data-testid="leads-start-more"
          className="w-fit cursor-pointer text-sm font-medium text-indigo-700 underline-offset-4 hover:underline dark:text-indigo-300"
        >
          {more ? tx("start.less", "Hide targeting and autonomy") : tx("start.more", "Targeting, offer and autonomy")}
        </button>
        {more ? (
          <div className="grid gap-4" data-testid="leads-start-advanced">
            <TextField
              label={tx("start.offer", "Your offer, in one line")}
              value={offer}
              onChange={(_, value) => setOffer(value || "")}
              placeholder={tx("start.offerPh", "We automate B2B prospecting for agencies.")}
              description={tx("start.offerHint", "Drives the outreach drafts in the prospect's language.")}
              multiline
              rows={2}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label={tx("start.cities", "Cities to focus on")}
                value={cities}
                onChange={(_, value) => setCities(value || "")}
                placeholder="Lyon, Annecy"
                description={tx("start.citiesHint", "Empty = the main cities of each country, rotated each hunt.")}
              />
              <TextField
                label={tx("start.keywords", "Extra keywords")}
                value={keywords}
                onChange={(_, value) => setKeywords(value || "")}
                placeholder="Shopify, ISO 27001"
              />
            </div>
            <TextField
              label={tx("start.signals", "Roles they hire when they need you")}
              value={signals}
              onChange={(_, value) => setSignals(value || "")}
              placeholder={tx("start.signalsPh", "Head of growth, supply chain manager")}
              description={tx("start.signalsHint", "Public LinkedIn job listings become a hiring signal on the lead.")}
            />
            <ChoiceGroup
              label={tx("start.mode", "Execution")}
              selectedKey={mode}
              onChange={(_, option) => setMode(String(option?.key || "approval"))}
              options={[
                {
                  key: "approval",
                  text: tx("start.modeApproval", "Approval: the loop drafts every due step, you send"),
                },
                {
                  key: "autonomous",
                  text: tx("start.modeAutonomous", "Autonomous: the loop sends due steps by email, within the daily cap"),
                },
              ]}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label={tx("start.cap", "Daily send cap")}
                value={cap}
                onChange={(_, value) => setCap(value || "")}
                disabled={mode !== "autonomous"}
              />
              <TextField
                label={tx("start.sender", "Signature name")}
                value={sender}
                onChange={(_, value) => setSender(value || "")}
                placeholder={tx("start.senderPh", "Your name or brand")}
              />
            </div>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <PrimaryButton
            text={tx("start.launch", "Start the hunt")}
            iconProps={{ iconName: "Search" }}
            disabled={!ready || busy}
            onClick={launch}
            styles={BUTTON_STYLES}
          />
          {onSave ? (
            <DefaultButton
              text={tx("start.save", "Save without hunting")}
              iconProps={{ iconName: "Save" }}
              disabled={!ready || busy}
              onClick={() => onSave(payload())}
              styles={BUTTON_STYLES}
              data-testid="leads-start-save"
            />
          ) : null}
          <DefaultButton
            text={tx("start.providers", "Provider keys")}
            iconProps={{ iconName: "Settings" }}
            onClick={onDetails}
            styles={BUTTON_STYLES}
          />
          {onBack ? (
            <DefaultButton
              text={tx("start.back", "Back to the desk")}
              iconProps={{ iconName: "Back" }}
              onClick={onBack}
              styles={BUTTON_STYLES}
            />
          ) : null}
        </div>
      </motion.div>
    </div>
  );
}
