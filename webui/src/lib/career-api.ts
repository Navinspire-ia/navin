// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { apiBodyHeaders, apiRequest } from "@/lib/api";
import { officialSearchPack, type OfficialPortal } from "@/lib/career-portals";
import { externalHttpUrl } from "@/lib/external-url";
import { studioSessionQuery } from "@/lib/studio-request";
import type { DocumentGeneration } from "@/lib/document-generation";

export type { OfficialPortal };

/** Official board / offer URL that must leave the Tauri WebView. */
export function officialCareerHref(href: string): string | null {
  return externalHttpUrl(href);
}

export function isAbortError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const name = String((err as { name?: string }).name || "");
  const message = String((err as { message?: string }).message || "");
  return name === "AbortError" || /aborted|AbortError/i.test(message);
}

export type CareerTrack = "freelance" | "jobs" | "both";

export type CareerLoopKind = "daily" | "weekdays" | "weekend" | "weekly" | "monthly";

export interface CareerLoopSchedule {
  kind: CareerLoopKind;
  hour: number;
  minute: number;
  weekday?: number;
  day?: number | "last";
  tz?: string | null;
  expr?: string;
}

export interface CareerLoop {
  enabled?: boolean;
  phase?: string;
  next_due?: number;
  last_tick?: number;
  last_watch?: number;
  last_result?: string;
  cycle?: number;
  schedule?: CareerLoopSchedule | null;
  added?: number;
  scanned?: number;
  alerts?: number;
}

export interface CareerJournalRow {
  kind?: string;
  text?: string;
  at?: number;
  added?: number;
  alerts?: number;
}
export type CareerStage =
  | "discovered"
  | "matched"
  | "ready"
  | "applied"
  | "replied"
  | "interview"
  | "offer"
  | "won"
  | "rejected";

export interface CareerSource {
  id: string;
  name: string;
  level: number;
  zone?: string;
  ingest: string;
  auto_search?: boolean;
  auto_apply?: boolean;
  priority?: number;
  url: string;
  notes?: string;
}

export interface MatchReason {
  label: string;
  ok: boolean;
}

export interface CareerCv {
  name?: string;
  language?: string;
  headline?: string;
  contacts?: string[];
  target?: string;
  summary?: string;
  skills?: string;
  strengths?: string[];
  highlights?: string[];
  experiences?: { title?: string; company?: string; period?: string; bullets?: string[] }[];
  education?: { diploma?: string; school?: string; year?: string }[];
  languages?: string[];
  sections?: { kind?: string; heading: string; paragraphs: string[] }[];
}

export interface CareerOpportunity {
  id: string;
  source: string;
  title: string;
  company?: string;
  location?: string;
  country?: string;
  compensation?: number | null;
  currency?: string;
  stack?: string[];
  remote?: string;
  posted_at?: string;
  description?: string;
  url?: string;
  track?: CareerTrack | string;
  stage: CareerStage | string;
  match_score?: number | null;
  match_reasons?: MatchReason[];
  bucket?: "perfect" | "good" | "skip" | string;
  cv_name?: string;
  cv_text?: string;
  cover?: string;
  cv?: CareerCv;
  ats_notes?: string;
  pack_ready?: boolean;
  generation?: DocumentGeneration;
  next_action?: string;
  attribution?: string;
  languages?: string[];
  ingest?: string;
  favorite?: boolean;
  archived?: boolean;
  archived_at?: number | null;
  created_at?: number;
  applied_at?: number;
  employer_opened?: boolean;
  application_email?: string;
  application_email_source?: string;
  mail_receipt?: CareerMailReceipt;
  last_reply_at?: number;
  duration?: string;
  duration_months?: number;
  hybrid_days_min?: number;
  hybrid_days_max?: number;
  /** Shared vocabulary filled by every connector (see navin/career/normalize.py). */
  contracts?: CareerContract[] | string[];
  employment_type?: string;
  experience_level?: CareerExperienceLevel | string;
  experience_years_min?: number | null;
  /** Posted seniority text ("Senior", "3+ years") kept for legacy rows. */
  seniority?: string;
  start_date?: string;
  valid_through?: string;
  daily_rate_min?: number | null;
  daily_rate_max?: number | null;
  salary_min?: number | null;
  salary_max?: number | null;
}

export type CareerContract =
  | "contractor"
  | "permanent"
  | "fixed-term"
  | "part-time"
  | "temporary"
  | "internship"
  | "apprenticeship";

export type CareerExperienceLevel = "junior" | "mid" | "senior" | "expert";

export interface CareerApplication {
  id: string;
  opportunity_id: string;
  title?: string;
  company?: string;
  source?: string;
  cv_name?: string;
  cv_text?: string;
  cover?: string;
  cv?: CareerCv;
  summary?: string;
  ats_notes?: string;
  pack_ready?: boolean;
  generation?: DocumentGeneration;
  keywords_matched?: string[];
  stage?: string;
  apply_mode?: string;
  next_action?: string;
  employer_opened?: boolean;
  created_at?: number;
  updated_at?: number;
  applied_at?: number;
  mail_receipt?: CareerMailReceipt;
  last_reply_at?: number;
  exports?: Partial<Record<"docx" | "cv_docx" | "cover_docx", { file_id?: string; name?: string; mime?: string }>>;
}

export interface CareerInboxItem {
  id: string;
  opportunity_id?: string;
  sender?: string;
  subject?: string;
  body?: string;
  classification?: string;
  received_at?: number;
  application_id?: string;
  source?: "imap" | string;
  message_id?: string;
  in_reply_to?: string;
  automatic_reply?: boolean;
  delivery_report?: boolean;
}

export interface CareerMailbox {
  enabled: boolean;
  sender_name: string;
  sender_email: string;
  smtp_host: string;
  smtp_port: number;
  smtp_security: "ssl" | "starttls";
  smtp_username: string;
  imap_host: string;
  imap_port: number;
  imap_security: "ssl" | "starttls";
  imap_username: string;
  imap_folder: string;
  read_replies: boolean;
  auto_send: boolean;
  min_match_score: number;
  max_per_day: number;
  poll_interval_minutes: number;
  allowed_recipient_domains: string[];
}

export interface CareerMailAttachment {
  kind: "cv_docx" | "cover_docx";
  file_id: string;
  name: string;
  mime: string;
  size: number;
  sha256: string;
}

export interface CareerMailReceipt {
  opportunity_id: string;
  application_id: string;
  status: "sending" | "accepted" | "failed" | "unknown";
  message_id: string;
  sender: string;
  recipient: string;
  subject: string;
  revision: string;
  attachments: CareerMailAttachment[];
  attempted_at?: number;
  accepted_at?: number;
  smtp_code?: number;
  smtp_reply?: string;
  error?: string;
  delivery_status?: string;
  attempts?: number;
  retry_at?: number;
  automatic?: boolean;
  deduplicated?: boolean;
  retry_required?: boolean;
}

export interface CareerMailDraft {
  opportunity_id: string;
  application_id: string;
  sender: string;
  sender_name?: string;
  recipient: string;
  recipient_source?: string;
  subject: string;
  body: string;
  attachments: CareerMailAttachment[];
  requires_review: boolean;
  generation?: DocumentGeneration;
  revision: string;
}

export interface CareerMailCheck {
  status: "connected" | "failed";
  checked_at?: number;
  error?: string;
}

export interface CareerMailSync {
  status?: "complete" | "failed" | "disabled" | "waiting" | "busy";
  received?: number;
  scanned?: number;
  skipped_large?: number;
  checked_at?: number;
  next_due?: number;
  has_more?: boolean;
  reason?: string;
  error?: string;
}

export interface CareerMailboxStatus {
  enabled?: boolean;
  smtp_password_set?: boolean;
  imap_password_set?: boolean;
  checks?: { smtp?: CareerMailCheck; imap?: CareerMailCheck };
  sync?: CareerMailSync;
  accepted?: number;
  failed?: number;
  uncertain?: number;
  pending_notifications?: number;
  receipts?: CareerMailReceipt[];
}

export interface CareerProject {
  title?: string;
  result?: string;
}

export interface CareerExperience {
  title?: string;
  company?: string;
  period?: string;
  facts?: string;
}

export interface CareerEducation {
  school?: string;
  diploma?: string;
  year?: string;
}

export interface CareerTalent {
  id?: string;
  name?: string;
  headline?: string;
  titles?: string[];
  master_cv?: string;
  experiences?: CareerExperience[];
  education?: CareerEducation[];
  strengths?: string[];
  weaknesses?: string[];
  stack?: string[];
}

export interface CareerCompany {
  name?: string;
  email?: string;
  phone?: string;
  address?: string;
  city?: string;
  country?: string;
}

export interface CareerChannels {
  email?: boolean;
  teams?: boolean;
  whatsapp?: boolean;
  telegram?: boolean;
  email_to?: string;
  teams_to?: string;
  whatsapp_to?: string;
  telegram_to?: string;
}

export interface CareerProfile {
  mailbox?: CareerMailbox;
  track?: CareerTrack | string;
  account_kind?: "solo" | "company" | string;
  wizard_complete?: boolean;
  wizard_step?: number;
  display_name?: string;
  headline?: string;
  email?: string;
  phone?: string;
  residence_country?: string;
  titles?: string[];
  engagement?: string;
  countries_primary?: string[];
  countries_secondary?: string[];
  countries_excluded?: string[];
  country_weights?: Record<string, number>;
  work_mode?: string;
  hybrid_days_min?: number;
  hybrid_days_max?: number;
  min_rate?: number;
  max_rate?: number;
  min_salary?: number;
  max_salary?: number;
  currency?: string;
  available_from?: string;
  languages?: string[];
  visa?: string;
  stack?: string[];
  industries_priority?: string[];
  industries_excluded?: string[];
  master_cv?: string;
  cv_path?: "import" | "create" | string;
  experiences?: CareerExperience[];
  education?: CareerEducation[];
  strengths?: string[];
  weaknesses?: string[];
  highlights?: string[];
  projects?: CareerProject[];
  prospect_email?: string;
  prospect_email_approved?: boolean;
  apply_mode?: "manual" | "review" | "autopilot" | string;
  ai_assist?: boolean;
  mail?: { gmail?: boolean; outlook?: boolean };
  channels?: CareerChannels;
  company?: CareerCompany;
  talents?: CareerTalent[];
  active_talent_id?: string;
  source_ids?: string[];
  ats_boards?: string[];
  employer_watch?: boolean;
  employers?: CareerUserEmployer[];
  employers_hidden?: string[];
  archive_after_days?: number;
  delete_after_days?: number;
  api_keys?: {
    adzuna?: boolean;
    jooble?: boolean;
    usajobs?: boolean;
  };
}

export interface CareerSearchHint {
  added?: number;
  scanned?: number;
  web?: number;
  official?: number;
  scrape?: number;
  query?: string;
  queries?: { country: string; query: string; kind?: string; url?: string; family?: string; site?: string }[];
  portals?: { id: string; label: string; url: string; country?: string; kind?: string }[];
  families?: {
    remotive?: boolean;
    ats?: boolean;
    web?: boolean;
    scrape?: boolean;
    official?: boolean;
    linkedin?: boolean;
  };
  connectors?: CareerConnector[];
  note?: string;
}

export interface CareerConnector {
  id: string;
  name: string;
  live?: boolean;
  needs_key?: boolean;
  recommended?: boolean;
  env?: string;
  docs?: string;
  ingest?: string;
}

export interface CareerMcpHint {
  id: string;
  name: string;
  role: string;
  docs: string;
  recommended?: boolean;
  command?: string;
  official?: string[];
  jobs?: string[];
  confirm?: string[];
}

export interface CareerStack {
  tool: string;
  desk?: string;
  skills: string[];
  mcp: CareerMcpHint[];
  connectors: CareerConnector[];
  rules?: string;
}

export interface CareerEmployerRow {
  id: string;
  name: string;
  kind: string;
  markets: string[];
  user?: boolean;
  hidden?: boolean;
  ats?: string;
  careers_url?: string;
  last_count?: number;
  last_error?: string;
  checked_at?: number | null;
}

export interface CareerEmployers {
  enabled: boolean;
  markets: string[];
  summary: { directory: number; resolved: number; by_ats: Record<string, number>; user: number; hidden: number };
  rows: CareerEmployerRow[];
}

export interface CareerUserEmployer {
  name: string;
  url: string;
  kind?: string;
  markets?: string[];
}

export interface CareerDesk {
  mailbox_status?: CareerMailboxStatus;
  mail_draft?: CareerMailDraft;
  mail_receipt?: CareerMailReceipt;
  mail_checks?: { smtp?: CareerMailCheck; imap?: CareerMailCheck };
  mail_sync?: CareerMailSync;
  profile: CareerProfile;
  opportunities: CareerOpportunity[];
  applications: CareerApplication[];
  inbox: CareerInboxItem[];
  catalog: CareerSource[];
  stack?: CareerStack;
  employers?: CareerEmployers;
  kpis: {
    opportunities?: number;
    strong?: number;
    applications?: number;
    replies?: number;
    interviews?: number;
    offers?: number;
    avg_score?: number;
    response_rate?: number;
    headline?: string;
  };
  stages: string[];
  tagline?: string;
  files?: Record<string, string>;
  book?: string;
  search?: CareerSearchHint;
  prepared?: CareerApplication;
  download?: { file_id?: string; name?: string; mime?: string; data?: string; kind?: string };
  followup?: { opportunity_id?: string; wave?: string; body?: string };
  retention?: {
    archive_after_days?: number;
    delete_after_days?: number;
    favorites?: number;
    archived?: number;
  };
  deleted?: { id?: string; title?: string };
  loop?: CareerLoop;
  journal?: CareerJournalRow[];
  loop_tick?: {
    did_work?: boolean;
    phase?: string;
    reason?: string;
  };
  watch?: {
    events?: unknown[];
    count?: number;
    digest?: string;
    sent?: Record<string, boolean>;
    skipped?: string;
  };
}

export const FALLBACK_STACK: CareerStack = {
  tool: "career",
  desk: "#/career",
  skills: [
    "career-agent",
    "job-search-agent",
    "cv-builder",
    "cv-tailoring",
    "cover-letter-writer",
    "ats-analyzer",
    "application-tracker",
    "followup-writer",
    "interview-coach",
    "offer-analyzer",
    "salary-negotiator",
    "freelance-rate-card",
    "career-advisor",
    "linkedin-optimizer",
    "scrape-operator",
    "scrapling",
    "web-extractor",
  ],
  mcp: [
    {
      id: "linkedin",
      name: "LinkedIn",
      recommended: true,
      role: "Recommended option: your LinkedIn session for jobs, saved jobs, profile, people and inbox. Never scrape. Never Easy Apply.",
      docs: "https://github.com/stickerdaniel/linkedin-mcp-server",
      command: "uvx mcp-server-linkedin@latest",
      official: [
        "get_person_profile",
        "get_my_profile",
        "connect_with_person",
        "get_sidebar_profiles",
        "get_inbox",
        "get_conversation",
        "search_conversations",
        "send_message",
        "get_company_profile",
        "get_company_posts",
        "search_companies",
        "get_company_employees",
        "search_jobs",
        "get_saved_jobs",
        "search_people",
        "get_job_details",
        "get_feed",
        "search_posts",
        "close_session",
      ],
      jobs: ["search_jobs", "get_saved_jobs", "get_job_details"],
      confirm: ["connect_with_person", "send_message"],
    },
    {
      id: "notion",
      name: "Notion",
      role: "Application notes and interview pages",
      docs: "https://developers.notion.com/docs/mcp",
    },
    {
      id: "github",
      name: "GitHub",
      role: "Public repos and portfolio proof",
      docs: "https://github.com/github/github-mcp-server",
    },
    {
      id: "exa",
      name: "Exa",
      role: "Public web research, never closed job pages",
      docs: "https://exa.ai/mcp",
    },
  ],
  connectors: [
    { id: "remotive", name: "Remotive", live: true, docs: "https://remotive.com/api/remote-jobs" },
    { id: "ats", name: "Greenhouse / Lever / Ashby / Workable", live: true, docs: "https://developers.greenhouse.io/" },
    {
      id: "feeds",
      name: "Public job APIs and RSS",
      live: true,
      ingest: "public_api",
      docs: "https://github.com/Jobicy/remote-jobs-api/blob/main/README.md",
    },
    { id: "jsonld", name: "schema.org JobPosting", live: true, ingest: "jsonld", docs: "https://schema.org/JobPosting" },
    {
      id: "employers",
      name: "ESN / consulting / agency feeds",
      live: true,
      ingest: "employer_feed",
      docs: "https://api.smartrecruiters.com/v1/companies",
    },
    { id: "web-job-search", name: "Web Job Search", live: true, docs: "https://html.duckduckgo.com/html/" },
    { id: "web-search", name: "Web search snippets", live: true, docs: "https://html.duckduckgo.com/html/" },
    { id: "scrape", name: "scrape + scrapling", live: true, docs: "https://scrapling.readthedocs.io/en/latest/" },
    {
      id: "adzuna",
      name: "Adzuna",
      live: false,
      needs_key: true,
      env: "ADZUNA_APP_ID + ADZUNA_APP_KEY",
      docs: "https://developer.adzuna.com/",
    },
    {
      id: "jooble",
      name: "Jooble",
      live: false,
      needs_key: true,
      env: "JOOBLE_API_KEY",
      docs: "https://jooble.org/api/about",
    },
    {
      id: "usajobs",
      name: "USAJOBS",
      live: false,
      needs_key: true,
      env: "USAJOBS_API_KEY + USAJOBS_USER_AGENT",
      docs: "https://developer.usajobs.gov/",
    },
    {
      id: "jobopportunities",
      name: "Job Opportunities API",
      live: false,
      needs_key: true,
      env: "JOBOPPORTUNITIES_API_KEY",
      docs: "https://www.jobopportunitiesapi.org/docs",
    },
    {
      id: "linkedin",
      name: "LinkedIn",
      live: true,
      recommended: true,
      ingest: "public_listing",
      docs: "https://www.linkedin.com/jobs/search",
    },
    {
      id: "free-work",
      name: "Free-Work",
      live: true,
      ingest: "public_listing",
      docs: "https://www.free-work.com/fr/tech-it/jobs",
    },
    {
      id: "collective",
      name: "Collective.work",
      live: true,
      ingest: "public_listing",
      docs: "https://www.collective.work/jobs/fr",
    },
    {
      id: "malt",
      name: "Malt",
      live: false,
      ingest: "open_manual",
      docs: "https://www.malt.fr/",
    },
  ],
  rules:
    "LinkedIn public listings come from the guest job search (no login, rate limited). Never auto Easy Apply. Free-Work and Collective.work public listings come from the public search pages (no login, rate limited). Employer feeds read the ESN, consulting and agency boards of the selected markets directly. CV packs reuse Master CV facts only. Closed boards are official open + paste import.",
};

export const FALLBACK_CATALOG: CareerSource[] = [
  {
    id: "adzuna",
    name: "Adzuna",
    level: 1,
    zone: "FR EU US",
    ingest: "official_api",
    url: "https://developer.adzuna.com/",
      notes: "Official search API. Enable in setup, then store the keys.",
  },
  {
    id: "jooble",
    name: "Jooble",
    level: 1,
    zone: "World",
    ingest: "official_api",
    url: "https://jooble.org/api/about",
      notes: "Official REST API. Enable in setup, then store JOOBLE_API_KEY.",
  },
  {
    id: "france-travail",
    name: "France Travail",
    level: 1,
    zone: "FR",
    ingest: "partner_api",
    url: "https://francetravail.io/",
    notes: "Partner API. Public search URL always available.",
  },
  {
    id: "usajobs",
    name: "USAJOBS",
    level: 1,
    zone: "US",
    ingest: "official_api",
    url: "https://developer.usajobs.gov/",
      notes: "US federal jobs API. Enable in setup, then store the keys.",
  },
  {
    id: "remotive",
    name: "Remotive",
    level: 1,
    zone: "Remote",
    ingest: "official_api",
    url: "https://remotive.com/remote-jobs/api",
    notes: "Public remote API. Attribution required.",
  },
  {
    id: "jobicy",
    name: "Jobicy",
    level: 1,
    zone: "Remote",
    ingest: "public_api",
    url: "https://jobicy.com/api/v2/remote-jobs",
    notes: "Public API, no key: geo, industry and tag filters, salary with currency and period, employment type, level.",
  },
  {
    id: "remoteok",
    name: "Remote OK",
    level: 1,
    zone: "Remote",
    ingest: "public_api",
    url: "https://remoteok.com/api",
    notes: "Public JSON feed. Remote OK credited and the listing link kept. USD salaries.",
  },
  {
    id: "himalayas",
    name: "Himalayas",
    level: 1,
    zone: "Remote",
    ingest: "public_api",
    url: "https://himalayas.app/jobs/api",
    notes: "Public jobs API: salary with currency and period, seniority, employment type, location restrictions.",
  },
  {
    id: "weworkremotely",
    name: "We Work Remotely",
    level: 1,
    zone: "Remote",
    ingest: "public_rss",
    url: "https://weworkremotely.com/remote-jobs.rss",
    notes: "Public RSS: region, contract type, skills, listing link.",
  },
  {
    id: "arbeitnow",
    name: "Arbeitnow",
    level: 1,
    zone: "EU",
    ingest: "public_api",
    url: "https://www.arbeitnow.com/api/job-board-api",
    notes: "Public job board API (Europe, hourly refresh, no key).",
  },
  {
    id: "hn-hiring",
    name: "Hacker News Who is hiring",
    level: 1,
    zone: "World",
    ingest: "public_api",
    url: "https://hn.algolia.com/api/v1/search_by_date",
    notes: "Monthly Ask HN thread read through the public Algolia API.",
  },
  {
    id: "jobopportunities",
    name: "Job Opportunities API",
    level: 1,
    zone: "World",
    ingest: "official_api",
    url: "https://www.jobopportunitiesapi.org/docs",
    notes: "Employer-direct ledger with per-field provenance. Free key: JOBOPPORTUNITIES_API_KEY.",
  },
  {
    id: "greenhouse",
    name: "Greenhouse",
    level: 2,
    zone: "Tech",
    ingest: "ats_api",
    url: "https://developers.greenhouse.io/job-board.html",
    notes: "Company job board API.",
  },
  {
    id: "lever",
    name: "Lever",
    level: 2,
    zone: "Tech",
    ingest: "ats_api",
    url: "https://github.com/lever/postings-api",
    notes: "Published postings API.",
  },
  {
    id: "ashby",
    name: "Ashby",
    level: 2,
    zone: "Tech",
    ingest: "ats_api",
    url: "https://developers.ashbyhq.com/docs/job-posting-api",
    notes: "Official published jobs API.",
  },
  {
    id: "employers",
    name: "ESN, consulting and agency feeds",
    level: 2,
    zone: "Markets",
    ingest: "employer_feed",
    url: "https://api.smartrecruiters.com/v1/companies",
    notes: "Directory per market plus your own employers. Feeds read directly from their ATS or careers page.",
  },
  {
    id: "eures",
    name: "EURES",
    level: 1,
    zone: "EU",
    ingest: "partner_only",
    url: "https://eures.europa.eu/",
    notes: "Partner only. No scrape.",
  },
  {
    id: "linkedin",
    name: "LinkedIn Jobs",
    level: 4,
    zone: "World",
    ingest: "public_listing",
    url: "https://www.linkedin.com/jobs/",
    notes: "Source #1. Public guest listings, rate limited, no login. Never Easy Apply.",
  },
  {
    id: "web-job-search",
    name: "Web Job Search",
    level: 3,
    zone: "World",
    ingest: "search_snippet",
    url: "https://html.duckduckgo.com/html/",
    notes: "Generic connector. One query pack per preferred country.",
  },
  {
    id: "web-search",
    name: "Web Job Search",
    level: 3,
    zone: "World",
    ingest: "search_snippet",
    url: "https://html.duckduckgo.com/html/",
    notes: "Preferential queries per market. Titles and snippets only.",
  },
  {
    id: "free-work",
    name: "Free-Work",
    level: 3,
    zone: "FR",
    ingest: "public_listing",
    url: "https://www.free-work.com/",
    notes: "IT freelance missions and jobs, FR and UK. Public search pages read live: TJM, duration, remote mode, skills.",
  },
  {
    id: "collective",
    name: "Collective.work",
    level: 3,
    zone: "FR EU",
    ingest: "public_listing",
    url: "https://www.collective.work/",
    notes: "IT freelance missions and jobs, FR and neighbouring markets. Public search pages read live: TJM, remote mode, skills.",
  },
  {
    id: "malt",
    name: "Malt",
    level: 4,
    zone: "FR EU",
    ingest: "open_manual",
    url: "https://www.malt.fr/",
    notes: "Open official. Paste missions. No scrape.",
  },
  {
    id: "jobserve",
    name: "JobServe",
    level: 3,
    zone: "GB",
    ingest: "web_agent",
    url: "https://www.jobserve.com/",
    notes: "UK IT contracts. Web discovery.",
  },
  {
    id: "dice",
    name: "Dice",
    level: 3,
    zone: "US",
    ingest: "web_agent",
    url: "https://www.dice.com/",
    notes: "US IT. Web discovery.",
  },
  {
    id: "apec",
    name: "APEC",
    level: 3,
    zone: "FR",
    ingest: "web_agent",
    url: "https://www.apec.fr/",
    notes: "Cadres / IT. Web discovery.",
  },
  {
    id: "wttj",
    name: "Welcome to the Jungle",
    level: 3,
    zone: "FR",
    ingest: "web_agent",
    url: "https://www.welcometothejungle.com/",
    notes: "Tech / startups. Web discovery.",
  },
  {
    id: "ictjob",
    name: "ICTjob",
    level: 3,
    zone: "BE",
    ingest: "web_agent",
    url: "https://www.ictjob.be/",
    notes: "Belgium IT.",
  },
  {
    id: "jobs-ch",
    name: "jobs.ch",
    level: 3,
    zone: "CH",
    ingest: "web_agent",
    url: "https://www.jobs.ch/",
    notes: "Switzerland main board.",
  },
  {
    id: "cwjobs",
    name: "CWJobs",
    level: 3,
    zone: "GB",
    ingest: "web_agent",
    url: "https://www.cwjobs.co.uk/",
    notes: "UK IT / contract.",
  },
  {
    id: "wellfound",
    name: "Wellfound",
    level: 3,
    zone: "US",
    ingest: "web_agent",
    url: "https://wellfound.com/",
    notes: "Startups / tech.",
  },
  {
    id: "job-bank-ca",
    name: "Job Bank Canada",
    level: 3,
    zone: "CA",
    ingest: "web_agent",
    url: "https://www.jobbank.gc.ca/",
    notes: "Official Canada board.",
  },
  {
    id: "indeed",
    name: "Indeed",
    level: 4,
    zone: "World",
    ingest: "open_manual",
    url: "https://www.indeed.com/",
    notes: "Aggregator. Never scrape.",
  },
  {
    id: "chooseyourboss",
    name: "ChooseYourBoss",
    level: 3,
    zone: "FR",
    ingest: "web_agent",
    url: "https://www.chooseyourboss.com/",
    notes: "France IT.",
  },
  {
    id: "vdab",
    name: "VDAB",
    level: 3,
    zone: "BE",
    ingest: "web_agent",
    url: "https://www.vdab.be/",
    notes: "Flanders official board.",
  },
  {
    id: "le-forem",
    name: "Le Forem",
    level: 3,
    zone: "BE",
    ingest: "web_agent",
    url: "https://www.leforem.be/",
    notes: "Wallonia official board.",
  },
  {
    id: "actiris",
    name: "Actiris",
    level: 3,
    zone: "BE",
    ingest: "web_agent",
    url: "https://www.actiris.brussels/",
    notes: "Brussels official board.",
  },
  {
    id: "jobat",
    name: "Jobat",
    level: 3,
    zone: "BE",
    ingest: "web_agent",
    url: "https://www.jobat.be/",
    notes: "Belgium generalist.",
  },
  {
    id: "stepstone-be",
    name: "StepStone Belgium",
    level: 3,
    zone: "BE",
    ingest: "web_agent",
    url: "https://www.stepstone.be/",
    notes: "Belgium generalist.",
  },
  {
    id: "jobscout24",
    name: "JobScout24",
    level: 3,
    zone: "CH",
    ingest: "web_agent",
    url: "https://www.jobscout24.ch/",
    notes: "Switzerland board.",
  },
  {
    id: "swissdevjobs",
    name: "SwissDevJobs",
    level: 3,
    zone: "CH",
    ingest: "web_agent",
    url: "https://swissdevjobs.ch/",
    notes: "Switzerland tech.",
  },
  {
    id: "reed",
    name: "Reed",
    level: 3,
    zone: "GB",
    ingest: "web_agent",
    url: "https://www.reed.co.uk/",
    notes: "UK contract + permanent.",
  },
  {
    id: "totaljobs",
    name: "Totaljobs",
    level: 3,
    zone: "GB",
    ingest: "web_agent",
    url: "https://www.totaljobs.com/",
    notes: "UK contract + jobs.",
  },
  {
    id: "technojobs",
    name: "Technojobs",
    level: 3,
    zone: "GB",
    ingest: "web_agent",
    url: "https://www.technojobs.co.uk/",
    notes: "UK IT contracts.",
  },
  {
    id: "govuk-find-a-job",
    name: "GOV.UK Find a Job",
    level: 3,
    zone: "GB",
    ingest: "web_agent",
    url: "https://www.gov.uk/find-a-job",
    notes: "UK official board.",
  },
  {
    id: "builtin",
    name: "Built In",
    level: 3,
    zone: "US",
    ingest: "web_agent",
    url: "https://builtin.com/",
    notes: "US tech.",
  },
  {
    id: "ziprecruiter",
    name: "ZipRecruiter",
    level: 4,
    zone: "US",
    ingest: "open_manual",
    url: "https://www.ziprecruiter.com/",
    notes: "US aggregator. Never scrape.",
  },
  {
    id: "jobillico",
    name: "Jobillico",
    level: 3,
    zone: "CA",
    ingest: "web_agent",
    url: "https://www.jobillico.com/",
    notes: "Canada.",
  },
  {
    id: "jobboom",
    name: "Jobboom",
    level: 3,
    zone: "CA",
    ingest: "web_agent",
    url: "https://www.jobboom.com/",
    notes: "Canada.",
  },
  {
    id: "eluta",
    name: "Eluta",
    level: 3,
    zone: "CA",
    ingest: "web_agent",
    url: "https://www.eluta.ca/",
    notes: "Canada career pages.",
  },
  {
    id: "arbeitsagentur",
    name: "Bundesagentur fur Arbeit",
    level: 3,
    zone: "DE",
    ingest: "web_agent",
    url: "https://www.arbeitsagentur.de/",
    notes: "Germany official board.",
  },
  {
    id: "stepstone-de",
    name: "StepStone Germany",
    level: 3,
    zone: "DE",
    ingest: "web_agent",
    url: "https://www.stepstone.de/",
    notes: "Germany board.",
  },
  {
    id: "werk-nl",
    name: "Werk.nl",
    level: 3,
    zone: "NL",
    ingest: "web_agent",
    url: "https://www.werk.nl/",
    notes: "Netherlands official board.",
  },
  {
    id: "bayt",
    name: "Bayt",
    level: 4,
    zone: "Gulf",
    ingest: "open_manual",
    url: "https://www.bayt.com/",
    notes: "Gulf board. No scrape.",
  },
  {
    id: "gulftalent",
    name: "GulfTalent",
    level: 4,
    zone: "Gulf",
    ingest: "open_manual",
    url: "https://www.gulftalent.com/",
    notes: "Gulf board. No scrape.",
  },
  {
    id: "jadarat",
    name: "Jadarat",
    level: 4,
    zone: "SA",
    ingest: "open_manual",
    url: "https://jadarat.sa/",
    notes: "Saudi official. No scrape.",
  },
];

export function authorizedPortals(
  query: string,
  countries: string[] = ["FR", "AE", "SA", "US"],
  track: string = "freelance",
  workMode: string = "remote",
): OfficialPortal[] {
  return officialSearchPack(query, countries, track, workMode);
}

export function emptyCareerDesk(): CareerDesk {
  return {
    profile: {
      track: "freelance",
      account_kind: "solo",
      wizard_complete: false,
      wizard_step: 1,
      titles: [],
      countries_primary: [],
      countries_secondary: [],
      work_mode: "remote",
      min_rate: 0,
      languages: ["fr", "en"],
      stack: [],
      apply_mode: "manual",
      strengths: [],
      weaknesses: [],
      channels: {},
      mail: { gmail: false, outlook: false },
      ats_boards: ["databricks", "stripe", "spotify", "notion"],
    },
    opportunities: [],
    applications: [],
    inbox: [],
    catalog: FALLBACK_CATALOG,
    stack: FALLBACK_STACK,
    kpis: {},
    retention: {
      archive_after_days: 45,
      delete_after_days: 60,
      favorites: 0,
      archived: 0,
    },
    loop: {
      enabled: false,
      phase: "idle",
      next_due: 0,
      cycle: 0,
      added: 0,
      alerts: 0,
      schedule: { kind: "daily", hour: 9, minute: 0, weekday: 1, day: 1 },
    },
    journal: [],
    stages: [
      "discovered",
      "matched",
      "ready",
      "applied",
      "replied",
      "interview",
      "offer",
      "won",
      "rejected",
    ],
  };
}

function careerUrl(action: string): string {
  return `/api/career?action=${encodeURIComponent(action)}${studioSessionQuery()}`;
}

export async function fetchCareerDesk(token: string): Promise<CareerDesk> {
  return apiRequest<CareerDesk>(careerUrl("snapshot"), token, {
    headers: apiBodyHeaders("{}"),
  });
}

export function triggerCareerDownload(pack: { name?: string; mime?: string; data?: string }): void {
  if (!pack.data || !pack.name) return;
  const binary = atob(pack.data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: pack.mime || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = pack.name;
  link.click();
  URL.revokeObjectURL(url);
}

export async function postCareer(
  token: string,
  action: string,
  body: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<CareerDesk> {
  return apiRequest<CareerDesk>(careerUrl(action), token, {
    headers: apiBodyHeaders(JSON.stringify(body)),
    ...(signal ? { signal } : {}),
  });
}

export async function fetchRemotiveOpportunities(
  query: string,
  profile: CareerProfile,
  track: CareerTrack | string,
  signal?: AbortSignal,
): Promise<CareerOpportunity[]> {
  const { jobIsRelevant, normalizeRemotive } = await import("@/lib/career-match");
  const q = encodeURIComponent((query || "engineer").trim() || "engineer");
  const response = await fetch(`/remotive-api/remote-jobs?search=${q}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error(`Remotive ${response.status}`);
  const payload = (await response.json()) as { jobs?: Record<string, unknown>[] };
  const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
  return jobs
    .slice(0, 40)
    .map((item) => normalizeRemotive(item, track, profile))
    .filter((row) => row.title && row.url && jobIsRelevant(row, profile));
}
