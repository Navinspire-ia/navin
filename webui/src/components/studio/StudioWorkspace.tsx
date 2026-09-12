// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BadgeDollarSign,
  Check,
  Eye,
  FileSpreadsheet,
  FileText,
  Globe2,
  Info,
  LayoutTemplate,
  Clapperboard,
  Megaphone,
  Presentation,
  Palette,
  Play,
  ServerCog,
  Mic,
  ShieldAlert,
  Target,
  TrendingUp,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Icon as FluentIcon } from "@fluentui/react";
import "@/lib/fluent-icons";

import { cn } from "@/lib/utils";
import { fetchDocumentTemplates, type MediaTemplateItem } from "@/lib/api";
import type {
  DocumentTemplateInfo,
  ProjectFileMatch,
  RecentProjectEntry,
} from "@/lib/types";
import {
  MediaTemplateLibrary,
  mediaTemplateSeedText,
} from "@/components/studio/MediaTemplateLibrary";
import { useClient } from "@/providers/ClientProvider";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { DocumentTemplatePreviewDialog } from "@/components/thread/DocumentTemplatePreview";
import { NOTIFICATION_GUTTER } from "@/components/NotificationCenter";
import { opsGroups, opsSeedText } from "@/components/studio/opsCatalog";
import { DevProjectSelector } from "@/components/dev/DevProjectSelector";
import { MarketingQA } from "@/components/studio/MarketingQA";

const ONBOARDING_DISMISSED_KEY = "navin.studio.onboarding.dismissed";

function readDismissedOnboarding(): Set<string> {
  try {
    const raw = window.localStorage.getItem(ONBOARDING_DISMISSED_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? new Set(parsed.filter((value): value is string => typeof value === "string"))
      : new Set();
  } catch {
    return new Set();
  }
}

function writeDismissedOnboarding(modules: Iterable<string>) {
  try {
    window.localStorage.setItem(
      ONBOARDING_DISMISSED_KEY,
      JSON.stringify([...modules]),
    );
  } catch {
    // Storage unavailable: tip may reappear next visit.
  }
}

export type StudioModule =
  | "risklens"
  | "scraping"
  | "content"
  | "marketing"
  | "montage"
  | "ads"
  | "seo"
  | "leads"
  | "meeting"
  | "ops";

type DocFormat = "ppt" | "doc" | "pdf" | "xls";

type StudioCard = {
  id: string;
  format?: DocFormat;
  label: string;
  description: string;
  prompt: string;
  legal?: boolean;
  /** Contract HTML template slug (legal cards). */
  templateName?: string;
  /** Preferred PPT/DOC/PDF design-template name preselected in the dialog. */
  recommendedTemplate?: string;
};

type StudioGroup = {
  id: string;
  label: string;
  cards: StudioCard[];
};

const FORMAT_META: Record<DocFormat, { label: string; className: string }> = {
  ppt: { label: "PPTX", className: "bg-muted text-muted-foreground" },
  doc: { label: "DOCX", className: "bg-muted text-muted-foreground" },
  pdf: { label: "PDF", className: "bg-muted text-muted-foreground" },
  xls: { label: "XLSX", className: "bg-muted text-muted-foreground" },
};

/** Maps a card's output format to its HTML design-template category. */
const FORMAT_TO_CATEGORY: Record<DocFormat, string> = {
  ppt: "ppt",
  doc: "word",
  pdf: "pdf",
  xls: "excel",
};

/** Built-in visual themes - specs live in the `document-templates` skill. Dots preview the doc palette. */
const DOC_THEMES: { id: string; dots: [string, string, string] }[] = [
  { id: "executive", dots: ["#1F3A5F", "#C9A227", "#1A2333"] },
  { id: "minimal", dots: ["#111111", "#9CA3AF", "#FFFFFF"] },
  { id: "tech", dots: ["#0F172A", "#3B82F6", "#22D3EE"] },
  { id: "bold", dots: ["#000000", "#FFD400", "#FFFFFF"] },
  { id: "warm", dots: ["#FAF3E0", "#C1553D", "#8A6F5C"] },
  { id: "nature", dots: ["#1E4D2B", "#9CAF88", "#F7F7F2"] },
  { id: "elegant", dots: ["#2B2B2B", "#D9A5A5", "#FCFAF9"] },
  { id: "corporate", dots: ["#0B5394", "#6B7280", "#FFFFFF"] },
];

type Tx = (key: string, fallback: string) => string;

const LEGAL_CONTRACTS = [
  ["legalMutualNda", "nda_mutuel", "Mutual NDA", "Protect confidential information shared by both parties."],
  ["legalLetterOfIntent", "lettre_intention", "Letter of intent", "Set out the main terms before the definitive agreement."],
  ["legalMemorandumOfUnderstanding", "protocole_accord", "Memorandum of understanding", "Formalize shared objectives and each party's commitments."],
  ["legalCommercialPartnership", "partenariat_commercial", "Commercial partnership", "Organize a business partnership and its operating terms."],
  ["legalBusinessIntroducer", "apporteur_affaires", "Business introducer agreement", "Define introductions, commissions and payment conditions."],
  ["legalCommercialAgent", "agent_commercial", "Commercial agency agreement", "Define mandate, territory, commission and reporting."],
  ["legalExclusiveDistribution", "distribution_exclusive", "Exclusive distribution agreement", "Set exclusivity, territory, targets and supply terms."],
  ["legalReseller", "contrat_revendeur", "Reseller agreement", "Frame resale rights, pricing and customer responsibilities."],
  ["legalWhiteLabel", "marque_blanche", "White-label agreement", "Define branding, delivery, support and intellectual property."],
  ["legalIntegratorPartnership", "partenariat_integrateur", "Integrator partnership", "Organize implementation, referrals and customer support."],
  ["legalCoDevelopment", "co_developpement", "Co-development agreement", "Allocate work, funding, governance and resulting IP."],
  ["legalJointVenture", "joint_venture", "Joint-venture agreement", "Define contributions, governance and profit sharing."],
  ["legalMasterServices", "contrat_cadre_services", "Master services agreement", "Set the general terms governing future services."],
  ["legalStatementOfWork", "statement_of_work", "Statement of Work", "Specify deliverables, milestones, acceptance and fees."],
  ["legalSaas", "contrat_saas", "SaaS agreement", "Define subscriptions, service access, support and liability."],
  ["legalSoftwareLicense", "licence_logicielle", "Software license agreement", "Set license scope, restrictions, maintenance and fees."],
  ["legalSla", "sla", "Service level agreement", "Define availability, support targets and service credits."],
  ["legalDpa", "dpa_rgpd", "DPA / GDPR agreement", "Frame personal-data processing and GDPR obligations."],
  ["legalIpAssignment", "developpement_cession_pi", "Development and IP assignment", "Define development scope and transfer of intellectual property."],
  ["legalPilot", "poc_pilote", "POC / pilot agreement", "Set pilot scope, success criteria, duration and exit."],
  ["legalSubcontracting", "sous_traitance", "Subcontracting agreement", "Define delegated services, controls and responsibilities."],
  ["legalFreelance", "contrat_freelance", "Freelance agreement", "Frame independent services, fees, IP and confidentiality."],
  ["legalEmployment", "contrat_travail", "Employment agreement", "Define role, compensation, working terms and obligations."],
  ["legalShareholders", "pacte_actionnaires", "Shareholders' agreement", "Organize governance, transfers, exits and protections."],
  ["legalSettlementTermination", "protocole_transactionnel_resiliation", "Settlement and termination agreement", "Settle a dispute and organize the contractual termination."],
] as const;

function risklensGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "run",
      label: tx("studio.groups.runRisklens", "Run"),
      cards: [
        card(
          "risklensFull",
          "Full RiskLens",
          "Assume failure in 6 months; revise the plan before you build.",
          "Run a full RiskLens on the plan above. Frame it: 6 months from now, this has already failed. List every genuine failure reason, deep-dive each in parallel with subagents, then synthesize: most likely failure, most dangerous failure, hidden assumption, concrete revised plan, and a 3-5 item pre-launch checklist. Save a Track A risklens-report-[timestamp] UI (Vite + official DS + framer-motion + three + R3F + drei) and risklens-transcript-[timestamp].md, then open_preview on that UI (not Export PDF). Do not start implementing.",
        ),
        card(
          "risklensProduct",
          "Product / feature launch",
          "Stress-test a product or feature before coding starts.",
          "Run RiskLens on the product / feature launch in the plan above, before any implementation. Focus on audience mismatch, adoption friction, scope creep, competitive timing, and whether the success metric is reachable. Produce the HTML report, transcript, revised plan, and pre-launch checklist. Do not write code.",
        ),
        card(
          "risklensLaunch",
          "Go-to-market launch",
          "Find what kills a launch with money or reputation on the line.",
          "Run RiskLens on the go-to-market launch in the plan above. Probe pricing approval friction, channel reach, messaging-market fit, prep time vs. opportunity cost, and post-launch compounding risks. Deliver synthesis, revised plan, checklist, and saved reports. Do not execute the launch plan.",
        ),
      ],
    },
    {
      id: "decisions",
      label: tx("studio.groups.decisions", "Decisions"),
      cards: [
        card(
          "risklensHire",
          "Hire or role",
          "Why this hire fails - before you make the offer.",
          "Run RiskLens on the hire / role decision in the plan above. Probe role ambiguity, wrong seniority, culture fit, ramp time, and whether the hiring problem is actually an org-design problem. Deliver failure modes, revised role brief, and a pre-offer checklist.",
        ),
        card(
          "risklensPricing",
          "Pricing / business model",
          "What breaks if the pricing or model shift fails.",
          "Run RiskLens on the pricing / business-model change in the plan above. Probe willingness to pay, approval chains, cannibalization, unit economics, and switching costs. Deliver synthesis, a concrete test plan (pilot price / cohort), and a go/no-go checklist.",
        ),
        card(
          "risklensPartnership",
          "Partnership or deal",
          "Why the partnership dies after the handshake.",
          "Run RiskLens on the partnership / deal in the plan above. Probe misaligned incentives, unclear ownership, dependency risk, brand/reputation spillover, and exit terms. Deliver revised deal guardrails and a pre-signature checklist.",
        ),
        card(
          "risklensStrategy",
          "Strategy pivot",
          "Blind spots in a positioning or strategy shift.",
          "Run RiskLens on the strategy / positioning pivot in the plan above. Probe capability gaps, brand confusion, channel access, competitor response, and whether success criteria are measurable. Deliver revised strategy with early warning signs and a pre-commit checklist.",
        ),
      ],
    },
    {
      id: "focus",
      label: tx("studio.groups.focus", "Focus"),
      cards: [
        card(
          "risklensAssumptions",
          "Hidden assumptions",
          "Surface the one assumption that would kill the plan.",
          "Run RiskLens on the plan above focused on hidden assumptions. Lead with the single biggest unquestioned assumption, then early warning signs and the smallest experiment that would falsify it this week. Save the report files.",
        ),
        card(
          "risklensChecklist",
          "Pre-launch checklist only",
          "3-5 verifiable actions before execution.",
          "Run a focused RiskLens on the plan above and deliver primarily the pre-launch checklist: 3-5 verifiable actions that prevent or detect the top failure modes. Include a short synthesis (most likely failure + hidden assumption) and save the report files.",
        ),
      ],
    },
  ];
}

function scrapingGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "collect",
      label: tx("studio.groups.collect", "Collect"),
      cards: [
        card(
          "scrapePage",
          "Scrape a page",
          "Clean text/markdown from one or more URLs.",
          "Scrape the given URL(s) with the scrape tool (action=fetch): extract title, clean text/markdown, meta and outbound links. Save JSON + Markdown under scrape/, then summarize what you got (chars, status, issues).",
        ),
        card(
          "scrapeSite",
          "Crawl a site",
          "Same-domain BFS crawl with depth and page caps.",
          "Crawl the seed URL with scrape action=pipeline (same-domain, sensible max_depth/max_pages). Export xlsx + a short HTML report under scrape/. Highlight empty/JS-gated pages that need the browser tool next.",
        ),
        card(
          "scrapeSitemap",
          "From sitemap / list",
          "Fetch a URL list or sitemap seeds in parallel.",
          "Take the provided URL list or discover seeds from a sitemap, then scrape action=fetch in parallel. Deduplicate, keep source URLs, export csv + jsonl under scrape/, and report ok/error counts.",
        ),
        card(
          "scrapeJsPage",
          "JS-rendered page",
          "Use the browser when HTML is an empty shell.",
          "Open the target with the browser tool, capture rendered content (or the JSON API via network/response_body), normalize into scrape records, then export with scrape action=export. Prefer browser only when static fetch fails.",
        ),
      ],
    },
    {
      id: "clean",
      label: tx("studio.groups.clean", "Clean & enrich"),
      cards: [
        card(
          "scrapeCleanCorpus",
          "Clean a corpus",
          "Strip chrome, normalize text, drop near-dupes.",
          "Clean the scraped corpus: strip nav/chrome leftovers, normalize whitespace, drop near-duplicate pages, keep canonical URL + title on every row. Rewrite the cleaned files and note what was removed.",
        ),
        card(
          "scrapeEnrichMeta",
          "Enrich metadata",
          "Add description, keywords, language, fetched_at.",
          "Enrich each scraped record with useful metadata (description/OG fields, language guess, word count, fetched_at). Save an enriched JSON/CSV and a short field dictionary.",
        ),
        card(
          "scrapeTables",
          "Extract tables",
          "Turn HTML tables into structured rows.",
          "From the scraped pages, extract tabular data into structured rows (CSV/Excel). Keep the source URL per row, normalize column names, and flag incomplete cells.",
        ),
        card(
          "scrapeForRag",
          "Prepare for RAG",
          "Chunked Markdown corpus ready to index.",
          "Turn the scrape into a RAG-ready Markdown corpus: one file per page with YAML frontmatter (url, title, fetched_at), sensible chunk boundaries, and a manifest.json listing every file.",
        ),
      ],
    },
    {
      id: "export",
      label: tx("studio.groups.exportData", "Export"),
      cards: [
        card(
          "scrapeToExcel",
          "Export Excel",
          "Workbook with url, title, status, text.",
          "Export the scrape results to Excel (xlsx) under scrape/ with columns url, title, status, text, error. Add a summary sheet (counts, top errors) if useful.",
        ),
        card(
          "scrapeToCsvJson",
          "Export CSV + JSON",
          "Machine-friendly dumps for pipelines.",
          "Export the scrape results as both CSV and JSON/JSONL under scrape/. Confirm row counts match and include a one-line schema note.",
        ),
        card(
          "scrapeToXml",
          "Export XML",
          "Pages XML for legacy / enterprise ingest.",
          "Export the scrape results as XML (pages/page nodes) under scrape/, validate it is well-formed, and note the schema briefly.",
        ),
        card(
          "scrapeReport",
          "HTML report",
          "Readable report with ok/error overview.",
          "Produce an HTML scrape report (scrape action=export format=report) plus a short executive summary in chat: pages fetched, success rate, export paths, and recommended next crawl.",
        ),
      ],
    },
  ];
}

function contentGroups(tx: Tx): StudioGroup[] {
  const card = (
    id: string,
    format: DocFormat,
    label: string,
    description: string,
    prompt: string,
    recommendedTemplate?: string,
  ): StudioCard => ({
    id,
    format,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
    ...(recommendedTemplate ? { recommendedTemplate } : {}),
  });
  return [
    {
      id: "project",
      label: tx("studio.themes.project", "Project"),
      cards: [
        card(
          "projectPitch",
          "ppt",
          "Project pitch deck",
          "10-12 slides: vision, problem, solution, plan, team, next steps.",
          "Create a project pitch deck (PowerPoint, 10-12 slides) using the attached HTML design template only (do not invent a freestyle Inter/gradient design). Structure: cover, vision, problem, solution, approach, timeline, team, risks, next steps. Copy rules: every title is a full assertion (not a topic label like Vision/Problem); one idea per slide; max 6 bullets and 12 words each; concrete numbers with units; no filler (innovant, synergies, ecosystem). Follow presentation-designer then pptx-generator; fill template blocks or drop the slide; run preview_pptx and fix defects before delivery.",
          "textbook",
        ),
        card(
          "projectCharter",
          "doc",
          "Project charter",
          "Scope, objectives, stakeholders, governance, risks.",
          "Create a project charter (Word): context, objectives (SMART), scope in/out, stakeholders and roles, governance, milestones, budget summary, risks and mitigations.",
        ),
        card(
          "projectRoadmap",
          "xls",
          "Roadmap & planning",
          "Phased timeline with milestones, owners and status.",
          "Create a project roadmap workbook (Excel): phases, workstreams, tasks with owners, start/end dates, dependencies, milestone sheet, and a status dashboard with conditional formatting.",
        ),
        card(
          "projectStatus",
          "pdf",
          "Status report",
          "Progress, KPIs, blockers, decisions needed.",
          "Create a one-page project status report (PDF): overall RAG status, progress vs plan, key metrics, accomplishments, blockers, decisions needed, next period plan.",
        ),
        card(
          "projectBudget",
          "xls",
          "Project budget",
          "Cost breakdown, forecast vs actual, burn rate.",
          "Create a project budget workbook (Excel): cost breakdown by category, forecast vs actual with variance, monthly burn rate chart data, and a summary sheet.",
        ),
      ],
    },
    {
      id: "invest",
      label: tx("studio.themes.invest", "Investment"),
      cards: [
        card(
          "investDeck",
          "ppt",
          "Investor pitch deck",
          "12-15 slides: traction, market, model, ask.",
          "Create an investor pitch deck (PowerPoint, 12-15 slides) using the attached HTML design template only (do not invent a freestyle design). Structure: problem, solution, product, market size (TAM/SAM/SOM), business model, traction, competition, team, financial projections, funding ask and use of funds. Copy rules: assertion titles, one idea per slide, max 6 bullets / 12 words, consistent invented metrics with units, no startup filler. Follow presentation-designer then pptx-generator; preview_pptx before delivery.",
          "startup",
        ),
        card(
          "businessPlan",
          "doc",
          "Business plan",
          "Full structured plan: market, strategy, financials.",
          "Create a complete business plan (Word): executive summary, company, market analysis, competitive landscape, go-to-market strategy, operations, team, 3-year financial plan, risks.",
        ),
        card(
          "financialModel",
          "xls",
          "3-year financial model",
          "Revenue, costs, cash flow, unit economics.",
          "Create a 3-year financial model (Excel): assumptions sheet, revenue build-up, cost structure, P&L, cash flow, headcount plan, unit economics (CAC, LTV), scenario toggles.",
        ),
        card(
          "onePager",
          "pdf",
          "Investor one-pager",
          "Single-page teaser: key numbers and ask.",
          "Create an investor one-pager (PDF): elevator pitch, problem/solution, key traction metrics, market, business model, team highlights, funding ask. Dense but scannable layout.",
        ),
        card(
          "dueDiligence",
          "doc",
          "Due diligence pack",
          "Data-room checklist and company memo.",
          "Create a due diligence pack (Word): data-room checklist (legal, financial, tech, HR), company memorandum structure, and a Q&A preparation section for investor meetings.",
        ),
      ],
    },
    {
      id: "marketing",
      label: tx("studio.themes.marketing", "Marketing"),
      cards: [
        card(
          "marketingPlan",
          "ppt",
          "Marketing plan",
          "Annual plan: targets, channels, budget, KPIs.",
          "Create an annual marketing plan deck (PowerPoint) using the attached HTML design template only: objectives and KPIs, target segments, positioning, channel strategy, campaign calendar, budget allocation, measurement plan. Assertion titles, one idea per slide, concrete KPIs with units; no freestyle redesign.",
          "startup",
        ),
        card(
          "campaignBrief",
          "doc",
          "Campaign brief",
          "Objectives, audience, message, deliverables.",
          "Create a campaign brief (Word): background, objectives and KPIs, target audience and insight, key message and proof points, deliverables, channels, timeline, budget.",
        ),
        card(
          "editorialCalendar",
          "xls",
          "Editorial calendar",
          "30-day multi-channel content schedule.",
          "Create a 30-day editorial calendar (Excel): date, channel, format, topic, hook, CTA, owner, status columns; one sheet per channel plus a master view.",
        ),
        card(
          "marketResearch",
          "doc",
          "Market research",
          "Market sizing, trends, competitors, insights.",
          "Create a market research report (Word): methodology, market size and growth, trends, competitor profiles, customer insights, opportunities and recommendations. Research real data with web tools.",
        ),
        card(
          "brandDeck",
          "ppt",
          "Brand deck",
          "Identity, voice, messaging and guidelines.",
          "Create a brand presentation deck (PowerPoint) using the attached HTML design template only: brand story, mission and values, personality and voice, messaging pillars, visual identity guidelines, do's and don'ts. Keep the template look; write sharp assertion titles; no freestyle redesign.",
          "editorial_luxe",
        ),
      ],
    },
    {
      id: "sales",
      label: tx("studio.themes.sales", "Sales"),
      cards: [
        card(
          "salesProposal",
          "doc",
          "Sales proposal",
          "Client-ready proposal: needs, solution, pricing.",
          "Create a sales proposal (Word): client context and needs, proposed solution, scope and deliverables, timeline, pricing options, terms, next steps. Professional and persuasive.",
        ),
        card(
          "salesDeck",
          "ppt",
          "Product sales deck",
          "Value-first deck for prospect meetings.",
          "Create a product sales deck (PowerPoint) using the attached HTML design template only: customer pain, value proposition, product demo flow, proof (cases, metrics), pricing overview, call to action. Assertion titles, proof with numbers, one idea per slide; no freestyle redesign.",
          "black_and_white_clean",
        ),
        card(
          "pipelineForecast",
          "xls",
          "Pipeline & forecast",
          "Deal tracker with stages and weighted forecast.",
          "Create a sales pipeline workbook (Excel): deal tracker (account, stage, amount, probability, close date, owner), weighted forecast per quarter, and a summary dashboard.",
        ),
        card(
          "battlecard",
          "pdf",
          "Competitive battlecard",
          "Win against a competitor: strengths, traps, counters.",
          "Create a competitive battlecard (PDF, 1-2 pages): competitor overview, their strengths and weaknesses, our differentiators, objection handling, landmine questions, proof points.",
        ),
        card(
          "pricingSheet",
          "xls",
          "Quote & pricing grid",
          "Configurable quote template with tiers.",
          "Create a quote and pricing workbook (Excel): pricing grid with tiers and options, a quote sheet with automatic totals, discounts and taxes, ready to fill per client.",
        ),
      ],
    },
    {
      id: "hr",
      label: tx("studio.themes.hr", "HR & Training"),
      cards: [
        card(
          "onboardingBook",
          "doc",
          "Onboarding handbook",
          "Welcome guide: culture, tools, first 30 days.",
          "Create an employee onboarding handbook (Word): welcome and culture, team and org overview, tools and access, policies summary, 30-60-90 day plan, FAQ.",
        ),
        card(
          "trainingDeck",
          "ppt",
          "Training deck",
          "Pedagogical slides with exercises and recaps.",
          "Create a training deck (PowerPoint) using the attached HTML design template only: learning objectives, structured modules with one concept per slide, practical exercises, knowledge-check questions, summary and resources. Clear instructional titles; no freestyle redesign.",
          "textbook",
        ),
        card(
          "reviewGrid",
          "xls",
          "Performance review grid",
          "Competency matrix and annual review scoring.",
          "Create a performance review workbook (Excel): competency matrix with rating scales, objectives tracking, self-assessment vs manager columns, and a synthesis sheet.",
        ),
        card(
          "jobDescription",
          "doc",
          "Job description",
          "Role, missions, requirements, hiring process.",
          "Create a job description (Word): role summary, key missions, required skills and experience, nice-to-haves, compensation range structure, hiring process steps.",
        ),
        card(
          "hrReport",
          "pdf",
          "HR monthly report",
          "Headcount, hiring, attrition, engagement KPIs.",
          "Create a monthly HR report (PDF): headcount and evolution, hiring pipeline, attrition and reasons, engagement indicators, key actions for next month.",
        ),
      ],
    },
    {
      id: "legal",
      label: tx("studio.themes.legal", "Legal"),
      cards: LEGAL_CONTRACTS.map(([id, templateName, label, description]) => ({
        id,
        format: "doc" as const,
        label: tx(`studio.cards.${id}.label`, label),
        description: tx(`studio.cards.${id}.desc`, description),
        prompt: `Draft a complete, professional ${label} using the attached contractual template. Adapt every clause to the user's facts and clearly mark any information that still needs confirmation. After drafting, run a first-pass review with contract-reviewer (blockers, negotiation points, missing protections). This is analysis, not legal advice.`,
        legal: true,
        templateName,
      })),
    },
  ];
}

function marketingGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "creative",
      label: tx("studio.groups.creative", "Creative"),
      cards: [
        card(
          "productImages",
          "Product images",
          "Generate polished product visuals and packshots.",
          "Generate professional product images under studio-expert-contract: propose 3 visual directions (angle + use), then produce studio packshot, lifestyle, and social-ready variants with consistent branding. Use generate_image (and montage assemble if you sequence stills). If a media template is attached, download it first and match that family/tag/format. Save under marketing/creatives/. Deliverables table plus expert gate. No invented product claims.",
        ),
        card(
          "adVideo",
          "Ad video",
          "Script + scenes + generated advertising video.",
          "Produce a complete advertising video as a senior brand desk: lock offer/audience/CTA, write a hook-driven 15-30s script and storyboard, then generate_video / generate_music / generate_speech and montage(action=assemble) for the cut. If a media template is attached, download it and keep its format. Save under marketing/; never invent performance metrics.",
        ),
        card(
          "projectMontage",
          "Open Montage studio",
          "Full product demo → multi-platform video desk (use Studio → Montage).",
          "Open Studio → Montage (or run /montage). Record a live product demo, package YouTube / Shorts / Reels / Feed / TikTok / LinkedIn exports, optional AI B-roll + Lyria music, HTML report under marketing/montage/. Prefer real UI footage. Never auto-publish. Managed media needs Navin Plus+ (or BYOK OpenRouter under Settings → Providers).",
        ),
        card(
          "socialVisuals",
          "Social media visuals",
          "Branded image sets for each platform format.",
          "Create social visuals with placement specs: 1:1 feed, 9:16 story/reel cover, 16:9 link post, consistent brand tokens. Use generate_image and attached media templates (Stock/Style/Character/...). Save under marketing/creatives/, list safe-margin notes and copy space per asset.",
        ),
        card(
          "brandKit",
          "Brand kit",
          "Logo directions, palette, typography, voice.",
          "Build a brand kit and voice card: 3 logo directions (generate images), hex palette, typography pairing, we-say/we-never-say, sample paragraphs. Save brand/<name>-voice.md plus kit assets; keep multi-brand discipline.",
        ),
      ],
    },
    {
      id: "design",
      label: tx("studio.groups.design", "Design"),
      cards: [
        card(
          "posterDesign",
          "Poster / key visual",
          "One hero poster designed from your references.",
          "Design ONE hero poster / key visual as a senior art director: lock a layout grid (attached Layouts reference if any), typography hierarchy, palette (attached Palettes reference), then produce the final visual with generate_image using the attached media references as reference_images. Deliver 4:5 master plus 9:16 and 1:1 crops with safe margins. Save under marketing/creatives/posters/. No placeholder filler text, real copy only.",
        ),
        card(
          "bannerPack",
          "Display banner pack",
          "Full ad-network set, every standard size.",
          "Produce a display banner pack from one master visual: design the master with generate_image (attached references as reference_images), then derive 300x250, 336x280, 728x90, 160x600, 320x50, 970x250 with exec + Pillow (exact pixel sizes, text legible at 100%). One CTA per banner, brand palette locked. Save under marketing/creatives/banners/ plus an index HTML contact sheet.",
        ),
        card(
          "productOrbit3d",
          "3D product page",
          "Interactive orbit view of the product on a web page.",
          "Build an interactive 3D product page: validated packshot (generate_image with attached references) as texture/fallback, then a web page with three + @react-three/fiber + @react-three/drei showing the product in orbit (designed 3D layer, not wallpaper), lighting and stage matching the brand palette. Working controls, no stub buttons. Save under marketing/creatives/orbit/ and give the preview command. Never bake this canvas into PPT/Word.",
        ),
        card(
          "socialCarousel",
          "Carousel design",
          "Multi-slide narrative carousel, ready to post.",
          "Design a 5-7 slide social carousel: narrative arc (hook slide, value slides, CTA slide), consistent grid and typography, visuals via generate_image with the attached references, exact 1080x1350 exports via exec + Pillow. Save under marketing/creatives/carousels/ with per-slide copy in a companion file.",
        ),
      ],
    },
    {
      id: "contentGroup",
      label: tx("studio.groups.content", "Content"),
      cards: [
        card(
          "socialPosts",
          "Social media posts",
          "Platform-native posts with hooks and hashtags.",
          "Write platform-native social posts (LinkedIn, X, Instagram, TikTok scripts): 3+ hook variants for flagship posts, native tone (no identical cross-posts), hashtags, one CTA, suggested timing, UTM when linking off-platform. Save marketing/social/*.md; no fabricated engagement metrics.",
        ),
        card(
          "blogArticle",
          "Blog article",
          "Long-form article optimized for engagement.",
          "Write a complete blog article with proof-backed claims only: title options, H2/H3 outline, engaging intro, actionable body with real examples/sources, CTA conclusion, meta description, 3 social snippets. Save under marketing/ or seo/content/ as appropriate.",
        ),
        card(
          "emailSequence",
          "Email sequence",
          "5-email nurture or launch sequence.",
          "Write a 5-email nurture or launch sequence: subject A/B, preview text, PAS/AIDA body, one CTA per email, send timing, UTM links. Align to persona + offer; never invent testimonials. Save marketing/copy/email-sequence-*.md.",
        ),
        card(
          "landingCopy",
          "Landing page copy",
          "Conversion-focused page: hero, proof, CTA.",
          "Write conversion landing copy section by section: hero headline/subhead variants, benefit blocks, proof (only real or marked placeholder), objection FAQ, single primary CTA mapped to campaign KPI. Save marketing/copy/landing-*.md.",
        ),
      ],
    },
    {
      id: "strategy",
      label: tx("studio.groups.strategy", "Strategy"),
      cards: [
        card(
          "fullCampaign",
          "360° campaign",
          "Complete campaign: message, assets, plan.",
          "Design and produce a senior 360° campaign under studio-expert-contract: brief (objective+KPI, persona, offer, message house, UTMs), run campaign-manager brief checker, channel plan by funnel stage, then generate deliverables (ad copy, social, visuals, video script, email, landing). Save under marketing/ + marketing-report-*.html with paths; critic-review and PASS/WARN/BLOCK gate. No invented ROAS/CPC.",
        ),
        card(
          "persona",
          "Customer persona",
          "Data-backed persona with pains and channels.",
          "Build 2-3 evidence-backed personas (primary/secondary/blocker): ICP firmographics, goals, pains with sources or marked assumptions, objections, triggers, watering holes, messaging map pain→message→proof. Research with web tools; save marketing/personas.md.",
        ),
        card(
          "contentCalendar",
          "30-day content plan",
          "Full month of content mapped to goals.",
          "Create a 30-day content plan mapped to funnel stages and campaign KPIs: weekly themes, daily row with channel, format, hook, CTA, UTM campaign name. Save as a structured table file under marketing/campaigns/.",
        ),
        card(
          "competitorScan",
          "Competitor analysis",
          "Positioning map and messaging gaps.",
          "Analyze competitors' marketing with fetched public pages: positioning, messaging, channels, content, offers. Comparison table with evidence URLs, differentiation angles, and prioritized actions. No invented traffic/ad-spend numbers.",
        ),
      ],
    },
  ];
}

function adsGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "adsEngine",
      label: tx("studio.groups.adsEngine", "Analysis engine (exports & MCP data)"),
      cards: [
        card(
          "adsImportAnalyze",
          "Audit from exports",
          "Real numbers from your Ads Manager exports: waste, CPA, CTR, pacing.",
          "Run the `ads` engine on my real data: action=pipeline with paths = the Ads Manager exports I attach to this message (CSV/XLSX from Google, Microsoft, Meta, LinkedIn, TikTok or Reddit; campaign report + search terms report when available) or the files under ads/imports/. Ask for my monthly budget and currency first if I did not give them. Then walk me through: account KPIs, wasted spend, high-CPA and low-CTR entities, pacing, and the proposed changes (ids, estimated savings). Write ads/ads-report.html + a Track A ads-report-* UI (Three.js, open_preview, not PDF). Do not apply anything: changes stay proposed until I approve them. No invented numbers; keep the data_gap entries visible.",
        ),
        card(
          "adsWasteNegatives",
          "Wasted spend & negatives",
          "Search terms that spend without converting, as ready negatives.",
          "Focus on wasted spend with the `ads` engine: action=pipeline on my search terms / keyword exports (attached or under ads/imports/), then list the search terms and keywords that spend without converting with their clicks, cost and monthly projection. Prepare the negative keyword proposals (exact match, ad group scope when known) and, once I approve them (action=changes status=approved ids=...), action=export_changes format=google_editor or microsoft_bulk so I can import the file in the editor. Save under ads/. Real exported numbers only.",
        ),
        card(
          "adsChangesReview",
          "Approve & apply changes",
          "Review proposals, approve, export or apply via MCP.",
          "Open the change queue with the `ads` engine: action=changes (list proposed / approved / applied). Present each proposal with its evidence and estimated monthly savings, ask me which ids to approve or reject, then record my decision (action=changes status=approved|rejected ids=...). For approved changes: action=export_changes (csv / google_editor / microsoft_bulk) or, if the platform MCP is connected, execute the mcp_plan steps one by one with the MCP write tools, verify each read-back, and mark them applied (status=applied). Never touch budgets, bids or status without an approved change id.",
        ),
      ],
    },
    {
      id: "googleAds",
      label: tx("studio.groups.googleAds", "Google Ads"),
      cards: [
        card(
          "googleAdsOverview",
          "Account overview",
          "List customers and live campaign structure via MCP.",
          "Use MCP `google-ads` when connected (Settings → MCP). List accessible customers, summarize active campaigns/ad groups and last-7-day spend/CTR/conversions with source metrics. If MCP is missing, ask for Ads exports - never invent numbers. Save under ads/google/ + ads-report-*.html.",
        ),
        card(
          "googleAdsStructure",
          "Campaign structure",
          "Propose or refine Search/PMax structure from live data.",
          "As paid-ads-manager for Google Ads: prefer MCP `google-ads` live reads first, then propose campaign → ad group → keyword/theme structure with negatives, budgets, and tracking checklist. Do not invent account topology. Save ads/google/structure-*.md + ads-report-*.html.",
        ),
        card(
          "googleAdsOptimize",
          "Weekly optimization",
          "Kill/scale decisions from recent metrics.",
          "Pull recent Google Ads metrics via MCP `google-ads` (or CSV if unavailable), feed the rows to the `ads` engine (action=pipeline, data=rows or paths=exports). Produce a weekly kill/scale report from its findings: winners, losers, search-term negatives, budget shifts - evidence only. Save ads/google/optimize-*.md + ads-report-*.html; no mutations without an approved change id.",
        ),
      ],
    },
    {
      id: "microsoftAds",
      label: tx("studio.groups.microsoftAds", "Microsoft Ads"),
      cards: [
        card(
          "microsoftAdsOverview",
          "Account overview",
          "Accounts, campaigns and reports via Microsoft Ads MCP.",
          "Use MCP `microsoft-ads` when connected (Settings → MCP). List accessible accounts, summarize active campaigns/ad groups and a last-7-day performance report (spend, clicks, CTR, conversions) with source metrics. If MCP is missing, ask for Microsoft Advertising exports and run the `ads` engine on them - never invent numbers. Save under ads/microsoft/ + ads-report-*.html.",
        ),
        card(
          "microsoftAdsStructure",
          "Campaign structure",
          "Search structure imported or mirrored from Google.",
          "As paid-ads-manager for Microsoft Ads: prefer MCP `microsoft-ads` live reads first, then propose campaign → ad group → keyword structure with negatives, budgets, UET tracking checklist, and whether to import from Google Ads. Do not invent account topology. New entities stay paused unless the user explicitly asks to activate. Save ads/microsoft/structure-*.md + ads-report-*.html.",
        ),
        card(
          "microsoftAdsOptimize",
          "Weekly optimization",
          "Kill/scale, negatives and quality score from reports.",
          "Pull recent Microsoft Ads reports via MCP `microsoft-ads` (or exports if unavailable) and feed the rows to the `ads` engine (action=pipeline). Weekly kill/scale report: wasted spend, search-term negatives, quality score, budget shifts - evidence only. Approved changes go out through action=export_changes format=microsoft_bulk or the MCP write tools; no mutations without approval.",
        ),
      ],
    },
    {
      id: "metaAds",
      label: tx("studio.groups.metaAds", "Meta Ads"),
      cards: [
        card(
          "metaAdsOverview",
          "Account overview",
          "Read Meta ad accounts via hosted MCP.",
          "Use MCP `meta-ads` (https://mcp.facebook.com/ads) when connected. List ad accounts, active campaigns/ad sets, and recent performance. If MCP missing or account not MCP-enabled, ask for Ads Manager exports. Save ads/meta/ + ads-report-*.html. Never invent ROAS.",
        ),
        card(
          "metaAdsStructure",
          "Campaign structure",
          "Audience → ad set → creative plan grounded in live data.",
          "Plan Meta (FB/IG) structure with paid-ads-manager: prefer MCP `meta-ads` reads, then audiences, placements, creative tests, pixel/CAPI checklist. New entities must stay paused unless the user explicitly asks to activate. Save ads/meta/structure-*.md + ads-report-*.html.",
        ),
        card(
          "metaAdsOptimize",
          "Creative & budget loop",
          "Fatigue, CPA, and budget recommendations.",
          "Using MCP `meta-ads` metrics when available, identify creative fatigue, high-CPA ad sets, and budget reallocation candidates. Evidence-only recommendations; no spend mutations without approval. Save ads/meta/optimize-*.md + ads-report-*.html.",
        ),
      ],
    },
    {
      id: "tiktokAds",
      label: tx("studio.groups.tiktokAds", "TikTok Ads"),
      cards: [
        card(
          "tiktokAdsOverview",
          "Advertiser overview",
          "Campaigns and reports via TikTok Ads MCP.",
          "Use MCP `tiktok-ads` when connected. List authorized advertisers, campaigns, and recent reports. If MCP is missing, ask for TikTok Ads Manager exports. Save ads/tiktok/ + a Track A ads-report-* UI (Three.js, open_preview, not PDF).",
        ),
        card(
          "tiktokAdsStructure",
          "Campaign structure",
          "Spark/In-Feed structure and creative brief.",
          "Plan TikTok Ads structure (campaign → ad group → ads) with hooks suited to short-form, using MCP `tiktok-ads` live data when available. Include pixel/events checklist. Save ads/tiktok/structure-*.md + ads-report-*.html.",
        ),
        card(
          "tiktokAdsOptimize",
          "Performance loop",
          "CPA/CTR cut and scale recommendations.",
          "From MCP `tiktok-ads` reports (or exports), recommend kill/scale and creative rotation. No invented metrics; no mutations without approval. Save ads/tiktok/optimize-*.md + ads-report-*.html.",
        ),
      ],
    },
    {
      id: "redditAds",
      label: tx("studio.groups.redditAds", "Reddit Ads"),
      cards: [
        card(
          "redditAdsOverview",
          "Account overview",
          "Campaigns and performance via Reddit Ads MCP.",
          "Use MCP `reddit-ads` when connected (read tier by default). List accounts, campaigns, ad groups, and last-7-day performance. If MCP missing, ask for Reddit Ads exports. Save ads/reddit/ + ads-report-*.html.",
        ),
        card(
          "redditAdsStructure",
          "Community targeting plan",
          "Subreddit/interest structure grounded in data.",
          "Plan Reddit Ads structure with community targeting (subreddits/interests), using MCP `reddit-ads` search tools when available. Keep write tier read unless the user asks to mutate. Save ads/reddit/structure-*.md + ads-report-*.html.",
        ),
        card(
          "redditAdsOptimize",
          "Weekly optimization",
          "Pause/scale recommendations from reports.",
          "Using MCP `reddit-ads` reports, propose pause/scale and creative swaps. Do not raise write tier or change spend without explicit user approval. Save ads/reddit/optimize-*.md + ads-report-*.html.",
        ),
      ],
    },
    {
      id: "linkedinAds",
      label: tx("studio.groups.linkedinAds", "LinkedIn Ads"),
      cards: [
        card(
          "linkedinAdsOverview",
          "Account overview",
          "Ad accounts and campaign groups via LinkedIn Ads MCP.",
          "Use MCP `linkedin-ads` when connected (Settings → MCP). List ad accounts, campaign groups, and last-7-day spend/CTR/leads with source metrics. If MCP is missing, ask for Campaign Manager exports - never invent numbers. Save under ads/linkedin/ + a Track A ads-report-* UI (Three.js, open_preview, not PDF).",
        ),
        card(
          "linkedinAdsStructure",
          "Title / industry structure",
          "Tight B2B ICP: titles, seniority, company size.",
          "As paid-ads-manager for LinkedIn Ads: prefer MCP `linkedin-ads` live reads first, then propose campaign group → campaign → creative structure with title/industry/seniority targeting, budget caps, and lead-gen form/tracking checklist. High CPC: keep ICP tight. Do not invent account topology. New entities stay paused unless the user explicitly asks to activate. Save ads/linkedin/structure-*.md + ads-report-*.html.",
        ),
        card(
          "linkedinAdsOptimize",
          "Weekly B2B optimization",
          "Kill/scale from CPL and audience demographics.",
          "Pull recent LinkedIn Ads metrics via MCP `linkedin-ads` (or CSV if unavailable). Produce a weekly kill/scale report: winners, losers, job-function/seniority breakdowns, budget shifts - evidence only. Prefer read-only; write tools can change spend. No mutations without explicit approval. Save ads/linkedin/optimize-*.md + ads-report-*.html.",
        ),
      ],
    },
  ];
}

function seoGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "audit",
      label: tx("studio.groups.audit", "Audit"),
      cards: [
        card(
          "techAudit",
          "Technical audit",
          "Crawlability, speed hints, structured data, meta.",
          "Run a senior technical SEO audit under studio-expert-contract: fetch homepage, robots.txt, sitemap, and a sample of key pages; check indexability, canonicals, meta/headings, structured data, redirects, internal links. Order Critical/Important/Nice-to-have with URL+evidence; score with audit_score.py when useful. CWV only with PSI data or mark requires measurement. Save seo/ + seo-report-*.html; expert gate.",
        ),
        card(
          "onPageAudit",
          "On-page audit (URL)",
          "Deep audit of a single page vs its target query.",
          "On-page audit one URL: fetch the page and top 3 SERP peers; evaluate title/meta/H-structure, intent match, content gaps, internal links, media, schema. Deliver before→after rewrites in a prioritized table; save seo/on-page-*.md.",
        ),
        card(
          "seoCompetitors",
          "Competitor analysis",
          "Compare rankings strategy, content and gaps.",
          "Analyze SERP competitors (who ranks, not only business rivals): fetch key pages, compare content/IA/strengths, produce beatable gap opportunities with evidence URLs. Never invent DR/backlink counts without seo-data-provider. Save seo/competitor-analysis-*.md.",
        ),
        card(
          "contentAudit",
          "Content audit",
          "Inventory, decay, cannibalization, refresh plan.",
          "Audit main site content via fetch/sample: inventory, thin/outdated flags, cannibalization risks, keep/refresh/merge/delete plan with owners and priority. State sample scope honestly; save seo/content-audit-*.md.",
        ),
      ],
    },
    {
      id: "research",
      label: tx("studio.groups.research", "Research"),
      cards: [
        card(
          "keywordResearch",
          "Keyword research",
          "Keywords with intent, difficulty and priority.",
          "Keyword research as a senior SEO desk: seed expansion, intent (informational/commercial/transactional), qualitative Low/Med/High difficulty from SERP, clusters with one primary keyword per page. Use seo-data-provider if configured; never invent volume/KD. Save seo/keyword-map-*.md table with Priority P1-P3.",
        ),
        card(
          "topicClusters",
          "Content plan & clusters",
          "Pillar pages and supporting cluster articles.",
          "Build topic clusters: pillars, supporting articles, primary+secondary keywords, intent, internal linking mesh, publishing order by business impact × winnability. Save seo/content-plan-*.md.",
        ),
        card(
          "questionsResearch",
          "Questions & intents",
          "People-also-ask style questions to target.",
          "Research user questions (PAA-style, forums, related searches): classify by intent, map to FAQ/article/tool formats, flag GEO quotability quick wins. Cite search evidence; save seo/questions-*.md.",
        ),
        card(
          "localSeo",
          "Local SEO",
          "Local pack, profile, citations, local pages.",
          "Local SEO plan: NAP consistency, profile checklist, local keywords, citations list, unique location page structure, LocalBusiness schema draft, reviews plan. No fake locations/reviews. Save seo/local-seo-*.md.",
        ),
      ],
    },
    {
      id: "optimize",
      label: tx("studio.groups.optimize", "Optimize"),
      cards: [
        card(
          "seoArticle",
          "SEO article",
          "Full optimized article ready to publish.",
          "Write a publish-ready SEO article under studio-expert-contract: title+meta, H1-H3 covering entities, answer-first intros, natural keywords, FAQ, internal links, Article/FAQPage JSON-LD, hero+section images (or prompts if image gen unavailable). No fabricated stats. Save seo/content/<slug>.md + seo-report-*.html; expert gate.",
        ),
        card(
          "metaTags",
          "Meta & tags optimization",
          "Rewritten titles, descriptions, OG tags.",
          "Optimize meta for given URLs after fetching them: CTR-focused titles (length), meta descriptions, OG/Twitter, heading notes. Before/after tables with uniqueness checks; save seo/meta-*.md.",
        ),
        card(
          "schemaMarkup",
          "Schema.org markup",
          "Structured data ready to paste (JSON-LD).",
          "Generate valid JSON-LD for the fetched pages (Organization, Product, Article, FAQ, Breadcrumb, LocalBusiness as relevant) with placement instructions. Prefer facts from the live page; mark unknowns. Save seo/schema-*.json.md.",
        ),
        card(
          "linkStrategy",
          "Links & backlinks plan",
          "Internal mesh plan and backlink acquisition.",
          "Linking strategy: internal mesh for money pages + realistic backlink targets (no PBNs/paid links), outreach templates, effort/impact priority. No invented DR metrics without API. Save seo/backlink-plan-*.md.",
        ),
      ],
    },
  ];
}

function montageGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "start",
      label: tx("studio.groups.start", "Start"),
      cards: [
        card(
          "montageFullPipeline",
          "Full montage (recommended)",
          "Default: doctor → live demo → package all social profiles → kit + calendar.",
          "Run the full Montage desk. 1) montage(action=doctor) then setup if needed. 2) browser record_start → drive the product happy path → record_stop. 3) montage(action=demo_register, path=…). 4) montage(action=package, path=…, profiles=default) for YouTube Landscape, Shorts, IG Reels, IG Feed, TikTok, LinkedIn (add profiles=all for YouTube 4K + Cinematic 21:9). 5) analyze + calendar 14 days. 6) Optional generate_image / generate_video / generate_music (Lyria) after validation. Save under marketing/montage/ + montage-report-*.html. Prefer real UI footage. Never auto-publish. Stop if media tools are not configured and explain Plus vs BYOK.",
        ),
        card(
          "montageDoctor",
          "Check toolchain",
          "ffmpeg, browser, HyperFrames readiness.",
          "Run montage(action=doctor) then montage(action=detect). Summarize what is ready vs missing, and the exact fix (install ffmpeg, montage setup for HyperFrames, browser). Do not invent readiness.",
        ),
        card(
          "montageAccessGuide",
          "Plus vs own providers (required read)",
          "How to unlock image/video/music/STT for Montage.",
          "Explain clearly to the user (French if they write French):\n1) Recommended: Navin Plus or higher - activate the plan so the managed Navin key powers image, video, music (Lyria), STT and TTS under Settings → Voice / Image / Video / Music.\n2) Alternative BYOK: add your own OpenRouter (or other) API keys under Settings → Providers, then set Image / Video / Music / Transcription providers to openrouter (or matching BYOK) and pick models.\n3) Free plan alone cannot run managed media generations.\n4) Local montage package (ffmpeg crops) works without AI once a demo file exists.\n5) List Settings paths and ask which path they choose before spending budget. Do not invent prices; use catalog notes when available.",
        ),
      ],
    },
    {
      id: "demo",
      label: tx("studio.groups.demo", "Demo"),
      cards: [
        card(
          "montageLiveDemo",
          "Product demo (browser)",
          "Film yourself, or brief the agent with URL + exact steps. Saves under marketing/montage/demos/.",
          "Ask me once for: (1) product URL, (2) exact happy path to film, (3) login/access notes if needed. Then: navigate to that URL → browser(action=record_start) → drive only those steps → record_stop → montage(action=demo_register, path=…). Prefer real UI over screenshots. Save under marketing/montage/demos/. Do not invent another product. Never auto-publish.",
        ),
        card(
          "montagePackageProfiles",
          "Package platform exports",
          "YouTube, Shorts, Reels, Feed, TikTok, LinkedIn (+ optional 4K / cinematic).",
          "Package an existing demo with montage(action=package, path=…, profiles=default). Profiles: youtube_landscape 1920x1080, youtube_shorts / instagram_reels / tiktok 1080x1920, instagram_feed 1080x1080, linkedin 1920x1080. For YouTube 4K 3840x2160 and Cinematic 2560x1080 use profiles=all (or list ids). Optional captions via srt=. Write brief under marketing/montage/exports/. Never auto-publish.",
        ),
        card(
          "montageScreenshots",
          "Capture UI stills",
          "Register product screenshots into the montage kit.",
          "Capture or register UI stills with browser screenshots or user files, then montage(action=screenshot, path=… or paths=…). Organize under marketing/montage/captures/ with short captions for social use.",
        ),
      ],
    },
    {
      id: "creative",
      label: tx("studio.groups.creative", "Creative"),
      cards: [
        card(
          "montageAiImages",
          "AI product images",
          "Studio / lifestyle / social packs linked to this project.",
          "Generate project-linked product images with generate_image (Navin/OpenRouter). Propose 3 directions, produce packshot + lifestyle + social-safe variants. Copy usable assets into marketing/montage/creatives/ (and keep artifact paths). Wait for validation before large batches. No invented product claims.",
        ),
        card(
          "montageAiVideo",
          "AI B-roll / promo clips",
          "Short AI video for hooks; combine with real demo when possible.",
          "After locking the brief, generate short AI clips with generate_video only when the user validates cost. Prefer real demo footage for UI truth; use AI for lifestyle/B-roll. Aspect ratios: 16:9 and 9:16. Save paths under marketing/montage/creatives/ and note platform profile targets.",
        ),
        card(
          "montageMusic",
          "Music bed (Lyria)",
          "30s clip by default; full song on request.",
          "Generate a music bed with generate_music: default Lyria Clip 30s (0.04 $). Lyria Pro full song (0.08 $) only if the user explicitly asks for a complete track. Confirm Plus/BYOK music provider first. Save under media + copy reference into marketing/montage/creatives/. Do not auto-mux unless the user asks; propose mix next step.",
        ),
        card(
          "montageHyperframes",
          "Compose & render (HyperFrames)",
          "HTML composition → MP4 with a platform profile.",
          "If HyperFrames is missing, montage(action=setup). Author HTML under marketing/montage/compositions/, then montage(action=render, composition=…, profile=youtube_shorts|instagram_feed|linkedin|…). List profile sizes via montage(action=profiles). Deliver MP4 path; never auto-publish.",
        ),
        card(
          "montageCompositionDesign",
          "Design a composition",
          "Motion title / lower-third / end-card, designed then rendered.",
          "Design a motion composition like an art director: animated title card, lower-third or end-card as HTML/CSS under marketing/montage/compositions/ (typography hierarchy, brand palette, attached Looks/Palettes references as the grade). Preview a frame with montage(action=screenshot), iterate, then montage(action=render, composition=…, profile=…) for the target platform. Deliver the MP4 and the composition path.",
        ),
      ],
    },
    {
      id: "plan",
      label: tx("studio.groups.plan", "Plan"),
      cards: [
        card(
          "montageAnalyze",
          "Analyze project for montage",
          "Positioning, proof points, demo script skeleton.",
          "Run montage(action=analyze) and enrich with a clear demo script (hook 3s, steps, CTA). Write kit under marketing/montage/. Prefer facts from the workspace README/product.",
        ),
        card(
          "montageCalendar",
          "14-day content calendar",
          "Channel plan using exported creatives.",
          "Run montage(action=calendar, days=14) after kit/analyze. Map each day to a platform profile (LinkedIn 16:9, TikTok/Reels 9:16, Feed 1:1). Point to real export paths when available. Save calendar under marketing/montage/.",
        ),
        card(
          "montageListProfiles",
          "List render profiles",
          "Built-in sizes for every major platform.",
          "Call montage(action=profiles) and present a clear table: Profile, Resolution, Aspect. Include YouTube Landscape 1920x1080, YouTube 4K 3840x2160, Shorts/Reels/TikTok 1080x1920, IG Feed 1080x1080, LinkedIn 1920x1080, Cinematic 2560x1080. Explain default package vs profiles=all.",
        ),
      ],
    },
  ];
}

function meetingGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "capture",
      label: tx("studio.groups.meetingCapture", "Capture"),
      cards: [
        card(
          "meetingMinutes",
          "Meeting minutes",
          "CR with decisions and owned actions from a transcript.",
          "Produce meeting minutes under studio-expert-contract from the provided transcript/notes. Separate decisions, discussion facts, and actions (owner+deadline). Save meetings/<slug>/minutes.md + summary.md + meeting-report-*.html. Never invent attendees or quotes.",
        ),
        card(
          "meetingSummary",
          "Executive summary",
          "Short stakeholder summary of outcomes and next steps.",
          "Write a concise executive summary (goal, outcomes, risks, next steps). Save meetings/<slug>/summary.md and meeting-report-*.html. Cite only transcript facts.",
        ),
        card(
          "meetingImport",
          "Clean transcript",
          "Normalize a rough transcript before summarizing.",
          "Clean and lightly structure the transcript (paragraphs, labeled speakers only if present). Do not invent speakers. Save meetings/<slug>/transcript.md then offer minutes.",
        ),
      ],
    },
    {
      id: "follow",
      label: tx("studio.groups.meetingFollow", "Follow-up"),
      cards: [
        card(
          "meetingFollowup",
          "Follow-up email",
          "Warm external email draft (human sends).",
          "Draft a same-day follow-up email (thanks, 3-5 retained points, next steps with owners/dates). Save meetings/<slug>/follow-up.md. Human sends. No internal commentary.",
        ),
        card(
          "meetingActions",
          "Action list",
          "Owner + deadline table with evidence quotes.",
          "Extract Action | Owner | Deadline | Evidence quote. Flag ambiguous items. Save meetings/<slug>/actions.md + meeting-report-*.html.",
        ),
        card(
          "meetingDiscovery",
          "Discovery digest",
          "Sales discovery notes and angles.",
          "Apply discovery-call-assistant: pains, buying signals, objections, recommended next step. Save meetings/<slug>/discovery.md + meeting-report-*.html. Label facts vs hypotheses.",
        ),
      ],
    },
  ];
}

function leadsGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "prospect",
      label: tx("studio.groups.prospect", "Find"),
      cards: [
        card(
          "companySearch",
          "Search companies",
          "Sourced company list matching your ICP.",
          "Hunt companies matching the ICP under studio-expert-contract: directories, registries, awards, competitor ecosystems, exhibitors. Use web_search, web_fetch, scrape (same-domain), and Exa/Firecrawl MCP when available. Deduplicated sales/prospects-*.csv (company, website, size, sector, country, signal, source, confidence); validate with score_leads.py --validate-only; top 5 fits + why; leads-report-*.html; expert gate. Never invent companies.",
        ),
        card(
          "peopleSearch",
          "Search people",
          "Decision-makers with role, profile and source.",
          "Find decision-makers from public sources only (team pages, press, conference bios, public profiles): person, role, company, profile URL, source, confidence. Use scrape/browser for JS team pages when needed. No login-walled scrapes; never invent contacts. Save table/CSV under sales/.",
        ),
        card(
          "jobSearch",
          "Search job postings",
          "Hiring signals: who is recruiting for what.",
          "Search job postings (careers + boards): company, role, stack/tools, pains verbatim, date, evidence URL. Prefer scrape on careers hubs. Rank by hiring intensity × ICP fit; suggest outreach angles. Save sales/job-signals-*.csv.",
        ),
        card(
          "webHunt",
          "Deep web hunt",
          "Scrape + search corpora then build a sourced lead list.",
          "Deep public hunt for the ICP: plan queries, run web_search + scrape (and Exa/Firecrawl MCP if configured) on allowed public sites, extract companies/people with source URLs, dedupe by domain, write sales/prospects-*.csv, run score_leads.py --validate-only, then leads-report-*.html. No login walls. Pause on captcha/Cloudflare and ask the user.",
        ),
        card(
          "contactEnrich",
          "Find contact info",
          "Verified emails when Hunter/Apollo keys exist.",
          "Enrich leads with contact context. Run lead-enrichment/scripts/enrich_leads.py --verify-existing when HUNTER_API_KEY or APOLLO_API_KEY is set; only email_status=verified from API proof. Else public patterns with unverified. Update CSV + DQ pass + score_leads.py.",
        ),
      ],
    },
    {
      id: "qualify",
      label: tx("studio.groups.qualify", "Qualify"),
      cards: [
        card(
          "icpBuilder",
          "Define ICP",
          "Ideal customer profile with disqualifiers.",
          "Build a precise ICP: firmographics, buying committee, pains/triggers, disqualifiers, 10 example matching companies (sourced). Base on best customers when provided. Save marketing/personas.md or sales/icp-*.md; flag assumptions.",
        ),
        card(
          "leadScoring",
          "Score & enrich leads",
          "Fit + signal scoring on your lead list.",
          "Score every lead with explicit BANT-F/ICP grid (0-100), enrich missing public fields, run lead-qualification/scripts/score_leads.py, apply data-quality-agent. Ranked CSV with tier actions (contact now / nurture / discard). No invented verified emails.",
        ),
        card(
          "accountDeepDive",
          "Account deep-dive",
          "Full research sheet on one target account.",
          "Complete account sheet: model, size, org/people, news, tech hints, pains, competitors, 3 talking angles. Cite URL per fact; mark hypotheses. Save sales/accounts/<company>/research.md.",
        ),
        card(
          "signalScan",
          "Buying signals scan",
          "Funding, hiring, tech moves - who to contact now.",
          "Scan accounts for buying signals with evidence URL+date: funding, hiring, leadership, expansion, tech, regulation. Score strength × recency; top 5 contact-this-week with openers. Stale (>quarter) marked. Save sales/signals-*.csv.",
        ),
      ],
    },
    {
      id: "outreach",
      label: tx("studio.groups.outreach", "Outreach"),
      cards: [
        card(
          "coldEmails",
          "Cold email sequence",
          "Personalized multi-touch email cadence.",
          "Write a 4-5 touch cold email sequence: researched openers from signals, <120 words, one CTA, A/B subjects, timing. Human tone, zero spam. Prepare only - human sends. Save sales/outreach/email-sequence-*.md.",
        ),
        card(
          "linkedinScripts",
          "Social outreach scripts",
          "Connection notes and DM sequences that get replies.",
          "LinkedIn-style scripts: connection notes <300 chars, 3-message DM (context, value, ask), comment-first warm-up. Personalize per segment/signals; no fake familiarity. Save sales/outreach/social-*.md.",
        ),
        card(
          "callScript",
          "Call script & objections",
          "Discovery call plan with objection handling.",
          "Call plan: 30s opener, discovery questions mapped to pains, value narrative, objection table (acknowledge/explore/respond) for ~8 objections, voicemail. Tie to account research. Save sales/outreach/call-*.md.",
        ),
        card(
          "cadencePlan",
          "Follow-up cadence",
          "Full multi-channel sequence over 3 weeks.",
          "3-week multi-channel cadence (email/social/call): day-by-day, new angle each touch, stop conditions (reply/opt-out), breakup email. No auto-send. Save sales/outreach/cadence-*.md.",
        ),
      ],
    },
    {
      id: "pipeline",
      label: tx("studio.groups.pipeline", "Pipeline"),
      cards: [
        card(
          "crmExport",
          "CRM-ready export",
          "Clean CSV formatted for HubSpot/Salesforce import.",
          "Format leads as CRM-ready CSV (HubSpot/Salesforce mapping): company, domain, contact, role, email pattern, phone, country, source, score, signal, next action. Dedupe + score_leads validation + data-quality-agent. Prefer HubSpot MCP/crm-update-agent when configured; else workspace file + import mapping.",
        ),
        card(
          "meetingPrep",
          "Meeting prep",
          "Brief, questions and angles for a specific meeting.",
          "Meeting prep one-pager: account+attendees, 3 pain hypotheses with evidence, discovery questions, objections/responses, ideal next step. Facts vs hypotheses labeled. Save sales/accounts/<company>/meeting-prep.md.",
        ),
        card(
          "pipelineReview",
          "Pipeline review",
          "Health check and weighted forecast of your deals.",
          "Pipeline review from CRM export or sales/crm/: stage mix, aging/stuck, weighted forecast with assumptions, win-rate notes, top 5 actions. Cite evidence for at-risk deals; no invented amounts. Save sales/pipeline-review-*.md.",
        ),
        card(
          "competitorCustomers",
          "Competitor customers",
          "Who buys from competitors - displacement targets.",
          "Map competitor customers from public evidence only (case studies, logos, reviews): account, competitor, evidence URL, switch angle. Confidence per row; never invent customers. Save sales/displacement-*.csv.",
        ),
      ],
    },
  ];
}

// The ops actions live in `opsCatalog.ts`: they are shared with the "Ops"
// menu of the Dev workbench navbar so both surfaces stay identical.

const MODULE_META: Record<
  StudioModule,
  {
    command: string;
    icon: typeof FileText;
    titleKey: string;
    titleDefault: string;
    subtitleKey: string;
    subtitleDefault: string;
    tipKey: string;
    tipDefault: string;
    groups: (tx: Tx) => StudioGroup[];
  }
> = {
  risklens: {
    command: "/risklens",
    icon: ShieldAlert,
    titleKey: "studio.risklens.title",
    titleDefault: "RiskLens studio",
    subtitleKey: "studio.risklens.subtitle",
    subtitleDefault:
      "Assume the plan already failed in 6 months. Expose blind spots and revise before you build.",
    tipKey: "studio.risklens.tip",
    tipDefault:
      "Click a card to fill the chat, complete the plan details there, then send.",
    groups: risklensGroups,
  },
  scraping: {
    command: "/scrape",
    icon: Globe2,
    titleKey: "studio.scraping.title",
    titleDefault: "Scraping studio",
    subtitleKey: "studio.scraping.subtitle",
    subtitleDefault:
      "Crawl, clean, enrich and export web data to CSV, JSON, XML, Excel or reports.",
    tipKey: "studio.scraping.tip",
    tipDefault:
      "Click a card, fill URL(s) / corpus in the chat (no brackets left), then send.",
    groups: scrapingGroups,
  },
  content: {
    command: "/studio",
    icon: FileText,
    titleKey: "studio.content.title",
    titleDefault: "Document studio",
    subtitleKey: "studio.content.subtitle",
    subtitleDefault:
      "Generate polished PowerPoint, Word, PDF and Excel documents from templates.",
    tipKey: "studio.content.tip",
    tipDefault:
      "Choose a document type, pick a design template, set the language, then generate.",
    groups: contentGroups,
  },
  marketing: {
    command: "/campaign",
    icon: Megaphone,
    titleKey: "studio.marketing.title",
    titleDefault: "Marketing studio",
    subtitleKey: "studio.marketing.subtitle",
    subtitleDefault:
      "Campaigns, ad videos, product images, social content - produced end to end.",
    tipKey: "studio.marketing.tip",
    tipDefault:
      "Click a card, fill Product / Audience / Goal in the chat (no brackets left), then send.",
    groups: marketingGroups,
  },
  montage: {
    command: "/montage",
    icon: Clapperboard,
    titleKey: "studio.montage.title",
    titleDefault: "Montage studio",
    subtitleKey: "studio.montage.subtitle",
    subtitleDefault:
      "Live product demos, platform video exports, AI creatives and calendars - end to end.",
    tipKey: "studio.montage.tip",
    tipDefault:
      "Start with Full montage. Navin Plus+ recommended for AI media; or configure your own OpenRouter keys under Settings → Providers.",
    groups: montageGroups,
  },
  ads: {
    command: "/ads",
    icon: BadgeDollarSign,
    titleKey: "studio.ads.title",
    titleDefault: "Ads studio",
    subtitleKey: "studio.ads.subtitle",
    subtitleDefault:
      "Paid media desk: real analysis of your exports plus Google, Microsoft, Meta, TikTok, Reddit and LinkedIn Ads via MCP.",
    tipKey: "studio.ads.tip",
    tipDefault:
      "Start with Audit from exports (attach your Ads Manager CSV/XLSX) or connect the platform MCP under Settings → MCP, then click a card.",
    groups: adsGroups,
  },
  seo: {
    command: "/seo",
    icon: TrendingUp,
    titleKey: "studio.seo.title",
    titleDefault: "SEO studio",
    subtitleKey: "studio.seo.subtitle",
    subtitleDefault: "Audits, keyword research, optimized content and link strategy.",
    tipKey: "studio.seo.tip",
    tipDefault:
      "Click a card, fill site / topic / URL in the chat (no brackets left), then send.",
    groups: seoGroups,
  },
  leads: {
    command: "/leads",
    icon: Target,
    titleKey: "studio.leads.title",
    titleDefault: "Leads & sales studio",
    subtitleKey: "studio.leads.subtitle",
    subtitleDefault:
      "Find companies, people and jobs; qualify, enrich, and turn them into pipeline.",
    tipKey: "studio.leads.tip",
    tipDefault:
      "Click a card, fill ICP / company / list in the chat (no brackets left), then send.",
    groups: leadsGroups,
  },
  meeting: {
    command: "/meeting",
    icon: Mic,
    titleKey: "studio.meeting.title",
    titleDefault: "Meeting studio",
    subtitleKey: "studio.meeting.subtitle",
    subtitleDefault:
      "Capture, transcribe with your STT provider, and turn meetings into minutes and follow-ups.",
    tipKey: "studio.meeting.tip",
    tipDefault:
      "Prefer the Meeting desk for record/import. Or click a card, paste a transcript in chat, then send.",
    groups: meetingGroups,
  },
  ops: {
    command: "/ops",
    icon: ServerCog,
    titleKey: "studio.ops.title",
    titleDefault: "Ops studio",
    subtitleKey: "studio.ops.subtitle",
    subtitleDefault:
      "DevOps, SysOps and infra end to end: Kubernetes, CI/CD, GitOps, cloud, servers, incidents.",
    tipKey: "studio.ops.tip",
    tipDefault: "Pick an ops action to seed the chat, complete the details, then send.",
    groups: opsGroups,
  },
};

function formatIcon(format?: DocFormat) {
  if (format === "xls") return FileSpreadsheet;
  if (format === "ppt") return Presentation;
  return FileText;
}

/** Modules that seed the chat composer on card click (no preparation dialog). */
const CHAT_SEED_MODULES: ReadonlySet<StudioModule> = new Set([
  "ops",
  "risklens",
  "scraping",
  "marketing",
  "montage",
  "ads",
  "seo",
  "leads",
  "meeting",
]);

function studioSeedText(command: string, card: StudioCard): string {
  return `${command} ${card.prompt}\n\n`;
}

/** RiskLens seed: plan block first so the user fills it before sending. */
function risklensSeedText(command: string, card: StudioCard): string {
  return (
    `${command}\n\n` +
    `## Plan to analyze (replace the brackets - required before send)\n` +
    `- What: [what you are building, launching, hiring, or deciding]\n` +
    `- Who: [primary buyer, user, team, or affected party]\n` +
    `- Success in 6 months: [what "it worked" looks like]\n` +
    `- Extra context: [constraints, budget, deadline, links - optional]\n\n` +
    `## Instructions\n` +
    `${card.prompt}\n\n` +
    `If any of What / Who / Success still has brackets, stop and ask only for the missing fields.\n\n`
  );
}

const SCRAPE_COLLECT_IDS = new Set([
  "scrapePage",
  "scrapeSite",
  "scrapeSitemap",
  "scrapeJsPage",
]);

/** Scraping seed: target block first so the user fills URLs / corpus before send. */
function scrapingSeedText(command: string, card: StudioCard): string {
  const target = SCRAPE_COLLECT_IDS.has(card.id)
    ? card.id === "scrapeSitemap"
      ? (
          `## Target (replace the brackets - required before send)\n` +
          `- Sitemap URL or URL list: [https://example.com/sitemap.xml or one URL per line]\n` +
          `- Limits: [max pages / concurrency - optional]\n` +
          `- Notes: [auth, selectors, language - optional]\n\n`
        )
      : card.id === "scrapeSite"
        ? (
            `## Target (replace the brackets - required before send)\n` +
            `- Seed URL: [https://example.com]\n` +
            `- Limits: [max_depth / max_pages - optional, sensible defaults ok]\n` +
            `- Notes: [same-domain only, paths to skip - optional]\n\n`
          )
        : (
            `## Target (replace the brackets - required before send)\n` +
            `- URL(s): [https://example.com/page]\n` +
            `- Notes: [JS required, login, selectors - optional]\n\n`
          )
    : (
        `## Target (replace the brackets - required before send)\n` +
        `- Corpus: [path under scrape/ or "last scrape in this chat"]\n` +
        `- Notes: [columns to keep, filters - optional]\n\n`
      );
  return (
    `${command}\n\n` +
    target +
    `## Instructions\n` +
    `${card.prompt}\n\n` +
    `If required target fields still have brackets, stop and ask only for the missing URLs or corpus path.\n\n`
  );
}

/** Marketing seed: brief block first so the user fills product / audience before send. */
function marketingSeedText(command: string, card: StudioCard): string {
  return (
    `${command}\n\n` +
    `## Brief (replace the brackets - required before send)\n` +
    `- Product / offer: [what you sell or promote]\n` +
    `- Audience: [who it is for]\n` +
    `- Goal: [launch, awareness, leads, sales, retention]\n` +
    `- Brand / tone: [optional]\n` +
    `- Channels / constraints: [platforms, languages, deadline - optional]\n` +
    `- Links / assets: [site, deck, existing creatives - optional]\n\n` +
    `## Studio\n` +
    `This is Marketing (product / brand), not a montage-only edit. ` +
    `For image, video, voice, music and clips use generate_image / generate_video / ` +
    `generate_music / generate_speech and montage assemble/package when needed.\n\n` +
    `## Instructions\n` +
    `${card.prompt}\n\n` +
    `If Product / offer, Audience, or Goal still has brackets, stop and ask only for the missing fields.\n\n`
  );
}

/** Montage seed: product URL + access path (Plus vs BYOK) before send. */
function montageSeedText(command: string, card: StudioCard): string {
  const accessGuide = card.id === "montageAccessGuide";
  const needsDemoPath =
    card.id === "montagePackageProfiles" || card.id === "montageHyperframes";
  const target = accessGuide
    ? (
        `## Access path (replace the brackets - required before send)\n` +
        `- Choice: [Navin Plus+ managed | BYOK own OpenRouter keys | ffmpeg-only package]\n` +
        `- If BYOK: [confirm keys set under Settings → Providers]\n` +
        `- Language: [fr or en]\n\n`
      )
    : needsDemoPath
      ? (
          `## Target (replace the brackets - required before send)\n` +
          `- Product / URL to demo: [app URL or local preview]\n` +
          `- Demo file (if already recorded): [path under marketing/montage/demos/ or "record now"]\n` +
          `- Profiles: [default | all | list of ids]\n` +
          `- Access: [Navin Plus+ | BYOK OpenRouter - required for AI image/video/music]\n` +
          `- Notes: [CTA, captions language - optional]\n\n`
        )
      : (
          `## Target (replace the brackets - required before send)\n` +
          `- Product / URL to demo: [app URL or local preview]\n` +
          `- Audience / goal: [who + launch / demo / ads]\n` +
          `- Access: [Navin Plus+ recommended | BYOK OpenRouter keys under Settings → Providers]\n` +
          `- Happy path steps: [optional checklist]\n` +
          `- Notes: [login test account, brand tone - optional]\n\n`
        );
  return (
    `${command}\n\n` +
    target +
    `## Access reminder\n` +
    `- Managed AI media (image / video / music / STT): Navin Plus or higher.\n` +
    `- Or BYOK: configure OpenRouter (or other) under Settings → Providers, then Image / Video / Music / Voice.\n` +
    `- ffmpeg packaging of an existing demo works without AI spend.\n\n` +
    `## Instructions\n` +
    `${card.prompt}\n\n` +
    `If required fields still have brackets, stop and ask only for the missing ones. ` +
    `Never auto-publish. Prefer real UI footage over invented screens.\n\n`
  );
}

const SEO_URL_IDS = new Set([
  "techAudit",
  "onPageAudit",
  "seoCompetitors",
  "contentAudit",
  "metaTags",
  "schemaMarkup",
  "linkStrategy",
]);

/** SEO seed: site / topic block first so the user fills targets before send. */
function seoSeedText(command: string, card: StudioCard): string {
  const target = SEO_URL_IDS.has(card.id)
    ? (
        `## Target (replace the brackets - required before send)\n` +
        `- Site / URL(s): [https://example.com]\n` +
        `- Target query / market: [optional but recommended]\n` +
        `- Competitors: [optional URLs or names]\n` +
        `- Locale / language: [optional]\n` +
        `- Notes: [CMS, constraints - optional]\n\n`
      )
    : (
        `## Target (replace the brackets - required before send)\n` +
        `- Topic / business: [what you want to rank for]\n` +
        `- Site / URL: [https://example.com - optional]\n` +
        `- Market / locale: [country, language]\n` +
        `- Competitors: [optional]\n` +
        `- Notes: [constraints - optional]\n\n`
      );
  return (
    `${command}\n\n` +
    target +
    `## Instructions\n` +
    `${card.prompt}\n\n` +
    `If required target fields still have brackets, stop and ask only for the missing site, topic, or URL.\n\n`
  );
}

/** Leads seed: ICP / list block first so the user fills criteria before send. */
function leadsSeedText(command: string, card: StudioCard): string {
  const needsList = card.id === "leadScoring" || card.id === "contactEnrich";
  const needsAccount = card.id === "accountDeepDive";
  const target = needsAccount
    ? (
        `## Target (replace the brackets - required before send)\n` +
        `- Company: [name + website]\n` +
        `- Your offer: [what you sell them]\n` +
        `- Decision-maker roles: [optional]\n` +
        `- Notes: [known contacts, prior context - optional]\n\n`
      )
    : needsList
      ? (
          `## Target (replace the brackets - required before send)\n` +
          `- Lead list: [path in workspace, pasted rows, or "last leads file in this chat"]\n` +
          `- ICP reminder: [optional]\n` +
          `- Notes: [fields to enrich / score weights - optional]\n\n`
        )
      : (
          `## Target (replace the brackets - required before send)\n` +
          `- ICP: [sector, size, geography, maturity]\n` +
          `- Buyer roles: [who to reach]\n` +
          `- Offer / angle: [what you sell and why now]\n` +
          `- Geography / language: [optional]\n` +
          `- Exclusions: [competitors, existing customers - optional]\n` +
          `- Notes: [signals, tools, volume target - optional]\n\n`
        );
  return (
    `${command}\n\n` +
    target +
    `## Instructions\n` +
    `${card.prompt}\n\n` +
    `If required target fields still have brackets, stop and ask only for the missing ICP, list, or company.\n\n`
  );
}

export function StudioWorkspace({
  module,
  chatOpen,
  onToggleChat,
  onRun,
  onSeed,
  projectPath,
  projectName,
  recentProjects,
  onSelectProject,
}: {
  module: StudioModule;
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onRun?: (
    text: string,
    documentTemplate?: { category: string; name: string; title?: string },
  ) => void;
  /** Puts text in the chat composer without sending it (ops, risklens, scraping). */
  onSeed?: (
    text: string,
    files?: ProjectFileMatch[],
    options?: {
      replace?: boolean;
      mediaTemplate?: {
        id: string;
        title?: string;
        kind?: string;
        format?: string;
        family?: string;
      };
      mediaTemplates?: {
        id: string;
        title?: string;
        kind?: string;
        format?: string;
        family?: string;
      }[];
      localFiles?: File[];
    },
  ) => void;
  projectPath?: string | null;
  projectName?: string | null;
  recentProjects?: RecentProjectEntry[];
  onSelectProject?: (path: string, name?: string) => void;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const tx = useCallback<Tx>(
    (key, fallback) => t(key, { defaultValue: fallback }),
    [t],
  );
  const meta = MODULE_META[module];
  const groups = useMemo(() => meta.groups(tx), [meta, tx]);
  const [brief, setBrief] = useState("");
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [docTheme, setDocTheme] = useState<string | null>(null);
  const [marketingPane, setMarketingPane] = useState<"templates" | "actions" | "qa">(
    "templates",
  );
  const [showOnboarding, setShowOnboarding] = useState(
    () => !readDismissedOnboarding().has(module),
  );

  useEffect(() => {
    setShowOnboarding(!readDismissedOnboarding().has(module));
  }, [module]);

  const dismissOnboarding = useCallback(() => {
    const dismissed = readDismissedOnboarding();
    dismissed.add(module);
    writeDismissedOnboarding(dismissed);
    setShowOnboarding(false);
  }, [module]);

  const visibleGroups = useMemo(
    () => (activeGroup ? groups.filter((g) => g.id === activeGroup) : groups),
    [groups, activeGroup],
  );

  // Document design templates (HTML library), used by the content module to
  // let the user pick the visual base for each generated document.
  const [designTemplates, setDesignTemplates] = useState<DocumentTemplateInfo[]>([]);
  useEffect(() => {
    if (module !== "content" || !token) return undefined;
    let cancelled = false;
    fetchDocumentTemplates(token)
      .then((payload) => {
        if (cancelled || !Array.isArray(payload?.categories)) return;
        setDesignTemplates(
          payload.categories.flatMap((category) =>
            (category.templates ?? []).map((template) =>
              template.preview_url
                ? {
                    ...template,
                    preview_url: `${template.preview_url}?token=${encodeURIComponent(token)}`,
                  }
                : template,
            ),
          ),
        );
      })
      .catch(() => {
        if (!cancelled) setDesignTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [module, token]);

  // Card click opens a preparation dialog (describe the request, pick a design
  // template) instead of firing the prompt immediately.
  const [dialogCard, setDialogCard] = useState<StudioCard | null>(null);
  const [details, setDetails] = useState("");
  const [dialogTemplate, setDialogTemplate] = useState<DocumentTemplateInfo | null>(null);
  const [dialogPreviewTemplate, setDialogPreviewTemplate] =
    useState<DocumentTemplateInfo | null>(null);
  const [documentLanguage, setDocumentLanguage] = useState("");
  const [legalOutputFormat, setLegalOutputFormat] = useState<"DOCX" | "PDF">("DOCX");
  const [jurisdiction, setJurisdiction] = useState("");

  const dialogCategory = dialogCard?.legal
    ? "word"
    : dialogCard?.format
      ? FORMAT_TO_CATEGORY[dialogCard.format]
      : null;
  const dialogTemplates = useMemo(
    () =>
      dialogCategory
        ? designTemplates.filter((template) => template.category === dialogCategory)
        : [],
    [designTemplates, dialogCategory],
  );

  const openCard = useCallback(
    (card: StudioCard) => {
      // Chat-seed modules (Ops, RiskLens, Scraping) skip the preparation
      // dialog: the instruction goes straight into the chat composer, where
      // the user completes it and sends. The composer autosizes to the text.
      if (CHAT_SEED_MODULES.has(module) && onSeed) {
        const trimmedBrief = brief.trim();
        const seeded =
          module === "ops"
            ? opsSeedText(card)
            : module === "risklens"
              ? risklensSeedText(meta.command, card)
              : module === "scraping"
                ? scrapingSeedText(meta.command, card)
                : module === "marketing"
                  ? marketingSeedText(meta.command, card)
                  : module === "montage"
                    ? montageSeedText(meta.command, card)
                    : module === "seo"
                      ? seoSeedText(meta.command, card)
                      : module === "leads"
                        ? leadsSeedText(meta.command, card)
                        : studioSeedText(meta.command, card);
        onSeed(
          trimmedBrief
            ? `${seeded}Brief from the user: ${trimmedBrief}\n\n`
            : seeded,
        );
        return;
      }
      setDialogCard(card);
      setDetails("");
      setDialogTemplate(null);
      setDialogPreviewTemplate(null);
      setDocumentLanguage("");
      setLegalOutputFormat("DOCX");
      setJurisdiction("");
    },
    [module, onSeed, brief, meta.command],
  );

  useEffect(() => {
    if (!dialogCard) return;
    if (dialogCard.templateName) {
      const contractualTemplate = designTemplates.find(
        (template) =>
          template.category === "word" && template.name === dialogCard.templateName,
      );
      if (contractualTemplate) {
        setDialogTemplate(contractualTemplate);
        return;
      }
    }
    if (dialogCard.recommendedTemplate && dialogCategory) {
      const recommended = designTemplates.find(
        (template) =>
          template.category === dialogCategory
          && template.name === dialogCard.recommendedTemplate,
      );
      if (recommended) setDialogTemplate(recommended);
    }
  }, [dialogCard, designTemplates, dialogCategory]);

  const languageRequired = module === "content";
  const designTemplateRequired =
    module === "content"
    && Boolean(dialogCard?.format === "ppt")
    && dialogTemplates.length > 0;
  const canGenerate =
    Boolean(onRun && dialogCard) &&
    (!languageRequired || Boolean(documentLanguage.trim())) &&
    (!dialogCard?.legal || Boolean(jurisdiction.trim())) &&
    (!designTemplateRequired || Boolean(dialogTemplate));

  const confirmRun = useCallback(() => {
    if (!onRun || !dialogCard || !canGenerate) return;
    const trimmedBrief = brief.trim();
    const trimmedDetails = details.trim();
    const exactLanguage = documentLanguage.trim();
    const normalizedLanguage = exactLanguage.toLocaleLowerCase();
    const isArabic = ["arabic", "arabe", "العربية", "عربي"].some((name) =>
      normalizedLanguage.includes(name),
    );
    const parts = [`${meta.command} ${dialogCard.prompt}`];
    if (module === "content") {
      parts.push(
        `Write the entire document in exactly this language: ${exactLanguage}. Follow the language's local legal, typographic, date, number, naming and drafting conventions. Writing direction: ${isArabic ? "right-to-left (RTL) is mandatory throughout, including paragraphs, tables and page layout" : "use the selected language's native writing direction"}.`,
      );
      parts.push(
        `Required output format: ${dialogCard.legal ? legalOutputFormat : FORMAT_META[dialogCard.format ?? "doc"].label}. Deliver the final file in that format.`,
      );
    }
    if (dialogCard.legal) {
      parts.push(
        `Applicable law and competent jurisdiction: ${jurisdiction.trim()}. Adapt clauses and terminology to that jurisdiction without inventing facts.`,
        "Important: this generated contract is a drafting aid and must be reviewed by a qualified legal professional before signature or use.",
      );
    }
    if (module === "content" && docTheme && !dialogTemplate) {
      parts.push(
        `Apply the "${docTheme}" visual theme exactly as specified in the document-templates skill.`,
      );
    }
    if (module === "content" && dialogTemplate) {
      parts.push(
        "Mandatory visual base: use only the attached Document Template. Keep its CSS, fonts, colors and layouts. Replace text, data and images. Do not invent another design system.",
      );
    }
    if (trimmedDetails) {
      parts.push(`User request details: ${trimmedDetails}`);
    }
    if (trimmedBrief) {
      parts.push(`Brief from the user: ${trimmedBrief}`);
    }
    onRun(
      parts.join("\n\n"),
      dialogTemplate
        ? {
            category: dialogTemplate.category,
            name: dialogTemplate.name,
            title: dialogTemplate.title,
          }
        : undefined,
    );
    setDialogCard(null);
  }, [
    onRun,
    dialogCard,
    canGenerate,
    brief,
    details,
    documentLanguage,
    meta.command,
    module,
    legalOutputFormat,
    jurisdiction,
    docTheme,
    dialogTemplate,
  ]);

  const ModuleIcon = meta.icon;
  const showMarketingLibrary =
    module === "marketing" && marketingPane === "templates";
  const showMarketingQA = module === "marketing" && marketingPane === "qa";
  const showStudioActions = module !== "marketing" || marketingPane === "actions";

  const seedMediaTemplate = useCallback(
    (items: MediaTemplateItem[]) => {
      if (items.length === 0) return;
      onSeed?.(mediaTemplateSeedText("marketing", items), undefined, {
        replace: true,
        mediaTemplates: items.map((item) => ({
          id: item.id,
          title: item.title,
          kind: item.kind,
          format: item.format,
          family: item.family,
        })),
      });
    },
    [onSeed],
  );

  // Marketing carries the same violet identity as its media library so the
  // studio never reads as a generic grey clone of the other modules.
  const isMarketing = module === "marketing";
  const activePillClass = isMarketing
    ? "border-violet-500/60 bg-violet-600 text-white"
    : "border-foreground/60 bg-foreground text-background";

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      {/* Header: slim navbar, same style and height as the dev tab bar. */}
      <div
        className={cn(
          "grid h-11 shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-2 border-b border-border/55 bg-muted/15 px-3",
          // Chat closed: this bar reaches the window edge under the fixed
          // notification bell. Reserve the same gutter DevWorkbench uses.
          !chatOpen && NOTIFICATION_GUTTER,
        )}
      >
        <div className="flex min-w-0 items-center gap-1.5">
          <ModuleIcon
            className={cn(
              "h-3.5 w-3.5 shrink-0",
              isMarketing ? "text-violet-600 dark:text-violet-400" : "text-foreground",
            )}
            aria-hidden
          />
          <h1 className="truncate text-[13px] font-semibold text-foreground">
            {tx(meta.titleKey, meta.titleDefault)}
          </h1>
        </div>
        {module === "marketing" ? (
          <nav className="flex items-center gap-0.5">
            {(
              [
                {
                  id: "templates" as const,
                  label: tx("studio.mediaLibrary.pane.templates", "Templates"),
                  icon: <LayoutTemplate className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />,
                },
                {
                  id: "actions" as const,
                  label: tx("studio.mediaLibrary.pane.actions", "Actions"),
                  icon: <Play className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />,
                },
                {
                  id: "qa" as const,
                  label: tx("studio.marketingQA.pane", "Visual QA"),
                  icon: <FluentIcon iconName="ComplianceAudit" aria-hidden />,
                },
              ]
            ).map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setMarketingPane(item.id)}
                className={cn(
                  "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium transition-colors active:scale-[0.96]",
                  marketingPane === item.id
                    ? "bg-violet-600 text-white"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </nav>
        ) : (
          <span />
        )}
        {onToggleChat || onSelectProject ? (
          <div className="flex min-w-0 items-center justify-end gap-1">
            {onToggleChat ? (
              <button
                type="button"
                aria-label={chatOpen ? tx("studio.hideChat", "Hide chat") : tx("studio.chat", "Chat")}
                title={chatOpen ? tx("studio.hideChat", "Hide chat") : tx("studio.chat", "Chat")}
                aria-pressed={Boolean(chatOpen)}
                onClick={onToggleChat}
                data-testid="studio-toggle-chat"
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
              >
                <FluentIcon iconName={chatOpen ? "ChatSolid" : "Chat"} aria-hidden />
              </button>
            ) : null}
            {onSelectProject ? (
              <DevProjectSelector
                projectPath={projectPath ?? null}
                projectName={projectName}
                recentProjects={recentProjects ?? []}
                onSelectProject={onSelectProject}
              />
            ) : null}
          </div>
        ) : (
          <span />
        )}
      </div>

      {showMarketingLibrary ? (
        <MediaTemplateLibrary
          studio="marketing"
          onUse={seedMediaTemplate}
          onWorkflow={(text, items) =>
            onSeed?.(text, undefined, {
              replace: true,
              mediaTemplates: items.map((item) => ({
                id: item.id,
                title: item.title,
                kind: item.kind,
                format: item.format,
                family: item.family,
              })),
            })
          }
          onLocalFiles={(files) =>
            onSeed?.("", undefined, { replace: false, localFiles: files })
          }
          onSeed={(text, options) =>
            onSeed?.(text, undefined, { replace: options?.replace ?? false })
          }
        />
      ) : null}

      {showMarketingQA ? <MarketingQA projectPath={projectPath} /> : null}

      {showStudioActions && showOnboarding ? (
        <div className="shrink-0 border-b border-border/55 bg-muted/20 px-5 py-3">
          <div className="flex items-start gap-3 rounded-xl border border-border/60 bg-background/80 px-3.5 py-3">
            <Info
              className="mt-0.5 h-4 w-4 shrink-0 text-foreground/70"
              aria-hidden
            />
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-[13px] font-medium text-foreground">
                {tx(meta.titleKey, meta.titleDefault)}
              </p>
              <p className="text-[12.5px] leading-relaxed text-muted-foreground">
                {tx(meta.subtitleKey, meta.subtitleDefault)}
              </p>
              <p className="text-[12.5px] leading-relaxed text-foreground/80">
                {tx(meta.tipKey, meta.tipDefault)}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={dismissOnboarding}
                className="h-7 rounded-lg px-2.5 text-[12px] text-muted-foreground hover:text-foreground"
              >
                {tx("studio.onboarding.gotIt", "Got it")}
              </Button>
              <button
                type="button"
                onClick={dismissOnboarding}
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label={tx("studio.onboarding.dismiss", "Dismiss")}
                title={tx("studio.onboarding.dismiss", "Dismiss")}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Brief (dialog-driven modules) + theme / group filter */}
      {showStudioActions ? (
      <>
      <div className="flex shrink-0 flex-col gap-2.5 border-b border-border/55 px-5 py-3">
        {!CHAT_SEED_MODULES.has(module) ? (
          <input
            type="text"
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
            placeholder={tx(
              "studio.briefPlaceholder",
              "Optional brief: product, audience, goal, tone… (attached to every action)",
            )}
            className="w-full rounded-lg border border-border/60 bg-muted/25 px-3 py-2 text-[13px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-primary/50"
          />
        ) : null}
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setActiveGroup(null)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors",
              activeGroup === null
                ? activePillClass
                : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {tx("studio.allGroups", "All")}
          </button>
          {groups.map((group) => (
            <button
              key={group.id}
              type="button"
              onClick={() =>
                setActiveGroup((current) => (current === group.id ? null : group.id))
              }
              className={cn(
                "rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors",
                activeGroup === group.id
                  ? activePillClass
                  : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {group.label}
            </button>
          ))}
        </div>
        {module === "content" ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11.5px] font-medium text-muted-foreground">
              {tx("studio.themePicker.label", "Theme:")}
            </span>
            <button
              type="button"
              onClick={() => setDocTheme(null)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors",
                docTheme === null
                  ? "border-foreground/60 bg-foreground text-background"
                  : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {tx("studio.themePicker.auto", "Auto")}
            </button>
            {DOC_THEMES.map((theme) => (
              <button
                key={theme.id}
                type="button"
                onClick={() =>
                  setDocTheme((current) => (current === theme.id ? null : theme.id))
                }
                title={tx(`studio.themePicker.names.${theme.id}`, theme.id)}
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors",
                  docTheme === theme.id
                    ? "border-foreground/60 bg-foreground text-background"
                    : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <span className="flex items-center gap-0.5">
                  {theme.dots.map((dot, index) => (
                    <span
                      key={index}
                      className="h-2 w-2 rounded-full border border-border/60"
                      style={{ backgroundColor: dot }}
                      aria-hidden
                    />
                  ))}
                </span>
                {tx(`studio.themePicker.names.${theme.id}`, theme.id)}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {/* Grid */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="flex flex-col gap-6">
          {visibleGroups.map((group) => (
            <section key={group.id}>
              <h2 className="mb-2.5 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                {isMarketing ? (
                  <span className="h-1.5 w-1.5 rounded-full bg-violet-500" aria-hidden />
                ) : null}
                {group.label}
              </h2>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
                {group.cards.map((card) => {
                  const CardIcon = card.format ? formatIcon(card.format) : ModuleIcon;
                  return (
                    <button
                      key={card.id}
                      type="button"
                      onClick={() => openCard(card)}
                      className={cn(
                        "group flex flex-col gap-1.5 rounded-xl border border-border/60 bg-background p-3.5 text-left transition-colors hover:bg-muted/30",
                        isMarketing
                          ? "hover:border-violet-500/60"
                          : "hover:border-foreground/40",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <CardIcon
                          className={cn(
                            "h-4 w-4 shrink-0",
                            isMarketing
                              ? "text-violet-600/80 dark:text-violet-400/80"
                              : "text-muted-foreground",
                          )}
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-foreground">
                          {card.label}
                        </span>
                        {card.format ? (
                          <span
                            className={cn(
                              "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold",
                              FORMAT_META[card.format].className,
                            )}
                          >
                            {FORMAT_META[card.format].label}
                          </span>
                        ) : (
                          <ArrowRight
                            className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
                            aria-hidden
                          />
                        )}
                      </div>
                      <p className="line-clamp-2 text-[12px] leading-snug text-muted-foreground">
                        {card.description}
                      </p>
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
        <p className="mt-6 pb-2 text-center text-[11.5px] text-muted-foreground/70">
          {CHAT_SEED_MODULES.has(module)
            ? tx(
                "studio.footerHintChatSeed",
                "Each card fills the chat - complete the message, then send. Files are saved to your workspace.",
              )
            : tx(
                "studio.footerHint",
                "Every action runs the agent in the chat panel - files are saved to your workspace.",
              )}{" "}
          <span className="font-mono">{meta.command}</span>
        </p>
      </div>
      </>
      ) : null}

      {/* Preparation dialog: describe the request + pick a design template */}
      <Dialog
        open={dialogCard !== null}
        onOpenChange={(open) => {
          if (!open) setDialogCard(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          {dialogCard ? (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  {(() => {
                    const HeaderIcon = formatIcon(dialogCard.format);
                    return (
                      <HeaderIcon className="h-4 w-4 text-muted-foreground" aria-hidden />
                    );
                  })()}
                  <span className="min-w-0 truncate">{dialogCard.label}</span>
                  {dialogCard.format ? (
                    <span
                      className={cn(
                        "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold",
                        FORMAT_META[dialogCard.format].className,
                      )}
                    >
                      {FORMAT_META[dialogCard.format].label}
                    </span>
                  ) : null}
                </DialogTitle>
                <DialogDescription>{dialogCard.description}</DialogDescription>
              </DialogHeader>

              <div className="flex flex-col gap-4">
                {module === "content" ? (
                  <div className="flex flex-col gap-1.5">
                    <label
                      htmlFor="studio-dialog-language"
                      className="text-[12.5px] font-medium text-foreground"
                    >
                      {tx("studio.dialog.languageLabel", "Document language")} *
                    </label>
                    <input
                      id="studio-dialog-language"
                      list="studio-language-suggestions"
                      value={documentLanguage}
                      onChange={(event) => setDocumentLanguage(event.target.value)}
                      placeholder={tx(
                        "studio.dialog.languagePlaceholder",
                        "French, English, Arabic, or another language",
                      )}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-[13px] text-foreground outline-none focus:border-primary/50"
                      required
                    />
                    <datalist id="studio-language-suggestions">
                      <option value={tx("studio.dialog.languages.french", "French")} />
                      <option value={tx("studio.dialog.languages.english", "English")} />
                      <option value={tx("studio.dialog.languages.arabic", "Arabic")} />
                    </datalist>
                    <p className="text-[11px] text-muted-foreground">
                      {tx(
                        "studio.dialog.languageHint",
                        "Choose a suggestion or enter any language.",
                      )}
                    </p>
                  </div>
                ) : null}

                {dialogCard.legal ? (
                  <>
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[12.5px] font-medium text-foreground">
                        {tx("studio.dialog.outputFormatLabel", "Output format")} *
                      </span>
                      <div className="flex gap-2">
                        {(["DOCX", "PDF"] as const).map((format) => (
                          <button
                            key={format}
                            type="button"
                            onClick={() => setLegalOutputFormat(format)}
                            className={cn(
                              "rounded-md border px-3 py-1.5 text-[12px] font-medium",
                              legalOutputFormat === format
                                ? "border-primary bg-primary/5 text-foreground"
                                : "border-border/60 text-muted-foreground",
                            )}
                          >
                            {format}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label
                        htmlFor="studio-dialog-jurisdiction"
                        className="text-[12.5px] font-medium text-foreground"
                      >
                        {tx(
                          "studio.dialog.jurisdictionLabel",
                          "Applicable law and jurisdiction",
                        )}{" "}
                        *
                      </label>
                      <input
                        id="studio-dialog-jurisdiction"
                        value={jurisdiction}
                        onChange={(event) => setJurisdiction(event.target.value)}
                        placeholder={tx(
                          "studio.dialog.jurisdictionPlaceholder",
                          "e.g. French law, courts of Paris",
                        )}
                        className="w-full rounded-md border border-border bg-background px-3 py-2 text-[13px] text-foreground outline-none focus:border-primary/50"
                        required
                      />
                    </div>
                    <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11.5px] text-muted-foreground">
                      {tx(
                        "studio.dialog.legalReviewNote",
                        "Generated contracts must be reviewed by a qualified legal professional before use.",
                      )}
                    </p>
                  </>
                ) : null}

                <div className="flex flex-col gap-1.5">
                  <label
                    htmlFor="studio-dialog-details"
                    className="text-[12.5px] font-medium text-foreground"
                  >
                    {tx("studio.dialog.detailsLabel", "Describe what you want")}
                  </label>
                  <Textarea
                    id="studio-dialog-details"
                    value={details}
                    onChange={(event) => setDetails(event.target.value)}
                    autoFocus
                    rows={4}
                    placeholder={tx(
                      "studio.dialog.detailsPlaceholder",
                      "Subject, audience, key points, tone, language… The more context, the better the result.",
                    )}
                    className="min-h-[92px] resize-y text-[13px]"
                  />
                </div>

                {dialogCategory ? (
                  <div className="flex flex-col gap-1.5">
                    <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-foreground">
                      <LayoutTemplate className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                      {tx("studio.dialog.templateLabel", "Design template")}
                    </span>
                    {/* auto-rows-min keeps every row at its content height: a
                        stretched row can be squeezed by the max height, and the
                        cards clip their overflow, which turns the thumbnails
                        into thin strips. */}
                    <div className="grid max-h-[280px] auto-rows-min grid-cols-2 items-start gap-2 overflow-y-auto pr-1 sm:grid-cols-3">
                      {!designTemplateRequired ? (
                        <button
                          type="button"
                          onClick={() => setDialogTemplate(null)}
                          className={cn(
                            "flex flex-col items-center justify-center gap-1.5 rounded-lg border p-3 text-center transition-colors",
                            dialogTemplate === null
                              ? "border-primary bg-primary/5"
                              : "border-border/60 hover:border-foreground/40 hover:bg-muted/30",
                          )}
                          data-testid="studio-template-auto"
                        >
                          <Palette className="h-5 w-5 text-muted-foreground" aria-hidden />
                          <span className="text-[12px] font-medium text-foreground">
                            {tx("studio.dialog.templateAuto", "Auto")}
                          </span>
                          <span className="text-[10.5px] leading-tight text-muted-foreground">
                            {tx(
                              "studio.dialog.templateAutoHint",
                              "Theme colors only, no HTML template",
                            )}
                          </span>
                        </button>
                      ) : null}
                      {dialogTemplates.map((template) => {
                        const selected =
                          dialogTemplate?.category === template.category &&
                          dialogTemplate?.name === template.name;
                        return (
                          <div
                            key={`${template.category}/${template.name}`}
                            className={cn(
                              "group relative overflow-hidden rounded-lg border transition-colors",
                              selected
                                ? "border-primary bg-primary/5"
                                : "border-border/60 hover:border-foreground/40 hover:bg-muted/30",
                            )}
                          >
                            <button
                              type="button"
                              onClick={() =>
                                setDialogTemplate((current) => {
                                  const alreadySelected =
                                    current?.category === template.category
                                    && current?.name === template.name;
                                  if (alreadySelected) {
                                    return designTemplateRequired ? current : null;
                                  }
                                  return template;
                                })
                              }
                              className="flex w-full flex-col text-left"
                              data-testid={`studio-template-${template.name}`}
                            >
                              {template.preview_url ? (
                                // object-top is required: previews mix 16:9
                                // mockups with full A4 pages, and centering the
                                // crop of a portrait page hides its header.
                                <img
                                  src={template.preview_url}
                                  alt=""
                                  loading="lazy"
                                  decoding="async"
                                  className="h-28 w-full shrink-0 bg-muted object-cover object-top"
                                />
                              ) : (
                                <div className="flex h-28 w-full shrink-0 items-center justify-center bg-muted">
                                  <LayoutTemplate
                                    className="h-6 w-6 text-muted-foreground"
                                    aria-hidden
                                  />
                                </div>
                              )}
                              <span className="truncate px-2 py-1.5 text-[11.5px] font-medium text-foreground">
                                {template.title}
                              </span>
                            </button>
                            {selected ? (
                              <span className="pointer-events-none absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground">
                                <Check className="h-3 w-3" aria-hidden />
                              </span>
                            ) : null}
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setDialogPreviewTemplate(template);
                              }}
                              aria-label={tx(
                                "studio.dialog.templatePreview",
                                "Preview the full template",
                              )}
                              title={tx(
                                "studio.dialog.templatePreview",
                                "Preview the full template",
                              )}
                              className={cn(
                                "absolute left-1.5 top-1.5 grid h-6 w-6 place-items-center rounded-full",
                                "bg-black/55 text-white opacity-0 shadow backdrop-blur-sm transition-opacity",
                                "hover:bg-black/75 focus-visible:opacity-100 group-hover:opacity-100",
                              )}
                              data-testid={`studio-template-preview-${template.name}`}
                            >
                              <Eye className="h-3.5 w-3.5" aria-hidden />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      {designTemplateRequired
                        ? tx(
                            "studio.dialog.templateRequiredHint",
                            "A design template is required for PowerPoint. Pitch decks default to a recommended layout; pick another if you prefer.",
                          )
                        : tx(
                            "studio.dialog.templateHint",
                            "The document will follow the selected template's design.",
                          )}
                    </p>
                  </div>
                ) : null}
              </div>

              <DialogFooter>
                <Button variant="outline" onClick={() => setDialogCard(null)}>
                  {tx("studio.dialog.cancel", "Cancel")}
                </Button>
                <Button
                  onClick={confirmRun}
                  disabled={!canGenerate}
                  data-testid="studio-dialog-generate"
                >
                  {tx("studio.dialog.generate", "Generate")}
                </Button>
              </DialogFooter>
              {dialogPreviewTemplate ? (
                <DocumentTemplatePreviewDialog
                  template={dialogPreviewTemplate}
                  onClose={() => setDialogPreviewTemplate(null)}
                  onUse={(template) => {
                    setDialogTemplate(template);
                    setDialogPreviewTemplate(null);
                  }}
                />
              ) : null}
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
