# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Official open tender portals and country-preferential web search.

Priority: Official API / Open Data, then the national portal, then ministries
and SOEs, then international IFIs, then a targeted web_search + scrape net
on official hosts. Paid aggregators are out of scope.
"""

from __future__ import annotations

from typing import Any

# Access: open = public HTML/search without a paid subscription.
# ingest: api | rss | html | search (web_search + scrape on official hosts).
SOURCES: tuple[dict[str, Any], ...] = (
    {
        "id": "ted",
        "name": "TED - Tenders Electronic Daily",
        "country": "EU",
        "zone": "europe",
        "priority": "P0",
        "ingest": "api",
        "url": "https://ted.europa.eu/",
        "api": "https://api.ted.europa.eu/v3/notices/search",
        "notes": "Official EU OJ S. HTML is behind AWS WAF captcha. Ingest uses the public search API.",
    },
    {
        "id": "place",
        "name": "PLACE - Marches publics de l'Etat",
        "country": "FR",
        "zone": "europe",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.marches-publics.gouv.fr/",
        "notes": "French central government e-procurement. Dual collect with BOAMP: TED/BOAMP APIs first, PLACE scrape for notices that never reach the bulletin.",
    },
    {
        "id": "boamp",
        "name": "BOAMP",
        "country": "FR",
        "zone": "europe",
        "priority": "P1",
        "ingest": "api",
        "url": "https://www.boamp.fr/",
        "api": "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records",
        "notes": "French official bulletin. Ingest uses the DILA open-data API.",
    },
    {
        "id": "find-a-tender",
        "name": "Find a Tender",
        "country": "GB",
        "zone": "europe",
        "priority": "P1",
        "ingest": "api",
        "url": "https://www.find-tender.service.gov.uk/",
        "api": "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages",
        "notes": "UK above-threshold notices after Brexit. Official OCDS API, no key.",
    },
    {
        "id": "contracts-finder",
        "name": "Contracts Finder",
        "country": "GB",
        "zone": "europe",
        "priority": "P1",
        "ingest": "api",
        "url": "https://www.contractsfinder.service.gov.uk/",
        "api": "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
        "notes": "UK below-threshold and wider public contracts. Official OCDS search.",
    },
    {
        "id": "sam-gov",
        "name": "SAM.gov Contract Opportunities",
        "country": "US",
        "zone": "americas",
        "priority": "P0",
        "ingest": "api",
        "url": "https://sam.gov/content/opportunities",
        "api": "https://api.sam.gov/opportunities/v2/search",
        "notes": "US federal opportunities. Public v2 search API. Connect a free SAM.gov key like any other provider key.",
    },
    {
        "id": "canadabuys",
        "name": "CanadaBuys",
        "country": "CA",
        "zone": "americas",
        "priority": "P1",
        "ingest": "opendata",
        "url": "https://canadabuys.canada.ca/",
        "api": "https://canadabuys.canada.ca/opendata/pub/newTenderNotice-nouvelAvisAppelOffres.csv",
        "notes": "Government of Canada procurement. Ingest uses the official open-data CSV.",
    },
    {
        "id": "etimad",
        "name": "Etimad Tenders",
        "country": "SA",
        "zone": "gcc",
        "priority": "P1",
        "ingest": "html",
        "url": "https://tenders.etimad.sa/",
        "notes": "Saudi national e-tendering. Structured search by activity, agency, region, deadline.",
    },
    {
        "id": "uae-procurement",
        "name": "UAE Government Procurement",
        "country": "AE",
        "zone": "gcc",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.mof.gov.ae/",
        "notes": "UAE federal procurement entry point.",
    },
    {
        "id": "monaqasat",
        "name": "Monaqasat",
        "country": "QA",
        "zone": "gcc",
        "priority": "P1",
        "ingest": "html",
        "url": "https://monaqasat.mof.gov.qa/",
        "notes": "Qatar public tenders. The official TLS certificate often fails verification from some networks.",
    },
    {
        "id": "bahrain-tender-board",
        "name": "Bahrain Tender Board",
        "country": "BH",
        "zone": "gcc",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.tenderboard.gov.bh/",
        "notes": "Kingdom of Bahrain tender board.",
    },
    {
        "id": "oman-tender-board",
        "name": "Oman Tender Board",
        "country": "OM",
        "zone": "gcc",
        "priority": "P2",
        "ingest": "html",
        "url": "https://etendering.tenderboard.gov.om/",
        "notes": "Sultanate of Oman e-tendering.",
    },
    {
        "id": "capt-kuwait",
        "name": "CAPT Kuwait",
        "country": "KW",
        "zone": "gcc",
        "priority": "P2",
        "ingest": "html",
        "url": "https://capt.gov.kw/",
        "notes": "Kuwait Central Agency for Public Tenders.",
    },
    {
        "id": "haicop",
        "name": "HAICOP / TUNEPS",
        "country": "TN",
        "zone": "maghreb",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.marchespublics.gov.tn/",
        "notes": "Tunisian national public procurement: notices, results, annual plans.",
    },
    {
        "id": "maroc-marches",
        "name": "Portail Marocain des Marches Publics",
        "country": "MA",
        "zone": "maghreb",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.marchespublics.gov.ma/",
        "notes": "Moroccan consultations, results and programmes previsionnels.",
    },
    {
        "id": "ungm",
        "name": "UN Global Marketplace",
        "country": "INTL",
        "zone": "international",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.ungm.org/Public/Notice",
        "notes": "UN agencies and many international organisations. Free public search. Cloudflare may challenge bots.",
    },
    {
        "id": "world-bank",
        "name": "World Bank Procurement Notices",
        "country": "INTL",
        "zone": "international",
        "priority": "P0",
        "ingest": "api",
        "url": "https://projects.worldbank.org/en/projects-operations/procurement-search",
        "api": "https://search.worldbank.org/api/v2/procnotices",
        "notes": "Current, upcoming and historical notices. Strong Africa / MENA coverage.",
    },
    {
        "id": "world-bank-business",
        "name": "World Bank Business Opportunities",
        "country": "INTL",
        "zone": "international",
        "priority": "P3",
        "ingest": "html",
        "url": "https://projects.worldbank.org/en/projects-operations/opportunities",
        "notes": "Current, upcoming and potential World Bank-financed work.",
    },
    {
        "id": "afdb",
        "name": "African Development Bank Procurement",
        "country": "INTL",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.afdb.org/en/projects-and-operations/procurement",
        "notes": "AfDB-financed projects across Africa.",
    },
    {
        "id": "ebrd",
        "name": "EBRD Procurement",
        "country": "INTL",
        "zone": "international",
        "priority": "P3",
        "ingest": "html",
        "url": "https://www.ebrd.com/work-with-us/procurement/notices.html",
        "notes": "European Bank for Reconstruction and Development notices.",
    },
    {
        "id": "adb",
        "name": "Asian Development Bank Procurement",
        "country": "INTL",
        "zone": "international",
        "priority": "P3",
        "ingest": "html",
        "url": "https://www.adb.org/business/operational-procurement",
        "notes": "ADB-financed projects across Asia and the Pacific.",
    },
    {
        "id": "iadb",
        "name": "Inter-American Development Bank Procurement",
        "country": "INTL",
        "zone": "americas",
        "priority": "P3",
        "ingest": "html",
        "url": "https://www.iadb.org/en/how-we-can-work-together/procurement",
        "notes": "IDB-financed projects in Latin America and the Caribbean.",
    },
    {
        "id": "isdb",
        "name": "Islamic Development Bank Procurement",
        "country": "INTL",
        "zone": "international",
        "priority": "P3",
        "ingest": "html",
        "url": "https://www.isdb.org/project-procurement",
        "notes": "IsDB-financed projects, strong GCC / Africa / Asia coverage.",
    },
    {
        "id": "senegal-armds",
        "name": "Senegal ARMP / marches publics",
        "country": "SN",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.armp.sn/",
        "notes": "Autorite de Regulation des Marches Publics. marchespublics.sn is often unreachable from abroad.",
    },
    {
        "id": "cote-ivoire-sigmap",
        "name": "Cote d'Ivoire marches publics",
        "country": "CI",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.marchespublics.ci/",
        "notes": "Ivorian public consultations and results.",
    },
    {
        "id": "sa-etenders",
        "name": "South Africa eTenders",
        "country": "ZA",
        "zone": "africa",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.etenders.gov.za/",
        "notes": "National Treasury eTender Publication Portal.",
    },
    {
        "id": "kenya-tenders",
        "name": "Kenya PPIP",
        "country": "KE",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://tenders.go.ke/",
        "notes": "Public Procurement Information Portal. Active notices, Excel export and OCDS identifiers. No invented API.",
    },
    {
        "id": "egypt-etenders",
        "name": "Egypt e-Tenders",
        "country": "EG",
        "zone": "africa",
        "priority": "P2",
        "ingest": "html",
        "url": "https://etenders.gov.eg/",
        "notes": "Egyptian government e-procurement. Often unreachable from abroad.",
    },
    {
        "id": "rwanda-umucyo",
        "name": "Rwanda Umucyo",
        "country": "RW",
        "zone": "africa",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.umucyo.gov.rw/",
        "notes": "Rwandan e-procurement.",
    },
    {
        "id": "australia-tenders",
        "name": "AusTender",
        "country": "AU",
        "zone": "oceania",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.tenders.gov.au/",
        "notes": "Australian Government tenders.",
    },
    {
        "id": "nz-gets",
        "name": "New Zealand GETS",
        "country": "NZ",
        "zone": "oceania",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.gets.govt.nz/",
        "notes": "Government Electronic Tenders Service.",
    },
    {
        "id": "india-cppp",
        "name": "India Central Public Procurement Portal",
        "country": "IN",
        "zone": "asia",
        "priority": "P2",
        "ingest": "html",
        "url": "https://eprocure.gov.in/",
        "notes": "Indian central government e-procurement.",
    },
    {
        "id": "singapore-gebiz",
        "name": "Singapore GeBIZ",
        "country": "SG",
        "zone": "asia",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.gebiz.gov.sg/",
        "notes": "Singapore government electronic business.",
    },
    {
        "id": "simap",
        "name": "Switzerland SIMAP",
        "country": "CH",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.simap.ch/",
        "notes": "Swiss public procurement information system.",
    },
    {
        "id": "tenderned",
        "name": "Netherlands TenderNed",
        "country": "NL",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.tenderned.nl/",
        "notes": "Dutch national e-procurement.",
    },
    {
        "id": "spain-pcsp",
        "name": "Spain Plataforma de Contratacion",
        "country": "ES",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://contrataciondelsectorpublico.gob.es/",
        "notes": "Spanish public-sector contracting platform. TED covers EU-threshold notices.",
    },
    {
        "id": "germany-evergabe",
        "name": "Germany e-Vergabe",
        "country": "DE",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.evergabe-online.de/",
        "notes": "German federal e-procurement. Cookie gate returns 400 to scripted clients.",
    },
    {
        "id": "italy-acquistinrete",
        "name": "Italy Acquisti in Rete PA",
        "country": "IT",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.acquistinretepa.it/",
        "notes": "Italian public e-marketplace (Consip).",
    },
    {
        "id": "ireland-etenders",
        "name": "Ireland eTenders",
        "country": "IE",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.etenders.gov.ie/",
        "notes": "Irish public procurement.",
    },
    {
        "id": "portugal-base",
        "name": "Portugal BASE",
        "country": "PT",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.base.gov.pt/Base4/pt/",
        "notes": "Portuguese public contracts portal (Base 4).",
    },
    {
        "id": "norway-doffin",
        "name": "Norway Doffin",
        "country": "NO",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.doffin.no/",
        "notes": "Norwegian database for public procurement.",
    },
    {
        "id": "belgium-eproc",
        "name": "Belgium e-Procurement",
        "country": "BE",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.publicprocurement.be/",
        "notes": "Belgian federal public procurement. Dual collect: TED API first, then this portal for national notices.",
    },
    {
        "id": "brazil-compras",
        "name": "Brazil Compras.gov",
        "country": "BR",
        "zone": "americas",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.gov.br/compras",
        "notes": "Brazilian federal procurement.",
    },
    {
        "id": "chile-mercadopublico",
        "name": "Chile Mercado Publico",
        "country": "CL",
        "zone": "americas",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.mercadopublico.cl/",
        "notes": "Chilean public marketplace.",
    },
    {
        "id": "mexico-compranet",
        "name": "Mexico Compras MX",
        "country": "MX",
        "zone": "americas",
        "priority": "P2",
        "ingest": "html",
        "url": "https://comprasmx.buengobierno.gob.mx/",
        "notes": "Federal public contracting platform. CompraNet was renamed Compras MX.",
    },
    {
        "id": "jordan-joneps",
        "name": "Jordan JONEPS",
        "country": "JO",
        "zone": "mena",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.joneps.gov.jo/",
        "notes": "Jordan National E-Procurement System.",
    },
    {
        "id": "luxembourg-pmp",
        "name": "Luxembourg Portail des marches publics",
        "country": "LU",
        "zone": "europe",
        "priority": "P1",
        "ingest": "html",
        "url": "https://pmp.b2g.etat.lu/",
        "notes": "Dual collect with TED: national notices and dossiers on the official PMP.",
    },
    {
        "id": "sweden-uhmynd",
        "name": "Sweden Upphandlingsmyndigheten",
        "country": "SE",
        "zone": "europe",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.upphandlingsmyndigheten.se/",
        "notes": "Dual collect with TED: national Swedish procurement guidance and notice entry points.",
    },
    {
        "id": "austria-usp",
        "name": "Austria USP / public procurement",
        "country": "AT",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.usp.gv.at/",
        "notes": "Austrian business service portal. EU-threshold notices come from TED.",
    },
    {
        "id": "poland-ezamowienia",
        "name": "Poland e-Zamowienia",
        "country": "PL",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://ezamowienia.gov.pl/",
        "notes": "Polish national e-procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "czech-vvz",
        "name": "Czech VVZ",
        "country": "CZ",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.vestnikverejnychzakazek.cz/",
        "notes": "Czech public contracts journal. TED covers EU-threshold notices.",
    },
    {
        "id": "romania-sicap",
        "name": "Romania SEAP / SICAP",
        "country": "RO",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.e-licitatie.ro/",
        "notes": "Romanian electronic public procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "greece-promitheus",
        "name": "Greece Promitheus",
        "country": "GR",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.promitheus.gov.gr/",
        "notes": "Greek national e-procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "croatia-eojn",
        "name": "Croatia EOJN",
        "country": "HR",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://eojn.nn.hr/",
        "notes": "Croatian electronic public procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "slovenia-enarocanje",
        "name": "Slovenia eNarocanje",
        "country": "SI",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.enarocanje.si/",
        "notes": "Slovenian e-procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "slovakia-uvo",
        "name": "Slovakia UVO",
        "country": "SK",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.uvo.gov.sk/",
        "notes": "Slovak Public Procurement Office. TED covers EU-threshold notices.",
    },
    {
        "id": "hungary-ekr",
        "name": "Hungary EKR",
        "country": "HU",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://ekr.gov.hu/",
        "notes": "Hungarian electronic public procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "bulgaria-eop",
        "name": "Bulgaria CAIS EOP",
        "country": "BG",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://app.eop.bg/",
        "notes": "Bulgarian electronic public procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "denmark-udbud",
        "name": "Denmark Udbud",
        "country": "DK",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.udbud.dk/",
        "notes": "Danish public procurement notices. TED covers EU-threshold notices.",
    },
    {
        "id": "finland-hilma",
        "name": "Finland Hilma",
        "country": "FI",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.hankintailmoitukset.fi/",
        "notes": "Finnish public procurement notices. TED covers EU-threshold notices.",
    },
    {
        "id": "estonia-riigihanked",
        "name": "Estonia Riigihanked",
        "country": "EE",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://riigihanked.riik.ee/",
        "notes": "Estonian public procurement register. TED covers EU-threshold notices.",
    },
    {
        "id": "latvia-eis",
        "name": "Latvia IUB / EIS",
        "country": "LV",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.iub.gov.lv/",
        "notes": "IUB publishes notices. EIS (eis.gov.lv) is the e-submission system. TED covers EU-threshold notices.",
    },
    {
        "id": "lithuania-cvpp",
        "name": "Lithuania CVP IS",
        "country": "LT",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://viesiejipirkimai.lt/",
        "notes": "New CVP IS since 2024-12-01. TED covers EU-threshold notices.",
    },
    {
        "id": "cyprus-eproc",
        "name": "Cyprus eProcurement",
        "country": "CY",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.eprocurement.gov.cy/",
        "notes": "Cypriot e-procurement. TED covers EU-threshold notices.",
    },
    {
        "id": "iceland-utbod",
        "name": "Iceland national procurement",
        "country": "IS",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://www.utbodsvefur.is/",
        "notes": "Icelandic procurement notices. EEA notices also flow through TED.",
    },
    {
        "id": "italy-anac",
        "name": "Italy ANAC BDNCP",
        "country": "IT",
        "zone": "europe",
        "priority": "P2",
        "ingest": "html",
        "url": "https://dati.anticorruzione.it/",
        "notes": "ANAC open contracting data. No dedicated fetcher yet; TED covers EU-threshold notices.",
    },
    {
        "id": "algeria-marches",
        "name": "Algeria DGB / marches publics",
        "country": "DZ",
        "zone": "maghreb",
        "priority": "P1",
        "ingest": "html",
        "url": "https://dgb.mf.gov.dz/?page_id=2515",
        "notes": "Ministry of Finance page for the electronic public procurement portal. marchespublics.gov.dz has no public DNS yet. Official-host search covers mf.gov.dz and gov.dz.",
    },
    {
        "id": "tuneps",
        "name": "Tunisia TUNEPS",
        "country": "TN",
        "zone": "maghreb",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.tuneps.tn/",
        "notes": "Tunisian e-procurement. Some operations need an account. Notices also on HAICOP.",
    },
    {
        "id": "cameroon-armp",
        "name": "Cameroon ARMP",
        "country": "CM",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.armp.cm/",
        "notes": "Public notices with title, buyer, type, region, amount, dates and documents.",
    },
    {
        "id": "benin-marches",
        "name": "Benin marches publics / ARMP",
        "country": "BJ",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://marches-publics.bj/",
        "notes": "Official notice board. ARMP (armp.bj) is the regulator.",
    },
    {
        "id": "cote-ivoire-sigomap",
        "name": "Cote d'Ivoire SIGOMAP",
        "country": "CI",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://sigomap.gouv.ci/",
        "notes": "Ivorian e-procurement complement to marchespublics.ci.",
    },
    {
        "id": "mali-dgmp",
        "name": "Mali DGMP-DSP",
        "country": "ML",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.dgmp.gouv.ml/",
        "notes": "Malian electronic public procurement.",
    },
    {
        "id": "niger-marches",
        "name": "Niger marches publics",
        "country": "NE",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.marchespublics.ne/",
        "notes": "Niger public contracts and DSP portal.",
    },
    {
        "id": "burkina-arcop",
        "name": "Burkina Faso ARCOP",
        "country": "BF",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.arcop.bf/",
        "notes": "Burkina Faso public procurement authority and notices.",
    },
    {
        "id": "ghana-ghaneps",
        "name": "Ghana GHANEPS",
        "country": "GH",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://www.ghaneps.gov.gh/",
        "notes": "Ghana Electronic Procurement System.",
    },
    {
        "id": "nigeria-nocopo",
        "name": "Nigeria NOCOPO / BPP",
        "country": "NG",
        "zone": "africa",
        "priority": "P1",
        "ingest": "html",
        "url": "https://nocopo.bpp.gov.ng/",
        "notes": "Nigeria Open Contracting Portal. Open contracting plus official-host search.",
    },
)

# ISO -> (English query fragments, local-language fragments).
_COUNTRY_QUERY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "FR": (
        ("appel d'offres", "marche public", "consultation", "RFP"),
        ("intelligence artificielle", "data", "cloud", "SI decisionnel"),
    ),
    "GB": (
        ("tender", "RFP", "contract notice"),
        ("artificial intelligence", "data platform", "cloud"),
    ),
    "US": (
        ("solicitation", "RFP", "RFQ", "sources sought"),
        ("artificial intelligence", "data platform", "cloud"),
    ),
    "CA": (
        ("tender", "RFP", "RFSO"),
        ("artificial intelligence", "data", "cloud"),
    ),
    "SA": (
        ("tender", "RFP", "RFQ", "EOI"),
        ("منافسة", "مناقصة", "طلب تقديم عروض", "ذكاء اصطناعي", "بيانات", "تقنية"),
    ),
    "AE": (
        ("tender", "RFP", "EOI"),
        ("مناقصة", "ذكاء اصطناعي", "بيانات"),
    ),
    "QA": (
        ("tender", "RFP", "monaqasat"),
        ("مناقصة", "ذكاء اصطناعي"),
    ),
    "KW": (
        ("tender", "CAPT"),
        ("مناقصة",),
    ),
    "BH": (
        ("tender", "tender board"),
        ("مناقصة",),
    ),
    "OM": (
        ("tender", "tender board"),
        ("مناقصة",),
    ),
    "TN": (
        ("appel d'offres", "marche public", "TUNEPS"),
        ("intelligence artificielle", "systeme d'information"),
    ),
    "MA": (
        ("appel d'offres", "marche public"),
        ("intelligence artificielle", "data", "cloud"),
    ),
    "SN": (
        ("appel d'offres", "DAO"),
        ("numerique", "systeme d'information"),
    ),
    "CM": (
        ("appel d'offres", "ARMP"),
        ("marche public",),
    ),
    "BJ": (
        ("appel d'offres", "ARMP"),
        ("marche public",),
    ),
    "ML": (
        ("appel d'offres", "DGMP"),
        ("marche public",),
    ),
    "NE": (
        ("appel d'offres", "ARMP"),
        ("marche public",),
    ),
    "BF": (
        ("appel d'offres", "ARCOP"),
        ("marche public",),
    ),
    "GH": (
        ("tender", "GHANEPS"),
        ("procurement",),
    ),
    "NG": (
        ("tender", "NOCOPO"),
        ("procurement",),
    ),
    "CI": (
        ("appel d'offres", "DAO", "SIGOMAP"),
        ("numerique", "systeme d'information"),
    ),
    "DZ": (
        ("appel d'offres", "consultation"),
        ("intelligence artificielle", "data"),
    ),
    "ZA": (
        ("tender", "RFP"),
        ("eTenders",),
    ),
    "KE": (
        ("tender", "RFP"),
        ("procurement"),
    ),
    "EG": (
        ("tender", "RFP"),
        ("مناقصة",),
    ),
    "RW": (
        ("tender", "Umucyo"),
        ("procurement"),
    ),
    "AU": (
        ("tender", "ATM", "RFT"),
        ("AusTender",),
    ),
    "NZ": (
        ("tender", "GETS", "RFP"),
        ("procurement"),
    ),
    "IN": (
        ("tender", "eProcurement", "RFP"),
        ("निविदा",),
    ),
    "SG": (
        ("tender", "GeBIZ", "ITQ"),
        ("procurement",),
    ),
    "CH": (
        ("appel d'offres", "soumission", "SIMAP"),
        ("marche public",),
    ),
    "NL": (
        ("aanbesteding", "TenderNed"),
        ("tender",),
    ),
    "LU": (
        ("appel d'offres", "marche public", "PMP"),
        ("soumission",),
    ),
    "SE": (
        ("upphandling", "anbud", "tender"),
        ("procurement",),
    ),
    "AT": (
        ("ausschreibung", "auftrag"),
        ("tender",),
    ),
    "PL": (
        ("zamowienie publiczne", "przetarg"),
        ("tender",),
    ),
    "CZ": (
        ("verejna zakazka", "VVZ"),
        ("tender",),
    ),
    "RO": (
        ("achizitie publica", "SICAP"),
        ("tender",),
    ),
    "GR": (
        ("διαγωνισμος", "Promitheus"),
        ("tender",),
    ),
    "HR": (
        ("javna nabava", "EOJN"),
        ("tender",),
    ),
    "SI": (
        ("javno narocilo", "eNarocanje"),
        ("tender",),
    ),
    "SK": (
        ("verejne obstaravanie", "UVO"),
        ("tender",),
    ),
    "HU": (
        ("kozbeszerzes", "EKR"),
        ("tender",),
    ),
    "BG": (
        ("obshtestvena porachka", "EOP"),
        ("tender",),
    ),
    "DK": (
        ("udbud", "offentlig"),
        ("tender",),
    ),
    "FI": (
        ("hankinta", "Hilma"),
        ("tender",),
    ),
    "EE": (
        ("riigihange",),
        ("tender",),
    ),
    "LV": (
        ("iepirkums", "EIS"),
        ("tender",),
    ),
    "LT": (
        ("viesasis pirkimas", "CVPP"),
        ("tender",),
    ),
    "CY": (
        ("tender", "eProcurement"),
        ("procurement",),
    ),
    "IS": (
        ("utbod", "tender"),
        ("procurement",),
    ),
    "ES": (
        ("licitacion", "contrato publico"),
        ("tender",),
    ),
    "DE": (
        ("ausschreibung", "e-Vergabe"),
        ("tender",),
    ),
    "IT": (
        ("bando di gara", "appalto"),
        ("tender",),
    ),
    "IE": (
        ("tender", "eTenders"),
        ("procurement",),
    ),
    "PT": (
        ("concurso publico", "BASE"),
        ("tender",),
    ),
    "NO": (
        ("anbud", "Doffin"),
        ("tender",),
    ),
    "BE": (
        ("appel d'offres", "overheidsopdracht"),
        ("tender",),
    ),
    "BR": (
        ("licitacao", "compras.gov"),
        ("tender",),
    ),
    "CL": (
        ("licitacion", "mercado publico"),
        ("tender",),
    ),
    "MX": (
        ("licitacion", "Compras MX"),
        ("tender",),
    ),
    "JO": (
        ("tender", "JONEPS"),
        ("مناقصة",),
    ),
    "INTL": (
        ("procurement notice", "invitation for bids", "RFP"),
        ("appel d'offres", "avis de marche"),
    ),
}

_SITE_HINTS: dict[str, tuple[str, ...]] = {
    "FR": ("site:gouv.fr", "site:boamp.fr"),
    "GB": ("site:gov.uk",),
    "US": ("site:gov", "site:sam.gov"),
    "CA": ("site:canada.ca",),
    "SA": ("site:gov.sa", "site:etimad.sa"),
    "AE": ("site:gov.ae",),
    "QA": ("site:gov.qa",),
    "KW": ("site:gov.kw",),
    "BH": ("site:gov.bh",),
    "OM": ("site:gov.om",),
    "TN": ("site:gov.tn", "site:tuneps.tn"),
    "MA": ("site:gov.ma",),
    "SN": ("site:gouv.sn", "site:armp.sn", "site:marchespublics.sn"),
    "CI": ("site:gouv.ci", "site:marchespublics.ci", "site:sigomap.gouv.ci"),
    "DZ": ("site:mf.gov.dz", "site:gov.dz"),
    "ZA": ("site:etenders.gov.za",),
    "KE": ("site:tenders.go.ke",),
    "EG": ("site:etenders.gov.eg",),
    "RW": ("site:umucyo.gov.rw",),
    "AU": ("site:tenders.gov.au",),
    "NZ": ("site:gets.govt.nz",),
    "IN": ("site:eprocure.gov.in",),
    "SG": ("site:gebiz.gov.sg",),
    "CH": ("site:simap.ch",),
    "NL": ("site:tenderned.nl",),
    "LU": ("site:pmp.b2g.etat.lu", "site:public.lu"),
    "SE": ("site:upphandlingsmyndigheten.se",),
    "AT": ("site:usp.gv.at", "site:gv.at"),
    "PL": ("site:ezamowienia.gov.pl",),
    "CZ": ("site:vestnikverejnychzakazek.cz",),
    "RO": ("site:e-licitatie.ro",),
    "GR": ("site:promitheus.gov.gr",),
    "HR": ("site:eojn.nn.hr",),
    "SI": ("site:enarocanje.si",),
    "SK": ("site:uvo.gov.sk",),
    "HU": ("site:ekr.gov.hu",),
    "BG": ("site:eop.bg",),
    "DK": ("site:udbud.dk",),
    "FI": ("site:hankintailmoitukset.fi",),
    "EE": ("site:riigihanked.riik.ee",),
    "LV": ("site:iub.gov.lv", "site:eis.gov.lv"),
    "LT": ("site:viesiejipirkimai.lt",),
    "CY": ("site:eprocurement.gov.cy",),
    "IS": ("site:utbodsvefur.is",),
    "ES": ("site:contrataciondelsectorpublico.gob.es", "site:contrataciondelestado.es"),
    "CM": ("site:armp.cm",),
    "BJ": ("site:marches-publics.bj", "site:armp.bj"),
    "ML": ("site:dgmp.gouv.ml",),
    "NE": ("site:marchespublics.ne",),
    "BF": ("site:arcop.bf",),
    "GH": ("site:ghaneps.gov.gh",),
    "NG": ("site:nocopo.bpp.gov.ng", "site:bpp.gov.ng"),
    "DE": ("site:evergabe-online.de",),
    "IT": ("site:acquistinretepa.it", "site:anticorruzione.it"),
    "INTL": ("site:worldbank.org", "site:ungm.org", "site:afdb.org"),
    "IE": ("site:etenders.gov.ie",),
    "PT": ("site:base.gov.pt",),
    "NO": ("site:doffin.no",),
    "BE": ("site:publicprocurement.be",),
    "BR": ("site:gov.br",),
    "CL": ("site:mercadopublico.cl",),
    "MX": ("site:comprasmx.buengobierno.gob.mx",),
    "JO": ("site:joneps.gov.jo",),
}

# Wired official feeds. scrape_net and collect must both read this set.
API_SOURCE_IDS = frozenset(
    {
        "ted",
        "world-bank",
        "boamp",
        "find-a-tender",
        "contracts-finder",
        "canadabuys",
        "sam-gov",
    }
)
_FETCH_IDS = API_SOURCE_IDS
_TED_COUNTRIES = frozenset(
    {
        "EU",
        "FR",
        "DE",
        "IT",
        "ES",
        "PT",
        "IE",
        "NL",
        "BE",
        "NO",
        "AT",
        "SE",
        "DK",
        "FI",
        "PL",
        "CZ",
        "GR",
        "RO",
        "HU",
        "BG",
        "HR",
        "SK",
        "SI",
        "LT",
        "LV",
        "EE",
        "MT",
        "CY",
        "LU",
        "IS",
    }
)
# National portals that still run official-host scrape. TED/BOAMP APIs stay first.
NATIONAL_SCRAPE_IDS = frozenset(
    {
        "place",
        "belgium-eproc",
        "simap",
        "luxembourg-pmp",
        "sweden-uhmynd",
    }
)
_NATIONAL_SCRAPE = NATIONAL_SCRAPE_IDS
_COVERAGES = frozenset({"api", "opendata", "key", "covered", "search"})
_ACCESS = frozenset({"api", "opendata", "api_key", "scrape", "covered"})


def _access_of(coverage: str) -> str:
    if coverage == "api":
        return "api"
    if coverage == "opendata":
        return "opendata"
    if coverage == "key":
        return "api_key"
    if coverage == "covered":
        return "covered"
    return "scrape"


def enrich_source(row: dict[str, Any]) -> dict[str, Any]:
    """Every catalog row must leave with an explicit coverage path."""
    out = dict(row)
    sid = str(out.get("id") or "")
    country = str(out.get("country") or "").upper()
    if sid == "sam-gov":
        out["coverage"] = "key"
        out["ingest"] = "api"
    elif sid in _FETCH_IDS:
        out["coverage"] = "opendata" if sid == "canadabuys" else "api"
    elif sid == "world-bank-business":
        out["coverage"] = "covered"
        out["covered_by"] = "world-bank"
    elif sid in _NATIONAL_SCRAPE:
        out["coverage"] = "search"
    elif country in _TED_COUNTRIES and sid not in _FETCH_IDS:
        out["coverage"] = "covered"
        out["covered_by"] = "ted"
    else:
        out["coverage"] = "search"
    out["access"] = _access_of(str(out.get("coverage") or ""))
    return out


def coverage_holes() -> list[str]:
    """Source ids with no ingest path and no country search net."""
    holes: list[str] = []
    for row in catalog():
        sid = str(row["id"])
        coverage = str(row.get("coverage") or "")
        if coverage not in _COVERAGES:
            holes.append(sid)
            continue
        if str(row.get("access") or "") not in _ACCESS:
            holes.append(sid)
            continue
        country = str(row.get("country") or "").upper()
        if coverage == "search" and country not in _COUNTRY_QUERY:
            holes.append(sid)
        if coverage == "covered" and not row.get("covered_by"):
            holes.append(sid)
        if coverage in {"api", "opendata", "key"} and sid not in _FETCH_IDS:
            holes.append(sid)
    return holes


def catalog() -> list[dict[str, Any]]:
    return [enrich_source(dict(row)) for row in SOURCES]


def official_hosts() -> set[str]:
    """Catalog hosts plus country site: hints used by the scrape net."""
    from navin.tenders.normalize import host_of

    hosts: set[str] = set()
    for row in catalog():
        for key in ("url", "api"):
            host = host_of(str(row.get(key) or "")).removeprefix("www.")
            if host:
                hosts.add(host)
    for hints in _SITE_HINTS.values():
        for hint in hints:
            token = hint.replace("site:", "").strip().lower().removeprefix("www.")
            if token:
                hosts.add(token)
    return hosts


def source_by_id(source_id: str) -> dict[str, Any] | None:
    sid = (source_id or "").strip().lower()
    for row in SOURCES:
        if row["id"] == sid:
            return enrich_source(dict(row))
    return None


def sources_for_countries(countries: list[str]) -> list[dict[str, Any]]:
    wanted = {c.strip().upper() for c in countries if str(c).strip()}
    if not wanted:
        return catalog()
    out: list[dict[str, Any]] = []
    for row in SOURCES:
        country = str(row.get("country") or "").upper()
        if country in wanted or country == "INTL" or country == "EU":
            out.append(enrich_source(dict(row)))
    return out


def web_search_queries(
    *,
    countries: list[str],
    crafts: list[str],
    tender_types: list[str] | None = None,
    project_types: list[str] | None = None,
) -> list[dict[str, str]]:
    """Safety-net queries (P4). Collect runs them through web_search + scrape."""
    from navin.tenders.needs import query_need_terms

    crafts_en = query_need_terms(crafts, tender_types, project_types, limit=8) or [
        "AI",
        "data platform",
        "cloud",
    ]
    craft_clause = " OR ".join(f'"{c}"' for c in crafts_en[:8])
    queries: list[dict[str, str]] = []
    seen: set[str] = set()
    codes = [iso.strip().upper() for iso in (countries or list(_COUNTRY_QUERY)) if str(iso).strip()]
    if "INTL" not in codes:
        codes.append("INTL")
    for code in codes:
        pair = _COUNTRY_QUERY.get(code)
        if pair is None:
            continue
        en_frags, local_frags = pair
        sites = _SITE_HINTS.get(code, ())
        site = sites[0] if sites else ""
        en_clause = " OR ".join(f'"{x}"' for x in en_frags[:4])
        english = f"({en_clause}) ({craft_clause})"
        if site:
            english = f"{english} {site}"
        if english not in seen:
            seen.add(english)
            queries.append({"country": code, "lang": "en", "query": english.strip()})
        local = " OR ".join(f'"{x}"' for x in local_frags[:6])
        if local and local not in seen:
            seen.add(local)
            extra_site = f" {site}" if site else ""
            queries.append(
                {
                    "country": code,
                    "lang": "local",
                    "query": f"({local}){extra_site}".strip(),
                }
            )
    return queries
