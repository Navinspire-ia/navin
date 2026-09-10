// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { CareerOpportunity, CareerProfile, CareerTrack } from "@/lib/career-api";
import { jobIsRelevant, scoreOpportunity, stableJobId, stripHtml } from "@/lib/career-match";
import { isLinkedInUrl } from "@/lib/career-portals";

export interface WebQuery {
  country: string;
  query: string;
  kind: "web" | "linkedin_open";
  url?: string;
}

const LANG_TERMS: Record<string, string> = {
  fr: "offre OR emploi OR mission",
  en: "jobs OR hiring OR contract",
  de: "Stelle OR Freiberuflich OR Job",
  nl: "vacature OR freelance",
  ar: "وظائف OR jobs",
  es: "oferta OR empleo OR freelance",
  it: "offerta OR lavoro OR freelance",
  pt: "oferta OR emprego OR freelance",
  sv: "jobb OR uppdrag",
  pl: "praca OR zlecenie",
};

const MARKET: Record<string, { label: string; domains: string[]; langs: string[]; aliases: string[]; cities: string[] }> = {
  FR: {
    label: "France",
    langs: ["fr", "en"],
    aliases: ["freelance", "mission", "TJM"],
    cities: ["Paris"],
    // free-work.com has its own public listing reader (backend): no web search slot spent on it.
    domains: ["francetravail.fr", "apec.fr", "welcometothejungle.com", "malt.fr", "chooseyourboss.com"],
  },
  BE: {
    label: "Belgium",
    langs: ["fr", "nl", "en"],
    aliases: ["freelance", "contractor", "consultant"],
    cities: ["Bruxelles", "Brussel"],
    domains: ["ictjob.be", "vdab.be", "leforem.be", "actiris.brussels", "jobat.be", "stepstone.be"],
  },
  CH: {
    label: "Switzerland",
    langs: ["fr", "de", "en"],
    aliases: ["freelance", "contract", "Freiberuflich"],
    cities: ["Zurich", "Geneva"],
    domains: ["jobs.ch", "jobscout24.ch", "swissdevjobs.ch"],
  },
  GB: {
    label: "United Kingdom",
    langs: ["en"],
    aliases: ["contract", "contractor", "Outside IR35"],
    cities: ["London"],
    domains: ["jobserve.com", "cwjobs.co.uk", "reed.co.uk", "totaljobs.com", "technojobs.co.uk", "findajob.dwp.gov.uk"],
  },
  US: {
    label: "USA",
    langs: ["en"],
    aliases: ["contract", "contractor", "1099", "C2C"],
    cities: ["New York"],
    domains: ["dice.com", "wellfound.com", "usajobs.gov", "builtin.com", "ziprecruiter.com"],
  },
  CA: {
    label: "Canada",
    langs: ["en", "fr"],
    aliases: ["contract", "contractor", "contractuel"],
    cities: ["Montreal", "Toronto"],
    domains: ["jobbank.gc.ca", "jobillico.com", "jobboom.com", "eluta.ca"],
  },
  AE: { label: "UAE", langs: ["en", "ar"], aliases: ["contract"], cities: ["Dubai"], domains: ["bayt.com", "gulftalent.com", "naukrigulf.com", "dubaicareers.ae"] },
  SA: { label: "Saudi Arabia", langs: ["en", "ar"], aliases: ["contract"], cities: ["Riyadh"], domains: ["jadarat.sa", "bayt.com"] },
  QA: { label: "Qatar", langs: ["en", "ar"], aliases: ["contract"], cities: ["Doha"], domains: ["bayt.com"] },
  KW: { label: "Kuwait", langs: ["en", "ar"], aliases: ["contract"], cities: ["Kuwait City"], domains: ["bayt.com"] },
  OM: { label: "Oman", langs: ["en", "ar"], aliases: ["contract"], cities: ["Muscat"], domains: ["bayt.com"] },
  BH: { label: "Bahrain", langs: ["en", "ar"], aliases: ["contract"], cities: ["Manama"], domains: ["bayt.com"] },
  MA: { label: "Morocco", langs: ["fr", "en", "ar"], aliases: ["freelance", "mission"], cities: ["Casablanca"], domains: ["rekrute.com", "bayt.com"] },
  TN: { label: "Tunisia", langs: ["fr", "en", "ar"], aliases: ["freelance", "mission"], cities: ["Tunis"], domains: ["tanitjobs.com", "bayt.com"] },
  ES: { label: "Spain", langs: ["es", "en"], aliases: ["freelance"], cities: ["Madrid"], domains: ["infojobs.net"] },
  IT: { label: "Italy", langs: ["it", "en"], aliases: ["freelance"], cities: ["Milan"], domains: ["infojobs.it"] },
  PT: { label: "Portugal", langs: ["pt", "en"], aliases: ["freelance"], cities: ["Lisbon"], domains: ["net-empregos.com"] },
  IE: { label: "Ireland", langs: ["en"], aliases: ["contract"], cities: ["Dublin"], domains: ["jobs.ie"] },
  SE: { label: "Sweden", langs: ["sv", "en"], aliases: ["freelance"], cities: ["Stockholm"], domains: ["arbetsformedlingen.se"] },
  PL: { label: "Poland", langs: ["pl", "en"], aliases: ["freelance"], cities: ["Warsaw"], domains: ["pracuj.pl"] },
  DE: { label: "Germany", langs: ["de", "en"], aliases: ["freelance"], cities: ["Berlin"], domains: ["arbeitsagentur.de", "stepstone.de"] },
  NL: { label: "Netherlands", langs: ["nl", "en"], aliases: ["freelance", "zzp"], cities: ["Amsterdam"], domains: ["werk.nl"] },
};

const NOISE = [
  "google.com",
  "bing.com",
  "duckduckgo.com",
  "youtube.com",
  "facebook.com",
  "instagram.com",
  "twitter.com",
  "x.com",
  "tiktok.com",
  "pinterest.com",
  "wikipedia.org",
];

const JOB_HINT =
  /job|hiring|career|engineer|developer|offre|emploi|mission|freelance|contract|recrut|vacanc|poste|data/i;

const LISTING_TITLE =
  /fiche m[eé]tier|m[eé]tierscope|les fiches m[eé]tiers|\b\d+\s+offres\b|plus de \d+\s+offres|offres d['\u2019]emploi|jobs for (january|february|march|april|may|june|july|august|september|october|november|december)|\bpage \d+\b|^emplois\s*:|is hiring:|find your next job|search\s*-\s*job bank|\bin various locations\b|^engineer jobs\b|\bjobs\s*\|\s*dice/i;

const LISTING_PATH = /metierscope|fiche[-_]?metier|tous-nos-metiers|\/jobsearch\/jobsearch/i;

export function isJobListingPage(title: string, url: string = ""): boolean {
  if (LISTING_TITLE.test(title || "")) return true;
  try {
    return LISTING_PATH.test(new URL(url).pathname);
  } catch {
    return LISTING_PATH.test(url || "");
  }
}

const RESULT_RE = /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
const SNIPPET_RE = /<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g;

export function duckDuckGoUrl(query: string): string {
  return `https://duckduckgo.com/?q=${encodeURIComponent(query.trim() || "Data Engineer jobs")}`;
}

export function webSearchQueries(
  query: string,
  countries: string[],
  track: string = "freelance",
  stack: string[] = [],
): WebQuery[] {
  const role = (query.split(",")[0] || "Data Engineer").trim() || "Data Engineer";
  const kind = track === "freelance" ? "freelance" : "permanent";
  const extra = stack.map((item) => item.trim()).filter(Boolean).slice(0, 3).join(" ");
  const hiring = "(jobs OR hiring OR offre OR emploi OR careers)";
  const perCountry: WebQuery[][] = [];
  const seen = new Set<string>();
  for (const iso of countries.map((item) => item.trim().toUpperCase()).filter(Boolean)) {
    const market = MARKET[iso];
    if (!market) continue;
    const templates = [
      `"${role}" ${kind} ${market.label} ${hiring}`,
      track === "freelance"
        ? `"${role}" contract ${market.label} ${hiring}`
        : `"${role}" ${market.label} remote ${hiring}`,
    ];
    for (const lang of market.langs) {
      const terms = LANG_TERMS[lang];
      if (terms) templates.push(`"${role}" ${kind} ${market.label} (${terms})`);
    }
    if (extra) templates.push(`"${role}" ${extra} ${market.label} ${hiring}`);
    if (track === "freelance") {
      for (const alias of market.aliases.slice(0, 3)) {
        templates.push(`"${role}" ${alias} ${market.label} ${hiring}`);
      }
      if (market.cities[0]) {
        templates.push(`"${role}" ${market.aliases[0] || "contract"} ${market.cities[0]} ${hiring}`);
      }
      if (iso === "FR") templates.push(`"${role}" mission 6 months France ${hiring}`);
      if (iso === "BE") {
        templates.push(`"${role}" freelance Bruxelles ${hiring}`);
        templates.push(`"${role}" freelance Brussel ${hiring}`);
      }
      if (iso === "GB") templates.push(`"${role}" Outside IR35 London remote ${hiring}`);
      if (iso === "US") templates.push(`"${role}" 1099 OR C2C OR "W2 contract" ${hiring}`);
      if (iso === "CA" && market.cities[0]) {
        templates.push(`"${role}" contractuel ${market.cities[0]} ${hiring}`);
      }
    }
    for (const domain of market.domains) {
      templates.push(`site:${domain} "${role}" ${hiring}`);
    }
    const bucket: WebQuery[] = [];
    for (const text of templates) {
      const key = text.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      bucket.push({ country: iso, query: text, kind: "web", url: duckDuckGoUrl(text) });
    }
    if (bucket.length) perCountry.push(bucket);
  }
  const out: WebQuery[] = [];
  const depth = Math.max(0, ...perCountry.map((rows) => rows.length));
  for (let index = 0; index < depth; index += 1) {
    for (const rows of perCountry) {
      if (rows[index]) out.push(rows[index]);
    }
  }
  return out.slice(0, 96);
}

export function webSearchRunPack(
  query: string,
  countries: string[],
  track: string = "freelance",
  stack: string[] = [],
): WebQuery[] {
  const all = webSearchQueries(query, countries, track, stack).filter((row) => row.kind === "web");
  const site = all.filter((row) => row.query.startsWith("site:"));
  const general = all.filter((row) => !row.query.startsWith("site:"));
  const picked: WebQuery[] = [];
  const seen = new Set<string>();
  const used = new Set<string>();
  const take = (row: WebQuery) => {
    const key = row.query.toLowerCase();
    if (!key || seen.has(key) || picked.length >= 12) return;
    seen.add(key);
    picked.push(row);
  };
  for (const row of site) {
    if (used.has(row.country)) continue;
    take(row);
    used.add(row.country);
  }
  for (const row of [...site, ...general]) take(row);
  return picked.slice(0, 12);
}

export function unwrapDdg(href: string): string {
  try {
    const url = new URL(href.startsWith("//") ? `https:${href}` : href);
    const target = url.searchParams.get("uddg");
    if (url.pathname.startsWith("/l/") && target) return decodeURIComponent(target);
    return href.startsWith("//") ? `https:${href}` : href;
  } catch {
    return href;
  }
}

export function parseDdgHtml(html: string): { title: string; url: string; snippet: string }[] {
  const links = [...html.matchAll(RESULT_RE)];
  const snippets = [...html.matchAll(SNIPPET_RE)].map((match) => stripHtml(match[1] || ""));
  return links.slice(0, 8).map((match, index) => ({
    title: stripHtml(match[2] || ""),
    url: unwrapDdg(match[1] || ""),
    snippet: snippets[index] || "",
  }));
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return "";
  }
}

function isNoise(url: string): boolean {
  const host = hostOf(url);
  return NOISE.some((blocked) => host === blocked || host.endsWith(`.${blocked}`));
}

function companyFromTitle(title: string): string {
  const parts = title.split(/\s[-|·•|/]+\s/);
  if (parts.length < 2) return "";
  return parts[parts.length - 1].replace(/\s+(on\s+)?LinkedIn.*$/i, "").trim().slice(0, 80);
}

export function normalizeWebHit(
  hit: { title: string; url: string; snippet: string },
  country: string,
  track: CareerTrack | string,
  profile: CareerProfile,
): CareerOpportunity | null {
  const title = stripHtml(hit.title).trim();
  const url = hit.url.trim();
  const snippet = stripHtml(hit.snippet).trim();
  if (!title || !url || isNoise(url)) return null;
  if (isJobListingPage(title, url)) return null;
  if (!JOB_HINT.test(`${title} ${snippet}`)) return null;
  if (!jobIsRelevant({ title, url, description: snippet, source: "web" }, profile)) return null;
  const linkedin = isLinkedInUrl(url);
  return scoreOpportunity(
    {
      id: stableJobId(linkedin ? "linkedin" : "web", url, title),
      source: linkedin ? "linkedin" : "web",
      title: title.slice(0, 180),
      company: companyFromTitle(title),
      country,
      description: snippet.slice(0, 4000),
      url,
      track,
      stage: "discovered",
      remote: /remote|teletravail|télétravail/i.test(`${title} ${snippet}`) ? "remote" : "",
      attribution: "Web search",
    },
    profile,
  );
}

async function searchOne(query: string, signal?: AbortSignal): Promise<{ title: string; url: string; snippet: string }[]> {
  const response = await fetch(`/career-web-search?q=${encodeURIComponent(query)}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error(`Web search ${response.status}`);
  const payload = (await response.json()) as { title?: string; url?: string; snippet?: string }[];
  if (!Array.isArray(payload) || !payload.length) {
    const html = await fetch("/ddg-html/", {
      method: "POST",
      headers: { Accept: "text/html", "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ q: query }).toString(),
      signal,
    }).then((item) => item.text()).catch(() => "");
    if (!html || html.includes("anomaly-modal")) return [];
    return parseDdgHtml(html);
  }
  return payload.map((item) => ({
    title: String(item.title || ""),
    url: String(item.url || ""),
    snippet: String(item.snippet || ""),
  }));
}

export async function fetchWebSearchOpportunities(
  query: string,
  countries: string[],
  profile: CareerProfile,
  track: CareerTrack | string,
  stack: string[] = [],
  signal?: AbortSignal,
): Promise<CareerOpportunity[]> {
  const pack = webSearchRunPack(query, countries, String(track), stack).slice(0, 12);
  const byId = new Map<string, CareerOpportunity>();
  for (let index = 0; index < pack.length; index += 2) {
    if (signal?.aborted) break;
    const chunk = pack.slice(index, index + 2);
    const settled = await Promise.allSettled(
      chunk.map(async (row) => {
        const hits = await searchOne(row.query, signal);
        return hits
          .map((hit) => normalizeWebHit(hit, row.country, track, profile))
          .filter((item): item is CareerOpportunity => Boolean(item));
      }),
    );
    for (const result of settled) {
      if (result.status !== "fulfilled") continue;
      for (const row of result.value) byId.set(row.id, row);
    }
  }
  return [...byId.values()].slice(0, 30);
}
