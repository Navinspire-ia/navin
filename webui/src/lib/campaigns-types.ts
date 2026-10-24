/**
 * Campaigns Studio: email + WhatsApp campaigns, lists, templates, replies.
 *
 * Contract with the gateway: GET /api/campaigns?action=<name>[&query...].
 * Mutating actions carry a JSON body as chunked base64 headers (apiBodyHeaders).
 * Errors come back as HTTP 4xx with a readable message.
 */

export type CampaignChannel = "email" | "whatsapp";
export type CampaignStatus = "draft" | "scheduled" | "sending" | "paused" | "sent" | "failed" | "cancelled";
export type ContactStatus = "subscribed" | "unsubscribed" | "bounced";
export type SendStatus = "queued" | "sent" | "failed" | "skipped";
export type ReplyClass =
  | "interested"
  | "meeting"
  | "question"
  | "not_interested"
  | "unsubscribe"
  | "out_of_office"
  | "bounce"
  | "other";
export type ReplyStatus = "new" | "drafted" | "answered" | "handed_off" | "ignored";

export interface Contact {
  id: string;
  email: string;
  phone: string;
  first_name: string;
  last_name: string;
  company: string;
  title: string;
  city: string;
  country: string;
  tags: string[];
  /** Free extra columns from an import, usable as {{attrs.key}} merge tags. */
  attrs: Record<string, string>;
  status: ContactStatus;
  source: string;
  list_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface ContactList {
  id: string;
  name: string;
  description: string;
  count: number;
  created_at: string;
}

export interface Attachment {
  id: string;
  name: string;
  size: number;
  mime: string;
}

export interface Template {
  id: string;
  name: string;
  channel: CampaignChannel;
  subject: string;
  preheader: string;
  /** Email body as HTML (TipTap output). */
  html: string;
  /** WhatsApp: approved template name, language and ordered body parameters. */
  wa_template: string;
  wa_language: string;
  wa_params: string[];
  /** WhatsApp free text (only inside the 24h customer window). */
  text: string;
  created_at: string;
  updated_at: string;
}

/** One follow-up of an email sequence, sent in the same thread. */
export interface SequenceStep {
  id: string;
  /** Days after the previous message (first email for step 1). */
  delay_days: number;
  /** no_reply: only if they did not answer; no_open: only if they did not open; always. */
  condition: "no_reply" | "no_open" | "always";
  /** Empty = "Re: <first subject>" (stays in the thread). */
  subject: string;
  html: string;
}

export interface StepStats {
  step: number;
  sent: number;
  failed: number;
  skipped: number;
  opened: number;
}

export interface VariantStats {
  variant: "A" | "B" | "C";
  subject: string;
  sent: number;
  opened: number;
  clicked: number;
  replied: number;
  open_rate: number;
  reply_rate: number;
}

export interface CampaignStats {
  recipients: number;
  queued: number;
  sent: number;
  failed: number;
  skipped: number;
  opened: number;
  clicked: number;
  replied: number;
  bounced: number;
  unsubscribed: number;
  open_rate: number;
  click_rate: number;
  reply_rate: number;
  /** Per follow-up step (sequence campaigns). */
  steps?: StepStats[];
  /** Per subject variant (A/B tests). */
  variants?: VariantStats[];
}

export interface Campaign {
  id: string;
  name: string;
  channel: CampaignChannel;
  status: CampaignStatus;
  subject: string;
  preheader: string;
  from_name: string;
  reply_to: string;
  html: string;
  text: string;
  wa_template: string;
  wa_language: string;
  wa_params: string[];
  template_id: string;
  list_ids: string[];
  /** Extra contacts targeted one by one, besides the lists. */
  contact_ids: string[];
  attachments: Attachment[];
  /** ISO datetime, empty = not scheduled. */
  schedule_at: string;
  throttle_per_minute: number;
  track_opens: boolean;
  track_clicks: boolean;
  /** Email sequence: follow-ups after the first email (max 6). Stopped by a reply, bounce or unsubscribe. */
  steps: SequenceStep[];
  /** A/B test: extra subjects (B, C) beside `subject` (A); contacts split evenly and stably. */
  subject_variants: string[];
  created_at: string;
  updated_at: string;
  launched_at: string;
  finished_at: string;
  last_error: string;
  stats: CampaignStats;
}

export interface Send {
  id: string;
  campaign_id: string;
  contact_id: string;
  contact_name: string;
  address: string;
  status: SendStatus;
  error: string;
  sent_at: string;
  opened_at: string;
  open_count: number;
  clicked_at: string;
  click_count: number;
  replied_at: string;
  bounced: boolean;
}

export interface Reply {
  id: string;
  /** Email reply (IMAP) or WhatsApp message (webhook). */
  channel: CampaignChannel;
  campaign_id: string;
  campaign_name: string;
  contact_id: string;
  contact_name: string;
  from_addr: string;
  subject: string;
  body: string;
  received_at: string;
  classification: ReplyClass;
  /** 0..1, heuristic or agent. */
  confidence: number;
  summary: string;
  status: ReplyStatus;
  /** Teammate (Collaboration Mode) or "human" who owns the follow-up. */
  assigned_to: string;
  /** What the owner must know and do to take the lead. */
  handoff_brief: string;
  draft_reply: string;
  answered_at: string;
}

export interface CampaignSettings {
  from_name: string;
  /** Read-only: the SMTP sender of Settings > Channels > Email. */
  from_email: string;
  reply_to: string;
  throttle_per_minute: number;
  daily_cap: number;
  company_address: string;
  footer_html: string;
  unsubscribe_text: string;
  tracking_enabled: boolean;
  /** Public URL that reaches this gateway (opens, clicks, unsubscribe). */
  tracking_base_url: string;
  /** off: replies only classified; draft: agent drafts answers; send: agent answers on its own. */
  auto_reply_mode: "off" | "draft" | "send";
  loop_enabled: boolean;
  /** Business-hours sending: ISO weekdays (1 = Monday), HH:MM range, IANA time zone. */
  send_window: { enabled: boolean; days: number[]; start: string; end: string; timezone: string };
  /** Skip addresses whose domain cannot receive email (checked before sending). */
  verify_domains: boolean;
  whatsapp: {
    phone_number_id: string;
    waba_id: string;
    /** Never returned; send a new value to replace it, "" keeps the current one. */
    token: string;
    token_hint: string;
    configured: boolean;
    /** Write-only like token: "" keeps the current one. */
    app_secret: string;
    app_secret_hint: string;
    /** Readable; generated when the webhook is enabled. */
    verify_token: string;
    webhook_enabled: boolean;
    webhook_port: number;
    /** Read-only: "/whatsapp/webhook". */
    webhook_path: string;
  };
}

export interface CampaignSetup {
  email_ready: boolean;
  email_hint: string;
  imap_ready: boolean;
  whatsapp_ready: boolean;
  tracking_ready: boolean;
}

export interface CampaignDashboard {
  totals: {
    contacts: number;
    subscribed: number;
    lists: number;
    campaigns: number;
    sent_30d: number;
    open_rate: number;
    click_rate: number;
    reply_rate: number;
    bounce_rate: number;
    unsubscribed: number;
    replies_new: number;
  };
  /** Last 30 days, oldest first, one row per day (YYYY-MM-DD). */
  timeline: Array<{ date: string; sent: number; opened: number; clicked: number; replied: number }>;
  replies_by_class: Partial<Record<ReplyClass, number>>;
  recent_campaigns: Campaign[];
  setup: CampaignSetup;
  loop: { last_tick: string; last_imap_poll: string; sending: number; scheduled: number; wa_webhook_error?: string };
}

export interface ImportPreview {
  import_id: string;
  filename: string;
  columns: string[];
  /** First rows, as strings, aligned with columns. */
  sample: string[][];
  rows: number;
  /** column name -> contact field (email, phone, first_name, last_name, company, title, city, country, tags, attrs.<key>, ignore). */
  mapping: Record<string, string>;
}

export interface ImportResult {
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
  list_id: string;
}

export interface Paged<T> {
  items: T[];
  total: number;
}

export interface CampaignPreview {
  subject: string;
  preheader: string;
  html: string;
  text: string;
  /** Merge tags without a value, missing unsubscribe link, empty subject... */
  warnings: string[];
}

export interface WhatsAppTemplate {
  name: string;
  language: string;
  status: string;
  category: string;
  body: string;
  /** Number of {{n}} body parameters. */
  params: number;
}

/**
 * Actions (query `action=`), body = JSON headers unless noted:
 *  dashboard                                  -> CampaignDashboard
 *  contacts.list   q, list_id, status, offset, limit (query) -> Paged<Contact>
 *  contacts.save   Partial<Contact>           -> Contact
 *  contacts.delete {ids}                      -> {deleted}
 *  contacts.export list_id (query)            -> {csv, filename}
 *  import.preview  {filename, data_b64}       -> ImportPreview   (csv, xlsx)
 *  import.commit   {import_id, mapping, list_id?, new_list_name?, tags?} -> ImportResult
 *  import.leads    {list_id?, new_list_name?} -> ImportResult    (from the Leads desk)
 *  lists.list                                 -> ContactList[]
 *  lists.save      Partial<ContactList>       -> ContactList
 *  lists.delete    {id}                       -> {deleted}
 *  lists.add       {list_id, contact_ids}     -> ContactList
 *  lists.remove    {list_id, contact_ids}     -> ContactList
 *  templates.list                             -> Template[]
 *  templates.save  Partial<Template>          -> Template
 *  templates.delete {id}                      -> {deleted}
 *  campaigns.list  status (query, optional)   -> Campaign[]
 *  campaigns.get   id (query)                 -> Campaign
 *  campaigns.save  Partial<Campaign>          -> Campaign   (draft/paused only)
 *  campaigns.delete {id}                      -> {deleted}
 *  campaigns.duplicate {id}                   -> Campaign
 *  campaigns.preview {campaign: Partial<Campaign>, contact_id?} -> CampaignPreview
 *  campaigns.test  {campaign: Partial<Campaign>, to: string[]} -> {sent: string[]}
 *  campaigns.schedule {id, schedule_at}       -> Campaign   ("" = send now)
 *  campaigns.pause {id} / campaigns.resume {id} / campaigns.cancel {id} -> Campaign
 *  campaigns.sends id, status, offset, limit (query) -> Paged<Send>
 *  attachments.upload {filename, data_b64}    -> Attachment  (max 10 MB)
 *  replies.list    status, classification, campaign_id, offset, limit (query) -> Paged<Reply>
 *  replies.update  {id, classification?, status?, assigned_to?, handoff_brief?, draft_reply?} -> Reply
 *  replies.send    {id, subject?, body}       -> Reply      (answers by email, status answered)
 *  settings.get                               -> CampaignSettings
 *  settings.save   Partial<CampaignSettings>  -> CampaignSettings
 *  whatsapp.test   {to}                       -> {ok, detail}
 *  whatsapp.templates                         -> WhatsAppTemplate[]
 *  loop.run                                   -> CampaignDashboard["loop"]  (tick now: send due, poll replies)
 */
/** Campaign audiences from the project's Navin CRM (per project: needs session_key). */
export type CrmKind = "contacts" | "leads" | "opportunities" | "companies";

export interface CrmFilters {
  q?: string;
  status?: string[];
  stage?: string[];
  owner?: string[];
  tags?: string[];
  source?: string[];
  country?: string[];
  score_min?: number;
  amount_min?: number;
  industry?: string[];
}

export interface CrmPreview {
  count: number;
  with_email: number;
  with_phone: number;
  sample: Array<{ name: string; email: string; phone: string; kind: CrmKind; company: string }>;
  facets: {
    status: string[];
    stage: string[];
    owner: string[];
    tags: string[];
    source: string[];
    country: string[];
    industry: string[];
  };
  project: string;
}

/*
 *  crm.preview  session_key (query) {kinds, filters}                        -> CrmPreview
 *  crm.import   session_key (query) {kinds, filters, list_id?, new_list_name?, tags?} -> ImportResult
 */
export const CAMPAIGN_MERGE_TAGS = [
  "first_name",
  "last_name",
  "company",
  "title",
  "email",
  "city",
  "country",
] as const;

/** Prospecting with the Leads desk engine and keys (Leads > Keys). */
export interface ProspectProviders {
  free: string[];
  keys: Record<"apollo" | "hunter" | "pdl" | "places" | "crunchbase" | "opencorporates" | "companies_house" | "pappers", boolean>;
  hint: string;
  can_find_people: boolean;
  can_find_email: boolean;
  can_verify: boolean;
  can_enrich_company: boolean;
  /** Free: each company's general contact (contact@, switchboard) from its public site. */
  can_find_contact?: boolean;
}

export interface ProspectCriteria {
  sector?: string;
  keywords?: string;
  /** ISO country codes, e.g. ["FR", "MA"]. */
  countries?: string[];
  size_min?: number;
  size_max?: number;
  /** Decision makers to find at each company, e.g. ["CEO", "Head of Sales"]. */
  titles?: string[];
  /** Search people at these exact company domains instead of a sector search. */
  domains?: string[];
  cities?: string[];
  /** Free sources to use: sirene, web, hiring, osm. */
  sources?: string[];
  limit?: number;
  /** Look up a decision maker per company (uses Apollo/Hunter credits). */
  find_people?: boolean;
  /** Read the company's public site for its general contact when no direct email (free, default on). */
  find_contact?: boolean;
}

export interface ProspectRow {
  company: string;
  domain: string;
  website: string;
  country: string;
  sector: string;
  size: string;
  first_name: string;
  last_name: string;
  role: string;
  email: string;
  /** verified | unverified | "" */
  email_status: string;
  phone: string;
  linkedin_url: string;
  source: string;
  signal: string;
  /** person: direct email of a named decision maker; general: company contact@ / switchboard. */
  contact_kind?: "person" | "general" | "";
}

export interface ProspectSearch {
  search_id: string;
  rows: ProspectRow[];
  count: number;
  with_email: number;
  with_person: number;
  /** Rows reached only through the company's general contact. */
  with_general?: number;
  providers: ProspectProviders;
}

export interface EnrichResult {
  processed: number;
  emails_found: number;
  verified: number;
  invalid: number;
  companies: number;
  skipped: number;
  errors: string[];
}

/**
 * Prospecting actions:
 *  prospects.providers                                   -> ProspectProviders
 *  prospects.search  {criteria: ProspectCriteria}        -> ProspectSearch
 *  prospects.import  {search_id, indexes?, only_with_email?, list_id?, new_list_name?, tags?} -> ImportResult
 *  contacts.enrich   {ids, find_email?, verify?, company?} -> EnrichResult (Hunter / PDL credits)
 * Contacts get attrs.email_status = verified | invalid | risky; "invalid" ones are never sent to.
 */
