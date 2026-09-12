// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { DefaultButton, PrimaryButton, ProgressIndicator, TextField, Toggle } from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { CrmPhoneField } from "@/components/crm/CrmPhoneField";
import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { CareerCvPanel, CareerDossierPanel } from "@/components/studio/career/CareerCvStep";
import { CareerCountryMultiSelect } from "@/components/studio/career/CareerCountrySelect";
import {
  BUTTON_STYLES,
  CAREER_API_CONNECTORS,
  CAREER_API_SOURCE_IDS,
  SPRING,
  SURFACE,
  TOOLS_HASH,
  careerLiveSourceOn,
  newId,
  openOfficialCareerUrl,
  toggleCareerLiveSource,
  type Tx,
} from "@/components/studio/career/career-ui";
import { countryOptions, currencyOptions, normalizeCountryValue } from "@/lib/crm-catalog";
import type {
  CareerChannels,
  CareerCompany,
  CareerDesk,
  CareerEducation,
  CareerExperience,
  CareerProject,
  CareerSource,
  CareerTalent,
  CareerTrack,
} from "@/lib/career-api";
import { cn } from "@/lib/utils";

const ALL_STEPS = [
  { id: "kind", icon: "Contact" },
  { id: "company", icon: "CityNext", companyOnly: true },
  { id: "cv", icon: "TextDocument" },
  { id: "dossier", icon: "Edit" },
  { id: "criteria", icon: "Filter" },
  { id: "pitch", icon: "Mail" },
  { id: "channels", icon: "Chat" },
  { id: "sources", icon: "Globe" },
] as const;

function FieldLabel({ children }: { children: string }) {
  return <p className="mb-1.5 text-[12px] font-medium text-foreground">{children}</p>;
}

function Chip({
  active,
  children,
  onClick,
}: {
  active?: boolean;
  children: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "min-h-10 rounded-full px-3.5 text-sm font-medium outline outline-1 transition-colors",
        active
          ? "bg-indigo-600 text-white outline-indigo-600"
          : "bg-background text-foreground outline-black/10 hover:bg-muted/60 dark:outline-white/10",
      )}
    >
      {children}
    </button>
  );
}

function TagList({ values, onRemove }: { values: string[]; onRemove: (value: string) => void }) {
  if (!values.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {values.map((value) => (
        <button
          key={value}
          type="button"
          onClick={() => onRemove(value)}
          className="rounded-full bg-muted px-3 py-1.5 text-xs font-medium text-foreground"
        >
          {value} ×
        </button>
      ))}
    </div>
  );
}

function emptyTalent(): CareerTalent {
  return {
    id: newId(),
    name: "",
    headline: "",
    titles: [],
    master_cv: "",
    experiences: [],
    education: [],
    strengths: [],
    weaknesses: [],
    stack: [],
  };
}

function emptyCompany(): CareerCompany {
  return { name: "", email: "", phone: "", address: "", city: "", country: "" };
}

function emptyChannels(): CareerChannels {
  return {
    email: false,
    teams: false,
    whatsapp: false,
    telegram: false,
    email_to: "",
    teams_to: "",
    whatsapp_to: "",
    telegram_to: "",
  };
}

export function CareerWizard({
  desk,
  track,
  tx,
  busy,
  catalog,
  token,
  onSave,
  onSeed,
  onSecret,
  onLeave,
}: {
  desk: CareerDesk;
  track: CareerTrack;
  tx: Tx;
  busy: boolean;
  catalog: CareerSource[];
  token?: string;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onSeed?: (text: string) => void;
  onSecret?: (name: string, value: string) => Promise<boolean>;
  onLeave?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const reduceMotion = useReducedMotion();
  const profile = desk.profile || {};
  const lang = (i18n.language || "fr").slice(0, 2);
  const crmTx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const countries = useMemo(() => countryOptions(lang), [lang]);
  const currencies = useMemo(() => currencyOptions(), []);

  const [kind, setKind] = useState<"solo" | "company">(profile.account_kind === "company" ? "company" : "solo");
  const [stepId, setStepId] = useState<string>(() => {
    if (profile.wizard_complete) return "kind";
    const offset = Math.max(0, (profile.wizard_step || 1) - 1);
    const filtered = ALL_STEPS.filter((row) => !("companyOnly" in row && row.companyOnly) || profile.account_kind === "company");
    return filtered[Math.min(offset, filtered.length - 1)]?.id || "kind";
  });
  const [cvPath, setCvPath] = useState<"" | "import" | "create">(
    profile.cv_path === "create" || profile.cv_path === "import"
      ? profile.cv_path
      : profile.master_cv
        ? "import"
        : "",
  );
  const [experiences, setExperiences] = useState<CareerExperience[]>(
    profile.experiences?.length ? profile.experiences : [{ title: "", company: "", period: "", facts: "" }],
  );
  const [education, setEducation] = useState<CareerEducation[]>(
    profile.education?.length ? profile.education : [{ school: "", diploma: "", year: "" }],
  );
  const [company, setCompany] = useState<CareerCompany>({ ...emptyCompany(), ...profile.company });
  const [talents, setTalents] = useState<CareerTalent[]>(
    profile.talents?.length ? profile.talents : [emptyTalent()],
  );
  const [activeTalentId, setActiveTalentId] = useState(profile.active_talent_id || talents[0]?.id || "");
  const [displayName, setDisplayName] = useState(profile.display_name || "");
  const [headline, setHeadline] = useState(profile.headline || "");
  const [masterCv, setMasterCv] = useState(profile.master_cv || "");
  const [strengths, setStrengths] = useState<string[]>(profile.strengths || []);
  const [weaknesses, setWeaknesses] = useState<string[]>(profile.weaknesses || []);
  const [highlights, setHighlights] = useState<string[]>(profile.highlights || []);
  const [projects, setProjects] = useState<CareerProject[]>(profile.projects?.length ? profile.projects : [{ title: "", result: "" }]);
  const [strengthDraft, setStrengthDraft] = useState("");
  const [weaknessDraft, setWeaknessDraft] = useState("");
  const [highlightDraft, setHighlightDraft] = useState("");
  const [titles, setTitles] = useState((profile.titles || []).join(", "));
  const [stack, setStack] = useState((profile.stack || []).join(", "));
  const [residence, setResidence] = useState(normalizeCountryValue(profile.residence_country || ""));
  const [searchCountries, setSearchCountries] = useState<string[]>(profile.countries_primary || []);
  const [secondaryCountries, setSecondaryCountries] = useState<string[]>(profile.countries_secondary || []);
  const [excludedCountries, setExcludedCountries] = useState<string[]>(profile.countries_excluded || []);
  const [phone, setPhone] = useState(profile.phone || "");
  const [email, setEmail] = useState(profile.email || "");
  const [currency, setCurrency] = useState(profile.currency || "EUR");
  const [available, setAvailable] = useState(profile.available_from || "");
  const [minPay, setMinPay] = useState(String((track === "jobs" ? profile.min_salary : profile.min_rate) || ""));
  const [maxPay, setMaxPay] = useState(String((track === "jobs" ? profile.max_salary : profile.max_rate) || ""));
  const [workMode, setWorkMode] = useState(profile.work_mode || "remote");
  const [hybridMin, setHybridMin] = useState(String(profile.hybrid_days_min || ""));
  const [hybridMax, setHybridMax] = useState(String(profile.hybrid_days_max || ""));
  const [prospect, setProspect] = useState(profile.prospect_email || "");
  const [prospectOk, setProspectOk] = useState(Boolean(profile.prospect_email_approved));
  const [channels, setChannels] = useState<CareerChannels>({ ...emptyChannels(), ...profile.channels });
  const [sourceIds, setSourceIds] = useState<string[]>(profile.source_ids || []);
  const [apiDrafts, setApiDrafts] = useState<Record<string, string>>({});
  const [languages, setLanguages] = useState((profile.languages || []).join(", "));
  const [visa, setVisa] = useState(profile.visa && profile.visa !== "none" ? profile.visa : "");
  const [aiAssist, setAiAssist] = useState(profile.ai_assist === true);
  const [archiveAfterDays, setArchiveAfterDays] = useState(String(profile.archive_after_days || ""));
  const [deleteAfterDays, setDeleteAfterDays] = useState(String(profile.delete_after_days || ""));

  useEffect(() => {
    setMinPay(String((track === "jobs" ? profile.min_salary : profile.min_rate) || ""));
    setMaxPay(String((track === "jobs" ? profile.max_salary : profile.max_rate) || ""));
  }, [profile.max_rate, profile.max_salary, profile.min_rate, profile.min_salary, track]);

  const steps = ALL_STEPS.filter((row) => !("companyOnly" in row && row.companyOnly) || kind === "company");
  const stepIndex = Math.max(0, steps.findIndex((row) => row.id === stepId));
  const current = steps[stepIndex] || steps[0];
  const activeTalent = talents.find((row) => row.id === activeTalentId) || talents[0];

  const addToken = (
    value: string,
    list: string[],
    setList: (next: string[]) => void,
    setDraft: (next: string) => void,
  ) => {
    const token = value.trim();
    if (!token || list.includes(token)) {
      setDraft("");
      return;
    }
    setList([...list, token]);
    setDraft("");
  };

  const applyTalent = (row: CareerTalent) => {
    setActiveTalentId(row.id || "");
    setDisplayName(row.name || "");
    setHeadline(row.headline || "");
    setMasterCv(row.master_cv || "");
    setExperiences(row.experiences?.length ? row.experiences : [{ title: "", company: "", period: "", facts: "" }]);
    setEducation(row.education?.length ? row.education : [{ school: "", diploma: "", year: "" }]);
    setStrengths(row.strengths || []);
    setWeaknesses(row.weaknesses || []);
    setTitles((row.titles || []).join(", "));
    setStack((row.stack || []).join(", "));
  };

  const snapshotTalent = (row: CareerTalent): CareerTalent => ({
    ...row,
    name: displayName,
    headline,
    titles: titles.split(",").map((item) => item.trim()).filter(Boolean),
    master_cv: masterCv,
    experiences: experiences.filter((row) => row.title || row.company || row.facts),
    education: education.filter((row) => row.school || row.diploma),
    strengths,
    weaknesses,
    stack: stack.split(",").map((item) => item.trim()).filter(Boolean),
  });

  const assignCountries = (bucket: "primary" | "secondary" | "excluded", next: string[]) => {
    const codes = [...new Set(next.map((item) => normalizeCountryValue(item)).filter(Boolean))];
    const locked = new Set(codes);
    if (bucket === "primary") setSearchCountries(codes);
    if (bucket === "secondary") setSecondaryCountries(codes);
    if (bucket === "excluded") setExcludedCountries(codes);
    if (bucket !== "primary") setSearchCountries((current) => current.filter((item) => !locked.has(item)));
    if (bucket !== "secondary") setSecondaryCountries((current) => current.filter((item) => !locked.has(item)));
    if (bucket !== "excluded") setExcludedCountries((current) => current.filter((item) => !locked.has(item)));
  };

  const persistTalent = (next: CareerTalent) => {
    setTalents((current) => current.map((row) => (row.id === next.id ? next : row)));
  };

  const payload = (extra: Record<string, unknown> = {}): Record<string, unknown> => {
    const min = Number(minPay) || 0;
    const max = Number(maxPay) || 0;
    const syncedTalent: CareerTalent = {
      ...activeTalent,
      name: kind === "company" ? activeTalent?.name || displayName : displayName,
      headline,
      titles: titles.split(",").map((item) => item.trim()).filter(Boolean),
      master_cv: masterCv,
      experiences: experiences.filter((row) => row.title || row.company || row.facts),
      education: education.filter((row) => row.school || row.diploma),
      strengths,
      weaknesses,
      stack: stack.split(",").map((item) => item.trim()).filter(Boolean),
    };
    const nextTalents =
      kind === "company"
        ? talents.map((row) => (row.id === syncedTalent.id ? syncedTalent : row))
        : [syncedTalent];
    return {
      track,
      account_kind: kind,
      display_name: displayName,
      headline,
      email: kind === "company" ? company.email || email : email,
      phone: kind === "company" ? company.phone || phone : phone,
      residence_country: residence,
      titles: syncedTalent.titles,
      countries_primary: searchCountries,
      countries_secondary: secondaryCountries,
      countries_excluded: excludedCountries,
      work_mode: workMode,
      hybrid_days_min: Number(hybridMin) || 0,
      hybrid_days_max: Number(hybridMax) || 0,
      min_rate: track === "freelance" ? min : 0,
      max_rate: track === "freelance" ? max : 0,
      min_salary: track === "jobs" ? min : 0,
      max_salary: track === "jobs" ? max : 0,
      currency,
      available_from: available,
      languages: languages.split(",").map((item) => item.trim()).filter(Boolean),
      visa: visa.trim() || "none",
      stack: syncedTalent.stack,
      master_cv: masterCv,
      cv_path: cvPath,
      experiences: syncedTalent.experiences,
      education: syncedTalent.education,
      strengths,
      weaknesses,
      highlights,
      projects: projects.filter((row) => row.title || row.result),
      prospect_email: prospect,
      prospect_email_approved: prospectOk,
      channels,
      company: kind === "company" ? company : emptyCompany(),
      talents: nextTalents,
      active_talent_id: syncedTalent.id,
      source_ids: sourceIds,
      engagement: track === "freelance" ? "freelance" : "permanent",
      ai_assist: aiAssist,
      archive_after_days: Number(archiveAfterDays) || 0,
      delete_after_days: Number(deleteAfterDays) || 0,
      wizard_step: stepIndex + 1,
      ...extra,
    };
  };

  const persist = (extra: Record<string, unknown> = {}) => {
    void onSave(payload(extra));
    return true;
  };

  const goTo = (nextId: string) => {
    setStepId(nextId);
    persist({ wizard_step: steps.findIndex((row) => row.id === nextId) + 1 });
  };

  const goNext = () => {
    const next = steps[stepIndex + 1];
    if (next) goTo(next.id);
  };

  const finish = () => {
    const pending = Object.entries(apiDrafts).filter(([, value]) => value.trim());
    void (async () => {
      for (const [name, value] of pending) {
        await onSecret?.(name, value.trim());
      }
      persist({ wizard_complete: true, wizard_step: steps.length });
    })();
  };

  const polishCv = () => {
    onSeed?.(
      `/career Rewrite this master CV for ${track === "freelance" ? "freelance missions" : "permanent jobs"}. Keep every fact. Never invent employers, dates or diplomas. Highlight strengths: ${(strengths || []).join(", ") || "from the text"}. Produce a clean ATS layout I can paste back into Master CV.\n\n${masterCv || "(facts missing - ask me first)"}\n\n`,
    );
  };

  const extractDossier = (kindKey: "strengths" | "weaknesses") => {
    onSeed?.(
      kindKey === "strengths"
        ? `/career From this CV only, list 5 to 8 strengths to highlight. Facts only. Return a short bullet list I can paste.\n\n${masterCv}\n\n`
        : `/career From this CV only, list honest gaps to watch (missing stack, seniority, language, visa). Never invent. Short bullets I can paste.\n\n${masterCv}\n\n`,
    );
  };

  const rewriteDossier = (kindKey: "strengths" | "weaknesses") => {
    const current = kindKey === "strengths" ? strengths : weaknesses;
    onSeed?.(
      kindKey === "strengths"
        ? `/career Rewrite these strengths so they sell the candidate without inventing anything: ${current.join(", ") || "extract from the CV"}. Keep them short.\n\nCV:\n${masterCv}\n\n`
        : `/career Rewrite these gaps honestly, no drama, no invention: ${current.join(", ") || "from the CV"}.\n\nCV:\n${masterCv}\n\n`,
    );
  };

  const goBack = () => {
    const prev = steps[stepIndex - 1];
    if (prev) goTo(prev.id);
  };

  const canContinue =
    current.id === "kind"
      ? Boolean(kind)
      : current.id === "company"
        ? Boolean(String(company.name || "").trim())
        : current.id === "cv"
          ? Boolean(masterCv.trim() || displayName.trim())
          : current.id === "criteria"
            ? Boolean(titles.trim() && searchCountries.length)
            : true;

  const liveIds = new Set(
    (desk.stack?.connectors || [])
      .filter((row) => row.live && !row.needs_key)
      .map((row) => row.id),
  );
  const catalogIsLive = (row: CareerSource) => {
    if (CAREER_API_SOURCE_IDS.includes(row.id as (typeof CAREER_API_SOURCE_IDS)[number])) return false;
    if (liveIds.has(row.id)) return true;
    if (["greenhouse", "lever", "ashby"].includes(row.id) && liveIds.has("ats")) return true;
    if (row.id === "web-job-search" && (liveIds.has("web-search") || liveIds.has("web-job-search"))) {
      return true;
    }
    return false;
  };
  const liveSources = catalog.filter(catalogIsLive);
  const apiIdSet = new Set<string>(CAREER_API_SOURCE_IDS);
  const keyedSources = catalog.filter((row) => !liveSources.includes(row) && !apiIdSet.has(row.id));
  const apiKeys = profile.api_keys || {};
  const envLabel = (env: string) => {
    if (env === "ADZUNA_APP_ID") return tx("wizard.adzunaAppId", "Adzuna app id");
    if (env === "ADZUNA_APP_KEY") return tx("wizard.adzunaAppKey", "Adzuna app key");
    if (env === "JOOBLE_API_KEY") return tx("wizard.joobleKey", "Jooble API key");
    if (env === "USAJOBS_API_KEY") return tx("wizard.usajobsKey", "USAJOBS API key");
    if (env === "USAJOBS_USER_AGENT") return tx("wizard.usajobsAgent", "USAJOBS user agent (your email)");
    return env;
  };
  const saveApiField = (env: string, value: string) => {
    const text = value.trim();
    if (!text) return;
    void onSecret?.(env, text).then((ok) => {
      if (ok) setApiDrafts((current) => ({ ...current, [env]: "" }));
    });
  };

  return (
    <div className="mx-auto grid w-full max-w-3xl gap-6" data-testid="career-wizard">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
            {tx("wizardKicker", "Career setup")}
          </p>
          <h2 className="mt-2 text-balance text-2xl font-semibold tracking-tight">
            {tx(`wizard.${current.id}Title`, current.id)}
          </h2>
          <p className="mt-2 text-pretty text-sm text-muted-foreground">
            {tx("wizardStepOf", "Step {{current}} of {{total}}", { current: stepIndex + 1, total: steps.length })}
            {" · "}
            {track === "freelance" ? tx("trackFreelance", "Freelance") : tx("trackJobs", "Jobs")}
          </p>
        </div>
        {onLeave ? (
          <DefaultButton
            text={tx("backOffers", "Back to offers")}
            title={tx("backOffers", "Back to offers")}
            iconProps={{ iconName: "Back" }}
            onClick={onLeave}
            styles={BUTTON_STYLES}
            data-testid="career-leave-setup"
          />
        ) : null}
      </div>
      <ProgressIndicator percentComplete={(stepIndex + 1) / steps.length} />
      <ol
        className="flex gap-1 overflow-x-auto pb-1"
        aria-label={tx("wizardStepsAria", "Setup steps")}
        data-testid="career-wizard-steps"
      >
        {steps.map((row, index) => {
          const active = row.id === current.id;
          const done = index < stepIndex;
          return (
            <li key={row.id} className="min-w-0 shrink-0">
              <button
                type="button"
                onClick={() => goTo(row.id)}
                className={cn(
                  "flex min-h-11 cursor-pointer items-center gap-2 rounded-full px-3 text-[13px]",
                  active
                    ? "bg-indigo-600 text-white"
                    : done
                      ? "bg-indigo-600/10 text-indigo-900 dark:text-indigo-100"
                      : "bg-muted/50 text-muted-foreground",
                )}
                aria-current={active ? "step" : undefined}
              >
                <span className="tabular-nums text-[12px] font-semibold">{index + 1}</span>
                {active ? <span className="max-w-[10rem] truncate">{tx(`wizard.${row.id}`, row.id)}</span> : null}
              </button>
            </li>
          );
        })}
      </ol>

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={current.id}
          initial={reduceMotion ? false : { opacity: 0, y: 10, filter: "blur(4px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={reduceMotion ? undefined : { opacity: 0, y: -8, filter: "blur(4px)" }}
          transition={SPRING}
          className={cn(SURFACE, "grid gap-5 p-6 sm:p-8")}
        >
          {current.id === "kind" ? (
            <>
              <p className="text-pretty text-sm text-muted-foreground">
                {tx("wizard.kindHint", "A solo freelancer, or a company that places several CVs (portage).")}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => setKind("solo")}
                  className={cn(
                    "min-h-28 rounded-2xl px-5 py-4 text-left outline outline-1 transition-colors",
                    kind === "solo"
                      ? "bg-indigo-600/10 outline-indigo-500"
                      : "outline-black/10 hover:bg-muted/40 dark:outline-white/10",
                  )}
                >
                  <p className="text-base font-semibold">{tx("wizard.solo", "Solo")}</p>
                  <p className="mt-2 text-pretty text-sm text-muted-foreground">
                    {tx("wizard.soloHint", "One person, one CV, one search.")}
                  </p>
                </button>
                <button
                  type="button"
                  onClick={() => setKind("company")}
                  className={cn(
                    "min-h-28 rounded-2xl px-5 py-4 text-left outline outline-1 transition-colors",
                    kind === "company"
                      ? "bg-indigo-600/10 outline-indigo-500"
                      : "outline-black/10 hover:bg-muted/40 dark:outline-white/10",
                  )}
                >
                  <p className="text-base font-semibold">{tx("wizard.company", "Company")}</p>
                  <p className="mt-2 text-pretty text-sm text-muted-foreground">
                    {tx("wizard.companyHint", "Portage or agency. Several talent profiles.")}
                  </p>
                </button>
              </div>
            </>
          ) : null}

          {current.id === "company" ? (
            <>
              <TextField
                label={tx("wizard.companyName", "Company name")}
                value={company.name || ""}
                onChange={(_, v) => setCompany({ ...company, name: v || "" })}
                required
              />
              <TextField
                label={tx("wizard.companyEmail", "Company email")}
                value={company.email || ""}
                onChange={(_, v) => setCompany({ ...company, email: v || "" })}
              />
              <div>
                <FieldLabel>{tx("wizard.companyPhone", "Company phone")}</FieldLabel>
                <CrmPhoneField
                  value={company.phone || ""}
                  onChange={(value) => setCompany({ ...company, phone: value })}
                  defaultCountry={company.country || residence || "FR"}
                  locale={lang}
                  tx={crmTx}
                />
              </div>
              <TextField
                label={tx("wizard.address", "Headquarters address")}
                value={company.address || ""}
                onChange={(_, v) => setCompany({ ...company, address: v || "" })}
              />
              <TextField
                label={tx("wizard.city", "City")}
                value={company.city || ""}
                onChange={(_, v) => setCompany({ ...company, city: v || "" })}
              />
              <div>
                <FieldLabel>{tx("wizard.hqCountry", "Headquarters country")}</FieldLabel>
                <SearchableSelect
                  value={company.country || ""}
                  options={countries}
                  onChange={(value) => setCompany({ ...company, country: normalizeCountryValue(value) })}
                  placeholder={crmTx("crm.searchCountry", "Search a country")}
                  searchPlaceholder={crmTx("crm.searchCountry", "Search a country")}
                  emptyLabel={crmTx("crm.noResults", "No results")}
                />
              </div>
            </>
          ) : null}

          {current.id === "cv" ? (
            <>
              {kind === "company" ? (
                <div className="flex flex-wrap gap-2">
                  {talents.map((row) => (
                    <Chip
                      key={row.id}
                      active={row.id === activeTalent?.id}
                      onClick={() => {
                        if (activeTalent && activeTalent.id !== row.id) {
                          persistTalent(snapshotTalent(activeTalent));
                        }
                        applyTalent(row);
                      }}
                    >
                      {row.name || tx("wizard.untitledTalent", "Untitled profile")}
                    </Chip>
                  ))}
                  <DefaultButton
                    text={tx("wizard.addTalent", "Add a profile")}
                    iconProps={{ iconName: "Add" }}
                    onClick={() => {
                      const next = emptyTalent();
                      const kept = activeTalent ? snapshotTalent(activeTalent) : null;
                      setTalents([
                        ...talents.map((row) => (kept && row.id === kept.id ? kept : row)),
                        next,
                      ]);
                      applyTalent(next);
                    }}
                    styles={BUTTON_STYLES}
                  />
                </div>
              ) : null}
              <CareerCvPanel
                tx={tx}
                kind={kind}
                displayName={displayName}
                setDisplayName={(value) => {
                  setDisplayName(value);
                  if (activeTalent) persistTalent({ ...activeTalent, name: value });
                }}
                headline={headline}
                setHeadline={setHeadline}
                masterCv={masterCv}
                setMasterCv={setMasterCv}
                experiences={experiences}
                setExperiences={setExperiences}
                education={education}
                setEducation={setEducation}
                strengths={strengths}
                cvPath={cvPath}
                setCvPath={setCvPath}
                onPolish={polishCv}
                onCompose={() => persist()}
              />
            </>
          ) : null}

          {current.id === "dossier" ? (
            <CareerDossierPanel
              tx={tx}
              masterCv={masterCv}
              strengths={strengths}
              setStrengths={setStrengths}
              weaknesses={weaknesses}
              setWeaknesses={setWeaknesses}
              strengthDraft={strengthDraft}
              setStrengthDraft={setStrengthDraft}
              weaknessDraft={weaknessDraft}
              setWeaknessDraft={setWeaknessDraft}
              onAdd={addToken}
              onExtract={extractDossier}
              onRewrite={rewriteDossier}
            />
          ) : null}

          {current.id === "criteria" ? (
            <>
              <TextField
                label={
                  track === "freelance"
                    ? tx("wizard.missions", "Missions and roles to search")
                    : tx("wizard.jobs", "Roles to search")
                }
                value={titles}
                onChange={(_, v) => setTitles(v || "")}
                placeholder="Data Engineer, AI Engineer"
              />
              <TextField
                label={tx("stack", "Preferred stack")}
                value={stack}
                onChange={(_, v) => setStack(v || "")}
              />
              <div>
                <FieldLabel>{tx("wizard.residence", "Country of residence")}</FieldLabel>
                  <SearchableSelect
                  value={residence}
                  options={countries}
                  onChange={(value) => setResidence(normalizeCountryValue(value))}
                  placeholder={crmTx("crm.searchCountry", "Search a country")}
                  searchPlaceholder={crmTx("crm.searchCountry", "Search a country")}
                  emptyLabel={crmTx("crm.noResults", "No results")}
                />
              </div>
              <div className="grid gap-5">
                <div>
                  <FieldLabel>{tx("wizard.searchCountries", "Countries to search")}</FieldLabel>
                  <p className="mb-3 text-pretty text-sm text-muted-foreground">
                    {tx(
                      "wizard.searchCountriesHint",
                      "Search the full country list and add as many as you want.",
                    )}
                  </p>
                  <CareerCountryMultiSelect
                    values={searchCountries}
                    onChange={(next) => assignCountries("primary", next)}
                    lang={lang}
                    placeholder={tx("wizard.addCountry", "Add a country")}
                    searchPlaceholder={crmTx("crm.searchCountry", "Search a country")}
                    emptyLabel={crmTx("crm.noResults", "No results")}
                    testId="career-countries-primary"
                  />
                </div>
                <div>
                  <FieldLabel>{tx("wizard.secondaryMarkets", "Secondary markets")}</FieldLabel>
                  <p className="mb-3 text-pretty text-sm text-muted-foreground">
                    {tx("wizard.secondaryMarketsHint", "Useful, but scored below your primary countries.")}
                  </p>
                  <CareerCountryMultiSelect
                    values={secondaryCountries}
                    onChange={(next) => assignCountries("secondary", next)}
                    lang={lang}
                    placeholder={tx("wizard.addCountry", "Add a country")}
                    searchPlaceholder={crmTx("crm.searchCountry", "Search a country")}
                    emptyLabel={crmTx("crm.noResults", "No results")}
                    testId="career-countries-secondary"
                  />
                </div>
                <div>
                  <FieldLabel>{tx("wizard.excludedMarkets", "Excluded markets")}</FieldLabel>
                  <p className="mb-3 text-pretty text-sm text-muted-foreground">
                    {tx("wizard.excludedMarketsHint", "Never search or rank offers from these countries.")}
                  </p>
                  <CareerCountryMultiSelect
                    values={excludedCountries}
                    onChange={(next) => assignCountries("excluded", next)}
                    lang={lang}
                    placeholder={tx("wizard.addCountry", "Add a country")}
                    searchPlaceholder={crmTx("crm.searchCountry", "Search a country")}
                    emptyLabel={crmTx("crm.noResults", "No results")}
                    testId="career-countries-excluded"
                  />
                </div>
              </div>
              <div>
                <FieldLabel>{tx("wizard.phone", "Phone")}</FieldLabel>
                <CrmPhoneField
                  value={phone}
                  onChange={setPhone}
                  defaultCountry={residence || "FR"}
                  locale={lang}
                  tx={crmTx}
                />
              </div>
              <TextField label={tx("wizard.email", "Email")} value={email} onChange={(_, v) => setEmail(v || "")} />
              <div>
                <FieldLabel>{tx("currency", "Currency")}</FieldLabel>
                <SearchableSelect
                  value={currency}
                  options={currencies}
                  onChange={setCurrency}
                  placeholder={crmTx("crm.searchCurrency", "Search a currency")}
                  searchPlaceholder={crmTx("crm.searchCurrency", "Search a currency")}
                  emptyLabel={crmTx("crm.noResults", "No results")}
                />
              </div>
              <TextField
                label={tx("available", "Available from")}
                value={available}
                onChange={(_, v) => setAvailable(v || "")}
                placeholder="2026-09-15"
              />
              <TextField
                label={tx("languages", "Languages")}
                value={languages}
                onChange={(_, v) => setLanguages(v || "")}
                placeholder="fr, en"
              />
              <TextField
                label={tx("visa", "Visa / sponsorship")}
                value={visa}
                onChange={(_, v) => setVisa(v || "")}
                placeholder={tx("wizard.visaPh", "none, EU, or sponsorship needed")}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField
                  label={
                    track === "freelance"
                      ? tx("wizard.minRate", "Min daily rate (optional)")
                      : tx("wizard.minSalary", "Min salary (optional)")
                  }
                  value={minPay}
                  onChange={(_, v) => setMinPay(v || "")}
                />
                <TextField
                  label={
                    track === "freelance"
                      ? tx("wizard.maxRate", "Max daily rate (optional)")
                      : tx("wizard.maxSalary", "Max salary (optional)")
                  }
                  value={maxPay}
                  onChange={(_, v) => setMaxPay(v || "")}
                />
              </div>
              <div>
                <FieldLabel>{tx("workMode", "Work mode")}</FieldLabel>
                <div className="flex flex-wrap gap-2">
                  {(
                    [
                      ["remote", tx("workRemote", "Full remote")],
                      ["hybrid", tx("workHybrid", "Hybrid")],
                      ["onsite", tx("workOnsite", "On site")],
                      ["any", tx("workAny", "Any")],
                    ] as const
                  ).map(([id, label]) => (
                    <Chip key={id} active={workMode === id} onClick={() => setWorkMode(id)}>
                      {label}
                    </Chip>
                  ))}
                </div>
              </div>
              {workMode === "hybrid" ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextField
                    label={tx("wizard.hybridMin", "Min office days")}
                    value={hybridMin}
                    onChange={(_, v) => setHybridMin(v || "")}
                  />
                  <TextField
                    label={tx("wizard.hybridMax", "Max office days")}
                    value={hybridMax}
                    onChange={(_, v) => setHybridMax(v || "")}
                  />
                </div>
              ) : null}
              <Toggle
                label={tx("wizard.aiAssist", "Polish CV with the docs model (opt-in)")}
                checked={aiAssist}
                onChange={(_, checked) => setAiAssist(Boolean(checked))}
                onText={tx("wizard.aiOn", "On")}
                offText={tx("wizard.aiOff", "Off")}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField
                  label={tx("wizard.archiveAfterDays", "Archive after (days)")}
                  value={archiveAfterDays}
                  onChange={(_, v) => setArchiveAfterDays(v || "")}
                />
                <TextField
                  label={tx("wizard.deleteAfterDays", "Delete after (days)")}
                  value={deleteAfterDays}
                  onChange={(_, v) => setDeleteAfterDays(v || "")}
                />
              </div>
            </>
          ) : null}

          {current.id === "pitch" ? (
            <>
              <TextField
                label={tx("wizard.highlights", "Values and proof to put forward")}
                value={highlightDraft}
                placeholder={tx("addThenEnter", "Type, then Enter")}
                onChange={(_, v) => setHighlightDraft(v || "")}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    addToken(highlightDraft, highlights, setHighlights, setHighlightDraft);
                  }
                }}
              />
              <TagList values={highlights} onRemove={(value) => setHighlights(highlights.filter((row) => row !== value))} />
              <div className="grid gap-4">
                <p className="text-[12px] font-medium text-foreground">{tx("wizard.projects", "Best projects")}</p>
                {projects.map((row, index) => (
                  <div key={`project-${index}`} className="grid gap-3 rounded-xl bg-muted/30 p-4">
                    <TextField
                      label={tx("wizard.projectTitle", "Project")}
                      value={row.title || ""}
                      onChange={(_, v) => {
                        const next = [...projects];
                        next[index] = { ...row, title: v || "" };
                        setProjects(next);
                      }}
                    />
                    <TextField
                      label={tx("wizard.projectResult", "Result")}
                      value={row.result || ""}
                      onChange={(_, v) => {
                        const next = [...projects];
                        next[index] = { ...row, result: v || "" };
                        setProjects(next);
                      }}
                    />
                  </div>
                ))}
                <DefaultButton
                  text={tx("wizard.addProject", "Add a project")}
                  onClick={() => setProjects([...projects, { title: "", result: "" }])}
                  styles={BUTTON_STYLES}
                />
              </div>
              <TextField
                label={tx("wizard.prospectMail", "Prospecting email")}
                multiline
                rows={8}
                value={prospect}
                onChange={(_, v) => {
                  setProspect(v || "");
                  setProspectOk(false);
                }}
                placeholder={tx(
                  "wizard.prospectMailPh",
                  "Write it together. Navin keeps your facts. You validate before any send.",
                )}
              />
              <div className="flex flex-wrap gap-3">
                <DefaultButton
                  text={tx("wizard.draftMail", "Draft with the agent")}
                  onClick={() =>
                    onSeed?.(
                      `/career Draft a short prospecting email for ${displayName || "the candidate"} seeking ${titles} in ${searchCountries.join(", ")}. Use only CV facts. Wait for my edit before any send.\n\n`,
                    )
                  }
                  styles={BUTTON_STYLES}
                />
                <PrimaryButton
                  text={
                    prospectOk
                      ? tx("wizard.mailValidated", "Email validated")
                      : tx("wizard.validateMail", "Validate this email")
                  }
                  disabled={!prospect.trim()}
                  onClick={() => setProspectOk(true)}
                  styles={BUTTON_STYLES}
                />
              </div>
            </>
          ) : null}

          {current.id === "channels" ? (
            <>
              <p className="text-pretty text-sm text-muted-foreground">
                {tx(
                  "wizard.channelsHint",
                  "Notifications, accept, reject and meeting invites land here. Connect the apps in Tools if a channel is still off.",
                )}
              </p>
              {(
                [
                  ["email", "email_to", tx("wizard.chEmail", "Email")],
                  ["teams", "teams_to", tx("wizard.chTeams", "Microsoft Teams")],
                  ["whatsapp", "whatsapp_to", tx("wizard.chWhatsapp", "WhatsApp")],
                  ["telegram", "telegram_to", tx("wizard.chTelegram", "Telegram")],
                ] as const
              ).map(([flag, dest, label]) => (
                <div key={flag} className="grid gap-3 rounded-xl bg-muted/30 p-4">
                  <Toggle
                    label={label}
                    checked={Boolean(channels[flag])}
                    onChange={(_, checked) => setChannels({ ...channels, [flag]: Boolean(checked) })}
                  />
                  {channels[flag] ? (
                    <TextField
                      label={tx("wizard.channelTo", "Destination")}
                      value={String(channels[dest] || "")}
                      onChange={(_, v) => setChannels({ ...channels, [dest]: v || "" })}
                    />
                  ) : null}
                </div>
              ))}
              <DefaultButton
                text={tx("wizard.openTools", "Open Tools to connect channels")}
                iconProps={{ iconName: "NavigateExternalInline" }}
                onClick={() => {
                  window.location.hash = TOOLS_HASH;
                }}
                styles={BUTTON_STYLES}
              />
            </>
          ) : null}

          {current.id === "sources" ? (
            <>
              <p className="text-pretty text-sm text-muted-foreground">
                {tx(
                  "wizard.sourcesHint",
                  "Live sources run without a key. Official APIs run only if you enable them and store a key. Closed boards stay official open plus paste. Never scrape LinkedIn.",
                )}
              </p>
              <div>
                <h3 className="text-sm font-semibold">{tx("wizard.liveSources", "Ready now")}</h3>
                <ul className="mt-3 grid gap-2">
                  {liveSources.map((row) => {
                    const live = liveSources.map((item) => item.id);
                    const on = careerLiveSourceOn(sourceIds, live, row.id);
                    return (
                    <li key={row.id}>
                      <button
                        type="button"
                        onClick={() =>
                          setSourceIds((current) => toggleCareerLiveSource(current, live, row.id))
                        }
                        className={cn(
                          "flex w-full items-center justify-between gap-3 rounded-xl px-4 py-3 text-left outline outline-1",
                          on
                            ? "bg-emerald-600/10 outline-emerald-500"
                            : "outline-black/10 dark:outline-white/10",
                        )}
                      >
                        <span className="text-sm font-medium">{row.name}</span>
                        <span className="text-xs text-emerald-700 dark:text-emerald-300">
                          {on ? tx("stackLive", "Live") : tx("wizard.optional", "Optional")}
                        </span>
                      </button>
                    </li>
                    );
                  })}
                </ul>
              </div>
              <div>
                <h3 className="text-sm font-semibold">{tx("wizard.apiSources", "Official APIs (your keys)")}</h3>
                <p className="mt-2 text-pretty text-sm text-muted-foreground">
                  {tx(
                    "wizard.apiSourcesHint",
                    "Turn a source on, then paste the key. Search uses it only when it is on and a key is stored. Env variables still count.",
                  )}
                </p>
                <ul className="mt-3 grid gap-3">
                  {CAREER_API_CONNECTORS.map((meta) => {
                    const row = catalog.find((item) => item.id === meta.id);
                    const on = sourceIds.includes(meta.id);
                    const ready = Boolean(apiKeys[meta.id as keyof typeof apiKeys]);
                    return (
                      <li
                        key={meta.id}
                        className={cn(
                          "grid gap-3 rounded-xl px-4 py-3 outline outline-1",
                          on ? "bg-indigo-600/10 outline-indigo-500" : "outline-black/10 dark:outline-white/10",
                        )}
                        data-testid={`career-wizard-api-${meta.id}`}
                      >
                        <button
                          type="button"
                          onClick={() =>
                            setSourceIds((current) =>
                              current.includes(meta.id)
                                ? current.filter((item) => item !== meta.id)
                                : [...current, meta.id],
                            )
                          }
                          className="flex w-full items-start justify-between gap-3 text-left"
                        >
                          <span>
                            <span className="block text-sm font-medium">{row?.name || meta.id}</span>
                            <span className="mt-1 block text-xs text-muted-foreground">
                              {row?.notes || meta.env.join(" + ")}
                            </span>
                          </span>
                          <span className="shrink-0 text-xs">
                            {ready
                              ? tx("wizard.keyLive", "Live (key OK)")
                              : on
                                ? tx("wizard.keyMissing", "Key required")
                                : tx("wizard.optional", "Optional")}
                          </span>
                        </button>
                        {on ? (
                          <div className="grid gap-3">
                            {meta.env.map((env) => (
                              <TextField
                                key={env}
                                label={envLabel(env)}
                                type="password"
                                autoComplete="off"
                                value={apiDrafts[env] || ""}
                                description={
                                  ready
                                    ? tx("wizard.keyOnFile", "A key is already on file. Leave empty to keep it.")
                                    : env
                                }
                                onChange={(_, v) => setApiDrafts((current) => ({ ...current, [env]: v || "" }))}
                                onBlur={() => saveApiField(env, apiDrafts[env] || "")}
                              />
                            ))}
                            <DefaultButton
                              text={tx("wizard.openDocs", "Get a key")}
                              iconProps={{ iconName: "NavigateExternalInline" }}
                              onClick={() => openOfficialCareerUrl(token || "", row?.url || meta.docs)}
                              styles={BUTTON_STYLES}
                            />
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </div>
              <div>
                <h3 className="text-sm font-semibold">{tx("wizard.authSources", "Needs a key or official tab")}</h3>
                <ul className="mt-3 grid gap-2">
                  {keyedSources.map((row) => {
                    const hint =
                      row.id === "linkedin"
                        ? tx("wizard.linkedinHint", "Open official, then paste")
                        : row.id === "malt"
                          ? tx("wizard.maltHint", "Open official. No scrape.")
                          : row.id === "france-travail"
                            ? tx(
                                "wizard.franceTravailHint",
                                "Public search URL, no key. Partner API stays closed unless you are a recognised partner.",
                              )
                            : row.notes || "";
                    return { id: row.id, name: row.name, hint };
                  }).map((row) => {
                    const on = sourceIds.includes(row.id);
                    return (
                      <li key={row.id}>
                        <button
                          type="button"
                          onClick={() =>
                            setSourceIds((current) =>
                              current.includes(row.id)
                                ? current.filter((item) => item !== row.id)
                                : [...current, row.id],
                            )
                          }
                          className={cn(
                            "flex w-full items-start justify-between gap-3 rounded-xl px-4 py-3 text-left outline outline-1",
                            on
                              ? "bg-indigo-600/10 outline-indigo-500"
                              : "outline-black/10 dark:outline-white/10",
                          )}
                        >
                          <span>
                            <span className="block text-sm font-medium">{row.name}</span>
                            <span className="mt-1 block text-xs text-muted-foreground">{row.hint}</span>
                          </span>
                          <span className="shrink-0 text-xs">{on ? tx("wizard.watched", "Watched") : tx("wizard.optional", "Optional")}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
              <PrimaryButton
                text={tx("wizard.finish", "Start the search desk")}
                disabled={busy}
                onClick={() => void finish()}
                styles={BUTTON_STYLES}
              />
            </>
          ) : null}

          <div className="mt-2 flex flex-wrap items-center gap-3 border-t border-black/5 pt-5 dark:border-white/10">
            {stepIndex > 0 ? (
              <DefaultButton text={tx("wizardBack", "Back")} onClick={goBack} styles={BUTTON_STYLES} />
            ) : null}
            {current.id !== "sources" ? (
              <PrimaryButton
                text={tx("continue", "Continue")}
                disabled={!canContinue}
                onClick={goNext}
                styles={BUTTON_STYLES}
              />
            ) : null}
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
