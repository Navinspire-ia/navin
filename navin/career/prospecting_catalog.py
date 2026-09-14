# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Company sourcing directory. A directory entry is not an API connector."""

import re
from urllib.parse import urlsplit

from navin.career.mission_platforms import mission_platform_catalog

def profile_navigation_url(url):
    """Restore the working public host without changing profile identity or path."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme in {"http", "https"} and parsed.netloc == "lesbonsfreelances.com"):
            return parsed._replace(scheme="https", netloc="www.lesbonsfreelances.com").geturl()
    except ValueError:
        pass
    return url

PLATFORMS = [
    ("linkedin", "LinkedIn", "linkedin.com", "International", "partner", "/in/"),
    ("freework", "Turnover-IT / Free-Work", "free-work.com", "FR, GB", "partner", ""),
    ("cvlibrary", "CV-Library", "cv-library.co.uk", "GB", "partner", ""),
    ("resumelibrary", "Resume-Library", "resume-library.com", "US", "partner", ""),
    ("dice", "Dice", "dice.com", "US", "partner", ""),
    ("seek", "SEEK Talent Search", "seek.com.au", "AU", "restricted", ""),
    ("malt", "Malt", "malt.fr", "Europe", "web", "/profile/"),
    ("freelancermap", "freelancermap", "freelancermap.com", "Europe, CH, International", "web", "/profile/"),
    ("freelancede", "freelance.de", "freelance.de", "DE", "web", "/Freelancer/"),
    ("jellow", "Jellow", "jellow.nl", "BE, NL", "manual", ""),
    ("lesbonsfreelances", "LesBonsFreelances", "lesbonsfreelances.com", "FR", "web", "/freelance/"),
    ("freelanceinformatique", "Freelance Informatique", "freelance-informatique.fr", "FR", "manual", ""),
    ("peopleperhour", "PeoplePerHour", "peopleperhour.com", "GB, International", "web", "/freelancer/"),
    ("workhoppers", "Workhoppers", "workhoppers.com", "CA", "manual", ""),
    ("contra", "Contra", "contra.com", "International", "manual", ""),
    ("freelancer", "Freelancer.com", "freelancer.com", "International", "web", "/u/"),
    ("upwork", "Upwork", "upwork.com", "International", "web", "/freelancers/"),
    ("prounity", "ProUnity", "pro-unity.com", "Europe", "partner", ""),
    ("connecting_expertise", "Connecting-Expertise", "connecting-expertise.com", "Europe", "partner", ""),
    ("freelancers_lu", "Freelancers.lu", "freelancers.lu", "LU FR BE DE", "public_listing", "/fr/freelancers/"),
    ("expert360", "Expert360", "expert360.com", "AU NZ", "restricted", ""),
    ("yunojuno", "YunoJuno", "yunojuno.com", "GB", "restricted", ""),
    ("lehibou", "LeHibou", "lehibou.com", "FR", "partner", ""),
    ("comet", "Comet", "comet.co", "FR", "manual", ""),
    ("apec", "APEC / Candidapec", "apec.fr", "FR", "manual", ""),
    ("hellowork", "HelloWork", "hellowork.com", "FR", "manual", ""),
    ("francetravail", "France Travail", "francetravail.fr", "FR", "manual", ""),
    ("ictjob", "ICTjob.be", "ictjob.be", "BE", "manual", ""),
    ("jobroom", "Job-Room", "job-room.ch", "CH", "manual", ""),
    ("adem", "ADEM JobBoard", "adem.public.lu", "LU", "manual", ""),
    ("reed", "Reed CV Search", "reed.co.uk", "GB", "manual", ""),
    ("wellfound", "Wellfound", "wellfound.com", "US, International", "manual", ""),
    ("jobbank", "Guichet-Emplois", "jobbank.gc.ca", "CA", "manual", ""),
]

B2B_PROFILE_PRIORITY = ("malt", "prounity", "connecting_expertise", "freelancermap", "upwork", "freelancer",
                        "expert360", "yunojuno", "freelancers_lu", "lehibou")

# Public indexed job pages, queried through the user's search provider. These
# entries do not claim access to a platform's private API or candidate database.
INDEXED_MISSIONS = [
    ("bayt", "Bayt", "bayt.com", "SA OM AE BH QA KW MA"),
    ("gulftalent", "GulfTalent", "gulftalent.com", "SA OM AE BH QA KW"),
    ("naukrigulf", "NaukriGulf", "naukrigulf.com", "SA OM AE BH QA KW"),
    ("rekrute", "ReKrute", "rekrute.com", "MA"),
    ("emploi_ma", "Emploi.ma", "emploi.ma", "MA"),
    ("indeed", "Indeed", "indeed.com", "International"),
    ("hellowork_jobs", "HelloWork", "hellowork.com", "FR"),
    ("apec_jobs", "APEC", "apec.fr", "FR"),
    ("francetravail_jobs", "France Travail", "candidat.francetravail.fr", "FR"),
    ("welcometojungle", "Welcome to the Jungle", "welcometothejungle.com", "Europe US"),
    ("freelancede_jobs", "freelance.de", "freelance.de", "DE CH AT"),
    ("reed_jobs", "Reed", "reed.co.uk", "GB"),
    ("cvlibrary_jobs", "CV-Library", "cv-library.co.uk", "GB"),
    ("dice_jobs", "Dice", "dice.com", "US"),
    ("seek_jobs", "SEEK", "seek.com.au", "AU"),
    ("jobbank_jobs", "Guichet-Emplois", "jobbank.gc.ca", "CA"),
    ("wellfound_jobs", "Wellfound", "wellfound.com", "International"),
]


def mission_catalog():
    native = [
        ("linkedin", "LinkedIn Jobs", "International", "", "https://www.linkedin.com/jobs/"),
        ("google_jobs", "Google Jobs", "International", "serpapi", "https://serpapi.com/google-jobs-api"),
        ("brave_jobs", "Brave Search", "International", "brave", "https://search.brave.com"),
        ("public_jobs", "Recherche web", "International", "", "https://duckduckgo.com"),
        ("freework", "Free-Work", "FR GB", "", "https://www.free-work.com"),
        ("collective", "Collective.work", "FR BE CH LU MA TN ES IT NL DE GB", "", "https://app.collective.work/talent/jobs"),
        ("freelancescope", "FreelanceScope", "FR", "", "https://www.freelancescope.fr/missions"),
        ("remotive", "Remotive", "International", "", "https://remotive.com"),
    ]
    rows = [{"id": key, "name": name, "markets": markets, "provider": provider, "url": url, "mode": "feed"}
            for key, name, markets, provider, url in native]
    rows.extend(mission_platform_catalog())
    for key, name in (("jobicy", "Jobicy"), ("remoteok", "Remote OK"), ("himalayas", "Himalayas"),
                      ("weworkremotely", "We Work Remotely"), ("arbeitnow", "Arbeitnow"), ("hn-hiring", "Hacker News Hiring")):
        rows.append({"id": key, "name": name, "markets": "International", "provider": "", "url": "", "mode": "feed"})
    rows.extend({"id": key, "name": name, "markets": markets, "provider": "serpapi", "url": "https://" + domain,
                 "mode": "indexed"} for key, name, domain, markets in INDEXED_MISSIONS)
    return rows


def platform_catalog():
    return [
        {"id": key, "name": name, "url": profile_navigation_url(f"https://{domain}"), "markets": markets,
         "access": access, "indexed_profiles": bool(path), "profile_path": path, "domain": domain,
         "profile_mode": "public_listing" if access == "public_listing" else "public_search" if path else "account",
         "domains": [domain, "malt.com"] if key == "malt" else [domain, "freelancer.com.au"] if key == "freelancer" else [domain],
         "priority": 1 if key in B2B_PROFILE_PRIORITY else 2}
        for key, name, domain, markets, access, path in PLATFORMS
    ]


def is_profile_url(url, platform):
    try:
        parsed = urlsplit(url)
        path = platform.get("profile_path", "")
        return (bool(path) and parsed.scheme in {"https", "http"} and not parsed.username and not parsed.password
                and any(parsed.hostname == domain or (parsed.hostname or "").endswith("." + domain)
                        for domain in platform.get("domains", [platform["domain"]]))
                and bool(re.fullmatch(re.escape(path) + r"[^/]+/?", parsed.path, re.I)))
    except ValueError:
        return False


def default_mission_sources(track="both", countries=None):
    countries = set(countries or [])
    rows = [row for row in mission_catalog() if not row["provider"]]
    if track == "freelance":
        rows = [row for row in rows if row.get("priority") == 1 or row["id"] in {"freework", "collective", "freelancescope"}]
    elif track == "jobs":
        rows = [row for row in rows if row.get("opportunity_kind") != "freelance"]
    return [row["id"] for row in rows if not countries or "International" in row["markets"]
            or "Europe" in row["markets"] or countries.intersection(row["markets"].split())]
