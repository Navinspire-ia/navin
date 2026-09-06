import type { CareerOpportunity, CareerProfile, CareerTrack } from "@/lib/career-api";
import { scoreOpportunity, stableJobId, stripHtml } from "@/lib/career-match";

export type PortalKind = "linkedin" | "board" | "api";

export interface OfficialPortal {
  id: string;
  label: string;
  url: string;
  country?: string;
  kind: PortalKind;
}

const MARKET_LABEL: Record<string, string> = {
  FR: "France",
  DE: "Germany",
  GB: "United Kingdom",
  CH: "Switzerland",
  BE: "Belgium",
  NL: "Netherlands",
  AE: "United Arab Emirates",
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
  US: "United States",
  CA: "Canada",
};

function enc(value: string): string {
  return encodeURIComponent(value.trim() || "Data Engineer");
}

function linkedinSearch(query: string, country: string, track: string, workMode: string): string {
  const params = new URLSearchParams();
  params.set("keywords", query.trim() || "Data Engineer");
  const location = MARKET_LABEL[country] || "";
  if (location) params.set("location", location);
  if (track === "freelance") params.set("f_JT", "C");
  if (workMode === "remote") params.set("f_WT", "2");
  return `https://www.linkedin.com/jobs/search/?${params.toString()}`;
}

function countryBoards(country: string, q: string): OfficialPortal[] {
  const map: Record<string, OfficialPortal[]> = {
    FR: [
      { id: "fr-freework", label: "Free-Work", url: `https://www.free-work.com/fr/tech-it/jobs?query=${q}`, country: "FR", kind: "board" },
      { id: "fr-ft", label: "France Travail", url: `https://candidat.francetravail.fr/offres/recherche?motsCles=${q}&offresPartenaires=true`, country: "FR", kind: "api" },
      { id: "fr-apec", label: "APEC", url: `https://www.apec.fr/candidat/recherche-emploi.html/emploi?motsCles=${q}`, country: "FR", kind: "board" },
      { id: "fr-wttj", label: "Welcome to the Jungle", url: `https://www.welcometothejungle.com/fr/jobs?query=${q}`, country: "FR", kind: "board" },
      { id: "fr-malt", label: "Malt", url: `https://www.malt.fr/s?q=${q}`, country: "FR", kind: "board" },
      { id: "fr-cyb", label: "ChooseYourBoss", url: `https://www.chooseyourboss.com/offres?q=${q}`, country: "FR", kind: "board" },
      { id: "fr-indeed", label: "Indeed France", url: `https://fr.indeed.com/jobs?q=${q}`, country: "FR", kind: "board" },
    ],
    DE: [
      { id: "de-ba", label: "Arbeitsagentur", url: `https://www.arbeitsagentur.de/jobsuche/suche?angebotsart=1&was=${q}`, country: "DE", kind: "board" },
      { id: "de-step", label: "StepStone", url: `https://www.stepstone.de/jobs/${q}`, country: "DE", kind: "board" },
    ],
    GB: [
      { id: "gb-jobserve", label: "JobServe", url: `https://www.jobserve.com/gb/en/JobSearch.aspx?shid=${q}`, country: "GB", kind: "board" },
      { id: "gb-cw", label: "CWJobs", url: `https://www.cwjobs.co.uk/jobs/${q}`, country: "GB", kind: "board" },
      { id: "gb-reed", label: "Reed", url: `https://www.reed.co.uk/jobs/${q}`, country: "GB", kind: "board" },
      { id: "gb-total", label: "Totaljobs", url: `https://www.totaljobs.com/jobs/${q}`, country: "GB", kind: "board" },
      { id: "gb-techno", label: "Technojobs", url: `https://www.technojobs.co.uk/search.phtml?kw=${q}`, country: "GB", kind: "board" },
      { id: "gb-gov", label: "GOV.UK Find a Job", url: `https://findajob.dwp.gov.uk/search?q=${q}`, country: "GB", kind: "board" },
    ],
    CH: [
      { id: "ch-jobs", label: "jobs.ch", url: `https://www.jobs.ch/en/vacancies/?term=${q}`, country: "CH", kind: "board" },
      { id: "ch-scout", label: "JobScout24", url: `https://www.jobscout24.ch/en?q=${q}`, country: "CH", kind: "board" },
      { id: "ch-swissdev", label: "SwissDevJobs", url: `https://swissdevjobs.ch/jobs?q=${q}`, country: "CH", kind: "board" },
    ],
    BE: [
      { id: "be-ict", label: "ICTjob", url: `https://www.ictjob.be/fr/search?q=${q}`, country: "BE", kind: "board" },
      { id: "be-vdab", label: "VDAB", url: `https://www.vdab.be/vindeenjob/vacatures?trefwoord=${q}`, country: "BE", kind: "board" },
      { id: "be-forem", label: "Le Forem", url: `https://www.leforem.be/chercher-un-emploi.html?q=${q}`, country: "BE", kind: "board" },
      { id: "be-actiris", label: "Actiris", url: `https://www.actiris.brussels/fr/citoyens/offres-d-emploi/?q=${q}`, country: "BE", kind: "board" },
      { id: "be-jobat", label: "Jobat", url: `https://www.jobat.be/fr/jobs?q=${q}`, country: "BE", kind: "board" },
      { id: "be-step", label: "StepStone Belgium", url: `https://www.stepstone.be/jobs/${q}`, country: "BE", kind: "board" },
    ],
    NL: [{ id: "nl-werk", label: "Werk.nl", url: `https://www.werk.nl/werkzoekenden/vacatures?zoekwoord=${q}`, country: "NL", kind: "board" }],
    AE: [
      { id: "ae-bayt", label: "Bayt UAE", url: `https://www.bayt.com/en/uae/jobs/q/${q}/`, country: "AE", kind: "board" },
      { id: "ae-gulf", label: "GulfTalent", url: `https://www.gulftalent.com/jobs?search=${q}`, country: "AE", kind: "board" },
      { id: "ae-dubai", label: "Dubai Careers", url: `https://dubaicareers.ae/`, country: "AE", kind: "board" },
    ],
    SA: [
      { id: "sa-jadarat", label: "Jadarat", url: `https://jadarat.sa/`, country: "SA", kind: "board" },
      { id: "sa-bayt", label: "Bayt KSA", url: `https://www.bayt.com/en/saudi-arabia/jobs/q/${q}/`, country: "SA", kind: "board" },
      { id: "sa-gulf", label: "GulfTalent KSA", url: `https://www.gulftalent.com/saudi-arabia/jobs?search=${q}`, country: "SA", kind: "board" },
    ],
    QA: [
      { id: "qa-bayt", label: "Bayt Qatar", url: `https://www.bayt.com/en/qatar/jobs/q/${q}/`, country: "QA", kind: "board" },
      { id: "qa-gulf", label: "GulfTalent Qatar", url: `https://www.gulftalent.com/qatar/jobs?search=${q}`, country: "QA", kind: "board" },
    ],
    KW: [{ id: "kw-bayt", label: "Bayt Kuwait", url: `https://www.bayt.com/en/kuwait/jobs/q/${q}/`, country: "KW", kind: "board" }],
    OM: [{ id: "om-bayt", label: "Bayt Oman", url: `https://www.bayt.com/en/oman/jobs/q/${q}/`, country: "OM", kind: "board" }],
    BH: [{ id: "bh-bayt", label: "Bayt Bahrain", url: `https://www.bayt.com/en/bahrain/jobs/q/${q}/`, country: "BH", kind: "board" }],
    MA: [
      { id: "ma-rekrute", label: "Rekrute", url: `https://www.rekrute.com/offres.html?q=${q}`, country: "MA", kind: "board" },
      { id: "ma-bayt", label: "Bayt Morocco", url: `https://www.bayt.com/en/morocco/jobs/q/${q}/`, country: "MA", kind: "board" },
    ],
    TN: [
      { id: "tn-tanit", label: "TanitJobs", url: `https://www.tanitjobs.com/jobs/?q=${q}`, country: "TN", kind: "board" },
      { id: "tn-bayt", label: "Bayt Tunisia", url: `https://www.bayt.com/en/tunisia/jobs/q/${q}/`, country: "TN", kind: "board" },
    ],
    ES: [{ id: "es-infojobs", label: "InfoJobs", url: `https://www.infojobs.net/jobsearch/search-results/list.xhtml?keyword=${q}`, country: "ES", kind: "board" }],
    IT: [{ id: "it-infojobs", label: "InfoJobs Italy", url: `https://www.infojobs.it/jobsearch/search-results/list.xhtml?keyword=${q}`, country: "IT", kind: "board" }],
    PT: [{ id: "pt-netemp", label: "Net-Empregos", url: `https://www.net-empregos.com/pesquisa-empregos.asp?chaves=${q}`, country: "PT", kind: "board" }],
    IE: [{ id: "ie-jobs", label: "Jobs.ie", url: `https://www.jobs.ie/jobs?q=${q}`, country: "IE", kind: "board" }],
    SE: [{ id: "se-af", label: "Arbetsformedlingen", url: `https://arbetsformedlingen.se/platsbanken/annonser?q=${q}`, country: "SE", kind: "board" }],
    PL: [{ id: "pl-pracuj", label: "Pracuj.pl", url: `https://www.pracuj.pl/praca/${q}`, country: "PL", kind: "board" }],
    US: [
      { id: "us-dice", label: "Dice", url: `https://www.dice.com/jobs?q=${q}`, country: "US", kind: "board" },
      { id: "us-wellfound", label: "Wellfound", url: `https://wellfound.com/role/l/${q}`, country: "US", kind: "board" },
      { id: "us-builtin", label: "Built In", url: `https://builtin.com/jobs?search=${q}`, country: "US", kind: "board" },
      { id: "us-usa", label: "USAJOBS", url: `https://www.usajobs.gov/Search/Results?k=${q}`, country: "US", kind: "api" },
    ],
    CA: [
      { id: "ca-bank", label: "Job Bank Canada", url: `https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=${q}`, country: "CA", kind: "board" },
      { id: "ca-jobillico", label: "Jobillico", url: `https://www.jobillico.com/search-jobs?k=${q}`, country: "CA", kind: "board" },
      { id: "ca-jobboom", label: "Jobboom", url: `https://www.jobboom.com/en/jobs?q=${q}`, country: "CA", kind: "board" },
      { id: "ca-eluta", label: "Eluta", url: `https://www.eluta.ca/search?q=${q}`, country: "CA", kind: "board" },
    ],
  };
  return map[country] || [];
}

/** Official search pages only. LinkedIn is opened in the user browser, never scraped. */
export function officialSearchPack(
  query: string,
  countries: string[],
  track: string = "freelance",
  workMode: string = "remote",
): OfficialPortal[] {
  const q = enc(query);
  const isos = countries.map((item) => item.trim().toUpperCase()).filter(Boolean);
  const out: OfficialPortal[] = [];
  const seen = new Set<string>();
  const push = (row: OfficialPortal) => {
    if (seen.has(row.id)) return;
    seen.add(row.id);
    out.push(row);
  };
  for (const iso of isos) {
    push({
      id: `li-${iso}`,
      label: `LinkedIn ${MARKET_LABEL[iso] || iso}`,
      url: linkedinSearch(query, iso, track, workMode),
      country: iso,
      kind: "linkedin",
    });
  }
  push({
    id: "remotive",
    label: "Remotive",
    url: `https://remotive.com/remote-jobs?search=${q}`,
    kind: "api",
  });
  const perCountry = isos.map((iso) => countryBoards(iso, q));
  const depth = Math.max(0, ...perCountry.map((rows) => rows.length));
  for (let index = 0; index < depth; index += 1) {
    for (const rows of perCountry) {
      if (rows[index]) push(rows[index]);
    }
  }
  return out.slice(0, 48);
}

const CLOSED_HOSTS = [
  "linkedin.com",
  "lnkd.in",
  "indeed.com",
  "indeed.fr",
  "indeed.be",
  "indeed.ch",
  "indeed.co.uk",
  "indeed.ca",
  "ziprecruiter.com",
  "jobserve.com",
  "cwjobs.co.uk",
  "ictjob.be",
  "jobat.be",
  "vdab.be",
  "leforem.be",
  "actiris.brussels",
  "stepstone.be",
  "jobillico.com",
  "jobboom.com",
  "eluta.ca",
  "wellfound.com",
  "bayt.com",
  "welcometothejungle.com",
  "apec.fr",
  "malt.fr",
  "malt.com",
  "free-work.com",
  "chooseyourboss.com",
  "gulftalent.com",
  "naukrigulf.com",
  "jadarat.sa",
  "dubaicareers.ae",
  "stepstone.de",
  "stepstone.com",
  "reed.co.uk",
  "totaljobs.com",
  "technojobs.co.uk",
  "contractoruk.com",
  "dice.com",
  "builtin.com",
  "themuse.com",
  "jobs.ch",
  "jobscout24.ch",
  "swissdevjobs.ch",
  "jobbank.gc.ca",
  "werk.nl",
  "arbeitsagentur.de",
  "findajob.dwp.gov.uk",
  "rekrute.com",
  "emploi.ma",
  "tanitjobs.com",
  "keejob.com",
  "infojobs.net",
  "infojobs.it",
  "infoempleo.com",
  "net-empregos.com",
  "jobs.ie",
  "irishjobs.ie",
  "arbetsformedlingen.se",
  "pracuj.pl",
];

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return "";
  }
}

export function isLinkedInUrl(url: string): boolean {
  const host = hostOf(url);
  return host === "linkedin.com" || host.endsWith(".linkedin.com") || host === "lnkd.in" || /linkedin\.com/i.test(url);
}

export function isClosedJobUrl(url: string): boolean {
  const host = hostOf(url);
  if (!host) return /linkedin\.com/i.test(url);
  return CLOSED_HOSTS.some((blocked) => host === blocked || host.endsWith(`.${blocked}`));
}

export function parsePastedOffer(input: {
  url?: string;
  title?: string;
  company?: string;
  body?: string;
  country?: string;
  track?: CareerTrack | string;
}): CareerOpportunity | null {
  const url = String(input.url || "").trim();
  const body = stripHtml(String(input.body || ""));
  const lines = body.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const title = String(input.title || lines[0] || "").trim();
  const company = String(input.company || lines[1] || "").trim();
  if (!title && !url) return null;
  const linkedin = isLinkedInUrl(url);
  return {
    id: stableJobId(linkedin ? "linkedin" : "import", url || title, title),
    source: linkedin ? "linkedin" : "import",
    title: title || "Imported offer",
    company,
    location: "",
    country: String(input.country || "").toUpperCase() || (linkedin ? "" : ""),
    description: body.slice(0, 4000),
    url,
    track: input.track || "freelance",
    stage: "discovered",
    remote: /remote|teletravail|télétravail/i.test(`${title} ${body}`) ? "remote" : "",
  };
}

type AtsJob = Record<string, unknown>;

function asText(value: unknown): string {
  return value == null ? "" : String(value);
}

async function fetchJson(url: string, timeoutMs = 7000, signal?: AbortSignal): Promise<Response | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const onParent = () => ctrl.abort();
  signal?.addEventListener("abort", onParent, { once: true });
  if (signal?.aborted) {
    clearTimeout(timer);
    return null;
  }
  try {
    return await fetch(url, { headers: { Accept: "application/json" }, signal: ctrl.signal });
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onParent);
  }
}

export async function fetchOfficialAtsBoards(
  boards: string[],
  profile: CareerProfile,
  track: CareerTrack | string,
  signal?: AbortSignal,
): Promise<CareerOpportunity[]> {
  const slugs = boards.map((item) => item.trim().toLowerCase()).filter(Boolean).slice(0, 8);
  const chunks = await Promise.all(
    slugs.map(async (slug) => {
      if (signal?.aborted) return [];
      const greenhouse = await fetchGreenhouseBoard(slug, signal);
      if (greenhouse.length) return greenhouse;
      const lever = await fetchLeverBoard(slug, signal);
      if (lever.length) return lever;
      return fetchAshbyBoard(slug, signal);
    }),
  );
  return chunks
    .flat()
    .filter((row) => row.title && row.url)
    .map((row) => scoreOpportunity({ ...row, track: row.track || track }, profile));
}

async function fetchGreenhouseBoard(slug: string, signal?: AbortSignal): Promise<CareerOpportunity[]> {
  const response = await fetchJson(`/ats-greenhouse/${encodeURIComponent(slug)}/jobs?content=true`, 7000, signal);
  if (!response?.ok) return [];
  const payload = (await response.json()) as { jobs?: AtsJob[] };
  return (payload.jobs || []).slice(0, 25).map((job) => {
    const title = asText(job.title);
    const loc = job.location && typeof job.location === "object" ? asText((job.location as AtsJob).name) : "";
    const url = asText(job.absolute_url);
    return {
      id: stableJobId("greenhouse", url, title),
      source: "greenhouse",
      title,
      company: slug,
      location: loc,
      country: "",
      description: stripHtml(asText(job.content)).slice(0, 4000),
      url,
      track: /contract|freelance/i.test(title) ? "freelance" : "jobs",
      stage: "discovered",
      remote: /remote/i.test(`${title} ${loc}`) ? "remote" : "",
    } as CareerOpportunity;
  });
}

async function fetchLeverBoard(slug: string, signal?: AbortSignal): Promise<CareerOpportunity[]> {
  const response = await fetchJson(`/ats-lever/${encodeURIComponent(slug)}?mode=json`, 7000, signal);
  if (!response?.ok) return [];
  const jobs = (await response.json()) as AtsJob[];
  if (!Array.isArray(jobs)) return [];
  return jobs.slice(0, 25).map((job) => {
    const title = asText(job.text);
    const cats = job.categories && typeof job.categories === "object" ? (job.categories as AtsJob) : {};
    const loc = asText(cats.location);
    const url = asText(job.hostedUrl || job.applyUrl);
    return {
      id: stableJobId("lever", url, title),
      source: "lever",
      title,
      company: slug,
      location: loc,
      description: stripHtml(asText(job.descriptionPlain || job.description)).slice(0, 4000),
      url,
      track: /contract|freelance/i.test(title) ? "freelance" : "jobs",
      stage: "discovered",
      remote: /remote/i.test(`${title} ${loc}`) ? "remote" : "",
    } as CareerOpportunity;
  });
}

async function fetchAshbyBoard(slug: string, signal?: AbortSignal): Promise<CareerOpportunity[]> {
  const response = await fetchJson(`/ats-ashby/${encodeURIComponent(slug)}?includeCompensation=true`, 7000, signal);
  if (!response?.ok) return [];
  const payload = (await response.json()) as { jobs?: AtsJob[] };
  return (payload.jobs || []).slice(0, 25).map((job) => {
    const title = asText(job.title);
    const loc = asText(job.location);
    const url = asText(job.jobUrl);
    return {
      id: stableJobId("ashby", url, title),
      source: "ashby",
      title,
      company: slug,
      location: loc,
      description: stripHtml(asText(job.descriptionHtml || job.descriptionPlain)).slice(0, 4000),
      url,
      track: /contract|freelance/i.test(title) ? "freelance" : "jobs",
      stage: "discovered",
      remote: /remote/i.test(`${title} ${loc}`) ? "remote" : "",
    } as CareerOpportunity;
  });
}
