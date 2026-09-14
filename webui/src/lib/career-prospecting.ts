// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import sourceCatalog from "./career-source-catalog.json";

export const DEFAULT_MISSION_CATALOG = sourceCatalog.missions;
export const DEFAULT_PLATFORM_CATALOG = sourceCatalog.platforms;
export const DEFAULT_NO_KEY_SOURCES = DEFAULT_MISSION_CATALOG.filter(source => !source.provider).map(source => source.id);
export const DEFAULT_PUBLIC_PLATFORMS = DEFAULT_PLATFORM_CATALOG.filter(source => source.indexed_profiles).map(source => source.id);

export function prospectingError(message: string, french: boolean): string {
  if (!french) return message;
  const translations: Record<string, string> = {
    "Candidate not found.": "Profil introuvable. Actualisez le vivier.",
    "Deletion requires confirmation.": "Confirmez la suppression pour continuer.",
    "A Career operation is in progress. Try again after it finishes.": "Une opération Carrière est en cours. Réessayez lorsqu'elle sera terminée.",
    "A company search is already running. Try again after it finishes.": "Une recherche société est déjà en cours. Réessayez lorsqu'elle sera terminée.",
    "Search countries must remain within the company configuration.": "Les pays de recherche doivent respecter la configuration de la société.",
    "Publication age must be between 1 and 30 days.": "L'ancienneté des demandes doit être comprise entre 1 et 30 jours.",
    "Invalid search mode.": "Mode de recherche ou de travail invalide.",
  };
  return translations[message] || message;
}

export type CompanyAutofillField = "skills" | "profile_roles" | "profile_skills" | "profile_domain" | "sources" | "platforms";
export interface CompanyAutofill {
  version: number;
  generated: Partial<Record<CompanyAutofillField, string[]>>;
  dismissed: Partial<Record<CompanyAutofillField, string[]>>;
}

export interface ProspectCriteria {
  daily_search: boolean;
  company: { name: string; email: string; phone: string; address: string };
  sale_rate: number; margin_percent: number; buy_rate_max: number; salary_max: number; currency: string;
  min_rate?: number; sale_rate_remote?: number; sale_rate_onsite?: number; min_project_budget?: number;
  profile_domain: string; profile_roles: string[]; profile_skills: string[]; profile_countries: string[]; profile_city: string;
  autofill?: CompanyAutofill;
  signature: string; auto_contact: boolean; auto_present: boolean; min_score: number; max_per_day: number;
  candidate_cc: string[]; client_cc: string[]; require_dossier: boolean;
  mode: "both" | "missions" | "profiles";
  domain: string;
  roles: string[];
  role_priorities: Record<string, number>;
  role_skills: Record<string, string[]>;
  skills: string[];
  countries: string[];
  city: string;
  track: "both" | "jobs" | "freelance";
  work_mode: "any" | "remote" | "hybrid" | "onsite";
  max_age_days: number;
  internal_talents: boolean;
  signal_only: boolean;
  sources: string[];
  platforms: string[];
}
export interface SourcedCandidate {
  email?: string; phone?: string; cv?: { name: string; received_at: number }; dossier?: { name: string; received_at: number };
  id: string; name: string; headline: string; snippet: string; url: string; source: string;
  skills: string[]; country: string; city: string; signal: string; observed_at: number;
}
export interface CandidateMatch {
  emails?: { kind: string; recipient: string; cc: string[]; body: string; status: string; at: number }[];
  availability_detail?: string; preparation?: string;
  interview?: { status: string; slot?: string; client_reply?: string; candidate_reply?: string };
  purchase_rate?: number; next_action?: string; last_reply?: { text: string; sender: string; at: number };
  candidate_mail?: { status: string; recipient: string; body: string }; client_mail?: { status: string; recipient: string; body: string };
  candidate: SourcedCandidate; score: number; stage: string;
  interest: string; availability: string; note: string; contract: string;
  reasons: { label: string; points: number; max: number; detail: string }[];
  missing_skills: string[];
}
export interface ProspectingState {
  runs?: { at: number; offers: number; new_profiles: number; matched_offers: number; matches: number; source_errors: number; deferred: number; status: string }[];
  mission_catalog?: MissionSource[];
  criteria: ProspectCriteria;
  candidates: SourcedCandidate[];
  matches: Record<string, { searched_at: number; results: CandidateMatch[] }>;
  keys: Record<string, boolean>;
  checks: Record<string, { ok: boolean; message: string }>;
  platform_catalog: { id: string; name: string; url: string; markets: string; access: string; indexed_profiles: boolean; profile_mode?: string; priority?: number }[];
  last_run: { at?: number; status?: string; offers?: number; revived_offers?: number; profiles?: number; deferred?: number;
    offer_id?: string;
    sources?: { source: string; role?: string; platform?: string; status: string; error_code?: string; count: number; rejected?: Record<string, number>; message?: string; retry_at?: number }[] };
}

export function sourcingStatusText(source: NonNullable<ProspectingState["last_run"]["sources"]>[number], language: string): string {
  const fr = language.startsWith("fr");
  if (source.status === "ok") return `${source.count} ${fr ? "résultat(s)" : "result(s)"}`;
  if (source.status === "not_configured") return fr
    ? "Source non configurée : clé API manquante. Les recherches disponibles continuent."
    : "Source not configured: missing API key. Available searches continue.";
  if (source.status === "access_required") return fr
    ? (source.source.startsWith("missions:") ? "Compte plateforme requis. La collecte automatique n'est pas connectée." : "Accès à la base de candidats requis. La recherche publique de profils est indisponible.")
    : (source.source.startsWith("missions:") ? "Marketplace account required. Automatic collection is not connected." : "Candidate database access required. Public profile search is unavailable.");
  if (source.error_code === "rate_limited" || source.message?.includes("limited by the provider")) return fr
    ? "Le moteur de recherche a temporairement bloqué cette requête. Cela ne signifie pas qu'aucun profil n'existe."
    : "The search engine temporarily blocked this query. This does not mean there are no candidates.";
  if (source.status === "cooldown") return fr
    ? "Source temporairement en pause après un échec."
    : "Source temporarily paused after a failure.";
  return fr ? "Source indisponible pendant cette recherche. Les autres sources continuent."
    : "Source unavailable during this search. Other sources continue.";
}

export const emptyProspecting: ProspectingState = {
  criteria: { daily_search: true, company: { name: "", email: "", phone: "", address: "" }, sale_rate: 0, margin_percent: 0,
    buy_rate_max: 0, salary_max: 0, currency: "EUR", profile_domain: "", profile_roles: [], profile_skills: [], profile_countries: [], profile_city: "",
    signature: "", auto_contact: false, auto_present: false, min_score: 70, max_per_day: 5,
    candidate_cc: [], client_cc: [], require_dossier: false,
    mode: "both", domain: "", roles: [], role_priorities: {}, role_skills: {}, skills: [], countries: [], city: "", track: "both",
    work_mode: "any", max_age_days: 30,
    internal_talents: true, signal_only: true,
    sources: DEFAULT_NO_KEY_SOURCES, platforms: DEFAULT_PUBLIC_PLATFORMS },
  candidates: [], matches: {}, keys: {}, checks: {}, mission_catalog: DEFAULT_MISSION_CATALOG, platform_catalog: DEFAULT_PLATFORM_CATALOG, last_run: {},
};

const EUROPE = new Set(["FR", "BE", "DE", "CH", "LU", "NL", "GB", "IE", "ES", "IT", "PT", "AT", "PL", "SE", "NO", "DK", "FI"]);
export function platformRelevant(markets: string, countries: string[]): boolean {
  if (!countries.length || markets.includes("International")) return true;
  return countries.some(country => markets.split(/[, ]+/).includes(country) || (markets.includes("Europe") && EUROPE.has(country)));
}

export interface MissionSource { id: string; name: string; markets: string; provider: string; url?: string; mode?: string; priority?: number; opportunity_kind?: string }
export function missionSourcesForCountries(countries: string[], catalog?: MissionSource[]) {
  return (catalog?.length ? catalog : DEFAULT_MISSION_CATALOG).filter(source => platformRelevant(source.markets, countries));
}

export function suggestedMissionSources(countries: string[], track: string, catalog?: MissionSource[]): string[] {
  return missionSourcesForCountries(countries, catalog).filter(source => !source.provider
    && (track === "freelance" ? source.priority === 1 || ["freework", "collective", "freelancescope"].includes(source.id)
      : track === "jobs" ? source.opportunity_kind !== "freelance" : true)).map(source => source.id);
}

export function candidateSheet(candidate: SourcedCandidate): string {
  return ["Fiche profil - Navin Carrière", candidate.name, candidate.headline,
    `Source : ${candidate.source}`, `Lien : ${candidate.url}`, `Compétences : ${candidate.skills.join(", ")}`,
    `Lieu : ${[candidate.city, candidate.country].filter(Boolean).join(", ") || "À confirmer"}`,
    `Observé le : ${new Date(candidate.observed_at * 1000).toISOString()}`,
    "Disponibilité et intérêt pour une mission : à confirmer directement avec le candidat.",
    "Informations recueillies :", candidate.snippet].join("\n\n");
}

export function downloadCandidate(candidate: SourcedCandidate) {
  const url = URL.createObjectURL(new Blob([candidateSheet(candidate)], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `profil-${candidate.id.replace(/[^a-zA-Z0-9_-]/g, "")}.txt`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
