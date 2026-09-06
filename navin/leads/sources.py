"""Catalog of lead sources. LinkedIn is a reference only, never scraped."""

from __future__ import annotations

STAGES = (
    "new",
    "qualified",
    "contacted",
    "replied",
    "meeting",
    "opportunity",
    "won",
)

PROVIDERS = (
    {
        "id": "web",
        "name": "Web Search + company sites",
        "tier": 1,
        "region": "world",
        "kind": "free",
        "docs": "https://navin.live/docs",
        "notes": "Wired: web_search then scrape public /about /team /contact. Never LinkedIn.",
    },
    {
        "id": "sirene",
        "name": "INSEE / SIRENE",
        "tier": 1,
        "region": "FR",
        "kind": "free",
        "docs": "https://recherche-entreprises.api.gouv.fr/docs/",
        "notes": "Every French company. Officers on the public search. No key.",
    },
    {
        "id": "pappers",
        "name": "Pappers",
        "tier": 1,
        "region": "FR",
        "kind": "byok",
        "docs": "https://www.pappers.fr/api/documentation",
        "notes": "SIREN, officers, accounts. Professional email for free credits.",
    },
    {
        "id": "companies_house",
        "name": "Companies House",
        "tier": 1,
        "region": "GB",
        "kind": "byok",
        "docs": "https://developer.company-information.service.gov.uk/",
        "notes": "Official UK register. Free API key.",
    },
    {
        "id": "places",
        "name": "Google Places",
        "tier": 2,
        "region": "world",
        "kind": "byok",
        "docs": "https://developers.google.com/maps/documentation/places/web-service",
        "notes": "Local SMBs: agencies, clinics, shops.",
    },
    {
        "id": "apollo",
        "name": "Apollo",
        "tier": 1,
        "region": "world",
        "kind": "byok",
        "docs": "https://docs.apollo.io/",
        "notes": "Best general people + company search. User pays Apollo.",
    },
    {
        "id": "pdl",
        "name": "People Data Labs",
        "tier": 2,
        "region": "world",
        "kind": "byok",
        "docs": "https://docs.peopledatalabs.com/",
        "notes": "Second people engine after Apollo.",
    },
    {
        "id": "hunter",
        "name": "Hunter",
        "tier": 1,
        "region": "world",
        "kind": "byok",
        "docs": "https://hunter.io/api-documentation/v2",
        "notes": "Domain search, email finder, verifier. Stop when verified.",
    },
    {
        "id": "crunchbase",
        "name": "Crunchbase",
        "tier": 3,
        "region": "world",
        "kind": "byok",
        "docs": "https://data.crunchbase.com/docs",
        "notes": "Funding and investors. Hunt + website when a key is set.",
    },
    {
        "id": "opencorporates",
        "name": "OpenCorporates",
        "tier": 3,
        "region": "world",
        "kind": "byok",
        "docs": "https://api.opencorporates.com/documentation/API-Reference",
        "notes": "International company registers. Hunt + officers when a key is set.",
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "tier": 1,
        "region": "world",
        "kind": "reference",
        "docs": "https://www.linkedin.com",
        "notes": "Identity and deep-link only. Navin never scrapes linkedin.com.",
    },
)


def catalog() -> list[dict[str, object]]:
    return [dict(row) for row in PROVIDERS]
