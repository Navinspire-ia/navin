// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef, useState } from "react";
import { Checkbox, DefaultButton, Dropdown, Label, Link, MessageBar, MessageBarType, PrimaryButton,
  ProgressIndicator, Stack, TextField } from "@fluentui/react";
import { motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";
import type { CareerDesk } from "@/lib/career-api";
import { DEFAULT_PLATFORM_CATALOG, emptyProspecting, missionSourcesForCountries, platformRelevant, type ProspectCriteria } from "@/lib/career-prospecting";
import { CareerCountryMultiSelect } from "./CareerCountrySelect";
import { CareerMultiSelect } from "./CareerMultiSelect";
import { careerSuggestions } from "@/lib/career-suggestions";
import { localizeCareerValue } from "@/lib/career-role-matrix";
import { careerRoleShares, initializeCompanyCriteria, updateCompanyCriteria } from "@/lib/career-company-autofill";
import { browserTimeZone } from "@/lib/trading-loop-schedule";
import { BUTTON_STYLES, openOfficialCareerUrl, openToolsHash } from "./career-ui";

const split = (s: string) => s.split(",").map(v => v.trim()).filter(Boolean);
export function CareerCompanySetup({ desk, initialStep = 0, busy, token, run, onFinish, onMail, onSchedule }: {
  desk: CareerDesk; initialStep?: number; busy: boolean; token: string;
  run: (action: string, body?: Record<string, unknown>) => Promise<CareerDesk | null>;
  onFinish: () => void; onMail: () => void; onSchedule: () => void;
}) {
  const { i18n } = useTranslation();
  const c = (fr: string, en: string) => i18n.language.startsWith("fr") ? fr : en;
  const reduced = useReducedMotion();
  const state = desk.prospecting || emptyProspecting;
  const [criteria, setCriteria] = useState<ProspectCriteria>(() => initializeCompanyCriteria({ ...emptyProspecting.criteria, ...state.criteria,
    daily_search: desk.profile.company_prospecting ? Boolean(desk.loop?.enabled) : true }, i18n.language, state.mission_catalog));
  const [step, setStep] = useState(initialStep);
  const [channels, setChannels] = useState({ ...desk.profile.channels });
  const [provider, setProvider] = useState("");
  const editorRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (provider) editorRef.current?.scrollIntoView({ block: "nearest" }); }, [provider]);
  useEffect(() => { headingRef.current?.scrollIntoView({ block: "start" }); }, [step]);
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);
  const [localError, setLocalError] = useState(false);
  const [sourceSearch, setSourceSearch] = useState("");
  const [platformSearch, setPlatformSearch] = useState("");
  const [candidateCc, setCandidateCc] = useState(criteria.candidate_cc.join(", "));
  const [clientCc, setClientCc] = useState(criteria.client_cc.join(", "));
  const set = <K extends keyof ProspectCriteria>(key: K, value: ProspectCriteria[K]) => { setSaved(false); setCriteria(old => updateCompanyCriteria(old, key, value, i18n.language, state.mission_catalog)); };
  const steps = [c("Société", "Company"), c("Missions", "Missions"), c("Marchés", "Markets"), c("Sources des missions", "Mission sources"), c("Profils et sources", "Candidates and sources"), c("Communication", "Communication")];
  const save = async (finish = false) => {
    setLocalError(false);
    if (finish && !criteria.company.name.trim()) { setStep(0); setLocalError(true); return false; }
    const result = await run("prospecting_config", { criteria, activate_company: finish,
      configure_daily: finish, tz: browserTimeZone() });
    if (!result) return false;
    if (finish && !await run("profile", { channels })) return false;
    setSaved(true);
    if (finish) onFinish();
    return true;
  };
  const toggle = (key: "sources" | "platforms", id: string, checked?: boolean) => set(key, checked ? [...new Set([...criteria[key], id])] : criteria[key].filter(v => v !== id));
  const countries = (key: "countries" | "profile_countries") => <CareerCountryMultiSelect values={criteria[key]} onChange={v => set(key, v)} lang={i18n.language}
    label={key === "countries" ? c("Pays cibles", "Target countries") : c("Pays des profils (vide = pays cibles)", "Candidate countries (empty = target countries)")}
    placeholder={c("Ajouter un pays", "Add a country")} searchPlaceholder={c("Rechercher un pays", "Search countries")} emptyLabel={c("Aucun résultat", "No results")} />;
  const choices = (key: "domain" | "skills" | "roles" | "profile_domain" | "profile_roles" | "profile_skills", label: string, kind: "domains" | "skills" | "roles") =>
    <CareerMultiSelect label={label} values={typeof criteria[key] === "string" ? split(criteria[key] as string) : criteria[key] as string[]}
      options={careerSuggestions(kind, i18n.language, { domains: split(key.startsWith("profile_") ? criteria.profile_domain || criteria.domain : criteria.domain),
        roles: key.startsWith("profile_") ? criteria.profile_roles : criteria.roles })} disabled={busy}
      onChange={values => set(key, typeof criteria[key] === "string" ? values.join(", ") : values)}
      placeholder={c("Sélectionnez ou saisissez, puis Entrée", "Select or type, then press Enter")}
      emptyLabel={c("Aucune suggestion", "No suggestions")} removeLabel={c("Retirer", "Remove")} />;
  const missionSources = missionSourcesForCountries(criteria.countries, state.mission_catalog).filter(source => source.name.toLocaleLowerCase().includes(sourceSearch.toLocaleLowerCase()));
  const candidateSources = (state.platform_catalog?.length ? state.platform_catalog : DEFAULT_PLATFORM_CATALOG)
    .filter(source => platformRelevant(source.markets, criteria.profile_countries.length ? criteria.profile_countries : criteria.countries))
    .filter(source => source.name.toLocaleLowerCase().includes(platformSearch.toLocaleLowerCase()));
  const money = (key: "sale_rate" | "margin_percent" | "buy_rate_max" | "salary_max", label: string) => <TextField label={label} type="number" min={0} max={key === "margin_percent" ? 95 : undefined}
    value={criteria[key] ? String(criteria[key]) : ""} onChange={(_, v) => set(key, Number(v || 0))} />;
  const shares = careerRoleShares(criteria.roles, criteria.role_priorities);
  const shareLabel = (role: string) => shares[role].toLocaleString(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const openProvider = (id: string) => { setProvider(id); setApiKey(""); };
  const apiEditor = provider && <div ref={editorRef}><Stack tokens={{ childrenGap: 10 }} styles={{ root: { padding: 16, border: "1px solid rgba(128,128,128,.25)", borderRadius: 12 } }}>
    <strong>{provider === "serpapi" ? "SerpApi" : "Brave Search"}</strong>
    <TextField type="password" canRevealPassword autoComplete="new-password" label={c("Clé API", "API key")} value={apiKey}
      placeholder={state.keys[provider] ? c("Clé enregistrée", "Key saved") : ""} onChange={(_, v) => setApiKey(v || "")} />
    <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
      <PrimaryButton text={c("Enregistrer et tester", "Save and test")} disabled={busy || !(apiKey || state.keys[provider])} styles={BUTTON_STYLES}
        onClick={async () => { if (apiKey && !await run("prospecting_config", { keys: { [provider]: apiKey } })) return; setApiKey(""); await run("prospecting_test", { provider }); }} />
      <DefaultButton text={c("Retirer la clé", "Remove key")} disabled={busy || !state.keys[provider]} styles={BUTTON_STYLES}
        onClick={async () => { await run("prospecting_config", { keys: { [provider]: "" } }); setApiKey(""); }} />
      <Link onClick={() => openOfficialCareerUrl(token, provider === "serpapi" ? "https://serpapi.com/manage-api-key" : "https://api-dashboard.search.brave.com/")}>{c("Activer mon accès", "Activate my access")}</Link>
      <DefaultButton text={c("Fermer", "Close")} onClick={() => setProvider("")} styles={BUTTON_STYLES} />
    </Stack>
    {state.checks[provider] && <MessageBar messageBarType={state.checks[provider].ok ? MessageBarType.success : MessageBarType.warning}>{state.checks[provider].message}</MessageBar>}
  </Stack></div>;

  return <Stack tokens={{ childrenGap: 20 }}>
    <div ref={headingRef}><p className="text-xs text-muted-foreground">{step + 1} / 6</p><h2 className="text-2xl font-semibold">{steps[step]}</h2></div>
    <ProgressIndicator percentComplete={(step + 1) / 6} />
    <div className="flex flex-wrap gap-1" aria-label={c("Étapes de configuration", "Setup steps")}>{steps.map((name, i) => <DefaultButton key={name} text={name} checked={step === i} onClick={() => { setStep(i); setProvider(""); }} styles={BUTTON_STYLES} />)}</div>
    {localError && <MessageBar messageBarType={MessageBarType.error}>{c("Indiquez le nom de la société.", "Enter the company name.")}</MessageBar>}
    <motion.div key={step} initial={reduced ? false : { opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.2 }}>
      <Stack tokens={{ childrenGap: 16 }}>
        {step === 0 && <>
          {(["name", "email", "phone", "address"] as const).map((key, i) => <TextField key={key}
            label={[c("Nom de la société", "Company name"), "Email", c("Téléphone", "Phone"), c("Adresse", "Address")][i]}
            required={key === "name"} type={key === "email" ? "email" : key === "phone" ? "tel" : "text"}
            value={criteria.company[key] || ""} onChange={(_, v) => set("company", { ...criteria.company, [key]: v || "" })} />)}
          <div className="grid gap-4 sm:grid-cols-2">{money("sale_rate", c("TJM de vente minimum", "Minimum daily selling rate"))}{money("margin_percent", c("Marge cible (%)", "Target margin (%)"))}</div>
          <Dropdown label={c("Devise", "Currency")} selectedKey={criteria.currency} options={["EUR", "USD", "GBP", "CHF", "CAD", "AUD", "SAR", "OMR", "AED", "BHD", "QAR", "KWD", "MAD"].map(key => ({ key, text: key }))} onChange={(_, o) => set("currency", String(o?.key))} />
          <p className="text-xs text-muted-foreground">{c("Marge sur le prix de vente. Exemple : achat 400, marge 20 %, vente minimale 500. Les propositions utiliseront vos conditions.", "Margin on selling price. Example: cost 400, margin 20%, minimum sale 500. Proposals use your terms.")}</p>
        </>}
        {step === 1 && <>
          {choices("domain", c("Domaines / secteurs", "Domains / sectors"), "domains")}
          {choices("roles", c("Métiers (facultatif)", "Roles (optional)"), "roles")}
          {!!criteria.roles.length && <details>
            <summary className="cursor-pointer">{c("Priorités de recherche (facultatif)", "Search priorities (optional)")}</summary>
            <Stack tokens={{ childrenGap: 10 }} styles={{ root: { paddingTop: 12 } }}>
              <p className="text-xs text-muted-foreground">{c("Exemple : IA générative 50, data engineering 20, full stack 20. Le total est ajusté à 100 %. Vide : partage du reste. 0 : en pause.", "Example: generative AI 50, data engineering 20, full stack 20. The total is scaled to 100%. Empty: share of the remainder. 0: paused.")}</p>
              {criteria.roles.map(role => <TextField key={role} label={localizeCareerValue(role, i18n.language)} ariaLabel={`${c("Priorité", "Priority")} ${localizeCareerValue(role, i18n.language)}`}
                type="number" min={0} max={100} step={1} suffix="%" placeholder={c("Auto", "Auto")} disabled={busy}
                value={criteria.role_priorities[role] === undefined ? "" : String(criteria.role_priorities[role])}
                description={c(`Part de recherche indicative : ${shareLabel(role)} %`, `Indicative search share: ${shareLabel(role)}%`)}
                onChange={(_, value) => {
                  const next = { ...criteria.role_priorities };
                  if (!value?.trim()) delete next[role];
                  else { const weight = Number(value); if (!Number.isFinite(weight)) return; next[role] = Math.min(100, Math.max(0, weight)); }
                  set("role_priorities", next);
                }} />)}
              <p className="text-xs text-muted-foreground">{c("L'agent répartit ses recherches de missions et de profils selon ces priorités, les sources et les pays disponibles. Le matching d'une mission reste ciblé sur son besoin.", "The agent allocates mission and candidate searches using these priorities and the available sources and countries. Matching an individual mission stays focused on its requirements.")}</p>
              {!Object.values(shares).some(value => value > 0) && <MessageBar messageBarType={MessageBarType.warning}>{c("Tous ces métiers sont en pause. Donnez une priorité positive à au moins un métier pour les rechercher.", "All these roles are paused. Give at least one role a positive priority to search for them.")}</MessageBar>}
            </Stack>
          </details>}
          {choices("skills", c("Compétences recherchées", "Required skills"), "skills")}
          <p className="text-xs text-muted-foreground">{c("Les métiers préremplissent les compétences et les profils recherchés. Ajoutez ou retirez librement des éléments.", "Roles prefill skills and candidate requirements. You can freely add or remove any item.")}</p>
          <Dropdown label={c("Besoins", "Engagement")} selectedKey={criteria.track} onChange={(_, o) => set("track", o?.key as ProspectCriteria["track"])} options={[
            { key: "both", text: c("Missions et emplois", "Missions and jobs") }, { key: "freelance", text: "Freelance" }, { key: "jobs", text: c("Salariés", "Employees") }]} />
          <Dropdown label={c("Mode de travail", "Work mode")} selectedKey={criteria.work_mode} disabled={busy}
            onChange={(_, option) => { if (option) set("work_mode", option.key as ProspectCriteria["work_mode"]); }} options={[
              { key: "any", text: c("Tous les modes", "Any work mode") }, { key: "remote", text: c("Télétravail complet (Full remote)", "Full remote") },
              { key: "hybrid", text: c("Hybride", "Hybrid") }, { key: "onsite", text: c("Sur site", "On site") }]} />
          <TextField label={c("Ancienneté maximale des demandes (jours)", "Maximum posting age (days)")} type="number" min={1} max={30} disabled={busy}
            value={String(criteria.max_age_days)} onChange={(_, value) => { const days = Number(value); if (Number.isInteger(days) && days >= 1 && days <= 30) set("max_age_days", days); }} />
          <p className="text-xs text-muted-foreground">{c("Les résultats doivent respecter les pays, le TJM minimum, le mode de travail et la date de publication. Les informations manquantes ne valident pas un critère.", "Results must meet the countries, minimum day rate, work mode and publication date. Missing information does not satisfy a criterion.")}</p>
        </>}
        {step === 2 && <>
          {countries("countries")}
          <TextField label={c("Ville (facultatif)", "City (optional)")} value={criteria.city} onChange={(_, v) => set("city", v || "")} />
          {criteria.countries.includes("AE") && <Stack horizontal tokens={{ childrenGap: 8 }}>{["Dubai", "Abu Dhabi"].map(city => <DefaultButton key={city} text={city} onClick={() => set("city", city)} styles={BUTTON_STYLES} />)}</Stack>}
        </>}
        {step === 3 && <>
          <p>{c("Les sources sans clé API sont sélectionnées par défaut selon vos pays cibles. Vous pouvez les décocher.", "Sources without API keys are selected by default for your target countries. You can deselect them.")}</p>
          <TextField label={c("Rechercher une plateforme de missions", "Search mission platforms")} value={sourceSearch} onChange={(_, value) => setSourceSearch(value || "")} />
          <p className="text-xs text-muted-foreground">{missionSources.length} {c("plateformes. Cochez celles à utiliser.", "platforms. Select the ones to use.")}</p>
          {missionSources.map(source => <div key={source.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-4">
            <div><Checkbox label={source.name} checked={criteria.sources.includes(source.id)} onChange={(_, v) => toggle("sources", source.id, v)} />
              {source.url && <Link onClick={() => openOfficialCareerUrl(token, source.url!)}>{c("Ouvrir la plateforme", "Open platform")}</Link>}
              {source.mode === "indexed" && <p className="text-xs text-muted-foreground">{c("Offres publiques via SerpApi ou Brave", "Public offers via SerpApi or Brave")}</p>}</div>
            {source.provider ? <DefaultButton text={state.keys[source.provider] ? c("Accès enregistré", "Access saved") : c("Configurer", "Configure")} onClick={() => openProvider(source.provider)} styles={BUTTON_STYLES} /> : <span className="text-xs text-muted-foreground">{c("Sans clé API", "No API key")}</span>}
          </div>)}
          {apiEditor}
        </>}
        {step === 4 && <>
          {choices("profile_domain", c("Domaines des profils", "Candidate domains"), "domains")}
          {choices("profile_roles", c("Métiers des profils", "Candidate roles"), "roles")}
          {choices("profile_skills", c("Compétences des profils", "Candidate skills"), "skills")}
          <p className="text-xs text-muted-foreground">{c("Prérempli à partir de votre société. Vos ajouts et suppressions sont conservés.", "Prefilled from your company choices. Your additions and removals are saved.")}</p>
          {countries("profile_countries")}
          <TextField label={c("Ville des profils (facultatif)", "Candidate city (optional)")} value={criteria.profile_city} onChange={(_, v) => set("profile_city", v || "")} />
          <div className="grid gap-4 sm:grid-cols-2">{money("buy_rate_max", c("TJM d'achat maximum", "Maximum daily buying rate"))}{money("salary_max", c("Salaire annuel maximum", "Maximum annual salary"))}</div>
          <Checkbox label={c("Inclure mon vivier interne", "Include my internal talent pool")} checked={criteria.internal_talents} onChange={(_, v) => set("internal_talents", !!v)} />
          <Checkbox label={c("Rechercher des profils indiquant leur disponibilité", "Search for candidates mentioning availability")} checked={criteria.signal_only} onChange={(_, v) => set("signal_only", !!v)} />
          <Label>{c("Sources de profils", "Candidate sources")}</Label>
          <TextField label={c("Rechercher une plateforme de recrutement", "Search recruitment platforms")} value={platformSearch} onChange={(_, value) => setPlatformSearch(value || "")} />
          <p className="text-xs text-muted-foreground">{candidateSources.length} {c("plateformes dans vos pays cibles", "platforms in your target countries")}</p>
          {candidateSources.map(p => <div key={p.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-4">
            <div>{p.indexed_profiles ? <Checkbox label={p.name} checked={criteria.platforms.includes(p.id)} onChange={(_, v) => toggle("platforms", p.id, v)} /> : <strong>{p.name}</strong>}
              <p className="mt-1 text-xs text-muted-foreground">{p.indexed_profiles ? c("Recherche publique sans clé API. API en option.", "Public search without an API key. API optional.") : c("Accès partenaire ou plateforme, API non connectée", "Partner or platform access, API not connected")}</p></div>
            <DefaultButton text={p.indexed_profiles ? c("API facultative", "Optional API") : c("Gérer mon accès", "Manage access")} styles={BUTTON_STYLES}
              onClick={() => p.indexed_profiles ? openProvider("serpapi") : openOfficialCareerUrl(token, p.url)} />
          </div>)}
          <DefaultButton text={c("Configurer Brave en complément", "Configure Brave as an additional source")} onClick={() => openProvider("brave")} styles={BUTTON_STYLES} />
          {apiEditor}
        </>}
        {step === 5 && <>
          <Checkbox label={c("Recherche et matching automatiques", "Automatic search and matching")}
            checked={criteria.daily_search} onChange={(_, value) => set("daily_search", !!value)} />
          <p className="text-xs text-muted-foreground">{c("Chaque jour à 9 h par défaut, ou selon vos horaires enregistrés. Le service Navin doit être en marche. Les résultats restent dans le suivi.", "Every day at 9 am by default, or on your saved schedule. The Navin service must be running. Results remain in tracking.")}</p>
          <DefaultButton text={c("Configurer l'email professionnel", "Configure business email")} onClick={onMail} styles={BUTTON_STYLES} iconProps={{ iconName: "Mail" }} />
          <TextField label={c("Signature email", "Email signature")} multiline rows={3} value={criteria.signature} onChange={(_, v) => set("signature", v || "")} placeholder={criteria.company.name} />
          <TextField label={c("En copie des échanges candidats", "CC on candidate emails")} placeholder={c("Emails séparés par des virgules (facultatif)", "Comma-separated emails (optional)")} value={candidateCc} onChange={(_, v) => { setCandidateCc(v || ""); set("candidate_cc", split(v || "")); }} />
          <TextField label={c("En copie des échanges clients", "CC on client emails")} placeholder={c("Emails séparés par des virgules (facultatif)", "Comma-separated emails (optional)")} value={clientCc} onChange={(_, v) => { setClientCc(v || ""); set("client_cc", split(v || "")); }} />
          <Checkbox label={c("Contacter automatiquement les profils correspondants par email", "Automatically email matching candidates")} checked={criteria.auto_contact} onChange={(_, v) => set("auto_contact", !!v)} />
          <Checkbox label={c("Présenter automatiquement le CV au client après accord du candidat", "Automatically present the CV to the client after candidate consent")} checked={criteria.auto_present} onChange={(_, v) => set("auto_present", !!v)} />
          <Checkbox label={c("Attendre aussi le dossier de compétences avant présentation", "Also wait for the skills dossier before presenting")} checked={criteria.require_dossier} onChange={(_, v) => set("require_dossier", !!v)} />
          <p className="text-sm">{c("L'agent suit ensuite les réponses, les disponibilités et la confirmation de l'entretien, puis envoie la préparation.", "The agent then follows replies, availability and interview confirmation, and sends preparation material.")}</p>
          <p className="text-xs text-muted-foreground">{c("L'envoi utilise une adresse connue. Le partage du CV exige un accord explicite pour la mission. Les données manquantes apparaissent dans le suivi.", "Sending requires a known address. CV sharing requires explicit consent for that mission. Missing information appears in tracking.")}</p>
          <div className="grid gap-4 sm:grid-cols-2"><TextField label={c("Score minimum (%)", "Minimum score (%)")} type="number" min={0} max={100} value={String(criteria.min_score)} onChange={(_, v) => set("min_score", Number(v || 0))} />
            <TextField label={c("Emails maximum par jour", "Maximum emails per day")} type="number" min={0} max={25} value={String(criteria.max_per_day)} onChange={(_, v) => set("max_per_day", Number(v || 0))} /></div>
          <Label>{c("Mes notifications", "My notifications")}</Label>
          {(["email", "whatsapp", "teams", "telegram"] as const).map(channel => <Stack key={channel} tokens={{ childrenGap: 8 }}>
            <Checkbox label={{ email: "Email", whatsapp: "WhatsApp", teams: "Teams", telegram: "Telegram" }[channel]} checked={!!channels[channel]} onChange={(_, v) => setChannels(old => ({ ...old, [channel]: !!v }))} />
            {channels[channel] && <TextField label={c("Destination", "Destination")} value={String(channels[`${channel}_to`] || "")} onChange={(_, v) => setChannels(old => ({ ...old, [`${channel}_to`]: v || "" }))} />}
          </Stack>)}
          <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
            <DefaultButton text={c("Connecter WhatsApp / Teams", "Connect WhatsApp / Teams")} styles={BUTTON_STYLES} onClick={() => openToolsHash()} />
            <DefaultButton text={c("Planifier les cycles automatiques", "Schedule automatic cycles")} onClick={async () => { if (await save(true)) onSchedule(); }} styles={BUTTON_STYLES} />
          </Stack>
        </>}
      </Stack>
    </motion.div>
    {saved && <MessageBar messageBarType={MessageBarType.success}>{c("Enregistré", "Saved")}</MessageBar>}
    <Stack horizontal wrap tokens={{ childrenGap: 8 }}>
      <DefaultButton text={c("Précédent", "Previous")} disabled={step === 0 || busy} onClick={() => { setStep(step - 1); setProvider(""); }} styles={BUTTON_STYLES} />
      <DefaultButton text={c("Enregistrer", "Save")} disabled={busy} onClick={() => void save()} styles={BUTTON_STYLES} />
      <PrimaryButton text={step === 5 ? c("Terminer", "Finish") : c("Continuer", "Continue")} disabled={busy || (step === 0 && !criteria.company.name.trim())}
        onClick={async () => { if (await save(step === 5) && step < 5) { setStep(step + 1); setProvider(""); } }} styles={BUTTON_STYLES} />
    </Stack>
  </Stack>;
}
