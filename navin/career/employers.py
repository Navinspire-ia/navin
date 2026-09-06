"""Employer watch: ESN, consulting houses and agencies read from their own feeds.

A freelancer or candidate in FR / BE / CH wants every posting from the houses
that actually buy their profile. Those houses publish through an ATS with a
public feed (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee,
Teamtailor, Personio, Workday) or on a careers page. This module keeps a
directory per market, resolves the feed behind any careers URL (directory or
user-added), caches that resolution, and reads the feeds directly. It never
signs in and never applies.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from loguru import logger

from navin.career.sources import (
    clean_job_text,
    html_to_text,
    infer_country_iso,
    is_listing_hit,
    stable_job_id,
)

Fetch = Callable[..., bytes]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT_S = 12.0
MAX_FEED_BYTES = 24_000_000
# Employers whose feed is read on one collect. Primary markets go first,
# then the least recently checked, so a big directory rotates over cycles.
MAX_EMPLOYERS_PER_RUN = 30
# Unresolved careers pages probed per collect (2-4 HTTP calls each).
MAX_RESOLVE_PER_RUN = 6
# Postings kept per employer before the desk relevance pass.
MAX_JOBS_PER_EMPLOYER = 40
RESOLVE_TTL_S = 30 * 86400
RESOLVE_RETRY_S = 7 * 86400
FEED_WORKERS = 6

KINDS = ("esn", "consulting", "agency", "big4", "product")

# --- Directory -------------------------------------------------------------
# name, kind, corporate site, markets where the house hires. The careers URL
# and the ATS are resolved live and cached; a wrong guess here only costs one
# probe, it never invents a posting.
_D: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # Global ESN / IT services
    ("Capgemini", "esn", "capgemini.com", ("FR", "BE", "NL", "DE", "GB", "ES", "IT", "PT", "PL", "US", "CA", "MA", "AE", "SA")),
    ("Sopra Steria", "esn", "soprasteria.com", ("FR", "BE", "DE", "GB", "ES", "IT", "NL", "PL", "SE", "MA", "TN")),
    ("Accenture", "consulting", "accenture.com", ("FR", "BE", "CH", "GB", "US", "CA", "DE", "NL", "ES", "IT", "PT", "IE", "SE", "PL", "AE", "SA", "MA")),
    ("Atos / Eviden", "esn", "atos.net", ("FR", "BE", "DE", "GB", "NL", "ES", "IT", "PL", "MA", "AE", "SA")),
    ("CGI", "esn", "cgi.com", ("FR", "CA", "US", "GB", "DE", "NL", "SE", "PT", "PL", "BE")),
    ("Devoteam", "esn", "devoteam.com", ("FR", "BE", "NL", "DE", "ES", "PT", "IT", "PL", "SE", "AE", "SA", "MA", "TN")),
    ("Inetum", "esn", "inetum.com", ("FR", "BE", "ES", "PT", "PL", "MA", "TN", "AE")),
    ("Alten", "esn", "alten.com", ("FR", "BE", "CH", "DE", "NL", "ES", "IT", "GB", "SE", "PL", "MA", "US", "CA")),
    ("Akkodis", "esn", "akkodis.com", ("FR", "BE", "CH", "DE", "IT", "ES", "GB", "NL", "US", "CA", "MA")),
    ("Expleo", "esn", "expleo.com", ("FR", "BE", "DE", "GB", "IE", "ES", "IT", "PT", "MA", "AE")),
    ("NTT Data", "esn", "nttdata.com", ("FR", "BE", "DE", "GB", "ES", "IT", "PT", "NL", "US", "CA", "AE", "SA")),
    ("Cognizant", "esn", "cognizant.com", ("FR", "BE", "NL", "DE", "GB", "IE", "US", "CA", "AE", "SA")),
    ("Tata Consultancy Services", "esn", "tcs.com", ("FR", "BE", "NL", "DE", "GB", "IE", "US", "CA", "AE", "SA", "QA")),
    ("Infosys", "esn", "infosys.com", ("FR", "BE", "NL", "DE", "GB", "US", "CA", "AE", "SA")),
    ("Wipro", "esn", "wipro.com", ("FR", "DE", "GB", "NL", "US", "CA", "AE", "SA")),
    ("HCLTech", "esn", "hcltech.com", ("FR", "DE", "GB", "NL", "SE", "US", "CA", "AE", "SA")),
    ("Kyndryl", "esn", "kyndryl.com", ("FR", "BE", "DE", "GB", "ES", "IT", "NL", "US", "CA", "AE")),
    ("EPAM", "esn", "epam.com", ("FR", "BE", "CH", "DE", "NL", "GB", "IE", "ES", "PL", "SE", "US", "CA", "AE")),
    ("Endava", "esn", "endava.com", ("GB", "IE", "DE", "NL", "US", "CA", "AE", "SA")),
    ("Nagarro", "esn", "nagarro.com", ("FR", "DE", "GB", "NL", "SE", "PL", "US", "CA", "AE", "SA", "QA")),
    ("GlobalLogic", "esn", "globallogic.com", ("FR", "DE", "GB", "PL", "SE", "US", "CA")),
    ("Thoughtworks", "consulting", "thoughtworks.com", ("DE", "GB", "ES", "NL", "US", "CA")),
    ("Reply", "esn", "reply.com", ("FR", "BE", "DE", "GB", "NL", "IT", "PL", "US")),
    ("Avanade", "esn", "avanade.com", ("FR", "BE", "CH", "DE", "GB", "NL", "ES", "IT", "SE", "PL", "US", "CA", "AE")),
    ("Publicis Sapient", "esn", "publicissapient.com", ("FR", "DE", "GB", "US", "CA", "AE")),
    ("Valtech", "esn", "valtech.com", ("FR", "DE", "GB", "NL", "SE", "US", "CA", "AE")),
    ("Slalom", "consulting", "slalom.com", ("GB", "IE", "DE", "US", "CA")),
    # Big 4 and strategy
    ("Deloitte", "big4", "deloitte.com", ("FR", "BE", "CH", "GB", "US", "CA", "DE", "NL", "ES", "IT", "PT", "IE", "SE", "PL", "AE", "SA", "QA", "MA", "TN")),
    ("EY", "big4", "ey.com", ("FR", "BE", "CH", "GB", "US", "CA", "DE", "NL", "ES", "IT", "PT", "IE", "SE", "PL", "AE", "SA", "QA", "MA", "TN")),
    ("KPMG", "big4", "kpmg.com", ("FR", "BE", "CH", "GB", "US", "CA", "DE", "NL", "ES", "IT", "PT", "IE", "SE", "PL", "AE", "SA", "QA", "MA", "TN")),
    ("PwC", "big4", "pwc.com", ("FR", "BE", "CH", "GB", "US", "CA", "DE", "NL", "ES", "IT", "PT", "IE", "SE", "PL", "AE", "SA", "QA", "MA", "TN")),
    ("BearingPoint", "consulting", "bearingpoint.com", ("FR", "BE", "CH", "DE", "NL", "GB", "IE", "SE", "PT", "MA", "AE")),
    ("Wavestone", "consulting", "wavestone.com", ("FR", "BE", "CH", "GB", "DE", "US", "MA")),
    ("Sia Partners", "consulting", "sia-partners.com", ("FR", "BE", "GB", "NL", "IT", "CA", "US", "AE", "SA", "QA", "MA")),
    ("Talan", "consulting", "talan.com", ("FR", "BE", "CH", "ES", "GB", "CA", "US", "MA", "TN")),
    ("Amaris Consulting", "consulting", "amaris.com", ("FR", "BE", "CH", "ES", "IT", "PT", "GB", "CA", "AE", "MA", "TN")),
    ("Onepoint", "consulting", "groupeonepoint.com", ("FR", "BE", "CA", "TN")),
    ("Mc2i", "consulting", "mc2i.fr", ("FR",)),
    ("Saegus", "consulting", "saegus.com", ("FR",)),
    ("Artefact", "consulting", "artefact.com", ("FR", "NL", "GB", "DE", "ES", "US", "AE", "SA", "MA")),
    ("Ekimetrics", "consulting", "ekimetrics.com", ("FR", "GB", "US", "AE")),
    # France: ESN and data / cloud houses
    ("Sword Group", "esn", "sword-group.com", ("FR", "BE", "CH", "GB", "MA", "TN", "AE")),
    ("Econocom", "esn", "econocom.com", ("FR", "BE", "ES", "IT", "NL", "MA")),
    ("Scalian", "esn", "scalian.com", ("FR", "BE", "ES", "GB", "MA")),
    ("Davidson Consulting", "esn", "davidson.fr", ("FR", "BE", "CH", "CA", "AE")),
    ("SII Group", "esn", "sii-group.com", ("FR", "BE", "DE", "ES", "PL", "CA", "MA")),
    ("Aubay", "esn", "aubay.com", ("FR", "BE", "ES", "IT", "PT")),
    ("Infotel", "esn", "infotel.com", ("FR", "BE", "ES", "GB", "CA")),
    ("Neosoft", "esn", "neo-soft.fr", ("FR", "BE", "MA")),
    ("Astek", "esn", "astekgroup.fr", ("FR", "BE", "CA", "MA")),
    ("Apside", "esn", "apside.com", ("FR", "BE", "CH", "PT", "CA", "MA")),
    ("Extia", "esn", "extia.fr", ("FR", "BE", "CH", "ES", "CA", "MA")),
    ("Meritis", "esn", "meritis.fr", ("FR",)),
    ("Sfeir", "esn", "sfeir.com", ("FR", "BE", "MA")),
    ("Zenika", "esn", "zenika.com", ("FR", "CA", "MA")),
    ("Theodo", "esn", "theodo.com", ("FR", "GB", "US", "MA")),
    ("Octo Technology", "esn", "octo.com", ("FR", "MA")),
    ("Ippon Technologies", "esn", "ippon.tech", ("FR", "US", "CA", "MA")),
    ("Micropole", "esn", "micropole.com", ("FR", "BE", "CH", "TN")),
    ("Keyrus", "esn", "keyrus.com", ("FR", "BE", "CH", "ES", "PT", "GB", "CA", "US", "AE", "SA", "MA", "TN")),
    ("Groupe Open", "esn", "open.global", ("FR", "BE", "NL", "MA")),
    ("Hardis Group", "esn", "hardis-group.com", ("FR", "ES", "CH")),
    ("Viveris", "esn", "viveris.fr", ("FR",)),
    ("Umanis", "esn", "umanis.com", ("FR", "BE", "CH")),
    ("Business & Decision", "esn", "businessdecision.com", ("FR", "BE", "CH", "MA", "TN")),
    ("Bee Engineering", "esn", "bee-eng.fr", ("FR",)),
    ("Squad", "esn", "squad.fr", ("FR", "BE")),
    ("Consort Group", "esn", "consort-group.com", ("FR", "BE", "MA")),
    ("Cellenza", "esn", "cellenza.com", ("FR",)),
    ("Sicara", "esn", "sicara.fr", ("FR",)),
    ("Quantmetry", "esn", "quantmetry.com", ("FR",)),
    # Belgium / Netherlands / Luxembourg houses
    ("Cronos Groep", "esn", "cronos-groep.be", ("BE", "NL")),
    ("Delaware", "esn", "delaware.pro", ("BE", "NL", "FR", "GB", "US", "MA")),
    ("Cegeka", "esn", "cegeka.com", ("BE", "NL", "DE", "IT", "SE", "PL")),
    ("NRB", "esn", "nrb.be", ("BE",)),
    ("Tobania", "esn", "tobania.be", ("BE",)),
    ("Ordina", "esn", "ordina.com", ("BE", "NL")),
    ("Conclusion", "esn", "conclusion.nl", ("NL",)),
    ("Centric", "esn", "centric.eu", ("NL", "BE", "SE")),
    ("Info Support", "esn", "infosupport.com", ("NL", "BE")),
    ("Xebia", "esn", "xebia.com", ("NL", "FR", "GB", "US", "AE", "SA", "QA")),
    # Switzerland
    ("ELCA", "esn", "elca.ch", ("CH",)),
    ("Zuhlke", "esn", "zuehlke.com", ("CH", "DE", "GB", "PT")),
    ("ti&m", "esn", "ti8m.com", ("CH", "DE")),
    ("Netcetera", "esn", "netcetera.com", ("CH", "DE", "AE")),
    ("Adnovum", "esn", "adnovum.com", ("CH", "PT")),
    ("Ergon Informatik", "esn", "ergon.ch", ("CH",)),
    # UK / Ireland
    ("Kainos", "esn", "kainos.com", ("GB", "IE", "PL", "US", "CA")),
    ("BJSS", "esn", "bjss.com", ("GB", "US", "PT")),
    ("Version 1", "esn", "version1.com", ("GB", "IE", "ES")),
    ("Softwire", "esn", "softwire.com", ("GB",)),
    ("Mastek", "esn", "mastek.com", ("GB", "NL", "US", "CA", "AE", "SA")),
    ("Ten10", "esn", "ten10.com", ("GB",)),
    # Germany / Austria
    ("adesso", "esn", "adesso.de", ("DE", "CH", "NL", "ES")),
    ("msg", "esn", "msg.group", ("DE", "CH", "NL", "ES", "IT", "PL")),
    ("Materna", "esn", "materna.de", ("DE",)),
    ("MHP", "consulting", "mhp.com", ("DE", "GB", "US", "CN")),
    ("Senacor", "consulting", "senacor.com", ("DE", "CH")),
    ("Netlight", "consulting", "netlight.com", ("DE", "SE", "CH", "NL", "GB")),
    ("Diconium", "esn", "diconium.com", ("DE", "PT", "US")),
    ("Bechtle", "esn", "bechtle.com", ("DE", "CH", "NL", "BE", "FR", "ES", "IT", "PT", "PL", "GB", "IE")),
    ("Computacenter", "esn", "computacenter.com", ("DE", "GB", "FR", "BE", "NL", "CH", "ES", "US")),
    # Spain / Italy / Portugal / Poland / Sweden
    ("Minsait (Indra)", "esn", "minsait.com", ("ES", "IT", "PT", "US")),
    ("Izertis", "esn", "izertis.com", ("ES", "PT")),
    ("Babel", "esn", "babelgroup.com", ("ES", "PT", "MA")),
    ("Engineering Ingegneria Informatica", "esn", "eng.it", ("IT", "ES", "BE", "DE", "US")),
    ("Almaviva", "esn", "almaviva.it", ("IT", "BE")),
    ("Lutech", "esn", "lutech.group", ("IT",)),
    ("Critical Software", "esn", "criticalsoftware.com", ("PT", "GB", "DE")),
    ("Noesis", "esn", "noesis.pt", ("PT", "ES", "NL", "IE", "US")),
    ("Celfocus", "esn", "celfocus.com", ("PT", "GB", "NL")),
    ("Sii Poland", "esn", "sii.pl", ("PL",)),
    ("Comarch", "esn", "comarch.com", ("PL", "DE", "FR", "BE")),
    ("Asseco", "esn", "asseco.com", ("PL", "ES", "PT")),
    ("Netguru", "esn", "netguru.com", ("PL",)),
    ("STX Next", "esn", "stxnext.com", ("PL",)),
    ("Software Mind", "esn", "softwaremind.com", ("PL", "US")),
    ("Knowit", "esn", "knowit.eu", ("SE", "PL")),
    ("Consid", "esn", "consid.com", ("SE",)),
    ("Tietoevry", "esn", "tietoevry.com", ("SE", "PL")),
    # North America
    ("Alithya", "esn", "alithya.com", ("CA", "US", "FR")),
    ("Levio", "consulting", "levio.ca", ("CA",)),
    ("Perficient", "esn", "perficient.com", ("US", "CA")),
    ("West Monroe", "consulting", "westmonroe.com", ("US",)),
    ("Booz Allen Hamilton", "consulting", "boozallen.com", ("US",)),
    ("Leidos", "esn", "leidos.com", ("US", "GB")),
    # Gulf
    ("Injazat", "esn", "injazat.com", ("AE",)),
    ("Intertec Systems", "esn", "intertecsystems.com", ("AE", "SA", "QA", "KW", "OM", "BH")),
    ("GBM", "esn", "gbmme.com", ("AE", "SA", "QA", "KW", "OM", "BH")),
    ("Mannai", "esn", "mannai.com", ("QA",)),
    ("Malomatia", "esn", "malomatia.com", ("QA",)),
    ("Elm", "esn", "elm.sa", ("SA",)),
    ("STC Solutions", "esn", "solutions.com.sa", ("SA",)),
    ("Saudi Business Machines", "esn", "sbm.com.sa", ("SA",)),
    # Maghreb
    ("Sofrecom", "esn", "sofrecom.com", ("FR", "MA", "TN")),
    ("Vermeg", "esn", "vermeg.com", ("TN", "FR", "BE", "GB")),
    ("Telnet", "esn", "groupe-telnet.com", ("TN", "FR")),
    ("Proxym", "esn", "proxym-group.com", ("TN", "FR")),
    ("Oxia", "esn", "oxia.com", ("TN", "FR")),
    ("Involys", "esn", "involys.com", ("MA",)),
    ("Intelcia IT Solutions", "esn", "intelcia.com", ("MA", "FR")),
    # Agencies: staffing, portage, recruitment (contract and permanent)
    ("Hays", "agency", "hays.com", ("FR", "BE", "CH", "GB", "IE", "DE", "NL", "ES", "IT", "PT", "PL", "SE", "US", "CA", "AE")),
    ("Michael Page", "agency", "michaelpage.com", ("FR", "BE", "CH", "GB", "IE", "DE", "NL", "ES", "IT", "PT", "PL", "SE", "US", "CA", "AE", "QA", "MA")),
    ("Robert Half", "agency", "roberthalf.com", ("FR", "BE", "CH", "GB", "DE", "NL", "US", "CA", "AE")),
    ("Randstad Digital", "agency", "randstaddigital.com", ("FR", "BE", "CH", "GB", "DE", "NL", "ES", "IT", "PT", "PL", "SE", "US", "CA")),
    ("Harnham", "agency", "harnham.com", ("GB", "US", "DE", "NL", "FR")),
    ("La Fosse", "agency", "lafosse.com", ("GB", "US")),
    ("Experis", "agency", "experis.com", ("FR", "BE", "CH", "GB", "DE", "NL", "ES", "IT", "SE", "PL", "US", "CA")),
    ("Insight Global", "agency", "insightglobal.com", ("US", "CA", "GB")),
    ("TEKsystems", "agency", "teksystems.com", ("US", "CA", "GB", "NL", "DE")),
    ("Huxley", "agency", "huxley.com", ("BE", "NL", "GB", "DE", "CH", "AE", "US")),
    ("Computer Futures", "agency", "computerfutures.com", ("BE", "NL", "GB", "DE", "CH", "AE", "US")),
    ("Darwin Recruitment", "agency", "darwinrecruitment.com", ("NL", "DE", "BE", "GB", "CH")),
    ("Cooptalis", "agency", "cooptalis.com", ("FR", "BE", "MA", "TN")),
)


# Feeds confirmed live (public API answered with postings under this slug).
# Anything not listed here is resolved at runtime and cached in the store.
_SEEDS: dict[str, dict[str, str]] = {
    "devoteam": {"ats": "smartrecruiters", "slug": "Devoteam"},
    "alten": {"ats": "smartrecruiters", "slug": "Alten"},
    "endava": {"ats": "smartrecruiters", "slug": "Endava"},
    "talan": {"ats": "smartrecruiters", "slug": "Talan"},
    "scalian": {"ats": "smartrecruiters", "slug": "Scalian"},
    "meritis": {"ats": "smartrecruiters", "slug": "Meritis"},
    "zenika": {"ats": "smartrecruiters", "slug": "Zenika"},
    "octo-technology": {"ats": "smartrecruiters", "slug": "OctoTechnology"},
    "keyrus": {"ats": "smartrecruiters", "slug": "Keyrus"},
    "version-1": {"ats": "smartrecruiters", "slug": "Version1"},
    "software-mind": {"ats": "smartrecruiters", "slug": "SoftwareMind"},
    "wavestone": {"ats": "smartrecruiters", "slug": "wavestone1"},
    "sopra-steria": {"ats": "smartrecruiters", "slug": "SopraSteria1"},
    "inetum": {"ats": "smartrecruiters", "slug": "inetum2"},
    "nagarro": {"ats": "smartrecruiters", "slug": "Nagarro1"},
    "sia-partners": {"ats": "smartrecruiters", "slug": "Sia"},
    "thoughtworks": {"ats": "greenhouse", "slug": "thoughtworks"},
    "valtech": {"ats": "greenhouse", "slug": "valtech"},
    "bearingpoint": {"ats": "greenhouse", "slug": "bearingpoint"},
    "artefact": {"ats": "greenhouse", "slug": "artefact"},
    "levio": {"ats": "greenhouse", "slug": "levio"},
    "ekimetrics": {"ats": "lever", "slug": "ekimetrics"},
    "sfeir": {"ats": "lever", "slug": "sfeir"},
    "theodo": {"ats": "lever", "slug": "theodo"},
    "sicara": {"ats": "lever", "slug": "theodo"},
    "sword-group": {"ats": "workable", "slug": "sword-group"},
    "consort-group": {"ats": "teamtailor", "slug": "consortgroup"},
    "celfocus": {"ats": "teamtailor", "slug": "celfocus"},
    "xebia": {"ats": "personio", "slug": "xebia"},
    "accenture": {"ats": "workday", "slug": "accenture", "host": "accenture.wd103.myworkdayjobs.com", "site": "AccentureCareers"},
    "avanade": {"ats": "workday", "slug": "accenture", "host": "accenture.wd103.myworkdayjobs.com", "site": "AvanadeCareers"},
    "kainos": {"ats": "workday", "slug": "kainos", "host": "kainos.wd3.myworkdayjobs.com", "site": "Kainos"},
    "zuhlke": {"ats": "workday", "slug": "zuehlke", "host": "zuehlke.wd3.myworkdayjobs.com", "site": "zuhlke-careers"},
    "kyndryl": {"ats": "workday", "slug": "kyndryl", "host": "kyndryl.wd5.myworkdayjobs.com", "site": "KyndrylProfessionalCareers"},
    "neosoft": {"ats": "workday", "slug": "neosoft", "host": "neosoft.wd3.myworkdayjobs.com", "site": "neo-soft"},
    "booz-allen-hamilton": {"ats": "workday", "slug": "bah", "host": "bah.wd1.myworkdayjobs.com", "site": "BAH_Jobs"},
    "leidos": {"ats": "workday", "slug": "leidos", "host": "leidos.wd5.myworkdayjobs.com", "site": "External"},
    "robert-half": {"ats": "workday", "slug": "roberthalf", "host": "roberthalf.wd1.myworkdayjobs.com", "site": "RobertHalfStaffingCareers"},
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _entry(name: str, kind: str, site: str, markets: tuple[str, ...], *, user: bool = False) -> dict[str, Any]:
    row = {
        "id": _slug(name),
        "name": name,
        "kind": kind if kind in KINDS else "esn",
        "site": site.lower(),
        "markets": [iso.upper() for iso in markets],
        "user": user,
    }
    seed = _SEEDS.get(row["id"])
    if seed:
        row["seed"] = dict(seed)
    return row


EMPLOYERS: tuple[dict[str, Any], ...] = tuple(_entry(*row) for row in _D)


def directory(countries: list[str] | None = None) -> list[dict[str, Any]]:
    """Directory rows for the given markets (all when none)."""
    wanted = {str(iso).strip().upper() for iso in (countries or []) if str(iso).strip()}
    rows = [dict(row) for row in EMPLOYERS if not wanted or wanted & set(row["markets"])]
    rows.sort(key=lambda row: (row["kind"] != "esn", row["name"].lower()))
    return rows


def user_employers(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Employers the user added: {"name", "url" or "site", "country"/"markets", "kind"}."""
    out: list[dict[str, Any]] = []
    for raw in profile.get("employers") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        url = str(raw.get("url") or raw.get("careers_url") or "").strip()
        site = str(raw.get("site") or "").strip().lower()
        if not site and url:
            site = _host(url)
        if not name and not site:
            continue
        markets = raw.get("markets") if isinstance(raw.get("markets"), list) else []
        if not markets and raw.get("country"):
            markets = [str(raw["country"])]
        row = _entry(name or site, str(raw.get("kind") or "esn"), site, tuple(str(m) for m in markets), user=True)
        if url:
            row["careers_url"] = url
        out.append(row)
    return out


# --- ATS detection ---------------------------------------------------------

_ATS_URL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"https?://boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"https?://jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"https?://(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"https?://api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"https?://apply\.workable\.com/(?:api/v\d/accounts/)?([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"https?://([A-Za-z0-9_-]+)\.workable\.com", re.I)),
    ("recruitee", re.compile(r"https?://([A-Za-z0-9_-]+)\.recruitee\.com", re.I)),
    ("teamtailor", re.compile(r"https?://([A-Za-z0-9_-]+)\.teamtailor\.com", re.I)),
    ("personio", re.compile(r"https?://([A-Za-z0-9_-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("workday", re.compile(r"https?://([A-Za-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}(?:-[A-Za-z]{2})?/)?([A-Za-z0-9_-]+)", re.I)),
)
_WORKDAY_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$", re.I)
_HREF_RE = re.compile(r"""(?:href|src|action)=["']([^"']+)["']""", re.I)
_CAREERS_LINK_RE = re.compile(
    r"career|carri[eè]re|jobs?\b|recrut|join[-_ ]?us|rejoignez|nous[-_ ]rejoindre|emploi|vacanc|talent|work[-_ ]with",
    re.I,
)
_CAREERS_PATHS = (
    "/careers",
    "/careers/",
    "/carrieres",
    "/carrieres/",
    "/fr/carrieres",
    "/en/careers",
    "/jobs",
    "/jobs/",
    "/join-us",
    "/nous-rejoindre",
    "/recrutement",
    "/career",
)


def detect_ats(url: str) -> dict[str, str] | None:
    """ATS kind + slug from a careers / job / feed URL, or None."""
    text = (url or "").strip()
    if not text:
        return None
    for ats, pattern in _ATS_URL_RULES:
        match = pattern.search(text)
        if not match:
            continue
        if ats == "workday":
            tenant, host_bit, site = match.group(1), match.group(2), match.group(3)
            if site.lower() in {"wday", "login", "job", "jobs"} or _WORKDAY_LOCALE.match(site):
                continue
            return {"ats": ats, "slug": tenant.lower(), "host": f"{tenant.lower()}.{host_bit.lower()}.myworkdayjobs.com", "site": site}
        slug = match.group(1)
        if ats == "workable" and slug.lower() in {"www", "apply", "jobs", "resources"}:
            continue
        if ats == "greenhouse" and slug.lower() in {"embed", "static"}:
            continue
        return {"ats": ats, "slug": slug}
    return None


def detect_ats_in_html(html_text: str) -> dict[str, str] | None:
    """First ATS link or embed found in a careers page."""
    if not html_text:
        return None
    for link in _HREF_RE.findall(html_text):
        found = detect_ats(html_text_unescape(link))
        if found:
            return found
    for pattern in (
        re.compile(r"boards\.greenhouse\.io/embed/job_board(?:/js)?\?(?:[^\"'\s]*&(?:amp;)?)?for=([A-Za-z0-9_-]+)", re.I),
        re.compile(r"grnh\.se/([A-Za-z0-9_-]+)", re.I),
    ):
        match = pattern.search(html_text)
        if match:
            return {"ats": "greenhouse", "slug": match.group(1)}
    match = re.search(r"https?://jobs\.lever\.co/([A-Za-z0-9_-]+)", html_text, re.I)
    if match:
        return {"ats": "lever", "slug": match.group(1)}
    return None


def html_text_unescape(value: str) -> str:
    return (value or "").replace("&amp;", "&").replace("&#x2F;", "/").replace("&#47;", "/")


# --- HTTP ------------------------------------------------------------------


def default_fetch(
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    accept: str = "application/json, text/html;q=0.9, */*;q=0.8",
    timeout: float = TIMEOUT_S,
    prime: str = "",
) -> bytes:
    """GET (or POST json) with a browser UA. `prime` GETs a page first to hold cookies (Workday)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": _UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9,fr;q=0.8"}
    if prime:
        try:
            opener.open(urllib.request.Request(prime, headers={"User-Agent": _UA}), timeout=timeout).read(4096)
        except (urllib.error.URLError, OSError, ValueError):
            pass
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with opener.open(req, timeout=timeout) as resp:
        # Group-wide Lever / Greenhouse boards with full descriptions run past 5 MB.
        return resp.read(MAX_FEED_BYTES)


def final_url(url: str, timeout: float = TIMEOUT_S) -> str:
    """Where a careers link lands after redirects (many go straight to the ATS)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return str(resp.geturl() or url)


def _host(url: str) -> str:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8", errors="replace"))


def _text(value: Any) -> str:
    return clean_job_text(html_to_text(str(value or ""))).strip()


# Countries outside MARKETS still need an ISO so the market gate can drop them.
_FAR_COUNTRIES = {
    "india": "IN",
    "united states": "US",
    "usa": "US",
    "brazil": "BR",
    "brasil": "BR",
    "singapore": "SG",
    "australia": "AU",
    "china": "CN",
    "japan": "JP",
    "mexico": "MX",
    "argentina": "AR",
    "colombia": "CO",
    "chile": "CL",
    "peru": "PE",
    "vietnam": "VN",
    "philippines": "PH",
    "indonesia": "ID",
    "malaysia": "MY",
    "egypt": "EG",
    "south africa": "ZA",
    "nigeria": "NG",
    "kenya": "KE",
    "turkey": "TR",
    "romania": "RO",
    "bulgaria": "BG",
    "serbia": "RS",
    "ukraine": "UA",
    "czech": "CZ",
    "czechia": "CZ",
    "hungary": "HU",
    "austria": "AT",
    "denmark": "DK",
    "norway": "NO",
    "finland": "FI",
    "greece": "GR",
    "israel": "IL",
    "pakistan": "PK",
    "bangladesh": "BD",
    "new zealand": "NZ",
    "luxembourg": "LU",
}
_CITY_HINTS = {
    "geneve": "CH",
    "geneva": "CH",
    "genf": "CH",
    "zurich": "CH",
    "zuerich": "CH",
    "lausanne": "CH",
    "bern": "CH",
    "basel": "CH",
    "schlieren": "CH",
    "zug": "CH",
    "bengaluru": "IN",
    "bangalore": "IN",
    "hyderabad": "IN",
    "pune": "IN",
    "chennai": "IN",
    "noida": "IN",
    "gurugram": "IN",
    "mumbai": "IN",
    "niort": "FR",
    "sevres": "FR",
    "puteaux": "FR",
    "lille": "FR",
    "toulouse": "FR",
    "sophia antipolis": "FR",
}
_TRAILING_ISO = re.compile(r"(?:^|,)\s*([A-Za-z]{2})\s*$")


def _country_from_location(location: str) -> str:
    """ISO from ATS location text: trailing ISO token, market label / city, far country, city hint."""
    text = (location or "").strip()
    if not text:
        return ""
    match = _TRAILING_ISO.search(text)
    if match and "," in text:
        return match.group(1).upper()
    mapped = infer_country_iso("", text)
    if mapped:
        return mapped
    folded = text.lower().replace("é", "e").replace("è", "e").replace("ü", "u").replace("ö", "o")
    for name, iso in _FAR_COUNTRIES.items():
        if re.search(rf"\b{re.escape(name)}\b", folded):
            return iso
    for city, iso in _CITY_HINTS.items():
        if re.search(rf"\b{re.escape(city)}\b", folded):
            return iso
    return ""


# --- Feeds -----------------------------------------------------------------


def _row(
    employer: dict[str, Any],
    ats: str,
    *,
    title: str,
    url: str,
    location: str = "",
    country: str = "",
    description: str = "",
    posted_at: str = "",
    remote: bool | None = None,
    employment_type: str = "",
    department: str = "",
) -> dict[str, Any] | None:
    title = _text(title)[:180]
    url = str(url or "").strip()
    if not title or not url or is_listing_hit(title, url):
        return None
    location = _text(location)[:120]
    iso = (country or "").strip().upper()
    if len(iso) != 2:
        iso = _country_from_location(location)
    if not iso and not location and len(employer.get("markets") or []) == 1:
        iso = str(employer["markets"][0])
    hay = f"{title} {location} {employment_type}".lower()
    is_remote = bool(remote) or bool(re.search(r"\b(remote|t[ée]l[ée]travail|hybrid|hybride)\b", hay))
    return {
        "id": stable_job_id(ats, url, title),
        "source": ats,
        "title": title,
        "company": str(employer.get("name") or ""),
        "location": location,
        "country": iso,
        "compensation": None,
        "currency": "",
        "stack": [],
        "seniority": "",
        "remote": "remote" if is_remote else "",
        "posted_at": str(posted_at or "")[:10],
        "contact": "",
        "description": _text(description)[:4000],
        "url": url,
        "track": "jobs",
        "stage": "discovered",
        "ingest": "employer_feed",
        "attribution": f"{employer.get('name')} careers ({ats})",
        "employer_id": str(employer.get("id") or ""),
        "employer_kind": str(employer.get("kind") or ""),
        "employment_type": str(employment_type or "")[:60],
        "department": _text(department)[:80],
    }


def _feed_greenhouse(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    del query
    payload = _json(fetch(f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(res['slug'])}/jobs?content=true"))
    rows: list[dict[str, Any]] = []
    for item in (payload.get("jobs") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        row = _row(
            employer,
            "greenhouse",
            title=str(item.get("title") or ""),
            url=str(item.get("absolute_url") or ""),
            location=str(loc.get("name") or ""),
            description=str(item.get("content") or ""),
            posted_at=str(item.get("updated_at") or ""),
            department=" / ".join(str(d.get("name") or "") for d in (item.get("departments") or []) if isinstance(d, dict)),
        )
        if row:
            rows.append(row)
    return rows


def _feed_lever(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    del query
    slug = urllib.parse.quote(res["slug"])
    try:
        payload = _json(fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json"))
    except urllib.error.HTTPError:
        payload = _json(fetch(f"https://api.eu.lever.co/v0/postings/{slug}?mode=json"))
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        cats = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        created = item.get("createdAt")
        posted = ""
        if isinstance(created, (int, float)) and created > 0:
            posted = time.strftime("%Y-%m-%d", time.gmtime(float(created) / 1000.0))
        row = _row(
            employer,
            "lever",
            title=str(item.get("text") or ""),
            url=str(item.get("hostedUrl") or item.get("applyUrl") or ""),
            location=str(cats.get("location") or ""),
            description=str(item.get("descriptionPlain") or item.get("description") or ""),
            posted_at=posted,
            employment_type=str(cats.get("commitment") or ""),
            department=str(cats.get("team") or cats.get("department") or ""),
            remote=str(item.get("workplaceType") or "").lower() == "remote",
        )
        if row:
            rows.append(row)
    return rows


def _feed_ashby(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    del query
    payload = _json(fetch(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(res['slug'])}?includeCompensation=true"))
    rows: list[dict[str, Any]] = []
    for item in (payload.get("jobs") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        row = _row(
            employer,
            "ashby",
            title=str(item.get("title") or ""),
            url=str(item.get("jobUrl") or item.get("applyUrl") or ""),
            location=str(item.get("location") or ""),
            description=str(item.get("descriptionPlain") or item.get("descriptionHtml") or ""),
            posted_at=str(item.get("publishedAt") or ""),
            employment_type=str(item.get("employmentType") or ""),
            department=str(item.get("department") or item.get("team") or ""),
            remote=bool(item.get("isRemote")),
        )
        if row:
            rows.append(row)
    return rows


def _feed_smartrecruiters(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    """One call per wanted market: global boards (Sopra, Alten) hold thousands of postings."""
    slug = urllib.parse.quote(res["slug"])
    countries = [str(iso).lower() for iso in (res.get("_markets") or []) if len(str(iso)) == 2][:4] or [""]
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for country in countries:
        params: dict[str, Any] = {"limit": 100}
        if query:
            params["q"] = query
        if country:
            params["country"] = country
        payload = _json(fetch(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?{urllib.parse.urlencode(params)}"))
        for item in (payload.get("content") or []) if isinstance(payload, dict) else []:
            if isinstance(item, dict) and str(item.get("id") or "") not in seen_ids:
                seen_ids.add(str(item.get("id") or ""))
                items.append(item)
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        place = ", ".join(str(loc.get(k) or "") for k in ("city", "region", "country") if loc.get(k))
        job_id = str(item.get("id") or "")
        url = f"https://jobs.smartrecruiters.com/{res['slug']}/{job_id}" if job_id else ""
        row = _row(
            employer,
            "smartrecruiters",
            title=str(item.get("name") or ""),
            url=url,
            location=place,
            country=str(loc.get("country") or ""),
            posted_at=str(item.get("releasedDate") or ""),
            remote=bool(loc.get("remote")),
            department=str(((item.get("department") or {}) if isinstance(item.get("department"), dict) else {}).get("label") or ""),
            employment_type=str(((item.get("typeOfEmployment") or {}) if isinstance(item.get("typeOfEmployment"), dict) else {}).get("label") or ""),
        )
        if row:
            rows.append(row)
    return rows


def _feed_workable(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    slug = urllib.parse.quote(res["slug"])
    payload = _json(
        fetch(
            f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
            json_body={"query": query or "", "location": [], "department": [], "worktype": [], "remote": []},
        )
    )
    rows: list[dict[str, Any]] = []
    for item in (payload.get("results") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        place = ", ".join(str(loc.get(k) or "") for k in ("city", "region", "country") if loc.get(k))
        shortcode = str(item.get("shortcode") or "")
        row = _row(
            employer,
            "workable",
            title=str(item.get("title") or ""),
            url=f"https://apply.workable.com/{res['slug']}/j/{shortcode}/" if shortcode else "",
            location=place,
            country=str(loc.get("countryCode") or ""),
            posted_at=str(item.get("published") or ""),
            remote=bool(item.get("remote")),
            employment_type=str(item.get("type") or ""),
            department=" / ".join(str(d) for d in (item.get("department") or []) if d) if isinstance(item.get("department"), list) else "",
        )
        if row:
            rows.append(row)
    return rows


def _feed_recruitee(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    del query
    payload = _json(fetch(f"https://{res['slug']}.recruitee.com/api/offers/"))
    rows: list[dict[str, Any]] = []
    for item in (payload.get("offers") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or str(item.get("status") or "published") != "published":
            continue
        row = _row(
            employer,
            "recruitee",
            title=str(item.get("title") or ""),
            url=str(item.get("careers_url") or ""),
            location=str(item.get("location") or ""),
            country=str(item.get("country_code") or ""),
            description=str(item.get("description") or ""),
            posted_at=str(item.get("published_at") or item.get("created_at") or ""),
            remote=bool(item.get("remote")),
            employment_type=str(item.get("employment_type_code") or ""),
            department=str(item.get("department") or ""),
        )
        if row:
            rows.append(row)
    return rows


def _rss_items(raw: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out: list[dict[str, str]] = []
    for item in root.iter("item"):
        out.append(
            {
                "title": item.findtext("title") or "",
                "link": (item.findtext("link") or "").strip(),
                "description": item.findtext("description") or "",
                "pubDate": item.findtext("pubDate") or "",
                "location": next((child.text or "" for child in item if child.tag.endswith("location")), ""),
                "department": next((child.text or "" for child in item if child.tag.endswith("department")), ""),
            }
        )
    return out


def _rss_date(text: str) -> str:
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def _feed_teamtailor(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    del query
    base = res.get("careers_url") or f"https://{res['slug']}.teamtailor.com"
    host = _host(base) or f"{res['slug']}.teamtailor.com"
    raw = fetch(f"https://{host}/jobs.rss", accept="application/rss+xml, application/xml, text/xml")
    rows: list[dict[str, Any]] = []
    for item in _rss_items(raw):
        row = _row(
            employer,
            "teamtailor",
            title=item["title"],
            url=item["link"],
            location=item["location"],
            description=item["description"],
            posted_at=_rss_date(item["pubDate"]),
            department=item["department"],
        )
        if row:
            rows.append(row)
    return rows


def _feed_personio(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    del query
    host = res.get("host") or f"{res['slug']}.jobs.personio.de"
    raw = fetch(f"https://{host}/xml", accept="application/xml, text/xml")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    rows: list[dict[str, Any]] = []
    for pos in root.iter("position"):
        job_id = (pos.findtext("id") or "").strip()
        offices = [pos.findtext("office") or ""] + [o.text or "" for o in pos.iter("office")]
        desc = " ".join(_text(d.findtext("value") or "") for d in pos.iter("jobDescription"))
        row = _row(
            employer,
            "personio",
            title=pos.findtext("name") or "",
            url=f"https://{host}/job/{job_id}" if job_id else "",
            location=", ".join(dict.fromkeys(o for o in offices if o)),
            description=desc,
            posted_at=(pos.findtext("createdAt") or "")[:10],
            employment_type=pos.findtext("employmentType") or pos.findtext("schedule") or "",
            department=pos.findtext("department") or "",
        )
        if row:
            rows.append(row)
    return rows


def _feed_workday(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    host = res["host"]
    site = res["site"]
    tenant = res["slug"]
    payload = _json(
        fetch(
            f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
            json_body={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": query or ""},
            accept="application/json",
            prime=f"https://{host}/{site}",
        )
    )
    rows: list[dict[str, Any]] = []
    for item in (payload.get("jobPostings") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("externalPath") or "")
        row = _row(
            employer,
            "workday",
            title=str(item.get("title") or ""),
            url=f"https://{host}/{site}{path}" if path else "",
            location=str(item.get("locationsText") or ""),
            posted_at="",
            description=str(item.get("postedOn") or ""),
        )
        if row:
            rows.append(row)
    return rows


_JSONLD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
_TITLE_HINT = re.compile(
    r"engineer|developer|d[ée]veloppeur|data|cloud|devops|architect|consultant|analyst|manager|scientist|"
    r"product owner|scrum|s[ée]curit|security|ing[ée]nieur|chef de projet|lead|sre|ml|ia\b|ai\b|"
    r"freelance|mission|h/f|f/h|m/f|w/m",
    re.I,
)


def _jsonld_jobs(html_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for blob in _JSONLD_RE.findall(html_text or ""):
        try:
            data = json.loads(blob.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and "@graph" in item and isinstance(item["@graph"], list):
                items.extend(node for node in item["@graph"] if isinstance(node, dict))
                continue
            if not isinstance(item, dict):
                continue
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" in [str(k) for k in kinds]:
                out.append(item)
    return out


def _jsonld_row(employer: dict[str, Any], item: dict[str, Any], page_url: str) -> dict[str, Any] | None:
    loc = item.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    address = (loc or {}).get("address") if isinstance(loc, dict) else {}
    if not isinstance(address, dict):
        address = {}
    place = ", ".join(str(address.get(k) or "") for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k))
    return _row(
        employer,
        "careers-page",
        title=str(item.get("title") or item.get("name") or ""),
        url=str(item.get("url") or page_url),
        location=place,
        country=str(address.get("addressCountry") or "") if len(str(address.get("addressCountry") or "")) == 2 else "",
        description=str(item.get("description") or ""),
        posted_at=str(item.get("datePosted") or ""),
        employment_type=str(item.get("employmentType") or ""),
        remote=str((item.get("jobLocationType") or "")).upper() == "TELECOMMUTE",
    )


def _feed_careers_page(employer: dict[str, Any], res: dict[str, Any], fetch: Fetch, query: str) -> list[dict[str, Any]]:
    """No known ATS: JSON-LD JobPosting first, then job-looking links on the page."""
    del query
    page_url = str(res.get("careers_url") or f"https://{employer.get('site')}")
    raw = fetch(page_url, accept="text/html")
    html_text = raw.decode("utf-8", errors="replace")
    rows: list[dict[str, Any]] = []
    for item in _jsonld_jobs(html_text):
        row = _jsonld_row(employer, item, page_url)
        if row:
            rows.append(row)
    if rows:
        return rows
    seen: set[str] = set()
    for href, inner in _ANCHOR_RE.findall(html_text):
        text = _text(inner)
        if not text or len(text) < 8 or len(text) > 140 or not _TITLE_HINT.search(text):
            continue
        link = urllib.parse.urljoin(page_url, html_text_unescape(href))
        if link in seen or (_host(link) != _host(page_url) and not detect_ats(link)):
            continue
        if not re.search(r"job|offre|emploi|career|carri|poste|mission|position|vacan", link, re.I):
            continue
        seen.add(link)
        row = _row(employer, "careers-page", title=text, url=link)
        if row:
            rows.append(row)
    return rows


FEEDS: dict[str, Callable[[dict[str, Any], dict[str, Any], Fetch, str], list[dict[str, Any]]]] = {
    "greenhouse": _feed_greenhouse,
    "lever": _feed_lever,
    "ashby": _feed_ashby,
    "smartrecruiters": _feed_smartrecruiters,
    "workable": _feed_workable,
    "recruitee": _feed_recruitee,
    "teamtailor": _feed_teamtailor,
    "personio": _feed_personio,
    "workday": _feed_workday,
    "careers-page": _feed_careers_page,
}


# --- Resolution ------------------------------------------------------------


def _skeleton(text: str) -> str:
    """Lowercase consonant skeleton: 'Zühlke' and 'zuehlke' both give 'zhlk'."""
    base = re.sub(r"[^a-z0-9]", "", (text or "").lower().replace("é", "e").replace("ü", "u").replace("è", "e"))
    return re.sub(r"[aeiouy]", "", base) or base


_NAME_STOPWORDS = {
    "group",
    "groupe",
    "consulting",
    "technologies",
    "technology",
    "digital",
    "solutions",
    "systems",
    "services",
    "data",
    "global",
    "software",
    "informatik",
    "informatica",
    "partners",
    "recruitment",
}


def board_matches_name(found: dict[str, str], name: str) -> bool:
    """A board found through search must carry the house's name, not a stranger's.

    "NTT Data" must not accept "nttglobaldatacenters" and "EY" must not accept
    "workday": short tokens only count when the whole name is that one token.
    """
    clean = re.sub(r"\(.*?\)", "", name)
    words = [w for w in re.split(r"[^A-Za-z0-9]+", clean) if w]
    haystack = " ".join(str(found.get(k) or "") for k in ("slug", "host", "site")).lower()
    if not words:
        return False
    if len(words) == 1:
        low = words[0].lower()
        if len(low) <= 4:
            # "GBM" must not accept "gbmc": short names need an exact token.
            slug = str(found.get("slug") or "").lower()
            host = str(found.get("host") or "").lower()
            site = str(found.get("site") or "").lower()
            return low == slug or host.startswith(f"{low}.") or site == low
        return low in haystack or (len(low) >= 5 and _skeleton(low) in _skeleton(haystack))
    compact = "".join(w.lower() for w in words)
    if compact in haystack.replace("-", "").replace("_", ""):
        return True
    slug_word = re.sub(r"[^a-z]", "", str(found.get("slug") or "").lower())
    significant = [w.lower() for w in words if w.lower() not in _NAME_STOPWORDS]
    if significant and slug_word and slug_word == significant[0]:
        # "Sia Partners" now publishes as "Sia": the slug is exactly the brand word.
        return True
    skel_hay = _skeleton(haystack)
    for low in significant:
        if len(low) < 4:
            continue
        if low in haystack or (len(low) >= 5 and _skeleton(low) in skel_hay):
            return True
    return False


def default_web_search(query: str, count: int = 8) -> str:
    from navin.career.scrape_net import default_web_search as _search

    return _search(query, count)


def _search_board(name: str, search: Callable[[str, int], str]) -> dict[str, str] | None:
    """Two web searches: Workday tenants first, then the other ATS hosts."""
    from navin.career.scrape_net import parse_search_hits

    quoted = f'"{name}"'
    for query in (
        f"{quoted} jobs site:myworkdayjobs.com",
        f"{quoted} careers greenhouse OR lever OR smartrecruiters OR workable OR teamtailor OR recruitee OR ashby",
    ):
        try:
            hits = parse_search_hits(search(query, 8))
        except Exception as exc:  # noqa: BLE001 - search is a best effort
            logger.debug("employer search failed for {}: {}", name, exc)
            continue
        for hit in hits:
            found = detect_ats(hit.get("url") or "")
            if found and board_matches_name(found, name):
                return {**found, "careers_url": hit.get("url") or ""}
    return None


def _own_domain(url: str, site: str, name: str) -> bool:
    """Does the hit live on the house's own domain (careers.theodo.fr for theodo.com)?"""
    host = _host(url)
    if not host:
        return False
    root = site.split(".")[0] if site else ""
    if root and len(root) >= 4 and root in host.replace("-", ""):
        return True
    first = re.split(r"[^A-Za-z0-9]+", re.sub(r"\(.*?\)", "", name).strip())[0].lower() if name else ""
    return bool(first) and len(first) >= 4 and first in host.replace("-", "")


def _search_careers_page(
    employer: dict[str, Any],
    search: Callable[[str, int], str],
    fetch: Fetch,
) -> dict[str, Any] | None:
    """One search for the house's own careers site (custom domains, /nous-rejoindre, ...)."""
    from navin.career.scrape_net import parse_search_hits

    name = str(employer.get("name") or "")
    site = str(employer.get("site") or "")
    try:
        hits = parse_search_hits(search(f'"{name}" carrières OR careers OR recrutement OR jobs', 8))
    except Exception as exc:  # noqa: BLE001 - search is a best effort
        logger.debug("employer careers search failed for {}: {}", name, exc)
        return None
    for hit in hits:
        url = str(hit.get("url") or "")
        found = detect_ats(url)
        if found and board_matches_name(found, name):
            return {**found, "careers_url": url}
        if not _own_domain(url, site, name) or not _CAREERS_LINK_RE.search(url):
            continue
        try:
            html_text = fetch(url, accept="text/html").decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError):
            continue
        found = detect_ats_in_html(html_text)
        if found:
            return {**found, "careers_url": url}
        return {"ats": "careers-page", "slug": "", "careers_url": url}
    return None


def resolve_employer(
    employer: dict[str, Any],
    *,
    fetch: Fetch = default_fetch,
    landing: Callable[[str], str] = final_url,
    search: Callable[[str, int], str] | None = default_web_search,
) -> dict[str, Any]:
    """Find the feed behind an employer: seed, known URL, redirects, page markers, web search.

    Returns {"ats", "slug", ...} plus "careers_url"; "ats" is "careers-page"
    when only an HTML page was found and "" with an "error" when nothing
    answered. Never more than a handful of requests.
    """
    seed = employer.get("seed") if isinstance(employer.get("seed"), dict) else None
    if seed and seed.get("ats") in FEEDS:
        return {**seed, "careers_url": str(employer.get("careers_url") or "")}
    candidates: list[str] = []
    if employer.get("careers_url"):
        candidates.append(str(employer["careers_url"]))
    site = str(employer.get("site") or "").strip()
    if site:
        candidates.extend(f"https://{site}{path}" for path in _CAREERS_PATHS[:6])
        candidates.append(f"https://{site}/")
    tried = 0
    last_error = ""
    page_only: dict[str, Any] | None = None
    for url in candidates:
        direct = detect_ats(url)
        if direct:
            return {**direct, "careers_url": url}
        if tried >= 4:
            break
        tried += 1
        try:
            landed = landing(url)
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            continue
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = str(exc)[:80]
            continue
        found = detect_ats(landed)
        if found:
            return {**found, "careers_url": landed}
        try:
            raw = fetch(landed, accept="text/html")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = str(exc)[:80]
            continue
        html_text = raw.decode("utf-8", errors="replace")
        found = detect_ats_in_html(html_text)
        if found:
            return {**found, "careers_url": landed}
        if _jsonld_jobs(html_text):
            return {"ats": "careers-page", "slug": "", "careers_url": landed}
        if page_only is None and _CAREERS_LINK_RE.search(landed):
            page_only = {"ats": "careers-page", "slug": "", "careers_url": landed}
        # Home page: follow its first careers link once.
        for href, inner in _ANCHOR_RE.findall(html_text):
            if _CAREERS_LINK_RE.search(_text(inner)) or _CAREERS_LINK_RE.search(href):
                link = urllib.parse.urljoin(landed, html_text_unescape(href))
                found = detect_ats(link)
                if found:
                    return {**found, "careers_url": link}
                if _host(link) == _host(landed) and link not in candidates:
                    candidates.insert(candidates.index(url) + 1, link)
                break
    if search is not None and employer.get("name"):
        found = _search_board(str(employer["name"]), search)
        if found:
            return found
        if page_only is None:
            found = _search_careers_page(employer, search, fetch)
            if found:
                return found
    if page_only:
        return page_only
    return {"ats": "", "slug": "", "careers_url": candidates[0] if candidates else "", "error": last_error or "no careers page found"}


def _query_for(titles: list[str]) -> str:
    first = str(titles[0] if titles else "").strip()
    return first.split(",")[0].strip()[:60]


def _title_tokens(titles: list[str]) -> set[str]:
    tokens: set[str] = set()
    for title in titles:
        for tok in re.findall(r"[a-zA-Zéèàùç+#.]{4,}", str(title or "").lower()):
            if tok not in {"senior", "junior", "lead", "confirm", "confirmé", "consultant", "engineer", "ingénieur", "developer", "développeur"}:
                tokens.add(tok)
    return tokens


def _light_match(row: dict[str, Any], tokens: set[str]) -> bool:
    if not tokens:
        return True
    hay = str(row.get("title") or "").lower()
    return any(tok in hay for tok in tokens)


def _entry_of(state: dict[str, Any], employer_id: str) -> dict[str, Any]:
    entry = state.get(employer_id)
    return entry if isinstance(entry, dict) else {}


def apply_seeds(rows: list[dict[str, Any]], state: dict[str, Any], now: float) -> int:
    """Directory seeds become resolved state without any request."""
    applied = 0
    for row in rows:
        seed = row.get("seed") if isinstance(row.get("seed"), dict) else None
        if not seed or seed.get("ats") not in FEEDS:
            continue
        entry = _entry_of(state, row["id"])
        if entry.get("ats"):
            continue
        state[row["id"]] = {**entry, **seed, "careers_url": str(entry.get("careers_url") or ""), "name": row["name"], "resolved_at": now, "seeded": True}
        applied += 1
    return applied


def pick_employers(
    profile: dict[str, Any],
    markets: list[str],
    state: dict[str, Any],
    *,
    limit: int = MAX_EMPLOYERS_PER_RUN,
    resolve_budget: int = MAX_RESOLVE_PER_RUN,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(feeds to read, houses to resolve) for the markets: primary and user rows first, stalest first.

    Reading and resolving have separate budgets so a big directory keeps
    resolving a few new houses per cycle while the known feeds stay fresh.
    """
    clock = now if now is not None else time.time()
    wanted = [str(iso).strip().upper() for iso in markets if str(iso).strip()]
    primary = {str(iso).strip().upper() for iso in (profile.get("countries_primary") or [])}
    hidden = {str(item).strip().lower() for item in (profile.get("employers_hidden") or [])}
    rows = [row for row in directory(wanted) if row["id"] not in hidden] + user_employers(profile)
    apply_seeds(rows, state, clock)

    def order(row: dict[str, Any]) -> tuple[int, int, float]:
        entry = _entry_of(state, row["id"])
        last = float(entry.get("checked_at") or 0)
        in_primary = 0 if (set(row["markets"]) & primary or row.get("user")) else 1
        return (in_primary, 0 if row.get("user") else 1, last)

    rows.sort(key=order)
    readable: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in rows:
        entry = _entry_of(state, row["id"])
        ats = str(entry.get("ats") or "")
        resolved_at = float(entry.get("resolved_at") or 0)
        if ats in FEEDS and (clock - resolved_at) < RESOLVE_TTL_S:
            readable.append(row)
            continue
        if not ats and resolved_at and (clock - resolved_at) < RESOLVE_RETRY_S:
            continue
        unresolved.append(row)
    return readable[:limit], unresolved[:resolve_budget]


def collect_employers(
    *,
    profile: dict[str, Any],
    markets: list[str],
    state: dict[str, Any],
    track: str = "",
    fetch: Fetch = default_fetch,
    landing: Callable[[str], str] = final_url,
    search: Callable[[str, int], str] | None = default_web_search,
    now: float | None = None,
    limit: int = MAX_EMPLOYERS_PER_RUN,
    resolve_budget: int = MAX_RESOLVE_PER_RUN,
    workers: int = FEED_WORKERS,
) -> dict[str, Any]:
    """Read the feeds of the selected employers. Mutates `state` (caller persists it)."""
    clock = now if now is not None else time.time()
    titles = [str(item) for item in (profile.get("titles") or []) if str(item).strip()]
    query = _query_for(titles)
    tokens = _title_tokens(titles)
    readable, unresolved = pick_employers(profile, markets, state, limit=limit, resolve_budget=resolve_budget, now=clock)

    # 1. Resolve a few houses never seen (or whose last probe failed long ago), in parallel.
    to_resolve: list[dict[str, Any]] = []
    for employer in unresolved:
        known_url = str(_entry_of(state, employer["id"]).get("careers_url") or employer.get("careers_url") or "")
        to_resolve.append({**employer, **({"careers_url": known_url} if known_url else {})})

    def _resolve(employer: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            return employer, resolve_employer(employer, fetch=fetch, landing=landing, search=search)
        except Exception as exc:  # noqa: BLE001 - one odd careers site must not stop the run
            return employer, {"ats": "", "slug": "", "careers_url": str(employer.get("careers_url") or ""), "error": str(exc)[:100]}

    if to_resolve:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(to_resolve)))) as pool:
            for employer, res in pool.map(_resolve, to_resolve):
                entry = dict(_entry_of(state, employer["id"]))
                state[employer["id"]] = {**entry, **res, "name": employer["name"], "resolved_at": clock}
                if res.get("ats") in FEEDS:
                    readable.append(employer)
    resolved_now = len(to_resolve)
    picked = readable + [row for row in unresolved if row not in readable]

    # 2. Read every resolved feed in parallel.
    wanted_markets = [str(iso).strip().upper() for iso in markets if str(iso).strip()]
    market_set = set(wanted_markets)

    def _in_market(row: dict[str, Any]) -> bool:
        country = str(row.get("country") or "").upper()
        return not market_set or country in market_set or country in {"", "REMOTE", "WW"}

    def _read(employer: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        entry = _entry_of(state, employer["id"])
        ats = str(entry.get("ats") or "")
        reader = FEEDS.get(ats)
        if not reader:
            return employer, [], str(entry.get("error") or "unresolved")
        house_markets = [iso for iso in wanted_markets if iso in set(employer.get("markets") or [])] or wanted_markets
        rows: list[dict[str, Any]] = []
        for attempt in (1, 2):
            try:
                rows = reader(employer, {**entry, "_markets": house_markets}, fetch, query)
                break
            except urllib.error.HTTPError as exc:
                return employer, [], f"HTTP {exc.code}"
            except (urllib.error.URLError, OSError) as exc:
                # Timeouts and TLS hiccups on multi-megabyte boards: one retry.
                if attempt == 2:
                    return employer, [], str(exc)[:100]
            except (ValueError, ET.ParseError) as exc:
                return employer, [], str(exc)[:100]
        kept = [row for row in rows if _light_match(row, tokens) and _in_market(row)][:MAX_JOBS_PER_EMPLOYER]
        if track:
            for row in kept:
                row["track"] = track if track in {"freelance", "jobs"} else "jobs"
        return employer, kept, ""

    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    reports: list[dict[str, Any]] = []
    if readable:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(readable)))) as pool:
            for employer, rows, error in pool.map(_read, readable):
                entry = dict(_entry_of(state, employer["id"]))
                entry.update({"checked_at": clock, "last_count": len(rows), "last_error": error})
                state[employer["id"]] = entry
                reports.append({"id": employer["id"], "name": employer["name"], "ats": entry.get("ats"), "count": len(rows), "error": error})
                if error:
                    errors.append(f"{employer['name']}: {error}")
                    continue
                jobs.extend(rows)
    for employer in picked:
        if employer in readable:
            continue
        entry = dict(_entry_of(state, employer["id"]))
        entry.setdefault("checked_at", clock)
        state[employer["id"]] = entry
        reports.append({"id": employer["id"], "name": employer["name"], "ats": entry.get("ats") or "", "count": 0, "error": entry.get("error") or "unresolved"})
    if errors:
        logger.info("career employers: {} feed errors: {}", len(errors), errors[:5])
    return {
        "jobs": jobs,
        "checked": len(readable),
        "resolved_now": resolved_now,
        "picked": len(picked),
        "reports": reports,
        "errors": errors,
    }


def employer_summary(state: dict[str, Any], countries: list[str] | None = None) -> dict[str, Any]:
    """Desk view: how many houses are watched, resolved, and which ATS they use."""
    rows = directory(countries)
    by_ats: dict[str, int] = {}
    resolved = 0
    for row in rows:
        entry = state.get(row["id"]) if isinstance(state.get(row["id"]), dict) else {}
        ats = str(entry.get("ats") or "")
        if ats:
            resolved += 1
            by_ats[ats] = by_ats.get(ats, 0) + 1
    return {"directory": len(rows), "resolved": resolved, "by_ats": by_ats}
