import { useCallback, useMemo, useState } from "react";
import {
  ArrowRight,
  FileSpreadsheet,
  FileText,
  Megaphone,
  PanelRightClose,
  PanelRightOpen,
  Presentation,
  Target,
  TrendingUp,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { TeamOrgChart } from "@/components/studio/TeamOrgChart";

export type StudioModule = "content" | "marketing" | "seo" | "leads" | "team";

type DocFormat = "ppt" | "doc" | "pdf" | "xls";

type StudioCard = {
  id: string;
  format?: DocFormat;
  label: string;
  description: string;
  prompt: string;
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

/** Built-in visual themes — specs live in the `document-templates` skill. Dots preview the doc palette. */
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

function contentGroups(tx: Tx): StudioGroup[] {
  const card = (
    id: string,
    format: DocFormat,
    label: string,
    description: string,
    prompt: string,
  ): StudioCard => ({
    id,
    format,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
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
          "Create a project pitch deck (PowerPoint, 10-12 slides): vision, problem, solution, approach, timeline, team, risks, next steps. Strong visual hierarchy, one idea per slide.",
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
          "Create an investor pitch deck (PowerPoint, 12-15 slides): problem, solution, product, market size (TAM/SAM/SOM), business model, traction, competition, team, financial projections, funding ask and use of funds.",
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
          "Create an annual marketing plan deck (PowerPoint): objectives and KPIs, target segments, positioning, channel strategy, campaign calendar, budget allocation, measurement plan.",
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
          "Create a brand presentation deck (PowerPoint): brand story, mission and values, personality and voice, messaging pillars, visual identity guidelines, do's and don'ts.",
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
          "Create a product sales deck (PowerPoint): customer pain, value proposition, product demo flow, proof (cases, metrics), pricing overview, call to action.",
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
          "Create a training deck (PowerPoint): learning objectives, structured modules with one concept per slide, practical exercises, knowledge-check questions, summary and resources.",
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
          "Generate professional product images: studio packshot style, lifestyle scene, and social-ready variants. Use the image generation tools, save files to the workspace, and propose 3 visual directions first.",
        ),
        card(
          "adVideo",
          "Ad video",
          "Script + scenes + generated advertising video.",
          "Produce a complete advertising video: hook-driven script (15-30s), scene-by-scene storyboard with shot descriptions and on-screen text, voiceover lines, then generate the video with the available video generation tools and save it to the workspace.",
        ),
        card(
          "socialVisuals",
          "Social media visuals",
          "Branded image sets for each platform format.",
          "Create a set of social media visuals: square (feed), vertical (story/reel cover), and landscape (link post) variants with consistent branding. Generate the images and save them to the workspace.",
        ),
        card(
          "brandKit",
          "Brand kit",
          "Logo directions, palette, typography, voice.",
          "Build a brand kit: 3 logo concept directions (generate images), color palette with hex codes, typography pairing, tone of voice guide, and usage examples. Save everything to the workspace.",
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
          "Write a batch of social media posts adapted per platform (LinkedIn, X, Instagram, TikTok script): strong hooks, platform-native tone, hashtags, CTA, and a suggested posting time for each.",
        ),
        card(
          "blogArticle",
          "Blog article",
          "Long-form article optimized for engagement.",
          "Write a complete blog article: compelling title options, structured H2/H3 outline, engaging intro, actionable body with examples, conclusion with CTA, plus a meta description and 3 social snippets to promote it.",
        ),
        card(
          "emailSequence",
          "Email sequence",
          "5-email nurture or launch sequence.",
          "Write a 5-email marketing sequence (nurture or launch): subject line A/B variants, preview text, persuasive body copy with one clear CTA per email, and recommended send timing.",
        ),
        card(
          "landingCopy",
          "Landing page copy",
          "Conversion-focused page: hero, proof, CTA.",
          "Write conversion-optimized landing page copy: hero headline and subheadline variants, benefit blocks, social proof section, objection-handling FAQ, and CTA copy. Structure it section by section.",
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
          "Design and produce a complete 360° marketing campaign: key message and creative concept, channel plan, then generate every deliverable (ad copy, social posts, product visuals, video script, email, landing copy) and save assets to the workspace with a final summary table.",
        ),
        card(
          "persona",
          "Customer persona",
          "Data-backed persona with pains and channels.",
          "Build detailed customer personas: demographics, goals, pain points, objections, buying triggers, preferred channels, and messaging angles per persona. Research the market with web tools when useful.",
        ),
        card(
          "contentCalendar",
          "30-day content plan",
          "Full month of content mapped to goals.",
          "Create a 30-day content plan: themes per week, post per day with channel, format, hook and CTA, mapped to funnel stages. Deliver as a structured table and save it as a file.",
        ),
        card(
          "competitorScan",
          "Competitor analysis",
          "Positioning map and messaging gaps.",
          "Analyze competitors' marketing: positioning, messaging, channels, content strategy and offers. Fetch their public pages, build a comparison table, and recommend differentiation angles.",
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
          "Run a full technical SEO audit of the given site: fetch key pages, check indexability signals (robots, canonicals, sitemaps), meta and heading structure, structured data, internal linking, and obvious performance issues. Deliver findings ordered by impact with fixes.",
        ),
        card(
          "onPageAudit",
          "On-page audit (URL)",
          "Deep audit of a single page vs its target query.",
          "Audit one URL on-page: fetch the page, evaluate title/meta/H-structure, keyword targeting and intent match, content depth vs top competitors, internal links, and media optimization. Provide a prioritized fix list with rewritten elements.",
        ),
        card(
          "seoCompetitors",
          "Competitor analysis",
          "Compare rankings strategy, content and gaps.",
          "Analyze SEO competitors: fetch their key pages, compare content strategy, site structure, targeted keywords and strengths. Produce a gap analysis with concrete opportunities to outrank them.",
        ),
        card(
          "contentAudit",
          "Content audit",
          "Inventory, decay, cannibalization, refresh plan.",
          "Audit existing content: inventory the site's main pages, flag thin or outdated content, detect keyword cannibalization, and produce a keep/refresh/merge/delete action plan.",
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
          "Do keyword research for the topic/business: seed expansion, search intent classification (informational/commercial/transactional), estimated difficulty and value, grouped into clusters and prioritized in a table saved as a file.",
        ),
        card(
          "topicClusters",
          "Content plan & clusters",
          "Pillar pages and supporting cluster articles.",
          "Build a content plan with topic clusters: pillar pages, supporting articles per cluster, target keyword and intent for each, internal linking plan between them, and a publishing order by impact.",
        ),
        card(
          "questionsResearch",
          "Questions & intents",
          "People-also-ask style questions to target.",
          "Research the questions users ask about the topic (people-also-ask style, forums, related searches): list them by intent, map each to a content format (FAQ, article, tool), and highlight quick wins.",
        ),
        card(
          "localSeo",
          "Local SEO",
          "Local pack, profile, citations, local pages.",
          "Build a local SEO plan: business profile optimization checklist, local keyword targets, citations and reviews strategy, localized landing page structure, and tracking plan.",
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
          "Write a complete SEO-optimized article: title tag and meta description, H1-H3 structure covering the topic's entities, natural keyword usage, FAQ section, internal link suggestions, and schema.org FAQPage/Article markup. Save it as a file.",
        ),
        card(
          "metaTags",
          "Meta & tags optimization",
          "Rewritten titles, descriptions, OG tags.",
          "Optimize meta elements for the given pages: rewrite title tags (CTR-focused, right length), meta descriptions, Open Graph/Twitter tags, and heading hierarchy. Deliver before/after tables.",
        ),
        card(
          "schemaMarkup",
          "Schema.org markup",
          "Structured data ready to paste (JSON-LD).",
          "Generate schema.org structured data (JSON-LD) adapted to the site/pages: Organization, Product, Article, FAQ, Breadcrumb or LocalBusiness as relevant, valid and ready to paste, with placement instructions.",
        ),
        card(
          "linkStrategy",
          "Links & backlinks plan",
          "Internal mesh plan and backlink acquisition.",
          "Build a linking strategy: internal linking plan (money pages, anchors, mesh between clusters) and a backlink acquisition plan (targets, tactics, outreach templates), prioritized by effort/impact.",
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
          "Hunt companies matching the target profile: sweep directories, registries, award lists, competitor ecosystems and event exhibitor lists. Deliver a deduplicated CSV (company, website, size, sector, country, signal, source, confidence) saved to the workspace, then highlight the 5 best fits and why.",
        ),
        card(
          "peopleSearch",
          "Search people",
          "Decision-makers with role, profile and source.",
          "Find the decision-makers at the target companies (or for the target role): search public profiles, team/leadership pages, press quotes, conference bios and bylines. Deliver a table (person, role, company, profile URL, source, confidence) — public sources only, never invent contacts.",
        ),
        card(
          "jobSearch",
          "Search job postings",
          "Hiring signals: who is recruiting for what.",
          "Search job postings matching the target (role, sector, geography): careers pages and job boards. For each posting extract company, role, stack/tools mentioned, pains stated verbatim, and posting date. These are hot buying signals — rank companies by hiring intensity and relevance.",
        ),
        card(
          "contactEnrich",
          "Find contact info",
          "Email patterns and contact context, sourced.",
          "Enrich the given leads with contact context: infer email patterns from public sources (press contacts, legal pages, published addresses) with a confidence level, collect official phone/switchboard and social profiles. Mark everything unverified as such — never fabricate a verified address.",
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
          "Build a precise Ideal Customer Profile: firmographics (size, sector, geography, maturity), buying committee roles, pains and triggers, disqualifiers, and 10 example companies that match. Base it on our current best customers if data is available.",
        ),
        card(
          "leadScoring",
          "Score & enrich leads",
          "Fit + signal scoring on your lead list.",
          "Take the lead list and score every lead: ICP fit (0-100) plus signal strength, with the scoring grid made explicit. Enrich missing fields from public sources. Deliver the ranked table saved to the workspace with a recommended action per tier (contact now / nurture / discard).",
        ),
        card(
          "accountDeepDive",
          "Account deep-dive",
          "Full research sheet on one target account.",
          "Build a complete account sheet on the target company: business model, size and finances, org and key people, recent news, tech stack hints, active pains, competitors they use, and 3 concrete talking angles for a first meeting. Cite every source.",
        ),
        card(
          "signalScan",
          "Buying signals scan",
          "Funding, hiring, tech moves — who to contact now.",
          "Scan the target accounts for buying signals: funding rounds, hiring sprees, leadership changes, expansions, tech changes, regulation deadlines. Score signal × recency, deliver a ranked table with evidence URL, date and suggested angle, and flag the top 5 accounts to contact this week.",
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
          "Write a cold outreach email sequence (4-5 touches): personalized opener per lead segment using their signals, value-first body, one clear CTA, A/B subject lines, and timing between touches. Tone: human, specific, zero spam patterns.",
        ),
        card(
          "linkedinScripts",
          "Social outreach scripts",
          "Connection notes and DM sequences that get replies.",
          "Write social outreach scripts (LinkedIn-style): connection note variants under 300 characters, a 3-message DM sequence (context, value, ask), and a comment-first warm-up play. Personalize per segment using the signals collected.",
        ),
        card(
          "callScript",
          "Call script & objections",
          "Discovery call plan with objection handling.",
          "Build a call plan: 30-second opener, discovery questions mapped to pains, value narrative, and an objection-handling table (objection → acknowledge, explore, respond) for the 8 most likely objections. Include a voicemail script.",
        ),
        card(
          "cadencePlan",
          "Follow-up cadence",
          "Full multi-channel sequence over 3 weeks.",
          "Design a complete outreach cadence over 3 weeks mixing email, social and calls: day-by-day plan, channel and message goal per touch, exit criteria (reply, meeting, disqualify), and rules for breaking the sequence personally.",
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
          "Format the collected leads as a CRM-ready CSV: standard columns (company, domain, contact, role, email pattern, phone, country, source, score, signal, next action), deduplicated and validated. Save it to the workspace and provide the import mapping.",
        ),
        card(
          "meetingPrep",
          "Meeting prep",
          "Brief, questions and angles for a specific meeting.",
          "Prepare the upcoming meeting: one-page brief on the account and attendees, 3 hypotheses about their pains with supporting evidence, discovery questions, likely objections with responses, and the ideal next step to propose.",
        ),
        card(
          "pipelineReview",
          "Pipeline review",
          "Health check and weighted forecast of your deals.",
          "Review the sales pipeline: stage distribution, aging and stuck deals, weighted forecast, win-rate by segment, and the 5 actions with the highest expected impact this month. Deliver as a scored dashboard saved to the workspace.",
        ),
        card(
          "competitorCustomers",
          "Competitor customers",
          "Who buys from competitors — displacement targets.",
          "Map competitors' customers from public evidence (case studies, logo walls, reviews, testimonials): build a displacement target list with the competitor used, evidence URL, apparent contract context, and a switch angle per account.",
        ),
      ],
    },
  ];
}

function teamGroups(tx: Tx): StudioGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): StudioCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "design",
      label: tx("studio.groups.design", "Design"),
      cards: [
        card(
          "orgChart",
          "Org structure & chart",
          "Structure derived from strategy, with reporting lines.",
          "Design the organization from the strategy: map the outcome streams and workload, choose the lightest fitting structure (functional, squads, pods), and deliver an org chart (Mermaid diagram) with reporting lines, spans of control, and the trade-offs made explicit. Save it to org/org-chart.md.",
        ),
        card(
          "roleSheets",
          "Role definitions",
          "One sheet per role: mission, outcomes, KPIs.",
          "Write complete role sheets for the team: for each role — mission in one sentence, 3-5 owned outcomes, key activities, interfaces, required skills, 2-4 KPIs, seniority and reporting line, and what success looks like in the first 90 days. One file per role under org/roles/. Rule: every outcome has exactly one owner.",
        ),
        card(
          "raciMatrix",
          "RACI & decision rights",
          "Who decides what — no orphan or double-owned decisions.",
          "Build the RACI matrix for the key processes and decisions: list the recurring processes, assign Responsible/Accountable/Consulted/Informed per role, define escalation paths, and flag decisions that currently have zero or multiple owners. Save to org/raci.md.",
        ),
        card(
          "ritualsPlan",
          "Rituals & governance",
          "Operating rhythm: syncs, reviews, OKR cadence.",
          "Design the team's operating rhythm: daily/weekly syncs, monthly business review, quarterly OKR setting. For each ritual: purpose, attendees, cadence, expected output, and maximum duration. Keep it minimal — flag any meeting without a clear output. Save to org/rituals.md.",
        ),
      ],
    },
    {
      id: "staff",
      label: tx("studio.groups.staff", "Staff"),
      cards: [
        card(
          "hiringPlan",
          "Hiring plan",
          "Sequenced hiring with metric triggers, not dates.",
          "Build a sequenced hiring plan: which roles to hire in what order, with metric-based triggers ('hire X when Y passes Z') rather than dates, cost estimates per hire, and the risk of hiring too early vs too late for each role. Save to org/hiring-plan.md.",
        ),
        card(
          "jobPostings",
          "Job descriptions",
          "Compelling, precise postings per role.",
          "Write job descriptions for the open roles: role summary that sells the mission, key responsibilities, must-have vs nice-to-have requirements, compensation structure, and the hiring process steps. One file per role, ready to publish.",
        ),
        card(
          "interviewKit",
          "Interview kits",
          "Structured interviews with scoring grids.",
          "Create interview kits for the roles: interview stages with purpose, structured questions per competency, scoring grid (1-5 anchors per criterion), red flags to watch, and a candidate comparison sheet. Consistent and bias-aware.",
        ),
        card(
          "onboardingPlan",
          "Onboarding plans",
          "30-60-90 day plans per role.",
          "Build onboarding plans per role: first-week checklist (accounts, tools, intros), 30-60-90 day objectives with clear success criteria, assigned buddy/mentor, and the check-in cadence with the manager.",
        ),
      ],
    },
    {
      id: "run",
      label: tx("studio.groups.run", "Run"),
      cards: [
        card(
          "virtualTeam",
          "Virtual AI team",
          "Spawn a team of AI agents with roles on a mission.",
          "Compose and run a virtual AI team on the mission: define 2-5 roles with one clear deliverable and exact output path each, write a complete brief per agent (mission, context, skills, boundaries), spawn them as subagents, track them in team/roster.md, verify every deliverable, and merge the results into a final assembly yourself.",
        ),
        card(
          "okrCascade",
          "OKRs & goals",
          "Company → team → role objective cascade.",
          "Cascade the objectives: from the company goal, derive team OKRs (3 objectives max, 2-4 measurable key results each), then role-level contributions. Flag vanity metrics and key results nobody owns. Deliver as org/okrs.md with a scoring method for the quarter.",
        ),
        card(
          "projectStaffing",
          "Project staffing",
          "Assign the right people and RACI to a project.",
          "Staff the project: break it into workstreams, assign roles to each based on skills and load, build the project RACI, identify capacity conflicts and single points of failure, and propose mitigations. Deliver a staffing plan with a load estimate per person.",
        ),
        card(
          "opsReview",
          "Ops review pack",
          "Weekly/monthly review: KPIs, risks, decisions.",
          "Prepare the operations review pack: KPI dashboard structure per team, wins and misses vs plan, top risks with owners, decisions needed with options and a recommendation each, and next period priorities. Save as a ready-to-run meeting document.",
        ),
      ],
    },
    {
      id: "grow",
      label: tx("studio.groups.grow", "Grow"),
      cards: [
        card(
          "skillsMatrix",
          "Skills matrix & training",
          "Competency gaps and a training plan.",
          "Build the team skills matrix: competencies in rows, people in columns, current level vs required level, highlight the gaps and single points of failure, then propose a training plan (internal, external, mentoring) prioritized by business impact.",
        ),
        card(
          "feedbackCycle",
          "Performance & feedback",
          "Review templates and a feedback cadence.",
          "Design the performance and feedback system: lightweight review template (outcomes vs objectives, strengths, growth areas), 1:1 structure and cadence, continuous feedback norms, and a calibration method to keep reviews fair across the team.",
        ),
        card(
          "careerPaths",
          "Career paths",
          "Progression tracks and promotion criteria.",
          "Define career paths: progression tracks (expert vs management), levels with explicit expectations per level, promotion criteria and process, and how compensation bands map to levels. One page per track under org/careers/.",
        ),
        card(
          "teamHealth",
          "Team health check",
          "Workload, engagement, friction diagnostic.",
          "Run a team health diagnostic: design a short survey (workload, clarity, autonomy, recognition, psychological safety), an interview guide for 1:1 deep dives, and a scoring model. Deliver the diagnostic kit plus an action-plan template ranked by impact.",
        ),
      ],
    },
  ];
}

const MODULE_META: Record<
  StudioModule,
  {
    command: string;
    icon: typeof FileText;
    titleKey: string;
    titleDefault: string;
    subtitleKey: string;
    subtitleDefault: string;
    groups: (tx: Tx) => StudioGroup[];
  }
> = {
  content: {
    command: "/studio",
    icon: FileText,
    titleKey: "studio.content.title",
    titleDefault: "Document studio",
    subtitleKey: "studio.content.subtitle",
    subtitleDefault:
      "Generate polished PowerPoint, Word, PDF and Excel documents from templates.",
    groups: contentGroups,
  },
  marketing: {
    command: "/campaign",
    icon: Megaphone,
    titleKey: "studio.marketing.title",
    titleDefault: "Marketing studio",
    subtitleKey: "studio.marketing.subtitle",
    subtitleDefault:
      "Campaigns, ad videos, product images, social content — produced end to end.",
    groups: marketingGroups,
  },
  seo: {
    command: "/seo",
    icon: TrendingUp,
    titleKey: "studio.seo.title",
    titleDefault: "SEO studio",
    subtitleKey: "studio.seo.subtitle",
    subtitleDefault: "Audits, keyword research, optimized content and link strategy.",
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
    groups: leadsGroups,
  },
  team: {
    command: "/team",
    icon: Users,
    titleKey: "studio.team.title",
    titleDefault: "Team studio",
    subtitleKey: "studio.team.subtitle",
    subtitleDefault:
      "Design the organization, staff it, run it — and spawn virtual AI teams on missions.",
    groups: teamGroups,
  },
};

function formatIcon(format?: DocFormat) {
  if (format === "xls") return FileSpreadsheet;
  if (format === "ppt") return Presentation;
  return FileText;
}

export function StudioWorkspace({
  module,
  chatOpen,
  onToggleChat,
  onRun,
}: {
  module: StudioModule;
  chatOpen?: boolean;
  onToggleChat?: () => void;
  onRun?: (text: string) => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback<Tx>(
    (key, fallback) => t(key, { defaultValue: fallback }),
    [t],
  );
  const meta = MODULE_META[module];
  const groups = useMemo(() => meta.groups(tx), [meta, tx]);
  const [brief, setBrief] = useState("");
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [docTheme, setDocTheme] = useState<string | null>(null);

  const visibleGroups = useMemo(
    () => (activeGroup ? groups.filter((g) => g.id === activeGroup) : groups),
    [groups, activeGroup],
  );

  const runCard = useCallback(
    (cardPrompt: string) => {
      if (!onRun) return;
      const trimmedBrief = brief.trim();
      const parts = [`${meta.command} ${cardPrompt}`];
      if (module === "content" && docTheme) {
        parts.push(
          `Apply the "${docTheme}" visual theme exactly as specified in the document-templates skill.`,
        );
      }
      if (trimmedBrief) {
        parts.push(`Brief from the user: ${trimmedBrief}`);
      }
      onRun(parts.join("\n\n"));
    },
    [onRun, brief, meta.command, module, docTheme],
  );

  const ModuleIcon = meta.icon;

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      {/* Header */}
      <div className="flex shrink-0 items-start gap-3 border-b border-border/55 px-5 py-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted/40">
          <ModuleIcon className="h-5 w-5 text-foreground" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[15px] font-semibold text-foreground">
            {tx(meta.titleKey, meta.titleDefault)}
          </h1>
          <p className="truncate text-[12.5px] text-muted-foreground">
            {tx(meta.subtitleKey, meta.subtitleDefault)}
          </p>
        </div>
        {onToggleChat ? (
          <button
            type="button"
            onClick={onToggleChat}
            className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label={
              chatOpen ? tx("dev.hideChat", "Hide chat") : tx("dev.showChat", "Show chat")
            }
            title={chatOpen ? tx("dev.hideChat", "Hide chat") : tx("dev.showChat", "Show chat")}
          >
            {chatOpen ? (
              <PanelRightClose className="h-4 w-4" aria-hidden />
            ) : (
              <PanelRightOpen className="h-4 w-4" aria-hidden />
            )}
          </button>
        ) : null}
      </div>

      {/* Brief + theme filter */}
      <div className="flex shrink-0 flex-col gap-2.5 border-b border-border/55 px-5 py-3">
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
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setActiveGroup(null)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors",
              activeGroup === null
                ? "border-foreground/60 bg-foreground text-background"
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
                  ? "border-foreground/60 bg-foreground text-background"
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
          {module === "team" && !activeGroup ? (
            <TeamOrgChart onCall={onRun} />
          ) : null}
          {visibleGroups.map((group) => (
            <section key={group.id}>
              <h2 className="mb-2.5 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                {group.label}
              </h2>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
                {group.cards.map((card) => {
                  const FormatIcon = formatIcon(card.format);
                  return (
                    <button
                      key={card.id}
                      type="button"
                      onClick={() => runCard(card.prompt)}
                      className="group flex flex-col gap-1.5 rounded-xl border border-border/60 bg-background p-3.5 text-left transition-colors hover:border-foreground/40 hover:bg-muted/30"
                    >
                      <div className="flex items-center gap-2">
                        <FormatIcon
                          className="h-4 w-4 shrink-0 text-muted-foreground"
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
          {tx(
            "studio.footerHint",
            "Every action runs the agent in the chat panel — files are saved to your workspace.",
          )}{" "}
          <span className="font-mono">{meta.command}</span>
        </p>
      </div>
    </div>
  );
}
