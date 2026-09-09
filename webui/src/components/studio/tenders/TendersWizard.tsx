// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChoiceGroup, DefaultButton, TextField, Toggle } from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { CrmPhoneField } from "@/components/crm/CrmPhoneField";
import { SearchableSelect } from "@/components/crm/SearchableSelect";
import {
  BUTTON_STYLES,
  CHANNELS_HASH,
  OfficialLink,
  SPRING,
  Surface,
  TagList,
  openInIdeHash,
  type Tx,
} from "@/components/studio/tenders/tenders-ui";
import { NeedPicker } from "@/components/studio/tenders/wizard/NeedPicker";
import { Block, FileLibrary, WizardFooter, WizardStepper } from "@/components/studio/tenders/wizard/WizardChrome";
import { filterSourcesByQuery, groupSourcesByZone } from "@/components/studio/tenders/wizard/group-sources";
import { pinElementToScrollStart } from "@/components/studio/tenders/wizard/pin-zone";
import { SourcesTable } from "@/components/studio/tenders/wizard/SourcesTable";
import {
  CRM_LOCALES,
  countryOptions,
  currencyOptions,
  normalizeCountryValue,
} from "@/lib/crm-catalog";
import {
  DEFAULT_ARCHIVE_AFTER_DAYS,
  DEFAULT_DELETE_AFTER_DAYS,
  retentionProfilePayload,
} from "@/components/studio/tenders/retention";
import {
  asTemplateFiles,
  fileToBase64,
  officialTenderHref,
  type TenderCustomSource,
  type TenderDesk,
  type TenderPartner,
  type TenderProfile,
  type TenderSite,
} from "@/lib/tenders-api";

const STEPS = [
  { id: 1, key: "company", icon: "CityNext" },
  { id: 2, key: "sites", icon: "MapPin" },
  { id: 3, key: "templates", icon: "TextDocument" },
  { id: 4, key: "references", icon: "Work" },
  { id: 5, key: "needs", icon: "Filter" },
  { id: 6, key: "sources", icon: "Globe" },
  { id: 7, key: "channels", icon: "Chat" },
] as const;

type ChannelDraft = NonNullable<TenderProfile["channels"]>;

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function FieldLabel({ children }: { children: string }) {
  return <p className="mb-1.5 text-[12px] font-medium text-foreground">{children}</p>;
}

function TokenField({
  label,
  value,
  onChange,
  onAdd,
  tags,
  onRemove,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onAdd: () => void;
  tags: string[];
  onRemove: (value: string) => void;
  placeholder: string;
}) {
  return (
    <div>
      <TextField
        label={label}
        value={value}
        placeholder={placeholder}
        onChange={(_, next) => onChange(next || "")}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            onAdd();
          }
        }}
      />
      <div className="mt-2">
        <TagList values={tags} onRemove={onRemove} />
      </div>
    </div>
  );
}

export function TendersWizard({
  desk,
  tx,
  busy,
  token,
  onSave,
  onUpload,
  onRemoveFile,
  onAddReference,
  onCustomSource,
  onSecret,
  onTest,
  onPreviewFile,
}: {
  desk: TenderDesk;
  tx: Tx;
  busy: string;
  token: string;
  onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onUpload: (body: Record<string, unknown>) => Promise<boolean>;
  onRemoveFile: (body: Record<string, unknown>) => Promise<boolean>;
  onAddReference: (body: Record<string, unknown>) => Promise<boolean>;
  onCustomSource: (body: Record<string, unknown>) => Promise<boolean>;
  onSecret: (name: string, value: string) => Promise<boolean>;
  onTest: () => Promise<boolean>;
  onPreviewFile?: (fileId: string) => Promise<string>;
}) {
  const { t, i18n } = useTranslation();
  const reduceMotion = useReducedMotion();
  const profile = desk.profile;
  const [step, setStep] = useState(Math.min(7, Math.max(1, profile.wizard_step || 1)));
  const [name, setName] = useState(profile.name || "");
  const [legalName, setLegalName] = useState(profile.legal_name || "");
  const [specialty, setSpecialty] = useState(profile.specialty || "");
  const [methodology, setMethodology] = useState(profile.methodology || "");
  const [legalClauses, setLegalClauses] = useState(profile.legal_clauses || "");
  const [teamText, setTeamText] = useState(
    (profile.team || [])
      .map((row) =>
        [String(row.name || row.role || ""), String(row.role || row.title || "")].filter(Boolean).join(" | "),
      )
      .filter(Boolean)
      .join("\n"),
  );
  const [priceBookText, setPriceBookText] = useState(
    (profile.price_book || [])
      .map((row) =>
        [String(row.item || row.name || ""), String(row.amount || row.price || "")].filter(Boolean).join(" | "),
      )
      .filter(Boolean)
      .join("\n"),
  );
  const [strengths, setStrengths] = useState<string[]>(profile.strengths || []);
  const [strengthDraft, setStrengthDraft] = useState("");
  const [country, setCountry] = useState(normalizeCountryValue(profile.country || ""));
  const [phone, setPhone] = useState(profile.phone || "");
  const [email, setEmail] = useState(profile.email || "");
  const [website, setWebsite] = useState(profile.website || "");
  const [currency, setCurrency] = useState(profile.currency || "EUR");
  const [locale, setLocale] = useState(profile.locale || "fr-FR");
  const [hq, setHq] = useState<TenderSite>(
    profile.sites?.find((row) => row.kind === "hq") || {
      id: "hq",
      kind: "hq",
      name: "",
      country: profile.country || "",
      address: "",
      city: "",
      headcount: 0,
    },
  );
  const [branches, setBranches] = useState<TenderSite[]>(
    (profile.sites || []).filter((row) => row.kind !== "hq"),
  );
  const [headcount, setHeadcount] = useState(String(profile.headcount || ""));
  const [tenderTypes, setTenderTypes] = useState<string[]>(profile.tender_types || []);
  const [searchCountries, setSearchCountries] = useState<string[]>(profile.countries || []);
  const [countryPick, setCountryPick] = useState("");
  const [crafts, setCrafts] = useState<string[]>(profile.crafts || []);
  const [projectTypes, setProjectTypes] = useState<string[]>(profile.project_types || []);
  const [partners, setPartners] = useState<TenderPartner[]>(profile.partners || []);
  const [minBudget, setMinBudget] = useState(String(profile.min_budget || ""));
  const [maxBudget, setMaxBudget] = useState(String(profile.max_budget || ""));
  const [minDeadline, setMinDeadline] = useState(String(profile.min_deadline_days ?? 10));
  const [minScore, setMinScore] = useState(String(profile.min_score ?? 70));
  const [archiveAfterDays, setArchiveAfterDays] = useState(
    String(profile.archive_after_days ?? desk.retention?.archive_after_days ?? DEFAULT_ARCHIVE_AFTER_DAYS),
  );
  const [deleteAfterDays, setDeleteAfterDays] = useState(
    String(profile.delete_after_days ?? desk.retention?.delete_after_days ?? DEFAULT_DELETE_AFTER_DAYS),
  );
  const [sourceIds, setSourceIds] = useState<string[]>(profile.source_ids || []);
  const [enabledHosts, setEnabledHosts] = useState<string[]>(profile.enabled_sources || []);
  const [sourceQuery, setSourceQuery] = useState("");
  const [openZone, setOpenZone] = useState("");
  const [customName, setCustomName] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customApi, setCustomApi] = useState("");
  const [customCountry, setCustomCountry] = useState("");
  const [customKey, setCustomKey] = useState("");
  const [samKey, setSamKey] = useState("");
  const [sendMode, setSendMode] = useState(profile.send_mode || "approval");
  const [refTitle, setRefTitle] = useState("");
  const [refClient, setRefClient] = useState("");
  const [refYear, setRefYear] = useState("");
  const [refCountry, setRefCountry] = useState("");
  const [refAmount, setRefAmount] = useState("");
  const [channels, setChannels] = useState<ChannelDraft>({
    telegram: Boolean(profile.channels?.telegram),
    whatsapp: Boolean(profile.channels?.whatsapp),
    email: Boolean(profile.channels?.email),
    teams: Boolean(profile.channels?.teams),
    slack: Boolean(profile.channels?.slack),
    telegram_to: String(profile.channels?.telegram_to || ""),
    whatsapp_to: String(profile.channels?.whatsapp_to || ""),
    email_to: String(profile.channels?.email_to || ""),
    teams_to: String(profile.channels?.teams_to || ""),
    slack_to: String(profile.channels?.slack_to || ""),
  });
  const lang = (i18n.language || "fr").slice(0, 2);
  const crmTx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const countries = useMemo(() => countryOptions(lang), [lang]);
  const currencies = useMemo(() => currencyOptions(), []);
  const locales = CRM_LOCALES.map((row) => ({ value: row.code, label: row.label, keywords: row.code }));
  const zones = desk.catalog_by_zone?.length ? desk.catalog_by_zone : groupSourcesByZone(desk.catalog || []);
  const visibleZones = useMemo(
    () =>
      zones
        .map((group) => ({ ...group, sources: filterSourcesByQuery(group.sources, sourceQuery) }))
        .filter((group) => group.sources.length),
    [sourceQuery, zones],
  );
  const zoneSectionRefs = useRef<Record<string, HTMLElement | null>>({});

  useLayoutEffect(() => {
    if (!openZone) return;
    const node = zoneSectionRefs.current[openZone];
    pinElementToScrollStart(node);
    const frame = requestAnimationFrame(() => pinElementToScrollStart(node));
    return () => cancelAnimationFrame(frame);
  }, [openZone]);

  const addToken = (value: string, list: string[], setList: (next: string[]) => void, setDraft: (v: string) => void) => {
    const next = value.trim();
    if (!next || list.some((row) => row.toLowerCase() === next.toLowerCase())) {
      setDraft("");
      return;
    }
    setList([...list, next]);
    setDraft("");
  };

  const sitesPayload = (): TenderSite[] => {
    const rows = [
      { ...hq, id: hq.id || "hq", kind: "hq" as const, name: hq.name || name || legalName || "" },
      ...branches,
    ];
    return rows.filter((row) => row.name || row.address || row.city || row.country);
  };

  const branchHeadcount = branches.reduce((sum, row) => sum + Number(row.headcount || 0), 0);
  const totalHeadcount = Number(headcount) || Number(hq.headcount || 0) + branchHeadcount;

  const persist = (extra: Record<string, unknown> = {}) =>
    onSave({
      name,
      legal_name: legalName,
      specialty,
      strengths,
      country,
      phone,
      email,
      website,
      currency,
      locale,
      countries: searchCountries,
      crafts,
      tender_types: tenderTypes,
      project_types: projectTypes,
      partners,
      sites: sitesPayload(),
      headcount: totalHeadcount,
      min_budget: Number(minBudget) || 0,
      max_budget: Number(maxBudget) || 0,
      min_deadline_days: Number(minDeadline) || 0,
      min_score: Number(minScore) || 70,
      ...retentionProfilePayload({
        archive_after_days: archiveAfterDays,
        delete_after_days: deleteAfterDays,
      }),
      source_ids: sourceIds,
      enabled_sources: enabledHosts,
      methodology,
      legal_clauses: legalClauses,
      team: teamText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const [name, role] = line.split("|").map((part) => part.trim());
          return { name: name || "", role: role || "" };
        }),
      price_book: priceBookText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const [item, amount] = line.split("|").map((part) => part.trim());
          return { item: item || "", amount: amount || "" };
        }),
      send_mode: sendMode,
      channels,
      wizard_step: step,
      ...extra,
    });

  const saveSamIfNeeded = () => {
    if (samKey.trim()) void onSecret("sam_gov", samKey.trim());
  };

  const goTo = (next: number, extra: Record<string, unknown> = {}) => {
    if (step === 6) saveSamIfNeeded();
    const target = Math.min(7, Math.max(1, next));
    setStep(target);
    void persist({ wizard_step: target, ...extra });
  };

  const finish = () => persist({ wizard_step: 7, wizard_complete: true });

  const pickFiles = async (kind: string, accept: string, meta: Record<string, unknown> = {}) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = true;
    input.onchange = () => {
      const files = Array.from(input.files || []);
      if (!files.length) return;
      void (async () => {
        for (const file of files) {
          const data = await fileToBase64(file);
          const ok = await onUpload({ kind, name: file.name, data, ...meta });
          if (!ok) break;
        }
      })();
    };
    input.click();
  };

  const removeFiled = (row: { file_id?: string; title?: string; name?: string }) => {
    void onRemoveFile(row.file_id ? { id: row.file_id } : { title: row.title || row.name || "" });
  };

  const wordFiles = asTemplateFiles(profile.templates?.word);
  const pptFiles = asTemplateFiles(profile.templates?.ppt);
  const slideFiles = asTemplateFiles(profile.templates?.reuse_slides);
  const referenceFiles = asTemplateFiles(profile.references);

  const zoneLabel = (zone: string) =>
    tx(`zone.${zone}`, zone === "gcc" ? "Gulf" : zone.charAt(0).toUpperCase() + zone.slice(1));

  const canContinue =
    step === 1
      ? Boolean(name.trim() && specialty.trim() && country && currency)
      : step === 5
        ? Boolean(searchCountries.length && crafts.length)
        : step === 6
          ? Boolean(sourceIds.length || (profile.custom_sources || []).length)
          : true;

  const saving = Boolean(busy);
  const current = STEPS[step - 1];

  return (
    <div className="mx-auto grid w-full max-w-2xl gap-6">
      <div>
        <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-emerald-800 dark:text-emerald-200">
          {tx("wizardKicker", "Company setup")}
        </p>
        <h2 className="mt-2 text-balance text-2xl font-semibold">
          {tx(`wizard.${current.key}Title`, current.key)}
        </h2>
        <p className="mt-1 text-pretty text-sm text-muted-foreground">
          {tx(`wizard.${current.key}Lead`, tx("wizardStepOf", "Step {{current}} of {{total}}", { current: step, total: 7 }))}
        </p>
      </div>
      <WizardStepper steps={STEPS} current={step} tx={tx} onSelect={(id) => goTo(id)} />

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={step}
          initial={reduceMotion ? false : { opacity: 0, y: 10, filter: "blur(4px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={reduceMotion ? undefined : { opacity: 0, y: -8, filter: "blur(4px)" }}
          transition={SPRING}
        >
          <Surface className="grid gap-5 p-6 sm:p-8">
            {step === 1 ? (
              <>
                <TextField label={tx("company", "Company")} value={name} onChange={(_, v) => setName(v || "")} required />
                <TextField label={tx("legalName", "Legal name")} value={legalName} onChange={(_, v) => setLegalName(v || "")} />
                <TextField
                  label={tx("specialty", "Specialty")}
                  value={specialty}
                  onChange={(_, v) => setSpecialty(v || "")}
                  required
                />
                <TextField
                  label={tx("methodology", "How you deliver")}
                  value={methodology}
                  multiline
                  rows={4}
                  placeholder={tx(
                    "methodologyHint",
                    "Method, team habits, price-book notes. The writer reuses this on every dossier.",
                  )}
                  onChange={(_, v) => setMethodology(v || "")}
                />
                <TextField
                  label={tx("teamOnFile", "Team on file")}
                  value={teamText}
                  multiline
                  rows={3}
                  placeholder={tx("teamHint", "One person per line: Name | Role")}
                  onChange={(_, v) => setTeamText(v || "")}
                />
                <TextField
                  label={tx("priceBook", "Price book")}
                  value={priceBookText}
                  multiline
                  rows={3}
                  placeholder={tx("priceBookHint", "One line: Item | amount. Empty stays not on file.")}
                  onChange={(_, v) => setPriceBookText(v || "")}
                />
                <TextField
                  label={tx("legalClauses", "Legal clauses")}
                  value={legalClauses}
                  multiline
                  rows={3}
                  placeholder={tx("legalHint", "Clauses already on file. Never invented.")}
                  onChange={(_, v) => setLegalClauses(v || "")}
                />
                <TokenField
                  label={tx("strengths", "Strengths")}
                  value={strengthDraft}
                  placeholder={tx("addThenEnter", "Type a strength, then Enter")}
                  onChange={setStrengthDraft}
                  onAdd={() => addToken(strengthDraft, strengths, setStrengths, setStrengthDraft)}
                  tags={strengths}
                  onRemove={(value) => setStrengths(strengths.filter((row) => row !== value))}
                />
                <div>
                  <FieldLabel>{tx("homeCountry", "Company country")}</FieldLabel>
                  <SearchableSelect
                    value={country}
                    options={countries}
                    onChange={(value) => setCountry(normalizeCountryValue(value))}
                    placeholder={tx("crm.searchCountry", "Search a country")}
                    searchPlaceholder={tx("crm.searchCountry", "Search a country")}
                    emptyLabel={tx("crm.noResults", "No results")}
                  />
                </div>
                <div>
                  <FieldLabel>{tx("phone", "Phone")}</FieldLabel>
                  <CrmPhoneField
                    value={phone}
                    onChange={setPhone}
                    defaultCountry={country || "FR"}
                    locale={lang}
                    tx={crmTx}
                  />
                </div>
                <TextField label={tx("email", "Email")} value={email} onChange={(_, v) => setEmail(v || "")} />
                <TextField label={tx("website", "Website")} value={website} onChange={(_, v) => setWebsite(v || "")} />
                {officialTenderHref(website) ? (
                  <OfficialLink href={website} token={token} className="text-sm text-teal-700 underline underline-offset-2 dark:text-teal-300">
                    {tx("openWebsite", "Open company site")}
                  </OfficialLink>
                ) : null}
                <div>
                  <FieldLabel>{tx("currency", "Currency")}</FieldLabel>
                  <SearchableSelect
                    value={currency}
                    options={currencies}
                    onChange={setCurrency}
                    placeholder={tx("crm.searchCurrency", "Search a currency")}
                    searchPlaceholder={tx("crm.searchCurrency", "Search a currency")}
                    emptyLabel={tx("crm.noResults", "No results")}
                  />
                </div>
                <div>
                  <FieldLabel>{tx("locale", "Number format")}</FieldLabel>
                  <SearchableSelect
                    value={locale}
                    options={locales}
                    onChange={setLocale}
                    placeholder={locale}
                    searchPlaceholder={tx("crm.searchPlaceholder", "Search...")}
                    emptyLabel={tx("crm.noResults", "No results")}
                  />
                </div>
              </>
            ) : null}

            {step === 2 ? (
              <>
                <Block title={tx("hqTitle", "Headquarters")} body={tx("hqLead", "One address for the company seat.")}>
                  <TextField
                    label={tx("hqName", "HQ name")}
                    value={hq.name || ""}
                    onChange={(_, v) => setHq({ ...hq, name: v || "" })}
                    placeholder={name || legalName || tx("hqNameHint", "Same as the company, or a site name")}
                  />
                  <TextField
                    label={tx("address", "Address")}
                    value={hq.address || ""}
                    onChange={(_, v) => setHq({ ...hq, address: v || "" })}
                  />
                  <TextField
                    label={tx("city", "City")}
                    value={hq.city || ""}
                    onChange={(_, v) => setHq({ ...hq, city: v || "" })}
                  />
                  <div>
                    <FieldLabel>{tx("hqCountry", "HQ country")}</FieldLabel>
                    <SearchableSelect
                      value={hq.country || ""}
                      options={countries}
                      onChange={(value) => setHq({ ...hq, country: normalizeCountryValue(value) })}
                      placeholder={tx("crm.searchCountry", "Search a country")}
                      searchPlaceholder={tx("crm.searchCountry", "Search a country")}
                      emptyLabel={tx("crm.noResults", "No results")}
                    />
                  </div>
                  <TextField
                    label={tx("hqHeadcount", "HQ headcount")}
                    value={String(hq.headcount || "")}
                    onChange={(_, v) => setHq({ ...hq, headcount: Number(v || 0) })}
                  />
                </Block>
                <Block
                  title={tx("branchesTitle", "Subsidiaries")}
                  body={tx("branchesLead", "Country, address and headcount for each office.")}
                >
                  {branches.map((row, index) => (
                    <div key={row.id || index} className="grid gap-3 rounded-xl bg-background/70 p-3">
                      <TextField
                        label={tx("branchName", "Subsidiary")}
                        value={row.name || ""}
                        onChange={(_, v) => {
                          const next = [...branches];
                          next[index] = { ...row, name: v || "" };
                          setBranches(next);
                        }}
                      />
                      <TextField
                        label={tx("address", "Address")}
                        value={row.address || ""}
                        onChange={(_, v) => {
                          const next = [...branches];
                          next[index] = { ...row, address: v || "" };
                          setBranches(next);
                        }}
                      />
                      <div>
                        <FieldLabel>{tx("homeCountry", "Company country")}</FieldLabel>
                        <SearchableSelect
                          value={row.country || ""}
                          options={countries}
                          onChange={(value) => {
                            const next = [...branches];
                            next[index] = { ...row, country: normalizeCountryValue(value) };
                            setBranches(next);
                          }}
                          placeholder={tx("crm.searchCountry", "Search a country")}
                          searchPlaceholder={tx("crm.searchCountry", "Search a country")}
                          emptyLabel={tx("crm.noResults", "No results")}
                        />
                      </div>
                      <TextField
                        label={tx("branchHeadcount", "Headcount")}
                        value={String(row.headcount || "")}
                        onChange={(_, v) => {
                          const next = [...branches];
                          next[index] = { ...row, headcount: Number(v || 0) };
                          setBranches(next);
                        }}
                      />
                      <DefaultButton
                        text={tx("removeBranch", "Remove this subsidiary")}
                        onClick={() => setBranches(branches.filter((_, i) => i !== index))}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                  ))}
                  <DefaultButton
                    text={tx("addBranch", "Add a subsidiary")}
                    onClick={() =>
                      setBranches([
                        ...branches,
                        { id: newId(), kind: "branch", name: "", country: "", address: "", city: "", headcount: 0 },
                      ])
                    }
                    styles={BUTTON_STYLES}
                  />
                  <TextField
                    label={tx("totalHeadcount", "Total headcount")}
                    value={headcount || String(totalHeadcount || "")}
                    onChange={(_, v) => setHeadcount(v || "")}
                  />
                </Block>
              </>
            ) : null}

            {step === 3 ? (
              <>
                <p className="text-pretty text-sm text-muted-foreground">
                  {tx(
                    "styleRule",
                    "If a file is on disk, write reuses it. The agent never invents a page.",
                  )}
                </p>
                <FileLibrary
                  icon="TextDocument"
                  title={tx("wordModel", "Word response model")}
                  files={wordFiles}
                  emptyLabel={tx("noneOnFile", "not on file")}
                  action={tx("importWord", "Import Word models")}
                  onImport={() => void pickFiles("word_template", ".docx")}
                  onRemove={removeFiled}
                  onPreview={onPreviewFile}
                  tx={tx}
                />
                <FileLibrary
                  icon="PowerPointDocument"
                  title={tx("pptModel", "PowerPoint offer model")}
                  files={pptFiles}
                  emptyLabel={tx("noneOnFile", "not on file")}
                  action={tx("importPpt", "Import PowerPoint models")}
                  onImport={() => void pickFiles("ppt_template", ".pptx")}
                  onRemove={removeFiled}
                  onPreview={onPreviewFile}
                  tx={tx}
                />
                <FileLibrary
                  icon="Page"
                  title={tx("reuseSlides", "Slides to reuse every time")}
                  files={slideFiles}
                  emptyLabel={tx("noneOnFile", "not on file")}
                  action={tx("importSlide", "Import reuse slides")}
                  onImport={() => void pickFiles("reuse_slide", ".pptx,.pdf,.docx")}
                  onRemove={removeFiled}
                  onPreview={onPreviewFile}
                  tx={tx}
                />
              </>
            ) : null}

            {step === 4 ? (
              <>
                <p className="text-pretty text-sm text-muted-foreground">
                  {tx(
                    "refsLead",
                    "Add a reference by hand, or import one or more PPT, PDF or Word files. Write only uses what you file.",
                  )}
                </p>
                <FileLibrary
                  icon="Work"
                  title={tx("wizard.references", "References")}
                  files={referenceFiles}
                  emptyLabel={tx("noRefs", "No references on file yet.")}
                  action={tx("importRef", "Import PPT, PDF or Word")}
                  onImport={() =>
                    void pickFiles("reference", ".pptx,.pdf,.docx", {
                      title: refTitle,
                      client: refClient,
                      year: refYear,
                      country: refCountry,
                      amount: Number(refAmount) || null,
                    })
                  }
                  onRemove={removeFiled}
                  onPreview={onPreviewFile}
                  tx={tx}
                />
                <TextField label={tx("refTitle", "Project title")} value={refTitle} onChange={(_, v) => setRefTitle(v || "")} />
                <TextField label={tx("refClient", "Client")} value={refClient} onChange={(_, v) => setRefClient(v || "")} />
                <TextField label={tx("refYear", "Year")} value={refYear} onChange={(_, v) => setRefYear(v || "")} />
                <div>
                  <FieldLabel>{tx("refCountry", "Country")}</FieldLabel>
                  <SearchableSelect
                    value={refCountry}
                    options={countries}
                    onChange={(value) => setRefCountry(normalizeCountryValue(value))}
                    placeholder={tx("crm.searchCountry", "Search a country")}
                    searchPlaceholder={tx("crm.searchCountry", "Search a country")}
                    emptyLabel={tx("crm.noResults", "No results")}
                    allowEmpty
                  />
                </div>
                <TextField label={tx("refAmount", "Amount")} value={refAmount} onChange={(_, v) => setRefAmount(v || "")} />
                <DefaultButton
                  text={tx("addReference", "Add this reference")}
                  disabled={!refTitle.trim()}
                  onClick={() => {
                    void onAddReference({
                      title: refTitle,
                      client: refClient,
                      year: refYear,
                      country: refCountry,
                      amount: Number(refAmount) || null,
                    }).then((ok) => {
                      if (!ok) return;
                      setRefTitle("");
                      setRefClient("");
                      setRefYear("");
                      setRefCountry("");
                      setRefAmount("");
                    });
                  }}
                  styles={BUTTON_STYLES}
                />
              </>
            ) : null}

            {step === 5 ? (
              <>
                <Block
                  title={tx("needsWhat", "What you look for")}
                  body={tx(
                    "needsWhatLead",
                    "Official TED / BOAMP types and CPV domains. Pick several, or type your own. No invented crafts.",
                  )}
                >
                  <p className="text-[12px] text-muted-foreground">
                    {tx(
                      "needsCatalogHint",
                      "Lists come from public tender portals. Each field accepts several values.",
                    )}
                  </p>
                  <NeedPicker
                    label={tx("tenderTypes", "Tender types")}
                    catalog={desk.needs_catalog?.tender_types || []}
                    locale={lang}
                    tags={tenderTypes}
                    onChange={setTenderTypes}
                    tx={tx}
                  />
                  <NeedPicker
                    label={tx("crafts", "Domains")}
                    catalog={desk.needs_catalog?.domains || []}
                    locale={lang}
                    tags={crafts}
                    onChange={setCrafts}
                    tx={tx}
                  />
                  <NeedPicker
                    label={tx("projectTypes", "Completed project types")}
                    catalog={desk.needs_catalog?.project_types || []}
                    locale={lang}
                    tags={projectTypes}
                    onChange={setProjectTypes}
                    tx={tx}
                  />
                </Block>
                <Block title={tx("needsWhere", "Where you cover")} body={tx("needsWhereLead", "Countries the collect will search.")}>
                  <SearchableSelect
                    value={countryPick}
                    options={countries}
                    onChange={(value) => {
                      const iso = normalizeCountryValue(value);
                      setCountryPick("");
                      if (iso && !searchCountries.includes(iso)) setSearchCountries([...searchCountries, iso]);
                    }}
                    placeholder={tx("crm.searchCountry", "Search a country")}
                    searchPlaceholder={tx("crm.searchCountry", "Search a country")}
                    emptyLabel={tx("crm.noResults", "No results")}
                  />
                  <TagList
                    values={searchCountries}
                    onRemove={(value) => setSearchCountries(searchCountries.filter((row) => row !== value))}
                  />
                </Block>
                <Block title={tx("needsEnvelope", "Envelope and partners")} body={tx("needsEnvelopeLead", "Budget range and integrators in those countries.")}>
                  {partners.map((row, index) => (
                    <div key={`${row.name}-${index}`} className="grid gap-3 rounded-xl bg-background/70 p-3">
                      <TextField
                        label={tx("partnerName", "Partner or integrator")}
                        value={row.name || ""}
                        onChange={(_, v) => {
                          const next = [...partners];
                          next[index] = { ...row, name: v || "" };
                          setPartners(next);
                        }}
                      />
                      <TextField
                        label={tx("partnerRole", "Role")}
                        value={row.role || ""}
                        onChange={(_, v) => {
                          const next = [...partners];
                          next[index] = { ...row, role: v || "" };
                          setPartners(next);
                        }}
                      />
                      <div>
                        <FieldLabel>{tx("partnerCountry", "Country")}</FieldLabel>
                        <SearchableSelect
                          value={row.country || ""}
                          options={countries}
                          onChange={(value) => {
                            const next = [...partners];
                            next[index] = { ...row, country: normalizeCountryValue(value) };
                            setPartners(next);
                          }}
                          placeholder={tx("crm.searchCountry", "Search a country")}
                          searchPlaceholder={tx("crm.searchCountry", "Search a country")}
                          emptyLabel={tx("crm.noResults", "No results")}
                          allowEmpty
                        />
                      </div>
                      <DefaultButton
                        text={tx("removePartner", "Remove this partner")}
                        onClick={() => setPartners(partners.filter((_, i) => i !== index))}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                  ))}
                  <DefaultButton
                    text={tx("addPartner", "Add a partner")}
                    onClick={() => setPartners([...partners, { name: "", country: "", role: "" }])}
                    styles={BUTTON_STYLES}
                  />
                  <TextField label={tx("minBudget", "Min budget")} value={minBudget} onChange={(_, v) => setMinBudget(v || "")} />
                  <TextField label={tx("maxBudget", "Max budget")} value={maxBudget} onChange={(_, v) => setMaxBudget(v || "")} />
                  <TextField
                    label={tx("minDeadline", "Min days")}
                    value={minDeadline}
                    onChange={(_, v) => setMinDeadline(v || "")}
                  />
                  <TextField label={tx("minScore", "Min score")} value={minScore} onChange={(_, v) => setMinScore(v || "")} />
                </Block>
                <Block
                  title={tx("retentionTitle", "Archive and delete")}
                  body={tx(
                    "retentionLead",
                    "Counted from the day the notice arrives. The desktop app uses the same file as this page.",
                  )}
                >
                  <TextField
                    label={tx("archiveAfterDays", "Archive after (days)")}
                    value={archiveAfterDays}
                    onChange={(_, v) => setArchiveAfterDays(v || "")}
                  />
                  <TextField
                    label={tx("deleteAfterDays", "Delete after (days)")}
                    value={deleteAfterDays}
                    onChange={(_, v) => setDeleteAfterDays(v || "")}
                  />
                </Block>
              </>
            ) : null}

            {step === 6 ? (
              <>
                <p className="text-pretty text-sm text-muted-foreground">
                  {tx(
                    "sourcesHint",
                    "API and open data first. Official scrape next. No paid aggregator. TED already covers EU-threshold notices; national portals add the rest.",
                  )}
                </p>
                <p className="text-[13px] text-muted-foreground">
                  {tx("sourcesPicked", "{{count}} sources selected", { count: sourceIds.length })}
                </p>
                {enabledHosts.length ? (
                  <Block
                    title={tx("keptHosts", "Hosts you kept")}
                    body={tx(
                      "keptHostsLead",
                      "Collect found these official portals. Remove a host to stop using it on the next hunt.",
                    )}
                  >
                    <TagList values={enabledHosts} onRemove={(host) => setEnabledHosts((current) => current.filter((item) => item !== host))} />
                  </Block>
                ) : null}
                <TextField
                  label={tx("searchSources", "Search a portal")}
                  value={sourceQuery}
                  placeholder={tx("searchSourcesHint", "TED, BOAMP, SAM, country...")}
                  onChange={(_, v) => setSourceQuery(v || "")}
                />
                <div className="grid gap-3 [overflow-anchor:none]">
                  {visibleZones.map((group) => {
                  const open = group.zone === openZone;
                  const ids = group.sources.map((row) => row.id);
                  const selected = ids.filter((id) => sourceIds.includes(id)).length;
                  return (
                    <section
                      key={group.zone}
                      ref={(node) => {
                        zoneSectionRefs.current[group.zone] = node;
                      }}
                      className="rounded-2xl bg-muted/25"
                    >
                      <button
                        type="button"
                        data-zone-header
                        className="flex min-h-12 w-full cursor-pointer items-center justify-between gap-3 px-4 py-3 text-left active:scale-[0.99]"
                        onClick={() => setOpenZone(open ? "" : group.zone)}
                        aria-expanded={open}
                      >
                        <span className="font-semibold">{zoneLabel(group.zone)}</span>
                        <span className="tabular-nums text-[12px] text-muted-foreground">
                          {selected}/{ids.length}
                        </span>
                      </button>
                      {open ? (
                        <div className="grid gap-2 border-t border-black/5 px-4 py-3 dark:border-white/10">
                          <DefaultButton
                            text={
                              selected === ids.length
                                ? tx("clearZone", "Clear this region")
                                : tx("selectZone", "Select this region")
                            }
                            onClick={() =>
                              setSourceIds(
                                selected === ids.length
                                  ? sourceIds.filter((id) => !ids.includes(id))
                                  : [...new Set([...sourceIds, ...ids])],
                              )
                            }
                            styles={BUTTON_STYLES}
                          />
                          <SourcesTable
                            rows={group.sources}
                            selected={sourceIds}
                            token={token}
                            tx={tx}
                            onToggle={(id, checked) =>
                              setSourceIds(checked ? [...sourceIds, id] : sourceIds.filter((sid) => sid !== id))
                            }
                          />
                        </div>
                      ) : null}
                    </section>
                  );
                })}
                </div>
                {desk.catalog.some((row) => row.id === "sam-gov") ? (
                  <TextField
                    label={tx("samKey", "SAM.gov API key")}
                    value={samKey}
                    type="password"
                    description={
                      profile.has_sam_key
                        ? tx("samKeyOnFile", "A key is already on file. Leave empty to keep it.")
                        : tx("samKeyHint", "Stored locally. Never sent back in the snapshot.")
                    }
                    onChange={(_, v) => setSamKey(v || "")}
                    onBlur={saveSamIfNeeded}
                  />
                ) : null}
                <Block title={tx("customSource", "Add an API source")} body={tx("customLead", "Your endpoint. We only store what you type.")}>
                  <TextField label={tx("customName", "Name")} value={customName} onChange={(_, v) => setCustomName(v || "")} />
                  <TextField label={tx("customUrl", "Portal URL")} value={customUrl} onChange={(_, v) => setCustomUrl(v || "")} />
                  <TextField label={tx("customApi", "API endpoint")} value={customApi} onChange={(_, v) => setCustomApi(v || "")} />
                  <div>
                    <FieldLabel>{tx("customCountry", "Country")}</FieldLabel>
                    <SearchableSelect
                      value={customCountry}
                      options={countries}
                      onChange={(value) => setCustomCountry(normalizeCountryValue(value))}
                      placeholder={tx("crm.searchCountry", "Search a country")}
                      searchPlaceholder={tx("crm.searchCountry", "Search a country")}
                      emptyLabel={tx("crm.noResults", "No results")}
                      allowEmpty
                    />
                  </div>
                  <TextField
                    label={tx("customKey", "API key (optional)")}
                    type="password"
                    value={customKey}
                    onChange={(_, v) => setCustomKey(v || "")}
                  />
                  <DefaultButton
                    text={tx("addCustom", "Add this source")}
                    disabled={!customName.trim() || !customUrl.trim()}
                    onClick={() => {
                      void onCustomSource({
                        name: customName,
                        url: customUrl,
                        api: customApi,
                        country: customCountry,
                        zone: "international",
                        ingest: customApi.trim() ? "api" : "html",
                        api_key: customKey,
                      }).then((ok) => {
                        if (!ok) return;
                        setCustomName("");
                        setCustomUrl("");
                        setCustomApi("");
                        setCustomKey("");
                      });
                    }}
                    styles={BUTTON_STYLES}
                  />
                  {(profile.custom_sources || []).map((row: TenderCustomSource) => (
                    <div key={row.id} className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm text-muted-foreground">
                        {row.name} · {row.country} {row.has_key ? `· ${tx("keyOnFile", "key on file")}` : ""}
                        {row.url && officialTenderHref(row.url) ? (
                          <>
                            {" · "}
                            <OfficialLink
                              href={row.url}
                              token={token}
                              className="text-teal-700 underline underline-offset-2 dark:text-teal-300"
                            >
                              {row.url.replace(/^https:\/\/(www\.)?/, "")}
                            </OfficialLink>
                          </>
                        ) : null}
                      </p>
                      <DefaultButton
                        text={tx("removeCustom", "Remove")}
                        onClick={() => void onCustomSource({ remove: true, id: row.id })}
                        styles={BUTTON_STYLES}
                      />
                    </div>
                  ))}
                </Block>
                <Block
                  title={tx("mcpWizardTitle", "Buyer research (optional)")}
                  body={tx(
                    "mcpWizardBody",
                    "LinkedIn MCP (stickerdaniel/linkedin-mcp-server) is the recommended option for the buyer company, contacts, posts and inbox. Not a notice source. Job tools stay hiring context. Enable it in Settings > Tools > Tenders MCP, then sign in on first use (or uvx mcp-server-linkedin@latest --login). Connection requests and messages need your confirmation.",
                  )}
                >
                  <DefaultButton
                    text={tx("openTendersMcp", "Open Settings > Tools")}
                    onClick={() => openInIdeHash(CHANNELS_HASH)}
                    styles={BUTTON_STYLES}
                  />
                </Block>
              </>
            ) : null}

            {step === 7 ? (
              <>
                <p className="text-pretty text-sm text-muted-foreground">
                  {tx(
                    "channelsHint",
                    "Alerts for Collect, deadlines and mail drafts. The desk never posts a public bid by itself.",
                  )}
                </p>
                <ChoiceGroup
                  label={tx("sendMode", "Send mode")}
                  selectedKey={sendMode}
                  options={(desk.send_modes.length ? desk.send_modes : ["draft", "approval", "autonomous"]).map(
                    (mode) => ({
                      key: mode,
                      text:
                        mode === "draft"
                          ? tx("sendModeDraft", "Draft only - no mail leaves the desk")
                          : mode === "autonomous"
                            ? tx(
                                "sendModeAuto",
                                "Autonomous - last draft may leave without a second click. Still never posts a public bid.",
                              )
                            : tx("sendModeApproval", "Approval before send"),
                    }),
                  )}
                  onChange={(_, option) => setSendMode(String(option?.key || "approval"))}
                />
                {(
                  [
                    ["telegram", "telegram_to", tx("channelTelegram", "Telegram"), "123456789"],
                    ["whatsapp", "whatsapp_to", tx("channelWhatsapp", "WhatsApp"), "+33612345678"],
                    ["email", "email_to", tx("channelEmail", "Email"), "you@example.com"],
                    ["teams", "teams_to", tx("channelTeams", "Microsoft Teams"), "19:meeting@thread.v2"],
                    ["slack", "slack_to", tx("channelSlack", "Slack"), "#tenders"],
                  ] as const
                ).map(([id, dest, label, placeholder]) => {
                  const ready = Boolean(desk.channels?.[id]?.ready);
                  const hint = desk.channels?.[id]?.hint || "";
                  return (
                    <div key={id} className="grid gap-2 rounded-2xl bg-muted/25 px-4 py-3">
                      <Toggle
                        label={label}
                        checked={Boolean(channels[id])}
                        onChange={(_, checked) => setChannels({ ...channels, [id]: Boolean(checked) })}
                      />
                      <p className="text-[12px] text-muted-foreground">
                        {ready
                          ? tx("channelReady", "Channel ready")
                          : hint || tx("channelWait", "Connect this channel in Settings > Channels")}
                      </p>
                      {!ready ? (
                        <DefaultButton
                          text={tx("openChannels", "Open Settings > Channels")}
                          onClick={() => {
                            openInIdeHash(CHANNELS_HASH);
                          }}
                          styles={BUTTON_STYLES}
                        />
                      ) : null}
                      {channels[id] ? (
                        <TextField
                          label={tx(`${id}To`, "Destination")}
                          value={String(channels[dest] || "")}
                          placeholder={placeholder}
                          onChange={(_, value) => setChannels({ ...channels, [dest]: value || "" })}
                        />
                      ) : null}
                    </div>
                  );
                })}
                <DefaultButton
                  text={tx("testNotify", "Send a test alert")}
                  disabled={saving}
                  onClick={() => void onTest()}
                  styles={BUTTON_STYLES}
                />
              </>
            ) : null}

            <WizardFooter
              tx={tx}
              busy={saving}
              canContinue={canContinue}
              continueLabel={
                step === 7 ? tx("finishWizard", "Save and open the desk") : tx("continue", "Continue")
              }
              showBack={step > 1}
              showSkip={step === 2 || step === 3 || step === 4 || step === 7}
              onBack={() => goTo(step - 1)}
              onSkip={() => {
                if (step === 2) goTo(3, { sites_skipped: true });
                else if (step === 3) goTo(4, { templates_skipped: true });
                else if (step === 4) goTo(5, { references_skipped: true });
                else void persist({ wizard_complete: true, channels_skipped: true });
              }}
              onContinue={() => {
                if (step === 6 && samKey.trim()) void onSecret("sam_gov", samKey.trim());
                if (step === 7) void finish();
                else goTo(step + 1);
              }}
            />
          </Surface>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
