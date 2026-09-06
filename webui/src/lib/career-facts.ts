import type { CareerOpportunity, CareerTrack } from "@/lib/career-api";
import { formatMoney } from "@/lib/career-money";
import { stripHtml } from "@/lib/plain-text";

/** Table-like facts on offer and application cards. Missing values stay unknown. */
export type OfferFact = { id: string; label: string; value: string };

export type OfferFactCopy = {
  unknown: string;
  remote: string;
  office: string;
  country: string;
  city: string;
  pay: string;
  duration: string;
  workRemote: string;
  workHybrid: string;
  workOnsite: string;
  perDay: string;
};

function offerCity(location: string | undefined, unknown: string): string {
  const raw = String(location || "").trim();
  if (!raw) return unknown;
  const first = raw.split(",")[0].trim();
  if (!first || /^remote$/i.test(first) || /^worldwide$/i.test(first)) return unknown;
  return first;
}

function offerCountryValue(country: string | undefined, unknown: string): string {
  const raw = String(country || "").trim();
  if (!raw || /^remote$/i.test(raw)) return unknown;
  return raw;
}

export function offerRemoteLabel(remote: string | undefined, copy: OfferFactCopy): string {
  const key = String(remote || "")
    .trim()
    .toLowerCase();
  if (["remote", "yes", "true", "fully_remote", "full_remote", "fully-remote"].includes(key)) {
    return copy.workRemote;
  }
  if (key === "hybrid" || key === "hybride") return copy.workHybrid;
  if (["onsite", "on-site", "on_site", "office"].includes(key)) return copy.workOnsite;
  return copy.unknown;
}

function parsedFacts(row: CareerOpportunity): {
  remote: string;
  officeDays: string;
  pay: number;
  currency: string;
  duration: string;
} {
  const text = stripHtml(row.description || "");
  let remote = String(row.remote || "")
    .trim()
    .toLowerCase();
  if (!remote && /\bhybrid\b/i.test(text)) remote = "hybrid";
  else if (!remote && /\b(remote|teletravail|work from home)\b/i.test(text)) remote = "remote";

  const min = Number(row.hybrid_days_min);
  const max = Number(row.hybrid_days_max);
  const hasMin = Number.isFinite(min) && min > 0;
  const hasMax = Number.isFinite(max) && max > 0;
  let officeDays = "";
  if (hasMin && hasMax && min !== max) officeDays = `${min}-${max}`;
  else if (hasMin || hasMax) officeDays = String(hasMin ? min : max);
  if (!officeDays) {
    const hybridDays = text.match(/\bhybrid\s+(\d+)\s+days?\b/i);
    const siteDays = text.match(
      /(\d)\s*(?:-\s*(\d)\s*)?(?:days?|jours?)\s*(?:on[ -]?site|in (?:the )?office|presentiel|présentiel|bureau)/i,
    );
    if (hybridDays) officeDays = hybridDays[1];
    else if (siteDays) officeDays = siteDays[2] ? `${siteDays[1]}-${siteDays[2]}` : siteDays[1];
  }

  let pay = Number(row.compensation);
  if (!Number.isFinite(pay) || pay <= 0) pay = 0;
  let currency = String(row.currency || "").trim();
  if (pay <= 0) {
    const tjm = text.match(/\btjm\s+(\d{3,4})\s*(eur|usd|gbp|chf)?\b/i);
    if (tjm) {
      pay = Number(tjm[1]);
      currency = currency || (tjm[2] || "EUR").toUpperCase();
    }
  }

  let duration = String(row.duration || "").trim();
  if (!duration) {
    const match =
      text.match(/\b(?:mission|duration|duree|durée)\s+(\d+)\s*(months?|mois)\b/i) ||
      text.match(/\b(\d+)\s*(months?|mois)\b/i);
    if (match) {
      const n = match[1];
      const unit = /mois/i.test(match[2]) ? "months" : match[2].toLowerCase();
      duration = `${n} ${unit}`;
    }
  }

  return { remote, officeDays, pay, currency, duration };
}

export function offerFacts(
  row: CareerOpportunity,
  copy: OfferFactCopy,
  locale = "fr",
  track?: CareerTrack | string,
): OfferFact[] {
  const parsed = parsedFacts(row);
  const raw = Number(row.compensation) > 0 ? Number(row.compensation) : parsed.pay;
  let pay = copy.unknown;
  if (Number.isFinite(raw) && raw > 0) {
    const money = formatMoney(raw, String(row.currency || parsed.currency || "EUR"), locale);
    if (money) {
      const rowTrack = String(row.track || track || "");
      pay = rowTrack === "freelance" ? `${money} ${copy.perDay}` : money;
    }
  }
  return [
    { id: "remote", label: copy.remote, value: offerRemoteLabel(parsed.remote || row.remote, copy) },
    { id: "office", label: copy.office, value: parsed.officeDays || copy.unknown },
    { id: "country", label: copy.country, value: offerCountryValue(row.country, copy.unknown) },
    { id: "city", label: copy.city, value: offerCity(row.location, copy.unknown) },
    { id: "pay", label: copy.pay, value: pay },
    { id: "duration", label: copy.duration, value: parsed.duration || copy.unknown },
  ];
}
