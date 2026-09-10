# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Official and authorized career sources. No LinkedIn scrape or auto-apply."""

from __future__ import annotations

import hashlib
import html
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

TRACKS = ("freelance", "jobs")
STAGES = (
    "discovered",
    "matched",
    "ready",
    "applied",
    "replied",
    "interview",
    "offer",
    "won",
    "rejected",
)
APPLY_MODES = ("manual", "review", "autopilot")
BUCKETS = ("perfect", "good", "skip")
INBOX_CLASSES = (
    "interview",
    "positive",
    "need_response",
    "rejected",
    "offer",
    "waiting",
)

# Preferred markets: languages, cities, freelance aliases, public board domains.
# Web Job Search uses langs + aliases. LinkedIn is always opened, never fetched.
# Free-Work has its own public listing reader, so it takes no web search slot.
MARKETS: dict[str, dict[str, Any]] = {
    "FR": {
        "label": "France",
        "langs": ("fr", "en"),
        "cities": ("Paris", "Lyon", "Toulouse", "Nantes"),
        "aliases": ("freelance", "mission", "TJM", "contrat"),
        "domains": (
            "francetravail.fr",
            "apec.fr",
            "welcometothejungle.com",
            "malt.fr",
            "chooseyourboss.com",
        ),
    },
    "BE": {
        "label": "Belgium",
        "langs": ("fr", "nl", "en"),
        "cities": ("Bruxelles", "Brussel", "Antwerp", "Liege"),
        "aliases": ("freelance", "contractor", "consultant"),
        "domains": ("ictjob.be", "vdab.be", "leforem.be", "actiris.brussels", "jobat.be", "stepstone.be"),
    },
    "CH": {
        "label": "Switzerland",
        "langs": ("fr", "de", "en"),
        "cities": ("Zurich", "Geneva", "Lausanne", "Basel"),
        "aliases": ("freelance", "contract", "contractor", "consultant", "Freiberuflich", "Temporar"),
        "domains": ("jobs.ch", "jobscout24.ch", "swissdevjobs.ch"),
    },
    "GB": {
        "label": "United Kingdom",
        "langs": ("en",),
        "cities": ("London", "Manchester", "Edinburgh"),
        "aliases": ("contract", "contractor", "Outside IR35", "IR35"),
        "domains": (
            "jobserve.com",
            "cwjobs.co.uk",
            "reed.co.uk",
            "totaljobs.com",
            "technojobs.co.uk",
            "findajob.dwp.gov.uk",
        ),
    },
    "US": {
        "label": "USA",
        "langs": ("en",),
        "cities": ("New York", "Austin", "Seattle", "San Francisco"),
        "aliases": ("contract", "contractor", "1099", "W2 contract", "C2C", "Corp-to-Corp"),
        "domains": ("dice.com", "wellfound.com", "builtin.com", "usajobs.gov", "ziprecruiter.com"),
    },
    "CA": {
        "label": "Canada",
        "langs": ("en", "fr"),
        "cities": ("Montreal", "Toronto", "Vancouver"),
        "aliases": ("contract", "contractor", "contractuel", "consultant"),
        "domains": ("jobbank.gc.ca", "jobillico.com", "jobboom.com", "eluta.ca"),
    },
    "DE": {
        "label": "Germany",
        "langs": ("de", "en"),
        "cities": ("Berlin", "Munich", "Hamburg"),
        "aliases": ("freelance", "Freiberuflich", "contract"),
        "domains": ("arbeitsagentur.de", "stepstone.de", "xing.com"),
    },
    "NL": {
        "label": "Netherlands",
        "langs": ("nl", "en"),
        "cities": ("Amsterdam", "Rotterdam"),
        "aliases": ("freelance", "contractor", "zzp"),
        "domains": ("werk.nl", "nationalevacaturebank.nl"),
    },
    "AE": {
        "label": "UAE",
        "langs": ("en", "ar"),
        "cities": ("Dubai", "Abu Dhabi"),
        "aliases": ("contract", "contractor", "consultant"),
        "domains": ("bayt.com", "gulftalent.com", "naukrigulf.com", "dubaicareers.ae"),
    },
    "SA": {
        "label": "Saudi Arabia",
        "langs": ("en", "ar"),
        "cities": ("Riyadh", "Jeddah"),
        "aliases": ("contract", "contractor"),
        "domains": ("jadarat.sa", "bayt.com", "gulftalent.com", "naukrigulf.com"),
    },
    "QA": {
        "label": "Qatar",
        "langs": ("en", "ar"),
        "cities": ("Doha",),
        "aliases": ("contract", "contractor"),
        "domains": ("bayt.com", "gulftalent.com", "qatarliving.com"),
    },
    "KW": {
        "label": "Kuwait",
        "langs": ("en", "ar"),
        "cities": ("Kuwait City",),
        "aliases": ("contract",),
        "domains": ("bayt.com", "gulftalent.com", "naukrigulf.com"),
    },
    "OM": {
        "label": "Oman",
        "langs": ("en", "ar"),
        "cities": ("Muscat",),
        "aliases": ("contract",),
        "domains": ("bayt.com", "gulftalent.com", "naukrigulf.com"),
    },
    "BH": {
        "label": "Bahrain",
        "langs": ("en", "ar"),
        "cities": ("Manama",),
        "aliases": ("contract",),
        "domains": ("bayt.com", "gulftalent.com", "naukrigulf.com"),
    },
    "MA": {
        "label": "Morocco",
        "langs": ("fr", "en", "ar"),
        "cities": ("Casablanca", "Rabat"),
        "aliases": ("freelance", "mission", "contrat"),
        "domains": ("rekrute.com", "bayt.com", "emploi.ma"),
    },
    "TN": {
        "label": "Tunisia",
        "langs": ("fr", "en", "ar"),
        "cities": ("Tunis",),
        "aliases": ("freelance", "mission", "contrat"),
        "domains": ("tanitjobs.com", "keejob.com", "bayt.com"),
    },
    "ES": {
        "label": "Spain",
        "langs": ("es", "en"),
        "cities": ("Madrid", "Barcelona"),
        "aliases": ("freelance", "autonomo", "contrato"),
        "domains": ("infojobs.net", "infoempleo.com"),
    },
    "IT": {
        "label": "Italy",
        "langs": ("it", "en"),
        "cities": ("Milan", "Rome"),
        "aliases": ("freelance", "partita iva", "contratto"),
        "domains": ("infojobs.it",),
    },
    "PT": {
        "label": "Portugal",
        "langs": ("pt", "en"),
        "cities": ("Lisbon", "Porto"),
        "aliases": ("freelance", "contrato"),
        "domains": ("net-empregos.com",),
    },
    "IE": {
        "label": "Ireland",
        "langs": ("en",),
        "cities": ("Dublin",),
        "aliases": ("contract", "contractor"),
        "domains": ("jobs.ie", "irishjobs.ie"),
    },
    "SE": {
        "label": "Sweden",
        "langs": ("sv", "en"),
        "cities": ("Stockholm",),
        "aliases": ("freelance", "konsult"),
        "domains": ("arbetsformedlingen.se",),
    },
    "PL": {
        "label": "Poland",
        "langs": ("pl", "en"),
        "cities": ("Warsaw",),
        "aliases": ("freelance", "kontrakt"),
        "domains": ("pracuj.pl",),
    },
}

# Search starts on Gulf + Maghreb + core Europe, not France alone.
DEFAULT_SEARCH_COUNTRIES = (
    "AE",
    "QA",
    "SA",
    "OM",
    "BH",
    "KW",
    "MA",
    "TN",
    "FR",
    "DE",
    "GB",
    "ES",
    "IT",
    "NL",
    "BE",
    "CH",
)
WEB_SEARCH_PACK_LIMIT = 16
WEB_SEARCH_ATS_SLOTS = 5
WEB_SEARCH_COMPANY_SLOTS = 3
COMPANY_CAREER_OR = (
    'careers OR "job openings" OR "nous recrutons" OR "offre d\'emploi" OR "join our team"'
)
# Public ATS hosts used in web snippets. Must stay off CLOSED_FETCH_HOSTS.
ATS_WEB_SITES = (
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "jobs.workable.com",
    "jobs.smartrecruiters.com",
)

# Default match weights when the user did not type them. Primary decays, then secondary.
PRIMARY_WEIGHTS = (100.0, 90.0, 85.0, 80.0)
SECONDARY_WEIGHTS = (70.0, 60.0, 50.0, 45.0)

# Web Job Search language packs. France → FR+EN, Germany → DE+EN, Gulf → EN+AR.
SEARCH_LANG_TERMS: dict[str, str] = {
    "fr": "offre OR emploi OR mission",
    "en": "jobs OR hiring OR contract",
    "de": "Stelle OR Freiberuflich OR Job",
    "nl": "vacature OR freelance",
    "ar": "وظائف OR jobs",
    "es": "oferta OR empleo OR freelance",
    "it": "offerta OR lavoro OR freelance",
    "pt": "oferta OR emprego OR freelance",
    "sv": "jobb OR uppdrag",
    "pl": "praca OR zlecenie",
}

# Level 1 official APIs + Level 2 ATS + Level 4 closed platforms (open only).
CATALOG: list[dict[str, Any]] = [
    {
        "id": "adzuna",
        "name": "Adzuna",
        "level": 1,
        "zone": "FR EU US",
        "ingest": "official_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://developer.adzuna.com/",
        "notes": "Official search API. Enable in setup and store ADZUNA_APP_ID + ADZUNA_APP_KEY.",
    },
    {
        "id": "jooble",
        "name": "Jooble",
        "level": 1,
        "zone": "World",
        "ingest": "official_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://jooble.org/api/about",
        "notes": "Official REST API. Enable in setup and store JOOBLE_API_KEY.",
    },
    {
        "id": "france-travail",
        "name": "France Travail",
        "level": 1,
        "zone": "FR",
        "ingest": "partner_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://francetravail.io/",
        "notes": "Partner API. Search URL always available without a key.",
    },
    {
        "id": "usajobs",
        "name": "USAJOBS",
        "level": 1,
        "zone": "US",
        "ingest": "official_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 3,
        "url": "https://developer.usajobs.gov/",
        "notes": "US federal jobs. Enable in setup and store USAJOBS_API_KEY + USAJOBS_USER_AGENT.",
    },
    {
        "id": "remotive",
        "name": "Remotive",
        "level": 1,
        "zone": "Remote",
        "ingest": "official_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 4,
        "url": "https://remotive.com/remote-jobs/api",
        "notes": "Public remote API. Attribution + original URL required. 24h delay on public feed.",
    },
    {
        "id": "jobicy",
        "name": "Jobicy",
        "level": 1,
        "zone": "Remote",
        "ingest": "public_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 8,
        "url": "https://jobicy.com/api/v2/remote-jobs",
        "notes": (
            "Public API, no key: geo (USA, Canada, UK, France, Belgium, Switzerland, UAE, Europe, EMEA, APAC...), "
            "industry and keyword tag, 200 offers per request, salary min / max / currency / period, "
            "employment type and level. One hour cache, Jobicy credited, canonical job URL kept."
        ),
    },
    {
        "id": "remoteok",
        "name": "Remote OK",
        "level": 1,
        "zone": "Remote",
        "ingest": "public_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://remoteok.com/api",
        "notes": "Public JSON feed. Remote OK credited and the listing link kept, as its terms ask. USD salaries.",
    },
    {
        "id": "himalayas",
        "name": "Himalayas",
        "level": 1,
        "zone": "Remote",
        "ingest": "public_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://himalayas.app/jobs/api",
        "notes": "Public jobs API: salary with currency and period, seniority, employment type, location restrictions.",
    },
    {
        "id": "weworkremotely",
        "name": "We Work Remotely",
        "level": 1,
        "zone": "Remote",
        "ingest": "public_rss",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://weworkremotely.com/remote-jobs.rss",
        "notes": "Public RSS (all jobs + programming category): region, contract type, skills, listing link.",
    },
    {
        "id": "arbeitnow",
        "name": "Arbeitnow",
        "level": 1,
        "zone": "EU",
        "ingest": "public_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.arbeitnow.com/api/job-board-api",
        "notes": "Public job board API (Europe, hourly refresh, no key). Link back to Arbeitnow kept.",
    },
    {
        "id": "hn-hiring",
        "name": "Hacker News Who is hiring",
        "level": 1,
        "zone": "World",
        "ingest": "public_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 4,
        "url": "https://hn.algolia.com/api/v1/search_by_date",
        "notes": "Monthly Ask HN thread read through the public Algolia API. Header line gives company, role, place, remote, pay.",
    },
    {
        "id": "jobopportunities",
        "name": "Job Opportunities API",
        "level": 1,
        "zone": "World",
        "ingest": "official_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.jobopportunitiesapi.org/docs",
        "notes": "Employer-direct ledger (ATS, career sites, public services) with per-field provenance. Free key; store JOBOPPORTUNITIES_API_KEY.",
    },
    {
        "id": "greenhouse",
        "name": "Greenhouse",
        "level": 2,
        "zone": "Tech",
        "ingest": "ats_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://developers.greenhouse.io/job-board.html",
        "notes": "Company job board API. Career pages, not LinkedIn.",
    },
    {
        "id": "lever",
        "name": "Lever",
        "level": 2,
        "zone": "Tech",
        "ingest": "ats_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://github.com/lever/postings-api",
        "notes": "Published postings API. Apply only when the posting allows it.",
    },
    {
        "id": "ashby",
        "name": "Ashby",
        "level": 2,
        "zone": "Tech",
        "ingest": "ats_api",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://developers.ashbyhq.com/docs/job-posting-api",
        "notes": "Official published jobs API.",
    },
    {
        "id": "employers",
        "name": "ESN, consulting and agency feeds",
        "level": 2,
        "zone": "Markets",
        "ingest": "employer_feed",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://api.smartrecruiters.com/v1/companies",
        "notes": (
            "Directory of houses per selected market plus the employers you add. "
            "Feeds read directly: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, "
            "Teamtailor, Personio, Workday, or the careers page."
        ),
    },
    {
        "id": "eures",
        "name": "EURES",
        "level": 1,
        "zone": "EU",
        "ingest": "partner_only",
        "auto_search": False,
        "auto_apply": False,
        "priority": 3,
        "url": "https://eures.europa.eu/",
        "notes": "No screen scrape. API reserved to recognised EURES partners.",
    },
    {
        "id": "linkedin",
        "name": "LinkedIn Jobs",
        "level": 4,
        "zone": "World",
        "ingest": "public_listing",
        "auto_search": True,
        "auto_apply": False,
        "priority": 10,
        "url": "https://www.linkedin.com/jobs/",
        "notes": "Source #1 in every country. Public guest listings, rate limited, no login. Never Easy Apply.",
    },
    {
        "id": "web-job-search",
        "name": "Web Job Search",
        "level": 3,
        "zone": "World",
        "ingest": "search_snippet",
        "auto_search": True,
        "auto_apply": False,
        "priority": 9,
        "url": "https://html.duckduckgo.com/html/",
        "notes": "Generic connector. One query pack per preferred country, language, and board domain. Snippets only.",
    },
    {
        "id": "web-search",
        "name": "Web Job Search",
        "level": 3,
        "zone": "World",
        "ingest": "search_snippet",
        "auto_search": True,
        "auto_apply": False,
        "priority": 8,
        "url": "https://html.duckduckgo.com/html/",
        "notes": "Alias of Web Job Search. Preferential queries per market. Closed boards are never fetched.",
    },
    {
        "id": "free-work",
        "name": "Free-Work",
        "level": 3,
        "zone": "FR",
        "ingest": "public_listing",
        "auto_search": True,
        "auto_apply": False,
        "priority": 9,
        "url": "https://www.free-work.com/",
        "notes": (
            "IT freelance missions and jobs, FR and UK. Public search pages read live "
            "(no login, one request per second, capped per run): TJM, duration, remote "
            "mode, skills and full description. Generic scrape never touches the host."
        ),
    },
    {
        "id": "apec",
        "name": "APEC",
        "level": 3,
        "zone": "FR",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.apec.fr/",
        "notes": "Cadres / IT. Web discovery, no login scrape.",
    },
    {
        "id": "malt",
        "name": "Malt",
        "level": 4,
        "zone": "FR EU",
        "ingest": "open_manual",
        "auto_search": False,
        "auto_apply": False,
        "priority": 4,
        "url": "https://www.malt.fr/",
        "notes": "Marketplace. Open official + paste. No scrape.",
    },
    {
        "id": "wttj",
        "name": "Welcome to the Jungle",
        "level": 3,
        "zone": "FR",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.welcometothejungle.com/",
        "notes": "Tech / startups. Web discovery only.",
    },
    {
        "id": "ictjob",
        "name": "ICTjob",
        "level": 3,
        "zone": "BE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 7,
        "url": "https://www.ictjob.be/",
        "notes": "Belgium IT. Search FR + NL + EN.",
    },
    {
        "id": "jobs-ch",
        "name": "jobs.ch",
        "level": 3,
        "zone": "CH",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 7,
        "url": "https://www.jobs.ch/",
        "notes": "Switzerland main board. Search FR + DE + EN.",
    },
    {
        "id": "jobserve",
        "name": "JobServe",
        "level": 3,
        "zone": "GB",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 8,
        "url": "https://www.jobserve.com/",
        "notes": "UK IT contracts. Web discovery. IR35 stays a match criterion.",
    },
    {
        "id": "cwjobs",
        "name": "CWJobs",
        "level": 3,
        "zone": "GB",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 7,
        "url": "https://www.cwjobs.co.uk/",
        "notes": "UK IT / contract. Web discovery.",
    },
    {
        "id": "dice",
        "name": "Dice",
        "level": 3,
        "zone": "US",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 8,
        "url": "https://www.dice.com/",
        "notes": "US IT contracts. Web discovery. 1099 / W2 / C2C stay in the query pack.",
    },
    {
        "id": "wellfound",
        "name": "Wellfound",
        "level": 3,
        "zone": "US",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://wellfound.com/",
        "notes": "Startups / tech. Web discovery.",
    },
    {
        "id": "job-bank-ca",
        "name": "Job Bank Canada",
        "level": 3,
        "zone": "CA",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 8,
        "url": "https://www.jobbank.gc.ca/",
        "notes": "Official Canada board. Search EN + FR.",
    },
    {
        "id": "indeed",
        "name": "Indeed",
        "level": 4,
        "zone": "World",
        "ingest": "open_manual",
        "auto_search": False,
        "auto_apply": False,
        "priority": 4,
        "url": "https://www.indeed.com/",
        "notes": "Aggregator. Web search + official link. Never scrape.",
    },
    {
        "id": "chooseyourboss",
        "name": "ChooseYourBoss",
        "level": 3,
        "zone": "FR",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.chooseyourboss.com/",
        "notes": "France IT. Web discovery.",
    },
    {
        "id": "vdab",
        "name": "VDAB",
        "level": 3,
        "zone": "BE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.vdab.be/",
        "notes": "Flanders official board.",
    },
    {
        "id": "le-forem",
        "name": "Le Forem",
        "level": 3,
        "zone": "BE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.leforem.be/",
        "notes": "Wallonia official board.",
    },
    {
        "id": "actiris",
        "name": "Actiris",
        "level": 3,
        "zone": "BE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.actiris.brussels/",
        "notes": "Brussels official board.",
    },
    {
        "id": "jobat",
        "name": "Jobat",
        "level": 3,
        "zone": "BE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.jobat.be/",
        "notes": "Belgium generalist. Web discovery.",
    },
    {
        "id": "stepstone-be",
        "name": "StepStone Belgium",
        "level": 3,
        "zone": "BE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.stepstone.be/",
        "notes": "Belgium generalist. Web discovery.",
    },
    {
        "id": "jobscout24",
        "name": "JobScout24",
        "level": 3,
        "zone": "CH",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.jobscout24.ch/",
        "notes": "Switzerland board. Web discovery.",
    },
    {
        "id": "swissdevjobs",
        "name": "SwissDevJobs",
        "level": 3,
        "zone": "CH",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://swissdevjobs.ch/",
        "notes": "Switzerland tech. Web discovery.",
    },
    {
        "id": "reed",
        "name": "Reed",
        "level": 3,
        "zone": "GB",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.reed.co.uk/",
        "notes": "UK contract + permanent. Web discovery.",
    },
    {
        "id": "totaljobs",
        "name": "Totaljobs",
        "level": 3,
        "zone": "GB",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.totaljobs.com/",
        "notes": "UK contract + jobs. Web discovery.",
    },
    {
        "id": "technojobs",
        "name": "Technojobs",
        "level": 3,
        "zone": "GB",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.technojobs.co.uk/",
        "notes": "UK IT contracts. Web discovery.",
    },
    {
        "id": "govuk-find-a-job",
        "name": "GOV.UK Find a Job",
        "level": 3,
        "zone": "GB",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.gov.uk/find-a-job",
        "notes": "UK official board. Web discovery.",
    },
    {
        "id": "builtin",
        "name": "Built In",
        "level": 3,
        "zone": "US",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://builtin.com/",
        "notes": "US tech. Web discovery.",
    },
    {
        "id": "ziprecruiter",
        "name": "ZipRecruiter",
        "level": 4,
        "zone": "US",
        "ingest": "open_manual",
        "auto_search": False,
        "auto_apply": False,
        "priority": 4,
        "url": "https://www.ziprecruiter.com/",
        "notes": "US aggregator. Official link only. Never scrape.",
    },
    {
        "id": "jobillico",
        "name": "Jobillico",
        "level": 3,
        "zone": "CA",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.jobillico.com/",
        "notes": "Canada. Web discovery.",
    },
    {
        "id": "jobboom",
        "name": "Jobboom",
        "level": 3,
        "zone": "CA",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.jobboom.com/",
        "notes": "Canada. Web discovery.",
    },
    {
        "id": "eluta",
        "name": "Eluta",
        "level": 3,
        "zone": "CA",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.eluta.ca/",
        "notes": "Canada career pages. Web discovery.",
    },
    {
        "id": "arbeitsagentur",
        "name": "Bundesagentur fur Arbeit",
        "level": 3,
        "zone": "DE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 6,
        "url": "https://www.arbeitsagentur.de/",
        "notes": "Germany official board. Search DE + EN.",
    },
    {
        "id": "stepstone-de",
        "name": "StepStone Germany",
        "level": 3,
        "zone": "DE",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.stepstone.de/",
        "notes": "Germany board. Web discovery.",
    },
    {
        "id": "werk-nl",
        "name": "Werk.nl",
        "level": 3,
        "zone": "NL",
        "ingest": "web_agent",
        "auto_search": True,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.werk.nl/",
        "notes": "Netherlands official board.",
    },
    {
        "id": "bayt",
        "name": "Bayt",
        "level": 4,
        "zone": "Gulf",
        "ingest": "open_manual",
        "auto_search": False,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.bayt.com/",
        "notes": "Gulf board. Open official + paste. No scrape.",
    },
    {
        "id": "gulftalent",
        "name": "GulfTalent",
        "level": 4,
        "zone": "Gulf",
        "ingest": "open_manual",
        "auto_search": False,
        "auto_apply": False,
        "priority": 5,
        "url": "https://www.gulftalent.com/",
        "notes": "Gulf board. Open official + paste. No scrape.",
    },
    {
        "id": "jadarat",
        "name": "Jadarat",
        "level": 4,
        "zone": "SA",
        "ingest": "open_manual",
        "auto_search": False,
        "auto_apply": False,
        "priority": 5,
        "url": "https://jadarat.sa/",
        "notes": "Saudi official. Open official + paste. No scrape.",
    },
]


def catalog() -> list[dict[str, Any]]:
    return [dict(row) for row in CATALOG]


def default_country_weights(
    primary: list[str],
    secondary: list[str],
    excluded: list[str] | None = None,
    current: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Fill missing market weights. Never overwrite a value the user already set."""
    weights: dict[str, float] = {}
    for key, value in (current or {}).items():
        try:
            weights[str(key).upper()] = float(value)
        except (TypeError, ValueError):
            continue
    blocked = {str(item).strip().upper() for item in (excluded or []) if str(item).strip()}
    for iso in blocked:
        weights.pop(iso, None)
    for index, raw in enumerate(primary):
        iso = str(raw).strip().upper()
        if not iso or iso in blocked or iso in weights:
            continue
        weights[iso] = PRIMARY_WEIGHTS[index] if index < len(PRIMARY_WEIGHTS) else 75.0
    for index, raw in enumerate(secondary):
        iso = str(raw).strip().upper()
        if not iso or iso in blocked or iso in weights:
            continue
        weights[iso] = SECONDARY_WEIGHTS[index] if index < len(SECONDARY_WEIGHTS) else 50.0
    return weights


def source_by_id(source_id: str) -> dict[str, Any] | None:
    key = (source_id or "").strip().lower()
    for row in CATALOG:
        if row["id"] == key:
            return dict(row)
    return None


def market_of(country: str) -> dict[str, Any]:
    return dict(MARKETS.get((country or "").strip().upper()) or {})


# Public hosts the scrape tool may fetch. Closed boards stay off this list.
OPEN_SCRAPE_HOSTS = (
    "remotive.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "weworkremotely.com",
    "remoteok.com",
    "arbeitnow.com",
    "himalayas.app",
    "workable.com",
    "smartrecruiters.com",
    "usajobs.gov",
    "francetravail.fr",
)

# Prefer the public job-board hostname in scrape/web site: queries.
OPEN_SCRAPE_QUERY_HOSTS: dict[str, str] = {
    "greenhouse.io": "boards.greenhouse.io",
    "lever.co": "jobs.lever.co",
    "ashbyhq.com": "jobs.ashbyhq.com",
    "workable.com": "jobs.workable.com",
    "smartrecruiters.com": "jobs.smartrecruiters.com",
}


def is_open_scrape_url(url: str) -> bool:
    host = host_of(url)
    if not host or is_closed_job_url(url) or is_linkedin_url(url):
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in OPEN_SCRAPE_HOSTS)


CLOSED_FETCH_HOSTS = (
    "linkedin.com",
    "lnkd.in",
    "indeed.com",
    "indeed.fr",
    "indeed.be",
    "indeed.ch",
    "indeed.co.uk",
    "indeed.ca",
    "ziprecruiter.com",
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
    "stepstone.be",
    "reed.co.uk",
    "totaljobs.com",
    "cwjobs.co.uk",
    "jobserve.com",
    "technojobs.co.uk",
    "contractoruk.com",
    "dice.com",
    "builtin.com",
    "wellfound.com",
    "themuse.com",
    "jobs.ch",
    "jobscout24.ch",
    "swissdevjobs.ch",
    "ictjob.be",
    "jobat.be",
    "vdab.be",
    "leforem.be",
    "actiris.brussels",
    "jobbank.gc.ca",
    "jobillico.com",
    "jobboom.com",
    "eluta.ca",
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
)

_MARKET_LABEL = {
    "FR": "France",
    "DE": "Germany",
    "GB": "United Kingdom",
    "CH": "Switzerland",
    "BE": "Belgium",
    "NL": "Netherlands",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "QA": "Qatar",
    "KW": "Kuwait",
    "OM": "Oman",
    "BH": "Bahrain",
    "MA": "Morocco",
    "TN": "Tunisia",
    "ES": "Spain",
    "IT": "Italy",
    "PT": "Portugal",
    "IE": "Ireland",
    "SE": "Sweden",
    "PL": "Poland",
    "US": "United States",
    "CA": "Canada",
}


def stable_job_id(source: str, url: str, title: str) -> str:
    raw = f"{source}|{url}|{title}".encode("utf-8")
    return "job-" + hashlib.sha1(raw).hexdigest()[:12]


_LOC_FOLD = str.maketrans(
    "àáâäãåèéêëìíîïòóôöõùúûüýÿçñ",
    "aaaaaaeeeeiiiiooooouuuuyycn",
)
_COUNTRY_NAME_ALIASES: dict[str, str] = {
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "united states": "US",
    "united states of america": "US",
    "united arab emirates": "AE",
    "belgique": "BE",
    "suisse": "CH",
    "deutschland": "DE",
    "nederland": "NL",
    "holland": "NL",
    "arabie saoudite": "SA",
    "koweit": "KW",
    "bahrein": "BH",
    "uae": "AE",
    "emirates": "AE",
    "emirats": "AE",
    "emirats arabes unis": "AE",
    "maroc": "MA",
    "morocco": "MA",
    "tunisie": "TN",
    "tunisia": "TN",
    "espagne": "ES",
    "spain": "ES",
    "italie": "IT",
    "italy": "IT",
    "portugal": "PT",
    "irlande": "IE",
    "ireland": "IE",
    "suede": "SE",
    "sweden": "SE",
    "pologne": "PL",
    "poland": "PL",
    "qatar": "QA",
    "oman": "OM",
    "koweït": "KW",
}


def _fold_location(text: str) -> str:
    return (text or "").lower().translate(_LOC_FOLD)


def _country_alias_pairs() -> list[tuple[str, str]]:
    """Longest market label/city first. Never match a 2-letter ISO in prose."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(name: str, iso: str) -> None:
        key = _fold_location(name).strip()
        if len(key) < 3 or key in seen:
            return
        seen.add(key)
        pairs.append((key, iso))

    for iso, market in MARKETS.items():
        add(str(market.get("label") or ""), iso)
        for city in market.get("cities") or ():
            add(str(city), iso)
    for name, iso in _COUNTRY_NAME_ALIASES.items():
        add(name, iso)
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


_COUNTRY_ALIAS_PAIRS = _country_alias_pairs()


def infer_country_iso(country: str = "", location: str = "") -> str:
    """Map a country field or a LinkedIn location string onto a MARKETS ISO.

    An explicit ISO or REMOTE on the country field wins. Location text is
    matched against market labels and cities only, never a bare 2-letter code
    (so 'join us in London' does not become US).
    """
    raw = (country or "").strip()
    if raw:
        upper = raw.upper()
        if upper in MARKETS:
            return upper
        if upper in {"REMOTE", "WW", "WORLDWIDE"}:
            return "REMOTE"
        folded = _fold_location(raw)
        if folded in _COUNTRY_NAME_ALIASES:
            return _COUNTRY_NAME_ALIASES[folded]
        mapped = _match_country_text(raw)
        if mapped:
            return mapped
    mapped = _match_country_text(location or "")
    if mapped:
        return mapped
    if re.search(r"\b(remote|worldwide|world wide|anywhere)\b", _fold_location(location or "")):
        return "REMOTE"
    return ""


def countries_mentioned(text: str) -> list[str]:
    """ISO codes named in a search brief (France, UAE, Qatar, ...)."""
    hay = _fold_location(text or "")
    if not hay:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for name, iso in _COUNTRY_ALIAS_PAIRS:
        if iso in seen or iso not in MARKETS:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", hay):
            seen.add(iso)
            found.append(iso)
    return found


def _match_country_text(text: str) -> str:
    hay = _fold_location(text)
    if not hay:
        return ""
    for name, iso in _COUNTRY_ALIAS_PAIRS:
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", hay):
            return iso
    return ""


def normalize_job_url(url: str) -> str:
    """Host + path (+ query) so the same offer keeps one store row."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    query = parsed.query
    if not host and not path:
        return raw.lower()
    return f"{host}{path}" + (f"?{query}" if query else "")


def clean_job_text(text: str) -> str:
    return (text or "").replace("\u2014", "-").replace("\u2013", "-")


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"<br\s*/?>|</(?:p|div|h[1-6]|li|tr|blockquote|ul|ol)>", re.I)
_ENTITY_RE = re.compile(r"&(?:[a-z\d]+|#\d+|#x[0-9a-f]+);", re.I)


def html_to_text(raw: str) -> str:
    """Turn stored HTML job copy into readable text. Never execute markup."""
    text = html.unescape(raw or "")
    for _ in range(2):
        if "<" not in text and not _ENTITY_RE.search(text):
            break
        text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
        text = _BLOCK_RE.sub("\n", text)
        text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
        text = _TAG_RE.sub(" ", text)
        text = html.unescape(text)
    text = clean_job_text(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def expand_search_countries(
    selected: list[str] | None = None,
    excluded: list[str] | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    """Profile markets first, then Gulf / Maghreb / core Europe. Never search excluded."""
    blocked = {str(item).strip().upper() for item in (excluded or []) if str(item).strip()}
    seen: set[str] = set()
    out: list[str] = []

    def push(iso: str) -> None:
        key = str(iso or "").strip().upper()
        if len(key) != 2 or key in seen or key in blocked or key not in MARKETS:
            return
        seen.add(key)
        out.append(key)

    for item in list(selected or []) + list(extra or []):
        push(item)
    for item in DEFAULT_SEARCH_COUNTRIES:
        push(item)
    return out


_LISTING_TITLE = re.compile(
    r"fiche m[eé]tier|m[eé]tierscope|les fiches m[eé]tiers|"
    r"\b\d+\s+offres\b|plus de \d+\s+offres|"
    r"offres d['\u2019]emploi|"
    r"jobs for (january|february|march|april|may|june|july|august|september|october|november|december)|"
    r"\bpage \d+\b|"
    r"^emplois\s*:|"
    r"is hiring:|"
    r"find your next job|"
    r"search\s*-\s*job bank|"
    r"\bin various locations\b|"
    r"^engineer jobs\b|"
    r"\bjobs\s*\|\s*dice",
    re.I,
)
_LISTING_PATH = re.compile(
    r"metierscope|fiche[-_]?metier|tous-nos-metiers|/jobsearch/jobsearch",
    re.I,
)


def is_listing_hit(title: str, url: str = "") -> bool:
    """True for career-page SEO / search-result pages, not a single offer."""
    if _LISTING_TITLE.search(title or ""):
        return True
    path = urlparse(url or "").path.lower()
    return bool(_LISTING_PATH.search(path))


def host_of(url: str) -> str:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def is_linkedin_url(url: str) -> bool:
    host = host_of(url)
    return host == "linkedin.com" or host.endswith(".linkedin.com") or host == "lnkd.in"


def is_closed_job_url(url: str) -> bool:
    host = host_of(url)
    if not host:
        return "linkedin.com" in (url or "").lower()
    return any(host == blocked or host.endswith("." + blocked) for blocked in CLOSED_FETCH_HOSTS)


def france_travail_search_url(query: str) -> str:
    q = quote_plus((query or "").strip() or "data engineer")
    return f"https://candidat.francetravail.fr/offres/recherche?motsCles={q}&offresPartenaires=true"


def usajobs_search_url(query: str) -> str:
    q = quote_plus((query or "").strip() or "data engineer")
    return f"https://www.usajobs.gov/Search/Results?k={q}"


def remotive_search_url(query: str) -> str:
    q = quote_plus((query or "").strip() or "engineer")
    return f"https://remotive.com/remote-jobs?search={q}"


def linkedin_search_url(
    query: str,
    country: str = "",
    track: str = "",
    work_mode: str = "",
) -> str:
    q = quote_plus((query or "").strip() or "data engineer")
    loc = quote_plus(
        _MARKET_LABEL.get((country or "").upper())
        or (MARKETS.get((country or "").upper()) or {}).get("label")
        or ""
    )
    extra = f"&location={loc}" if loc else ""
    if (track or "").strip().lower() == "freelance":
        extra += "&f_JT=C"
    if (work_mode or "").strip().lower() == "remote":
        extra += "&f_WT=2"
    return f"https://www.linkedin.com/jobs/search/?keywords={q}{extra}"


def _country_boards(country: str, q: str) -> list[dict[str, str]]:
    iso = (country or "").upper()
    enc = quote_plus(q)
    boards: dict[str, list[dict[str, str]]] = {
        "FR": [
            {"id": "fr-freework", "label": "Free-Work", "url": f"https://www.free-work.com/fr/tech-it/jobs?query={enc}", "country": "FR", "kind": "board"},
            {"id": "fr-ft", "label": "France Travail", "url": france_travail_search_url(q), "country": "FR", "kind": "api"},
            {"id": "fr-apec", "label": "APEC", "url": f"https://www.apec.fr/candidat/recherche-emploi.html/emploi?motsCles={enc}", "country": "FR", "kind": "board"},
            {"id": "fr-wttj", "label": "Welcome to the Jungle", "url": f"https://www.welcometothejungle.com/fr/jobs?query={enc}", "country": "FR", "kind": "board"},
            {"id": "fr-malt", "label": "Malt", "url": f"https://www.malt.fr/s?q={enc}", "country": "FR", "kind": "board"},
            {"id": "fr-cyb", "label": "ChooseYourBoss", "url": f"https://www.chooseyourboss.com/offres?q={enc}", "country": "FR", "kind": "board"},
            {"id": "fr-indeed", "label": "Indeed France", "url": f"https://fr.indeed.com/jobs?q={enc}", "country": "FR", "kind": "board"},
        ],
        "BE": [
            {"id": "be-ict", "label": "ICTjob", "url": f"https://www.ictjob.be/fr/search?q={enc}", "country": "BE", "kind": "board"},
            {"id": "be-vdab", "label": "VDAB", "url": f"https://www.vdab.be/vindeenjob/vacatures?trefwoord={enc}", "country": "BE", "kind": "board"},
            {"id": "be-forem", "label": "Le Forem", "url": f"https://www.leforem.be/chercher-un-emploi.html?q={enc}", "country": "BE", "kind": "board"},
            {"id": "be-actiris", "label": "Actiris", "url": f"https://www.actiris.brussels/fr/citoyens/offres-d-emploi/?q={enc}", "country": "BE", "kind": "board"},
            {"id": "be-jobat", "label": "Jobat", "url": f"https://www.jobat.be/fr/jobs?q={enc}", "country": "BE", "kind": "board"},
            {"id": "be-step", "label": "StepStone Belgium", "url": f"https://www.stepstone.be/jobs/{enc}", "country": "BE", "kind": "board"},
        ],
        "CH": [
            {"id": "ch-jobs", "label": "jobs.ch", "url": f"https://www.jobs.ch/en/vacancies/?term={enc}", "country": "CH", "kind": "board"},
            {"id": "ch-scout", "label": "JobScout24", "url": f"https://www.jobscout24.ch/en?q={enc}", "country": "CH", "kind": "board"},
            {"id": "ch-swissdev", "label": "SwissDevJobs", "url": f"https://swissdevjobs.ch/jobs?q={enc}", "country": "CH", "kind": "board"},
        ],
        "GB": [
            {"id": "gb-jobserve", "label": "JobServe", "url": f"https://www.jobserve.com/gb/en/JobSearch.aspx?shid={enc}", "country": "GB", "kind": "board"},
            {"id": "gb-cw", "label": "CWJobs", "url": f"https://www.cwjobs.co.uk/jobs/{enc}", "country": "GB", "kind": "board"},
            {"id": "gb-reed", "label": "Reed", "url": f"https://www.reed.co.uk/jobs/{enc}", "country": "GB", "kind": "board"},
            {"id": "gb-total", "label": "Totaljobs", "url": f"https://www.totaljobs.com/jobs/{enc}", "country": "GB", "kind": "board"},
            {"id": "gb-techno", "label": "Technojobs", "url": f"https://www.technojobs.co.uk/search.phtml?kw={enc}", "country": "GB", "kind": "board"},
            {"id": "gb-gov", "label": "GOV.UK Find a Job", "url": f"https://findajob.dwp.gov.uk/search?q={enc}", "country": "GB", "kind": "board"},
        ],
        "US": [
            {"id": "us-dice", "label": "Dice", "url": f"https://www.dice.com/jobs?q={enc}", "country": "US", "kind": "board"},
            {"id": "us-wellfound", "label": "Wellfound", "url": f"https://wellfound.com/role/l/{enc}", "country": "US", "kind": "board"},
            {"id": "us-builtin", "label": "Built In", "url": f"https://builtin.com/jobs?search={enc}", "country": "US", "kind": "board"},
            {"id": "us-usa", "label": "USAJOBS", "url": usajobs_search_url(q), "country": "US", "kind": "api"},
        ],
        "CA": [
            {"id": "ca-bank", "label": "Job Bank Canada", "url": f"https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring={enc}", "country": "CA", "kind": "board"},
            {"id": "ca-jobillico", "label": "Jobillico", "url": f"https://www.jobillico.com/search-jobs?k={enc}", "country": "CA", "kind": "board"},
            {"id": "ca-jobboom", "label": "Jobboom", "url": f"https://www.jobboom.com/en/jobs?q={enc}", "country": "CA", "kind": "board"},
            {"id": "ca-eluta", "label": "Eluta", "url": f"https://www.eluta.ca/search?q={enc}", "country": "CA", "kind": "board"},
        ],
        "DE": [
            {"id": "de-ba", "label": "Arbeitsagentur", "url": f"https://www.arbeitsagentur.de/jobsuche/suche?angebotsart=1&was={enc}", "country": "DE", "kind": "board"},
            {"id": "de-step", "label": "StepStone", "url": f"https://www.stepstone.de/jobs/{enc}", "country": "DE", "kind": "board"},
        ],
        "NL": [
            {"id": "nl-werk", "label": "Werk.nl", "url": f"https://www.werk.nl/werkzoekenden/vacatures?zoekwoord={enc}", "country": "NL", "kind": "board"},
        ],
        "AE": [
            {"id": "ae-bayt", "label": "Bayt UAE", "url": f"https://www.bayt.com/en/uae/jobs/q/{enc}/", "country": "AE", "kind": "board"},
            {"id": "ae-gulf", "label": "GulfTalent", "url": f"https://www.gulftalent.com/jobs?search={enc}", "country": "AE", "kind": "board"},
            {"id": "ae-dubai", "label": "Dubai Careers", "url": "https://dubaicareers.ae/", "country": "AE", "kind": "board"},
        ],
        "SA": [
            {"id": "sa-jadarat", "label": "Jadarat", "url": "https://jadarat.sa/", "country": "SA", "kind": "board"},
            {"id": "sa-bayt", "label": "Bayt KSA", "url": f"https://www.bayt.com/en/saudi-arabia/jobs/q/{enc}/", "country": "SA", "kind": "board"},
        ],
        "QA": [
            {"id": "qa-bayt", "label": "Bayt Qatar", "url": f"https://www.bayt.com/en/qatar/jobs/q/{enc}/", "country": "QA", "kind": "board"},
        ],
        "KW": [
            {"id": "kw-bayt", "label": "Bayt Kuwait", "url": f"https://www.bayt.com/en/kuwait/jobs/q/{enc}/", "country": "KW", "kind": "board"},
        ],
        "OM": [
            {"id": "om-bayt", "label": "Bayt Oman", "url": f"https://www.bayt.com/en/oman/jobs/q/{enc}/", "country": "OM", "kind": "board"},
        ],
        "BH": [
            {"id": "bh-bayt", "label": "Bayt Bahrain", "url": f"https://www.bayt.com/en/bahrain/jobs/q/{enc}/", "country": "BH", "kind": "board"},
        ],
        "MA": [
            {"id": "ma-rekrute", "label": "Rekrute", "url": f"https://www.rekrute.com/offres.html?q={enc}", "country": "MA", "kind": "board"},
            {"id": "ma-bayt", "label": "Bayt Morocco", "url": f"https://www.bayt.com/en/morocco/jobs/q/{enc}/", "country": "MA", "kind": "board"},
        ],
        "TN": [
            {"id": "tn-tanit", "label": "TanitJobs", "url": f"https://www.tanitjobs.com/jobs/?q={enc}", "country": "TN", "kind": "board"},
            {"id": "tn-bayt", "label": "Bayt Tunisia", "url": f"https://www.bayt.com/en/tunisia/jobs/q/{enc}/", "country": "TN", "kind": "board"},
        ],
        "ES": [
            {"id": "es-infojobs", "label": "InfoJobs", "url": f"https://www.infojobs.net/jobsearch/search-results/list.xhtml?keyword={enc}", "country": "ES", "kind": "board"},
        ],
        "IT": [
            {"id": "it-infojobs", "label": "InfoJobs Italy", "url": f"https://www.infojobs.it/jobsearch/search-results/list.xhtml?keyword={enc}", "country": "IT", "kind": "board"},
        ],
        "PT": [
            {"id": "pt-netemp", "label": "Net-Empregos", "url": f"https://www.net-empregos.com/pesquisa-empregos.asp?chaves={enc}", "country": "PT", "kind": "board"},
        ],
        "IE": [
            {"id": "ie-jobs", "label": "Jobs.ie", "url": f"https://www.jobs.ie/jobs?q={enc}", "country": "IE", "kind": "board"},
        ],
        "SE": [
            {"id": "se-af", "label": "Arbetsformedlingen", "url": f"https://arbetsformedlingen.se/platsbanken/annonser?q={enc}", "country": "SE", "kind": "board"},
        ],
        "PL": [
            {"id": "pl-pracuj", "label": "Pracuj.pl", "url": f"https://www.pracuj.pl/praca/{enc}", "country": "PL", "kind": "board"},
        ],
    }
    return [dict(row) for row in boards.get(iso, [])]


def official_search_pack(
    query: str,
    countries: list[str],
    track: str = "freelance",
    work_mode: str = "remote",
) -> list[dict[str, str]]:
    """Official search pages only. LinkedIn is opened in the user browser, never fetched."""
    role = (query or "").strip() or "Data Engineer"
    isos = [item.strip().upper() for item in countries if str(item).strip()]
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def push(row: dict[str, str]) -> None:
        key = str(row.get("id") or "")
        if not key or key in seen:
            return
        seen.add(key)
        out.append(row)

    for iso in isos:
        push(
            {
                "id": f"li-{iso}",
                "label": f"LinkedIn {_MARKET_LABEL.get(iso, iso)}",
                "url": linkedin_search_url(role, iso, track, work_mode),
                "country": iso,
                "kind": "linkedin",
            }
        )
    push(
        {
            "id": "remotive",
            "label": "Remotive",
            "url": remotive_search_url(role),
            "country": "REMOTE",
            "kind": "api",
        }
    )
    per_country = [_country_boards(iso, role) for iso in isos]
    depth = max((len(rows) for rows in per_country), default=0)
    for index in range(depth):
        for rows in per_country:
            if index < len(rows):
                push(rows[index])
    return out[:48]


def web_search_queries(
    *,
    titles: list[str],
    countries: list[str],
    track: str = "jobs",
    stack: list[str] | None = None,
) -> list[dict[str, str]]:
    """Preferential web queries per country. Discovery only, not a scrape plan."""
    roles = [t.strip() for t in titles if str(t).strip()] or ["Data Engineer"]
    role = roles[0]
    kind = "freelance" if track == "freelance" else "permanent"
    extra = " ".join((stack or [])[:3]).strip()
    per_country: list[list[dict[str, str]]] = []
    company_rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for iso in countries:
        market = market_of(iso)
        if not market:
            continue
        label = str(market.get("label") or iso)
        aliases = [str(item) for item in (market.get("aliases") or ())]
        cities = [str(item) for item in (market.get("cities") or ())]
        domains = list(market.get("domains") or [])
        langs = [str(item).lower()[:2] for item in (market.get("langs") or ()) if str(item).strip()]
        hiring = "(jobs OR hiring OR offre OR emploi OR careers)"
        company_query = f'"{role}" ({COMPANY_CAREER_OR}) {label}'
        company_key = company_query.lower()
        if company_key not in seen:
            seen.add(company_key)
            company_rows.append(
                {"country": iso.upper(), "query": company_query, "kind": "web", "family": "company"}
            )
        templates = [
            f'"{role}" {kind} {label} {hiring}',
            f'"{role}" contract {label} {hiring}' if track == "freelance" else f'"{role}" {label} remote {hiring}',
        ]
        for lang in langs:
            terms = SEARCH_LANG_TERMS.get(lang)
            if terms:
                templates.append(f'"{role}" {kind} {label} ({terms})')
        if extra:
            templates.append(f'"{role}" {extra} {label} {hiring}')
        if track == "freelance":
            for alias in aliases[:4]:
                templates.append(f'"{role}" {alias} {label} {hiring}')
            if cities:
                templates.append(f'"{role}" {aliases[0] if aliases else "contract"} {cities[0]} {hiring}')
            if iso == "FR":
                templates.append(f'"{role}" mission 6 months France {hiring}')
            if iso == "BE":
                templates.append(f'"{role}" freelance Bruxelles {hiring}')
                templates.append(f'"{role}" freelance Brussel {hiring}')
            if iso == "GB":
                templates.append(f'"{role}" Outside IR35 London remote {hiring}')
            if iso == "US":
                templates.append(f'"{role}" 1099 OR C2C OR "W2 contract" {hiring}')
            if iso == "CA" and cities:
                templates.append(f'"{role}" contractuel {cities[0]} {hiring}')
        elif iso == "FR":
            templates.append(f'"{role}" (CDI OR emploi) France {hiring}')
        for domain in domains:
            templates.append(f'site:{domain} "{role}" {hiring}')
        bucket: list[dict[str, str]] = []
        for query in templates:
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            bucket.append({"country": iso.upper(), "query": query, "kind": "web", "family": "web"})
        if bucket:
            per_country.append(bucket)
    out: list[dict[str, str]] = []
    depth = max((len(rows) for rows in per_country), default=0)
    for index in range(depth):
        for rows in per_country:
            if index < len(rows):
                out.append(rows[index])
    hiring = "(jobs OR hiring OR offre OR emploi OR careers)"
    ats_rows: list[dict[str, str]] = []
    for site in ATS_WEB_SITES:
        query = f'site:{site} "{role}" {hiring}'
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        ats_rows.append({"country": "ATS", "query": query, "kind": "web", "family": "ats"})
    li_row = {
        "country": "LI",
        "query": f"{role} {kind}",
        "kind": "linkedin_open",
        "url": linkedin_search_url(f"{role} {kind}", countries[0] if countries else "", track),
    }
    if len(out) > 90:
        out = out[:90]
    out.extend(company_rows)
    out.extend(ats_rows)
    out.append(li_row)
    return out


def web_search_run_pack(
    *,
    titles: list[str],
    countries: list[str],
    track: str = "jobs",
    stack: list[str] | None = None,
) -> list[dict[str, str]]:
    """Compact pack actually sent to the search provider. LinkedIn stays open-in-browser."""
    queries = web_search_queries(titles=titles, countries=countries, track=track, stack=stack)
    web = [row for row in queries if row.get("kind") == "web"]
    ats_rows = [row for row in web if row.get("family") == "ats"]
    company_rows = [row for row in web if row.get("family") == "company"]
    site_rows = [
        row
        for row in web
        if str(row.get("query") or "").startswith("site:") and row.get("family") != "ats"
    ]
    general = [
        row
        for row in web
        if not str(row.get("query") or "").startswith("site:") and row.get("family") != "company"
    ]
    picked: list[dict[str, str]] = []
    seen: set[str] = set()

    def take(row: dict[str, str]) -> None:
        key = str(row.get("query") or "").lower()
        if not key or key in seen or len(picked) >= WEB_SEARCH_PACK_LIMIT:
            return
        seen.add(key)
        picked.append(row)

    ats_budget = min(WEB_SEARCH_ATS_SLOTS, len(ats_rows))
    company_budget = min(WEB_SEARCH_COMPANY_SLOTS, len(company_rows))
    country_budget = max(2, WEB_SEARCH_PACK_LIMIT - ats_budget - company_budget)
    used_countries: set[str] = set()
    country_taken = 0
    for row in site_rows:
        iso = str(row.get("country") or "")
        if iso in used_countries or country_taken >= country_budget:
            continue
        before = len(picked)
        take(row)
        if len(picked) > before:
            used_countries.add(iso)
            country_taken += 1
    for row in ats_rows[:ats_budget]:
        take(row)
    for row in company_rows[:company_budget]:
        take(row)
    for row in site_rows:
        take(row)
    for row in general:
        take(row)
    return picked[:WEB_SEARCH_PACK_LIMIT]


def scrape_search_queries(*, titles: list[str], countries: list[str], track: str = "jobs") -> list[dict[str, str]]:
    """Queries aimed at open hosts the scrape tool is allowed to fetch."""
    role = next((str(item).strip() for item in titles if str(item).strip()), "Data Engineer")
    kind = "freelance" if track == "freelance" else "jobs"
    isos = {str(item).strip().upper() for item in countries if str(item).strip()}
    sites: list[str] = []
    seen: set[str] = set()
    for host in OPEN_SCRAPE_HOSTS:
        if host == "usajobs.gov" and "US" not in isos:
            continue
        if host == "francetravail.fr" and "FR" not in isos:
            continue
        site = OPEN_SCRAPE_QUERY_HOSTS.get(host, host)
        if site in seen:
            continue
        seen.add(site)
        sites.append(site)
    rows: list[dict[str, str]] = []
    for site in sites:
        if "francetravail.fr" in site:
            country = "FR"
        elif "usajobs" in site:
            country = "US"
        else:
            country = "REMOTE"
        rows.append(
            {
                "country": country,
                "query": f'site:{site} "{role}" {kind}',
                "kind": "scrape_open",
                "site": site,
            }
        )
    return rows
