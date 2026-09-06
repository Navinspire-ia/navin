import type {
  CareerInboxItem,
  CareerOpportunity,
  CareerProfile,
  CareerTrack,
  MatchReason,
} from "@/lib/career-api";
import { stripHtml } from "@/lib/plain-text";

export { stripHtml };

const MARKETS: Record<string, string> = {
  FR: "France",
  DE: "Germany",
  GB: "United Kingdom",
  CH: "Switzerland",
  BE: "Belgium",
  NL: "Netherlands",
  AE: "UAE",
  SA: "Saudi Arabia",
  QA: "Qatar",
  KW: "Kuwait",
  OM: "Oman",
  BH: "Bahrain",
  MA: "Morocco",
  TN: "Tunisia",
  ES: "Spain",
  IT: "Italy",
  PT: "Portugal",
  IE: "Ireland",
  SE: "Sweden",
  PL: "Poland",
  US: "USA",
  CA: "Canada",
  REMOTE: "Remote",
};

const TITLE_STOP = new Set([
  "senior",
  "sr",
  "junior",
  "jr",
  "lead",
  "staff",
  "principal",
  "freelance",
  "contract",
  "contractor",
  "remote",
  "intern",
  "internship",
  "job",
  "jobs",
  "hiring",
  "independent",
  "the",
  "and",
  "for",
  "with",
  "from",
]);
const GENERIC_ROLE = new Set([
  "engineer",
  "developer",
  "consultant",
  "specialist",
  "manager",
  "analyst",
  "technician",
  "officer",
  "associate",
  "architect",
]);
const SYNONYMS: Record<string, string[]> = { ai: ["ai", "ia"], ml: ["ml"], nlp: ["nlp"] };
const PHRASES: Record<string, string[]> = {
  ai: ["artificial intelligence", "intelligence artificielle"],
  ml: ["machine learning", "apprentissage automatique"],
};
const RELATED: Record<string, string[]> = {
  ai: ["ml", "llm", "genai", "nlp", "machine", "learning"],
};
const OFF_ROLE =
  /\b(sales jedi|support jedi|inside sales|office assistant|account executive|customer support|sales representative|sales contractor|receptionist|talent acquisition|recruiter|copywriter|jedi)\b|\bsales\b/i;
const CITIZEN =
  /citizenship is required|must be (an? )?(us|u\.s\.|united states) citizen|us citizen(ship)? required|security clearance required/i;

function tokens(text: string): Set<string> {
  return new Set((text || "").toLowerCase().match(/[a-zA-Z0-9+#.]{2,}/g) || []);
}

function asList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).map((item) => item.trim()).filter(Boolean);
  if (typeof value === "string") return value.split(/[,;]/).map((item) => item.trim()).filter(Boolean);
  return [];
}

function roleTokens(text: string): Set<string> {
  return new Set([...tokens(text)].filter((item) => !TITLE_STOP.has(item)));
}

function titleHasToken(titleLow: string, titleTokens: Set<string>, token: string): boolean {
  const aliases = SYNONYMS[token] || [token];
  if (aliases.some((alias) => titleTokens.has(alias))) return true;
  return (PHRASES[token] || []).some((phrase) => titleLow.includes(phrase));
}

export function titleMatchPct(jobTitle: string, profile: CareerProfile): number {
  const wantedTitles = asList(profile.titles).map(roleTokens).filter((item) => item.size);
  if (!wantedTitles.length) return 70;
  const titleLow = (jobTitle || "").toLowerCase();
  const titleTokens = roleTokens(jobTitle);
  let best = 0;
  for (const wanted of wantedTitles) {
    const distinctive = [...wanted].filter((item) => !GENERIC_ROLE.has(item));
    const hits = [...wanted].filter((item) => titleHasToken(titleLow, titleTokens, item));
    if (distinctive.length && !distinctive.some((item) => titleHasToken(titleLow, titleTokens, item))) {
      const related = new Set(distinctive.flatMap((item) => RELATED[item] || []));
      const relatedHit = [...related].some((item) => titleTokens.has(item));
      best = Math.max(best, relatedHit ? 70 : 25);
      continue;
    }
    const ratio = hits.length / wanted.size;
    if (ratio >= 0.5) best = Math.max(best, 90);
    else if (hits.length) best = Math.max(best, 55);
    else best = Math.max(best, 25);
  }
  return best;
}

export function isOffRole(title: string, profile: CareerProfile): boolean {
  if (!OFF_ROLE.test(title || "")) return false;
  const wanted = asList(profile.titles).join(" ").toLowerCase();
  return !/\b(sales|recruiter|assistant|jedi)\b/.test(wanted);
}

export function requiresRestrictedEligibility(
  row: Pick<CareerOpportunity, "title"> & Partial<CareerOpportunity>,
  profile: CareerProfile,
): boolean {
  const hay = `${row.title || ""} ${row.description || ""}`;
  if (!CITIZEN.test(hay)) return false;
  const visa = String(profile.visa || "none").trim().toLowerCase();
  if (visa && visa !== "none" && visa !== "open") return false;
  const primary = new Set(asList(profile.countries_primary).map((item) => item.toUpperCase()));
  return !primary.has("US");
}

export function jobIsRelevant(
  row: Pick<CareerOpportunity, "title"> & Partial<CareerOpportunity>,
  profile: CareerProfile,
): boolean {
  const title = row.title || "";
  if (isOffRole(title, profile)) return false;
  if (requiresRestrictedEligibility(row, profile)) return false;
  return titleMatchPct(title, profile) >= 50;
}

export function guessTrack(title: string, fallback: CareerTrack | string = "jobs"): CareerTrack {
  const hay = (title || "").toLowerCase();
  if (/(freelance|contract|contractor|mission|consultant|tjm)/.test(hay)) return "freelance";
  if (fallback === "freelance" || fallback === "jobs") return fallback;
  return "jobs";
}

export function scoreOpportunity(
  row: CareerOpportunity,
  profile: CareerProfile,
): CareerOpportunity {
  const title = row.title || "";
  const description = stripHtml(row.description || "");
  const hay = tokens(`${title} ${row.company || ""} ${description} ${(row.stack || []).join(" ")}`);
  const stack = asList(profile.stack).map((item) => item.toLowerCase());
  const stackHits = stack.filter((item) => hay.has(item));
  const stackMissing = stack.filter((item) => !hay.has(item));
  const stackPct = stack.length ? (100 * stackHits.length) / stack.length : 70;

  const country = String(row.country || "").toUpperCase();
  const weights = profile.country_weights || {};
  const primary = new Set(asList(profile.countries_primary).map((item) => item.toUpperCase()));
  const secondary = new Set(asList(profile.countries_secondary).map((item) => item.toUpperCase()));
  const excluded = new Set(asList(profile.countries_excluded).map((item) => item.toUpperCase()));
  let countryPct = 45;
  if (country && excluded.has(country)) countryPct = 0;
  else if (country && country in weights) countryPct = Number(weights[country]) || 70;
  else if (primary.has(country)) countryPct = 100;
  else if (secondary.has(country)) countryPct = 70;
  else if (["REMOTE", "WW", ""].includes(country)) countryPct = 80;

  const workMode = String(profile.work_mode || "any").toLowerCase();
  const remote = String(row.remote || "").toLowerCase();
  let modePct = 80;
  if (workMode === "remote" && ["remote", "yes", "true"].includes(remote)) modePct = 100;
  else if (workMode && workMode !== "any" && workMode === remote) modePct = 95;
  else if (workMode && workMode !== "any") modePct = 40;

  const track = String(row.track || profile.track || "jobs");
  const floor = track === "freelance" ? Number(profile.min_rate || 0) : Number(profile.min_salary || 0);
  const ceiling = track === "freelance" ? Number(profile.max_rate || 0) : Number(profile.max_salary || 0);
  const comp = Number(row.compensation || 0);
  let payPct = 70;
  if (floor > 0 && comp > 0) payPct = comp >= floor ? 100 : Math.max(20, 100 * (comp / floor));
  if (ceiling > 0 && comp > ceiling * 1.35) payPct = Math.min(payPct, 65);

  const langs = asList(profile.languages).map((item) => item.toLowerCase().slice(0, 2));
  const rowLangs = new Set(asList(row.languages).map((item) => item.toLowerCase().slice(0, 2)));
  const langHits = langs.filter((item) => hay.has(item) || rowLangs.has(item));
  const langPct = langs.length ? (100 * langHits.length) / langs.length : 75;
  const titlePct = titleMatchPct(title, profile);
  let score = Math.round(
    0.22 * titlePct + 0.22 * stackPct + 0.18 * countryPct + 0.14 * payPct + 0.12 * modePct + 0.12 * langPct,
  );
  const strengths = asList(profile.strengths).map((item) => item.toLowerCase());
  const strengthHits = strengths.filter((item) => {
    const wanted = tokens(item);
    if (!wanted.size) return false;
    for (const token of wanted) if (hay.has(token)) return true;
    return false;
  });
  if (strengthHits.length) score = Math.min(100, score + Math.min(8, 2 * strengthHits.length));
  const profileTrack = String(profile.track || "").trim().toLowerCase();
  if (profileTrack && profileTrack !== "both" && track && profileTrack !== track) {
    score = Math.max(20, score - 8);
  }
  if (country && excluded.has(country)) score = Math.min(score, 24);
  if (isOffRole(title, profile) || requiresRestrictedEligibility(row, profile)) score = Math.min(score, 35);
  if (titlePct < 50) score = Math.min(score, 48);

  const reasons: MatchReason[] = [];
  for (const item of stackHits) reasons.push({ label: item, ok: true });
  for (const item of stackMissing.slice(0, 4)) reasons.push({ label: item, ok: false });
  for (const item of langs) reasons.push({ label: item, ok: langHits.includes(item) });
  for (const item of strengthHits.slice(0, 4)) reasons.push({ label: item, ok: true });
  if (country) reasons.push({ label: MARKETS[country] || country, ok: countryPct >= 70 });

  const bucket = score >= 85 ? "perfect" : score >= 65 ? "good" : "skip";
  const stage =
    row.stage && row.stage !== "discovered"
      ? row.stage
      : bucket === "skip"
        ? "discovered"
        : "matched";
  return { ...row, description: description || row.description, match_score: score, match_reasons: reasons, bucket, stage };
}

export function classifyReply(text: string): CareerInboxItem["classification"] {
  const lower = (text || "").toLowerCase();
  if (/(interview|entretien|call next|technical interview)/.test(lower)) return "interview";
  if (/(offer|offre|package)/.test(lower)) return "offer";
  if (/(reject|filled|unfortunately|refus)/.test(lower)) return "rejected";
  if (/(rate|tjm|daily|salary|please provide)/.test(lower)) return "need_response";
  if (/(pleased|interested|positive|next step)/.test(lower)) return "positive";
  return "waiting";
}

export function draftFollowup(
  job: Pick<CareerOpportunity, "title" | "company">,
  wave: "j3" | "j7" = "j3",
): string {
  const title = job.title || "the role";
  const company = job.company || "your team";
  if (wave === "j7") {
    return `Hello,\n\nI am following up a second time on the ${title} conversation with ${company}. Happy to share availability or a clarified rate if useful.\n\nBest regards`;
  }
  return `Hello,\n\nI wanted to follow up on the ${title} opportunity at ${company}. I remain available and glad to share any extra material.\n\nBest regards`;
}

/** SHA-1 hex, same bytes as Python hashlib.sha1(text.encode("utf-8")). */
export function sha1Hex(text: string): string {
  const msg = new TextEncoder().encode(text);
  const bitLen = msg.length * 8;
  const paddedLen = (((msg.length + 9 + 63) >> 6) << 6);
  const padded = new Uint8Array(paddedLen);
  padded.set(msg);
  padded[msg.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLen - 4, bitLen >>> 0, false);
  let h0 = 0x67452301;
  let h1 = 0xefcdab89;
  let h2 = 0x98badcfe;
  let h3 = 0x10325476;
  let h4 = 0xc3d2e1f0;
  const w = new Uint32Array(80);
  for (let offset = 0; offset < paddedLen; offset += 64) {
    for (let i = 0; i < 16; i += 1) w[i] = view.getUint32(offset + i * 4, false);
    for (let i = 16; i < 80; i += 1) {
      const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
      w[i] = (x << 1) | (x >>> 31);
    }
    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;
    for (let i = 0; i < 80; i += 1) {
      let f: number;
      let k: number;
      if (i < 20) {
        f = (b & c) | (~b & d);
        k = 0x5a827999;
      } else if (i < 40) {
        f = b ^ c ^ d;
        k = 0x6ed9eba1;
      } else if (i < 60) {
        f = (b & c) | (b & d) | (c & d);
        k = 0x8f1bbcdc;
      } else {
        f = b ^ c ^ d;
        k = 0xca62c1d6;
      }
      const temp = (((a << 5) | (a >>> 27)) + f + e + k + (w[i] >>> 0)) >>> 0;
      e = d;
      d = c;
      c = ((b << 30) | (b >>> 2)) >>> 0;
      b = a;
      a = temp;
    }
    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }
  return [h0, h1, h2, h3, h4].map((word) => word.toString(16).padStart(8, "0")).join("");
}

export function normalizeJobUrl(url: string): string {
  const raw = (url || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw.includes("://") ? raw : `https://${raw}`);
    let host = parsed.hostname.toLowerCase();
    if (host.startsWith("www.")) host = host.slice(4);
    const path = parsed.pathname.replace(/\/+$/, "");
    const query = parsed.search.replace(/^\?/, "");
    if (!host && !path) return raw.toLowerCase();
    return `${host}${path}` + (query ? `?${query}` : "");
  } catch {
    return raw.toLowerCase();
  }
}

export function stableJobId(source: string, url: string, title: string): string {
  return `job-${sha1Hex(`${source}|${url}|${title}`).slice(0, 12)}`;
}

export function normalizeRemotive(
  item: Record<string, unknown>,
  track: CareerTrack | string,
  profile: CareerProfile,
): CareerOpportunity {
  const title = String(item.title || "").trim();
  const url = String(item.url || "").trim();
  const tags = Array.isArray(item.tags) ? item.tags.map(String).slice(0, 8) : [];
  const row: CareerOpportunity = {
    id: stableJobId("remotive", url, title),
    source: "remotive",
    title,
    company: String(item.company_name || "").trim(),
    location: String(item.candidate_required_location || "Remote"),
    country: "REMOTE",
    stack: tags,
    remote: "remote",
    posted_at: String(item.publication_date || ""),
    description: stripHtml(String(item.description || "")).slice(0, 4000),
    url,
    track: guessTrack(title, track),
    stage: "discovered",
    attribution: "Remotive",
  };
  return scoreOpportunity(row, { ...profile, track });
}

export function dedupeApplications<T extends { id?: string; opportunity_id?: string }>(apps: T[]): T[] {
  const byOid = new Map<string, T>();
  const orphans: T[] = [];
  for (const app of apps) {
    const oid = String(app.opportunity_id || "").trim();
    if (!oid) {
      orphans.push(app);
      continue;
    }
    byOid.set(oid, app);
  }
  return [...byOid.values(), ...orphans];
}

export function computeCareerKpis(
  rows: CareerOpportunity[],
  apps: { id?: string }[],
  inbox: CareerInboxItem[],
) {
  const strong = rows.filter((row) => (row.match_score || 0) >= 80);
  const applied = rows.filter((row) =>
    ["applied", "replied", "interview", "offer", "won"].includes(String(row.stage)),
  );
  const replies = inbox.filter((item) => item.classification && item.classification !== "waiting");
  const interviews = rows.filter((row) => row.stage === "interview");
  const offers = rows.filter((row) => row.stage === "offer" || row.stage === "won");
  const scores = rows.map((row) => Number(row.match_score || 0)).filter((value) => value > 0);
  const avg = scores.length ? Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 10) / 10 : 0;
  return {
    opportunities: rows.length,
    strong: strong.length,
    applications: apps.length || applied.length,
    replies: replies.length,
    interviews: interviews.length,
    offers: offers.length,
    avg_score: avg,
    response_rate: applied.length ? Math.round((1000 * replies.length) / applied.length) / 10 : 0,
    headline: `${rows.length} opportunities, ${strong.length} strong matches, ${apps.length || applied.length} applications, ${interviews.length} interviews.`,
  };
}
