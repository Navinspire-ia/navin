# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Company sourcing directory. A directory entry is not an API connector."""

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
    ("upwork", "Upwork", "upwork.com", "International", "restricted", ""),
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
    ("freelancermap_jobs", "freelancermap", "freelancermap.com", "International"),
    ("freelancede_jobs", "freelance.de", "freelance.de", "DE CH AT"),
    ("reed_jobs", "Reed", "reed.co.uk", "GB"),
    ("cvlibrary_jobs", "CV-Library", "cv-library.co.uk", "GB"),
    ("dice_jobs", "Dice", "dice.com", "US"),
    ("seek_jobs", "SEEK", "seek.com.au", "AU"),
    ("jobbank_jobs", "Guichet-Emplois", "jobbank.gc.ca", "CA"),
    ("wellfound_jobs", "Wellfound", "wellfound.com", "International"),
    ("freelancer_jobs", "Freelancer.com", "freelancer.com", "International"),
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
    for key, name in (("jobicy", "Jobicy"), ("remoteok", "Remote OK"), ("himalayas", "Himalayas"),
                      ("weworkremotely", "We Work Remotely"), ("arbeitnow", "Arbeitnow"), ("hn-hiring", "Hacker News Hiring")):
        rows.append({"id": key, "name": name, "markets": "International", "provider": "", "url": "", "mode": "feed"})
    rows.extend({"id": key, "name": name, "markets": markets, "provider": "serpapi", "url": "https://" + domain,
                 "mode": "indexed"} for key, name, domain, markets in INDEXED_MISSIONS)
    return rows


def platform_catalog():
    return [
        {"id": key, "name": name, "url": f"https://{domain}", "markets": markets,
         "access": access, "indexed_profiles": bool(path), "profile_path": path, "domain": domain}
        for key, name, domain, markets, access, path in PLATFORMS
    ]
