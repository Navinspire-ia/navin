import { formatTenderMoney } from "@/components/studio/tenders/money";
import type { TenderNotice, TenderSource } from "@/lib/tenders-api";
import { firstNonEmpty, stripHtml } from "@/lib/plain-text";

export type TenderFactKey =
  | "status"
  | "deadline"
  | "place"
  | "budget"
  | "duration"
  | "buyer"
  | "language"
  | "source"
  | "remote"
  | "sector"
  | "cpv"
  | "published"
  | "reference"
  | "stage"
  | "submission"
  | "need"
  | "eligibility";

export type TenderFact = {
  key: TenderFactKey;
  labelKey: string;
  labelFallback: string;
  value: string;
  valueKey?: string;
  valueFallback?: string;
};

export type ParsedTenderFacts = {
  title: string;
  description: string;
  need: string;
  eligibility: string;
  goReason: string;
  goNote: string;
  card: TenderFact[];
  extra: TenderFact[];
};

const OPEN_STATUS = new Set(["open", "published", "active"]);
const CLOSED_STATUS = new Set(["closed", "complete", "awarded", "expired"]);
const CANCELLED_STATUS = new Set(["cancelled", "canceled", "unsuccessful", "withdrawn"]);
const FR_HINT =
  /\b(le|la|les|des|une|pour|avec|dans|sur|objet|avis|marche|prestataire|cahier|fourniture|travaux|modernisation|refonte|plateforme|acheteur|depot)\b|[àâäéèêëïîôùûüç]/gi;
const EN_HINT =
  /\b(the|and|for|with|this|shall|must|tender|procurement|scope|services|please|rebuild|platform|authority|deadline|submission)\b/gi;
const AR_HINT = /[\u0600-\u06FF]/g;
const DURATION_RE =
  /(?:dur[eé]e|duration|charge)(?:\s+de\s+mission)?\s*[:-]?\s*[^\n.]{0,48}|\b\d+\s*(?:mois|semaines?|jours?(?:[ -]homme)?|months?|weeks?|man[ -]?days?)\b/i;
const PLACE_RE =
  /(?:lieu(?:\s+d['’]ex[eé]cution)?|place of performance|ville)\s*[:-]\s*([A-Za-zÀ-ÿ][\wÀ-ÿ'’\-\s]{1,40})/i;

const CARD_KEYS: TenderFactKey[] = [
  "status",
  "deadline",
  "place",
  "budget",
  "duration",
  "buyer",
  "language",
  "source",
];

function asText(value: unknown): string {
  if (typeof value !== "string") return "";
  return stripHtml(value).trim();
}

function analysisText(row: TenderNotice, key: string): string {
  const analysis = row.analysis;
  if (!analysis || typeof analysis !== "object") return "";
  return asText(analysis[key]);
}

function countMatches(source: string, pattern: RegExp): number {
  const copy = new RegExp(pattern.source, pattern.flags);
  return source.match(copy)?.length || 0;
}

export function detectNoticeLanguage(row: Pick<TenderNotice, "title" | "description" | "eligibility" | "cdc_text" | "response">): string {
  const blob = [
    row.title,
    row.description,
    row.eligibility,
    row.cdc_text,
  ]
    .map((part) => asText(part))
    .join(" ");
  const ar = countMatches(blob, AR_HINT);
  if (ar >= 10) return "ar";
  const fr = countMatches(blob, FR_HINT);
  const en = countMatches(blob, EN_HINT);
  if (fr >= 3 && fr > en * 1.15) return "fr";
  if (en >= 3 && en > fr * 1.15) return "en";
  const written = String(row.response?.language || "").trim().toLowerCase().slice(0, 2);
  if (written === "fr" || written === "en" || written === "ar") return written;
  return "";
}

export function pickDuration(text: string): string {
  const match = DURATION_RE.exec(text);
  if (!match) return "";
  return stripHtml(match[0]).replace(/\s+/g, " ").trim().slice(0, 80);
}

export function pickCity(row: TenderNotice, blob: string): string {
  const stated = firstNonEmpty(row.city, analysisText(row, "city"), analysisText(row, "place"));
  if (stated) return stated;
  const match = PLACE_RE.exec(blob);
  if (!match) return "";
  return stripHtml(match[1] || "").replace(/\s+/g, " ").trim().slice(0, 48);
}

export function detectWorkMode(text: string): "remote" | "onsite" | "hybrid" | "" {
  const hay = text.toLowerCase();
  if (/\b(t[eé]l[eé]travail|fully remote|100%\s*remote|remote work|travail\s+a?\s*distance)\b/.test(hay)) {
    return "remote";
  }
  if (/\b(hybride|hybrid)\b/.test(hay)) return "hybrid";
  if (/\b(pr[eé]sentiel|on[ -]?site|sur site)\b/.test(hay)) return "onsite";
  return "";
}

export function noticeStatusCode(raw: string): "open" | "closed" | "cancelled" | "amended" | "" {
  const key = raw.trim().toLowerCase();
  if (!key) return "";
  if (key === "amended") return "amended";
  if (OPEN_STATUS.has(key)) return "open";
  if (CLOSED_STATUS.has(key)) return "closed";
  if (CANCELLED_STATUS.has(key)) return "cancelled";
  return "";
}

export function formatNoticeDate(value: string, locale = "fr-FR"): string {
  const text = String(value || "").trim();
  if (!text) return "";
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return text.slice(0, 32);
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  if (Number.isNaN(date.getTime())) return text.slice(0, 32);
  return date.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

export function tenderSourceName(row: TenderNotice, names?: Record<string, string>): string {
  const id = String(row.source_id || "").trim();
  if (!id) return "";
  const named = names?.[id]?.trim();
  return named || id;
}

export function sourceNameMap(sources: TenderSource[] = [], catalog: TenderSource[] = []): Record<string, string> {
  const map: Record<string, string> = {};
  for (const row of [...catalog, ...sources]) {
    const id = String(row.id || "").trim();
    const name = String(row.name || "").trim();
    if (id && name) map[id] = name;
  }
  return map;
}

function fact(
  key: TenderFactKey,
  labelKey: string,
  labelFallback: string,
  value: string,
  mapped?: { valueKey: string; valueFallback: string },
): TenderFact {
  return {
    key,
    labelKey,
    labelFallback,
    value,
    valueKey: mapped?.valueKey,
    valueFallback: mapped?.valueFallback,
  };
}

function languageFact(code: string): TenderFact {
  if (code === "fr") return fact("language", "factLanguage", "Language", code, { valueKey: "langFr", valueFallback: "French" });
  if (code === "en") return fact("language", "factLanguage", "Language", code, { valueKey: "langEn", valueFallback: "English" });
  if (code === "ar") return fact("language", "factLanguage", "Language", code, { valueKey: "langAr", valueFallback: "Arabic" });
  return fact("language", "factLanguage", "Language", "");
}

function statusFact(raw: string): TenderFact {
  const code = noticeStatusCode(raw);
  if (code === "open") {
    return fact("status", "factStatus", "Status", raw, { valueKey: "noticeStatus.open", valueFallback: "Open" });
  }
  if (code === "closed") {
    return fact("status", "factStatus", "Status", raw, { valueKey: "noticeStatus.closed", valueFallback: "Closed" });
  }
  if (code === "cancelled") {
    return fact("status", "factStatus", "Status", raw, { valueKey: "noticeStatus.cancelled", valueFallback: "Cancelled" });
  }
  if (code === "amended") {
    return fact("status", "factStatus", "Status", raw, { valueKey: "noticeStatus.amended", valueFallback: "Amended" });
  }
  return fact("status", "factStatus", "Status", raw.trim());
}

function remoteFact(mode: "remote" | "onsite" | "hybrid" | ""): TenderFact | null {
  if (mode === "remote") {
    return fact("remote", "factRemote", "Remote", mode, { valueKey: "remoteYes", valueFallback: "Remote" });
  }
  if (mode === "onsite") {
    return fact("remote", "factRemote", "Remote", mode, { valueKey: "remoteOnsite", valueFallback: "On site" });
  }
  if (mode === "hybrid") {
    return fact("remote", "factRemote", "Remote", mode, { valueKey: "remoteHybrid", valueFallback: "Hybrid" });
  }
  return null;
}

function placeValue(country: string, city: string): string {
  const iso = country.trim().toUpperCase();
  const town = city.trim();
  if (town && iso && iso !== "INTL") return `${town}, ${iso}`;
  if (town) return town;
  if (iso && iso !== "INTL") return iso;
  if (iso === "INTL") return "INTL";
  return "";
}

export function parseTenderFacts(
  row: TenderNotice,
  opts: { locale?: string; sourceNames?: Record<string, string> } = {},
): ParsedTenderFacts {
  const locale = opts.locale || "fr-FR";
  const title = asText(row.title);
  const description = asText(row.description || "");
  const cdc = asText(row.cdc_text || "");
  const blob = `${title} ${description} ${cdc} ${asText(row.eligibility)}`;
  const need = firstNonEmpty(analysisText(row, "need"));
  const eligibility = firstNonEmpty(analysisText(row, "eligibility"), row.eligibility);
  const city = pickCity(row, blob);
  const duration = pickDuration(blob);
  const language = detectNoticeLanguage(row);
  const workMode = detectWorkMode(blob);
  const budget =
    typeof row.budget === "number" && Number.isFinite(row.budget)
      ? formatTenderMoney(row.budget, row.currency || "EUR")
      : "";
  const byKey: Record<TenderFactKey, TenderFact> = {
    status: statusFact(String(row.status || "")),
    deadline: fact("deadline", "factDeadline", "Deadline", formatNoticeDate(row.deadline || "", locale)),
    place: fact("place", "factPlace", "Country / city", placeValue(String(row.country || ""), city)),
    budget: fact("budget", "factBudget", "Budget", budget),
    duration: fact("duration", "factDuration", "Duration", duration),
    buyer: fact("buyer", "factBuyer", "Buyer", asText(row.buyer || "")),
    language: languageFact(language),
    source: fact("source", "factSource", "Source", tenderSourceName(row, opts.sourceNames)),
    remote: remoteFact(workMode) || fact("remote", "factRemote", "Remote", ""),
    sector: fact("sector", "factSector", "Sector", asText(row.sector || "")),
    cpv: fact("cpv", "factCpv", "CPV", asText(row.cpv || "")),
    published: fact("published", "factPublished", "Published", formatNoticeDate(row.publication_date || "", locale)),
    reference: fact("reference", "factReference", "Reference", asText(row.reference || "")),
    stage: fact("stage", "factStage", "Stage", String(row.stage || "").trim()),
    submission: fact("submission", "factSubmission", "Submission", asText(row.submission_method || "")),
    need: fact("need", "factNeed", "Need", need),
    eligibility: fact("eligibility", "factEligibility", "Eligibility", eligibility),
  };
  const extraKeys: TenderFactKey[] = ["stage", "reference", "published", "sector", "cpv", "submission"];
  if (workMode) extraKeys.unshift("remote");
  return {
    title,
    description,
    need,
    eligibility,
    goReason: asText(row.go_reason || ""),
    goNote: asText(row.go_note || ""),
    card: CARD_KEYS.map((key) => byKey[key]),
    extra: extraKeys.map((key) => byKey[key]),
  };
}

export function factDisplay(fact: TenderFact, tx: (key: string, fallback: string) => string): string {
  if (fact.valueKey) return tx(fact.valueKey, fact.valueFallback || fact.value);
  if (fact.value) return fact.value;
  return tx("unknown", "non renseigne");
}
