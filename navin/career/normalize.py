# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One vocabulary for every job source: contracts, work mode, experience, pay.

Every connector (public listing readers, API / RSS feeds, JSON-LD JobPosting,
ATS boards, keyed APIs) speaks its own dialect. This module folds them onto the
fields the desk filters and cards rely on:

- ``contracts``: list out of CONTRACT_KINDS ("contractor", "permanent", ...)
- ``remote``: "remote" | "hybrid" | "onsite" | ""
- ``experience_level``: "junior" | "mid" | "senior" | "expert" | ""
- ``experience_years_min``: int | None
- ``daily_rate_min/max`` and ``salary_min/max`` in ``currency``
- ``compensation``: the figure the score compares (day rate for freelance,
  yearly salary for jobs), always in ``currency``

Currency follows the market the offer is in when the source does not state
it. The FX table is indicative (rounded, reviewed 2026-09) and only serves the
match score; the card always shows the posted currency.
"""

from __future__ import annotations

import re
from typing import Any

CONTRACT_KINDS: tuple[str, ...] = (
    "contractor",
    "permanent",
    "fixed-term",
    "part-time",
    "temporary",
    "internship",
    "apprenticeship",
)

# Market ISO -> currency the offers there are posted in.
MARKET_CURRENCY: dict[str, str] = {
    "FR": "EUR",
    "BE": "EUR",
    "LU": "EUR",
    "DE": "EUR",
    "NL": "EUR",
    "IE": "EUR",
    "ES": "EUR",
    "IT": "EUR",
    "PT": "EUR",
    "AT": "EUR",
    "FI": "EUR",
    "CH": "CHF",
    "GB": "GBP",
    "US": "USD",
    "CA": "CAD",
    "AU": "AUD",
    "SE": "SEK",
    "PL": "PLN",
    "AE": "AED",
    "SA": "SAR",
    "QA": "QAR",
    "KW": "KWD",
    "OM": "OMR",
    "BH": "BHD",
    "MA": "MAD",
    "TN": "TND",
    "IN": "INR",
    "ZA": "ZAR",
}

# Indicative rates to one EUR unit, for the score only. Rounded, reviewed 2026-09.
FX_TO_EUR: dict[str, float] = {
    "EUR": 1.0,
    "USD": 0.92,
    "GBP": 1.17,
    "CHF": 1.05,
    "CAD": 0.67,
    "AUD": 0.60,
    "SEK": 0.088,
    "PLN": 0.23,
    "AED": 0.25,
    "SAR": 0.245,
    "QAR": 0.25,
    "KWD": 3.0,
    "OMR": 2.39,
    "BHD": 2.44,
    "MAD": 0.092,
    "TND": 0.30,
    "INR": 0.011,
    "ZAR": 0.05,
}

_CURRENCY_MARKERS: tuple[tuple[str, str], ...] = (
    ("€", "EUR"),
    ("eur", "EUR"),
    ("£", "GBP"),
    ("gbp", "GBP"),
    ("chf", "CHF"),
    ("ca$", "CAD"),
    ("cad", "CAD"),
    ("c$", "CAD"),
    ("a$", "AUD"),
    ("aud", "AUD"),
    ("us$", "USD"),
    ("usd", "USD"),
    ("$", "USD"),
    ("aed", "AED"),
    ("sar", "SAR"),
    ("qar", "QAR"),
    ("kwd", "KWD"),
    ("omr", "OMR"),
    ("bhd", "BHD"),
    ("mad", "MAD"),
    ("tnd", "TND"),
    ("sek", "SEK"),
    ("pln", "PLN"),
    ("zł", "PLN"),
    ("inr", "INR"),
    ("₹", "INR"),
)

# A pay amount: not glued to a word (IR35, k8s, H1B), not a share, an age, a
# duration or a weekly hour count ("40h/week", "3 days on site").
_MONEY_RE = re.compile(
    r"(?<![A-Za-z\d.,])(\d+(?:[.,]\d+)?)\s*(k)?"
    r"(?![\d.,]*\s*(?:%|ans?\b|years?\b|yrs?\b|mois\b|months?\b|h\b|hrs?\b|hours?\b|heures?\b|days?\b|jours?\b|weeks?\b|semaines?\b|fte\b))",
    re.I,
)
# "45 000", "45.000", "105,000": a separator followed by exactly three digits groups thousands.
_THOUSANDS_RE = re.compile(r"(?<=\d)[ \u00a0\u202f.,](?=\d{3}(?!\d))")
_PERIOD_RE = re.compile(
    r"(?P<day>/\s*(?:j|jour|day|d)\b|per day|par jour|daily|tjm|day rate|journ(?:ée|ee))|"
    r"(?P<hour>/\s*(?:h|hr|hour|heure)\b|per hour|hourly|par heure)|"
    r"(?P<month>/\s*(?:m|mois|month|mo)\b|per month|monthly|par mois|mensuel)|"
    r"(?P<year>/\s*(?:an|année|annee|yr|year|y|a)\b|per year|per annum|yearly|annual|annuel|par an|p\.?a\.?\b|brut annuel)",
    re.I,
)
_YEARS_RE = re.compile(r"(\d{1,2})\s*(?:\+|-|à|a|to|/)?\s*(\d{1,2})?\s*\+?\s*(?:ans?|years?|yrs?)\b", re.I)
_REMOTE_RE = re.compile(r"\b(full[ -]?remote|100\s*%\s*(?:remote|t[ée]l[ée]travail)|remote|t[ée]l[ée]travail (?:total|complet)|work from home|wfh|anywhere)\b", re.I)
_HYBRID_RE = re.compile(r"\b(hybrid|hybride|partial|partiel|t[ée]l[ée]travail partiel|\d\s*(?:j|jours|days?)\s*(?:de\s*)?(?:t[ée]l[ée]travail|remote|sur site|on[ -]?site))\b", re.I)
_ONSITE_RE = re.compile(r"\b(on[ -]?site|onsite|pr[ée]sentiel|sur site|in[ -]office|no remote)\b", re.I)

_CONTRACT_TOKENS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(freelance|free-lance|contractor|contract|indépendant|independant|independent|mission|b2b|portage|consultant ind[ée]pendant|outside ir35|inside ir35|1099|c2c|corp[- ]to[- ]corp)\b", re.I), "contractor"),
    (re.compile(r"\b(cdi|permanent|full[ -]?time|fulltime|plein temps|temps plein|unbefristet|vast contract|regular)\b", re.I), "permanent"),
    (re.compile(r"\b(cdd|fixed[ -]?term|temporary|temp\b|interim|intérim|befristet|tijdelijk)\b", re.I), "fixed-term"),
    (re.compile(r"\b(part[ -]?time|temps partiel|mi-temps|teilzeit|deeltijd)\b", re.I), "part-time"),
    (re.compile(r"\b(intern(?:ship)?|stage|stagiaire|praktikum)\b", re.I), "internship"),
    (re.compile(r"\b(apprentice(?:ship)?|alternance|alternant|apprenti|ausbildung|work[ -]study)\b", re.I), "apprenticeship"),
)
_CONTRACT_EXACT: dict[str, str] = {
    "contractor": "contractor",
    "contract": "contractor",
    "freelance": "contractor",
    "permanent": "permanent",
    "full_time": "permanent",
    "full-time": "permanent",
    "full time": "permanent",
    "fulltime": "permanent",
    "cdi": "permanent",
    "fixed-term": "fixed-term",
    "fixed_term": "fixed-term",
    "cdd": "fixed-term",
    "temporary": "fixed-term",
    "temp": "fixed-term",
    "part-time": "part-time",
    "part_time": "part-time",
    "part time": "part-time",
    "internship": "internship",
    "intern": "internship",
    "stage": "internship",
    "apprenticeship": "apprenticeship",
    "alternance": "apprenticeship",
    "volunteer": "",
    "other": "",
    "per_diem": "contractor",
}

_LEVEL_TOKENS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(principal|staff|distinguished|expert|architecte? senior|head of|director|directeur|vp\b|chief)\b", re.I), "expert"),
    (re.compile(r"\b(senior|sr\.?|lead|confirm[ée]|experienced|mid[- ]senior)\b", re.I), "senior"),
    (re.compile(r"\b(junior|jr\.?|entry(?:[- ]level)?|graduate|débutant|debutant|associate|internship|intern|stage)\b", re.I), "junior"),
    (re.compile(r"\b(mid[- ]?level|intermediate|interm[ée]diaire|mid\b|regular)\b", re.I), "mid"),
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def market_currency(country: str) -> str:
    return MARKET_CURRENCY.get(_clean(country).upper(), "")


def detect_currency(text: str) -> str:
    raw = _clean(text)
    if not raw:
        return ""
    lower = raw.lower()
    for marker, code in _CURRENCY_MARKERS:
        if marker in lower:
            return code
    return ""


def convert_money(value: float | None, from_currency: str, to_currency: str) -> float | None:
    """Indicative conversion for the score. None when either currency is unknown."""
    if value is None:
        return None
    src = _clean(from_currency).upper() or "EUR"
    dst = _clean(to_currency).upper() or "EUR"
    if src == dst:
        return float(value)
    src_rate = FX_TO_EUR.get(src)
    dst_rate = FX_TO_EUR.get(dst)
    if not src_rate or not dst_rate:
        return None
    return float(value) * src_rate / dst_rate


def detect_period(text: str) -> str:
    match = _PERIOD_RE.search(_clean(text))
    if not match:
        return ""
    for name in ("day", "hour", "month", "year"):
        if match.group(name):
            return name
    return ""


def parse_pay_range(text: str, *, default_period: str = "") -> dict[str, Any]:
    """Numbers, currency and period out of a posted pay line.

    "330-350 €" -> min 330, max 350, EUR; "40k-45k €" -> 40000 / 45000;
    "£350-450" -> GBP; "600 €/j" -> period day; "45 000 € brut annuel" -> year.
    Numbers followed by %, years or months are ignored. Amounts under 1000
    with no period are read as day rates, amounts of 1000 and more as yearly
    unless the text says otherwise.
    """
    raw = _THOUSANDS_RE.sub("", _clean(text))
    out: dict[str, Any] = {"min": None, "max": None, "currency": detect_currency(raw), "period": detect_period(raw) or default_period}
    if not raw:
        return out
    amounts: list[float] = []
    saw_kilo = False
    for number, kilo in _MONEY_RE.findall(raw):
        try:
            value = float(number.replace(",", "."))
        except ValueError:
            continue
        if kilo:
            value *= 1000.0
            saw_kilo = True
        if value > 0:
            amounts.append(value)
    if not amounts:
        return out
    if saw_kilo:
        # "$200-260k": the k applies to both ends of the range.
        amounts = [value * 1000.0 if value < 1000 else value for value in amounts]
    low, high = min(amounts), max(amounts)
    out["min"] = low
    out["max"] = high
    if not out["period"]:
        out["period"] = "day" if high < 1000 else "year"
    return out


def to_daily(value: float | None, period: str) -> float | None:
    if value is None:
        return None
    unit = _clean(period).lower()
    if unit == "day":
        return float(value)
    if unit == "hour":
        return float(value) * 8.0
    return None


def to_annual(value: float | None, period: str) -> float | None:
    if value is None:
        return None
    unit = _clean(period).lower()
    if unit == "year":
        return float(value)
    if unit == "month":
        return float(value) * 12.0
    if unit == "hour":
        return float(value) * 8.0 * 216.0
    return None


def pay_fields(
    *,
    daily: tuple[float | None, float | None] = (None, None),
    annual: tuple[float | None, float | None] = (None, None),
    currency: str = "",
    country: str = "",
    track: str = "",
) -> dict[str, Any]:
    """The pay block every row carries: both ranges, one currency, the score figure."""
    day_min, day_max = daily
    year_min, year_max = annual
    code = _clean(currency).upper()
    if not code and any(item is not None for item in (day_min, day_max, year_min, year_max)):
        code = market_currency(country)
    wanted = _clean(track).lower()
    if wanted == "freelance":
        compensation = day_max if day_max is not None else day_min
    elif wanted == "jobs":
        compensation = year_max if year_max is not None else year_min
    else:
        compensation = day_max if day_max is not None else (day_min if day_min is not None else (year_max if year_max is not None else year_min))
    return {
        "daily_rate_min": day_min,
        "daily_rate_max": day_max,
        "salary_min": year_min,
        "salary_max": year_max,
        "currency": code,
        "compensation": compensation,
    }


def pay_from_text(text: str, *, country: str = "", track: str = "") -> dict[str, Any]:
    """pay_fields() straight from one posted line ("400-500 €/j", "$90k-$120k")."""
    parsed = parse_pay_range(text)
    period = parsed["period"]
    daily = (to_daily(parsed["min"], period), to_daily(parsed["max"], period))
    annual = (to_annual(parsed["min"], period), to_annual(parsed["max"], period))
    return pay_fields(daily=daily, annual=annual, currency=parsed["currency"], country=country, track=track)


def pay_from_numbers(
    minimum: Any,
    maximum: Any,
    *,
    period: str = "",
    currency: str = "",
    country: str = "",
    track: str = "",
) -> dict[str, Any]:
    """pay_fields() from structured min / max / period (APIs, JSON-LD baseSalary)."""

    def _num(value: Any) -> float | None:
        try:
            number = float(str(value).replace(",", ".").replace(" ", ""))
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    low = _num(minimum)
    high = _num(maximum)
    if low is None and high is None:
        return pay_fields(currency=currency, country=country, track=track)
    unit = _clean(period).lower()
    if unit in {"annual", "annually", "yearly", "year", "y", "p.a."}:
        unit = "year"
    elif unit in {"monthly", "month", "mo", "m"}:
        unit = "month"
    elif unit in {"daily", "day", "d", "per_diem"}:
        unit = "day"
    elif unit in {"hourly", "hour", "h", "hr"}:
        unit = "hour"
    elif unit in {"weekly", "week", "w"}:
        unit = "week"
    if not unit:
        top = high if high is not None else low
        unit = "day" if top is not None and top < 1000 else "year"
    if unit == "week":
        daily = ((low / 5.0) if low is not None else None, (high / 5.0) if high is not None else None)
        annual = (None, None)
    else:
        daily = (to_daily(low, unit), to_daily(high, unit))
        annual = (to_annual(low, unit), to_annual(high, unit))
    return pay_fields(daily=daily, annual=annual, currency=currency, country=country, track=track)


def normalize_contracts(*values: Any) -> list[str]:
    """Contract kinds out of any mix of labels, codes and free text."""
    out: list[str] = []

    def add(kind: str) -> None:
        if kind and kind not in out:
            out.append(kind)

    for value in values:
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            text = _clean(item)
            if not text:
                continue
            exact = _CONTRACT_EXACT.get(text.lower())
            if exact is not None:
                add(exact)
                continue
            for pattern, kind in _CONTRACT_TOKENS:
                if pattern.search(text):
                    add(kind)
    return out


def contracts_track(contracts: list[str], wanted: str = "") -> str:
    """freelance | jobs from the contract kinds, the wanted track breaking ties."""
    kinds = set(contracts or [])
    freelance = "contractor" in kinds
    employee = bool(kinds & {"permanent", "fixed-term", "part-time", "temporary", "internship", "apprenticeship"})
    if freelance and not employee:
        return "freelance"
    if employee and not freelance:
        return "jobs"
    return wanted if wanted in {"freelance", "jobs"} else ("freelance" if freelance else "jobs")


def normalize_remote(*values: Any) -> str:
    """remote | hybrid | onsite | "" out of flags, labels and free text."""
    for value in values:
        if value is True:
            return "remote"
        if value is False or value is None:
            continue
        text = _clean(value)
        if not text:
            continue
        lower = text.lower()
        if lower in {"remote", "yes", "true", "full", "fully_remote", "full_remote", "fully-remote", "telecommute", "100%"}:
            return "remote"
        if lower in {"hybrid", "hybride", "partial", "partiel"}:
            return "hybrid"
        if lower in {"onsite", "on-site", "on_site", "office", "none", "no", "false", "presentiel", "présentiel"}:
            return "onsite"
        if _HYBRID_RE.search(text):
            return "hybrid"
        if _REMOTE_RE.search(text):
            return "remote"
        if _ONSITE_RE.search(text):
            return "onsite"
    return ""


def normalize_experience(*values: Any) -> tuple[str, int | None]:
    """(level, years_min) out of seniority labels, years mentions and titles."""
    level = ""
    years: int | None = None
    for value in values:
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if years is None or int(item) < years:
                    years = int(item)
                continue
            text = _clean(item)
            if not text:
                continue
            match = _YEARS_RE.search(text)
            if match:
                try:
                    found = int(match.group(1))
                except ValueError:
                    found = None
                if found is not None and (years is None or found < years):
                    years = found
            if not level:
                for pattern, name in _LEVEL_TOKENS:
                    if pattern.search(text):
                        level = name
                        break
    if not level and years is not None:
        level = level_for_years(years)
    return level, years


def level_for_years(years: int | None) -> str:
    if years is None:
        return ""
    if years <= 2:
        return "junior"
    if years <= 5:
        return "mid"
    if years <= 10:
        return "senior"
    return "expert"


def duration_months(value: Any, period: str = "month") -> int:
    try:
        count = float(value)
    except (TypeError, ValueError):
        return 0
    unit = _clean(period).lower()
    factor = {"day": 1 / 21.0, "days": 1 / 21.0, "week": 0.25, "weeks": 0.25, "month": 1.0, "months": 1.0, "mois": 1.0, "year": 12.0, "years": 12.0, "an": 12.0, "ans": 12.0}.get(unit, 0.0)
    if count <= 0 or factor <= 0:
        return 0
    return max(1, round(count * factor))


def duration_label(value: Any, period: str = "month") -> str:
    try:
        count = int(float(value))
    except (TypeError, ValueError):
        return ""
    unit = _clean(period).lower().rstrip("s")
    if count <= 0 or unit not in {"day", "week", "month", "year"}:
        return ""
    return f"{count} {unit}{'s' if count > 1 else ''}"


_DURATION_TEXT_RE = re.compile(
    r"\b(\d{1,2})\s*(?:\+|-|à|a|to)?\s*(\d{1,2})?\s*(mois|months?|ans?|years?|semaines?|weeks?)\b",
    re.I,
)


_PAY_LINE_RE = re.compile(
    r"(tjm|taux journalier|day rate|daily rate|rate|salaire|salary|r[ée]mun[ée]ration|package|compensation|brut|gross|per hour|/\s*(?:j|jour|day|h|an|year|yr)\b)",
    re.I,
)
_PAY_AMOUNT_RE = re.compile(r"(?:€|£|\$|eur|gbp|usd|chf|cad|aed)\s?\d|\d\s?(?:k\s?)?(?:€|£|\$|eur\b|gbp\b|usd\b|chf\b|cad\b|aed\b)", re.I)
# "$11M ARR", "2,000+ customers", "$1B valuation": money, but not pay.
_NOT_PAY_RE = re.compile(
    r"\d\s?(?:k\s?)?(?:€|£|\$|eur|gbp|usd|chf|cad|aed)?\s?(?:m|mm|b|bn|million|billion|milliards?|millions?|arr|mrr|revenue|valuation|funding|raised|series)\b|"
    r"(?:raised|funding|revenue|valuation|arr|mrr|series [a-e])\b[^.\n]{0,25}(?:€|£|\$)\s?\d",
    re.I,
)
_PAY_WINDOW_BEFORE = 70
_PAY_WINDOW_AFTER = 45
_PAY_FRAGMENT_BEFORE = 28
# A pay range ends at a bracket, a clause break or a "+ perks" tail: "(39h + 12 RTT)" is not money.
_PAY_FRAGMENT_STOP_RE = re.compile(r"[()\[\];|+]|,\s|\.\s")


def _pay_fragment(line: str, start: int, end: int) -> str:
    """The words that belong to one posted amount, cut at clause breaks on both sides."""
    before = line[max(0, start - _PAY_FRAGMENT_BEFORE) : start]
    stops = list(_PAY_FRAGMENT_STOP_RE.finditer(before))
    if stops:
        before = before[stops[-1].end() :]
    after = line[end : end + _PAY_WINDOW_AFTER]
    stop = _PAY_FRAGMENT_STOP_RE.search(after)
    if stop:
        after = after[: stop.start()]
    return f"{before}{line[start:end]}{after}"
# Plausible posted pay in EUR terms; anything outside is a company figure, a year or a typo.
_DAY_RATE_BOUNDS = (80.0, 5000.0)
_ANNUAL_BOUNDS = (8000.0, 1_500_000.0)
_PAY_RANGE_MAX_RATIO = 5.0


def _plausible_pay(daily: tuple[float | None, float | None], annual: tuple[float | None, float | None], currency: str) -> bool:
    def _eur(value: float | None) -> float | None:
        if value is None:
            return None
        converted = convert_money(value, currency, "EUR")
        return converted if converted is not None else float(value)

    for pair, bounds in ((daily, _DAY_RATE_BOUNDS), (annual, _ANNUAL_BOUNDS)):
        low, high = pair
        if low is None and high is None:
            continue
        for value in (low, high):
            eur = _eur(value)
            if eur is not None and not bounds[0] <= eur <= bounds[1]:
                return False
        if low and high and low > 0 and high / low > _PAY_RANGE_MAX_RATIO:
            return False
    return True


def pay_from_description(text: str, *, country: str = "", track: str = "") -> dict[str, Any] | None:
    """The first posted pay figure in a free-text description, or None.

    Only the words around a currency amount are read: the amount needs a pay
    word (TJM, salary, rate, per day...) within a few words, company figures
    ("$11M ARR", "2,000 customers") are skipped, and the result must be a
    plausible day rate or yearly salary. Bare numbers never count.
    """
    for raw_line in _clean(text).splitlines():
        line = _THOUSANDS_RE.sub("", raw_line.strip())
        if not line:
            continue
        for match in _PAY_AMOUNT_RE.finditer(line):
            context = line[max(0, match.start() - _PAY_WINDOW_BEFORE) : match.end() + _PAY_WINDOW_AFTER]
            if not _PAY_LINE_RE.search(context):
                continue
            fragment = _pay_fragment(line, match.start(), match.end())
            if _NOT_PAY_RE.search(fragment):
                continue
            parsed = parse_pay_range(fragment)
            if parsed["max"] is None or not parsed["currency"]:
                continue
            period = parsed["period"]
            daily = (to_daily(parsed["min"], period), to_daily(parsed["max"], period))
            annual = (to_annual(parsed["min"], period), to_annual(parsed["max"], period))
            if daily == (None, None) and annual == (None, None):
                continue
            if not _plausible_pay(daily, annual, parsed["currency"]):
                continue
            return pay_fields(daily=daily, annual=annual, currency=parsed["currency"], country=country, track=track)
    return None


def enrich_facts(row: dict[str, Any], *, track: str = "") -> dict[str, Any]:
    """Fill the shared vocabulary on a row that came without it (snippets, ATS, LinkedIn).

    Structured values already on the row win; only empty fields are read out of
    the title and description. The row is updated in place and returned.
    """
    title = _clean(row.get("title"))
    description = _clean(row.get("description"))
    hay = f"{title}\n{description[:1500]}"
    if not row.get("contracts"):
        kinds = normalize_contracts(row.get("employment_type"), title, description[:400])
        if kinds:
            row["contracts"] = kinds
            if not _clean(row.get("employment_type")):
                row["employment_type"] = ", ".join(kinds)
    if not _clean(row.get("remote")):
        row["remote"] = normalize_remote(_clean(row.get("location")), title, description[:600])
    if not _clean(row.get("experience_level")):
        level, years = normalize_experience(_clean(row.get("seniority")), title, description[:800])
        if level:
            row["experience_level"] = level
            if not _clean(row.get("seniority")):
                row["seniority"] = level
        if years is not None and row.get("experience_years_min") is None:
            row["experience_years_min"] = years
    if not row.get("duration_months"):
        months, label = duration_from_text(hay)
        kinds_now = list(row.get("contracts") or [])
        if months and ("contractor" in kinds_now or not kinds_now):
            row["duration_months"] = months
            if not _clean(row.get("duration")):
                row["duration"] = label
    has_pay = any(row.get(key) is not None for key in ("daily_rate_min", "daily_rate_max", "salary_min", "salary_max"))
    if not has_pay:
        wanted = track or _clean(row.get("track"))
        country = _clean(row.get("country"))
        if row.get("compensation") is not None:
            # Older rows: one figure in the track's unit.
            value = float(row["compensation"])
            if wanted == "jobs" or value >= 5000:
                row.update(pay_fields(annual=(None, value), currency=_clean(row.get("currency")), country=country, track=wanted))
            else:
                row.update(pay_fields(daily=(None, value), currency=_clean(row.get("currency")), country=country, track=wanted))
        else:
            # Always write the pay keys: a re-collect must clear a stale figure on the stored row.
            found = pay_from_description(description, country=country, track=wanted)
            row.update(found or pay_fields(currency=_clean(row.get("currency")), country=country, track=wanted))
    if not _clean(row.get("currency")):
        row["currency"] = market_currency(_clean(row.get("country")))
    return row


def duration_from_text(text: str) -> tuple[int, str]:
    """(months, label) for "mission 6 mois", "12 months contract", "1 year"."""
    for match in _DURATION_TEXT_RE.finditer(_clean(text)):
        count = match.group(2) or match.group(1)
        unit_raw = match.group(3).lower()
        unit = "month" if unit_raw.startswith(("mois", "month")) else "year" if unit_raw.startswith(("an", "year")) else "week"
        months = duration_months(count, unit)
        if months:
            return months, duration_label(count, unit)
    return 0, ""
